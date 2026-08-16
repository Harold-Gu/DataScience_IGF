"""Full-pipeline controller: scrape steps -> cleanup -> manifest ->
classify -> extract.  This is the exact orchestration of the former
single-file main(); every step function keeps its original behaviour,
with optional debug filters (years= / limit=) that are no-ops when unset."""
import os, time, json
from datetime import datetime

from ..config import STEPS, YEAR_START, YEAR_END, year_range
from ..state import _MANIFEST, _stats, _stats_lock, _failed_seen, _failed_lock, _FILE_MAP
from ..models import network
from ..models.classify import run_classify
from ..models.extract import run_extract
from . import scraper as steps


def run(args):
    """Execute the legacy command line (args = argparse.Namespace)."""
    if args.retry_failed:
        retry_dir = args.output or os.path.dirname(os.path.abspath(args.retry_failed))
        os.makedirs(retry_dir, exist_ok=True)
        network._set_failed_log(os.path.join(retry_dir, "failed_urls_retry.tsv"))
        steps._retry_failed_file(args.retry_failed, args.workers)
        network._print_stat()
        return
    if args.classify_only:
        src = args.classify_dir or next((d for d in sorted(os.listdir("."), reverse=True) if d.startswith("igf_full_") and os.path.isdir(d)), None)
        if not src:
            print("No igf_full_* found!")
            return
        run_classify(src, args.classify_out, args.workers)
        return

    years = None
    if getattr(args, "year", None):
        years = [args.year]
    elif getattr(args, "years", None):
        years = year_range(args.years)

    do = set(args.step.split(",")) if args.step else set(STEPS)
    out = args.output or f"igf_full_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    print(f"\n{'#'*55}\n  IGF COMPLETE SCRAPER + CLASSIFIER + EXTRACTOR\n  Steps: {', '.join(sorted(do))}\n  Workers: {args.workers}\n  Year range: {YEAR_START}-{YEAR_END}\n  Output: {os.path.abspath(out)}\n{'#'*55}")
    if args.dry_run:
        print("\n[Dry run - no downloads]")
        return
    os.makedirs(out, exist_ok=True)
    network._set_failed_log(os.path.join(out, "failed_urls.tsv"))
    t0 = time.time()
    STEPS_MAP = {"sessions": steps.step_sessions, "reports": steps.step_reports, "transcripts": steps.step_transcripts, "schedules": steps.step_schedules}
    for s, f in STEPS_MAP.items():
        if s in do:
            s0 = network._snap()
            ts = time.time()
            if s == "sessions":
                f(out, args.workers, years=years, limit=getattr(args, "limit", None))
            else:
                f(out, args.workers, years=years)
            network._step_note(s, s0, ts)
    if "archived" in do or "dashboard" in do:
        s0 = network._snap()
        ts = time.time()
        steps.step_archived_dashboard(out, set(years) if years else None, args.workers)
        network._step_note("archived_dashboard", s0, ts)
        if "reports" in do:
            s0 = network._snap()
            ts = time.time()
            steps._discover_reports(out, args.workers, years=years)
            network._step_note("reports_discovery", s0, ts)
    if "participants" in do:
        s0 = network._snap()
        ts = time.time()
        steps.step_participants(out, args.workers)
        network._step_note("participants", s0, ts)
    if not args.no_clean:
        print(f"\n{'='*55}\n  CLEANUP")
        steps._remove_empty_dirs(out)
    with _failed_lock:
        nfailed = len(_failed_seen)
    if nfailed:
        print(f"  Failed URLs logged: {nfailed} -> {os.path.join(os.path.abspath(out),'failed_urls.tsv')}")
    with _stats_lock:
        s = dict(_stats)
    attempts = s.get("ok", 0) + s.get("fail", 0)
    rate = (s.get("ok", 0) * 100.0 / attempts) if attempts else 100.0
    print(f"  Overall download success rate: {rate:.1f}% ({s.get('ok',0)} ok / {attempts} attempts)")
    _MANIFEST["_totals"] = {"ok": s.get("ok", 0), "fail": s.get("fail", 0), "skip": s.get("skip", 0),
        "pages": s.get("pages", 0), "errors": s.get("errors", 0), "failed_urls": nfailed,
        "success_rate": round(rate, 2), "minutes": round((time.time() - t0) / 60, 1)}
    mpath = os.path.join(out, "manifest.json")
    try:
        with open(mpath, "w", encoding="utf-8") as f:
            json.dump({"generated": datetime.now().isoformat(timespec="seconds"), "output": os.path.abspath(out), "steps": _MANIFEST}, f, ensure_ascii=False, indent=2)
        print(f"  Manifest -> {mpath}")
    except Exception:
        pass
    if _FILE_MAP:
        fmp = os.path.join(out, "file_map.tsv")
        try:
            with open(fmp, "w", encoding="utf-8") as f:
                for a, b in _FILE_MAP:
                    f.write(f"{a}\t{b}\n")
            print(f"  Binary extension map -> {fmp}")
        except Exception:
            pass
    elapsed = (time.time() - t0) / 60
    print(f"\n{'#'*55}\n  SCRAPE DONE  ({elapsed:.0f}m)")
    network._print_stat()
    print(f"  Output: {os.path.abspath(out)}\n{'#'*55}")
    if not args.no_classify:
        run_extract(run_classify(out, args.classify_out, args.workers), args.extract_out, args.workers) if not args.no_extract else run_classify(out, args.classify_out, args.workers)


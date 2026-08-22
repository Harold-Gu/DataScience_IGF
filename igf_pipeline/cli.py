import argparse
import os
import subprocess
import sys
import time
from datetime import datetime

from . import analyze, crawl, llm, pipeline, process
from .config import WORKERS


COMMANDS = [
    "scrape", "classify", "extract", "validate", "denoise", "recover",
    "analyze", "retry", "selftest", "probe", "run",
    "llm-bench", "llm-verify", "llm-kw", "llm-score", "baselines",
    "gold-sample", "gold-annotate-a", "gold-annotate-b", "gold-kappa", "gold-stats",
    "full-extract", "verify-extract", "downstream",
    "hot-topics", "cross-validate",
]

RUN_STAGES = ["scrape", "classify", "extract", "validate", "full-extract",
              "verify-extract", "downstream", "baselines", "significance"]


def build_parser():
    p = argparse.ArgumentParser(prog="igf", description="IGF full-data pipeline (scrape / classify / extract / validate / LLM)")
    p.add_argument("command", nargs="?", default=None, choices=COMMANDS,
                   help="subcommand; omit to run the legacy full pipeline")
    p.add_argument("--step", help="comma-separated: sessions,reports,transcripts,schedules,archived,dashboard,participants")
    p.add_argument("--year", type=int, help="single year (legacy)")
    p.add_argument("--years", default=None, help="debug filter: '2020' or '2020-2022'")
    p.add_argument("--limit", type=int, default=None, help="debug: cap session pages to download")
    p.add_argument("--workers", type=int, default=WORKERS)
    p.add_argument("--output", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-clean", action="store_true")
    p.add_argument("--no-classify", action="store_true")
    p.add_argument("--no-extract", action="store_true")
    p.add_argument("--classify-only", action="store_true")
    p.add_argument("--classify-dir", default=None, help="source dir for classify/extract subcommands")
    p.add_argument("--classify-out", default=None)
    p.add_argument("--extract-out", default=None)
    p.add_argument("--retry-failed", default=None, help="path to failed_urls.tsv")
    p.add_argument("--full", default=None)
    p.add_argument("--classified", default=None)
    p.add_argument("--extracted", default=None)
    p.add_argument("--no-drupal", action="store_true")
    p.add_argument("--input", default=None, help="input JSON for denoise/recover/analyze/llm-score/run")
    p.add_argument("--base", default=None, help="classified base dir for recover")
    p.add_argument("--top-k", type=int, default=None)
    p.add_argument("--min-body", type=int, default=None)
    p.add_argument("--url", default=None)
    p.add_argument("--wayback-year", default=None)
    p.add_argument("--models", default=None)
    p.add_argument("--methods", default=None)
    p.add_argument("--docs", default=None)
    p.add_argument("--gold", default=None)
    p.add_argument("--bench-out", default=None)
    p.add_argument("--llm-extra", default="", help="comma-separated extra flags for llm-verify, e.g. --self-consistency,--negatives")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--window-dir", default="sample_windows")
    p.add_argument("--skip", default=None, help="run: comma-separated stages to skip")
    p.add_argument("--wayback", action="store_true", help="cross-validate: query Wayback CDX (network)")
    p.add_argument("--indico", action="store_true", help="cross-validate: query Indico API (network)")
    return p


def _latest(prefix):
    dirs = [d for d in os.listdir(".") if d.startswith(prefix) and os.path.isdir(d)]
    if not dirs:
        return None
    dirs.sort(reverse=True)
    return dirs[0]


BENCH_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "..", "llm_extract_benchmark"))


def _run_bench_script(script, *args):
    path = os.path.join(BENCH_DIR, script)
    return subprocess.call([sys.executable, path] + [str(a) for a in args], cwd=BENCH_DIR)


def _latest_file(dirs, names):
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for name in names:
            path = os.path.join(d, name)
            if os.path.isfile(path):
                return path
    return None


def _find_extraction():
    dirs = [d for d in (_latest("igf_llm_extract_"), _latest("igf_verify_"),
                        _latest("igf_analysis_")) if d]
    return _latest_file(dirs, ["extraction.jsonl"])


def _find_all_json():
    latest = _latest("igf_extracted_")
    return _latest_file([latest], ["all.json"])


def _find_raw_results():
    for cand in (os.path.join(BENCH_DIR, "results", "raw_results.json"),
                 os.path.join(BENCH_DIR, "results_kw", "kw_raw_results.json")):
        if os.path.isfile(cand):
            return cand
    return None


def run_benchmark(models="qwen3.5:9b", methods="fewshot", docs="doc_access_2007,doc_closing_2006",
                  gold=None, out=None):
    argv = ["--models", models, "--methods", methods, "--docs", docs]
    if gold:
        argv += ["--gold", gold]
    if out:
        argv += ["--out", out]
    return _run_bench_script("benchmark.py", *argv)


def run_verify(models="qwen3.5:9b,qwen2.5:latest", method="oneshot", extra=None):
    argv = ["--models", models, "--method", method]
    argv += [str(x) for x in (extra or [])]
    return _run_bench_script("verify.py", *argv)


def run_kw(model="qwen3.5:9b", methods="fewshot"):
    return _run_bench_script("run_kw_rerun.py", model, methods)


def run_score(gold=None, raw=None, base=None):
    argv = [a for a in (gold, raw, base) if a]
    return _run_bench_script("kw_similarity.py", *argv)


def _stage_scrape(args):
    pipeline.run(args)
    return 0


def _stage_classify(args):
    src = args.classify_dir or _latest("igf_full_")
    if not src:
        return 3
    process.run_classify(src, args.classify_out, args.workers, dry_run=args.dry_run)
    return 0


def _stage_extract(args):
    src = args.classify_dir or _latest("igf_classified_")
    if not src:
        return 3
    process.run_extract(src, args.extract_out, args.workers)
    return 0


def _stage_validate(args):
    v = []
    if args.full:
        v += ["--full", args.full]
    if args.classified:
        v += ["--classified", args.classified]
    if args.extracted:
        v += ["--extracted", args.extracted]
    if args.no_drupal:
        v.append("--no-drupal")
    if not v and not any(_latest(p) for p in ("igf_full_", "igf_classified_", "igf_extracted_")):
        return 3
    process.run_validation_report(v)
    return 0


def _stage_full_extract(args):
    src = args.classify_dir or _latest("igf_classified_")
    if not src:
        return 3
    out = args.output or "igf_llm_extract_%s" % datetime.now().strftime("%Y%m%d_%H%M%S")
    v = ["--classified", src, "--out", out, "--model", args.models or "qwen3.5:9b",
         "--workers", str(args.workers)]
    if args.limit:
        v += ["--limit", str(args.limit)]
    return llm.full_extract_main(v)


def _stage_verify_extract(args):
    extraction = args.input or _find_extraction()
    classified = args.classify_dir or _latest("igf_classified_")
    if not extraction or not classified:
        return 3
    out = args.output or "igf_verify_%s" % datetime.now().strftime("%Y%m%d_%H%M%S")
    v = ["--extraction", extraction, "--classified", classified, "--out", out]
    if args.gold:
        v += ["--gold", args.gold]
    return llm.verify_extract_main(v)


def _stage_downstream(args):
    extraction = args.input or _find_extraction()
    all_json = args.extracted or _find_all_json()
    if not extraction and not all_json:
        return 3
    out = args.output or "igf_analysis_%s" % datetime.now().strftime("%Y%m%d_%H%M%S")
    v = ["--out", out]
    if extraction:
        v += ["--extraction", extraction]
    if all_json:
        v += ["--json", all_json]
    return analyze.downstream_main(v)


def _stage_baselines(args):
    gold = args.gold
    base = args.classified or args.classify_dir
    if not gold or not base or not os.path.isdir(base):
        return 3
    return analyze.run_baselines(gold, base, out_dir=args.bench_out)


def _stage_significance(args):
    raw = args.input or _find_raw_results()
    if not raw:
        return 3
    out = args.output or "significance_report.txt"
    return _run_bench_script("significance.py", raw, "--gold",
                             args.gold or "gold_keywords.json", "--out", out)


RUN_HANDLERS = {
    "scrape": _stage_scrape,
    "classify": _stage_classify,
    "extract": _stage_extract,
    "validate": _stage_validate,
    "full-extract": _stage_full_extract,
    "verify-extract": _stage_verify_extract,
    "downstream": _stage_downstream,
    "baselines": _stage_baselines,
    "significance": _stage_significance,
}


def _cmd_run(args):
    skip = set(s.strip() for s in (args.skip or "").split(",") if s.strip())
    unknown = skip - set(RUN_STAGES)
    if unknown:
        print("run: unknown stages in --skip: %s" % ",".join(sorted(unknown)))
        return 2
    for stage in RUN_STAGES:
        if stage in skip:
            print("STAGE %-15s skipped" % stage)
            continue
        t0 = time.time()
        code = RUN_HANDLERS[stage](args)
        dt = time.time() - t0
        if code == 0:
            print("STAGE %-15s ok (%.1fs)" % (stage, dt))
        elif code == 3:
            print("STAGE %-15s skipped, missing input (%.1fs)" % (stage, dt))
        else:
            print("STAGE %-15s FAIL (%.1fs)" % (stage, dt))
            return 1
    return 0


def _cmd_selftest():
    import unittest
    from tests import test_download
    suite = unittest.defaultTestLoader.loadTestsFromModule(test_download)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def _cmd_probe(args):
    url = args.url
    if not url:
        print("probe needs --url")
        return 2
    wb = args.wayback_year
    r = crawl._fetch(url, wb_year=wb)
    if r is None:
        err = crawl._fetch_err[0] if crawl._fetch_err[1] else "unknown"
        print("FETCH FAILED (%s): %s" % (err, url))
        return 1
    text = getattr(r, "text", "") or ""
    print("status      : %s" % getattr(r, "status_code", "?"))
    print("content-type: %s" % getattr(r, "headers", {}).get("Content-Type", "?"))
    print("length      : %d chars" % len(text))
    low = text[:5000].lower()
    print("has <main>  : %s" % ("<main" in low or "main-content" in low))
    print("blocked hint: %s" % ("cf-browser-verify" in low or "just a moment" in low or "access denied" in low))
    print("head        : %r" % text[:200])
    return 0


def dispatch(args):
    cmd = args.command or "scrape"
    if cmd == "scrape":
        pipeline.run(args)
        return 0
    if cmd == "run":
        return _cmd_run(args)
    if cmd == "classify":
        if not (args.classify_dir or _latest("igf_full_")):
            print("No igf_full_* directory found; pass --classify-dir")
            return 2
        return _stage_classify(args)
    if cmd == "extract":
        if not (args.classify_dir or _latest("igf_classified_")):
            print("No igf_classified_* directory found; pass --classify-dir")
            return 2
        return _stage_extract(args)
    if cmd == "validate":
        _stage_validate(args)
        return 0
    if cmd == "denoise":
        v = []
        if args.input:
            v += ["--input", args.input]
        if args.output:
            v += ["--output", args.output]
        if args.min_body:
            v += ["--min-body", str(args.min_body)]
        if args.dry_run:
            v.append("--dry-run")
        process.denoise_main(v)
        return 0
    if cmd == "recover":
        v = []
        if args.input:
            v += ["--input", args.input]
        if args.base:
            v += ["--base", args.base]
        if args.output:
            v += ["--output", args.output]
        process.transcripts_main(v)
        return 0
    if cmd == "analyze":
        v = []
        if args.input:
            v += ["--input", args.input]
        if args.output:
            v += ["--output", args.output]
        if args.top_k:
            v += ["--top-k", str(args.top_k)]
        analyze.analysis_main(v)
        return 0
    if cmd == "retry":
        if not args.retry_failed:
            print("retry needs --retry-failed <failed_urls.tsv>")
            return 2
        pipeline.run(args)
        return 0
    if cmd == "selftest":
        return _cmd_selftest()
    if cmd == "probe":
        return _cmd_probe(args)
    if cmd == "llm-bench":
        return run_benchmark(
            args.models or "qwen3.5:9b",
            args.methods or "fewshot",
            args.docs or "doc_access_2007,doc_closing_2006",
            args.gold, args.bench_out)
    if cmd == "llm-verify":
        extra = [x for x in args.llm_extra.split(",") if x]
        return run_verify(
            args.models or "qwen3.5:9b,qwen2.5:latest",
            args.methods or "oneshot", extra)
    if cmd == "llm-kw":
        return run_kw(args.models or "qwen3.5:9b", args.methods or "fewshot")
    if cmd == "llm-score":
        return run_score(args.gold, args.input, args.classified)
    if cmd == "gold-sample":
        v = ["sample", args.classify_dir or _latest("igf_classified_") or "", "--target", str(args.limit or 48),
             "--seed", str(args.seed), "--out", args.output or "sample.tsv",
             "--window-dir", args.window_dir]
        return llm.main(v)
    if cmd == "gold-annotate-a":
        v = ["annotate-a", args.input or "sample.tsv", "--classified", args.classify_dir or _latest("igf_classified_") or "",
             "--window-dir", args.window_dir, "--out", args.output or "gold_annotator_A.json"]
        return llm.main(v)
    if cmd == "gold-annotate-b":
        v = ["annotate-b", args.input or "sample.tsv", "--classified", args.classify_dir or _latest("igf_classified_") or "",
             "--window-dir", args.window_dir, "--out", args.output or "gold_annotator_B.json"]
        return llm.main(v)
    if cmd == "gold-kappa":
        v = ["kappa", args.input or "gold_annotator_A.json", args.gold or "gold_annotator_B.json"]
        return llm.main(v)
    if cmd == "gold-stats":
        return llm.main(["stats", args.classify_dir or _latest("igf_classified_") or ""])
    if cmd == "full-extract":
        src = args.classify_dir or _latest("igf_classified_") or ""
        out = args.output or "igf_llm_extract_%s" % datetime.now().strftime("%Y%m%d_%H%M%S")
        v = ["--classified", src, "--out", out, "--model", args.models or "qwen3.5:9b",
             "--workers", str(args.workers)]
        if args.limit:
            v += ["--limit", str(args.limit)]
        return llm.full_extract_main(v)
    if cmd == "verify-extract":
        out = args.output or "igf_verify_%s" % datetime.now().strftime("%Y%m%d_%H%M%S")
        v = ["--extraction", args.input or "", "--classified", args.classify_dir or _latest("igf_classified_") or "",
             "--out", out]
        if args.gold:
            v += ["--gold", args.gold]
        return llm.verify_extract_main(v)
    if cmd == "downstream":
        out = args.output or "igf_analysis_%s" % datetime.now().strftime("%Y%m%d_%H%M%S")
        v = ["--out", out]
        if args.input:
            v += ["--extraction", args.input]
        if args.extracted:
            v += ["--json", args.extracted]
        return analyze.downstream_main(v)
    if cmd == "baselines":
        code = _stage_baselines(args)
        if code == 3:
            if not args.gold:
                print("baselines needs --gold <gold_keywords.json>")
            else:
                print("baselines needs --classified <igf_classified_dir> (source HTML for windows)")
            return 2
        return code
    if cmd == "hot-topics":
        v = []
        if args.input:
            v += ["--extraction", args.input]
        if args.extracted:
            v += ["--json", args.extracted]
        v += ["--out", args.output or "igf_analysis_%s" % datetime.now().strftime("%Y%m%d_%H%M%S")]
        return analyze.hot_topics_main(v)
    if cmd == "cross-validate":
        full = args.full or _latest("igf_full_")
        if not full:
            print("cross-validate needs --full <igf_full_dir>")
            return 2
        v = ["--full", full, "--out", args.output or "igf_verify_%s" % datetime.now().strftime("%Y%m%d_%H%M%S")]
        if args.wayback:
            v.append("--wayback")
        if args.indico:
            v.append("--indico")
        if args.limit:
            v += ["--limit", str(args.limit)]
        return analyze.cross_validate_main(v)
    print("unknown command", cmd)
    return 2


def main(argv=None):
    args = build_parser().parse_args(argv)
    code = dispatch(args)
    if code:
        sys.exit(code)


if __name__ == "__main__":
    main()

"""argparse entry point.

Legacy flags from the single-file era and debug subcommands are both
supported:

    python scrape_igf.py --step sessions --year 2023
    python main.py scrape --years 2017-2019 --limit 20 --workers 3
    python main.py probe --url https://intgovforum.org/...
    python main.py selftest
"""
import argparse
import os
import sys

from .config import WORKERS
from .models import network
from .models.classify import run_classify
from .models.extract import run_extract
from .controllers import pipeline, llm_runner
from .views import console


COMMANDS = [
    "scrape", "classify", "extract", "validate", "denoise", "recover",
    "analyze", "retry", "selftest", "probe",
    "llm-bench", "llm-verify", "llm-kw", "llm-score", "baselines",
]


def build_parser():
    p = argparse.ArgumentParser(prog="igf", description="IGF full-data pipeline (scrape / classify / extract / validate / LLM)")
    p.add_argument("command", nargs="?", default=None, choices=COMMANDS,
                   help="subcommand; omit to run the legacy full pipeline")
    # legacy pipeline flags (unchanged)
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
    # validation / post-processing
    p.add_argument("--full", default=None)
    p.add_argument("--classified", default=None)
    p.add_argument("--extracted", default=None)
    p.add_argument("--no-drupal", action="store_true")
    p.add_argument("--input", default=None, help="input JSON for denoise/recover/analyze/llm-score")
    p.add_argument("--base", default=None, help="classified base dir for recover")
    p.add_argument("--top-k", type=int, default=None)
    p.add_argument("--min-body", type=int, default=None)
    # probe
    p.add_argument("--url", default=None)
    p.add_argument("--wayback-year", default=None)
    # LLM experiments
    p.add_argument("--models", default=None)
    p.add_argument("--methods", default=None)
    p.add_argument("--docs", default=None)
    p.add_argument("--gold", default=None)
    p.add_argument("--bench-out", default=None)
    p.add_argument("--llm-extra", default="", help="comma-separated extra flags for llm-verify, e.g. --self-consistency,--negatives")
    return p


def _latest(prefix):
    dirs = [d for d in os.listdir(".") if d.startswith(prefix) and os.path.isdir(d)]
    if not dirs:
        return None
    dirs.sort(reverse=True)
    return dirs[0]


def _cmd_validate(args):
    from .controllers.validate import main as vmain
    v = []
    if args.full:
        v += ["--full", args.full]
    if args.classified:
        v += ["--classified", args.classified]
    if args.extracted:
        v += ["--extracted", args.extracted]
    if args.no_drupal:
        v.append("--no-drupal")
    vmain(v)


def _cmd_denoise(args):
    from .models.denoise import main as dmain
    v = []
    if args.input:
        v += ["--input", args.input]
    if args.output:
        v += ["--output", args.output]
    if args.min_body:
        v += ["--min-body", str(args.min_body)]
    if args.dry_run:
        v.append("--dry-run")
    dmain(v)


def _cmd_recover(args):
    from .models.transcripts import main as tmain
    v = []
    if args.input:
        v += ["--input", args.input]
    if args.base:
        v += ["--base", args.base]
    if args.output:
        v += ["--output", args.output]
    tmain(v)


def _cmd_analyze(args):
    from .models.analysis import main as amain
    v = []
    if args.input:
        v += ["--input", args.input]
    if args.output:
        v += ["--output", args.output]
    if args.top_k:
        v += ["--top-k", str(args.top_k)]
    amain(v)


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
    r = network._fetch(url, wb_year=wb)
    if r is None:
        err = network._fetch_err[0] if network._fetch_err[1] else "unknown"
        print(f"FETCH FAILED ({err}): {url}")
        return 1
    text = getattr(r, "text", "") or ""
    print(f"status      : {getattr(r, 'status_code', '?')}")
    print(f"content-type: {getattr(r, 'headers', {}).get('Content-Type', '?')}")
    print(f"length      : {len(text)} chars")
    low = text[:5000].lower()
    print(f"has <main>  : {'<main' in low or 'main-content' in low}")
    print(f"blocked hint: {'cf-browser-verify' in low or 'just a moment' in low or 'access denied' in low}")
    print(f"head        : {text[:200]!r}")
    return 0


def dispatch(args):
    cmd = args.command or "scrape"
    if cmd == "scrape":
        pipeline.run(args)
        return 0
    if cmd == "classify":
        src = args.classify_dir or _latest("igf_full_")
        if not src:
            print("No igf_full_* directory found; pass --classify-dir")
            return 2
        run_classify(src, args.classify_out, args.workers, dry_run=args.dry_run)
        return 0
    if cmd == "extract":
        src = args.classify_dir or _latest("igf_classified_")
        if not src:
            print("No igf_classified_* directory found; pass --classify-dir")
            return 2
        run_extract(src, args.extract_out, args.workers)
        return 0
    if cmd == "validate":
        _cmd_validate(args)
        return 0
    if cmd == "denoise":
        _cmd_denoise(args)
        return 0
    if cmd == "recover":
        _cmd_recover(args)
        return 0
    if cmd == "analyze":
        _cmd_analyze(args)
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
        return llm_runner.run_benchmark(
            args.models or "qwen3.5:9b",
            args.methods or "fewshot",
            args.docs or "doc_access_2007,doc_closing_2006",
            args.gold, args.bench_out)
    if cmd == "llm-verify":
        extra = [x for x in args.llm_extra.split(",") if x]
        return llm_runner.run_verify(
            args.models or "qwen3.5:9b,qwen2.5:latest",
            args.methods or "oneshot", extra)
    if cmd == "llm-kw":
        return llm_runner.run_kw(args.models or "qwen3.5:9b", args.methods or "fewshot")
    if cmd == "llm-score":
        return llm_runner.run_score(args.gold, args.input, args.classified)
    if cmd == "baselines":
        from .controllers.baselines import run_baselines
        gold = args.gold
        if not gold:
            print("baselines needs --gold <gold_keywords.json>")
            return 2
        base = args.classified or args.classify_dir
        if not base or not os.path.isdir(base):
            print("baselines needs --classified <igf_classified_dir> (source HTML for windows)")
            return 2
        return run_baselines(gold, base, out_dir=args.bench_out)
    print("unknown command", cmd)
    return 2


def main(argv=None):
    args = build_parser().parse_args(argv)
    code = dispatch(args)
    if code:
        sys.exit(code)


if __name__ == "__main__":
    main()

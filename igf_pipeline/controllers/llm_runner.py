"""LLM experiment controller: dispatches the self-contained benchmark project
(llm_extract_benchmark/) in a subprocess using the current interpreter.

The experiment itself is a completed, reproducible sub-project: gold labels,
gold keywords, results and EXPERIMENT_DESIGN.md stay under
llm_extract_benchmark/.  This controller only gives it a stable CLI surface
so model selection / keyword extraction can be re-run per model or method
without re-running the whole matrix.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.normpath(os.path.join(HERE, "..", "..", "llm_extract_benchmark"))


def _run(script, *args):
    path = os.path.join(BENCH, script)
    return subprocess.call([sys.executable, path] + [str(a) for a in args])


def run_benchmark(models="qwen3.5:9b", methods="fewshot", docs="doc_access_2007,doc_closing_2006",
                  gold=None, out=None):
    """Field-level structured-extraction benchmark (benchmark.py)."""
    argv = ["--models", models, "--methods", methods, "--docs", docs]
    if gold:
        argv += ["--gold", gold]
    if out:
        argv += ["--out", out]
    return _run("benchmark.py", *argv)


def run_verify(models="qwen3.5:9b,qwen2.5:latest", method="oneshot", extra=None):
    """Black-box verification: grounding, self-consistency, cross-model,
    negative tests (verify.py).  extra= list of extra verify.py flags,
    e.g. ['--self-consistency', '--negatives']."""
    argv = ["--models", models, "--method", method]
    argv += [str(x) for x in (extra or [])]
    return _run("verify.py", *argv)


def run_kw(model="qwen3.5:9b", methods="fewshot"):
    """Keyword-extraction experiment with gold keyword labels (run_kw_rerun.py)."""
    return _run("run_kw_rerun.py", model, methods)


def run_score(gold=None, raw=None, base=None):
    """Similarity scoring of keyword results (kw_similarity.py)."""
    argv = []
    if gold:
        argv.append(gold)
    if raw:
        argv.append(raw)
    if base:
        argv.append(base)
    return _run("kw_similarity.py", *argv)

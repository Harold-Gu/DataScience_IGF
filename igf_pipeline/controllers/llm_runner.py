"""CLI bridge that runs the llm_extract_benchmark/ scripts in a subprocess."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.normpath(os.path.join(HERE, "..", "..", "llm_extract_benchmark"))


def _run(script, *args):
    path = os.path.join(BENCH, script)
    return subprocess.call([sys.executable, path] + [str(a) for a in args], cwd=BENCH)


def run_benchmark(models="qwen3.5:9b", methods="fewshot", docs="doc_access_2007,doc_closing_2006",
                  gold=None, out=None):
    argv = ["--models", models, "--methods", methods, "--docs", docs]
    if gold:
        argv += ["--gold", gold]
    if out:
        argv += ["--out", out]
    return _run("benchmark.py", *argv)


def run_verify(models="qwen3.5:9b,qwen2.5:latest", method="oneshot", extra=None):
    argv = ["--models", models, "--method", method]
    argv += [str(x) for x in (extra or [])]
    return _run("verify.py", *argv)


def run_kw(model="qwen3.5:9b", methods="fewshot"):
    return _run("run_kw_rerun.py", model, methods)


def run_score(gold=None, raw=None, base=None):
    argv = []
    if gold:
        argv.append(gold)
    if raw:
        argv.append(raw)
    if base:
        argv.append(base)
    return _run("kw_similarity.py", *argv)

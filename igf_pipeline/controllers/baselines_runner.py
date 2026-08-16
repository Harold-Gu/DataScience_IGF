# -*- coding: utf-8 -*-
"""Alternate baseline runner: keyword baselines over the gold windows, the
same five metrics as the LLM outputs, comparison report + per-doc JSON."""
import argparse
import json
import os

from ..models.baselines import (BASELINES, score_keywords, window_of)


def _load_gold(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get("docs", [])
    return data


def run(gold_path, base_dir=None, topn=12, only=None, out_prefix="baselines"):
    docs = _load_gold(gold_path)
    if not docs:
        print("[ERR] no docs in gold file")
        return 2
    names = [n for n in BASELINES if not only or n in only]
    metrics = ["phrase_f1", "token_f1", "soft_recall", "soft_precision", "exact_hit_rate"]
    per_doc = {}
    agg = {n: {m: 0.0 for m in metrics} for n in names}
    n_scored = 0
    for d in docs:
        win = window_of(d, base_dir)
        if not win.strip():
            continue
        n_scored += 1
        for n in names:
            kws = BASELINES[n](win, topn=topn)
            s = score_keywords(d.get("keywords") or [], kws)
            for m in metrics:
                agg[n][m] += s[m]
            per_doc.setdefault(d["doc"], {})[n] = s
    if not n_scored:
        print("[ERR] no windows available (needs window_text or a valid base_dir)")
        return 2
    for n in names:
        for m in metrics:
            agg[n][m] = round(agg[n][m] / n_scored, 4)
    lines = [f"Baseline keyword extraction vs gold (n_docs={n_scored}, topn={topn})", ""]
    lines.append(f"{'baseline':10s} " + " ".join(f"{m:>10s}" for m in metrics))
    for n in names:
        lines.append(f"{n:10s} " + " ".join(f"{agg[n][m]:>10.4f}" for m in metrics))
    lines.append("")
    lines.append("Baselines: TF-IDF (Sparck Jones 1972), RAKE (Rose et al. 2010),")
    lines.append("TextRank (Mihalcea & Tarau 2004), KeyBERT (Grootendorst 2021, zenodo DOI).")
    text = "\n".join(lines)
    with open(out_prefix + "_report.txt", "w", encoding="utf-8") as f:
        f.write(text + "\n")
    with open(out_prefix + "_scores.json", "w", encoding="utf-8") as f:
        json.dump({"agg": agg, "per_doc": per_doc}, f, ensure_ascii=False, indent=1)
    print(text)
    print(f"\nWrote {out_prefix}_report.txt and {out_prefix}_scores.json")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("gold", help="gold json (list of docs or {docs:[...]})")
    ap.add_argument("--base-dir", default=None,
                    help="classified dir for window extraction when window_text is absent")
    ap.add_argument("--topn", type=int, default=12)
    ap.add_argument("--only", default=None,
                    help="comma-separated subset: tfidf,rake,textrank,keybert")
    ap.add_argument("--out", default="baselines")
    args = ap.parse_args(argv)
    return run(args.gold, args.base_dir, args.topn,
               args.only.split(",") if args.only else None, args.out)

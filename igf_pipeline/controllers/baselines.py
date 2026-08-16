# -*- coding: utf-8 -*-
"""Baseline evaluation controller.

Reads the gold keywords, extracts the annotation window from each source
HTML, runs RAKE / TextRank / KeyBERT and scores them with the same five
lexical metrics used for the LLM experiments. Writes baseline_runs.json,
baseline_metrics.csv and baseline_report.txt.

    python main.py baselines --gold gold_keywords.json --classified <dir>
"""
import csv
import json
import os
import re

from ..models.baselines import BASELINES, _score_keywords


def _extract_window(gold_entry, base_dir):
    fpath = os.path.join(base_dir, gold_entry.get("file", ""))
    if not os.path.exists(fpath):
        return ""
    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
        html = f.read()
    text = re.sub(r"(?is)<script.*?</script>", " ", html)
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    for a, b in (("&amp;", "&"), ("&gt;", ">"), ("&lt;", "<"),
                 ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(a, b)
    text = re.sub(r"&#\d+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:gold_entry.get("window_chars", 4000)]


def run_baselines(gold_path, base_dir, out_dir=None, topn=12, seed=1):
    with open(gold_path, "r", encoding="utf-8-sig") as f:
        gold = json.load(f)
    gold = gold if isinstance(gold, list) else gold.get("docs", [])
    if not gold:
        print("[ERR] no gold documents found")
        return 2
    if out_dir is None:
        out_dir = os.path.join(os.path.dirname(os.path.abspath(gold_path)), "results_kw")
    os.makedirs(out_dir, exist_ok=True)
    runs = []
    metric_rows = []
    report_lines = ["Baseline keyword extraction vs gold", ""]
    sums = {name: {m: 0.0 for m in ("phrase_f1", "token_f1", "soft_recall",
                                    "soft_precision", "exact_hit_rate")}
            for name in BASELINES}
    n_win = 0
    for g in gold:
        win = _extract_window(g, base_dir)
        if not win:
            continue
        n_win += 1
        gk = g.get("keywords") or []
        for name, fn in BASELINES.items():
            pred = fn(win, topn=topn)
            metrics = _score_keywords(gk, pred)
            for m, v in metrics.items():
                sums[name][m] += v
            runs.append({"model": name, "method": "baseline", "doc": g.get("doc"),
                         "keywords": pred, "parsed": True, "source": "baseline",
                         "latency_s": None, "error": None,
                         "metrics": metrics})
            metric_rows.append([g.get("doc"), name, topn] +
                               [metrics[m] for m in
                                ("phrase_f1", "token_f1", "soft_recall",
                                 "soft_precision", "exact_hit_rate")])
    report_lines.append(f"n_docs_with_window={n_win} topn={topn}")
    report_lines.append("")
    report_lines.append("baseline   phrase_f1  token_f1  soft_recall  soft_precision  exact_hit")
    for name in BASELINES:
        mean = {m: round(sums[name][m] / max(1, n_win), 4) for m in sums[name]}
        report_lines.append(f"{name:9s} {mean['phrase_f1']:.4f}     {mean['token_f1']:.4f}  "
                            f"{mean['soft_recall']:.4f}      {mean['soft_precision']:.4f}       "
                            f"{mean['exact_hit_rate']:.4f}")
    runs_path = os.path.join(out_dir, "baseline_runs.json")
    with open(runs_path, "w", encoding="utf-8") as f:
        json.dump({"gold": os.path.basename(gold_path), "base_dir": base_dir,
                   "runs": runs}, f, ensure_ascii=False, indent=1)
    csv_path = os.path.join(out_dir, "baseline_metrics.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["doc", "baseline", "topn", "phrase_f1", "token_f1",
                    "soft_recall", "soft_precision", "exact_hit_rate"])
        w.writerows(metric_rows)
    report_path = os.path.join(out_dir, "baseline_report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines) + "\n")
    print("\n".join(report_lines))
    print(f"\nwrote: {runs_path}")
    print(f"wrote: {csv_path}")
    print(f"wrote: {report_path}")
    return 0

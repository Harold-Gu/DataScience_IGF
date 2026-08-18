# -*- coding: utf-8 -*-
"""Paired bootstrap significance between systems (Berg-Kirkpatrick et al. 2012)."""
import argparse, json, os, random, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kw_similarity import (score_keywords, load_gold, tf_baseline, extract_window)

METRICS = ["phrase_f1", "token_f1", "soft_recall", "soft_precision", "exact_hit_rate"]


def system_scores(gold_map, runs, metric):
    sys_scores = {}
    for r in runs:
        if not r.get("parsed", True):
            continue
        doc = r["doc"]
        if doc not in gold_map:
            continue
        g = gold_map[doc]
        s = score_keywords(g.get("keywords") or [], r.get("keywords") or [])[metric]
        key = (r["model"], r.get("method", "?"))
        sys_scores.setdefault(key, {})[doc] = s
    return sys_scores


def tf_scores(gold_map, base_dir, metric):
    out = {}
    for doc, g in gold_map.items():
        try:
            win = extract_window({"file": g["file"], "window_chars": g["window_chars"]}, base_dir)
        except Exception:
            win = ""
        s = score_keywords(g.get("keywords") or [], tf_baseline(win))[metric]
        out[doc] = s
    return {("tf-baseline", "tf"): out}


def paired_bootstrap(a_docs, b_docs, B, seed):
    """a_docs/b_docs: lists of scores aligned on the same docs. Returns stats."""
    n = len(a_docs)
    if n == 0:
        return {"n": 0}
    obs_delta = sum(a_docs) / n - sum(b_docs) / n
    rng = random.Random(seed)
    idx = list(range(n))
    deltas = []
    for _ in range(B):
        pick = [idx[rng.randrange(n)] for _ in range(n)]
        da = sum(a_docs[i] for i in pick) / n
        db = sum(b_docs[i] for i in pick) / n
        deltas.append(da - db)
    p_two = sum(1 for d in deltas if abs(d - obs_delta) >= abs(obs_delta)) / B
    deltas_sorted = sorted(deltas)
    lo = deltas_sorted[int(0.025 * (B - 1))]
    hi = deltas_sorted[int(0.975 * (B - 1))]
    win = sum(1 for d in deltas if d > 0) / B
    return {"n": n, "mean_a": round(sum(a_docs) / n, 4),
            "mean_b": round(sum(b_docs) / n, 4),
            "delta": round(obs_delta, 4), "ci95": [round(lo, 4), round(hi, 4)],
            "p_two_sided": round(p_two, 4), "win_rate_a": round(win, 4)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("raw_results")
    ap.add_argument("--gold", default="gold_keywords.json")
    ap.add_argument("--metric", default="phrase_f1", choices=METRICS)
    ap.add_argument("--B", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default="significance_report.txt")
    args = ap.parse_args()
    raw = json.load(open(args.raw_results, encoding="utf-8-sig"))
    runs = raw["runs"]
    base_dir = raw.get("base_dir", ".")
    gold_path = raw.get("gold", args.gold)
    gold_path = gold_path if os.path.exists(gold_path) else os.path.join(
        os.path.dirname(os.path.abspath(args.raw_results)), os.path.basename(gold_path))
    gold = load_gold(gold_path)
    gold_map = {g["doc"]: g for g in gold}
    systems = system_scores(gold_map, runs, args.metric)
    systems.update(tf_scores(gold_map, base_dir, args.metric))
    lines = []
    lines.append(f"Paired bootstrap significance ({args.metric}), B={args.B}, n_docs={len(gold_map)}")
    lines.append("Method: Berg-Kirkpatrick, Burkett & Klein 2012, EMNLP-CoNLL (D12-1091)")
    lines.append("")
    best = max(systems.items(), key=lambda kv: sum(kv[1].values()) / max(1, len(kv[1])))
    lines.append(f"Reference (best mean): {best[0][0]} / {best[0][1]}")
    lines.append("")
    lines.append("system                  mean    delta   ci95_lo  ci95_hi  p(2s)   win")
    results = {}
    for key, scores in sorted(systems.items()):
        docs = sorted(set(scores) & set(best[1]))
        if len(docs) < 3:
            continue
        a = [best[1][d] for d in docs]
        b = [scores[d] for d in docs]
        st = paired_bootstrap(a, b, args.B, args.seed)
        if not st.get("n"):
            continue
        name = f"{key[0]}/{key[1]}"
        lines.append(f"{name:22s} {st['mean_b']:.4f}  {st['delta']:+.4f}  "
                     f"{st['ci95'][0]:.4f}  {st['ci95'][1]:.4f}  {st['p_two_sided']:.4f}  {st['win_rate_a']:.3f}")
        results[name] = st
    lines.append("")
    lines.append("delta = mean(best) - mean(this system); p(2s) = two-sided paired-bootstrap p-value;")
    lines.append("win = fraction of bootstrap resamples where best beats this system.")
    text = "\n".join(lines)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    with open(args.out.replace(".txt", ".json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(text)


if __name__ == "__main__":
    main()

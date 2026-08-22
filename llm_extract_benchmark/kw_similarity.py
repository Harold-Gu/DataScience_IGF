import json, os, re, csv, sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
_STOPWORDS_PATH = os.path.join(_HERE, "..", "igf_pipeline", "models", "english_stopwords.txt")


def _load_stopwords():
    try:
        from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS
        return set(ENGLISH_STOP_WORDS)
    except Exception:
        pass
    try:
        with open(_STOPWORDS_PATH, encoding="utf-8") as fh:
            words = {line.strip().lower() for line in fh if line.strip()}
        if words:
            return words
    except OSError:
        pass
    return set("the a an and or but of to in on for with as at by is are was "
               "were be been this that these those it its from we you they he "
               "she i not no so".split())


STOPWORDS = _load_stopwords()

def normalize(kw):
    kw = re.sub(r"[^A-Za-z0-9' -]", " ", str(kw).lower())
    return re.sub(r"\s+", " ", kw).strip(" -'")

def load_gold(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)

def load_results(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)

def tokens(s):
    return [t for t in s.split() if t]

def f1(a, b):
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    p = inter / len(a)
    r = inter / len(b)
    return 2 * p * r / (p + r) if (p + r) else 0.0

def soft_match(g, pred_set):
    gt = tokens(g)
    for p in pred_set:
        pt = tokens(p)
        if g == p:
            return 1.0
        if len(gt) >= 2 and len(pt) >= 2:
            if gt[0] == pt[0] and gt[-1] == pt[-1]:
                return 0.7
            if set(gt) & set(pt):
                return 0.5
        if len(gt) == 1 and len(pt) >= 1 and gt[0] in pt:
            return 0.5
    return 0.0

def score_keywords(gold, pred):
    g = {normalize(k["kw"]) for k in gold}
    p = {normalize(k) for k in pred}
    g = {x for x in g if x}
    p = {x for x in p if x}
    if not p:
        return {"phrase_f1": 0.0, "token_f1": 0.0, "soft_recall": 0.0, "soft_precision": 0.0, "exact_hit_rate": 0.0, "n_pred": 0, "n_gold": len(g)}
    phrase_f1 = f1(g, p)
    tg = set()
    for x in g:
        tg.update(tokens(x))
    tp = set()
    for x in p:
        tp.update(tokens(x))
    token_f1 = f1(tg, tp)
    sr = sum(soft_match(x, p) for x in g) / len(g)
    sp = sum(soft_match(x, g) for x in p) / len(p)
    exact = sum(1 for x in g if x in p) / len(g)
    return {"phrase_f1": round(phrase_f1, 4), "token_f1": round(token_f1, 4),
            "soft_recall": round(sr, 4), "soft_precision": round(sp, 4),
            "exact_hit_rate": round(exact, 4), "n_pred": len(p), "n_gold": len(g)}

def tf_baseline(window_text, topn=12):
    words = re.findall(r"[a-z]+", str(window_text).lower())
    freq = Counter(w for w in words if len(w) > 3 and w not in STOPWORDS)
    return [w for w, _ in freq.most_common(topn)]

def extract_window(gold_entry, base_dir):
    fpath = os.path.join(base_dir, gold_entry["file"])
    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
        html = f.read()
    text = re.sub(r"(?is)<script.*?</script>", " ", html)
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&amp;", "&").replace("&gt;", ">").replace("&lt;", "<").replace("&quot;", '"')
    text = re.sub(r"&#\d+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[: gold_entry["window_chars"]]

def main():
    gold_path = sys.argv[1] if len(sys.argv) > 1 else "gold_keywords.json"
    results_path = sys.argv[2] if len(sys.argv) > 2 else "results_kw/kw_raw_results.json"
    base_dir = sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_windows")
    out_dir = os.path.dirname(results_path) or "."
    gold = load_gold(gold_path)
    results = load_results(results_path)
    rows = []
    for item in results.get("runs", []):
        doc = item["doc"]
        g = next(x for x in gold if x["doc"] == doc)
        s = score_keywords(g["keywords"], item["keywords"])
        rows.append({"model": item["model"], "method": item["method"], "doc": doc, **s,
                     "n_expected": g["window_chars"], "parse": "ok" if item.get("parsed") else "fail"})
    for g in gold:
        window = extract_window(g, base_dir)
        base = tf_baseline(window)
        s = score_keywords(g["keywords"], base)
        rows.append({"model": "tf-baseline", "method": "tf", "doc": g["doc"], **s,
                     "n_expected": g["window_chars"], "parse": "ok"})
    agg = {}
    for r in rows:
        key = (r["model"], r["method"])
        a = agg.setdefault(key, {k: [] for k in ("phrase_f1", "token_f1", "soft_recall", "soft_precision", "exact_hit_rate")})
        for k in a:
            a[k].append(r[k])
    csv_path = os.path.join(out_dir, "kw_metrics.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["model", "method", "docs", "phrase_f1", "token_f1", "soft_recall", "soft_precision", "exact_hit_rate", "fail_parses"])
        fails = Counter((r["model"], r["method"]) for r in rows if r["parse"] != "ok")
        for key in sorted(agg, key=lambda k: -sum(agg[k]["phrase_f1"]) / len(agg[k]["phrase_f1"])):
            a = agg[key]
            n = len(a["phrase_f1"])
            w.writerow([key[0], key[1], n] + [round(sum(a[k]) / n, 4) for k in ("phrase_f1", "token_f1", "soft_recall", "soft_precision", "exact_hit_rate")] + [fails.get(key, 0)])
    rep_path = os.path.join(out_dir, "kw_report.txt")
    with open(rep_path, "w", encoding="utf-8") as f:
        f.write("Keyword extraction vs gold labels (similarity-based evaluation)\n")
        f.write("Gold docs: %d, per-doc gold keywords: %s\n\n" % (len(gold), [len(x["keywords"]) for x in gold]))
        for key in sorted(agg, key=lambda k: -sum(agg[k]["phrase_f1"]) / len(agg[k]["phrase_f1"])):
            a = agg[key]
            n = len(a["phrase_f1"])
            f.write("%-18s %-8s n=%-2d phraseF1=%.3f tokenF1=%.3f softR=%.3f softP=%.3f exact=%.3f parse_fail=%d\n" % (
                key[0], key[1], n, sum(a["phrase_f1"]) / n, sum(a["token_f1"]) / n,
                sum(a["soft_recall"]) / n, sum(a["soft_precision"]) / n,
                sum(a["exact_hit_rate"]) / n, fails.get(key, 0)))
    print("wrote", csv_path, "and", rep_path)

if __name__ == "__main__":
    main()

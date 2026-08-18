# -*- coding: utf-8 -*-
"""Stratified sample of a classified crawl: sample.tsv + 4000-char windows."""
import argparse, os, random, re, sys
from html.parser import HTMLParser

YEAR_BANDS = [(2006, 2009), (2010, 2014), (2015, 2019), (2020, 2022), (2023, 2025)]
TYPES = ["workshop", "open-forum", "lightning-talk", "day-0-event",
         "launch-award", "networking", "main-session", "town-hall",
         "transcript", "report", "dc-bpf-nri", "other"]


class TextExtract(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "footer", "header"):
            self.skip += 1
    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "footer", "header") and self.skip:
            self.skip -= 1
    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def body_text(path, limit=50000):
    try:
        raw = open(path, encoding="utf-8", errors="ignore").read()
    except Exception:
        return ""
    p = TextExtract()
    p.feed(raw)
    return re.sub(r"\s+", " ", " ".join(p.parts)).strip()[:limit]


def band_of(year):
    for lo, hi in YEAR_BANDS:
        if lo <= year <= hi:
            return f"{lo}-{hi}"
    return "other"


def scan(classified_dir):
    pool = []
    for root, dirs, files in os.walk(classified_dir):
        if "_invalid" in root.split(os.sep):
            continue
        for f in files:
            if not f.endswith(".html"):
                continue
            rel = os.path.relpath(os.path.join(root, f), classified_dir)
            parts = rel.replace("\\", "/").split("/")
            ptype = parts[0] if parts and parts[0] in TYPES else "other"
            year = None
            for seg in parts:
                m = re.match(r"^(20\d{2})$", seg)
                if m:
                    year = int(m.group(1))
            if year is None or not (2006 <= year <= 2025):
                continue
            pool.append({"rel": rel, "type": ptype, "year": year,
                         "band": band_of(year),
                         "path": os.path.join(root, f)})
    return pool


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("classified_dir")
    ap.add_argument("--target", type=int, default=48)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="sample.tsv")
    ap.add_argument("--window-dir", default="sample_windows")
    args = ap.parse_args()
    if not os.path.isdir(args.classified_dir):
        print(f"[ERR] classified dir not found: {args.classified_dir}")
        print("      Run classify first: python main.py classify --classify-dir igf_full_xxx")
        sys.exit(2)
    pool = scan(args.classified_dir)
    if len(pool) < args.target:
        print(f"[WARN] only {len(pool)} usable html files (< target {args.target}); sampling all")
    strata = {}
    for d in pool:
        strata.setdefault((d["type"], d["band"]), []).append(d)
    per_stratum = max(1, args.target // max(1, len(strata)))
    rng = random.Random(args.seed)
    picked = []
    for key, docs in sorted(strata.items()):
        docs = sorted(docs, key=lambda d: d["rel"])
        rng.shuffle(docs)
        docs = sorted(docs, key=lambda d: body_text(d["path"]) and 0 or 1)
        picked.extend(docs[:per_stratum])
    picked = sorted(picked, key=lambda d: (d["type"], d["year"], d["rel"]))[:args.target]
    os.makedirs(args.window_dir, exist_ok=True)
    rows = ["doc\tfile\tyear\tvenue\tsession_type\trel_path\twindow_chars"]
    for i, d in enumerate(picked, 1):
        text = body_text(d["path"])
        doc_id = f"doc_{d['type']}_{d['year']}_{i:02d}"
        win = text[:4000]
        with open(os.path.join(args.window_dir, doc_id + ".txt"), "w",
                  encoding="utf-8") as wf:
            wf.write(win)
        rows.append("\t".join([doc_id, os.path.basename(d["path"]), str(d["year"]),
                               "", d["type"], d["rel"], str(len(win))]))
    with open(args.out, "w", encoding="utf-8", newline="") as of:
        of.write("\n".join(rows) + "\n")
    print(f"[OK] sampled {len(picked)} docs from {len(strata)} strata")
    print(f"     manifest : {args.out}")
    print(f"     windows  : {args.window_dir}/ (4000 chars each)")
    from collections import Counter
    c = Counter((d["type"], d["band"]) for d in picked)
    for k, v in sorted(c.items()):
        print(f"       {k[0]:15s} {k[1]:9s} x{v}")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""Gold annotation assistant: init / check / kappa."""
import argparse, json, os, re, sys


def norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def load_sample_tsv(tsv_path):
    rows = []
    with open(tsv_path, encoding="utf-8") as f:
        head = f.readline().rstrip("\n").split("\t")
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < len(head):
                continue
            rows.append(dict(zip(head, parts)))
    return rows


def init(args):
    rows = load_sample_tsv(args.sample)
    out = []
    for r in rows:
        doc = r["doc"]
        win_path = os.path.join(args.window_dir, doc + ".txt")
        win = open(win_path, encoding="utf-8").read() if os.path.exists(win_path) else ""
        out.append({
            "doc": doc,
            "file": r["file"],
            "year": int(r["year"]) if r.get("year", "").isdigit() else None,
            "venue": "",
            "session_type": r["session_type"],
            "window_chars": len(win),
            "window_text": win,
            "fields": {"title": "", "speakers": [], "moderator": None,
                       "themes": [], "summary": ""},
            "keywords": [],
        })
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[OK] created {len(out)} draft records -> {args.out}")
    print("     fill fields + 1-10 keywords per record (as many as the window supports),")
    print("     then run: annotate.py check")


def check(args):
    data = json.load(open(args.gold, encoding="utf-8-sig"))
    docs = data if isinstance(data, list) else data.get("docs", [])
    n_err = 0
    n_thin = 0
    n_ok = 0
    for d in docs:
        win = norm(d.get("window_text", ""))
        kw = d.get("keywords", [])
        if len(kw) > 10:
            print(f"  [ERR] {d.get('doc')}: {len(kw)} keywords (max 10)"); n_err += 1
        if len(kw) == 0:
            print(f"  [WARN] {d.get('doc')}: 0 keywords (thin page, must be justified)")
            n_thin += 1
        seen = set()
        for k in kw:
            kwn = norm(k.get("kw", ""))
            if not kwn:
                print(f"  [ERR] {d.get('doc')}: empty keyword"); n_err += 1; continue
            if kwn in seen:
                print(f"  [WARN] {d.get('doc')}: duplicate keyword {k.get('kw')}")
            seen.add(kwn)
            ev = norm(k.get("evidence", ""))
            if not ev:
                print(f"  [ERR] {d.get('doc')}: {k.get('kw')} has no evidence"); n_err += 1
            elif ev not in win:
                print(f"  [ERR] {d.get('doc')}: evidence not found in window for {k.get('kw')}"); n_err += 1
        fld = d.get("fields", {})
        for req in ("title",):
            if not (fld.get(req) or "").strip():
                print(f"  [ERR] {d.get('doc')}: missing fields.title"); n_err += 1
        if d.get("year") is None:
            print(f"  [ERR] {d.get('doc')}: missing year"); n_err += 1
        if len(kw) >= 1:
            n_ok += 1
    total = sum(len(d.get("keywords", [])) for d in docs)
    print(f"[{'ERR' if n_err else 'OK'}] checked {len(docs)} records: "
          f"{n_ok} with >=1 kw, {n_thin} thin (0 kw), {total} keywords, {n_err} problems")


def kappa(args):
    a = json.load(open(args.gold_a, encoding="utf-8-sig"))
    b = json.load(open(args.gold_b, encoding="utf-8-sig"))
    a = a if isinstance(a, list) else a.get("docs", [])
    b = b if isinstance(b, list) else b.get("docs", [])
    am = {d["doc"]: d for d in a}
    bm = {d["doc"]: d for d in b}
    keys = sorted(set(am) & set(bm))
    if not keys:
        print("[ERR] no common doc ids"); sys.exit(2)
    po_sum = pe_sum = 0.0
    per_doc = []
    for k in keys:
        ka = {norm(x["kw"]) for x in am[k].get("keywords", []) if norm(x["kw"])}
        kb = {norm(x["kw"]) for x in bm[k].get("keywords", []) if norm(x["kw"])}
        items = sorted(ka | kb)
        n = len(items)
        if n == 0:
            per_doc.append((k, None))
            continue
        agree = sum(1 for it in items if (it in ka) == (it in kb))
        po = agree / n
        p_a = len(ka) / n
        p_b = len(kb) / n
        pe = p_a * p_b + (1 - p_a) * (1 - p_b)
        po_sum += po; pe_sum += pe
        per_doc.append((k, (po - pe) / (1 - pe) if pe < 1 else 1.0))
    n_docs = len([x for x in per_doc if x[1] is not None])
    po = po_sum / n_docs
    pe = pe_sum / n_docs
    k = (po - pe) / (1 - pe) if pe < 1 else 1.0
    print(f"[KAPPA] n_docs={n_docs} Po={po:.3f} Pe={pe:.3f} Kappa={k:.3f}")
    for doc, kv in per_doc:
        if kv is not None and (kv < 0.6 or kv > 0.9):
            print(f"    {doc:35s} kappa={kv:.2f}")
    print("     interpretation: <0 poor, 0.0-0.2 slight, 0.2-0.4 fair,")
    print("     0.4-0.6 moderate, 0.6-0.8 substantial, >0.8 near-perfect (Landis & Koch 1977)")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("init")
    p1.add_argument("sample")
    p1.add_argument("--window-dir", default="sample_windows")
    p1.add_argument("--out", default="gold_draft.json")
    p1.set_defaults(fn=init)
    p2 = sub.add_parser("check")
    p2.add_argument("gold")
    p2.set_defaults(fn=check)
    p3 = sub.add_parser("kappa")
    p3.add_argument("gold_a")
    p3.add_argument("gold_b")
    p3.set_defaults(fn=kappa)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

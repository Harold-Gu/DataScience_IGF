import os, re, json, time, argparse
from collections import defaultdict, Counter
from pathlib import Path
from bs4 import BeautifulSoup

SEP = "=" * 70
SEP2 = "-" * 70

def sp(s):
    try: return str(s)
    except: return repr(s)

def scan_html(class_dir):
    """Scan and validate HTML files, return stats."""
    all_html = list(Path(class_dir).rglob("*.html"))
    counts = Counter(); bad_files = defaultdict(list)
    size_dist = Counter(); body_len_dist = Counter()
    type_stats = defaultdict(lambda: {"total":0,"bad":0,"total_size":0,"total_body":0,
        "drupal_count":0,"drupal_fields":0,"heading_count":0,"link_count":0})
    drupal_fields_by_type = defaultdict(Counter)
    drupal_labels_by_type = defaultdict(Counter)

    for i, fp in enumerate(all_html, 1):
        try:
            size = fp.stat().st_size
            try: rel = str(fp.relative_to(class_dir)).replace(chr(92), "/")
            except: rel = fp.name
            ptype = rel.split("/")[0] if "/" in rel else "root"
            if size < 100: size_dist["<100B"] += 1
            elif size < 500: size_dist["100-500B"] += 1
            elif size < 2000: size_dist["500B-2KB"] += 1
            elif size < 10000: size_dist["2-10KB"] += 1
            elif size < 50000: size_dist["10-50KB"] += 1
            elif size < 200000: size_dist["50-200KB"] += 1
            else: size_dist[">200KB"] += 1
            type_stats[ptype]["total"] += 1; type_stats[ptype]["total_size"] += size
            if size < 300:
                counts["empty"] += 1; bad_files["empty"].append(fp.name); type_stats[ptype]["bad"] += 1; continue
            with open(fp, "rb") as fh: raw = fh.read()
            try: html = raw.decode("utf-8")
            except: counts["bad_enc"] += 1; bad_files["bad_enc"].append(fp.name); type_stats[ptype]["bad"] += 1; continue
            low = html[:3000].lower()
            if len(html) < 3000 and ("cf-browser-verify" in low or "just a moment" in low):
                counts["cloudflare"] += 1; bad_files["cloudflare"].append(fp.name); type_stats[ptype]["bad"] += 1; continue
            if "access denied" in low and len(html) < 2000:
                counts["access_denied"] += 1; bad_files["access_denied"].append(fp.name); type_stats[ptype]["bad"] += 1; continue
            text = html
            for tag in ["script","style","noscript","nav","footer","header","svg"]:
                text = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", " ", text, flags=re.DOTALL|re.I)
            text = re.sub(r"<[^>]+>", " ", text); text = re.sub(r"&[a-z]+;", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            bl = len(text)
            if bl < 100: body_len_dist["<100"] += 1
            elif bl < 500: body_len_dist["100-500"] += 1
            elif bl < 2000: body_len_dist["500-2K"] += 1
            elif bl < 10000: body_len_dist["2-10K"] += 1
            elif bl < 50000: body_len_dist["10-50K"] += 1
            else: body_len_dist[">50K"] += 1
            type_stats[ptype]["total_body"] += bl
            tag_count = len(re.findall(r"<\w+", html))
            if tag_count > 20 and bl < 100:
                counts["tags_only"] += 1; bad_files["tags_only"].append(fp.name); type_stats[ptype]["bad"] += 1; continue
            jk = ["AccessibilityMenu","fontSizeLimit","wpemojiSettings","addEventListener","DOMContentLoaded"]
            if sum(1 for kw in jk if kw in html[:5000]) >= 2 and bl < 300:
                counts["js_only"] += 1; bad_files["js_only"].append(fp.name); type_stats[ptype]["bad"] += 1; continue
            if "\ufffd" in html and html.count("\ufffd") > 5:
                counts["repl"] += 1; bad_files["repl"].append(fp.name); type_stats[ptype]["bad"] += 1; continue
            try:
                soup = BeautifulSoup(html, "html.parser")
                has_any_drupal = False
                for elem in soup.select("[class*='field--name-field-']"):
                    field_name = None
                    for cls in elem.get("class", []):
                        m = re.match(r"field--name-field-(.+)", cls)
                        if m: field_name = m.group(1).replace("-","_").strip("_").lower(); break
                    if not field_name: continue
                    has_any_drupal = True
                    drupal_fields_by_type[ptype][field_name] += 1; type_stats[ptype]["drupal_fields"] += 1
                    label_elem = elem.select_one(".field__label")
                    if label_elem:
                        label = label_elem.get_text(strip=True)
                        label = re.sub(r"\s*\(.*?\)","",label).strip()
                        if label: drupal_labels_by_type[ptype][label] += 1
                if has_any_drupal: type_stats[ptype]["drupal_count"] += 1
            except: pass
            try:
                main = soup.find("main") or soup.find(id="main-content") or soup.find("body") or soup
                type_stats[ptype]["heading_count"] += len(main.find_all(["h1","h2","h3"]))
                type_stats[ptype]["link_count"] += len(main.find_all("a", href=True))
            except: pass
            counts["ok"] += 1
            if i % 500 == 0: print("    progress: {}/{}".format(i, len(all_html)))
        except: counts["rerr"] += 1
    return counts, bad_files, size_dist, body_len_dist, type_stats, drupal_fields_by_type, drupal_labels_by_type

def print_quality(label, counts, size_dist, body_len_dist, type_stats, bad_files):
    good = counts["ok"]; bad = sum(v for k,v in counts.items() if k!="ok"); total = good+bad
    print("\n  " + label + ": {} files, {} OK ({:.1f}%)".format(total, good, good/max(total,1)*100))
    print("\n  " + SEP2 + "\n  QUALITY FLAGS\n  " + SEP2)
    for l,key in [("Valid","ok"),("Empty (<300B)","empty"),("Tags-only","tags_only"),
        ("JS-only","js_only"),("Cloudflare","cloudflare"),("Access denied","access_denied"),
        ("Bad encoding","bad_enc"),("Replacement chars","repl"),("Read error","rerr")]:
        print("  {:<30s} {:>6d}".format(l, counts[key]))
    print("\n  " + SEP2 + "\n  FILE SIZE DISTRIBUTION\n  " + SEP2)
    for k in ["<100B","100-500B","500B-2KB","2-10KB","10-50KB","50-200KB",">200KB"]:
        bar = "#"*max(1,size_dist[k]//max(1,total//50))
        print("  {:<12s} {:>5d}  {}".format(k, size_dist[k], bar))
    print("\n  " + SEP2 + "\n  BODY TEXT LENGTH DISTRIBUTION\n  " + SEP2)
    for k in ["<100","100-500","500-2K","2-10K","10-50K",">50K"]:
        bar = "#"*max(1,body_len_dist[k]//max(1,total//50))
        print("  {:<12s} {:>5d}  {}".format(k, body_len_dist[k], bar))

def print_type_table(type_stats):
    print("\n  " + SEP2 + "\n  PER-DIRECTORY BREAKDOWN\n  " + SEP2)
    print("  {:<25s} {:>6s} {:>5s} {:>6s} {:>8s} {:>8s} {:>7s}".format(
        "Directory","Files","Bad","Bad%","AvgSize","AvgBody","Drupal%"))
    for t in sorted(type_stats.keys(),key=lambda k:-type_stats[k]["total"]):
        s = type_stats[t]; n = s["total"]; bn = s["bad"]; bp = bn/max(n,1)*100
        vn = max(n-bn,1); asize = s["total_size"]/max(n,1); abody = s["total_body"]/vn
        dp = s["drupal_count"]/vn*100
        flag = " !!!" if bp>50 else " !" if bp>20 else ""
        print("  {:<25s} {:>6d} {:>5d} {:>5.0f}%{} {:>7.1f}KB {:>7.0f}c {:>6.1f}%".format(
            t, n, bn, bp, flag, asize/1024, abody, dp))

TYPE_DESCRIPTIONS = {
    "workshop": {"family":"Family A (no-suffix)","note":"Core content. body(2010-16) -> session-content(2017+).","key_fields":["session-content","theme","speakers","policy-questions","sdgs","co-organizers","discussion-facilitation"]},
    "open-forum": {"family":"Family B (-of suffix)","note":"Organized by ITU/UNESCO/OECD. -of suffix = Open Forum specific.","key_fields":["description-of","theme-of","organizers-of","speakers-of","rapporteur-of","report"]},
    "lightning-talk": {"family":"Family C (-0 suffix)","note":"5-15 min talks. -0 from Drupal field collection.","key_fields":["description-0","speakers-0","organizers-0","duration-0","format-0","language"]},
    "day-0-event": {"family":"Mixed (A+C)","note":"Pre-events. Light on Drupal, body text is primary.","key_fields":["description","description-0","organizers","organizers-0"]},
    "launch-award": {"family":"Mixed","note":"Report launches + awards.","key_fields":["description","description-0","organizers","speakers","report"]},
    "networking": {"family":"Family C (-0 suffix)","note":"Informal. Similar to Lightning Talks.","key_fields":["description-0","organizers-0","theme-0","format-0","duration-0"]},
    "main-session": {"family":"Mixed","note":"Plenary/high-level. Sparse Drupal.","key_fields":["description","speakers","theme","organizers"]},
    "town-hall": {"family":"Mixed","note":"Open discussions.","key_fields":["description","organizers","speakers","format"]},
    "report": {"family":"N/A","note":"Post-session reports.","key_fields":["report","body","description"]},
    "transcript": {"family":"N/A","note":"Verbatim transcripts.","key_fields":["body","description"]},
    "schedule": {"family":"N/A","note":"Schedules/agendas.","key_fields":["body","description"]},
    "participants": {"family":"N/A","note":"From indico.un.org.","key_fields":["body"]},
    "dc-bpf-nri": {"family":"Mixed","note":"DC/BPF/NRI intersessional.","key_fields":["description","organizers","theme","report"]},
}

def analyze_drupal(type_stats, drupal_fields_by_type, drupal_labels_by_type):
    print("\n" + SEP + "\n  PART 2: DRUPAL FIELD ANALYSIS (classified types only)\n" + SEP)
    print("  HOW TO VIEW: browser F12 -> search 'field--name-field-'")
    for ptype in sorted(drupal_fields_by_type.keys(), key=lambda k: -sum(drupal_fields_by_type[k].values())):
        fields = drupal_fields_by_type[ptype]; labels = drupal_labels_by_type[ptype]
        if not fields or sum(fields.values()) < 10: continue
        ts = type_stats.get(ptype,{}); n_pages = ts.get("total",0)-ts.get("bad",0)
        drupal_pages = ts.get("drupal_count",0)
        desc = TYPE_DESCRIPTIONS.get(ptype,{"family":"?","note":"","key_fields":[]})
        print("\n  [{}]  {}  |  {} pages ({:.0f}% Drupal)  |  {} fields, {} unique".format(
            ptype.upper(), desc.get('family','?'), drupal_pages, drupal_pages/max(n_pages,1)*100,
            sum(fields.values()), len(fields)))
        print("    Note: " + desc.get('note',''))
        for fn, cnt in fields.most_common(10):
            pct = cnt/max(n_pages,1)*100
            print("    field_{:<45s} {:>5d} ({:>5.0f}%) {}".format(fn, cnt, pct, "#"*max(1,int(pct/5))))
        if labels:
            parts = ["[{}]{}".format(sp(lb),cn) for lb,cn in labels.most_common(5)]
            print("    Labels: " + " | ".join(parts))

def validate_json(extracted_dir, classified_dir=None):
    jp = None
    if extracted_dir:
        c = os.path.join(extracted_dir,"all.json")
        if os.path.exists(c): jp = c
    if not jp and classified_dir:
        for root,dirs,files in os.walk(classified_dir):
            if "all.json" in files: jp = os.path.join(root,"all.json"); break
    if not jp:
        print("\n" + SEP + "\n  PART 3: JSON  -- NOT FOUND\n" + SEP); return
    print("\n" + SEP + "\n  PART 3: JSON VALIDATION\n  Source: " + jp + "\n" + SEP)
    try:
        with open(jp,"r",encoding="utf-8") as f: data = json.load(f)
    except Exception as e:
        print("  JSON PARSE ERROR: " + str(e)); return
    fsize = os.path.getsize(jp)/1024/1024
    print("  Loaded {:,} records in {:.1f}s ({:.1f} MB)".format(len(data), 0, fsize))
    if not data: print("  EMPTY!"); return
    n = len(data)
    body_lens = [len(r.get("body_text","")) for r in data]; sbl = sorted(body_lens)
    has_drupal = sum(1 for r in data if r.get("drupal_fields"))
    empty_body = sum(1 for b in body_lens if b<50)
    abs_paths = sum(1 for r in data if "\\\\" in r.get("rel_path",""))
    hashes = [r.get("content_hash","") for r in data if r.get("content_hash")]
    dup_hashes = len(hashes)-len(set(hashes))
    null_years = sum(1 for r in data if r.get("year") is None)
    print("\n  Body: avg={:,.0f} median={:,} min={} max={:,}".format(sum(body_lens)/n, sbl[n//2], sbl[0], sbl[-1]))
    print("  Empty body(<50): {} ({:.1f}%)  |  Has Drupal: {} ({:.1f}%)".format(empty_body, empty_body/n*100, has_drupal, has_drupal/n*100))
    print("  Quality: Abs paths={}  Dup hashes={}  Null years={}".format(abs_paths, dup_hashes, null_years))
    type_data = defaultdict(list)
    for r in data: type_data[r.get("type","?")].append(r)
    print("\n  TYPE DISTRIBUTION ({} types):".format(len(type_data)))
    print("  {:<22s} {:>6s} {:>9s} {:>9s} {:>7s}".format("Type","Count","Body avg","Body med","Drupal%"))
    for t in sorted(type_data.keys(),key=lambda k:-len(type_data[k])):
        items = type_data[t]; nn = len(items); bls = [len(r.get("body_text","")) for r in items]
        ab = sum(bls)/max(nn,1); med = sorted(bls)[nn//2] if nn>0 else 0
        dp = sum(1 for r in items if r.get("drupal_fields"))/max(nn,1)*100
        print("  {:<22s} {:>6,d} {:>9,.0f} {:>9,} {:>6.1f}%".format(t, nn, ab, med, dp))

def validate_documents(class_dir):
    all_docs = []
    for ext in ["*.pdf","*.doc","*.docx","*.xls","*.xlsx","*.ppt","*.pptx"]:
        for fp in Path(class_dir).rglob(ext): all_docs.append(fp)
    if not all_docs: return
    by_ext = Counter(); total_size = 0
    for fp in all_docs: by_ext[fp.suffix.lower()] += 1; total_size += fp.stat().st_size
    print("\n  Documents: {} files, {:.1f} MB".format(len(all_docs), total_size/1024/1024))
    for ext,cnt in sorted(by_ext.items()): print("    {:<8s} {:>5d}".format(ext, cnt))

def main():
    p = argparse.ArgumentParser(description="IGF Validation Report 鈥?full + classified")
    p.add_argument("--full",default=None); p.add_argument("--classified",default=None)
    p.add_argument("--extracted",default=None); p.add_argument("--no-drupal",action="store_true")
    args = p.parse_args()
    cwd = os.path.dirname(os.path.abspath(__file__))
    if not args.full:
        dirs = sorted([d for d in os.listdir(cwd) if d.startswith("igf_full_") and os.path.isdir(os.path.join(cwd,d))],reverse=True)
        args.full = os.path.join(cwd,dirs[0]) if dirs else None
    if not args.classified:
        dirs = sorted([d for d in os.listdir(cwd) if d.startswith("igf_classified_") and os.path.isdir(os.path.join(cwd,d))],reverse=True)
        args.classified = os.path.join(cwd,dirs[0]) if dirs else None
    if not args.extracted:
        dirs = sorted([d for d in os.listdir(cwd) if d.startswith("igf_extracted_") and os.path.isdir(os.path.join(cwd,d))],reverse=True)
        args.extracted = os.path.join(cwd,dirs[0]) if dirs else None

    print("\n" + "#"*70 + "\n  IGF SCRAPE VALIDATION REPORT\n  Time: " + time.strftime('%Y-%m-%d %H:%M:%S'))
    print("  Full:       " + (args.full or 'N/A'))
    print("  Classified: " + (args.classified or 'N/A'))
    print("  Extracted:  " + (args.extracted or 'N/A') + "\n" + "#"*70)

    if args.full and os.path.isdir(args.full):
        print("\n" + SEP + "\n  PART 1a: FULL SCRAPE DIRECTORY\n  Source: " + args.full + "\n" + SEP)
        c_full, bf_full, sz_full, bl_full, ts_full, df_full, dl_full = scan_html(args.full)
        print_quality("FULL SCRAPE", c_full, sz_full, bl_full, ts_full, bf_full)
        print_type_table(ts_full)
        validate_documents(args.full)

    if args.classified and os.path.isdir(args.classified):
        print("\n" + SEP + "\n  PART 1b: CLASSIFIED DIRECTORY\n  Source: " + args.classified + "\n" + SEP)
        c_cls, bf_cls, sz_cls, bl_cls, ts_cls, df_cls, dl_cls = scan_html(args.classified)
        print_quality("CLASSIFIED", c_cls, sz_cls, bl_cls, ts_cls, bf_cls)
        print_type_table(ts_cls)

        if args.full and os.path.isdir(args.full):
            full_total = sum(v for k,v in c_full.items() if k!="rerr")
            cls_total = sum(v for k,v in c_cls.items() if k!="rerr")
            gap = full_total - cls_total
            print("\n  " + SEP2 + "\n  GAP: FULL -> CLASSIFIED\n  " + SEP2)
            print("  Full scrape:   {} files".format(full_total))
            print("  Classified:    {} files".format(cls_total))
            print("  LOST:          {} files ({:.1f}%)".format(gap, gap/max(full_total,1)*100))
            print("  Lost = duplicates removed during dedup + <300B pages dropped + _invalid pages")

        if not args.no_drupal: analyze_drupal(ts_cls, df_cls, dl_cls)

        if any(bf_cls.values()):
            print("\n  " + SEP2 + "\n  BAD FILES (classified)\n  " + SEP2)
            for cat in ["empty","tags_only","js_only","cloudflare","access_denied","bad_enc","repl"]:
                if bf_cls[cat]:
                    files = bf_cls[cat][:5]
                    more = " ..." if len(bf_cls[cat])>5 else ""
                    print("  [{}] {} files: {}{}".format(cat, len(bf_cls[cat]), ", ".join(files[:5]), more))

        validate_documents(args.classified)

    validate_json(args.extracted, args.classified)

    print("\n" + "#"*70 + "\n  DONE at " + time.strftime('%Y-%m-%d %H:%M:%S') + "\n" + "#"*70 + "\n")

if __name__ == "__main__": main()
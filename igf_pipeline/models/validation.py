"""Validation model: scan crawl directories, flag bad pages,
statistics per directory and JSON-level integrity checks (report_scrape
logic).  Rendering lives in views/report_view.py."""
import os,re,json
from collections import defaultdict, Counter
from pathlib import Path
from bs4 import BeautifulSoup

from ..views.report_view import SEP, SEP2


def scan_html(class_dir):
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
    print("  Loaded {:,} records ({:.1f} MB)".format(len(data), fsize))
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
    print("  Empty(<50): {} ({:.1f}%)  |  Has Drupal: {} ({:.1f}%)".format(empty_body, empty_body/n*100, has_drupal, has_drupal/n*100))
    print("  Abs paths={}  Dup hashes={}  Null years={}".format(abs_paths, dup_hashes, null_years))
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

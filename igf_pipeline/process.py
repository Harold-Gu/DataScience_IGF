import argparse
import hashlib
import html
import json
import os
import re
import shutil
import sys
import time
import urllib.parse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from . import crawl
from .config import (FOLDER_TYPE_MAP, SEP, SEP2, TYPE_PRIORITY, TYPE_RE_P1,
                     TYPE_RE_P2, WEIGHTED_RULES, _classify_err_lock,
                     _classify_errors)




def _classify_by_filename(fname):
    low=fname.lower()
    for t,patterns in TYPE_RE_P1:
        for p in patterns:
            if p.search(low):return t
    for t,patterns in TYPE_RE_P2:
        for p in patterns:
            if p.search(low):return t
    return None

def _classify_by_folder(rel):
    # Fallback for pages whose filename has no session signal: the crawl
    # folder (workshops-2017, main-sessions-2022, transcripts, ...) states
    # the session type.  Filename always wins; archived/dashboard folders
    # mix types and have no entry in the map.
    for part in rel.replace("\\", "/").lower().split("/"):
        seg = re.sub(r"^\d+_", "", part)
        for key, ctype in FOLDER_TYPE_MAP.items():
            if seg == key or seg.startswith(key + "-") or seg.startswith(key + "_"):
                return ctype
    return None

def _classify_by_content(html):
    # Title-only fallback: session type comes from the page title, never from
    # body keywords, so meeting content cannot leak into participants etc.
    try:
        soup=BeautifulSoup(html,"html.parser")
        crawl._strip_noise(soup)
        title=((soup.title.string or"").strip())if soup.title else""
        search_text=title.lower()
        scores=defaultdict(int)
        for keywords,category,weight in WEIGHTED_RULES:
            for kw in keywords:
                if re.search(kw,search_text,re.I):
                    scores[category]+=weight
                    break
        if scores:return max(scores,key=lambda t:(scores[t],-TYPE_PRIORITY.get(t,99)))
    except:pass
    return None

def _validate_html(html,fname):
    issues=[]
    if len(html)<400:issues.append("too_short")
    try:
        soup=BeautifulSoup(html,"html.parser")
        main=soup.find("main")or soup.find(id="main-content")or soup.find("body")
        if main:
            text=main.get_text(separator=" ",strip=True)
            if len(text)<80:issues.append("no_body_text")
            tag_count=len(main.find_all())
            if tag_count>0 and len(text)/max(tag_count,1)<4:issues.append("low_text_ratio")
        else:issues.append("no_main_body")
        low=html[:2000].lower()
        if"cf-browser-verify"in low or"just a moment"in low:issues.append("cloudflare_block")
        if"access denied"in low and len(html)<2000:issues.append("access_denied")
        title=((soup.title.string or"").strip())if soup.title else""
        list_issue=_detect_list_page(html,title,len(soup.find_all("a",href=True)))
        if list_issue:issues.append(list_issue)
        if main:
            crawl._strip_noise(soup)
            main2=soup.find("main")or soup.find(id="main-content")or soup.find("body")
            stripped=main2.get_text(separator=" ",strip=True)if main2 else""
            if len(stripped)<80:issues.append("no_content")
    except:issues.append("parse_error")
    return issues

def _detect_list_page(html,title,link_count):
    # Pure index pages: sched events without a published schedule, sched
    # venue/track listing pages ("Schedule For ... Events @ ..."), and
    # "Transcripts" link directories.  They have a valid DOM and enough text
    # to pass the structural checks, so they need their own signal.
    low=html.lower()
    if"empty-schedule-message"in low or"no schedule listed"in low:
        return"sched_stub"
    if re.search(r"IGF\s*\d{4}\s*:\s*Schedule\s*For",title or"",re.I):
        return"sched_index"
    if re.search(r"^IGF\s*\d{4}\s*:(?:\s*Directory)?\s*$",title or"",re.I):
        return"sched_directory"
    if re.search(r":\s*Schedule\s*$",title or"",re.I):
        return"sched_schedule_index"
    if(title or"").strip().lower()=="transcripts"and link_count>=10:
        return"transcript_index"
    return None

def _content_hash(html):
    soup=BeautifulSoup(html,"html.parser")
    crawl._strip_noise(soup)
    main=soup.find("main")or soup.find(id="main-content")or soup.find("body")or soup
    text=main.get_text(separator=" ",strip=True)[:10000]
    return hashlib.md5(text.encode()).hexdigest()

def _extract_year(fname):
    # Year priority: igf-YYYY in the filename, then any token-bounded YYYY in
    # the filename, then an exact year folder, then any bounded YYYY in a path
    # segment.  Digits embedded in hash names (eb799444e2012140...) are not
    # token bounded and fall through to the crawl folder year.
    path=fname.replace("\\","/")
    base=path.rsplit("/",1)[-1]
    m=re.search(r"igf[-_\s]*(20\d{2})",base,re.I)
    if not m:
        m=re.search(r"(?:^|[^0-9])(20\d{2})(?=[^0-9]|$)",base)
    if m:
        y=int(m.group(1))
        if 2006<=y<=2025:return y
    parts=path.split("/")
    for part in parts:
        if re.fullmatch(r"20\d{2}",part):
            y=int(part)
            if 2006<=y<=2025:return y
    for part in parts:
        m2=re.search(r"(?:^|[^0-9])(20\d{2})(?=[^0-9]|$)",part)
        if m2:
            y2=int(m2.group(1))
            if 2006<=y2<=2025:return y2
    return None

def _process_html_file(fp,src_root):
    try:
        fname=os.path.basename(fp)
        rel=os.path.relpath(fp,src_root).replace("\\","/")
        with open(fp,"r",encoding="utf-8",errors="ignore")as f:html=f.read()
        if len(html)<300:return None
        try:
            soup=BeautifulSoup(html,"html.parser")
            title=((soup.title.string or"").strip())if soup.title else""
        except Exception:
            title=""
        ntype=_classify_by_filename(fname)
        if ntype:ctype,type_src=ntype,"filename"
        else:
            ftype=_classify_by_folder(rel)
            if ftype:ctype,type_src=ftype,"folder"
            else:
                ttype=_classify_by_content(html)
                if ttype:ctype,type_src=ttype,"title"
                else:ctype,type_src="other","other"
        chash=_content_hash(html)
        year=_extract_year(rel)
        issues=_validate_html(html,fname)
        is_valid=len(issues)==0
        return{"path":fp,"name":fname,"type":ctype,"type_src":type_src,"title":title,
               "hash":chash,"year":year,"size":len(html),"valid":is_valid,"issues":issues}
    except Exception as e:
        with _classify_err_lock:
            if len(_classify_errors)<2000:_classify_errors.append((os.path.basename(fp),type(e).__name__))
        return None

def run_classify(src_dir,out_dir=None,workers=4,dry_run=False):
    src=os.path.abspath(src_dir)
    out=out_dir or f"igf_classified_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out=os.path.abspath(out)
    print(f"\n{'='*55}\n  CLASSIFY & DEDUP\n{'='*55}")
    print(f"  Source: {src}")
    print(f"  Output: {out}")
    html_files=[str(p)for p in Path(src).rglob("*.html")if".venv"not in str(p)]
    print(f"  Scanning {len(html_files)} HTML files...")
    results=[];seen_hashes=set();dups=0;invalid_count=0
    issue_stats=defaultdict(int)
    with ThreadPoolExecutor(max_workers=workers)as ex:
        futures={ex.submit(_process_html_file,fp,src):fp for fp in html_files}
        for i,future in enumerate(as_completed(futures),1):
            r=future.result()
            if r is not None:results.append(r)
            if i%1000==0:print(f"    {i}/{len(html_files)}")
    if _classify_errors:
        print(f"  Parse errors skipped: {len(_classify_errors)} files (e.g. {_classify_errors[0][0]}: {_classify_errors[0][1]})")
        _classify_errors.clear()
    results.sort(key=lambda r:str(r.get("path")or""))
    uniq=[]
    for r in results:
        if r["hash"]in seen_hashes:dups+=1;continue
        seen_hashes.add(r["hash"]);uniq.append(r)
        if not r["valid"]:
            invalid_count+=1
            for iss in r["issues"]:issue_stats[iss]+=1
    results=uniq
    print(f"  Done: {len(results)} unique, {dups} duplicates, {invalid_count} invalid")
    if issue_stats:
        print("  Validation issues:")
        for iss,count in sorted(issue_stats.items(),key=lambda x:-x[1]):
            print(f"    {iss:<20s}: {count:>5d}")
    type_counts=defaultdict(int);type_year=defaultdict(lambda:defaultdict(int))
    type_valid=defaultdict(lambda:defaultdict(int))
    for r in results:
        t=r["type"];y=r["year"]or"unknown"
        type_counts[t]+=1
        status="valid"if r["valid"]else"invalid"
        type_valid[t][status]+=1
        if r["year"]:type_year[t][r["year"]]+=1
    print(f"\n  Classification ({len(results)} unique pages):")
    for t in sorted(type_counts.keys(),key=lambda k:-type_counts[k]):
        years_str=",".join(sorted(str(y)for y in type_year[t].keys()))
        vc=type_valid[t].get("valid",0);ic=type_valid[t].get("invalid",0)
        flag=" !"if ic>0 else""
        print(f"    {t:<22s}: {type_counts[t]:>5d}  (valid={vc}, invalid={ic}){flag}  [{years_str}]")
    name_src=sum(1 for r in results if r.get("type_src")=="filename")
    folder_src=sum(1 for r in results if r.get("type_src")=="folder")
    title_src=sum(1 for r in results if r.get("type_src")=="title")
    other_src=sum(1 for r in results if r.get("type_src")=="other")
    bad=[r for r in results if _classify_by_filename(r["name"])and _classify_by_filename(r["name"])!=r["type"]]
    print(f"\n  Type source: {name_src} by filename, {folder_src} by folder, {title_src} by title, {other_src} other")
    print(f"  Name/type consistency: {len(results)-len(bad)}/{len(results)} consistent, {len(bad)} mismatches")
    if bad:
        for r in bad[:10]:print(f"    MISMATCH {r['name']}: filename={_classify_by_filename(r['name'])} assigned={r['type']}")
    audit_path=os.path.join(out,"classify_type_audit.tsv")
    os.makedirs(out,exist_ok=True)
    with open(audit_path,"w",encoding="utf-8",newline="")as af:
        af.write("name\tyear\ttype\ttype_src\ttitle\n")
        for r in sorted(results,key=lambda x:(x["type"],x["name"])):
            af.write("\t".join([r["name"],str(r["year"]or""),r["type"],r.get("type_src",""),(r.get("title")or"").replace("\t"," ")])+"\n")
    print(f"  Audit -> {audit_path}")
    if dry_run:print("\n  [Dry run - no files copied]");return out
    print(f"\n  Copying to {out} (valid pages only)...")
    total_copied=0
    for r in results:
        if not r["valid"]:continue
        t=r["type"];y=r["year"]or"unknown"
        dest_dir=os.path.join(out,t,str(y));os.makedirs(dest_dir,exist_ok=True)
        dest=os.path.join(dest_dir,r["name"])
        if not os.path.exists(dest):shutil.copy2(r["path"],dest);total_copied+=1
    print(f"  Valid HTML copied: {total_copied}")
    inv_dir=os.path.join(out,"_invalid");os.makedirs(inv_dir,exist_ok=True)
    inv_copied=0
    for r in results:
        if r["valid"]:continue
        sub=os.path.join(inv_dir,r["type"]);os.makedirs(sub,exist_ok=True)
        dest=os.path.join(sub,r["name"])
        if not os.path.exists(dest):shutil.copy2(r["path"],dest);inv_copied+=1
    if inv_copied:print(f"  Invalid HTML (for inspection): {inv_copied} -> _invalid/")
    doc_count=0
    for ext in["*.pdf","*.doc","*.docx","*.xls","*.xlsx","*.ppt","*.pptx","*.zip"]:
        for fp in Path(src).rglob(ext):
            dest_dir=os.path.join(out,"documents");os.makedirs(dest_dir,exist_ok=True)
            dest=os.path.join(dest_dir,os.path.basename(fp))
            if not os.path.exists(dest):shutil.copy2(str(fp),dest);doc_count+=1
    print(f"  Document files copied: {doc_count}")
    print(f"  All done -> {out}")
    return out




def _extract_one_file(fp,src_root):
    try:
        fname=os.path.basename(fp)
        rel=os.path.relpath(fp,src_root).replace("\\","/")
        folder=os.path.dirname(rel).replace("\\","/")
        with open(fp,"r",encoding="utf-8",errors="ignore")as f:html=f.read()
        if len(html)<300:return None
        soup=BeautifulSoup(html,"html.parser")
        if _validate_html(html,fname):return None
        crawl._strip_noise(soup)
        ntype=_classify_by_filename(fname)
        if ntype:page_type,type_src=ntype,"filename"
        else:
            ftype=_classify_by_folder(rel)
            if ftype:page_type,type_src=ftype,"folder"
            else:
                ttype=_classify_by_content(html)
                if ttype:page_type,type_src=ttype,"title"
                else:page_type,type_src="other","other"
        year=_extract_year(rel)
        title=soup.title.string.strip()if soup.title else""
        main=soup.find("main")or soup.find(id="main-content")or soup.find("body")
        body_text=re.sub(r"\s+"," ",main.get_text(separator=" ",strip=True))if main else""
        if len(body_text)<80:return None
        drupal_fields=crawl._extract_drupal_fields_json(soup)
        headings=[]
        if main:
            for tag in main.find_all(["h1","h2","h3"]):
                t=re.sub(r"\s+"," ",tag.get_text(strip=True))
                if t and len(t)>1:headings.append(t)
        headings=list(dict.fromkeys(headings))
        meta={}
        for m in soup.find_all("meta"):
            name=m.get("name")or m.get("property")
            content=m.get("content")
            if name and content:meta[name]=content
        links=[]
        if main:
            for a in main.find_all("a",href=True):
                href=a["href"].strip()
                if href and not href.startswith("#")and not href.startswith("javascript:")and"mailto:"not in href:
                    txt=re.sub(r"\s+"," ",a.get_text(strip=True))[:200]
                    if txt:links.append({"text":txt,"href":crawl._make_url(href)})
        chash=_content_hash(html)
        is_spa=("IGF Schedule"in html[:5000]and"Calendar view"in html[:8000])
        q=[]
        if not title.strip():q.append("no_title")
        if year is None:q.append("no_year")
        quality=",".join(q)or"ok"
        return{"file":fname,"rel_path":rel,"folder":folder,"type":page_type,
               "type_src":type_src,"year":year,"title":title,"drupal_fields":drupal_fields,
               "body_text":body_text,"headings":headings,"meta":meta,
               "links":links,"content_hash":chash,"size_bytes":len(html),
               "quality":quality,"is_spa_shell":is_spa}
    except Exception:
        return None

def run_extract(src_dir,out_dir=None,workers=4):
    src=os.path.abspath(src_dir)
    out=out_dir or f"igf_extracted_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out=os.path.abspath(out)
    os.makedirs(out,exist_ok=True)
    print(f"\n{'='*55}\n  EXTRACT TO JSON\n{'='*55}")
    print(f"  Source: {src}")
    print(f"  Output: {out}")
    html_files=[str(p)for p in Path(src).rglob('*.html')if'.venv'not in str(p)]
    print(f"  Processing {len(html_files)} files...")
    results=[];seen=set();dups=0;noise_skipped=0
    with ThreadPoolExecutor(max_workers=workers)as ex:
        futures={ex.submit(_extract_one_file,fp,src):fp for fp in html_files}
        for i,future in enumerate(as_completed(futures),1):
            r=future.result()
            if r is None:noise_skipped+=1;continue
            results.append(r)
            if i%500==0:print(f"    {i}/{len(html_files)}")
    results.sort(key=lambda r:str(r.get("rel_path")or r.get("file")or""))
    uniq=[]
    for r in results:
        h=r.get('content_hash','')
        if h and h in seen:dups+=1;continue
        if h:seen.add(h)
        uniq.append(r)
    results=uniq
    print(f"  Extracted: {len(results)} unique, {dups} duplicates, {noise_skipped} noise/invalid skipped")
    all_path=os.path.join(out,'all.json')
    with open(all_path,'w',encoding='utf-8')as f:json.dump(results,f,ensure_ascii=False,indent=2)
    print(f"    all.json -> {len(results)} pages")
    print(f"  Done -> {out}")
    return out




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


def run_validation_report(argv=None):
    p = argparse.ArgumentParser(description="IGF Validation Report")
    p.add_argument("--full", default=None)
    p.add_argument("--classified", default=None)
    p.add_argument("--extracted", default=None)
    p.add_argument("--no-drupal", action="store_true")
    args = p.parse_args(argv)
    cwd = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

    def pick(pattern):
        dirs = [d for d in os.listdir(cwd) if d.startswith(pattern) and os.path.isdir(os.path.join(cwd, d))]
        if not dirs:
            return None
        dirs.sort(reverse=True)
        return os.path.join(cwd, dirs[0])

    if not args.full:
        args.full = pick("igf_full_")
    if not args.classified:
        args.classified = pick("igf_classified_")
    if not args.extracted:
        args.extracted = pick("igf_extracted_")

    print("\nIGF SCRAPE VALIDATION REPORT  " + time.strftime("%Y-%m-%d %H:%M:%S"))
    print("  Full: {}  Classified: {}  Extracted: {}".format(
        args.full or "N/A", args.classified or "N/A", args.extracted or "N/A"))

    if args.full and os.path.isdir(args.full):
        print("\n" + SEP + "\n  PART 1a: FULL SCRAPE  |  " + args.full + "\n" + SEP)
        c_full, bf_full, sz_full, bl_full, ts_full, df_full, dl_full = scan_html(args.full)
        print_quality("FULL SCRAPE", c_full, sz_full, bl_full, ts_full, bf_full)
        print_type_table(ts_full)
        validate_documents(args.full)

    if args.classified and os.path.isdir(args.classified):
        print("\n" + SEP + "\n  PART 1b: CLASSIFIED  |  " + args.classified + "\n" + SEP)
        c_cls, bf_cls, sz_cls, bl_cls, ts_cls, df_cls, dl_cls = scan_html(args.classified)
        print_quality("CLASSIFIED", c_cls, sz_cls, bl_cls, ts_cls, bf_cls)
        print_type_table(ts_cls)

        if args.full and os.path.isdir(args.full):
            full_total = sum(v for k, v in c_full.items() if k != "rerr")
            cls_total = sum(v for k, v in c_cls.items() if k != "rerr")
            gap = full_total - cls_total
            print("\n  " + SEP2 + "\n  GAP: FULL -> CLASSIFIED\n  " + SEP2)
            print("  Full scrape:   {} files".format(full_total))
            print("  Classified:    {} files".format(cls_total))
            print("  Lost:          {} files ({:.1f}%)".format(gap, gap / max(full_total, 1) * 100))
            print("  (dedup by content hash + <300B dropped + _invalid pages)")

        if not args.no_drupal:
            analyze_drupal(ts_cls, df_cls, dl_cls)

        if any(bf_cls.values()):
            print("\n  " + SEP2 + "\n  BAD FILES (classified)\n  " + SEP2)
            for cat in ["empty", "tags_only", "js_only", "cloudflare", "access_denied", "bad_enc", "repl"]:
                if bf_cls[cat]:
                    files = bf_cls[cat][:5]
                    more = " ..." if len(bf_cls[cat]) > 5 else ""
                    print("  [{}] {} files: {}{}".format(cat, len(bf_cls[cat]), ", ".join(files), more))

        validate_documents(args.classified)

    validate_json(args.extracted, args.classified)
    print("\nVALIDATION DONE  " + time.strftime("%Y-%m-%d %H:%M:%S"))



MEETING_TYPES = {
    'workshop', 'open-forum', 'lightning-talk', 'day-0-event', 'launch-award',
    'networking', 'main-session', 'town-hall', 'transcript', 'report',
    'schedule', 'participants', 'dc-bpf-nri', 'bpf-nri', 'high-level',
    'newcomers', 'news', 'press', 'calls', 'capacity-building',
    'policy-networks', 'donors', 'mag-eg', 'village', 'youth',
    'accessibility', 'about',
}

# Strong, IGF-specific signals.  One hit in title / filename / meta is enough
# to keep a page; two hits inside the body are enough (a single "igf" inside
# a Flickr navigation shell is not real content).
STRONG_RE = re.compile(
    r'\bigf\b|\bigf\d{1,4}\b|internet governance|intgovforum|wsis|multistakeholder|'
    r'dynamic coalition|best practice forum|\bnri\b|\bmag\b|open forum|'
    r'lightning talk|day 0|pre-?event|town ?hall|main session|'
    r'networking session|plenary|workshop|rapporteur|panelist|'
    r"sharm el sheikh|gobernanza de internet|gouvernance de l' ?internet|"
    r'governan[çc]a (?:da|de) internet|gesti[óoão]n de internet|'
    r'governo da internet', re.I)

# Weak, meeting-document signals (agenda, programme, synthesis paper, ...).
# They are not enough on their own inside a body, but a hit in the filename
# keeps old .txt/.rtf-derived meeting documents whose text the extractor
# could not parse.
WEAK_RE = re.compile(
    r'agenda|programme|program\b|schedule|transcript|proceedings|verbatim|'
    r'speaker|moderator|attendee|registration|venue|consult|synthesis|'
    r'summary|remarks|highlights|questionnaire|orientation|taking stock|'
    r'emerging issues|critical internet resources|regional perspectives|'
    r'opening ceremony|closing ceremony|opening session|closing session|'
    r'roundtable|forum|session|summit|conference|seminar|webinar|meeting|'
    r'report|launch|award|village|exhibition|booth|newsletter|press release|'
    r'call for proposals|panel|dialogue|debate', re.I)

# Third-party brand patterns found in titles of junk pages.  A genuine IGF
# page always ends with "| Internet Governance Forum (IGF)", never with one
# of these brands.  The -of suffix on the URL is irrelevant here.
BRAND_SUFFIX_RE = re.compile(
    r'(?:^\s*|\|\s*|[-–—]\s*)'
    r'(google drive|google docs|google sheets|google forms|zoom|flickr|'
    r'dropbox|youtube|facebook|instagram|linkedin|twitter|pinterest|'
    r'tumblr|slideshare|scribd|prezi|eventbrite|medium|substack|blogspot|'
    r'wordpress|livejournal|internet archive(?: blogs)?)\s*$', re.I)
BRAND_PREFIX_RE = re.compile(
    r'^(google drive|google docs|google sheets|google forms|zoom|flickr|'
    r'youtube|dropbox|facebook|instagram|linkedin|medium|substack|blogspot|'
    r'wordpress|livejournal|internet archive)\b', re.I)
YOUTUBE_CONSENT_RE = re.compile(r'before you continue to', re.I)

# Drupal field keys that only exist on structured IGF meeting pages.
DRUPAL_FIELD_RE = re.compile(
    r'theme|subtheme|speaker|organizer|organiser|rapporteur|moderator|'
    r'panelist|co_?organizer|co_?organiser|proposer|policy_?question|'
    r'session_content|key_session|takeaway|call_to_action|sdg|duration|'
    r'format|room|language|participant|issues|outcomes|description', re.I)

# Supporting evidence only: IGF footers are full of "sign in / subscribe",
# so these markers never remove a page by themselves.
AD_MARKER_RE = re.compile(
    r'\b(?:advert|banner|promo|sponsor|casino|gambling|coupon|discount)\b|'
    r'buy now|free trial|retweet|click here', re.I)


def _norm(value):
    return str(value or '')


def _decoded(value):
    try:
        return urllib.parse.unquote(_norm(value))
    except Exception:
        return _norm(value)


def _strong_hits(text):
    return len(STRONG_RE.findall(text or ''))


def _weak_hits(text):
    return len(WEAK_RE.findall(text or ''))


def _drupal_signal(record):
    fields = record.get('drupal_fields') or {}
    if isinstance(fields, dict):
        return any(DRUPAL_FIELD_RE.search(_norm(key)) for key in fields)
    if isinstance(fields, list):
        return any(DRUPAL_FIELD_RE.search(_norm(key)) for key in fields)
    return False


def _meta_signal(record):
    meta = record.get('meta') or {}
    if not isinstance(meta, dict):
        return 0, False
    lowered = {str(k).lower(): v for k, v in meta.items()}
    text = ' '.join(_norm(lowered.get(k)) for k in ('description', 'og:title', 'title'))
    return _strong_hits(text), bool(text.strip())


def _brand_title(title):
    title = _norm(title).strip()
    if not title:
        return False
    if BRAND_SUFFIX_RE.search(title) or BRAND_PREFIX_RE.search(title):
        return True
    return bool(YOUTUBE_CONSENT_RE.search(title))


# Hosting / interstitial shell pages.  Wayback Machine is deliberately not
# listed: old .txt/.rtf meeting documents keep that title and are valuable.
JUNK_TITLE_RE = re.compile(
    r'^\s*(?:email protection|just a moment|attention required|'
    r'please wait|enable javascript|checking your browser|robot check|'
    r'security check|one more step|site can.?t be reached|'
    r'page (?:not found|could not be found|not available|not exist)|'
    r'404|403 forbidden|error \d{3}|access denied|service unavailable|'
    r'this site requires javascript|suspected phishing)\b', re.I)

# Terms that show a body is a meeting document, not a generic page that
# merely contains "session" or "program".
DOC_SPEC_RE = re.compile(
    r'agenda|programme|verbatim|proceedings|synthesis|questionnaire|'
    r'taking stock|emerging issues|roundtable|call for proposals|'
    r'opening ceremony|closing ceremony|regional perspectives|'
    r'critical internet resources|press release|newsletter', re.I)


def assess(record, min_body):
    title = _norm(record.get('title')).strip()
    rel_path = _decoded(record.get('rel_path'))
    body = _norm(record.get('body_text'))
    body_len = len(body.strip())
    rec_type = record.get('type')
    drupal_sig = _drupal_signal(record)
    meta_strong, _ = _meta_signal(record)
    evidence = []

    if drupal_sig:
        return True, '', []
    if _strong_hits(title):
        return True, '', []
    if _strong_hits(rel_path):
        return True, '', []
    if meta_strong:
        return True, '', []
    if _brand_title(title):
        evidence.append('title matches a third-party brand pattern')
        return False, 'third_party_page', evidence
    if _strong_hits(body) >= 2:
        return True, '', []
    if rec_type in MEETING_TYPES:
        return True, '', []

    if _weak_hits(rel_path) or _weak_hits(title):
        return True, '', []

    if JUNK_TITLE_RE.search(title):
        evidence.append('title matches an interstitial/error shell pattern')
        return False, 'shell_page', evidence
    if _weak_hits(body) >= 3 and body_len >= min_body and DOC_SPEC_RE.search(body):
        return True, '', []

    title_lower = title.lower()
    meta = record.get('meta') or {}
    meta_text = ' '.join(_norm(v) for v in meta.values()).lower() if isinstance(meta, dict) else ''
    if AD_MARKER_RE.search(title_lower) or AD_MARKER_RE.search(meta_text):
        evidence.append('ad markers in title/meta')
        return False, 'ad_page', evidence
    if body_len < min_body:
        evidence.append('no IGF signal and empty/tiny body (%d chars)' % body_len)
        return False, 'empty_no_signal', evidence

    evidence.append('no IGF signal, unrelated content (%d body chars)' % body_len)
    return False, 'no_igf_signal', evidence


def _load_records(path):
    with open(path, encoding='utf-8') as handle:
        data = json.load(handle)
    if isinstance(data, dict):
        data = data.get('pages', data.get('records', []))
    return data if isinstance(data, list) else []


def find_latest_input(root):
    candidates = sorted(
        (p for p in Path(root).glob('igf_extracted_*') if (p / 'all.json').is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] / 'all.json' if candidates else None


def denoise_main(argv=None):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='Remove non-IGF noise from an extracted all.json')
    parser.add_argument('--input', help='path to all.json (default: newest igf_extracted_*/all.json)')
    parser.add_argument('--output', help='output directory (default: igf_denoised_<timestamp>)')
    parser.add_argument('--min-body', type=int, default=80,
                        help='minimum body length for a signal-less page (default 80)')
    parser.add_argument('--dry-run', action='store_true', help='print stats without writing files')
    args = parser.parse_args(argv)

    input_path = Path(args.input) if args.input else find_latest_input(Path.cwd())
    if input_path is None:
        sys.exit('No input given and no igf_extracted_*/all.json found in %s' % Path.cwd())
    if input_path.is_dir():
        input_path = input_path / 'all.json'
    input_path = input_path.resolve()

    print('INPUT :', input_path)
    raw = _load_records(input_path)
    print('Loaded %d records' % len(raw))

    kept, removed, broken, seen = [], [], 0, set()
    reason_counter, type_kept, type_removed = Counter(), Counter(), Counter()
    empty_kept = 0

    for rec in raw:
        if not isinstance(rec, dict):
            broken += 1
            continue
        rel_path = _norm(rec.get('rel_path'))
        if rel_path and rel_path in seen:
            removed.append(dict(rec, _noise_reason='duplicate', _noise_evidence=['same rel_path']))
            reason_counter['duplicate'] += 1
            continue
        if rel_path:
            seen.add(rel_path)
        keep, reason, evidence = assess(rec, args.min_body)
        if keep:
            if not (_norm(rec.get('body_text')).strip()):
                empty_kept += 1
            kept.append(rec)
            type_kept[_norm(rec.get('type')) or '(none)'] += 1
        else:
            removed.append(dict(rec, _noise_reason=reason, _noise_evidence=evidence))
            reason_counter[reason] += 1
            type_removed[_norm(rec.get('type')) or '(none)'] += 1

    print('\nRESULT: kept=%d removed=%d broken=%d' % (len(kept), len(removed), broken))
    print('Removed by reason:')
    for reason, count in reason_counter.most_common():
        print('  %-20s %d' % (reason, count))
    print('Kept by type:')
    for rec_type, count in sorted(type_kept.items(), key=lambda kv: -kv[1]):
        print('  %-20s %d' % (rec_type, count))
    if type_removed:
        print('Removed by type:')
        for rec_type, count in sorted(type_removed.items(), key=lambda kv: -kv[1]):
            print('  %-20s %d' % (rec_type, count))
    print('Kept records with empty body_text (old .txt/.rtf documents): %d' % empty_kept)
    print('\nRemoved samples:')
    shown = 0
    for reason, _ in reason_counter.most_common():
        for rec in removed:
            if rec.get('_noise_reason') == reason:
                print('  [%s] %s | %s' % (reason, rec.get('title') or '(no title)', rec.get('rel_path')))
                shown += 1
                if shown >= 30:
                    break
        if shown >= 30:
            break

    if args.dry_run:
        print('\nDry run - nothing written.')
        return 0

    out_dir = Path(args.output) if args.output else Path.cwd() / ('igf_denoised_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / 'all.json', 'w', encoding='utf-8') as handle:
        json.dump(kept, handle, ensure_ascii=False, indent=1)
    with open(out_dir / 'removed.json', 'w', encoding='utf-8') as handle:
        json.dump(removed, handle, ensure_ascii=False, indent=1)
    report_lines = [
        'input: %s' % input_path,
        'total: %d kept: %d removed: %d broken: %d' % (len(raw), len(kept), len(removed), broken),
        'reasons: %s' % dict(reason_counter),
    ]
    for rec in removed:
        report_lines.append('[%s] %s | %s | %s' % (
            rec.get('_noise_reason'), rec.get('title') or '(no title)', rec.get('rel_path'),
            '; '.join(rec.get('_noise_evidence', []))))
    with open(out_dir / 'denoise_report.txt', 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(report_lines))
    print('\nWrote %s (all.json, removed.json, denoise_report.txt)' % out_dir)
    return 0



SPEAKER_RE = re.compile(r'^\s*(?:>{1,3}\s*)?([A-Z][A-Z0-9 .&\'\-]{2,50}):\s*(.*)$')
SPEAKER_BLACKLIST = {
    'NOTE', 'NOTES', 'MODERATOR', 'CHAIR', 'CHAIRMAN', 'CHAIRPERSON', 'SPEAKER',
    'SPEAKERS', 'AUDIENCE', 'QUESTION', 'QUESTIONS', 'ANSWER', 'ANSWERS',
    'COMMENTS', 'REMARK', 'REMARKS', 'THANK', 'THANKS', 'NEXT', 'OKAY', 'OK',
    'MUSIC', 'APPLAUSE', 'LAUGHTER', 'AGENDA', 'TIME', 'DOCUMENT', 'DOCUMENTS',
    'RESOLUTION', 'CONCLUSION', 'CONCLUSIONS', 'SESSION', 'MORNING', 'AFTERNOON',
    'EVENING', 'DEBATE', 'DISCUSSION', 'INTERVENTION', 'INTERVENTIONS', 'REPORT',
    'REPORTS', 'RECOMMENDATION', 'RECOMMENDATIONS', 'STATEMENT', 'STATEMENTS',
    'TEXT', 'ANNEX', 'ANNEXES', 'SUMMARY', 'INTRODUCTION', 'BACKGROUND',
    'CONTEXT', 'OBJECTIVES', 'OUTCOME', 'OUTCOMES', 'FOLLOW', 'UP', 'END',
    'FINAL', 'POINTS', 'POINT', 'PROPOSAL', 'PROPOSALS', 'OTHERS', 'SIDE',
    'PANEL', 'PANELISTS', 'FACILITATOR', 'FACILITATORS', 'RAPPORTEUR', 'CO',
    'CHAIRS', 'SECRETARIAT', 'LADIES', 'GENTLEMEN', 'GOOD', 'WELCOME',
}


def log(*args):
    print(*args)


def decode_bytes(raw):
    candidates = []
    for encoding in ('utf-8', 'cp1252', 'latin-1'):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        candidates.append((text.count('\ufffd'), encoding, text))
    if not candidates:
        return raw.decode('latin-1', errors='replace')
    candidates.sort(key=lambda item: item[0])
    return candidates[0][2]


def rtf_to_text(raw):
    text = decode_bytes(raw)
    out = []
    skip_depth = None
    depth = 0
    i, n = 0, len(text)
    SKIP_GROUPS = {'fonttbl', 'colortbl', 'stylesheet', 'info', 'pict',
                   'object', 'field', 'header', 'footer', 'footerf',
                   'nonestdpictures', 'nonshppict'}
    while i < n:
        ch = text[i]
        if ch == '\\':
            if i + 1 < n and text[i + 1] == "'":
                try:
                    out.append(chr(int(text[i + 2:i + 4], 16)))
                except ValueError:
                    pass
                i += 4
                continue
            if i + 1 < n and text[i + 1] == '*':
                i += 2
                continue
            match = re.match(r'([a-zA-Z]+)(-?\d+)? ?', text[i + 1:])
            if match:
                word = match.group(1).lower()
                if word in ('par', 'line'):
                    out.append('\n')
                elif word == 'tab':
                    out.append('\t')
                elif word in ('emdash', 'endash'):
                    out.append('-')
                elif word == 'lquote':
                    out.append("'")
                elif word == 'rquote':
                    out.append("'")
                elif word == 'ldblquote':
                    out.append('"')
                elif word == 'rdblquote':
                    out.append('"')
                elif word == 'u':
                    num = match.group(2)
                    if num:
                        try:
                            out.append(chr(int(num)))
                        except ValueError:
                            pass
                consumed = 1 + match.end()
                if word == 'bin' and match.group(2):
                    consumed += int(match.group(2))
                i += consumed
                continue
            if i + 1 < n and text[i + 1] in '\\{}-_~|':
                out.append({'\\': '\\', '{': '{', '}': '}', '-': '-', '_': '-', '~': ' ', '|': ' '}[text[i + 1]])
                i += 2
                continue
            i += 1
            continue
        if ch == '{':
            depth += 1
            if skip_depth is None:
                match = re.match(r'\{\\(\*)?([a-zA-Z]+)', text[i:i + 64])
                if match and (match.group(1) or match.group(2).lower() in SKIP_GROUPS):
                    skip_depth = depth
            i += 1
            continue
        if ch == '}':
            if skip_depth == depth:
                skip_depth = None
            depth = max(0, depth - 1)
            i += 1
            continue
        if skip_depth is None:
            out.append(ch)
        i += 1
    return ''.join(out)


def html_to_text(raw):
    text = decode_bytes(raw)
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', text, flags=re.I | re.S)
    text = re.sub(r'<[^>]+>', ' ', text)
    return html.unescape(text)


def looks_like_html(raw):
    sample = raw[:4000]
    return sample.count(b'<') > sample.count(b'>') * 0.5 and b'<' in sample


def is_php_error_page(raw):
    sample = raw[:4000].lower()
    return b'deprecated' in sample and (b'.php' in sample or b'joomla' in sample)


def parse_file(path):
    raw = path.read_bytes()
    if raw.lstrip()[:5] == b'{\\rtf':
        return rtf_to_text(raw), 'rtf'
    if is_php_error_page(raw):
        return '', 'php_error_page'
    if looks_like_html(raw):
        return html_to_text(raw), 'html'
    return decode_bytes(raw), 'text'


def speaker_turns(text):
    turns = []
    current = None
    for line in text.splitlines():
        match = SPEAKER_RE.match(line)
        if match and match.group(1).strip().upper() not in SPEAKER_BLACKLIST:
            speaker = match.group(1).strip()
            if current is None or current[0] != speaker:
                current = [speaker, match.group(2).strip()]
                turns.append(current)
            else:
                current[1] += ' ' + match.group(2).strip()
        else:
            if current is not None and line.strip():
                current[1] += ' ' + line.strip()
    return [{'speaker': s, 'text': t} for s, t in turns if t]


def normalize(text):
    text = text.replace('\x00', '')
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def locate_file(record, root):
    rel_path = (record.get('rel_path') or '').replace('/', '\\')
    candidates = []
    if rel_path:
        candidates.append(root / rel_path)
    folder = record.get('folder')
    file_name = record.get('file')
    if folder and file_name:
        candidates.append(root / str(folder).replace('/', '\\') / str(file_name))
    for path in candidates:
        if path.is_file():
            return path
    return None


def transcripts_main(argv=None):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='Recover old verbatim transcripts into the JSON corpus')
    parser.add_argument('--input', help='denoised all.json (default: newest igf_denoised_*/all.json)')
    parser.add_argument('--base', help='classified base directory (default: newest igf_classified_*)')
    parser.add_argument('--output', help='output directory (default: igf_recovered_<timestamp>)')
    args = parser.parse_args(argv)

    if args.input:
        input_path = Path(args.input).resolve()
    else:
        candidates = sorted(Path.cwd().glob('igf_denoised_*/all.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            sys.exit('no igf_denoised_*/all.json found; use --input')
        input_path = candidates[0]
    records = json.load(open(input_path, encoding='utf-8'))
    log('INPUT :', input_path, '| records:', len(records))

    if args.base:
        base_dirs = [Path(args.base)]
    else:
        base_dirs = sorted(Path.cwd().glob('igf_classified_*'), key=lambda p: p.stat().st_mtime, reverse=True)
    if not base_dirs:
        sys.exit('no classified base directory found; use --base')

    targets = [r for r in records if not (r.get('body_text') or '').strip()]
    log('Empty-body targets:', len(targets))

    recovered, results = [], []
    status_counts = {}
    for record in targets:
        path = None
        for base in base_dirs:
            path = locate_file(record, base)
            if path:
                break
        if path is None:
            results.append((record, 'missing', 0, 0))
            status_counts['missing'] = status_counts.get('missing', 0) + 1
            continue
        text, kind = parse_file(path)
        text = normalize(text)
        turns = speaker_turns(text) if kind in ('text', 'rtf') else []
        status = kind if text else kind + '_empty'
        status_counts[status] = status_counts.get(status, 0) + 1
        results.append((record, status, len(text), len(turns)))
        if text:
            recovered.append({
                'rel_path': record.get('rel_path'),
                'file': record.get('file'),
                'source': str(path),
                'kind': kind,
                'chars': len(text),
                'speaker_turn_count': len(turns),
                'speaker_turns': turns,
                'text': text,
            })

    log('\nRecovery status:')
    for status, count in sorted(status_counts.items()):
        log('  %-20s %d' % (status, count))
    log('\nRecovered files:')
    for record, status, chars, turns in results:
        if chars:
            log('  [ok %-6d chars, %3d turns] %s' % (chars, turns, record.get('rel_path')))
        else:
            log('  [%s] %s' % (status, record.get('rel_path')))

    out_dir = Path(args.output) if args.output else Path.cwd() / ('igf_recovered_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / 'transcripts_recovered.json', 'w', encoding='utf-8') as handle:
        json.dump(recovered, handle, ensure_ascii=False, indent=1)

    merged = []
    by_rel = {item['rel_path']: item for item in recovered}
    filled = 0
    for record in records:
        item = by_rel.get(record.get('rel_path'))
        if item:
            record = dict(record)
            record['body_text'] = item['text']
            record['recovered_from_disk'] = True
            record['speaker_turns'] = item['speaker_turns']
            record['recovery_kind'] = item['kind']
            filled += 1
        merged.append(record)
    with open(out_dir / 'all_recovered.json', 'w', encoding='utf-8') as handle:
        json.dump(merged, handle, ensure_ascii=False, indent=1)

    speaker_counter = {}
    for item in recovered:
        for turn in item['speaker_turns']:
            speaker_counter[turn['speaker']] = speaker_counter.get(turn['speaker'], 0) + 1
    if speaker_counter:
        log('\nTop speakers in recovered transcripts:')
        for speaker, count in sorted(speaker_counter.items(), key=lambda kv: -kv[1])[:15]:
            log('  %-40s %d turns' % (speaker[:40], count))

    with open(out_dir / 'recovery_report.txt', 'w', encoding='utf-8') as handle:
        for record, status, chars, turns in results:
            handle.write('[%-16s] chars=%-7d turns=%-4d %s\n' % (status, chars, turns, record.get('rel_path')))
    log('\nWrote %s (transcripts_recovered.json, all_recovered.json, recovery_report.txt)' % out_dir)
    log('Records with body_text filled: %d' % filled)
    return 0


def print_quality(label, counts, size_dist, body_len_dist, type_stats, bad_files):
    good = counts["ok"]
    bad = sum(v for k, v in counts.items() if k != "ok")
    total = good + bad
    print(f"\n  {label}: {total} files, {good} OK ({good / max(total, 1) * 100:.1f}%)")
    print("\n  " + SEP2 + "\n  QUALITY FLAGS\n  " + SEP2)
    for lbl, key in [("Valid", "ok"), ("Empty (<300B)", "empty"), ("Tags-only", "tags_only"),
                     ("JS-only", "js_only"), ("Cloudflare", "cloudflare"),
                     ("Access denied", "access_denied"), ("Bad encoding", "bad_enc"),
                     ("Replacement chars", "repl"), ("Read error", "rerr")]:
        print(f"  {lbl:<30s} {counts[key]:>6d}")
    print("\n  " + SEP2 + "\n  SIZE DISTRIBUTION\n  " + SEP2)
    for k in ["<100B", "100-500B", "500B-2KB", "2-10KB", "10-50KB", "50-200KB", ">200KB"]:
        bar = "#" * max(1, size_dist[k] // max(1, total // 50))
        print(f"  {k:<12s} {size_dist[k]:>5d}  {bar}")
    print("\n  " + SEP2 + "\n  BODY TEXT LENGTH\n  " + SEP2)
    for k in ["<100", "100-500", "500-2K", "2-10K", "10-50K", ">50K"]:
        bar = "#" * max(1, body_len_dist[k] // max(1, total // 50))
        print(f"  {k:<12s} {body_len_dist[k]:>5d}  {bar}")


def print_type_table(type_stats):
    print("\n  " + SEP2 + "\n  DIRECTORY BREAKDOWN\n  " + SEP2)
    print("  {:<25s} {:>6s} {:>5s} {:>6s} {:>8s} {:>8s} {:>7s}".format(
        "Directory", "Files", "Bad", "Bad%", "AvgSize", "AvgBody", "Drupal%"))
    for t in sorted(type_stats.keys(), key=lambda k: -type_stats[k]["total"]):
        s = type_stats[t]
        n = s["total"]
        bn = s["bad"]
        bp = bn / max(n, 1) * 100
        vn = max(n - bn, 1)
        asize = s["total_size"] / max(n, 1)
        abody = s["total_body"] / vn
        dp = s["drupal_count"] / vn * 100
        flag = " !!!" if bp > 50 else " !" if bp > 20 else ""
        print("  {:<25s} {:>6d} {:>5d} {:>5.0f}%{} {:>7.1f}KB {:>7.0f}c {:>6.1f}%".format(
            t, n, bn, bp, flag, asize / 1024, abody, dp))


TYPE_DESCRIPTIONS = {
    "workshop": {"family": "Family A (no-suffix)", "note": "body(2010-16) -> session-content(2017+)",
                 "key_fields": ["session-content", "theme", "speakers", "policy-questions", "sdgs",
                                "co-organizers", "discussion-facilitation"]},
    "open-forum": {"family": "Family B (-of suffix)", "note": "ITU/UNESCO/OECD. -of = Open Forum specific",
                   "key_fields": ["description-of", "theme-of", "organizers-of", "speakers-of",
                                  "rapporteur-of", "report"]},
    "day-0-event": {"family": "Mixed (A+C)", "note": "Pre-events. Light Drupal, body is primary",
                    "key_fields": ["description", "description-0", "organizers", "organizers-0"]},
    "launch-award": {"family": "Mixed", "note": "Report launches + awards",
                     "key_fields": ["description", "description-0", "organizers", "speakers", "report"]},
    "networking": {"family": "Family C (-0 suffix)", "note": "Informal. Similar to Lightning Talks",
                   "key_fields": ["description-0", "organizers-0", "theme-0", "format-0", "duration-0"]},
    "main-session": {"family": "Mixed", "note": "Plenary/high-level. Sparse Drupal",
                     "key_fields": ["description", "speakers", "theme", "organizers"]},
    "town-hall": {"family": "Mixed", "note": "Open discussions",
                  "key_fields": ["description", "organizers", "speakers", "format"]},
    "report": {"family": "N/A", "note": "Post-session reports", "key_fields": ["report", "body", "description"]},
    "transcript": {"family": "N/A", "note": "Verbatim transcripts", "key_fields": ["body", "description"]},
    "schedule": {"family": "N/A", "note": "Schedules/agendas", "key_fields": ["body", "description"]},
    "participants": {"family": "N/A", "note": "indico.un.org", "key_fields": ["body"]},
    "dc-bpf-nri": {"family": "Mixed", "note": "DC/BPF/NRI intersessional",
                   "key_fields": ["description", "organizers", "theme", "report"]},
}


def analyze_drupal(type_stats, drupal_fields_by_type, drupal_labels_by_type):
    print("\n" + SEP + "\n  PART 2: DRUPAL FIELD ANALYSIS (classified types only)\n" + SEP)
    for ptype in sorted(drupal_fields_by_type.keys(), key=lambda k: -sum(drupal_fields_by_type[k].values())):
        fields = drupal_fields_by_type[ptype]
        labels = drupal_labels_by_type[ptype]
        if not fields or sum(fields.values()) < 10:
            continue
        ts = type_stats.get(ptype, {})
        n_pages = ts.get("total", 0) - ts.get("bad", 0)
        drupal_pages = ts.get("drupal_count", 0)
        desc = TYPE_DESCRIPTIONS.get(ptype, {"family": "?", "note": "", "key_fields": []})
        print("\n  [{}]  {}  |  {} pages ({:.0f}% Drupal)  |  {} fields, {} unique".format(
            ptype.upper(), desc.get("family", "?"), drupal_pages, drupal_pages / max(n_pages, 1) * 100,
            sum(fields.values()), len(fields)))
        print("    " + desc.get("note", ""))
        for fn, cnt in fields.most_common(10):
            pct = cnt / max(n_pages, 1) * 100
            print("    field_{:<45s} {:>5d} ({:>5.0f}%) {}".format(fn, cnt, pct, "#" * max(1, int(pct / 5))))
        if labels:
            parts = ["[{}]{}".format(str(lb), cn) for lb, cn in labels.most_common(5)]
            print("    Labels: " + " | ".join(parts))

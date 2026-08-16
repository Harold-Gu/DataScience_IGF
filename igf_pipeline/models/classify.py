"""Classification and validation: type rules by filename/content, HTML checks,
content hashing, year extraction, dedup and the output writer."""
import os,re,hashlib,shutil
from datetime import datetime
from collections import defaultdict
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from bs4 import BeautifulSoup

from ..config import TYPE_RE,WEIGHTED_RULES,TYPE_PRIORITY
from ..state import _classify_errors,_classify_err_lock
from . import dom


def _classify_by_filename(fname):
    for t,patterns in TYPE_RE.items():
        for p in patterns:
            if p.search(fname):return t
    return None

def _classify_by_content(html):
    try:
        soup=BeautifulSoup(html,"html.parser")
        dom._strip_noise(soup)
        title=(soup.title.string or"")if soup.title else""
        title_low=title.lower()
        main=soup.find("main")or soup.find(id="main-content")or soup.find("body")
        body_text=main.get_text(separator=" ",strip=True)[:5000].lower()if main else""
        search_text=title_low+" "+body_text
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
    except:issues.append("parse_error")
    return issues

def _content_hash(html):
    soup=BeautifulSoup(html,"html.parser")
    dom._strip_noise(soup)
    main=soup.find("main")or soup.find(id="main-content")or soup.find("body")or soup
    text=main.get_text(separator=" ",strip=True)[:10000]
    return hashlib.md5(text.encode()).hexdigest()

def _extract_year(fname):
    m=re.search(r"(20\d{2})",fname)
    if m:
        y=int(m.group(1))
        if 2006<=y<=2025:return y
    for part in fname.replace("\\","/").split("/"):
        m2=re.search(r"(20\d{2})",part)
        if m2:
            y2=int(m2.group(1))
            if 2006<=y2<=2025:return y2
    return None

def _process_html_file(fp,src_root):
    try:
        fname=os.path.basename(fp)
        with open(fp,"r",encoding="utf-8",errors="ignore")as f:html=f.read()
        if len(html)<300:return None
        ctype=_classify_by_filename(fname)or _classify_by_content(html)or"other"
        chash=_content_hash(html)
        year=_extract_year(fname)or _extract_year(str(fp))
        issues=_validate_html(html,fname)
        is_valid=len(issues)==0
        return{"path":fp,"name":fname,"type":ctype,"hash":chash,"year":year,"size":len(html),"valid":is_valid,"issues":issues}
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
        print(f"  Validation issues:")
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

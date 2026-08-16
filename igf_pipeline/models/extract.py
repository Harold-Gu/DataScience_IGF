"""HTML -> JSON: one record per page built from the DOM (title, meta, headings,
links, Drupal fields, full body text)."""
import os,re,json
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from bs4 import BeautifulSoup

from . import network,dom,classify


def _extract_one_file(fp,src_root):
    try:
        fname=os.path.basename(fp)
        rel=os.path.relpath(fp,src_root).replace("\\","/")
        folder=os.path.dirname(rel).replace("\\","/")
        with open(fp,"r",encoding="utf-8",errors="ignore")as f:html=f.read()
        if len(html)<300:return None
        soup=BeautifulSoup(html,"html.parser")
        if classify._validate_html(html,fname):return None
        dom._strip_noise(soup)
        page_type=classify._classify_by_filename(fname)or classify._classify_by_content(html)or"other"
        year=classify._extract_year(fname)or classify._extract_year(str(fp))
        title=soup.title.string.strip()if soup.title else""
        main=soup.find("main")or soup.find(id="main-content")or soup.find("body")
        body_text=re.sub(r"\s+"," ",main.get_text(separator=" ",strip=True))if main else""
        if len(body_text)<80:return None
        drupal_fields=dom._extract_drupal_fields_json(soup)
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
                    if txt:links.append({"text":txt,"href":network._make_url(href)})
        chash=classify._content_hash(html)
        is_spa=("IGF Schedule"in html[:5000]and"Calendar view"in html[:8000])
        q=[]
        if not title.strip():q.append("no_title")
        if year is None:q.append("no_year")
        quality=",".join(q)or"ok"
        return{"file":fname,"rel_path":rel,"folder":folder,"type":page_type,
               "year":year,"title":title,"drupal_fields":drupal_fields,
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

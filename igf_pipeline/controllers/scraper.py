"""Scrape steps: sessions, reports, transcripts, schedules, archived,
dashboard, participants, plus the failed-URL retry helper."""
import os,re,time,random
from pathlib import Path
from bs4 import BeautifulSoup

from ..config import (IGF_BASE,WORKERS,YEAR_START,YEAR_END,SESSION_TYPES,DETAIL_RE,
    _REPORT_HINTS,ARCHIVED,DASHBOARD,PARTICIPANTS,year_range)
from ..state import _fetch_err
from ..models import network,deepcrawl,dom


def step_sessions(out_root,workers=WORKERS,years=None,limit=None):
    print("\n"+"="*55+"\n  STEP 1: Sessions (2006-2025, 8 types)\n"+"="*55)
    base=os.path.join(out_root,"01_sessions");all_tasks=[];bl=["newsletter","call-for","registration","about","schedule","report","transcript"]
    for stype,templates in SESSION_TYPES.items():
        _fetch_err[0]=None;_fetch_err[1]=0
        y_ok=0;y_skip=0
        for y in year_range(years):
            links_proposals=[];links_content=[];tag=f"{stype}-{y}";any_ok=False
            for ti,tmpl in enumerate(templates):
                list_url=IGF_BASE+tmpl.format(year=y)
                r=network._fetch(list_url)
                if r is None:
                    time.sleep(random.uniform(1.0,2.0))
                    continue
                any_ok=True
                is_proposal=("proposal"in tmpl or"proposals"in tmpl)
                target=links_proposals if is_proposal else links_content
                seen=set();list_pages=[list_url];seen_pages={list_url.lower()};page_htmls={list_url:r.text};pi=0
                while pi<len(list_pages)and len(list_pages)<200:
                    cur=list_pages[pi];pi+=1
                    soup=BeautifulSoup(page_htmls[cur],"html.parser")
                    for a in soup.find_all("a",href=True):
                        href=a.get("href","")
                        if not href or href.startswith("#"):continue
                        full=network._make_url(href)
                        if full in seen:continue
                        seen.add(full)
                        if DETAIL_RE.search(href)and"/content/"in href:
                            if f"igf-{y}-"in href:target.append(full)
                        elif"/content/"in href and f"igf-{y}-"in href:
                            if not any(kw in href.lower()for kw in bl):
                                target.append(full)
                    for nxt in dom._next_page_links(soup,cur):
                        if nxt.lower()in seen_pages:continue
                        seen_pages.add(nxt.lower())
                        r2=network._fetch(nxt)
                        if r2 is None:
                            time.sleep(random.uniform(1.0,2.0));continue
                        page_htmls[nxt]=r2.text;list_pages.append(nxt)
            links_proposals=list(dict.fromkeys(links_proposals))
            links_content=list(dict.fromkeys(links_content))
            total_links=len(links_proposals)+len(links_content)
            if total_links>0:
                print(f"  [{tag}] {len(links_proposals)} proposals + {len(links_content)} content = {total_links} links")
                y_ok+=1
                for label,links_list in[("proposals",links_proposals),("content",links_content)]:
                    if not links_list:continue
                    sub=os.path.join(base,tag,label)
                    for link in links_list:
                        name=link.split("/")[-1].split("?")[0]
                        fpath=os.path.join(sub,f"{network._clean(name)}.html")
                        all_tasks.append((link,fpath,tag))
            else:y_skip+=1
        if y_skip>0:
            err_info=f" [{_fetch_err[0]}]"if _fetch_err[1]and y_ok==0 else""
            print(f"  [{stype}] {y_ok} yrs ok, {y_skip} skipped{err_info}")
    if not all_tasks:print("  No session links found.");return
    if limit:all_tasks=all_tasks[:limit]
    print(f"\n  Downloading {len(all_tasks)} pages...")
    network._download_batch(all_tasks,workers)

def step_reports(out_root,workers=WORKERS,years=None):
    print("\n"+"="*55+"\n  STEP 2: Reports\n"+"="*55)
    _download_yearly_pages(IGF_BASE+"/en/content/igf-{year}-report",os.path.join(out_root,"02_reports"),workers,
        fallback_templates=[IGF_BASE+"/en/igf-{year}-report",IGF_BASE+"/en/content/igf-{year}-final-report",IGF_BASE+"/en/igf-{year}-final-report"],years=years)

def _discover_reports(out_root,workers=WORKERS,years=None):
    base=os.path.join(out_root,"02_reports")
    found_any=False
    for y in year_range(years):
        cands=set()
        for src in[os.path.join(out_root,"05_archived",str(y)),os.path.join(out_root,"06_dashboard",str(y))]:
            if not os.path.isdir(src):continue
            for fp in Path(src).rglob("*.html"):
                try:
                    html=open(fp,"r",encoding="utf-8",errors="ignore").read()
                except:continue
                soup=BeautifulSoup(html,"html.parser")
                main=soup.find("main")or soup.find(id="main-content")or soup
                if main is None:continue
                for a in main.find_all("a",href=True):
                    href=a["href"].strip()
                    if not href or href.startswith(("#","javascript:"))or"mailto:"in href:continue
                    txt=re.sub(r"\s+"," ",a.get_text(strip=True)).lower()
                    href_low=href.lower()
                    if any(kw in txt for kw in _REPORT_HINTS)or any(kw in href_low for kw in["report","outcome","summary","synthesis","proceedings"]):
                        cands.add(network._make_url(href))
        if not cands:continue
        sub=os.path.join(base,str(y));os.makedirs(sub,exist_ok=True)
        tasks=[]
        for u in cands:
            if network._is_file(u):
                tasks.append((u,os.path.join(sub,network._clean(u.split("/")[-1].split("?")[0])),str(y)))
            elif network._same_domain(u,IGF_BASE):
                tasks.append((u,os.path.join(sub,network._clean(u.split("/")[-1].split("?")[0])+".html"),str(y)))
        if tasks:
            print(f"  [{y}] discovered {len(tasks)} report candidates from archived/dashboard pages")
            network._download_batch(tasks,workers);found_any=True
    if not found_any:print("  No extra report candidates discovered from archived/dashboard pages.")

def step_transcripts(out_root,workers=WORKERS,years=None):
    print("\n"+"="*55+"\n  STEP 3: Transcripts (deep crawl)\n"+"="*55)
    base=os.path.join(out_root,"03_transcripts")
    for y in year_range(years):
        sub=os.path.join(base,str(y))
        for url_tmpl in[IGF_BASE+"/en/igf-{year}-transcripts",IGF_BASE+"/en/content/igf-{year}-transcripts"]:
            test_url=url_tmpl.format(year=y)
            r=network._fetch(test_url)
            if r is None:r=network._fetch_wb(test_url,y)
            if r is not None:
                os.makedirs(sub,exist_ok=True)
                network._atomic_write_text(os.path.join(sub,"index.html"),r.text)
                print(f"\n  [{y}] {test_url}")
                deepcrawl._deep_crawl_parallel(test_url,sub,workers)
                break
        else:
            print(f"  [{y}] SKIP (all URL patterns failed)")
        time.sleep(random.uniform(1.0,2.0))

def step_schedules(out_root,workers=WORKERS,years=None):
    print("\n"+"="*55+"\n  STEP 4: Schedules\n"+"="*55)
    base=os.path.join(out_root,"04_schedules")
    for y in year_range(years):
        sub=os.path.join(base,str(y))
        for url_tmpl in[IGF_BASE+"/en/content/igf-{year}-schedule",IGF_BASE+"/en/igf-{year}-schedule"]:
            test_url=url_tmpl.format(year=y)
            r=network._fetch(test_url)
            if r is None:r=network._fetch_wb(test_url,y)
            if r is not None:
                os.makedirs(sub,exist_ok=True)
                network._atomic_write_text(os.path.join(sub,"index.html"),r.text)
                print(f"\n  [{y}] {test_url}")
                deepcrawl._deep_crawl_parallel(test_url,sub,workers)
                break
        else:
            print(f"  [{y}] SKIP (all URL patterns failed)")
        time.sleep(random.uniform(1.0,2.0))

def _download_yearly_pages(url_template,out_base,workers,fallback_templates=None,years=None):
    if fallback_templates is None:
        fallback_templates=[]
    for y in year_range(years):
        seed_url=None;sub=os.path.join(out_base,str(y))
        for tmpl in [url_template]+list(fallback_templates):
            test_url=tmpl.format(year=y)
            r=network._fetch(test_url)
            if r is not None:
                seed_url=test_url;break
        if seed_url is None:
            for tmpl in [url_template]+list(fallback_templates):
                test_url=tmpl.format(year=y)
                r=network._fetch_wb(test_url,y)
                if r is not None:
                    seed_url=test_url;break
        if seed_url is None:
            print(f"  [{y}] SKIP (all URL patterns failed)")
            continue
        os.makedirs(sub,exist_ok=True)
        network._atomic_write_text(os.path.join(sub,"index.html"),r.text)
        page=0;total_items=0;pages_saved=0;page_urls=set()
        while True:
            url=seed_url
            if page>0:sep="&"if"?"in seed_url else"?";url=f"{seed_url}{sep}page={page}"
            if url in page_urls:break
            page_urls.add(url)
            if page==0 and os.path.exists(os.path.join(sub,"index.html")):
                html=open(os.path.join(sub,"index.html"),"r",encoding="utf-8",errors="ignore").read()
            else:
                r=network._fetch(url)
                if r is None:
                    if page==0:print(f"  [{y}] SKIP (fetch failed)")
                    break
                html=r.text
            soup=BeautifulSoup(html,"html.parser")
            is_dashboard_spa=("IGF Schedule" in html[:5000] and "Calendar view" in html[:8000])
            os.makedirs(sub,exist_ok=True)
            pname=f"page_{page}"if page>0 else"index"
            ppath=os.path.join(sub,f"{pname}.html")
            if not os.path.exists(ppath):network._atomic_write_text(ppath,html)
            pages_saved+=1
            main=soup.find("main")or soup.find(id="main-content")or soup;page_tasks=[]
            for a in (main.find_all("a",href=True) if not is_dashboard_spa else []):
                href=a["href"].strip()
                if not href or href.startswith("#"):continue
                full=network._make_url(href,seed_url)
                if network._is_file(full):
                    fp=os.path.join(sub,network._clean(full.split("/")[-1].split("?")[0]))
                    page_tasks.append((full,fp,str(y)))
                elif network._same_domain(full,IGF_BASE)and"/content/"in full:
                    fp=os.path.join(sub,f"{network._clean(full.split('/')[-1].split('?')[0])}.html")
                    page_tasks.append((full,fp,str(y)))
            if page_tasks:network._download_batch(page_tasks,workers);total_items+=len(page_tasks)
            next_link=soup.find("a",title=re.compile(r"next|next page",re.I))
            if not next_link:next_link=soup.find("a",rel="next")
            if not next_link:
                pager=soup.select_one(".pager__item--next a,.pagination .next a,li.next a")
                if pager:next_link=pager
            if next_link and page<200:page+=1
            else:break
            time.sleep(random.uniform(0.3,0.6))
        if pages_saved>0:
            print(f"    [{y}] {total_items} items, {pages_saved} pages")
        time.sleep(random.uniform(0.5,1.0))

def step_archived_dashboard(out_root,years=None,workers=WORKERS):
    print("\n"+"="*55+"\n  STEP 5&6: Archived+Dashboard (deep crawl)\n"+"="*55)
    for y,path in ARCHIVED.items():
        if years and y not in years:continue
        sub=os.path.join(out_root,"05_archived",str(y))
        print(f"\n  [{y}] {IGF_BASE}{path}")
        deepcrawl._deep_crawl_parallel(IGF_BASE+path,sub,workers)
        time.sleep(random.uniform(2.0,4.0))
    for y,path in DASHBOARD.items():
        if years and y not in years:continue
        sub=os.path.join(out_root,"06_dashboard",str(y))
        print(f"\n  [{y}] {IGF_BASE}{path}")
        deepcrawl._deep_crawl_parallel(IGF_BASE+path,sub,workers)

def step_participants(out_root,workers=2):
    print("\n"+"="*55+"\n  STEP 7: Participants (indico.un.org)\n"+"="*55)
    base=os.path.join(out_root,"07_participants")
    tasks=[(url,os.path.join(base,str(y),"participants.html"),str(y))for y,url in PARTICIPANTS.items()]
    network._download_batch(tasks,workers)

def _remove_empty_dirs(root):
    removed=0
    for dirpath,dirnames,filenames in os.walk(root,topdown=False):
        if dirpath==root:continue
        if not dirnames and not filenames:
            try:os.rmdir(dirpath);removed+=1;print(f"  [CLEAN] {os.path.relpath(dirpath,root)}")
            except:pass
    if removed:print(f"  Removed {removed} empty directories.")

def _retry_failed_file(tsv_path,workers=WORKERS):
    tasks=[];skipped=0
    try:
        with open(tsv_path,"r",encoding="utf-8",errors="ignore")as f:
            for line in f:
                line=line.rstrip("\n")
                if not line or"\t"not in line:continue
                url,rest=line.split("\t",1);fpath=rest.rsplit("\t",1)[0].strip()
                if not url.startswith("http"):continue
                if not fpath:skipped+=1;continue
                tasks.append((url,fpath,"retry"))
    except OSError:
        print(f"  Cannot read {tsv_path}");return
    if skipped:print(f"  Skipped {skipped} list-fetch entries (no local target file)")
    if not tasks:print("  No downloadable entries.");return
    print(f"  Re-downloading {len(tasks)} failed URLs...")
    network._download_batch(tasks,workers)

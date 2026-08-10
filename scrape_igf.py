import os,re,sys,time,random,argparse,threading,hashlib,shutil,json
from datetime import datetime
from urllib.parse import urljoin,urlparse
from queue import Queue
from collections import defaultdict
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from bs4 import BeautifulSoup
import cloudscraper

_visited_lock=threading.Lock()
_visited_urls=set()
_stats_lock=threading.Lock()
_stats={"ok":0,"fail":0,"skip":0,"pages":0}

_rate_lock=threading.Lock()
_next_ts=0.0
def _rate_wait(min_gap=0.35):
    global _next_ts
    with _rate_lock:
        now=time.time()
        wait=max(0,_next_ts-now)
        _next_ts=max(now,_next_ts)+min_gap+random.uniform(0,0.15)
    if wait>0:time.sleep(wait)

def _norm_url(url):
    try:
        p=urlparse(url)
        netloc=p.netloc.lower().replace("www.","")
        path=p.path.rstrip("/")or"/"
        return f"{p.scheme}://{netloc}{path}"
    except:return url

def _mark_visited(url):
    nu=_norm_url(url)
    with _visited_lock:
        if nu in _visited_urls:return False
        _visited_urls.add(nu);return True

def _add_stat(key,n=1):
    with _stats_lock:_stats[key]=_stats.get(key,0)+n

def _print_stat():
    with _stats_lock:s=dict(_stats)
    print(f"  [ok={s['ok']} fail={s['fail']} skip={s['skip']} pages={s['pages']}]")

IGF_BASE="https://intgovforum.org"
WORKERS=5
MAX_DEPTH=3
YEAR_START=2006
YEAR_END=2025

_GLOBAL_SCRAPER=None
_GLOBAL_SCRAPER_LOCK=threading.Lock()
def _get_tl_scraper():
    global _GLOBAL_SCRAPER
    if _GLOBAL_SCRAPER is None:
        with _GLOBAL_SCRAPER_LOCK:
            if _GLOBAL_SCRAPER is None:
                print("  [INIT] Creating cloudscraper session (~30s)...",flush=True)
                _GLOBAL_SCRAPER=cloudscraper.create_scraper(
                    browser={"browser":"chrome","platform":"windows","desktop":True})
                print("  [INIT] Session ready.",flush=True)
    return _GLOBAL_SCRAPER

_fetch_err=[None,0]
def _fetch(url,timeout=25,retries=5):
    for attempt in range(retries):
        _rate_wait(0.35)
        try:
            r=_get_tl_scraper().get(url,timeout=timeout)
            if r.status_code==404:
                if _fetch_err[1]==0:_fetch_err[0]="404";_fetch_err[1]=1
                return None
            if r.status_code==429:
                wait=2**(attempt+1)+random.uniform(1,3);time.sleep(wait);continue
            if r.status_code in(502,503,504):
                if _fetch_err[1]==0:_fetch_err[0]=str(r.status_code);_fetch_err[1]=1
                time.sleep(2**(attempt+1));continue
            r.raise_for_status()
            if len(r.text)<300:
                if _fetch_err[1]==0:_fetch_err[0]=f"short({len(r.text)}b)";_fetch_err[1]=1
                return None
            return r
        except Exception as e:
            if _fetch_err[1]==0:_fetch_err[0]=str(type(e).__name__)+":"+str(e)[:80];_fetch_err[1]=1
            if attempt<retries-1:time.sleep(2**(attempt+1)+random.uniform(0.5,2));continue
            return None
    return None

def _clean(s):
    return re.sub(r'[\\/*?:"<>|]',"",str(s)).replace("\n"," ").strip()

def _ext(link):
    low=link.lower()
    for e in[".pdf",".doc",".docx",".xls",".xlsx",".ppt",".pptx",".zip"]:
        if low.endswith(e):return e
    if"filedepot_download"in low:return".bin"
    return".html"

def _is_file(link):return _ext(link)!=".html"

def _same_domain(url,base):
    try:
        a=urlparse(url).netloc.replace("www.","")
        b=urlparse(base).netloc.replace("www.","")
        return a==b
    except:return False

def _is_igf_domain(url):
    try:
        netloc=urlparse(url).netloc.replace("www.","")
        return netloc in{"intgovforum.org","un.org","indico.un.org"}or netloc.endswith((".intgovforum.org",".un.org"))
    except:return False

def _make_url(href,base=IGF_BASE):
    if href.startswith("http"):return href
    if href.startswith("/"):return base+href
    return urljoin(base,href)

def _download_one(scraper,url,fpath,max_retries=3):
    if not _mark_visited(url):return"skip"
    if os.path.exists(fpath)and os.path.getsize(fpath)>0:return"skip"
    os.makedirs(os.path.dirname(fpath),exist_ok=True)
    is_bin=_ext(url)in{".pdf",".doc",".docx",".xls",".xlsx",".ppt",".pptx",".zip",".bin"}
    for attempt in range(max_retries):
        _rate_wait(0.35)
        try:
            r=scraper.get(url,timeout=30)
            if r.status_code==429:time.sleep(2**(attempt+1)+random.uniform(0.5,2));continue
            if r.status_code in(502,503,504):time.sleep(1.5);continue
            r.raise_for_status()
            data=r.content if is_bin else r.text.encode("utf-8")
            if len(data)<(100 if is_bin else 300):
                if attempt<max_retries-1:time.sleep(1.5);continue
                return"fail"
            if is_bin:
                with open(fpath,"wb")as f:f.write(r.content)
            else:
                with open(fpath,"w",encoding="utf-8")as f:f.write(r.text)
            return"ok"
        except Exception:
            if attempt<max_retries-1:time.sleep(1.5);continue
    return"fail"

def _download_batch(tasks,workers):
    total=len(tasks);done=[0];lock=threading.Lock()
    def _worker_dl(url,fpath):
        scraper=_get_tl_scraper()
        result=_download_one(scraper,url,fpath)
        with lock:
            done[0]+=1
            _add_stat(result)
            if done[0]%200==0 or done[0]==total:
                print(f"      {done[0]}/{total}")
        return result
    with ThreadPoolExecutor(max_workers=workers)as ex:
        futures={}
        for url,fpath,_ in tasks:
            futures[ex.submit(_worker_dl,url,fpath)]=(url,fpath)
            if len(futures)>=workers*15:
                done_set=set()
                for f in as_completed(futures):
                    done_set.add(f)
                    if len(done_set)>=workers*5:break
                for f in done_set:del futures[f]
SESSION_TYPES={
    "workshops":["/en/workshop-proposals-{year}","/en/content/igf-{year}-workshops"],
    "open-forums":["/en/open-forum-proposals-{year}","/en/content/igf-{year}-open-forums"],
    "lightning-talks":["/en/lightning-talk-proposals-{year}","/en/content/igf-{year}-lightning-talks"],
    "day-0-events":["/en/pre-events-{year}","/en/content/igf-{year}-day-0-events"],
    "launches-awards":["/en/launches-awards-{year}","/en/content/igf-{year}-launches-awards"],
    "networking":["/en/networking-sessions-{year}","/en/content/igf-{year}-networking-sessions"],
    "main-sessions":["/en/content/igf-{year}-main-sessions"],
    "town-halls":["/en/content/igf-{year}-town-halls"],
}
DETAIL_RE=re.compile(r"igf-\d{4}-(?:ws|workshop|open-forum|lightning-talk|lightning-talk-event|day-0-event|networking-session|networking|launch-award-event|town-hall|main-session|pre-event)-\d+",re.I)

def step_sessions(out_root,workers=WORKERS):
    print("\n"+"="*55+"\n  STEP 1: Sessions (2006-2025, 8 types)\n"+"="*55)
    base=os.path.join(out_root,"01_sessions");all_tasks=[]
    bl=["newsletter","call-for","registration","about","schedule","report","transcript"]
    for stype,templates in SESSION_TYPES.items():
        _fetch_err[0]=None;_fetch_err[1]=0
        y_ok=0;y_skip=0
        for y in range(YEAR_START,YEAR_END+1):
            links=[];tag=f"{stype}-{y}";any_ok=False
            for tmpl in templates:
                r=_fetch(IGF_BASE+tmpl.format(year=y))
                if r is None:
                    time.sleep(random.uniform(1.0,2.0))
                    continue
                any_ok=True
                soup=BeautifulSoup(r.text,"html.parser");seen=set()
                for a in soup.find_all("a",href=True):
                    href=a.get("href","")
                    if not href or href.startswith("#"):continue
                    full=_make_url(href)
                    if full in seen:continue
                    seen.add(full)
                    if DETAIL_RE.search(href)and"/content/"in href:
                        if f"igf-{y}-"in href:links.append(full)
                    elif"/content/"in href and f"igf-{y}-"in href:
                        if not any(kw in href.lower()for kw in bl):
                            links.append(full)
            links=list(dict.fromkeys(links))
            if links:
                print(f"  [{tag}] {len(links)} links")
                y_ok+=1
                sub=os.path.join(base,tag)
                for link in links:
                    name=link.split("/")[-1].split("?")[0]
                    fpath=os.path.join(sub,f"{_clean(name)}.html")
                    all_tasks.append((link,fpath,tag))
            else:y_skip+=1
        if y_skip>0:
            err_info = f" [{_fetch_err[0]}]" if _fetch_err[1] and y_ok==0 else ""
            print(f"  [{stype}] {y_ok} yrs ok, {y_skip} skipped{err_info}")
    if not all_tasks:print("  No session links found.");return
    print(f"\n  Downloading {len(all_tasks)} pages...")
    _download_batch(all_tasks,workers)
def step_reports(out_root,workers=WORKERS):
    print("\n"+"="*55+"\n  STEP 2: Reports\n"+"="*55)
    _download_yearly_pages(IGF_BASE+"/en/content/igf-{year}-report",os.path.join(out_root,"02_reports"),workers,
        fallback_templates=[IGF_BASE+"/en/igf-{year}-report",IGF_BASE+"/en/content/igf-{year}-final-report"])

def step_transcripts(out_root,workers=WORKERS):
    print("\n"+"="*55+"\n  STEP 3: Transcripts\n"+"="*55)
    _download_yearly_pages(IGF_BASE+"/en/igf-{year}-transcripts",os.path.join(out_root,"03_transcripts"),workers,
        fallback_templates=[IGF_BASE+"/en/content/igf-{year}-transcripts"])

def step_schedules(out_root,workers=WORKERS):
    print("\n"+"="*55+"\n  STEP 4: Schedules\n"+"="*55)
    _download_yearly_pages(IGF_BASE+"/en/content/igf-{year}-schedule",os.path.join(out_root,"04_schedules"),workers,
        fallback_templates=[IGF_BASE+"/en/igf-{year}-schedule"])

def _download_yearly_pages(url_template,out_base,workers,fallback_templates=None):
    if fallback_templates is None:
        fallback_templates=[]
    for y in range(YEAR_START,YEAR_END+1):
        seed_url=None;sub=os.path.join(out_base,str(y))
        # Try primary template first, then fallbacks
        for tmpl in [url_template]+list(fallback_templates):
            test_url=tmpl.format(year=y)
            r=_fetch(test_url)
            if r is not None:
                seed_url=test_url
                # Pre-save the seed page HTML for this successful URL
                os.makedirs(sub,exist_ok=True)
                with open(os.path.join(sub,"index.html"),"w",encoding="utf-8")as f:f.write(r.text)
                break
        if seed_url is None:
            print(f"  [{y}] SKIP (all URL patterns failed)")
            continue
        page=0;total_items=0;pages_saved=0
        while True:
            url=seed_url
            if page>0:sep="&"if"?"in seed_url else"?";url=f"{seed_url}{sep}page={page}"
            if page==0 and os.path.exists(os.path.join(sub,"index.html")):
                html=open(os.path.join(sub,"index.html"),"r",encoding="utf-8").read()
            else:
                r=_fetch(url)
                if r is None:
                    if page==0:print(f"  [{y}] SKIP (fetch failed)")
                    break
                html=r.text
            soup=BeautifulSoup(html,"html.parser")
            is_dashboard_spa=("IGF Schedule" in html[:5000] and "Calendar view" in html[:8000])
            os.makedirs(sub,exist_ok=True)
            pname=f"page_{page}"if page>0 else"index"
            ppath=os.path.join(sub,f"{pname}.html")
            if not os.path.exists(ppath):
                with open(ppath,"w",encoding="utf-8")as f:f.write(html)
            pages_saved+=1
            main=soup.find("main")or soup.find(id="main-content")or soup;page_tasks=[]
            for a in (main.find_all("a",href=True) if not is_dashboard_spa else []):
                href=a["href"].strip()
                if not href or href.startswith("#"):continue
                full=_make_url(href,seed_url)
                if _is_file(full):
                    fp=os.path.join(sub,_clean(full.split("/")[-1].split("?")[0]))
                    page_tasks.append((full,fp,str(y)))
                elif _same_domain(full,IGF_BASE)and"/content/"in full:
                    fp=os.path.join(sub,f"{_clean(full.split('/')[-1].split('?')[0])}.html")
                    page_tasks.append((full,fp,str(y)))
            if page_tasks:_download_batch(page_tasks,workers);total_items+=len(page_tasks)
            next_link=soup.find("a",title=re.compile(r"next|next page",re.I))
            if not next_link:next_link=soup.find("a",rel="next")
            if not next_link:
                pager=soup.select_one(".pager__item--next a,.pagination .next a,li.next a")
                if pager:next_link=pager
            if next_link:page+=1
            else:break
            time.sleep(random.uniform(0.3,0.6))
        if pages_saved>0:
            print(f"    [{y}] {total_items} items, {pages_saved} pages")
        time.sleep(random.uniform(0.5,1.0))

ARCHIVED={
    2006:"/en/archived/first-igf-meeting-athens-greece",
    2007:"/en/archived/second-igf-meeting-rio-de-janeiro-brazil",
    2008:"/en/archived/the-igf-2008-meeting",
    2009:"/en/archived/the-igf-2009-meeting",
    2010:"/en/archived/the-igf-2010-meeting",
    2011:"/en/archived/igf-2011",
    2012:"/en/archived/igf-2012",
    2013:"/en/archived/igf-2013",
    2014:"/en/archived/igf-2014",
    2015:"/en/archived/igf-2015",
    2016:"/en/archived/igf-2016-enabling-inclusive-and-sustainable-growth",
    2017:"/en/archived/igf-2017",
    2018:"/en/archived/igf-2018",
    2019:"/en/archived/igf-2019",
    2020:"/en/archived/igf-2020",
    2021:"/en/archived/igf-2021",
}
DASHBOARD={
    2022:"/en/dashboard/igf-2022",
    2023:"/en/dashboard/igf-2023",
    2024:"/en/dashboard/igf-2024",
    2025:"/en/dashboard/igf-2025",
}

def step_archived_dashboard(out_root,years=None,workers=WORKERS):
    print("\n"+"="*55+"\n  STEP 5&6: Archived+Dashboard (deep crawl)\n"+"="*55)
    for y,path in ARCHIVED.items():
        if years and y not in years:continue
        sub=os.path.join(out_root,"05_archived",str(y))
        print(f"\n  [{y}] {IGF_BASE}{path}")
        _deep_crawl_parallel(IGF_BASE+path,sub,workers)
        time.sleep(random.uniform(2.0,4.0))
    for y,path in DASHBOARD.items():
        if years and y not in years:continue
        sub=os.path.join(out_root,"06_dashboard",str(y))
        print(f"\n  [{y}] {IGF_BASE}{path}")
        _deep_crawl_parallel(IGF_BASE+path,sub,workers)

def _deep_crawl_parallel(seed_url,out_dir,workers=WORKERS):
    os.makedirs(out_dir,exist_ok=True)
    seed_year=re.search(r"(20\d{2})",seed_url)
    seed_year=seed_year.group(1)if seed_year else""
    files_dir=os.path.join(out_dir,"files");os.makedirs(files_dir,exist_ok=True)
    task_queue=Queue();task_queue.put((seed_url,0,out_dir))
    local_stats={"pages":0,"files":0,"errors":0};stats_lock=threading.Lock();running=[True]
    def _worker():
        scraper=_get_tl_scraper()
        while running[0]:
            try:item=task_queue.get(timeout=3)
            except:continue
            url,depth,current_dir=item
            if not _mark_visited(url):task_queue.task_done();continue
            r=_fetch(url)
            if r is None:
                with stats_lock:local_stats["errors"]+=1
                if depth==0:
                    err_path=os.path.join(current_dir,"_FETCH_FAILED.txt")
                    try:
                        with open(err_path,"w")as ef:ef.write(f"Failed to fetch seed URL: {url}\nCheck if URL exists.\n")
                    except:pass
                task_queue.task_done();continue
            html=r.text
            is_dashboard_spa=("IGF Schedule" in html[:5000] and "Calendar view" in html[:8000])
            name=url.split("/")[-1].split("?")[0]or f"page_{abs(hash(url))}"
            page_path=os.path.join(current_dir,f"{_clean(name)}.html")
            if not os.path.exists(page_path):
                try:
                    with open(page_path,"w",encoding="utf-8")as f:f.write(html)
                except:pass
            with stats_lock:local_stats["pages"]+=1
            soup=BeautifulSoup(html,"html.parser")
            main=soup.find("main")or soup.find(id="main-content")or soup
            for a in (main.find_all("a",href=True) if not (is_dashboard_spa and depth>0) else []):
                href=a["href"].strip()
                if not href or href.startswith("#")or href.startswith("javascript:"):continue
                if"mailto:"in href:continue
                full=_make_url(href,url)
                if _is_file(full):
                    fname=_clean(full.split("/")[-1].split("?")[0])
                    fpath=os.path.join(files_dir,fname)
                    if os.path.exists(fpath) and os.path.getsize(fpath)>0:
                        with stats_lock:local_stats["files"]+=1
                    elif _download_one(scraper,full,fpath)=="ok":
                        with stats_lock:local_stats["files"]+=1
                elif _is_igf_domain(full):
                    if depth<MAX_DEPTH:
                        task_queue.put((full,depth+1,current_dir))
                else:
                    ext_name=_clean(full.split("/")[-1].split("?")[0])or f"ext_{abs(hash(full))}"
                    ext_path=os.path.join(current_dir,f"{ext_name}.html")
                    _download_one(scraper,full,ext_path)
                    if depth==0:
                        url_low=full.lower()
                        if seed_year and seed_year in url_low:
                            task_queue.put((full,depth+1,current_dir))
                        elif any(kw in url_low for kw in["igf","intgov","internet-governance"]):
                            task_queue.put((full,depth+1,current_dir))
                            task_queue.put((full,depth+1,current_dir))
            task_queue.task_done()
            time.sleep(random.uniform(0.2,0.5))
    threads=[threading.Thread(target=_worker,daemon=True)for _ in range(workers)]
    for t in threads:t.start()
    last_qsize=-1;stable_count=0;last_p=0;last_f=0
    while True:
        time.sleep(1.5);qsize=task_queue.qsize()
        with stats_lock:s=dict(local_stats)
        if s["pages"]!=last_p or s["files"]!=last_f:
            print(f"    [q={qsize}] {s['pages']}p {s['files']}f")
            last_p=s["pages"];last_f=s["files"]
        if qsize==last_qsize:
            stable_count+=1
            if stable_count>=20 and qsize==0:break
        else:stable_count=0
        last_qsize=qsize
    running[0]=False
    for t in threads:t.join(timeout=10)
    with stats_lock:s=dict(local_stats)
    print(f"    DONE: {s['pages']} pages, {s['files']} files, {s['errors']} errors")
    _add_stat("pages",s["pages"])

PARTICIPANTS={
    2021:"https://indico.un.org/event/36215/registrations/participants",
    2022:"https://indico.un.org/event/1002089/registrations/participants",
    2023:"https://indico.un.org/event/1006568/registrations/participants",
    2025:"https://indico.un.org/event/1016806/registrations/participants",
}

def step_participants(out_root,workers=2):
    print("\n"+"="*55+"\n  STEP 7: Participants (indico.un.org)\n"+"="*55)
    base=os.path.join(out_root,"07_participants")
    tasks=[(url,os.path.join(base,str(y),"participants.html"),str(y))for y,url in PARTICIPANTS.items()]
    _download_batch(tasks,workers)

def _remove_empty_dirs(root):
    removed=0
    for dirpath,dirnames,filenames in os.walk(root,topdown=False):
        if dirpath==root:continue
        if not dirnames and not filenames:
            try:os.rmdir(dirpath);removed+=1;print(f"  [CLEAN] {os.path.relpath(dirpath,root)}")
            except:pass
    if removed:print(f"  Removed {removed} empty directories.")
TYPE_PATTERNS={
    "workshop":[r"igf-\d{4}-ws-\d+",r"igf-\d{4}-workshop-"],
    "open-forum":[r"igf-\d{4}-open-forum-",r"igf-\d{4}-of-\d+"],
    "lightning-talk":[r"igf-\d{4}-lightning-talk"],
    "day-0-event":[r"igf-\d{4}-day-0-event",r"igf-\d{4}-pre-event"],
    "launch-award":[r"igf-\d{4}-launch-award",r"igf-\d{4}-launches-awards"],
    "networking":[r"igf-\d{4}-networking"],
    "main-session":[r"igf-\d{4}-main-session",r"high-level-track",r"opening-ceremony",r"closing-ceremony",r"parliamentary-track",r"open-mic"],
    "town-hall":[r"igf-\d{4}-town-hall"],
    "report":[r"igf-\d{4}-report",r"session-report",r"report",r"final-report"],
    "transcript":[r"transcript",r"igf-\d{4}-transcript"],
    "schedule":[r"igf-\d{4}-schedule",r"schedule"],
}
TYPE_RE={k:[re.compile(p,re.I)for p in v]for k,v in TYPE_PATTERNS.items()}

WEIGHTED_RULES=[
    (["workshop","ws #","breakout","ws-"],"workshop",5),
    (["open forum","of #","open-forum"],"open-forum",5),
    (["lightning talk","lightning-talk","lightning talk event"],"lightning-talk",5),
    (["day 0","day-0","pre-event","pre event","day 0 event"],"day-0-event",5),
    (["launch","award","laureate","launches & awards"],"launch-award",4),
    (["networking","networking session"],"networking",5),
    (["main session","plenary","high-level session","high level session",
      "opening session","closing session","opening ceremony","closing ceremony","open mic"],"main-session",5),
    (["town hall","townhall"],"town-hall",5),
    (["transcript","verbatim","proceedings","record of","meeting record"],"transcript",4),
    (["executive summary","session report","outcome document","final report",
      "meeting report","summary report","annual report","chair summary","rapporteur"],"report",3),
    (["schedule","agenda","programme","program overview","timetable","calendar"],"schedule",3),
    (["participant","registration list","attendee"],"participants",4),
    (["dynamic coalition","dc session","bpf","best practice","nri",
      "national regional","intersessional"],"dc-bpf-nri",4),
]

TYPE_PRIORITY={"workshop":0,"open-forum":1,"lightning-talk":2,"day-0-event":3,
    "networking":4,"main-session":5,"town-hall":6,"launch-award":7,
    "transcript":8,"report":9,"schedule":10,"participants":11,"dc-bpf-nri":12}

def _classify_by_filename(fname):
    for t,patterns in TYPE_RE.items():
        for p in patterns:
            if p.search(fname):return t
    return None

def _classify_by_content(html):
    try:
        soup=BeautifulSoup(html,"html.parser")
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
    except:return None

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
            if r is None:continue
            if r["hash"]in seen_hashes:dups+=1;continue
            seen_hashes.add(r["hash"]);results.append(r)
            if not r["valid"]:
                invalid_count+=1
                for iss in r["issues"]:issue_stats[iss]+=1
            if i%1000==0:print(f"    {i}/{len(html_files)} (dups={dups}, invalid={invalid_count})")
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
def _extract_drupal_fields_json(soup):
    fields={}
    for elem in soup.select("[class*='field--name-field-']"):
        field_name=None
        for cls in elem.get('class',[]):
            m=re.match(r'field--name-field-(.+)',cls)
            if m:field_name=m.group(1).replace('-','_').strip('_').lower();break
        if not field_name:continue
        label_elem=elem.select_one('.field__label')
        label=label_elem.get_text(strip=True)if label_elem else''
        label=re.sub(r'\s*\(.*?\)','',label).strip()
        items=elem.select('.field__item')
        if not items:continue
        contents=[]
        for item in items:
            links=[{'text':a.get_text(strip=True),'href':a['href']}for a in item.find_all('a',href=True)]
            text=item.get_text(separator='\n',strip=True)
            text=re.sub(r'\n{3,}','\n\n',text)
            contents.append({'text':text,'links':links})
        if field_name in fields:fields[field_name]['content'].extend(contents)
        else:fields[field_name]={'label':label,'content':contents}
    return fields





def _extract_one_file(fp,src_root):
    try:
        fname=os.path.basename(fp)
        rel=os.path.relpath(fp,src_root).replace("\\","/")
        folder=os.path.dirname(rel).replace("\\","/")
        with open(fp,"r",encoding="utf-8",errors="ignore")as f:html=f.read()
        if len(html)<300:return None
        soup=BeautifulSoup(html,"html.parser")
        page_type=_classify_by_filename(fname)or _classify_by_content(html)or"other"
        year=_extract_year(fname)or _extract_year(str(fp))
        title=soup.title.string.strip()if soup.title else""
        main=soup.find("main")or soup.find(id="main-content")or soup.find("body")
        body_text=main.get_text(separator=" ",strip=True)if main else""
        drupal_fields=_extract_drupal_fields_json(soup)
        headings=[]
        if main:
            for tag in main.find_all(["h1","h2","h3"]):
                t=tag.get_text(strip=True)
                if t and len(t)>1:headings.append(t)
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
                    links.append({"text":a.get_text(strip=True)[:200],"href":_make_url(href)})
        chash=_content_hash(html)
        return{"file":fname,"rel_path":rel,"folder":folder,"type":page_type,
               "year":year,"title":title,"drupal_fields":drupal_fields,
               "body_text":body_text,"headings":headings,"meta":meta,
               "links":links,"content_hash":chash,"size_bytes":len(html)}
    except Exception as e:
        return{"file":os.path.basename(fp),"error":str(e)}

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
    results=[];seen=set();dups=0
    with ThreadPoolExecutor(max_workers=workers)as ex:
        futures={ex.submit(_extract_one_file,fp,src):fp for fp in html_files}
        for i,future in enumerate(as_completed(futures),1):
            r=future.result()
            if r is None:continue
            h=r.get('content_hash','')
            if h and h in seen:dups+=1;continue
            if h:seen.add(h)
            results.append(r)
            if i%500==0:print(f"    {i}/{len(html_files)} ({len(results)} unique)")
    print(f"  Extracted: {len(results)} unique, {dups} duplicates")
    all_path=os.path.join(out,'all.json')
    with open(all_path,'w',encoding='utf-8')as f:json.dump(results,f,ensure_ascii=False,indent=2)
    print(f"    all.json -> {len(results)} pages")
    print(f"  Done -> {out}")
    return out

STEPS=["sessions","reports","transcripts","schedules","archived","dashboard","participants"]

def main():
    p=argparse.ArgumentParser(description="IGF Complete Scraper + Classifier + Extractor")
    p.add_argument("--step",help="Comma-sep: "+",".join(STEPS))
    p.add_argument("--year",type=int)
    p.add_argument("--workers",type=int,default=WORKERS)
    p.add_argument("--output",default=None)
    p.add_argument("--dry-run",action="store_true")
    p.add_argument("--no-clean",action="store_true")
    p.add_argument("--no-classify",action="store_true")
    p.add_argument("--no-extract",action="store_true")
    p.add_argument("--classify-only",action="store_true")
    p.add_argument("--classify-dir",default=None)
    p.add_argument("--classify-out",default=None)
    p.add_argument("--extract-out",default=None)
    args=p.parse_args()
    if args.classify_only:
        src=args.classify_dir or next((d for d in sorted(os.listdir("."),reverse=True)if d.startswith("igf_full_")and os.path.isdir(d)),None)
        if not src:print("No igf_full_* found!");return
        run_classify(src,args.classify_out,args.workers);return
    do=set(args.step.split(","))if args.step else set(STEPS)
    out=args.output or f"igf_full_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    print(f"\n{'#'*55}\n  IGF COMPLETE SCRAPER + CLASSIFIER + EXTRACTOR\n  Steps: {', '.join(sorted(do))}\n  Workers: {args.workers}\n  Year range: {YEAR_START}-{YEAR_END}\n  Output: {os.path.abspath(out)}\n{'#'*55}")
    if args.dry_run:print("\n[Dry run - no downloads]");return
    os.makedirs(out,exist_ok=True);t0=time.time()
    STEPS_MAP={"sessions":step_sessions,"reports":step_reports,"transcripts":step_transcripts,"schedules":step_schedules}
    for s,f in STEPS_MAP.items():
        if s in do:f(out,args.workers)
    if"archived"in do or"dashboard"in do:step_archived_dashboard(out,{args.year}if args.year else None,args.workers)
    if"participants"in do:step_participants(out,args.workers)
    if not args.no_clean:print(f"\n{'='*55}\n  CLEANUP");_remove_empty_dirs(out)
    elapsed=(time.time()-t0)/60
    print(f"\n{'#'*55}\n  SCRAPE DONE  ({elapsed:.0f}m)");_print_stat()
    print(f"  Output: {os.path.abspath(out)}\n{'#'*55}")
    if not args.no_classify:run_extract(run_classify(out,args.classify_out,args.workers),args.extract_out,args.workers)if not args.no_extract else run_classify(out,args.classify_out,args.workers)

if __name__=="__main__":main()
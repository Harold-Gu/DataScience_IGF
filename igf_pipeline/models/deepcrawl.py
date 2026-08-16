"""Breadth-first deep crawler (Model): worker-pool queue that expands list
pages up to MAX_DEPTH, downloads linked documents and re-queues dropped URLs.
Network hooks (_fetch / _get_tl_scraper) are called through the network module
so tests can monkeypatch them."""
import os,re,time,hashlib,threading
from queue import Queue
from bs4 import BeautifulSoup

from ..config import MAX_DEPTH,MAX_QUEUE,WORKERS
from .network import (_mark_visited,_unmark_visited,_clean,_is_igf_domain,_make_url,
    _is_file,_file_ok,_download_one,_record_failed,_atomic_write_text,
    _atomic_write_bytes,_add_stat,_norm_url)
from . import network,dom


def _deep_crawl_parallel(seed_url,out_dir,workers=WORKERS):
    os.makedirs(out_dir,exist_ok=True)
    seed_year=re.search(r"(20\d{2})",seed_url)
    if not seed_year:seed_year=re.search(r"(20\d{2})",str(out_dir))
    seed_year=seed_year.group(1)if seed_year else""
    files_dir=os.path.join(out_dir,"files");os.makedirs(files_dir,exist_ok=True)
    dropped_path=os.path.join(out_dir,"_dropped_urls.txt")
    task_queue=Queue();task_queue.put((seed_url,0,out_dir))
    local_stats={"pages":0,"files":0,"errors":0};stats_lock=threading.Lock()
    drop_lock=threading.Lock();running=[True]
    def _log_dropped(url):
        with drop_lock:
            try:
                with open(dropped_path,"a",encoding="utf-8")as f:f.write(url+"\n")
            except:pass
    def _worker():
        scraper=network._get_tl_scraper()
        while True:
            try:item=task_queue.get(timeout=2)
            except:item=None
            if item is None:
                if not running[0]:break
                continue
            url,depth,current_dir=item
            try:
                if not _mark_visited(url,current_dir):
                    if depth==0 and _norm_url(url)==_norm_url(seed_url):
                        print(f"    [WARN] seed already visited, skipped: {url}")
                    continue
                name=url.split("/")[-1].split("?")[0]or f"page_{hashlib.sha1(url.encode()).hexdigest()[:8]}"
                page_path=os.path.join(current_dir,f"{_clean(name)}.html")
                r=network._fetch(url,wb_year=seed_year)
                if r is None:
                    _unmark_visited(url,current_dir)
                    with stats_lock:local_stats["errors"]+=1
                    _record_failed(url,page_path,"fetch")
                    if depth==0:
                        err_path=os.path.join(current_dir,"_FETCH_FAILED.txt")
                        try:
                            with open(err_path,"w",encoding="utf-8")as ef:ef.write(f"Failed to fetch seed URL: {url}\nCheck if URL exists.\n")
                        except:pass
                    continue
                html=r.text
                is_dashboard_spa=("IGF Schedule" in html[:5000] and "Calendar view" in html[:8000])
                spa_marker=os.path.join(current_dir,"_SPA_SHELL.txt")
                if is_dashboard_spa and not os.path.exists(spa_marker):
                    try:
                        with open(spa_marker,"w",encoding="utf-8")as sf:
                            sf.write(f"SPA calendar shell: {url}\nContent is rendered by JavaScript; static crawl cannot expand embedded links.\n")
                    except:pass
                if not os.path.exists(page_path):
                    if _is_igf_domain(url):_atomic_write_text(page_path,html)
                    else:_atomic_write_bytes(page_path,r.content)
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
                        if _file_ok(full,fpath):
                            with stats_lock:local_stats["files"]+=1
                        elif _download_one(scraper,full,fpath)=="ok":
                            with stats_lock:local_stats["files"]+=1
                    elif _is_igf_domain(full):
                        if depth<MAX_DEPTH:
                            if task_queue.qsize()<MAX_QUEUE:task_queue.put((full,depth+1,current_dir))
                            else:_log_dropped(full)
                    elif depth<MAX_DEPTH:
                        url_low=full.lower()
                        relevant=(seed_year and seed_year in url_low)or any(kw in url_low for kw in["igf","intgov","internet-governance","wsis"])
                        if depth==0 or relevant:
                            if task_queue.qsize()<MAX_QUEUE:task_queue.put((full,depth+1,current_dir))
                            else:_log_dropped(full)
                for nxt in dom._next_page_links(soup,url):
                    if task_queue.qsize()<MAX_QUEUE:task_queue.put((nxt,depth,current_dir))
                    else:_log_dropped(nxt)
            except Exception:
                _unmark_visited(url,current_dir)
                with stats_lock:local_stats["errors"]+=1
            finally:
                task_queue.task_done()
    def _crawl_pass(q):
        running[0]=True
        threads=[threading.Thread(target=_worker,daemon=True)for _ in range(workers)]
        for t in threads:t.start()
        last_q=-1;idle_count=0;last_beat=time.time();last_sig=(0,0,0)
        while True:
            time.sleep(2)
            with q.mutex:unfinished=q.unfinished_tasks
            qsize=q.qsize()
            with stats_lock:s=dict(local_stats)
            now=time.time()
            sig=(s["pages"],s["files"],s["errors"])
            if qsize!=last_q and(qsize%50==0 or qsize==0):
                print(f"    [q={qsize}] {s['pages']}p {s['files']}f",flush=True)
                last_q=qsize;last_beat=now;last_sig=sig
            elif now-last_beat>=60:
                if sig!=last_sig:
                    print(f"    [busy] {s['pages']}p {s['files']}f {s['errors']}e",flush=True)
                else:
                    print(f"    [busy] no progress: {s['pages']}p {s['files']}f {s['errors']}e (inflight={unfinished})",flush=True)
                last_beat=now;last_sig=sig
            if unfinished==0:
                idle_count+=1
                if idle_count>=3:break
            else:idle_count=0
        running[0]=False
        for t in threads:t.join(timeout=30)
    dropped_seen=set()
    for _pass in range(3):
        _crawl_pass(task_queue)
        try:
            with open(dropped_path,"r",encoding="utf-8",errors="ignore")as f:
                drops=[u.strip()for u in f if u.strip()]
        except:drops=[]
        new=[]
        for u in drops:
            if u in dropped_seen:continue
            dropped_seen.add(u);new.append(u)
        if not new:break
        print(f"    [pass {_pass+2}] re-queuing {len(new)} dropped links")
        for u in new:task_queue.put((u,1,out_dir))
    with stats_lock:s=dict(local_stats)
    print(f"    DONE: {s['pages']} pages, {s['files']} files, {s['errors']} errors")
    _add_stat("pages",s["pages"])
    _add_stat("errors",s["errors"])
    if os.path.exists(dropped_path)and os.path.getsize(dropped_path)>0:
        print(f"    Note: queue-capped links logged -> {os.path.relpath(dropped_path,os.getcwd())}")

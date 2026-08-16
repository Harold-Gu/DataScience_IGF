"""Network engine (Model): cloudscraper session, rate limiting with
backoff/recovery, Wayback Machine fallback, atomic writes, binary magic
validation, visited/inflight tracking and the multi-threaded downloader."""
import os,re,time,random,threading,hashlib
from urllib.parse import urljoin,urlparse
from concurrent.futures import ThreadPoolExecutor,as_completed
import cloudscraper

from ..config import _RATE_MIN,_RATE_MAX,IGF_BASE,_BIN_MAGIC
from ..state import (_visited_lock,_visited_urls,_inflight_urls,_stats_lock,_stats,
    _rate_lock,_rate_state,_failed_lock,_failed_seen,_failed_log_path,_MANIFEST,
    _GLOBAL_SCRAPER,_GLOBAL_SCRAPER_LOCK,_fetch_err,_wb_lock,_wb_state,
    _FILE_MAP,_FILE_MAP_LOCK)


def _rate_wait():
    with _rate_lock:
        now=time.time()
        target=max(_rate_state["next_ts"],_rate_state.get("cooldown_until",0.0))
        wait=max(0.0,target-now)
        _rate_state["next_ts"]=max(now,target)+_rate_state["gap"]+random.uniform(0,0.1)
    if wait>0:time.sleep(wait)

def _rate_backoff(strong=False,cooldown=0.0):
    with _rate_lock:
        g=_rate_state["gap"]*2.0
        if strong:g=max(g,1.5)
        _rate_state["gap"]=min(_RATE_MAX,g)
        _rate_state["streak"]=0
        if cooldown:
            _rate_state["cooldown_until"]=max(_rate_state.get("cooldown_until",0.0),time.time()+cooldown)

def _rate_recover():
    with _rate_lock:
        _rate_state["streak"]=min(_rate_state.get("streak",0)+1,100)
        if _rate_state["streak"]>=8:
            _rate_state["gap"]=max(_RATE_MIN,_rate_state["gap"]*0.9)

def _norm_url(url):
    try:
        p=urlparse(url)
        netloc=p.netloc.lower().replace("www.","")
        path=p.path.rstrip("/")or"/"
        return f"{p.scheme}://{netloc}{path}"
    except:return url

def _scope_key(fpath):
    try:
        d=os.path.dirname(os.path.abspath(fpath))or os.path.abspath(".")
        return os.path.normcase(d).replace("\\","/")
    except:return str(fpath)

def _scope_dir(dpath):
    try:
        return os.path.normcase(os.path.abspath(str(dpath))).replace("\\","/")
    except:return str(dpath)

def _mark_visited(url,scope=None):
    key=(_norm_url(url),_scope_dir(scope)if scope else"")
    with _visited_lock:
        if key in _visited_urls:return False
        _visited_urls.add(key);return True

def _unmark_visited(url,scope=None):
    key=(_norm_url(url),_scope_dir(scope)if scope else"")
    with _visited_lock:_visited_urls.discard(key)

def _try_inflight(url,scope=None):
    key=(_norm_url(url),_scope_key(scope)if scope else"")
    with _visited_lock:
        if key in _visited_urls:return"visited"
        if key in _inflight_urls:return"inflight"
        _inflight_urls.add(key);return"new"

def _clear_inflight(url,scope=None):
    key=(_norm_url(url),_scope_key(scope)if scope else"")
    with _visited_lock:_inflight_urls.discard(key)

def _set_failed_log(path):_failed_log_path[0]=path

def _record_failed(url,fpath,reason):
    nu=_norm_url(url)
    with _failed_lock:
        if nu in _failed_seen:return
        _failed_seen.add(nu)
        p=_failed_log_path[0]
        if p:
            try:
                with open(p,"a",encoding="utf-8")as f:f.write(f"{url}\t{fpath}\t{reason}\n")
            except:pass

def _add_stat(key,n=1):
    with _stats_lock:_stats[key]=_stats.get(key,0)+n

def _print_stat():
    with _stats_lock:s=dict(_stats)
    print(f"  [ok={s.get('ok',0)} fail={s.get('fail',0)} skip={s.get('skip',0)} pages={s.get('pages',0)}]")

def _snap():
    with _stats_lock:return dict(_stats)

def _step_note(name,s0,t0):
    s1=_snap()
    _MANIFEST[name]={k:s1.get(k,0)-s0.get(k,0)for k in("ok","fail","skip","pages","errors")}
    _MANIFEST[name]["minutes"]=round((time.time()-t0)/60,2)

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

def _fetch(url,timeout=25,retries=5,wb_year=None):
    reason=None
    for attempt in range(retries):
        _rate_wait()
        try:
            r=_get_tl_scraper().get(url,timeout=timeout)
            code=r.status_code
            if code==404:
                if _fetch_err[1]==0:_fetch_err[0]="404";_fetch_err[1]=1
                if wb_year is not None:return _fetch_wb(url,wb_year)
                return None
            if code==429:
                _rate_backoff(strong=True,cooldown=25);reason="429"
                if _fetch_err[1]==0:_fetch_err[0]="429";_fetch_err[1]=1
                time.sleep(min(2**(attempt+1),8)+random.uniform(0.5,2));continue
            if code==403:
                _rate_backoff(strong=True,cooldown=45);reason="403"
                if _fetch_err[1]==0:_fetch_err[0]="403";_fetch_err[1]=1
                if attempt<retries-1:
                    time.sleep(min(2**(attempt+1),8)+random.uniform(1,3));continue
                return _fetch_wb(url,wb_year)if wb_year is not None else None
            if code in(502,503,504):
                _rate_backoff(cooldown=10);reason=str(code)
                if _fetch_err[1]==0:_fetch_err[0]=str(code);_fetch_err[1]=1
                time.sleep(min(2**(attempt+1),8)+random.uniform(0.5,1.5));continue
            r.raise_for_status()
            _rate_recover()
            if len(r.text)<300:
                if _fetch_err[1]==0:_fetch_err[0]=f"short({len(r.text)}b)";_fetch_err[1]=1
                if wb_year is not None:return _fetch_wb(url,wb_year)
                return None
            return r
        except Exception as e:
            reason=type(e).__name__
            if _fetch_err[1]==0:_fetch_err[0]=f"{type(e).__name__}:{str(e)[:80]}";_fetch_err[1]=1
            if reason=="HTTPError":
                code=getattr(getattr(e,"response",None),"status_code",None)
                if code in(403,429)or(code is not None and code>=500):
                    if attempt<retries-1:
                        cd=45 if code==403 else(25 if code==429 else 10)
                        _rate_backoff(strong=(code in(403,429)),cooldown=cd)
                        time.sleep(min(2**(attempt+1),8)+random.uniform(1,3));continue
                return _fetch_wb(url,wb_year)if wb_year is not None else None
            if wb_year is not None and attempt>=1:
                return _fetch_wb(url,wb_year)if reason not in("429","502","503","504")else None
            if attempt<retries-1:time.sleep(2**(attempt+1)+random.uniform(0.5,2));continue
            if wb_year is not None and reason not in("429","502","503","504"):return _fetch_wb(url,wb_year)
            return None
    return None

def _year_from_text(txt):
    m=re.search(r"(20\d{2})",str(txt))
    return m.group(1)if m else None

def _wb_get(url,timeout=20,scraper=None):
    with _wb_lock:
        if _wb_state["disabled"]:return None
    _rate_wait()
    try:
        r=(scraper or _get_tl_scraper()).get(url,timeout=timeout)
        if r.status_code==200 and len(r.text)>=300:
            with _wb_lock:_wb_state["fails"]=0
            return r
        return None
    except Exception:
        with _wb_lock:
            _wb_state["fails"]+=1
            if _wb_state["fails"]>=5 and not _wb_state["disabled"]:
                _wb_state["disabled"]=True
                print("  [WB] web.archive.org unreachable, Wayback fallback disabled for this run",flush=True)
        return None

def _fetch_wb(url,year=None):
    ts=str(year)if year else"2020"
    r=_wb_get(f"https://web.archive.org/web/{ts}/"+url)
    if r is not None:return r
    if ts!="2020":return _wb_get(f"https://web.archive.org/web/"+url)
    return None

def _clean(s):
    return re.sub(r'[\\/*?:"<>|]',"",str(s)).replace("\n"," ").strip()

def _ext(link):
    low=link.lower().split("?")[0]
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
    if href.startswith("//"):return urlparse(base).scheme+":"+href
    if href.startswith("/"):
        p=urlparse(base)
        return f"{p.scheme}://{p.netloc}{href}"
    return urljoin(base,href)

def _atomic_write_bytes(path,data):
    os.makedirs(os.path.dirname(path)or".",exist_ok=True)
    tmp=path+".part"
    try:
        with open(tmp,"wb")as f:
            f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):
            try:os.remove(tmp)
            except:pass

def _atomic_write_text(path,text):
    _atomic_write_bytes(path,text.encode("utf-8"))

def _bin_valid(url,head,size):
    low=url.lower().split("?")[0]
    if low.endswith(".bin"):return size>=100
    for ext,magic in _BIN_MAGIC.items():
        if low.endswith(ext):return head.startswith(magic)
    return size>=100

def _magic_ext(data):
    if data[:5]==b"%PDF-":return".pdf"
    if data[:2]==b"PK":
        return".docx"if b"[Content_Types].xml"in data[:600]else".zip"
    if data[:4]==b"\xd0\xcf\x11\xe0":return".doc"
    return None

def _fix_bin_ext(fpath,data):
    ext=_magic_ext(data)
    if not ext:return fpath
    base,cur=os.path.splitext(fpath)
    if cur.lower()and cur.lower()!=".bin":return fpath
    real=base+ext
    try:os.replace(fpath,real)
    except:return fpath
    with _FILE_MAP_LOCK:_FILE_MAP.append((fpath,real))
    return real

def _file_ok(url,fpath):
    if not os.path.exists(fpath):return False
    try:size=os.path.getsize(fpath)
    except:return False
    if size<=0:return False
    if _is_file(url):
        try:
            with open(fpath,"rb")as f:head=f.read(8)
            return _bin_valid(url,head,size)
        except:return False
    return size>=300

def _download_one(scraper,url,fpath,max_retries=3):
    sc=_scope_key(fpath)
    key=(_norm_url(url),sc)
    with _visited_lock:
        if key in _visited_urls:return"skip"
    if _file_ok(url,fpath):
        with _visited_lock:_visited_urls.add(key)
        return"skip"
    if _try_inflight(url,sc)!="new":return"skip"
    if _file_ok(url,fpath):
        with _visited_lock:_visited_urls.add(key)
        return"skip"
    try:os.makedirs(os.path.dirname(fpath)or".",exist_ok=True)
    except:pass
    is_bin=_is_file(url)
    reason="unknown"
    try:
        for attempt in range(max_retries):
            _rate_wait()
            try:
                r=scraper.get(url,timeout=30)
                code=r.status_code
                if code==404:
                    reason="HTTP 404";break
                if code==429:
                    _rate_backoff(strong=True,cooldown=25);reason="HTTP 429"
                    time.sleep(min(2**(attempt+1),8)+random.uniform(0.5,2));continue
                if code==403:
                    _rate_backoff(strong=True,cooldown=45);reason="HTTP 403"
                    if attempt<max_retries-1:
                        time.sleep(min(2**(attempt+1),8)+random.uniform(1,3));continue
                    break
                if code in(502,503,504):
                    _rate_backoff(cooldown=10);reason=f"HTTP {code}"
                    time.sleep(min(2**(attempt+1),8)+random.uniform(0.5,1.5));continue
                r.raise_for_status()
                data=r.content
                if is_bin:
                    if not _bin_valid(url,data[:8],len(data)):
                        reason="bad_magic";time.sleep(1.5);continue
                elif len(data)<300:
                    reason="short";time.sleep(1.5);continue
                _rate_recover()
                _atomic_write_bytes(fpath,data)
                _fix_bin_ext(fpath,data)
                with _visited_lock:_visited_urls.add(key)
                return"ok"
            except Exception as e:
                reason=type(e).__name__
                if reason=="HTTPError":
                    code=getattr(getattr(e,"response",None),"status_code",None)
                    reason=f"HTTP {code}" if code else "HTTPError"
                    if code in(403,429)or(code is not None and code>=500):
                        if attempt<max_retries-1:
                            cd=45 if code==403 else(25 if code==429 else 10)
                            _rate_backoff(strong=(code in(403,429)),cooldown=cd)
                            time.sleep(min(2**(attempt+1),8)+random.uniform(1,3));continue
                    break
                if attempt<max_retries-1:time.sleep(2**(attempt+1)+random.uniform(0.5,2));continue
        perm=reason.startswith("HTTP 4")and not reason.endswith("429")
        if perm or reason in("bad_magic","short","HTTPError"):
            wb_year=_year_from_text(fpath)or"2020"
            for cand in(f"https://web.archive.org/web/{wb_year}/"+url,f"https://web.archive.org/web/"+url):
                wbr=_wb_get(cand,scraper=scraper)
                if wbr is None:continue
                wdata=wbr.content
                if is_bin:
                    if not _bin_valid(url,wdata[:8],len(wdata)):continue
                elif len(wdata)<300:continue
                _atomic_write_bytes(fpath,wdata)
                _fix_bin_ext(fpath,wdata)
                with _visited_lock:_visited_urls.add(key)
                return"ok"
        _record_failed(url,fpath,reason)
        return"fail"
    finally:
        _clear_inflight(url,sc)

def _download_batch(tasks,workers):
    total=len(tasks)
    if not total:return
    def _run(ts,label):
        results={}
        with ThreadPoolExecutor(max_workers=workers)as ex:
            futs={ex.submit(_download_one,_get_tl_scraper(),u,f):(u,f)for u,f,_ in ts}
            last_beat=time.time();last_i=0
            for i,fut in enumerate(as_completed(futs),1):
                u,fp=futs[fut]
                try:r=fut.result()
                except Exception:r="fail"
                results[(u,fp)]=r;_add_stat(r)
                now=time.time()
                if i%200==0 or i==len(futs):
                    print(f"      [{label}] {i}/{len(futs)}");_print_stat();last_beat=now;last_i=i
                elif now-last_beat>=60:
                    rpm=(i-last_i)*60.0/max(1.0,now-last_beat)
                    print(f"      [{label}] {i}/{len(futs)} ({rpm:.1f} pages/min)");_print_stat();last_beat=now;last_i=i
        return results
    results=_run(tasks,"p1")
    ok=sum(1 for r in results.values()if r=="ok")
    fail=sum(1 for r in results.values()if r=="fail")
    skip=len(results)-ok-fail
    rate=(ok*100.0/(ok+fail))if(ok+fail)else 100.0
    print(f"      pass1: ok={ok} fail={fail} skip={skip} success={rate:.1f}%")
    if fail:
        time.sleep(3)
        failed=[(u,f)for(u,f),r in results.items()if r=="fail"]
        print(f"      Retrying {len(failed)} failed items...")
        results=_run([(u,f,"retry")for u,f in failed],"p2")
        ok2=sum(1 for r in results.values()if r=="ok")
        fail2=sum(1 for r in results.values()if r=="fail")
        if fail2:print(f"      Still failed: {fail2} -> failed_urls.tsv")
        total_ok=ok+ok2
        rate2=(total_ok*100.0/(total_ok+fail2))if(total_ok+fail2)else 100.0
        print(f"      after retry: ok={total_ok} fail={fail2} success={rate2:.1f}%")






import hashlib
import http.client as _http_client
import os
import platform
import random
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from . import config
from .config import (IGF_BASE, MAX_DEPTH, MAX_QUEUE, WORKERS, _BIN_MAGIC,
                     _FILE_MAP, _FILE_MAP_LOCK,
                     _GLOBAL_SCRAPER_LOCK, _MANIFEST, _NOISE_RE, _RATE_MAX,
                     _RATE_MIN, _failed_lock, _failed_log_path, _failed_seen,
                     _inflight_urls, _rate_lock, _rate_state, _stats,
                     _stats_lock, _visited_lock, _visited_urls, _wb_lock,
                     _wb_state, _fetch_err)

try:
    import cloudscraper
except Exception:
    cloudscraper = None




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

_UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
_LIVE_STATE=[0.0]
_LIVE_LOCK=threading.Lock()
_WB_RATE_LOCK=threading.Lock()
_WB_NEXT=[0.0]
_WB_SEM=threading.BoundedSemaphore(1)
_SOCK_MAP={}
_SOCK_LOCK=threading.Lock()


def _quiet_unraisable(err):
    try:
        if isinstance(getattr(err,"exc_value",None),ValueError)and"closed file"in str(err.exc_value):
            f=getattr(err,"traceback",None)
            while f is not None:
                if "http/client.py"in f.tb_frame.f_code.co_filename.replace("\\","/"):return
                f=f.tb_next
    except Exception:
        pass
    sys.__unraisablehook__(err)

sys.unraisablehook=_quiet_unraisable


def _patch_http_connect():
    """Record raw sockets created by urllib per calling thread so a hung
    request can be force-closed without touching other workers' sockets
    (trickling tarpits defeat per-recv timeouts)."""
    if getattr(_http_client.HTTPConnection,"_igf_tracked",False):return
    for _cls in(_http_client.HTTPConnection,_http_client.HTTPSConnection):
        _orig=_cls.connect
        def _tracked(self,_orig=_orig):
            _orig(self)
            try:
                if getattr(self,"sock",None)is not None:
                    tid=threading.get_ident()
                    with _SOCK_LOCK:_SOCK_MAP.setdefault(tid,set()).add(self.sock)
            except Exception:
                pass
        _cls.connect=_tracked
    _http_client.HTTPConnection._igf_tracked=True


def _kill_socks(tid):
    with _SOCK_LOCK:socks=list(_SOCK_MAP.pop(tid,set()))
    for s in socks:
        try:s.shutdown(socket.SHUT_RDWR)
        except Exception:pass
        try:s.close()
        except Exception:pass


def _urllib_deadline(url,timeout=45):
    """urllib GET with a hard wall-clock deadline: the request runs in a
    daemon thread and only that thread's sockets are force-closed when the
    deadline passes, so a trickling tarpit can never block a worker for more
    than timeout+20s (shutdown unblocks recv reliably on Windows)."""
    _patch_http_connect()
    box={}
    tid=[None]
    def _go():
        tid[0]=threading.get_ident()
        try:
            req=urllib.request.Request(url,headers={"User-Agent":_UA,"Accept":"*/*"})
            with urllib.request.urlopen(req,timeout=timeout)as resp:
                box["r"]=_Resp(resp.read(),resp.getcode())
        except Exception as e:
            box["e"]=e
    t=threading.Thread(target=_go,daemon=True)
    t.start()
    t.join(max(3,int(timeout))+20)
    if t.is_alive():
        _kill_socks(tid[0])
        t.join(5)
        return None
    _kill_socks(tid[0])
    r=box.get("r")
    if r is not None and r.status_code==200 and len(r.content)>=300:return r
    return None
def _bounded_scraper_get(scraper,url,timeout=15):
    """Run scraper.get in a daemon thread with a hard join deadline so a
    trickling tarpit can never block a worker forever."""
    box={}
    def _go():
        try:
            box["r"]=scraper.get(url,timeout=timeout)
        except Exception as e:
            box["e"]=e
    t=threading.Thread(target=_go,daemon=True)
    t.start()
    t.join(timeout+20)
    if t.is_alive():return None,TimeoutError("scraper hung")
    return box.get("r"),box.get("e")

class _Resp:
    def __init__(self,data,status=200):
        self.content=data;self.status_code=status;self._text=None
    @property
    def text(self):
        if self._text is None:self._text=self.content.decode("utf-8","replace")
        return self._text

    def raise_for_status(self):
        if self.status_code>=400:
            raise urllib.error.HTTPError("",self.status_code,"HTTP %d"%self.status_code,None,None)


class _FallbackScraper:
    def get(self,url,timeout=15):
        req=urllib.request.Request(url,headers={"User-Agent":_UA,"Accept":"*/*"})
        with urllib.request.urlopen(req,timeout=timeout)as resp:
            return _Resp(resp.read(),resp.getcode())

def _wb_rate_wait():
    with _WB_RATE_LOCK:
        extra=min(3.8,_wb_state.get("fails",0)*0.5)
        gap=1.2+extra+random.uniform(0,0.15)
        now=time.time()
        wait=max(0.0,_WB_NEXT[0]-now)
        _WB_NEXT[0]=max(now,_WB_NEXT[0])+gap
    if wait>0:time.sleep(wait)

def _norm_url(url):
    try:
        p=urlparse(url)
        netloc=p.netloc.lower().replace("www.","")
        path=p.path.rstrip("/")or"/"
        return f"{p.scheme}://{netloc}{path}"
    except:return url

_WB_LINK_RE=re.compile(r"^(?:https?://web\.archive\.org)?/web/(\d{4,14})\*?[a-z_]*/(.+)$",re.I)

def _unwrap_wb(href):
    href=str(href).strip()
    low=href.lower()
    m=_WB_LINK_RE.match(href)
    if m:
        target=m.group(2)
        tlow=target.lower()
        if tlow.startswith("//"):return"https:"+target
        if target.startswith("http")and"web.archive.org"not in tlow:return target
        return None
    if low.startswith("/web/"):return None
    if"web.archive.org"in low or"archive.org"in low:return None
    return href

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
    if config._GLOBAL_SCRAPER is None:
        with _GLOBAL_SCRAPER_LOCK:
            if config._GLOBAL_SCRAPER is None:
                if cloudscraper is None:
                    print("  cloudscraper unavailable, using urllib fallback",flush=True)
                    config._GLOBAL_SCRAPER=_FallbackScraper()
                else:
                    _platform=platform.system().lower()
                    if _platform not in("windows","darwin","linux"):_platform="windows"
                    print("  Initialising session (first request can take ~30s)...",flush=True)
                    config._GLOBAL_SCRAPER=cloudscraper.create_scraper(
                        browser={"browser":"chrome","platform":_platform,"desktop":True})
                    print("  Session ready.",flush=True)
    return config._GLOBAL_SCRAPER

def _fetch(url,timeout=25,retries=5,wb_year=None):
    reason=None
    for attempt in range(retries):
        _rate_wait()
        try:
            r,err=_bounded_scraper_get(_get_tl_scraper(),url,timeout=timeout)
            if r is None:raise err
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

def _wb_get(url,timeout=30,scraper=None):
    with _wb_lock:
        if _wb_state.get("disabled_until",0.0)>time.time():return None
    _wb_rate_wait()
    with _WB_SEM:
        budget=max(15,min(25,int(timeout)))+10
        t0=time.time()
        r=_urllib_deadline(url,budget)
        if r is not None:
            with _wb_lock:
                _wb_state["fails"]=0
                _wb_state["stalls"]=0
                _wb_state["disables"]=0
                _wb_state["disabled"]=False
                _wb_state.pop("disabled_until",None)
            return r
        if time.time()-t0>=budget-2:
            with _wb_lock:
                _wb_state["stalls"]=_wb_state.get("stalls",0)+1
                if _wb_state["stalls"]>=3:
                    _wb_state["disables"]=_wb_state.get("disables",0)+1
                    pause=min(1800.0,600.0*(2**max(0,_wb_state["disables"]-1)))
                    _wb_state["disabled_until"]=time.time()+pause
                    _wb_state["stalls"]=0
                    print(f"  [WB] archive.org trickling (tarpit): Wayback paused {int(pause)}s",flush=True)
            return None
    r,_=_bounded_scraper_get((scraper or _get_tl_scraper()),url,timeout=max(20,int(timeout)))
    if r is not None and getattr(r,"status_code",0)==200 and len(getattr(r,"content",b""))>=300:
        with _wb_lock:
            _wb_state["fails"]=0
            _wb_state["stalls"]=0
            _wb_state["disables"]=0
            _wb_state["disabled"]=False
            _wb_state.pop("disabled_until",None)
        return r
    with _wb_lock:
        _wb_state["fails"]=_wb_state.get("fails",0)+1
        if _wb_state["fails"]>=40:
            _wb_state["disables"]=_wb_state.get("disables",0)+1
            pause=min(1800.0,300.0*(2**max(0,_wb_state["disables"]-1)))
            _wb_state["disabled_until"]=time.time()+pause
            _wb_state["fails"]=0
            print(f"  [WB] archive.org unreachable, pausing Wayback for {int(pause)}s",flush=True)
    return None
def _fetch_wb(url,year=None):
    ts=str(year)if year else"2020"
    r=_wb_get(f"https://web.archive.org/web/{ts}/"+url)
    if r is not None:return r
    if ts!="2020":return _wb_get("https://web.archive.org/web/"+url)
    return None

def _live_get(url,timeout=10):
    return _urllib_deadline(url,max(10,int(timeout))+5)

def _live_ok(url,timeout=5):
    return _urllib_deadline(url,max(6,int(timeout)))is not None

def _fetch_deep(url,year=None,timeout=10,state=None):
    st=state if state is not None else _LIVE_STATE
    now=time.time()
    if st[0]<=now:
        if st[0]==0.0:
            r=_live_get(url,timeout)
            if r is not None:return r
            if _LIVE_LOCK.acquire(blocking=False):
                try:st[0]=max(now+240.0,_wb_state.get("disabled_until",0.0))
                finally:_LIVE_LOCK.release()
        elif _LIVE_LOCK.acquire(blocking=False):
            try:
                r=_live_get(url,timeout)
                if r is not None:
                    st[0]=0.0
                    return r
                st[0]=max(now+240.0,_wb_state.get("disabled_until",0.0))
            finally:
                _LIVE_LOCK.release()
    if _wb_state.get("disabled_until",0.0)>time.time():return None
    return _fetch_wb(url,year)or _fetch_wb(url,None)
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
    if head[:1]in(b"<",b"{")or head.lstrip()[:5].lower()==b"<html":return False
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

def _wb_download(url,fpath,is_bin,scraper,key):
    wb_year=_year_from_text(fpath)or"2020"
    for cand in("https://web.archive.org/web/%s/"%wb_year+url,"https://web.archive.org/web/"+url):
        with _wb_lock:
            if _wb_state.get("disabled_until",0.0)>time.time():return None
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
    return None
def _download_one(scraper,url,fpath,max_retries=3,prefer_wb=None):
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
        if prefer_wb is None:prefer_wb=(_LIVE_STATE[0]>time.time())
        if not prefer_wb and not _live_ok(url,timeout=5):
            prefer_wb=True
        if prefer_wb:
            if _wb_download(url,fpath,is_bin,scraper,key)=="ok":return"ok"
            _record_failed(url,fpath,"wb_miss")
            return"fail"
        for attempt in range(max_retries):
            _rate_wait()
            try:
                r,err=_bounded_scraper_get(scraper,url,timeout=15)
                if r is None:raise err
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
                    break
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
        wb_reasons=("bad_magic","short","HTTPError","ReadTimeout","ConnectTimeout",
            "ConnectionError","ProxyError","SSLError","ChunkedEncodingError","TooManyRedirects")
        if perm or reason in wb_reasons or reason.startswith("HTTP 5"):
            if _wb_download(url,fpath,is_bin,scraper,key)=="ok":return"ok"
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
                    print(f"      [{label}] {i}/{len(futs)}",flush=True);_print_stat();last_beat=now;last_i=i
                elif now-last_beat>=60:
                    rpm=(i-last_i)*60.0/max(1.0,now-last_beat)
                    print(f"      [{label}] {i}/{len(futs)} ({rpm:.1f} pages/min)",flush=True);_print_stat();last_beat=now;last_i=i
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




def _strip_noise(soup):
    for tag in soup(["script","style","noscript","template","iframe","form","button","input","select","textarea","link"]):
        tag.decompose()
    for tag in soup(["nav","header","footer","aside"]):
        tag.decompose()
    for el in soup.find_all(True):
        if getattr(el,"attrs",None)is None:continue
        cls=" ".join(el.get("class")or[])
        ident=str(el.get("id")or"")
        if not(_NOISE_RE.search(cls)or _NOISE_RE.search(ident)):continue
        if el.select_one("[class*='field--name-field-']"):continue
        el.decompose()
    return soup

def _next_page_links(soup,base_url):
    out=[];seen=set()
    for a in soup.find_all("a",href=True):
        href=a["href"].strip()
        if not href or href.startswith("#")or href.startswith("javascript:"):continue
        href=_unwrap_wb(href)
        if href is None:continue
        skip=False
        for anc in a.parents:
            if anc.name in("nav","header","footer","aside"):
                skip=True;break
        if skip:continue
        rel=" ".join(a.get("rel")or[])if isinstance(a.get("rel"),list)else str(a.get("rel")or"")
        title=str(a.get("title")or"")
        cls=" ".join(a.get("class")or[])if isinstance(a.get("class"),list)else str(a.get("class")or"")
        is_next=("next"in rel.lower())or re.search(r"next|next page",title,re.I)
        if not is_next and"next"in cls.lower():is_next=("pager"in cls.lower()or"pagination"in cls.lower())
        if not is_next:continue
        full=_make_url(href,base_url)
        if full in seen:continue
        seen.add(full);out.append(full)
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
        if not items and 'field__item' in (elem.get('class')or[]):
            items=[elem]
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
        scraper=_get_tl_scraper()
        while True:
            with _wb_lock:
                until=_wb_state.get("disabled_until",0.0)
            if until>time.time() and _LIVE_STATE[0]>time.time() and task_queue.qsize()>0:
                time.sleep(30)
                continue
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
                cached=None
                if os.path.exists(page_path)and os.path.getsize(page_path)>=300:
                    try:
                        with open(page_path,"r",encoding="utf-8",errors="ignore")as cf:cached=cf.read()
                        if len(cached)<300:cached=None
                    except:cached=None
                r=None
                if cached is None:
                    if depth==0 and _norm_url(url)==_norm_url(seed_url):
                        for attempt in range(3):
                            if _LIVE_STATE[0]<=time.time():
                                live_r,_=_bounded_scraper_get(scraper,url,timeout=15)
                                if live_r is not None and live_r.status_code==200 and len(getattr(live_r,"content",b""))>=300:
                                    r=live_r;break
                            r=_fetch_deep(url,year=seed_year,state=_LIVE_STATE)
                            if r is not None:break
                            with _wb_lock:
                                until=_wb_state.get("disabled_until",0.0)
                            if until>time.time():time.sleep(min(300.0,until-time.time()))
                            else:time.sleep(45*(attempt+1))
                    else:
                        r=_fetch_deep(url,year=seed_year,state=_LIVE_STATE)
                if cached is None and r is None:
                    _unmark_visited(url,current_dir)
                    with stats_lock:local_stats["errors"]+=1
                    _record_failed(url,page_path,"fetch")
                    if depth==0:
                        err_path=os.path.join(current_dir,"_FETCH_FAILED.txt")
                        try:
                            with open(err_path,"w",encoding="utf-8")as ef:ef.write(f"Failed to fetch seed URL: {url}\nCheck if URL exists.\n")
                        except:pass
                    continue
                html=cached if cached is not None else r.text
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
                    skip=False
                    for anc in a.parents:
                        if anc.name in("nav","header","footer","aside"):
                            skip=True;break
                    if skip:continue
                    href=_unwrap_wb(href)
                    if href is None:continue
                    full=_make_url(href,url)
                    if _is_file(full):
                        if not _is_igf_domain(full):
                            url_low=full.lower()
                            if not ((seed_year and seed_year in url_low)or any(kw in url_low for kw in["igf","intgov","internet-governance","wsis"])):
                                continue
                        fname=_clean(full.split("/")[-1].split("?")[0])
                        fpath=os.path.join(files_dir,fname)
                        if _file_ok(full,fpath):
                            with stats_lock:local_stats["files"]+=1
                        elif _download_one(scraper,full,fpath,prefer_wb=(_LIVE_STATE[0]>time.time()))=="ok":
                            with stats_lock:local_stats["files"]+=1
                    elif _is_igf_domain(full):
                        am=re.search(r"/en/(?:archived|dashboard)/([^/?#]*)",full)
                        if am and am.group(1):
                            ym=re.search(r"(20\d{2})",am.group(1))
                            if ym and ym.group(1)!=seed_year:continue
                        if depth<MAX_DEPTH:
                            if task_queue.qsize()<MAX_QUEUE:task_queue.put((full,depth+1,current_dir))
                            else:_log_dropped(full)
                    elif depth<MAX_DEPTH:
                        url_low=full.lower()
                        relevant=(seed_year and seed_year in url_low)or any(kw in url_low for kw in["igf","intgov","internet-governance","wsis"])
                        if relevant:
                            if task_queue.qsize()<MAX_QUEUE:task_queue.put((full,depth+1,current_dir))
                            else:_log_dropped(full)
                for nxt in _next_page_links(soup,url):
                    nxt=_unwrap_wb(nxt)
                    if nxt is None:continue
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
                    print(f"    [busy] no progress: {s['pages']}p {s['files']}f {s['errors']}e (queue={qsize} pending={unfinished})",flush=True)
                last_beat=now;last_sig=sig
            if unfinished==0:
                idle_count+=1
                if idle_count>=3:break
            else:idle_count=0
        running[0]=False
        for t in threads:t.join(timeout=30)
    dropped_seen=set()
    requeued_last=False
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
        if not new:
            requeued_last=False;break
        print(f"    [pass {_pass+2}] re-queuing {len(new)} dropped links")
        for u in new:task_queue.put((u,1,out_dir))
        requeued_last=True
    if requeued_last and task_queue.qsize()>0:
        print("    [pass final] draining re-queued links")
        _crawl_pass(task_queue)
    with stats_lock:s=dict(local_stats)
    print(f"    DONE: {s['pages']} pages, {s['files']} files, {s['errors']} errors")
    _add_stat("pages",s["pages"])
    _add_stat("errors",s["errors"])
    if os.path.exists(dropped_path)and os.path.getsize(dropped_path)>0:
        print(f"    Note: queue-capped links logged -> {os.path.relpath(dropped_path,os.getcwd())}")

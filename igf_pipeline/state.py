"""Thread-safe mutable runtime state shared by all model modules.
Kept in one place so the download engine can be tested in isolation."""
import threading


_visited_lock=threading.Lock()
_visited_urls=set()
_inflight_urls=set()
_stats_lock=threading.Lock()
_stats={"ok":0,"fail":0,"skip":0,"pages":0,"errors":0}

_rate_lock=threading.Lock()
_rate_state={"gap":0.35,"next_ts":0.0,"streak":0,"cooldown_until":0.0}

_failed_lock=threading.Lock()
_failed_seen=set()
_failed_log_path=[None]

_classify_errors=[]
_classify_err_lock=threading.Lock()

_MANIFEST={}

_GLOBAL_SCRAPER=None
_GLOBAL_SCRAPER_LOCK=threading.Lock()

_fetch_err=[None,0]

_wb_lock=threading.Lock()
_wb_state={"fails":0,"disabled":False}

_FILE_MAP=[];_FILE_MAP_LOCK=threading.Lock()



# -*- coding: utf-8 -*-
"""One-off generator: split scrape_igf.py into the igf_pipeline MVC package.

Principle: cut the original file at verified anchors, copy blocks verbatim,
and only rewrite cross-module references.  Every boundary is asserted, so the
script fails loudly if scrape_igf.py has drifted from the expected layout.
"""
import os
import sys

ROOT = r"C:\Users\guhao\PyCharmMiscProject"
SRC = os.path.join(ROOT, "scrape_igf.py")
PKG = os.path.join(ROOT, "igf_pipeline")
MODELS = os.path.join(PKG, "models")
CTRLS = os.path.join(PKG, "controllers")

lines = open(SRC, encoding="utf-8").read().split("\n")
total = len(lines)
print("source lines:", total)
assert total >= 1210, "unexpected source size"

# (start, end, target, anchor-to-assert)
SEGMENTS = [
    (11, 18, "state", "_visited_lock"),
    (19, 20, "config", "_RATE_MIN"),
    (21, 26, "network", "def _rate_wait"),
    (28, 30, "network", "def _rate_backoff"),
    (31, 32, "network", "def _rate_recover"),
    (33, 40, "network", "def _norm_url"),
    (41, 46, "network", "def _scope_key"),
    (47, 51, "network", "def _scope_dir"),
    (52, 57, "network", "def _mark_visited"),
    (58, 61, "network", "def _unmark_visited"),
    (62, 68, "network", "def _try_inflight"),
    (69, 71, "network", "def _clear_inflight"),
    (72, 74, "state", "_failed_lock"),
    (75, 76, "state", "_classify_errors"),
    (77, 78, "network", "def _set_failed_log"),
    (79, 88, "network", "def _record_failed"),
    (90, 92, "network", "def _add_stat"),
    (93, 96, "network", "def _print_stat"),
    (97, 97, "state", "_MANIFEST"),
    (98, 100, "network", "def _snap"),
    (101, 105, "network", "def _step_note"),
    (106, 111, "config", "IGF_BASE"),
    (112, 113, "state", "_GLOBAL_SCRAPER=None"),
    (114, 124, "network", "def _get_tl_scraper"),
    (125, 125, "state", "_fetch_err"),
    (126, 161, "network", "def _fetch("),
    (162, 165, "network", "def _year_from_text"),
    (166, 167, "state", "_wb_lock"),
    (168, 185, "network", "def _wb_get"),
    (186, 192, "network", "def _fetch_wb"),
    (193, 195, "network", "def _clean"),
    (196, 202, "network", "def _ext("),
    (203, 204, "network", "def _is_file"),
    (205, 211, "network", "def _same_domain"),
    (212, 217, "network", "def _is_igf_domain"),
    (218, 224, "network", "def _make_url"),
    (226, 226, "config", "_NOISE_RE"),
    (228, 239, "dom", "def _strip_noise"),
    (240, 255, "dom", "def _next_page_links"),
    (256, 267, "network", "def _atomic_write_bytes"),
    (268, 270, "network", "def _atomic_write_text"),
    (271, 273, "config", "_BIN_MAGIC"),
    (274, 280, "network", "def _bin_valid"),
    (281, 287, "network", "def _magic_ext"),
    (288, 288, "state", "_FILE_MAP"),
    (289, 299, "network", "def _fix_bin_ext"),
    (300, 310, "network", "def _file_ok"),
    (311, 373, "network", "def _download_one"),
    (374, 410, "network", "def _download_batch"),
    (411, 420, "config", "SESSION_TYPES"),
    (421, 422, "config", "DETAIL_RE"),
    (423, 481, "controllers", "def step_sessions"),
    (482, 486, "controllers", "def step_reports"),
    (487, 489, "config", "_REPORT_HINTS"),
    (490, 523, "controllers", "def _discover_reports"),
    (524, 542, "controllers", "def step_transcripts"),
    (543, 561, "controllers", "def step_schedules"),
    (562, 627, "controllers", "def _download_yearly_pages"),
    (628, 645, "config", "ARCHIVED"),
    (646, 652, "config", "DASHBOARD"),
    (653, 666, "controllers", "def step_archived_dashboard"),
    (667, 800, "deepcrawl", "def _deep_crawl_parallel"),
    (801, 807, "config", "PARTICIPANTS"),
    (808, 813, "controllers", "def step_participants"),
    (814, 821, "controllers", "def _remove_empty_dirs"),
    (822, 834, "config", "TYPE_PATTERNS"),
    (835, 836, "config", "TYPE_RE="),
    (837, 855, "config", "WEIGHTED_RULES"),
    (856, 859, "config", "TYPE_PRIORITY"),
    (860, 865, "classify", "def _classify_by_filename"),
    (866, 884, "classify", "def _classify_by_content"),
    (885, 902, "classify", "def _validate_html"),
    (903, 909, "classify", "def _content_hash"),
    (910, 921, "classify", "def _extract_year"),
    (922, 937, "classify", "def _process_html_file"),
    (938, 1012, "classify", "def run_classify"),
    (1013, 1039, "dom", "def _extract_drupal_fields_json"),
    (1040, 1088, "extract", "def _extract_one_file"),
    (1089, 1121, "extract", "def run_extract"),
    (1122, 1139, "controllers", "def _retry_failed_file"),
    (1140, 1140, "config", "STEPS="),
]

blocks = {t: [] for t in ("state", "config", "network", "dom", "deepcrawl", "classify", "extract", "controllers")}
for start, end, target, anchor in SEGMENTS:
    first = lines[start - 1]
    ok = first.startswith(anchor) or anchor in first
    if not ok:
        print("ANCHOR MISMATCH", target, (start, end), repr(anchor), "->", repr(first[:80]))
        sys.exit(1)
    block = lines[start - 1:end]
    while block and not block[-1].strip():
        block.pop()
    blocks[target].append("\n".join(block))

for target in blocks:
    print("%-12s %2d blocks, %5d chars" % (target, len(blocks[target]), sum(len(b) for b in blocks[target])))

REWRITES = {
    "controllers": [
        ("_fetch_wb(", "network._fetch_wb("),
        ("_fetch(", "network._fetch("),
        ("_download_batch(", "network._download_batch("),
        ("_atomic_write_text(", "network._atomic_write_text("),
        ("_make_url(", "network._make_url("),
        ("_is_file(", "network._is_file("),
        ("_same_domain(", "network._same_domain("),
        ("_clean(", "network._clean("),
        ("_deep_crawl_parallel(", "deepcrawl._deep_crawl_parallel("),
        ("_snap(", "network._snap("),
        ("_step_note(", "network._step_note("),
        ("_set_failed_log(", "network._set_failed_log("),
        ("_print_stat(", "network._print_stat("),
        ("_add_stat(", "network._add_stat("),
    ],
    "deepcrawl": [
        ("_fetch(", "network._fetch("),
        ("_get_tl_scraper(", "network._get_tl_scraper("),
        ("_next_page_links(", "dom._next_page_links("),
    ],
    "classify": [("_strip_noise(", "dom._strip_noise(")],
    "dom": [("_make_url(", "network._make_url(")],
    "extract": [
        ("_validate_html(", "classify._validate_html("),
        ("_strip_noise(", "dom._strip_noise("),
        ("_classify_by_filename(", "classify._classify_by_filename("),
        ("_classify_by_content(", "classify._classify_by_content("),
        ("_extract_year(", "classify._extract_year("),
        ("_extract_drupal_fields_json(", "dom._extract_drupal_fields_json("),
        ("_make_url(", "network._make_url("),
        ("_content_hash(", "classify._content_hash("),
    ],
}

# Debug-only, behavior-preserving edits (active only when --years/--limit are used).
CONTROLLER_EDITS = [
    ("def step_sessions(out_root,workers=WORKERS):",
     "def step_sessions(out_root,workers=WORKERS,years=None,limit=None):", 1),
    ("def step_reports(out_root,workers=WORKERS):",
     "def step_reports(out_root,workers=WORKERS,years=None):", 1),
    ("def _discover_reports(out_root,workers=WORKERS):",
     "def _discover_reports(out_root,workers=WORKERS,years=None):", 1),
    ("def step_transcripts(out_root,workers=WORKERS):",
     "def step_transcripts(out_root,workers=WORKERS,years=None):", 1),
    ("def step_schedules(out_root,workers=WORKERS):",
     "def step_schedules(out_root,workers=WORKERS,years=None):", 1),
    ("def _download_yearly_pages(url_template,out_base,workers,fallback_templates=None):",
     "def _download_yearly_pages(url_template,out_base,workers,fallback_templates=None,years=None):", 1),
    ("for y in range(YEAR_START,YEAR_END+1):",
     "for y in year_range(years):", 5),
    ("        fallback_templates=[IGF_BASE+\"/en/igf-{year}-report\",IGF_BASE+\"/en/content/igf-{year}-final-report\",IGF_BASE+\"/en/igf-{year}-final-report\"])",
     "        fallback_templates=[IGF_BASE+\"/en/igf-{year}-report\",IGF_BASE+\"/en/content/igf-{year}-final-report\",IGF_BASE+\"/en/igf-{year}-final-report\"],years=years)", 1),
    ("    if not all_tasks:print(\"  No session links found.\");return\n    print(f\"\\n  Downloading {len(all_tasks)} pages...\")",
     "    if not all_tasks:print(\"  No session links found.\");return\n    if limit:all_tasks=all_tasks[:limit]\n    print(f\"\\n  Downloading {len(all_tasks)} pages...\")", 1),
]

HEADERS = {
    "config": '"""Global configuration: URL templates, session types, year range,\nclassification rules and noise patterns (Model layer configuration)."""\nimport re\n\n\n',
    "state": '"""Thread-safe mutable runtime state shared by all model modules.\nKept in one place so the download engine can be tested in isolation."""\nimport threading\n\n\n',
    "network": '"""Network engine (Model): cloudscraper session, rate limiting with\nbackoff/recovery, Wayback Machine fallback, atomic writes, binary magic\nvalidation, visited/inflight tracking and the multi-threaded downloader."""\nimport os,re,time,random,threading,hashlib\nfrom urllib.parse import urljoin,urlparse\nfrom concurrent.futures import ThreadPoolExecutor,as_completed\nimport cloudscraper\n\nfrom ..config import _RATE_MIN,_RATE_MAX,IGF_BASE,_BIN_MAGIC\nfrom ..state import (_visited_lock,_visited_urls,_inflight_urls,_stats_lock,_stats,\n    _rate_lock,_rate_state,_failed_lock,_failed_seen,_failed_log_path,_MANIFEST,\n    _GLOBAL_SCRAPER,_GLOBAL_SCRAPER_LOCK,_fetch_err,_wb_lock,_wb_state,\n    _FILE_MAP,_FILE_MAP_LOCK)\n\n\n',
    "dom": '"""DOM parsing (Model): navigation/footer noise removal, pagination link\ndiscovery and Drupal structured-field extraction."""\nimport re\n\nfrom ..config import _NOISE_RE\nfrom . import network\n\n\n',
    "deepcrawl": '"""Breadth-first deep crawler (Model): worker-pool queue that expands list\npages up to MAX_DEPTH, downloads linked documents and re-queues dropped URLs.\nNetwork hooks (_fetch / _get_tl_scraper) are called through the network module\nso tests can monkeypatch them."""\nimport os,re,time,hashlib,threading\nfrom queue import Queue\nfrom bs4 import BeautifulSoup\n\nfrom ..config import MAX_DEPTH,MAX_QUEUE,WORKERS\nfrom .network import (_mark_visited,_unmark_visited,_clean,_is_igf_domain,_make_url,\n    _is_file,_file_ok,_download_one,_record_failed,_atomic_write_text,\n    _atomic_write_bytes,_add_stat,_norm_url)\nfrom . import network,dom\n\n\n',
    "classify": '"""Page classification and validation (Model): type rules by filename/content,\nHTML validity checks, content hashing, year extraction, dedup and the\nclassified-directory writer."""\nimport os,re,hashlib,shutil\nfrom datetime import datetime\nfrom collections import defaultdict\nfrom pathlib import Path\nfrom concurrent.futures import ThreadPoolExecutor,as_completed\nfrom bs4 import BeautifulSoup\n\nfrom ..config import TYPE_RE,WEIGHTED_RULES,TYPE_PRIORITY\nfrom ..state import _classify_errors,_classify_err_lock\nfrom . import dom\n\n\n',
    "extract": '"""HTML to JSON extraction (Model): one JSON record per page built from the\nDOM tree (title, meta, headings, links, Drupal fields, full body text)."""\nimport os,re,json\nfrom datetime import datetime\nfrom pathlib import Path\nfrom concurrent.futures import ThreadPoolExecutor,as_completed\nfrom bs4 import BeautifulSoup\n\nfrom . import network,dom,classify\n\n\n',
    "controllers": '"""Scrape step controllers: build the per-year task lists (sessions, reports,\ntranscripts, schedules, archived, dashboard, participants), drive the download\nengine and retry failed URLs.  Debug-friendly: years= filters the range and\nlimit= caps the number of session pages."""\nimport os,re,time,random\nfrom pathlib import Path\nfrom bs4 import BeautifulSoup\n\nfrom ..config import (IGF_BASE,WORKERS,YEAR_START,YEAR_END,SESSION_TYPES,DETAIL_RE,\n    _REPORT_HINTS,ARCHIVED,DASHBOARD,PARTICIPANTS,year_range)\nfrom ..state import _fetch_err\nfrom ..models import network,deepcrawl\n\n\n',
}

FILE_PATHS = {
    "config": os.path.join(PKG, "config.py"),
    "state": os.path.join(PKG, "state.py"),
    "network": os.path.join(MODELS, "network.py"),
    "dom": os.path.join(MODELS, "dom.py"),
    "deepcrawl": os.path.join(MODELS, "deepcrawl.py"),
    "classify": os.path.join(MODELS, "classify.py"),
    "extract": os.path.join(MODELS, "extract.py"),
    "controllers": os.path.join(CTRLS, "scraper.py"),
}

CONFIG_TAIL = '''

def year_range(years=None):
    """Resolve the years= debug filter to a concrete list of years.

    None  -> the full configured range (identical to the original behaviour)
    int   -> a single year
    str   -> '2020', or '2020-2022'
    list  -> the given years, coerced to int
    """
    if years is None:
        return list(range(YEAR_START, YEAR_END + 1))
    if isinstance(years, int):
        return [years]
    if isinstance(years, str):
        years = years.strip()
        m = re.match(r"^(\\d{4})\\s*-\\s*(\\d{4})$", years)
        if m:
            return list(range(int(m.group(1)), int(m.group(2)) + 1))
        m2 = re.match(r"^(\\d{4})$", years)
        if m2:
            return [int(m2.group(1))]
        return list(range(YEAR_START, YEAR_END + 1))
    return [int(y) for y in years]
'''

for target, path in FILE_PATHS.items():
    body = "\n\n".join(blocks[target])
    for old, new in REWRITES.get(target, []):
        body = body.replace(old, new)
    if target == "controllers":
        for old, new, expected in CONTROLLER_EDITS:
            n = body.count(old)
            if n != expected:
                print("EDIT COUNT MISMATCH", old[:60], "found", n, "expected", expected)
                sys.exit(1)
            body = body.replace(old, new)
    out = HEADERS[target] + body
    if target == "config":
        out += CONFIG_TAIL
    out += "\n"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(out)
    print("wrote", path)

print("PACKAGE SPLIT OK")

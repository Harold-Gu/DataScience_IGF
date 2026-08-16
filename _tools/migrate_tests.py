# -*- coding: utf-8 -*-
"""Migrate _test_dl.py -> tests/test_download.py (module-qualified names)."""
import os

ROOT = r"C:\Users\guhao\PyCharmMiscProject"
src = open(os.path.join(ROOT, "_test_dl.py"), encoding="utf-8").read()

src = src.replace(
    "# Offline self-tests for the scrape_igf.py download and extraction pipeline.\n"
    "# Run: python _test_dl.py\n"
    "# Every network call is monkeypatched, so the suite is fully offline.",
    "# Offline self-tests for the igf_pipeline download and extraction modules.\n"
    "# Run: python tests/test_download.py   (or: python main.py selftest)\n"
    "# Every network call is monkeypatched, so the suite is fully offline.",
)

old_import = (
    "try:\n"
    "    import scrape_igf as S\n"
    "    from bs4 import BeautifulSoup\n"
    "    HAS_S = True\n"
    "except Exception:\n"
    "    HAS_S = False\n"
)
new_import = (
    "try:\n"
    "    from igf_pipeline.models import network, dom, classify, extract, deepcrawl\n"
    "    from bs4 import BeautifulSoup\n"
    "    HAS_S = True\n"
    "except Exception:\n"
    "    HAS_S = False\n"
)
assert old_import in src, "import block not found"
src = src.replace(old_import, new_import)

REPL = [
    ("S._extract_drupal_fields_json", "dom._extract_drupal_fields_json"),
    ("S._classify_by_content", "classify._classify_by_content"),
    ("S._extract_one_file", "extract._extract_one_file"),
    ("S._deep_crawl_parallel", "deepcrawl._deep_crawl_parallel"),
    ("S._next_page_links", "dom._next_page_links"),
    ("S._strip_noise", "dom._strip_noise"),
    ("S._magic_ext", "network._magic_ext"),
    ("S._fix_bin_ext", "network._fix_bin_ext"),
    ("S._bin_valid", "network._bin_valid"),
    ("S._file_ok", "network._file_ok"),
    ("S._atomic_write_bytes", "network._atomic_write_bytes"),
    ("S._download_one", "network._download_one"),
    ("S._download_batch", "network._download_batch"),
    ("S._norm_url", "network._norm_url"),
    ("S._scope_key", "network._scope_key"),
    ("S._year_from_text", "network._year_from_text"),
    ("S._visited_urls", "network._visited_urls"),
    ("S._inflight_urls", "network._inflight_urls"),
    ("S._failed_seen", "network._failed_seen"),
    ("S._FILE_MAP", "network._FILE_MAP"),
    ("S._failed_log_path", "network._failed_log_path"),
    ("S._stats", "network._stats"),
    ("S._rate_state", "network._rate_state"),
    ("S._wb_state", "network._wb_state"),
    ("S._fetch", "network._fetch"),
    ("S._wb_get", "network._wb_get"),
    ("S._get_tl_scraper", "network._get_tl_scraper"),
    ("setattr(S, name, value)", "setattr(network, name, value)"),
    ("getattr(S, name)", "getattr(network, name)"),
]
for old, new in REPL:
    src = src.replace(old, new)

assert " as S" not in src
assert "S._" not in src

out_dir = os.path.join(ROOT, "tests")
os.makedirs(out_dir, exist_ok=True)
with open(os.path.join(out_dir, "test_download.py"), "w", encoding="utf-8", newline="\n") as fh:
    fh.write(src)
print("migrated", len(src), "chars")

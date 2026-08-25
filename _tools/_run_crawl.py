import faulthandler
import os
import sys


faulthandler.enable()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from igf_pipeline import crawl as network
from igf_pipeline import pipeline as scraper

OUT = os.path.join(ROOT, "igf_full_20260817_130607")
network._set_failed_log(os.path.join(OUT, "failed_urls.tsv"))
print("PHASE1_ARCHIVED_DASHBOARD", flush=True)
scraper.step_archived_dashboard(OUT, workers=8)
print("ARCHIVED_DASHBOARD_DONE", flush=True)
print("PHASE2_DISCOVER_REPORTS", flush=True)
scraper._discover_reports(OUT, workers=8)
print("DISCOVER_REPORTS_DONE", flush=True)
print("PHASE3_PARTICIPANTS", flush=True)
scraper.step_participants(OUT, workers=4)
print("PARTICIPANTS_DONE", flush=True)
print("PHASE4_RETRY_FAILED", flush=True)
scraper._retry_failed_file(os.path.join(OUT, "failed_urls.tsv"), workers=8)
print("RETRY_FAILED_DONE", flush=True)
print("ALL_PHASES_DONE", flush=True)

# -*- coding: utf-8 -*-
"""Split report_scrape.py into models/validation.py + views/report_view.py."""
import os
import sys

ROOT = r"C:\Users\guhao\PyCharmMiscProject"
SRC_REPORT = os.path.join(ROOT, "_tools", "legacy_backup", "report_scrape.py.bak")
lines = open(SRC_REPORT, encoding="utf-8").read().split("\n")


def grab(start, end):
    block = lines[start - 1:end]
    while block and not block[-1].strip():
        block.pop()
    return "\n".join(block)


MODEL_HEADER = '''"""Validation model: scan crawl directories, flag bad pages,
statistics per directory and JSON-level integrity checks (report_scrape
logic).  Rendering lives in views/report_view.py."""\nimport os,re,json\nfrom collections import defaultdict, Counter\nfrom pathlib import Path\nfrom bs4 import BeautifulSoup\n\nfrom ..views.report_view import SEP, SEP2\n\n\n'''
VIEW_HEADER = '''"""Validation report rendering (View layer): quality tables,
directory breakdown and Drupal field analysis output."""\n\n\n'''

model_body = "\n\n".join([
    grab(13, 93),    # scan_html
    grab(159, 197),  # validate_json
    grab(198, 206),  # validate_documents
])
view_body = "\n\n".join([
    'SEP = "' + "=" * 70 + '"',
    'SEP2 = "' + "-" * 70 + '"',
    grab(9, 11),     # sp()
    grab(95, 110),   # print_quality
    grab(112, 126),  # print_type_table
    grab(128, 138),  # TYPE_DESCRIPTIONS
    grab(140, 157),  # analyze_drupal
])

out_model = os.path.join(ROOT, "igf_pipeline", "models", "validation.py")
out_view = os.path.join(ROOT, "igf_pipeline", "views", "report_view.py")
with open(out_model, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(MODEL_HEADER + model_body + "\n")
with open(out_view, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(VIEW_HEADER + view_body + "\n")
print("wrote", out_model)
print("wrote", out_view)

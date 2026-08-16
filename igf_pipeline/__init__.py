"""IGF full-data pipeline, organised as MVC.

models/       data layer: download engine, DOM parsing, classification,
              extraction, validation, denoising, transcript recovery
views/        output layer: console and validation-report rendering
controllers/  orchestration: scrape steps, pipeline, validation runs,
              LLM experiment dispatch
cli.py        argparse entry point (legacy flags + debug subcommands)

All logic was split from the former single-file scrape_igf.py; the
original command line keeps working via the scrape_igf.py shim at the
project root.
"""

__version__ = "2.0.0"

# Public API (Model layer)
from .models import network, dom, deepcrawl, classify, extract
from .models.classify import run_classify
from .models.extract import run_extract

# Public API (Controller layer)
from .controllers.scraper import (
    step_sessions,
    step_reports,
    step_transcripts,
    step_schedules,
    step_archived_dashboard,
    step_participants,
    _discover_reports,
    _retry_failed_file,
)


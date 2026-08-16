"""IGF data pipeline, organised as MVC.

models/       download engine, DOM parsing, classification, extraction,
              validation, denoising, transcript recovery
views/        console and validation-report rendering
controllers/  scrape steps, pipeline, validation, LLM experiments
cli.py        argparse entry point (legacy flags + debug subcommands)
"""

__version__ = "2.0.0"

# public API
from .models import network, dom, deepcrawl, classify, extract
from .models.classify import run_classify
from .models.extract import run_extract

# controller API
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


"""Compatibility shim -> igf_pipeline package (MVC refactor).

The former single-file scraper was split, logic-preserving, into:
    igf_pipeline/models/       download engine, DOM, classify, extract, ...
    igf_pipeline/views/        console + report rendering
    igf_pipeline/controllers/  scrape steps, pipeline, validation, LLM
    igf_pipeline/cli.py        argparse entry point

Old command lines keep working unchanged:
    python scrape_igf.py --step sessions --year 2023
    python scrape_igf.py --classify-only --classify-dir igf_full_xxx
    python scrape_igf.py --retry-failed igf_full_xxx/failed_urls.tsv
    python -c "import scrape_igf; scrape_igf.run_extract('igf_classified_xxx','igf_extracted_xxx',5)"

The original single-file source is backed up at
_tools/legacy_backup/scrape_igf.py.bak
"""
from igf_pipeline import *  # noqa: F401,F403
from igf_pipeline.cli import main

if __name__ == "__main__":
    main()


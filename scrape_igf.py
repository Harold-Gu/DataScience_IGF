"""Backwards-compatible entry point for the igf_pipeline package.

The former single-file scraper was split into igf_pipeline/ (MVC layout);
the old command lines keep working unchanged, e.g.:

    python scrape_igf.py --step sessions --year 2023
    python scrape_igf.py --classify-only --classify-dir igf_full_xxx
    python scrape_igf.py --retry-failed igf_full_xxx/failed_urls.tsv
"""
from igf_pipeline import *  # noqa: F401,F403
from igf_pipeline.cli import main

if __name__ == "__main__":
    main()


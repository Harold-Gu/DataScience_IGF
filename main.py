"""IGF pipeline entry point.

    python main.py scrape --years 2017-2019 --limit 20 --workers 3
    python main.py probe --url https://intgovforum.org/en/content/igf-2023-workshops
    python main.py --help
"""
from igf_pipeline.cli import main

if __name__ == "__main__":
    main()


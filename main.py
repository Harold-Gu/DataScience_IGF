"""Unified entry point for the IGF pipeline.

    python main.py scrape   --years 2017-2019 --limit 20 --workers 3
    python main.py classify --classify-dir igf_full_xxx
    python main.py extract  --classify-dir igf_classified_xxx
    python main.py validate --full igf_full_xxx
    python main.py denoise | recover | analyze
    python main.py probe --url https://intgovforum.org/en/content/igf-2023-workshops
    python main.py selftest
    python main.py llm-bench --models qwen3.5:9b --methods fewshot

Full help:  python main.py --help
"""
from igf_pipeline.cli import main

if __name__ == "__main__":
    main()


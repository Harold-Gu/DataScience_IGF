import os as _os
import sys as _sys

__version__ = "2.0.0"

# add a local .venv site-packages when present (dev convenience, skipped otherwise)
_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
for _site in (_os.path.join(_root, ".venv", "Lib", "site-packages"),
              _os.path.join(_root, ".venv", "lib",
                            "python%d.%d" % _sys.version_info[:2], "site-packages")):
    if _os.path.isdir(_site) and _site not in _sys.path:
        _sys.path.append(_site)

from . import crawl, pipeline, process
from .process import run_classify, run_extract
from .pipeline import (
    _discover_reports,
    _retry_failed_file,
    step_archived_dashboard,
    step_participants,
    step_reports,
    step_schedules,
    step_sessions,
    step_transcripts,
)

# module aliases kept for the pre-merge public API
network = dom = deepcrawl = crawl
classify = extract = process

__all__ = [
    "crawl", "pipeline", "process", "network", "dom", "deepcrawl",
    "classify", "extract", "run_classify", "run_extract",
    "_discover_reports", "_retry_failed_file",
    "step_archived_dashboard", "step_participants", "step_reports",
    "step_schedules", "step_sessions", "step_transcripts",
]

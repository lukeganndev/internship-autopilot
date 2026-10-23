"""Entry point for Windows Task Scheduler (run with pythonw, so there is no console window).

Usage: pythonw -m iap.task source
Output goes to <data>/logs/<command>.log.
"""
import sys
import traceback
from datetime import datetime

from . import config
from .cli import app

if __name__ == "__main__":
    config.LOGS.mkdir(parents=True, exist_ok=True)
    log = open(config.LOGS / f"{sys.argv[1] if len(sys.argv) > 1 else 'task'}.log", "a", encoding="utf-8")
    sys.stdout = sys.stderr = log
    print(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} {' '.join(sys.argv[1:])}")
    try:
        app(sys.argv[1:], standalone_mode=False)
    except Exception:
        traceback.print_exc()
    log.close()

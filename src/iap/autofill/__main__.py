"""`python -m iap.autofill <job_id>`: what the dashboard's Launch autofill button starts.
Output goes to <data>/logs/autofill.log."""
import sys
import traceback
from datetime import datetime

from .. import config
from .runner import run

if __name__ == "__main__":
    config.LOGS.mkdir(parents=True, exist_ok=True)
    with open(config.LOGS / "autofill.log", "a", encoding="utf-8") as log:
        sys.stdout = sys.stderr = log
        print(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} job {sys.argv[1]}")
        try:
            result = run(int(sys.argv[1]))
            print(f"filled {result['filled']}, needs you: {result['needs']}, new questions: {result['new_questions']}, "
                  f"refused clicks: {result['guarded']}, applied: {result['applied']}")
        except Exception:
            traceback.print_exc()

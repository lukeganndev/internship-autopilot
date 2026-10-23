import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "examples"

# Minimal .env support (KEY=value lines); real environment variables win.
if (REPO / ".env").exists():
    for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
        k, sep, v = line.partition("=")
        if sep and k.strip() and not k.lstrip().startswith("#") and v.strip():
            os.environ.setdefault(k.strip(), v.strip())

# Everything machine-local lives in ~/.internship-autopilot: data/, venv/, bin/ (tectonic).
# Not in the repo, not in OneDrive (corrupts open SQLite/Chromium files), and not in AppData
# (packaged Windows apps get a private, redirected copy of AppData, so tools would disagree).
HOME = Path.home() / ".internship-autopilot"
DATA = Path(os.environ.get("IAP_DATA_DIR") or HOME / "data")
PROFILE = DATA / "profile.yaml"
ANSWERS = DATA / "answers.yaml"
OUT = DATA / "out"
DB = DATA / "app.db"
LOGS = DATA / "logs"

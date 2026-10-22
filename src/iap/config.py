import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "examples"

# Personal data stays outside the repo (and outside OneDrive, which corrupts open SQLite/Chromium files).
DATA = Path(
    os.environ.get("IAP_DATA_DIR")
    or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "internship-autopilot"
)
PROFILE = DATA / "profile.yaml"
ANSWERS = DATA / "answers.yaml"
OUT = DATA / "out"

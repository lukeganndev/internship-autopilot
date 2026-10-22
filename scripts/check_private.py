"""Pre-commit guard: block personal data and secrets from being committed.

Checks staged files for:
  - paths that only ever hold personal data (profile/answers YAML, databases, .env, resumes, browser profile)
  - secret-looking strings (API keys, tokens, private keys)
  - your real phone, emails and street address, read from your local answers.yaml,
    plus any extra lines in a gitignored `.private-terms` file
Usage: python scripts/check_private.py [files...]   (no args = all staged files)
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

BLOCKED_PATH = re.compile(
    r"(^|/)(data/|out/|browser-profile/|\.env$|profile\.ya?ml$|answers\.ya?ml$)"
    r"|\.(db|sqlite3?|bak)$"
    r"|(client_secret|credentials|token)[^/]*\.json$"
    r"|^(?!docs/|examples/|tests/).*\.pdf$"
)
SECRETS = {
    "private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "AWS access key": r"AKIA[0-9A-Z]{16}",
    "GitHub token": r"\bgh[pousr]_[A-Za-z0-9]{36,}",
    "Anthropic key": r"sk-ant-[A-Za-z0-9_-]{20,}",
    "OpenAI-style key": r"\bsk-[A-Za-z0-9]{32,}",
    "Google OAuth client secret": r"GOCSPX-[A-Za-z0-9_-]{20,}",
    "Google API key": r"AIza[0-9A-Za-z_-]{35}",
    "OAuth refresh token": r"\"refresh_token\"\s*:\s*\"[^\"]{10,}",
}


def personal_patterns(answers: dict, extra: list[str]) -> dict[str, str]:
    pats = {}
    digits = re.sub(r"\D", "", str(answers.get("phone", "")))[-10:]
    if len(digits) == 10:
        pats["your phone number"] = r"\D{0,2}".join(digits)
    for key in ("email_personal", "email_school"):
        if answers.get(key):
            pats[f"your {key}"] = re.escape(answers[key])
    if street := (answers.get("address") or {}).get("street"):
        pats["your street address"] = re.escape(street)
    for i, term in enumerate(t.strip() for t in extra):
        if term and not term.startswith("#"):
            pats[f".private-terms line {i + 1}"] = re.escape(term)
    return pats


def problems(path: str, text: str, personal: dict[str, str]) -> list[str]:
    if BLOCKED_PATH.search(path.replace("\\", "/")):
        return [f"{path}: personal-data file type, must not be committed"]
    found = [f"{path}: looks like a {name}" for name, pat in SECRETS.items() if re.search(pat, text)]
    found += [f"{path}: contains {name}" for name, pat in personal.items() if re.search(pat, text, re.I)]
    return found


def load_personal() -> dict[str, str]:
    import yaml
    from iap import config

    answers = yaml.safe_load(config.ANSWERS.read_text(encoding="utf-8")) if config.ANSWERS.exists() else {}
    extra_file = ROOT / ".private-terms"
    extra = extra_file.read_text(encoding="utf-8").splitlines() if extra_file.exists() else []
    return personal_patterns(answers or {}, extra)


def main(files: list[str]) -> int:
    if not files:
        out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                             capture_output=True, text=True, cwd=ROOT).stdout
        files = out.split()
    personal = load_personal()
    errors = []
    for f in files:
        p = ROOT / f
        text = p.read_text(encoding="utf-8", errors="ignore") if p.is_file() else ""
        errors += problems(f, text, personal)
    for e in errors:
        print(f"BLOCKED  {e}", file=sys.stderr)
    if errors:
        print("Commit blocked by scripts/check_private.py. Unstage those files or remove the data.", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

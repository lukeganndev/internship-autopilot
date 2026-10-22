"""Master profile: the single source of truth for every resume and application.

Stored as YAML in the data dir, never in the repo. Every experience/project/activity
item and every bullet gets a stable id so tailored resumes can cite exactly which
profile text they were built from (that is how "never invent" gets enforced later).
"""
import re
import shutil
from pathlib import Path

import typer
import yaml

from . import config

BULLETED = ("experience", "projects", "activities")
LISTS = ("education", *BULLETED, "awards", "certifications")
SKILL_CATEGORIES = ("Languages", "Frameworks & Libraries", "Developer Tools", "Security", "Hardware & Embedded")
EEO_DECLINE = "Decline to self-identify"
BULLET_TIP = "Tip: start each bullet with a verb (Built, Designed, Automated...). Only use numbers that are true."


# ---------- storage ----------

def load_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run `iap init` first.")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def save_yaml(data: dict, path: Path, backup: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=1000), encoding="utf-8")


def load(path: Path | None = None) -> dict:
    return normalize(load_yaml(Path(path or config.PROFILE)))


def save(p: dict, path: Path | None = None, backup: bool = True) -> None:
    save_yaml(normalize(p), Path(path or config.PROFILE), backup)


# ---------- structure ----------

def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "item"


def _unique(base: str, taken: set) -> str:
    cand, n = base, 2
    while cand in taken:
        cand, n = f"{base}-{n}", n + 1
    taken.add(cand)
    return cand


def normalize(p: dict) -> dict:
    """Fill missing sections; give items and bullets ids without changing ids that already exist."""
    for key in LISTS:
        p[key] = p.get(key) or []
    p["contact"] = p.get("contact") or {}
    p["skills"] = p.get("skills") or {}
    p["exclude_terms"] = p.get("exclude_terms") or []
    taken = {i["id"] for s in BULLETED for i in p[s] if i.get("id")}
    for sec in BULLETED:
        for item in p[sec]:
            if not item.get("id"):
                item["id"] = _unique(slug(item.get("name") or item.get("org") or sec), taken)
            bullets = [b if isinstance(b, dict) else {"text": b} for b in item.get("bullets") or []]
            btaken = {b["id"] for b in bullets if b.get("id")}
            n = 1
            for b in bullets:
                if not b.get("id"):
                    while f"{item['id']}.{n}" in btaken:
                        n += 1
                    b["id"] = f"{item['id']}.{n}"
                    btaken.add(b["id"])
            item["bullets"] = bullets
    return p


def _strings(o):
    if isinstance(o, dict):
        for v in o.values():
            yield from _strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v)
    elif isinstance(o, str):
        yield o


def find_excluded(p: dict) -> list[str]:
    """Excluded terms (old businesses etc.) that appear anywhere in resume content."""
    text = "\n".join(_strings({k: v for k, v in p.items() if k != "exclude_terms"})).lower()
    return [t for t in p.get("exclude_terms", []) if t.lower() in text]


def validate(p: dict) -> list[str]:
    errors = []
    if not p.get("name"):
        errors.append("name is missing")
    if not p["contact"].get("email"):
        errors.append("contact.email is missing")
    if not p["education"]:
        errors.append("no education entries")
    ids = [i["id"] for s in BULLETED for i in p[s]]
    ids += [b["id"] for s in BULLETED for i in p[s] for b in i["bullets"]]
    errors += [f"duplicate id: {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    errors += [f"{i['id']}: bullet {b['id']} has no text" for s in BULLETED for i in p[s] for b in i["bullets"] if not b.get("text")]
    errors += [f"excluded term appears in profile: {t}" for t in find_excluded(p)]
    return errors


def summary(p: dict) -> str:
    counts = ", ".join(f"{len(p[k])} {k}" for k in LISTS if p[k])
    skills = sum(len(v) for v in p["skills"].values())
    return f"{p.get('name', '?')}: {counts}, {skills} skills"


# ---------- terminal interview ----------

def ask(label: str, default: str = "") -> str:
    return str(typer.prompt(label, default=default, show_default=bool(default))).strip()


def ask_list(label: str, default=()) -> list[str]:
    raw = ask(f"{label} (comma-separated)", ", ".join(default))
    return [x.strip() for x in raw.split(",") if x.strip()]


def ask_lines(label: str) -> list[str]:
    typer.echo(f"{label}: one per line, blank line to finish")
    lines = []
    while line := input("  - ").strip():
        lines.append(line)
    return lines


def clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in ("", [], {}, None)}


def collect(what: str, fn, first_default: bool = False) -> list[dict]:
    items = []
    while typer.confirm(f"Add {'another' if items else 'a'} {what}?", default=first_default and not items):
        items.append(clean(fn()))
    return items


def ask_education() -> dict:
    return {
        "school": ask("School"),
        "location": ask("School location (City, ST)"),
        "degree": ask("Degree line (e.g. B.S. in Computer Science, Cybersecurity Concentration)"),
        "minors": ask_list("Minors"),
        "gpa": ask("GPA (blank to omit)"),
        "start": ask("Start (e.g. Aug 2025)"),
        "end": ask("Expected graduation (e.g. May 2029)"),
        "coursework": ask_list("Relevant coursework"),
    }


def ask_project() -> dict:
    typer.echo(BULLET_TIP)
    return {
        "name": ask("Project name"),
        "tech": ask_list("Technologies used"),
        "link": ask("Link (GitHub/demo, blank if none)"),
        "start": ask("Start (e.g. Jan 2026, blank ok)"),
        "end": ask("End (e.g. Mar 2026 or Present, blank ok)"),
        "bullets": ask_lines("What you built, how, and the result"),
    }


def ask_experience() -> dict:
    typer.echo(BULLET_TIP)
    return {
        "role": ask("Role / title"),
        "org": ask("Organization"),
        "location": ask("Location (City, ST or Remote)"),
        "start": ask("Start (e.g. May 2026)"),
        "end": ask("End (e.g. Aug 2026 or Present)"),
        "bullets": ask_lines("What you did and the result"),
    }


def ask_activity() -> dict:
    return {
        "role": ask("Role (e.g. Member, Officer)"),
        "org": ask("Club / organization"),
        "location": ask("Location (blank ok)"),
        "start": ask("Start (e.g. Aug 2025)"),
        "end": ask("End (e.g. Present)"),
        "bullets": ask_lines("What you did (blank line to skip)"),
    }


def ask_award() -> dict:
    return {"name": ask("Award / honor"), "issuer": ask("Issuer (blank ok)"), "date": ask("Date (e.g. Dec 2025)")}


def ask_certification() -> dict:
    return {"name": ask("Certification"), "issuer": ask("Issuer"), "date": ask("Date earned (or 'Expected May 2027')")}


def ask_skills(existing: dict) -> dict:
    typer.echo("Skills: list only what you could talk about in an interview.")
    skills = dict(existing)
    for cat in dict.fromkeys([*SKILL_CATEGORIES, *existing]):
        skills[cat] = ask_list(cat, existing.get(cat, []))
    while typer.confirm("Add another skill category?", default=False):
        skills[ask("Category name")] = ask_list("Skills")
    return {k: v for k, v in skills.items() if v}


def interview() -> dict:
    typer.secho("\nMaster profile interview. Everything is saved locally and can be edited later.\n", bold=True)
    p = {
        "name": ask("Full name as it should appear on your resume"),
        "contact": clean({
            "email": ask("Email for your resume"),
            "phone": ask("Phone"),
            "github": ask("GitHub URL (blank if none)"),
            "linkedin": ask("LinkedIn URL (blank if none)"),
            "portfolio": ask("Portfolio URL (blank if none)"),
        }),
    }
    p["education"] = collect("school", ask_education, first_default=True)
    p["projects"] = collect("technical project", ask_project, first_default=True)
    p["experience"] = collect("work / research / TA experience", ask_experience)
    p["skills"] = ask_skills({})
    p["activities"] = collect("club or leadership role", ask_activity)
    p["awards"] = collect("award or honor", ask_award)
    p["certifications"] = collect("certification", ask_certification)
    p["exclude_terms"] = ask_list("Names that must NEVER appear on a resume (e.g. old businesses)")
    return normalize(p)


def ask_answers(p: dict) -> dict:
    """Standard answers reused on every application form."""
    typer.secho("\nStandard application answers (asked once, reused for autofill).\n", bold=True)
    ed = p["education"][0] if p["education"] else {}
    c = p["contact"]
    first, _, last = p.get("name", "").partition(" ")
    return {
        "legal_first_name": ask("Legal first name", first),
        "legal_last_name": ask("Legal last name", last),
        "preferred_name": ask("Preferred first name", first),
        "school": ed.get("school", ""),
        "degree": ed.get("degree", ""),
        "gpa": ask("GPA as reported on applications", ed.get("gpa", "")),
        "graduation": ask("Graduation date", ed.get("end", "")),
        "class_standing": ask("Current class standing", "Sophomore"),
        "earliest_start": ask("Earliest internship start (e.g. May 2027)"),
        "us_citizen": typer.confirm("US citizen?", default=True),
        "authorized_to_work_us": typer.confirm("Legally authorized to work in the US?", default=True),
        "needs_sponsorship": typer.confirm("Will you now or in the future need visa sponsorship?", default=False),
        "willing_to_relocate": typer.confirm("Willing to relocate for an internship?", default=True),
        "address": {
            "street": ask("Street address"),
            "city": ask("City"),
            "state": ask("State"),
            "zip": ask("ZIP"),
            "country": ask("Country", "United States"),
        },
        "phone": ask("Phone", c.get("phone", "")),
        "email_personal": ask("Personal email (Gmail)", c.get("email", "")),
        "email_school": ask("School email"),
        "links": clean({k: c.get(k, "") for k in ("github", "linkedin", "portfolio")}),
        "how_did_you_hear": ask("Default answer for 'How did you hear about us?'", "Company careers website"),
        "eeo": {
            "gender": ask("EEO gender", EEO_DECLINE),
            "race_ethnicity": ask("EEO race/ethnicity", EEO_DECLINE),
            "hispanic_latino": ask("EEO Hispanic/Latino", EEO_DECLINE),
            "veteran": ask("EEO veteran status", "I am not a protected veteran"),
            "disability": ask("EEO disability status", EEO_DECLINE),
        },
    }

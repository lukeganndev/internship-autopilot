"""Phase 7b: people worth contacting about an application, from public sources only.

Source: the company's public GitHub organization and its *public* members (name, bio, profile URL).
LinkedIn is never fetched: you get search links to open yourself. Drafts are short, built only from
your profile, and never sent: you copy and send them.
"""
import os
import re
from urllib.parse import quote_plus

import httpx

from . import db
from .sources.common import UA

GITHUB = "https://api.github.com"


def _client() -> httpx.Client:
    h = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if token := os.environ.get("GITHUB_TOKEN"):  # optional, raises the 60 requests/hour anonymous limit
        h["Authorization"] = f"Bearer {token}"
    return httpx.Client(timeout=20, headers=h)


def github_org(c, company: str) -> dict | None:
    """The company's GitHub org, only if its login or display name actually matches the company."""
    cn = db.norm_company(company)
    compact = cn.replace(" ", "")
    items = c.get(f"{GITHUB}/search/users", params={"q": f"{company} type:org", "per_page": 5}).raise_for_status().json()
    for item in items.get("items", []):
        org = c.get(f"{GITHUB}/users/{item['login']}").raise_for_status().json()
        names = {db.norm_company(org.get("name") or ""), db.norm_company(item["login"])}
        if cn in names or compact in {n.replace(" ", "") for n in names}:
            return org
    return None


def github_people(c, org: dict, limit: int = 6) -> list[dict]:
    members = c.get(f"{GITHUB}/orgs/{org['login']}/public_members", params={"per_page": 30}).raise_for_status().json()
    people = []
    for m in members[: limit * 2]:
        u = c.get(f"{GITHUB}/users/{m['login']}").raise_for_status().json()
        people.append({"name": u.get("name") or u["login"], "role": " ".join((u.get("bio") or "").split())[:160],
                       "source_url": u["html_url"]})
        if len(people) >= limit:
            break
    return people


def draft(p: dict, job, person: dict, project: dict | None, applied: bool = True) -> str:
    """A short, plain note built only from profile facts. Under ~600 characters."""
    first = (person.get("name") or "").split()[0] if person.get("name") else "there"
    school = p["education"][0]["school"] if p["education"] else "my university"
    me = p["name"].split()[0]
    verb = "recently applied for" if applied else "am applying for"  # don't claim an application that hasn't happened
    lines = [f"Hi {first},", "",
             f"I'm {p['name']}, a computer science student at {school}. I {verb} the {job['title']} role at "
             f"{job['company']} and saw you're part of {job['company']}'s GitHub organization."]
    if project and project.get("bullets"):
        b = project["bullets"][0]["text"].rstrip(".")
        lines[-1] += f" Most recently, on {project['name']}, I {b[0].lower() + b[1:]}."
    lines[-1] += " If you have a few minutes, I'd really appreciate any advice on what the team looks for in interns."
    return "\n".join([*lines, "", "Thanks,", me])


def search_links(job, school: str = "") -> list[tuple[str, str]]:
    """Links you open yourself. Nothing here is fetched by the program."""
    co = job["company"]
    alumni = [(f"LinkedIn: {school} alumni there", f"https://www.linkedin.com/search/results/people/?keywords={quote_plus(co + ' ' + school)}")] if school else []
    return [
        ("LinkedIn: university recruiters", f"https://www.linkedin.com/search/results/people/?keywords={quote_plus(co + ' university recruiter')}"),
        *alumni,
        ("Web: campus recruiting page", f"https://www.google.com/search?q={quote_plus(chr(34) + co + chr(34) + ' university recruiting internships team')}"),
    ]


def suggest(con, job_id: int, profile: dict, spec: dict | None = None) -> dict:
    job = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    status = (con.execute("SELECT status FROM applications WHERE job_id=?", (job_id,)).fetchone() or [None])[0]
    applied = status in ("applied", "oa", "interview", "offer", "rejected")
    projects = (spec or profile)["projects"]
    project = projects[0] if projects else None
    try:
        with _client() as c:
            org = github_org(c, job["company"])
            people = github_people(c, org) if org else []
    except httpx.HTTPStatusError as e:
        limited = e.response.status_code in (403, 429)
        return {"contacts": [], "error": "GitHub rate limit reached; try again in an hour (or set GITHUB_TOKEN in .env)."
                if limited else f"GitHub error {e.response.status_code}"}
    for person in people:
        con.execute("""INSERT INTO contacts(job_id, name, role, source_url, draft) VALUES (?,?,?,?,?)
                       ON CONFLICT(job_id, source_url) DO UPDATE SET name=excluded.name, role=excluded.role, draft=excluded.draft""",
                    (job_id, person["name"], person["role"], person["source_url"], draft(profile, job, person, project, applied)))
    con.commit()
    note = None if org else f"No public GitHub organization found for {job['company']}. Use the search links."
    return {"contacts": people, "org": org["html_url"] if org else None, "error": note}

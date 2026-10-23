import json
import os
import shutil
from enum import Enum
from pathlib import Path

import typer
import yaml

from . import config, db, profile as prof, resume

app = typer.Typer(no_args_is_help=True, help="Internship Autopilot: source, score, tailor, autofill. Never auto-submits.")
profile_app = typer.Typer(no_args_is_help=True, help="View and update your master profile.")
app.add_typer(profile_app, name="profile")


def _report(p: dict) -> None:
    typer.echo(prof.summary(p))
    for e in prof.validate(p):
        typer.secho(f"  ! {e}", fg="yellow")


@app.command()
def init():
    """First run: interview for your master profile and standard application answers."""
    if config.PROFILE.exists() and not typer.confirm(f"A profile already exists at {config.PROFILE}. Redo the whole interview?", default=False):
        typer.echo("Use `iap profile add ...` or `iap profile edit` instead.")
        raise typer.Exit()
    p = prof.interview()
    prof.save(p)
    prof.save_yaml(prof.ask_answers(p), config.ANSWERS)
    typer.secho(f"\nSaved {config.PROFILE}\nSaved {config.ANSWERS}", fg="green")
    _report(p)
    if typer.confirm("Build your resume PDF now?", default=True):
        build_resume(profile_path=None, open_pdf=True)


class Kind(str, Enum):
    project = "project"
    experience = "experience"
    activity = "activity"
    award = "award"
    certification = "certification"
    education = "education"
    skill = "skill"
    course = "course"


ADDERS = {
    Kind.project: ("projects", prof.ask_project),
    Kind.experience: ("experience", prof.ask_experience),
    Kind.activity: ("activities", prof.ask_activity),
    Kind.award: ("awards", prof.ask_award),
    Kind.certification: ("certifications", prof.ask_certification),
    Kind.education: ("education", prof.ask_education),
}


@profile_app.command()
def add(kind: Kind):
    """Add one item (project, experience, skill, course, ...) without redoing the interview."""
    p = prof.load()
    if kind is Kind.skill:
        p["skills"] = prof.ask_skills(p["skills"])
    elif kind is Kind.course:
        if not p["education"]:
            raise typer.BadParameter("Add an education entry first.")
        p["education"][0].setdefault("coursework", []).extend(prof.ask_list("Courses to add"))
    else:
        section, ask = ADDERS[kind]
        p[section].append(prof.clean(ask()))
    prof.save(p)
    _report(p)


@profile_app.command()
def edit(answers: bool = typer.Option(False, "--answers", help="Edit standard application answers instead.")):
    """Open the profile (or answers) YAML in your editor, then validate it."""
    path = config.ANSWERS if answers else config.PROFILE
    if not path.exists():
        raise typer.BadParameter(f"{path} not found. Run `iap init` first.")
    shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    typer.edit(filename=str(path))
    typer.prompt("Press Enter after you've saved the file", default="", show_default=False)
    try:
        data = prof.load_yaml(path)
    except yaml.YAMLError as e:
        typer.secho(f"YAML error, fix it and re-run (backup at {path}.bak):\n{e}", fg="red")
        raise typer.Exit(1)
    if not answers:
        prof.save(data, backup=False)
        _report(prof.load())


@profile_app.command()
def show():
    """Summary of the profile plus any validation problems."""
    typer.echo(f"Profile: {config.PROFILE}\nAnswers: {config.ANSWERS}")
    _report(prof.load())


@app.command()
def source(no_boards: bool = typer.Option(False, "--no-boards", help="Only read the GitHub lists (fast).")):
    """Fetch new internships from all sources, filter, dedupe, store. Toasts on dream companies."""
    from .sources import run

    stats = run.run(use_boards=False if no_boards else None)
    typer.echo(json.dumps(stats, indent=2))


@app.command()
def dashboard(port: int = typer.Option(8765), open_browser: bool = typer.Option(True, "--open/--no-open")):
    """Open the local dashboard (http://127.0.0.1:8765). Ctrl+C to stop."""
    import threading
    import webbrowser

    import uvicorn

    url = f"http://127.0.0.1:{port}"
    typer.echo(f"Dashboard at {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(1.2, webbrowser.open, [url]).start()
    uvicorn.run("iap.web.app:app", host="127.0.0.1", port=port, log_level="warning")


@app.command("tailor")
def tailor_cmd(
    job: int = typer.Option(None, "--job", help="Tailor one job now (any status)."),
    limit: int = typer.Option(None, "-n", help="Max jobs this batch (default: setting tailor_batch)."),
):
    """Tailor resumes + drafts for approved jobs with Claude Code (runs twice a day on a schedule)."""
    from . import tailor

    result = tailor.run(limit=limit, job_id=job)
    for t in result["tailored"]:
        typer.secho(f"job {t['job']}: ATS {t['ats']}%, {t['pages']} page(s), {t['questions']} question(s) -> {t['pdf']}", fg="green")
    for f in result["failed"]:
        typer.secho(f"job {f['job']}: {f['error']}", fg="red")
    if not result["tailored"] and not result["failed"]:
        typer.echo("Nothing to tailor. Approve jobs in the dashboard first.")


@app.command("score")
def score_jobs():
    """Score every eligible job against your profile (also runs after each `iap source`)."""
    from . import match

    typer.echo(match.score_all(db.connect(), prof.load()))


@app.command("queue")
def show_queue(limit: int = typer.Option(30, "-n")):
    """Jobs above your threshold (plus all dream companies), best first."""
    from . import match

    con = db.connect()
    rows = match.queue(con, limit)
    for r in rows:
        star = "*" if r["is_dream"] else " "
        typer.echo(f"{star}{r['score']:>3}  {r['id']:>6}  {r['company'][:22]:<22}  {r['title'][:54]:<54}  {r['location'][:22]}")
    typer.echo(f"\nThreshold {match.threshold(con)}. * = dream company. Details: iap why <id>")


@app.command()
def why(job_id: int):
    """Show a job's score breakdown and missing skills."""
    r = db.connect().execute(
        "SELECT j.*, s.score, s.reasons, s.missing FROM jobs j LEFT JOIN scores s ON s.job_id=j.id WHERE j.id=?", (job_id,)
    ).fetchone()
    if not r:
        raise typer.BadParameter(f"no job {job_id}")
    typer.secho(f"{r['company']} — {r['title']}", bold=True)
    typer.echo(f"{r['location']}  |  {r['season'] or 'season not stated'}  |  {r['url']}")
    if r["excluded_reason"]:
        typer.secho(f"Filtered out: {r['excluded_reason']}", fg="yellow")
    if r["score"] is None:
        typer.echo("Not scored yet (run `iap score`).")
        return
    typer.secho(f"\nScore {r['score']}/100", bold=True)
    for line in json.loads(r["reasons"]):
        typer.echo(f"  {line}")
    if missing := json.loads(r["missing"]):
        typer.secho(f"  Not in your profile: {', '.join(missing)}", fg="yellow")


@app.command()
def settings(key: str = typer.Argument(None), value: str = typer.Argument(None)):
    """Show settings, or set one: `iap settings threshold strict` (strict | balanced | broad | 0-100)."""
    con = db.connect()
    if key is None:
        for k in db.DEFAULT_SETTINGS:
            typer.echo(f"{k}: {json.dumps(db.get_setting(con, k))}")
        return
    if key not in db.DEFAULT_SETTINGS:
        raise typer.BadParameter(f"unknown setting {key}; one of {', '.join(db.DEFAULT_SETTINGS)}")
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        parsed = value
    if key == "threshold" and not (parsed in ("strict", "balanced", "broad") or (isinstance(parsed, int) and 0 <= parsed <= 100)):
        raise typer.BadParameter("threshold must be strict, balanced, broad, or a number 0-100")
    db.set_setting(con, key, parsed)
    typer.echo(f"{key} = {json.dumps(parsed)}")


@app.command()
def refilter():
    """Re-apply filters and the dream-company watchlist to all stored jobs."""
    from .sources import run

    typer.echo(run.refilter(db.connect()))


@app.command()
def jobs(
    limit: int = typer.Option(30, "-n"),
    all_jobs: bool = typer.Option(False, "--all", help="Include filtered-out jobs with the reason."),
    dream: bool = typer.Option(False, "--dream", help="Only dream-company jobs."),
    search: str = typer.Option("", "-s", help="Filter company/title by text."),
):
    """List stored jobs, newest first."""
    con = db.connect()
    where = ["active=1"]
    where += [] if all_jobs else ["excluded_reason IS NULL"]
    where += ["is_dream=1"] if dream else []
    args = []
    if search:
        where.append("(company LIKE ? OR title LIKE ?)")
        args += [f"%{search}%"] * 2
    rows = con.execute(
        f"SELECT * FROM jobs WHERE {' AND '.join(where)} ORDER BY first_seen DESC, posted_at DESC LIMIT ?", (*args, limit)
    ).fetchall()
    for r in rows:
        star = "*" if r["is_dream"] else " "
        note = r["excluded_reason"] or ", ".join(json.loads(r["flags"]))
        typer.echo(f"{star}{r['id']:>6}  {r['company'][:22]:<22}  {r['title'][:52]:<52}  {r['location'][:24]:<24}  {note}")
    total = con.execute("SELECT count(*) FROM jobs WHERE active=1 AND excluded_reason IS NULL").fetchone()[0]
    typer.echo(f"\n{len(rows)} shown, {total} eligible active jobs total. * = dream company")


@app.command("add")
def add_job(url: str):
    """Import one listing by URL. LinkedIn/Handshake/Indeed pages are never fetched: you paste the details."""
    from .sources import run
    from .sources.common import Job

    info = {}
    if run.no_fetch(url):
        typer.secho("This site bans automated access, so it won't be fetched. If the posting links to the company's "
                    "own application page, run `iap add` with that URL instead.", fg="yellow")
    else:
        try:
            info = run.fetch_listing(url)
        except Exception as e:
            typer.secho(f"Couldn't read the page ({e}); enter the details by hand.", fg="yellow")
    company = prof.ask("Company", info.get("company", ""))
    title = prof.ask("Title", info.get("title", ""))
    location = prof.ask("Location", info.get("location", ""))
    description = info.get("description", "")
    if not description:
        description = "\n".join(prof.ask_lines("Paste the job description"))
    jid, is_new, warning = run.import_job(db.connect(), Job("manual", company, title, url, location=location, description=description))
    typer.secho(f"{'Added' if is_new else 'Already tracked as'} job {jid}: {company} — {title}", fg="green")
    if warning:
        typer.secho(f"Heads up: the filters would normally skip this ({warning}). Kept because you added it.", fg="yellow")


@app.command("resume")
def build_resume(
    profile_path: Path = typer.Option(None, "--profile", help="Profile YAML (default: your master profile)."),
    open_pdf: bool = typer.Option(True, "--open/--no-open"),
):
    """Build the master one-page resume PDF."""
    p = prof.load(profile_path)
    if errors := prof.validate(p):
        for e in errors:
            typer.secho(f"  ! {e}", fg="red")
        raise typer.Exit(1)
    pdf, pages = resume.build(p, config.OUT / "master")
    typer.secho(f"Built {pdf}", fg="green")
    if pages > 1:
        typer.secho(f"Resume is {pages} pages. Trim it with `iap profile edit`; tailored resumes auto-select to one page.", fg="yellow")
    if open_pdf and os.name == "nt":
        os.startfile(pdf)

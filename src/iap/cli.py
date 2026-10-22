import os
import shutil
from enum import Enum
from pathlib import Path

import typer
import yaml

from . import config, profile as prof, resume

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
        build_resume()


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

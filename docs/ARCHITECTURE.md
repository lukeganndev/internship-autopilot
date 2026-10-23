# Architecture

Internship Autopilot is a local-first pipeline: find internships, score them, tailor a resume,
fill the application, and track what happens. Everything runs on one Windows PC. The only network
calls are to public job APIs, GitHub's public API, your own inboxes (read-only), and Claude Code
through your subscription.

## Pipeline

```
                ┌──────────────────── Task Scheduler: every 2 h + 2 min after logon ────────────────────┐
                │                                                                                        │
 GitHub lists ──┤  sources/lists.py  ─┐                                                                  │
 (listings.json,│                     ├─► discover boards ─► sources/ats.py (Greenhouse, Lever, Ashby,    │
  README tables)│                     │                      Workday, SmartRecruiters, Oracle, Amazon,  │
                │                     │                      Microsoft)                                  │
                │                     ▼                                                                  │
                │          dedupe on ATS job id ─► filters.py (hard rules) ─► SQLite ─► match.py (0-100) │
                │                                                         │                              │
                │                                        dream company? ──┴─► Windows toast              │
                │          reminders.py (deadlines ≤ 3 days) ─► toast    mail.py (Gmail/Outlook) ─► status│
                └────────────────────────────────────────────────────────────────────────────────────────┘

 Dashboard (FastAPI + HTMX, 127.0.0.1:8765)
   Queue ── Approve ──► Task Scheduler 9 AM / 6 PM: tailor.py ──► claude -p (locked down) ──► proposal
                                                        │
                                verify against profile ─┴─► render one page (tectonic) ─► ATS check
   Review: PDF, gaps, verifier notes, edit bullets, approve answers / cover letter
   Launch autofill ──► autofill/runner.py: visible Chromium, fills, STOPS before submit
   Tracker · Email (status changes + undo) · Outreach (public GitHub + drafts) · Settings
```

## Modules

| Module | Job |
|---|---|
| `cli.py` | `iap` commands (Typer). `task.py` is the windowless entry point for Task Scheduler. |
| `config.py` | Paths. Everything machine-local lives in `~/.internship-autopilot/`. |
| `profile.py` | Master profile YAML: interview, add/edit, ids for every item and bullet, validation. |
| `resume.py` + `templates/` | Jinja2 → Jake's Resume LaTeX → tectonic → PDF. Cover letter PDF. |
| `sources/` | `lists.py` (GitHub lists), `ats.py` (8 job APIs: list + detail), `common.py` (URL → ATS key, HTTP, HTML → text), `run.py` (one sourcing pass). |
| `filters.py` | Hard eligibility rules, flags, deadline extraction, dream-company matching. |
| `match.py` | Rule-based 0-100 fit score with a reason per point; queue query. |
| `tailor.py` + `prompts/tailor.md` | Headless Claude Code call, the never-invent verifier, one-page fitting, ATS check, application questions. |
| `autofill/` | `page.js` (field discovery in the page), `fields.py` (field → saved answer, option matching), `runner.py` (browser loop, click guard, pauses). |
| `mail.py` + `vault.py` | Email classification and matching, forward-only status changes, Gmail/Graph connectors, secrets in Credential Manager. |
| `outreach.py` | Public GitHub org members + drafts; search links you open yourself. |
| `reminders.py` | Due-soon list and once-a-day deadline toasts. |
| `web/` | Dashboard: queue, job detail/review, tracker, email log, settings. |

## Data location

Everything machine-local lives in `~\.internship-autopilot\`:

```
data/      profile.yaml  answers.yaml  app.db  out/ (PDFs)  logs/  browser-profile/  tailor-work/
venv/      Python environment (.venv in the repo is a junction to it)
browsers/  Playwright's Chromium
bin/       tectonic.exe
```

It is deliberately outside the repo, outside OneDrive (cloud sync locks venv files, corrupts open
SQLite and Chromium files, and would upload saved logins), and outside `AppData` (packaged Windows
apps such as the Claude desktop app see a private, redirected copy of `AppData`, so a file written
there by one tool can be invisible to another). OAuth tokens are in Windows Credential Manager.

## Database (SQLite, `schema.sql`)

| Table | Holds |
|---|---|
| `jobs` | One row per unique posting: company, title, location, description, season, deadline, exclusion reason, flags, dream flag. |
| `job_sources` | Every (key, source) a job was seen under; keys look like `greenhouse:stripe:123` or `url:...`. |
| `boards` | Company boards discovered from the lists, with failure counts (gone boards retire after 3 misses). |
| `scores` | 0-100 score, reasons, missing skills per eligible job. |
| `applications` | Your decisions and progress: approved → tailored → applied → oa → interview → offer / rejected / skipped. |
| `tailored` | The verified resume spec, PDF path, ATS score and keywords, gaps, verifier notes, cover letter. |
| `answers` | Application questions with Claude's draft and your approved final text. |
| `emails`, `status_events` | Emails already classified, and every automatic status change with its evidence (undoable). |
| `contacts` | Outreach suggestions and drafts. |
| `runs`, `settings` | Run log; threshold, watchlist, model, batch size and other settings (JSON). |

Columns added after a database exists are applied by a small migration list in `db.connect()`.

## Key decisions

**Dedupe on ATS identity, not text.** The same posting shows up in SimplifyJobs, speedyapply and on
the company's own board. URLs are parsed into `ats:account:job_id` keys, so all of them collapse to
one row; URL-only listings fall back to company + title.

**Discover boards from the lists.** Instead of a hand-kept company list, every Greenhouse, Lever,
Ashby, Workday, SmartRecruiters and Oracle board that posted an internship to a list in the last
~13 months is polled directly (about 2,450). New postings show up before the lists catch them.
Runs have time budgets, dream-company boards go first, and boards cut off by the budget rotate to
the front next time. Rate limits are per host, and per data center for Workday.

**Rule-based scoring.** Free to run on all ~12k jobs every 2 hours (about 20 s) and every point is
explainable in the dashboard. Thresholds were calibrated on real postings.

**Claude proposes, Python decides.** Tailoring uses one locked-down `claude -p` call per approved
job (no tools, no MCP servers, no user plugins or hooks, no contact info sent). The proposal is
verified against the master profile: unknown items are dropped, and a reworded bullet reverts to
the original if it adds a number, a technology, a claim word or an excluded name. Your own edits in
the dashboard go through the same check.

**Autofill can't submit by construction.** All clicks go through one guard that refuses submit,
next, continue, review, save, sign-up and create controls; Enter is never pressed. Login, account
creation and CAPTCHA screens pause the run for you. Only empty fields are filled, consent boxes are
never ticked, and free text is only filled from answers you approved.

**Email only moves things forward.** Statuses advance applied → OA → interview → offer, except that a
rejection always applies. Only tracked applications are touched, and every change is logged with
the email that caused it.

## Safety invariants (and where they're enforced)

1. Never submits an application: `autofill/runner.py` (`safe_click`, no Enter).
2. Never creates accounts or touches CAPTCHAs: `autofill/page.js` (`blockers`) + `runner.step`.
3. Never invents resume content: `tailor.check_bullet` / `build_spec`; `profile.find_excluded`.
4. Never automates LinkedIn/Handshake/Indeed/Glassdoor: `sources/run.NO_FETCH_HOSTS`; outreach only builds links.
5. Never sends email or messages: mail is read-only scope; outreach drafts are copy-only.
6. Personal data stays out of git: `scripts/check_private.py` pre-commit hook (paths, secrets, your real phone/email/address).

## Tests

`uv run pytest` runs ~220 tests. They cover URL → key parsing for every ATS, list/table parsing,
filters, scoring, the verifier, one-page fitting and ATS checks (real tectonic), autofill against
fixture forms in a real headless Chromium (including "Submit is never clicked"), email
classification and status rules, reminders, the dashboard routes, and the pre-commit guard.
`scripts/demo.py` builds a fully fake data set and regenerates the README screenshots.

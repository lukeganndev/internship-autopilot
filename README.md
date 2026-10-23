# Internship Autopilot

A local-first internship pipeline for a CS student. It finds postings, scores fit, builds a tailored
one-page resume, and fills in the application form, then **stops at the submit button** so a human
reviews and clicks it.

> Built for Windows. Python 3.12 + Playwright + SQLite. The AI tailoring step runs through
> [Claude Code](https://claude.com/claude-code) (`claude -p`), so no API key is needed.

## Ground rules (enforced in code)

- **Never submits.** Autofill adapters have no submit method. CAPTCHAs, account creation and submit buttons pause for the human.
- **Never invents.** Tailored bullets must cite the id of a real bullet in the master profile; anything that adds new skills or numbers is rejected and shown as a gap instead.
- **No LinkedIn/Handshake bots.** Those sites ban automation. Paste a listing URL to import it instead.
- **Personal data never touches the repo.** It lives in `~\.internship-autopilot\data`, and a pre-commit hook blocks personal-data files, secrets, and your actual phone/email/address.

## Status

| Phase | What | Status |
|---|---|---|
| 1 | Master profile interview + Jake's Resume PDF | ✅ |
| 2 | Job sourcing (GitHub lists, Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Oracle, Amazon, Microsoft) | ✅ |
| 3 | Fit scoring with reasons | ✅ |
| 4 | Claude Code tailoring + ATS check | ⏳ |
| 5 | Playwright autofill (stops before submit) | ⏳ |
| 6 | Local dashboard | ⏳ |
| 7 | Gmail/Outlook status sync + outreach drafts | ⏳ |

## Setup (Windows)

```powershell
winget install astral-sh.uv
# Tectonic (LaTeX engine; fetches packages on first build)
$d="$HOME\.internship-autopilot\bin"; mkdir $d -Force
iwr https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%400.17.0/tectonic-0.17.0-x86_64-pc-windows-msvc.zip -OutFile t.zip
Expand-Archive t.zip $d; rm t.zip

git clone https://github.com/lukeganndev/internship-autopilot && cd internship-autopilot
# If the repo is under OneDrive, keep the venv out of it (OneDrive locks venv files):
#   mkdir $HOME\.internship-autopilot\venv; cmd /c mklink /J .venv $HOME\.internship-autopilot\venv
uv sync
uv run pre-commit install      # personal-data / secrets guard
```

## Usage

```powershell
uv run iap init                      # interview: profile + standard application answers
uv run iap resume                    # build the master one-page PDF
uv run iap profile add project       # also: experience, activity, award, certification, education, skill, course
uv run iap profile edit              # edit the YAML directly (re-validated on save)
uv run iap profile edit --answers    # edit standard application answers
uv run iap profile show

# Demo without personal data:
uv run iap resume --profile examples/sample_profile.yaml

# Job sourcing
uv run iap source                    # one pass over every source (first run takes a few minutes)
uv run iap jobs                      # newest eligible jobs (--all shows filtered ones + why, --dream, -s text)
uv run iap add <url>                 # import a listing you found yourself
.\scripts\install_tasks.ps1          # run `iap source` every 2h + at logon (Task Scheduler)

# Matching (runs automatically after every `iap source`)
uv run iap queue                     # best matches above your threshold, dream companies first
uv run iap why <id>                  # score breakdown + skills the job wants that you don't have
uv run iap settings threshold strict # strict (70) | balanced (60, default) | broad (45) | any 0-100
```

### How jobs are scored

Every eligible job gets 0-100 from four transparent parts, each shown with its reason:
role fit (0-40: SWE, security, AI/ML/data and embedded/hardware score highest), skills (0-30: tech
terms in the description vs. your skills, project tech and coursework; missing ones are listed),
class year (0-20: sophomore-friendly wording up, grad-student-leaning roles down) and season (0-10:
this summer's cycle best, co-ops low), minus 10 when an active clearance is required. It's rule-based
on purpose: free to run on every job every 2 hours, and every point is explainable. Dream companies
always make the queue.

### Where jobs come from

1. **GitHub lists**: SimplifyJobs and vanshb03 (`listings.json`), speedyapply (README tables).
2. **Company boards discovered from those lists**: every Greenhouse, Lever, Ashby, Workday,
   SmartRecruiters and Oracle board that posted an internship in the last ~13 months is polled
   directly through its public JSON API, so new postings show up before the lists catch them.
3. **Amazon and Microsoft** search APIs.
4. **`iap add <url>`** for anything else. LinkedIn, Handshake, Indeed and Glassdoor pages are never
   fetched (their terms ban automation); you paste the details instead.

Every posting is keyed by its ATS id, so the same job from five sources is stored once.
Hard filters drop non-internships, PhD/MBA roles, non-US, unpaid, seasons already under way,
and postings that require graduating before your grad year or senior standing. Sophomore-friendly
wording overrides the class-year rules. Dream-company postings trigger a Windows notification.

## Development

```powershell
uv run pytest
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design.

## Credits

Resume template adapted from [Jake's Resume](https://github.com/jakegut/resume) (MIT).

## License

MIT

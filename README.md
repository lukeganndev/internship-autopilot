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
| 4 | Claude Code tailoring + ATS check | ✅ |
| 5 | Playwright autofill (stops before submit) | ✅ |
| 6 | Local dashboard | 🟡 queue, job details, tracker, settings done; review screen + deadlines come with Phases 4-5 |
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

# Dashboard: click through the queue, see score breakdowns, approve/skip, track applications
uv run iap dashboard                 # opens http://127.0.0.1:8765 (local only)

# Matching (runs automatically after every `iap source`)
uv run iap queue                     # best matches above your threshold, dream companies first
uv run iap why <id>                  # score breakdown + skills the job wants that you don't have
uv run iap settings threshold strict # strict (70) | balanced (60, default) | broad (45) | any 0-100
```

### Tailoring (Phase 4)

```powershell
uv run iap tailor                    # tailor every approved job (scheduled 9 AM + 6 PM)
uv run iap tailor --job <id>         # one job now (also a "Tailor now" button in the dashboard)
# or inside Claude Code:  /tailor-queue
```

Each approved job gets one headless `claude -p` call (your Claude subscription, Sonnet by default,
no API key). The call is locked down: no tools, no MCP servers, no user plugins or hooks, and it
only receives the posting plus your profile's education, items, skills and awards. Contact info,
address and EEO answers never leave your machine.

Claude proposes which items to show and how to word them. `iap/tailor.py` then verifies the
proposal against the master profile before anything is rendered:

- every project, course, skill and award must exist in the profile (unknown ones are dropped);
- every reworded bullet is compared to its source bullet, and reverts to your original wording if
  it adds a number, a technology, a claim word ("led", "deployed", "users", "won"...) or an
  excluded name;
- the result is rendered to one page (trimming the least relevant bullets if needed) and
  ATS-checked: the share of the posting's tech keywords found in the PDF's extracted text.

Gaps (what the posting wants that you don't have) are listed, never added. Cover letters are only
written when the application requires one, and answers to custom questions (pulled from
Greenhouse application forms) are drafts. Both are flagged if they mention numbers or tech not in
your profile, and nothing is used until you approve it in the dashboard. Edits you make to
bullets in the dashboard go through the same verifier.

### Autofill (Phase 5)

```powershell
uv run playwright install chromium   # once (browsers go to ~\.internship-autopilot\browsers)
uv run iap apply <id>                # or "Launch autofill" on a tailored job in the dashboard
```

Opens the application in a visible Chromium window with its own persistent profile (portal logins
like Workday stay saved), fills it, and stops. Greenhouse, Lever and Ashby forms open directly;
on Workday, iCIMS and other portals you click Apply and Next yourself and it fills each step as
it appears. A banner on the page shows what it filled and what needs you (outlined in orange).

What it fills: your saved answers (name, contact, address, school, GPA, graduation, work
authorization, sponsorship, relocation, links, "how did you hear", EEO choices), the tailored
resume PDF, an approved cover letter, and approved answers to custom questions. Dropdowns, ARIA
comboboxes and Workday-style listboxes are matched by meaning ("AL" ↔ "Alabama", "B.S. in ..." ↔
"Bachelor's", "Decline to self-identify" ↔ "I don't wish to answer"). Free-text questions it hasn't
seen are saved so the next tailoring batch drafts them.

Safety, enforced in `autofill/runner.py`:
- **Never submits.** Every click goes through one guard that refuses submit buttons and anything
  labelled submit/apply/next/continue/review/save/sign up/create, and Enter is never pressed.
- **Never creates accounts or touches CAPTCHAs.** Login, account-creation and CAPTCHA screens pause
  the run with a banner and a Windows notification; it continues once you've handled them.
- **Never overwrites** a field that already has a value, never ticks consent boxes, never guesses:
  uncertain fields are left blank and highlighted.
- When the page says your application was received, the job is marked **applied** in the tracker.

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

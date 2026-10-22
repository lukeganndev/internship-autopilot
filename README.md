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
- **Personal data never touches the repo.** It lives in `%LOCALAPPDATA%\internship-autopilot`, and a pre-commit hook blocks personal-data files, secrets, and your actual phone/email/address.

## Status

| Phase | What | Status |
|---|---|---|
| 1 | Master profile interview + Jake's Resume PDF | ✅ |
| 2 | Job sourcing (GitHub lists, Greenhouse, Lever, Ashby, Workday, SmartRecruiters) | ⏳ |
| 3 | Fit scoring with reasons | ⏳ |
| 4 | Claude Code tailoring + ATS check | ⏳ |
| 5 | Playwright autofill (stops before submit) | ⏳ |
| 6 | Local dashboard | ⏳ |
| 7 | Gmail/Outlook status sync + outreach drafts | ⏳ |

## Setup (Windows)

```powershell
winget install astral-sh.uv
# Tectonic (LaTeX engine; fetches packages on first build)
$d="$env:LOCALAPPDATA\Programs\tectonic"; mkdir $d -Force
iwr https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%400.17.0/tectonic-0.17.0-x86_64-pc-windows-msvc.zip -OutFile t.zip
Expand-Archive t.zip $d; rm t.zip

git clone https://github.com/lukeganndev/internship-autopilot && cd internship-autopilot
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
```

## Development

```powershell
uv run pytest
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design.

## Credits

Resume template adapted from [Jake's Resume](https://github.com/jakegut/resume) (MIT).

## License

MIT

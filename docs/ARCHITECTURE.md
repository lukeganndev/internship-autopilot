# Architecture

## Pipeline

```
Task Scheduler ──every 2h + at logon──> iap source → iap score → toast if dream company
                                         │
                       SQLite ◄──────────┘
                         │
Dashboard: approve/skip ─┤
                         ▼
Task Scheduler 9:00 & 18:00 ──> claude -p "/tailor-queue"
     skill: iap tailor export (approved jobs + profile → JSON)
            Claude selects/rewords → JSON
            iap tailor import → VALIDATOR → render .tex → tectonic PDF → ATS score
                         ▼
Dashboard review: edit resume/answers → "Launch autofill" → Playwright fills, STOPS
                         ▼
Human clicks Submit → confirmation detected (or marked applied)
                         ▼
iap mail sync (each source pass) → status updates + change log
```

## Stack

| Piece | Choice |
|---|---|
| Runtime | Python 3.12 via `uv` |
| CLI | Typer (`iap`) |
| Storage | SQLite (stdlib), YAML for profile/answers |
| Resume | Jinja2 → Jake's Resume LaTeX → tectonic → PDF |
| Sourcing | httpx (async) against public JSON endpoints |
| Dashboard | FastAPI + Jinja2 + HTMX (no JS build) |
| Autofill | Playwright, headed Chromium, persistent profile |
| AI | Claude Code skill, headless `claude -p` |
| Secrets | Windows Credential Manager via `keyring`, `.env` |
| Email | Gmail API (read-only), Microsoft Graph (`Mail.Read`) |

## Data location

All personal data lives in `%LOCALAPPDATA%\internship-autopilot` (override with `IAP_DATA_DIR`):

```
profile.yaml   answers.yaml   app.db   out/   browser-profile/   tokens (in Credential Manager)
```

It is deliberately outside the repo **and** outside OneDrive: cloud sync corrupts open SQLite and
Chromium profile files, and would upload saved logins.

## Master profile

`profile.yaml` is the single source of truth. Each experience/project/activity item has an `id`
(slug of its name) and each bullet an id like `packetscope.2`. Ids are assigned once and never
renumbered, so tailored resumes can cite exactly which bullets they came from. `exclude_terms`
lists names that must never appear on a resume; rendering refuses if one shows up.

## Database schema (Phase 2+)

- **jobs**: id, dedupe_key UNIQUE, company, title, location, remote, url, apply_url, ats, description, season, posted_at, first_seen, last_seen, is_dream
- **job_sources**: job_id, source, source_url
- **scores**: job_id, score, reasons_json, flags_json, profile_hash
- **applications**: id, job_id, status, deadline, applied_at, notes
- **tailored**: job_id, tex_path, pdf_path, ats_score, keywords_json, gaps_json, cover_letter
- **answers**: id, job_id, question, draft, final, approved
- **status_events**: application_id, old, new, source, evidence, created_at
- **emails**: account, message_id UNIQUE, sender, subject, received_at, application_id, classification
- **contacts**: application_id, name, role, public_source_url, draft_message
- **settings**: key, value
- **runs**: kind, started, finished, new_jobs, error

Dedupe key: ATS job id when known (`greenhouse:<board>:<id>`), otherwise a hash of normalized
company + title + apply-URL path.

## Safety invariants

1. Autofill adapters expose fill operations only; no code path clicks a submit control.
2. CAPTCHA frames, account-creation forms, and submit buttons pause the run and notify.
3. Tailored output is validated against the profile: every bullet cites a source id, and new numbers or skills not in the profile are rejected.
4. LinkedIn and Handshake are never automated; listings are imported by pasted URL.
5. `scripts/check_private.py` (pre-commit) blocks personal-data paths, secret patterns, and the user's real phone/email/address.

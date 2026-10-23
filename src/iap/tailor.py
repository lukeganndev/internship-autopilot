"""Phase 4: a tailored one-page resume (plus cover letter / answer drafts) for each approved job.

Claude Code (`claude -p`, your subscription, no API key) proposes a selection and rewording. This
module is the gatekeeper: every item, skill and course must exist in the master profile, and every
reworded bullet is checked against its source bullet. A bullet that adds a number, a technology, a
claim word or an excluded name is reverted to the original wording. Cover letters and answers are
drafts with warnings; nothing is used until you approve it in the dashboard.
"""
import copy
import json
import re
import shutil
import subprocess
from pathlib import Path

import httpx
from pypdf import PdfReader

from . import config, db, match, notify, resume
from . import profile as prof
from .sources.common import UA, parse_url

PROMPT_FILE = Path(__file__).parent / "prompts" / "tailor.md"
MAX_DESC = 7000
NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
# Claims a rewording must not introduce unless the source bullet already makes them.
CLAIMS = re.compile(r"\b(led|lead|leading|managed|mentored|supervised|owned|launched|shipped|deployed|production|"
                    r"users|customers|clients|revenue|profit|won|winner|award\w*|first place|patent\w*|published|"
                    r"million|thousands?|hundreds?|percent|%)", re.I)


def _work() -> Path:
    return config.DATA / "tailor-work"


# ---------- what Claude sees ----------

def _item(x: dict) -> dict:
    keep = ("id", "name", "role", "org", "tech", "start", "end")
    return {**{k: x[k] for k in keep if x.get(k)}, "bullets": [{"id": b["id"], "text": b["text"]} for b in x["bullets"]]}


def payload(p: dict, job, questions: list[str], cover_required: bool) -> dict:
    """Only what tailoring needs: no phone, email, address or EEO answers leave the machine."""
    return {
        "posting": {"company": job["company"], "title": job["title"], "location": job["location"],
                    "description": (job["description"] or "")[:MAX_DESC] or "(no description available: tailor from the title)"},
        "cover_letter_required": cover_required,
        "questions": questions,
        "profile": {
            "name": p["name"],
            "education": [{k: e.get(k) for k in ("school", "degree", "minors", "gpa", "start", "end", "coursework")}
                          for e in p["education"]],
            **{sec: [_item(x) for x in p[sec]] for sec in prof.BULLETED},
            "skills": p["skills"], "awards": p["awards"], "certifications": p["certifications"],
        },
    }


def parse_json_object(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"no JSON object in Claude's reply: {text[:200]!r}")
    return json.loads(text[start:end + 1])


def ask_claude(data: dict, model: str) -> dict:
    """One locked-down headless Claude Code call: no tools, no MCP servers, no user plugins or hooks."""
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("Claude Code CLI (`claude`) not found on PATH")
    work = _work()
    work.mkdir(parents=True, exist_ok=True)
    (work / "settings.json").write_text('{"disableAllHooks": true}', encoding="utf-8")
    cmd = [exe, "-p", "--output-format", "json", "--model", model, "--tools", "", "--strict-mcp-config",
           "--setting-sources", "project", "--settings", str(work / "settings.json"),
           "--no-session-persistence", "--system-prompt-file", str(PROMPT_FILE)]
    r = subprocess.run(cmd, input=json.dumps(data, ensure_ascii=False), capture_output=True, text=True,
                       encoding="utf-8", cwd=work, timeout=900)
    if r.returncode:
        raise RuntimeError(f"claude exited {r.returncode}: {(r.stderr or r.stdout)[:400]}")
    out = json.loads(r.stdout)
    if out.get("is_error"):
        raise RuntimeError(f"claude error: {str(out.get('result'))[:400]}")
    return parse_json_object(out["result"])


# ---------- verification ----------

def _nums(s: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in NUM.findall(s)}


def check_bullet(text: str, src: str, item: dict, profile: dict) -> str | None:
    """Why a reworded bullet can't be used, or None if it only says what the source says."""
    if not text:
        return "empty"
    if extra := _nums(text) - _nums(src):
        return f"added the number {', '.join(sorted(extra))}"
    if extra := match.terms_in(text) - match.terms_in(src + "\n" + "\n".join(item.get("tech", []))):
        return f"added {', '.join(sorted(extra))}"
    if extra := {m.lower() for m in CLAIMS.findall(text)} - {m.lower() for m in CLAIMS.findall(src)}:
        return f"added the claim '{sorted(extra)[0]}'"
    if len(text) > max(260, 1.6 * len(src)):
        return "much longer than the original"
    if any(t.lower() in text.lower() for t in profile["exclude_terms"]):
        return "contains an excluded name"
    return None


def build_spec(profile: dict, out: dict) -> tuple[dict, list[str]]:
    """Claude's proposal → a profile-shaped dict the renderer uses. Returns (spec, notes about corrections)."""
    notes = []
    items = {x["id"]: (sec, x) for sec in prof.BULLETED for x in profile[sec]}
    bullets = {b["id"]: (x, b) for sec in prof.BULLETED for x in profile[sec] for b in x["bullets"]}
    spec = copy.deepcopy({k: profile[k] for k in ("name", "contact", "education", "exclude_terms")})

    courses = {c.lower(): c for e in profile["education"] for c in e.get("coursework", [])}
    asked = out.get("coursework") or []
    if chosen := [courses[c.lower()] for c in asked if c.lower() in courses]:
        spec["education"][0]["coursework"] = list(dict.fromkeys(chosen))  # ponytail: selection applies to the first school
    if bad := [c for c in asked if c.lower() not in courses]:
        notes.append(f"Ignored courses not in your profile: {', '.join(bad)}")

    for sec in prof.BULLETED:
        spec[sec] = []
        for i in out.get(sec) or []:
            if i in items and items[i][0] == sec and all(x["id"] != i for x in spec[sec]):
                spec[sec].append({**copy.deepcopy(items[i][1]), "bullets": []})
            else:
                notes.append(f"Ignored unknown {sec} id '{i}'")
    chosen_items = {x["id"]: x for sec in prof.BULLETED for x in spec[sec]}

    for b in out.get("bullets") or []:
        sid, text = b.get("source_id"), " ".join(str(b.get("text", "")).split())
        if sid not in bullets:
            notes.append(f"Ignored a bullet with unknown source '{sid}'")
            continue
        src_item, src = bullets[sid]
        target = chosen_items.get(src_item["id"])
        if target is None or any(x["id"] == sid for x in target["bullets"]):
            continue
        if problem := check_bullet(text, src["text"], src_item, profile):
            notes.append(f"{sid}: kept your original wording (the rewrite {problem})")
            text = src["text"]
        target["bullets"].append({"id": sid, "text": text})
    for x in chosen_items.values():
        if not x["bullets"]:
            x["bullets"] = copy.deepcopy(items[x["id"]][1]["bullets"][:3])

    for sec in ("awards", "certifications"):
        by_name = {a["name"].lower(): a for a in profile[sec]}
        spec[sec] = [copy.deepcopy(by_name[n.lower()]) for n in dict.fromkeys(out.get(sec) or []) if n.lower() in by_name]

    owned = {s.lower(): (cat, s) for cat, ss in profile["skills"].items() for s in ss}
    spec["skills"] = {}
    for cat, ss in (out.get("skills") or {}).items():
        for s in ss:
            if s.lower() in owned:
                home, name = owned[s.lower()]
                group = cat if cat in profile["skills"] else home
                if name not in spec["skills"].setdefault(group, []):
                    spec["skills"][group].append(name)
            else:
                notes.append(f"Ignored skill not in your profile: {s}")
    if not spec["skills"]:
        spec["skills"] = copy.deepcopy(profile["skills"])
    return spec, notes


def free_text_warnings(text: str, profile: dict, job_description: str) -> list[str]:
    """Cover letters/answers are reviewed by you, so problems are flagged rather than auto-fixed."""
    warns = []
    profile_text = json.dumps(profile, ensure_ascii=False)
    if extra := _nums(text) - _nums(profile_text) - _nums(job_description):
        warns.append(f"Mentions numbers not in your profile or the posting: {', '.join(sorted(extra))}")
    if extra := match.terms_in(text) - match.profile_terms(profile):
        warns.append(f"Mentions {', '.join(sorted(extra))}, which isn't in your profile. Fine only if phrased as something you'd learn.")
    if hits := [t for t in profile["exclude_terms"] if t.lower() in text.lower()]:
        warns.append(f"Contains excluded names: {', '.join(hits)}")
    return warns


# ---------- one page + ATS ----------

def fit_one_page(spec: dict, outdir: Path, stem: str) -> tuple[Path, int, list[str]]:
    """Render; while it spills onto page 2, drop the last bullet of the longest item, then whole sections."""
    trimmed = []
    for _ in range(25):
        pdf, pages = resume.build(spec, outdir, stem)
        if pages <= 1:
            return pdf, pages, trimmed
        multi = [x for sec in ("activities", "experience", "projects") for x in spec[sec] if len(x["bullets"]) > 1]
        if multi:
            x = max(reversed(multi), key=lambda x: len(x["bullets"]))
            trimmed.append(f"Trimmed to fit one page: {x['bullets'].pop()['id']}")
        elif spec["certifications"] or spec["awards"]:
            sec = "certifications" if spec["certifications"] else "awards"
            trimmed.append(f"Trimmed to fit one page: {spec[sec].pop()['name']}")
        elif spec["activities"] or len(spec["projects"]) > 1:
            sec = "activities" if spec["activities"] else "projects"
            trimmed.append(f"Trimmed to fit one page: {spec[sec].pop()['id']}")
        else:
            break
    return pdf, pages, trimmed


def ats_check(description: str, pdf: Path) -> tuple[int | None, list[str], list[str]]:
    """Share of the posting's tech keywords that appear in the resume's extracted text (what an ATS reads)."""
    wanted = sorted(match.terms_in(description))
    text = "\n".join(page.extract_text() or "" for page in PdfReader(pdf).pages)
    have = match.terms_in(text)
    matched = [w for w in wanted if w in have]
    missing = [w for w in wanted if w not in have]
    return (round(100 * len(matched) / len(wanted)) if wanted else None), matched, missing


# ---------- application questions ----------

STANDARD_Q = re.compile(r"name|e-?mail|phone|resume|\bcv\b|cover letter|linkedin|website|github|portfolio|\burl\b|address|"
                        r"\bcity\b|\bstate\b|zip|country|location|hear about|refer|salary|compensation|start date|availab|"
                        r"sponsor|authori[sz]|visa|citizen|clearance|\bgpa\b|graduat|school|university|degree|major|"
                        r"pronoun|gender|race|ethnic|veteran|disab|hispanic|privacy|consent|acknowledg|agree|\bage\b", re.I)
LONG_Q = re.compile(r"\bwhy\b|describe|tell us|explain|what (excites|interests|motivates|draws)|share|example of|how would you", re.I)


def application_questions(job) -> tuple[bool, list[tuple[str, bool]]]:
    """(cover letter required, [(free-text question, required)]). Greenhouse publishes its form; other
    portals' questions are discovered by autofill (Phase 5) and added to the answers table."""
    ref = parse_url(job["url"])
    if not ref or ref.ats != "greenhouse":
        return False, []
    url = f"https://boards-api.greenhouse.io/v1/boards/{ref.account}/jobs/{ref.job_id}?questions=true"
    return parse_greenhouse_questions(httpx.get(url, headers={"User-Agent": UA}, timeout=20).raise_for_status().json())


def parse_greenhouse_questions(d: dict) -> tuple[bool, list[tuple[str, bool]]]:
    cover, questions = False, []
    for q in d.get("questions") or []:
        label, fields = " ".join(q.get("label", "").split()), q.get("fields") or []
        names, types = {f.get("name") for f in fields}, {f.get("type") for f in fields}
        if names & {"cover_letter", "cover_letter_text"}:
            cover = cover or bool(q.get("required"))
        elif types & {"textarea", "input_text"} and not STANDARD_Q.search(label) and ("textarea" in types or LONG_Q.search(label)):
            questions.append((label, bool(q.get("required"))))
    return cover, questions


# ---------- batch ----------

def pending(con, limit: int, job_id: int | None = None):
    if job_id:
        return con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchall()
    return con.execute(
        """SELECT j.* FROM applications a JOIN jobs j ON j.id=a.job_id
           WHERE a.status='approved' AND j.id NOT IN (SELECT job_id FROM tailored)
           ORDER BY j.is_dream DESC, a.updated_at LIMIT ?""", (limit,)).fetchall()


def render_and_store(con, job, profile: dict, spec: dict, notes: list[str], gaps: list[str]) -> dict:
    """Shared by the batch and by dashboard edits: fit to one page, ATS-check, save."""
    outdir = config.OUT / "jobs" / str(job["id"])
    pdf, pages, trimmed = fit_one_page(spec, outdir, f"{profile['name'].replace(' ', '_')}_Resume")
    score, matched, missing = ats_check(job["description"] or job["title"], pdf)
    have = match.profile_terms(profile)
    if unshown := [m for m in missing if m in have]:
        notes = [*notes, f"In your profile but not on this resume: {', '.join(unshown)}"]
    gaps = list(dict.fromkeys([*gaps, *(f"{m} (not in your profile)" for m in missing if m not in have)]))
    con.execute(
        """INSERT INTO tailored(job_id, spec, pdf_path, pages, ats_score, ats_matched, ats_missing, gaps, notes)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(job_id) DO UPDATE SET spec=excluded.spec, pdf_path=excluded.pdf_path, pages=excluded.pages,
             ats_score=excluded.ats_score, ats_matched=excluded.ats_matched, ats_missing=excluded.ats_missing,
             gaps=excluded.gaps, notes=excluded.notes, updated_at=datetime('now')""",
        (job["id"], json.dumps(spec), str(pdf), pages, score, json.dumps(matched), json.dumps(missing),
         json.dumps(gaps), json.dumps([*notes, *trimmed])))
    return {"pdf": str(pdf), "pages": pages, "ats": score}


def tailor_job(con, job, profile: dict, model: str) -> dict:
    cover_required, found = application_questions(job)
    con.executemany("INSERT OR IGNORE INTO answers(job_id, question, required) VALUES (?,?,?)",
                    [(job["id"], q, int(req)) for q, req in found])
    questions = [r["question"] for r in con.execute("SELECT question FROM answers WHERE job_id=?", (job["id"],))]

    out = ask_claude(payload(profile, job, questions, cover_required), model)
    spec, notes = build_spec(profile, out)
    result = render_and_store(con, job, profile, spec, notes, [str(g) for g in out.get("gaps") or []])

    desc = job["description"] or ""
    cover = out.get("cover_letter") if cover_required else None
    con.execute("UPDATE tailored SET cover_letter_required=?, cover_letter=?, cover_letter_warnings=? WHERE job_id=?",
                (int(cover_required), cover, json.dumps(free_text_warnings(cover, profile, desc) if cover else []), job["id"]))
    by_q = {" ".join(a.get("question", "").split()).lower(): a for a in out.get("answers") or []}
    for q in questions:
        if a := by_q.get(" ".join(q.split()).lower()):
            text = str(a.get("answer", "")).strip()
            con.execute("UPDATE answers SET draft=?, warnings=? WHERE job_id=? AND question=?",
                        (text, json.dumps(free_text_warnings(text, profile, desc)), job["id"], q))
    con.execute("""INSERT INTO applications(job_id, status) VALUES (?, 'tailored')
                   ON CONFLICT(job_id) DO UPDATE SET status='tailored', updated_at=datetime('now')
                   WHERE applications.status IN ('approved', 'tailored')""", (job["id"],))
    con.commit()
    return {**result, "questions": len(questions), "cover_letter": bool(cover)}


def run(limit: int | None = None, job_id: int | None = None) -> dict:
    con = db.connect()
    profile = prof.load()
    if errors := prof.validate(profile):
        raise ValueError(f"fix your profile first: {'; '.join(errors)}")
    model = db.get_setting(con, "tailor_model")
    jobs = pending(con, limit or db.get_setting(con, "tailor_batch"), job_id)
    done, failed = [], []
    for job in jobs:
        try:
            done.append({"job": job["id"], **tailor_job(con, job, profile, model)})
        except Exception as e:  # one bad job must not stop the batch
            con.rollback()
            failed.append({"job": job["id"], "error": f"{type(e).__name__}: {str(e)[:300]}"})
            con.execute("UPDATE applications SET notes=? WHERE job_id=?", (f"Tailoring failed: {str(e)[:300]}", job["id"]))
            con.commit()
    if done:
        notify.toast("Tailored resumes ready", f"{len(done)} application(s) ready to review in the dashboard")
    return {"tailored": done, "failed": failed}

"""Local dashboard: queue, job details with score breakdown, approve/skip, applications, settings.

Run with `iap dashboard` (binds 127.0.0.1 only). Pages are server-rendered; HTMX swaps the job
detail pane and decision buttons without full reloads.
"""
import json
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import db, match

app = FastAPI(title="Internship Autopilot", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
T = Jinja2Templates(directory=Path(__file__).parent / "templates")
T.env.filters["fromjson"] = json.loads

PAGE = 100
ROLES = ["software", "security", "ai/ml/data", "embedded/hardware", "quant", "it/networking"]
STATUSES = ["approved", "tailored", "skipped", "applied", "oa", "interview", "offer", "rejected"]


def _require_htmx(request: Request) -> None:
    # State changes only via HTMX requests: a custom header can't be sent cross-site without CORS,
    # so another website can't make your browser approve/skip jobs on this localhost server.
    if request.headers.get("HX-Request") != "true":
        raise HTTPException(403, "state-changing requests must come from the dashboard")


def _nav(con) -> dict:
    run = con.execute("SELECT finished, stats FROM runs WHERE kind='source' AND finished IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    return {
        "queued": len(match.queue(con, 100000)),
        "tracked": con.execute("SELECT count(*) FROM applications WHERE status != 'skipped'").fetchone()[0],
        "last_run": run["finished"] if run else None,
        "threshold": match.threshold(con),
    }


@app.get("/", response_class=HTMLResponse)
def queue_page(request: Request, q: str = "", role: str = "", dream: bool = False, min_score: int | None = None, page: int = 0):
    con = db.connect()
    min_score = match.threshold(con) if min_score is None else min_score
    where = ["j.active=1", "j.excluded_reason IS NULL", "a.job_id IS NULL", "(s.score >= ? OR j.is_dream=1)"]
    args: list = [min_score]
    if q:
        where.append("(j.company LIKE ? OR j.title LIKE ? OR j.location LIKE ?)")
        args += [f"%{q}%"] * 3
    if role:
        where.append("json_extract(s.reasons, '$[0]') LIKE ?")
        args.append(f"%role: {role}%")
    if dream:
        where.append("j.is_dream=1")
    sql = f"""FROM jobs j JOIN scores s ON s.job_id=j.id LEFT JOIN applications a ON a.job_id=j.id
              WHERE {' AND '.join(where)}"""
    total = con.execute(f"SELECT count(*) {sql}", args).fetchone()[0]
    rows = con.execute(f"SELECT j.*, s.score {sql} ORDER BY j.is_dream DESC, s.score DESC, j.first_seen DESC LIMIT ? OFFSET ?",
                       (*args, PAGE, page * PAGE)).fetchall()
    return T.TemplateResponse(request, "queue.html", {
        "nav": _nav(con), "rows": rows, "total": total, "page": page, "pages": (total + PAGE - 1) // PAGE,
        "f": {"q": q, "role": role, "dream": dream, "min_score": min_score}, "roles": ROLES,
    })


def _job(con, job_id: int):
    r = con.execute(
        """SELECT j.*, s.score, s.reasons, s.missing, a.status, a.notes AS app_notes FROM jobs j
           LEFT JOIN scores s ON s.job_id=j.id LEFT JOIN applications a ON a.job_id=j.id WHERE j.id=?""", (job_id,)).fetchone()
    if not r:
        raise HTTPException(404, "no such job")
    sources = con.execute("SELECT DISTINCT source FROM job_sources WHERE job_id=?", (job_id,)).fetchall()
    return r, [s["source"] for s in sources]


def _panel(request: Request, con, job_id: int, tpl: str = "_job.html", **extra):
    r, sources = _job(con, job_id)
    from .. import outreach

    t = con.execute("SELECT * FROM tailored WHERE job_id=?", (job_id,)).fetchone()
    answers = con.execute("SELECT * FROM answers WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
    contacts = con.execute("SELECT * FROM contacts WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
    return T.TemplateResponse(request, tpl, {"j": r, "sources": sources, "statuses": STATUSES, "nav": _nav(con),
                                             "t": t, "spec": json.loads(t["spec"]) if t else None, "answers": answers,
                                             "contacts": contacts, "search_links": outreach.search_links(r), **extra})


@app.post("/job/{job_id}/outreach", response_class=HTMLResponse)
def find_people(request: Request, job_id: int):
    _require_htmx(request)
    from .. import outreach, profile as prof

    con = db.connect()
    _job(con, job_id)
    t = con.execute("SELECT spec FROM tailored WHERE job_id=?", (job_id,)).fetchone()
    result = outreach.suggest(con, job_id, prof.load(), json.loads(t["spec"]) if t else None)
    return _panel(request, con, job_id, outreach_note=result.get("error"))


@app.get("/email", response_class=HTMLResponse)
def email_page(request: Request):
    from .. import mail

    con = db.connect()
    events = con.execute(
        """SELECT e.*, j.company, j.title FROM status_events e JOIN jobs j ON j.id=e.job_id
           ORDER BY e.id DESC LIMIT 200""").fetchall()
    unmatched = con.execute(
        """SELECT * FROM emails WHERE classification IS NOT NULL AND job_id IS NULL
           ORDER BY received_at DESC LIMIT 30""").fetchall()
    return T.TemplateResponse(request, "email.html", {
        "nav": _nav(con), "events": events, "unmatched": unmatched, "accounts": mail.accounts(),
        "last_sync": db.get_setting(con, "mail_last_sync")})


@app.post("/email/sync")
def email_sync(request: Request):
    _require_htmx(request)
    from .. import mail

    mail.sync()
    resp = RedirectResponse("/email", status_code=303)
    resp.headers["HX-Redirect"] = "/email"
    return resp


@app.post("/events/{event_id}/undo")
def undo_event(request: Request, event_id: int):
    _require_htmx(request)
    from .. import mail

    mail.undo(db.connect(), event_id)
    resp = RedirectResponse("/email", status_code=303)
    resp.headers["HX-Redirect"] = "/email"
    return resp


@app.get("/job/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: int):
    # HTMX click from a list → just the panel; direct visit/bookmark → full page around it.
    return _panel(request, db.connect(), job_id, "_job.html" if request.headers.get("HX-Request") else "job_page.html")


@app.get("/job/{job_id}/resume.pdf")
def resume_pdf(job_id: int):
    t = db.connect().execute("SELECT pdf_path FROM tailored WHERE job_id=?", (job_id,)).fetchone()
    if not t or not Path(t["pdf_path"]).exists():
        raise HTTPException(404, "not tailored yet")
    return FileResponse(t["pdf_path"], media_type="application/pdf", filename=Path(t["pdf_path"]).name,
                        content_disposition_type="inline")


@app.post("/job/{job_id}/tailor", response_class=HTMLResponse)
def tailor_now(request: Request, job_id: int):
    """Tailor this job immediately (one Claude Code call, ~30-60 s) instead of waiting for the batch."""
    _require_htmx(request)
    from .. import tailor

    con = db.connect()
    _job(con, job_id)
    con.execute("""INSERT INTO applications(job_id, status) VALUES (?, 'approved')
                   ON CONFLICT(job_id) DO UPDATE SET status='approved', updated_at=datetime('now')""", (job_id,))
    con.execute("DELETE FROM tailored WHERE job_id=?", (job_id,))
    con.commit()
    result = tailor.run(job_id=job_id)
    error = result["failed"][0]["error"] if result["failed"] else None
    return _panel(request, db.connect(), job_id, error=error)


@app.post("/job/{job_id}/autofill", response_class=HTMLResponse)
def launch_autofill(request: Request, job_id: int):
    """Start the visible autofill browser as its own process, so the dashboard stays responsive."""
    _require_htmx(request)
    import subprocess
    import sys

    con = db.connect()
    _job(con, job_id)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen([sys.executable, "-m", "iap.autofill", str(job_id)], creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return _panel(request, con, job_id, launched=True)


@app.post("/job/{job_id}/bullets", response_class=HTMLResponse)
async def edit_bullets(request: Request, job_id: int):
    """Your edits go through the same verifier as Claude's, then the PDF is re-rendered."""
    _require_htmx(request)
    from .. import profile as prof, tailor

    form = await request.form()
    con = db.connect()
    job, _ = _job(con, job_id)
    t = con.execute("SELECT * FROM tailored WHERE job_id=?", (job_id,)).fetchone()
    if not t:
        raise HTTPException(404, "not tailored yet")
    profile, spec = prof.load(), json.loads(t["spec"])
    sources = {b["id"]: (x, b) for sec in prof.BULLETED for x in profile[sec] for b in x["bullets"]}
    notes = []
    for sec in prof.BULLETED:
        for item in spec[sec]:
            for b in item["bullets"]:
                new = " ".join(str(form.get(f"b:{b['id']}", b["text"])).split())
                if new == b["text"] or b["id"] not in sources:
                    continue
                src_item, src = sources[b["id"]]
                if problem := tailor.check_bullet(new, src["text"], src_item, profile):
                    notes.append(f"{b['id']}: edit not applied (it {problem}). Change the bullet in your profile first if it's true.")
                else:
                    b["text"] = new
                    notes.append(f"{b['id']}: your edit applied")
    tailor.render_and_store(con, job, profile, spec, notes, json.loads(t["gaps"]))
    con.commit()
    return _panel(request, con, job_id)


@app.post("/job/{job_id}/cover", response_class=HTMLResponse)
def save_cover(request: Request, job_id: int, text: str = Form(""), approve: bool = Form(False)):
    _require_htmx(request)
    from .. import profile as prof, tailor

    con = db.connect()
    job, _ = _job(con, job_id)
    warns = tailor.free_text_warnings(text, prof.load(), job["description"] or "")
    con.execute("UPDATE tailored SET cover_letter=?, cover_letter_warnings=?, cover_letter_approved=? WHERE job_id=?",
                (text, json.dumps(warns), int(approve), job_id))
    con.commit()
    return _panel(request, con, job_id)


@app.post("/job/{job_id}/answer/{answer_id}", response_class=HTMLResponse)
def save_answer(request: Request, job_id: int, answer_id: int, text: str = Form(""), approve: bool = Form(False)):
    _require_htmx(request)
    from .. import profile as prof, tailor

    con = db.connect()
    job, _ = _job(con, job_id)
    warns = tailor.free_text_warnings(text, prof.load(), job["description"] or "")
    con.execute("UPDATE answers SET final=?, approved=?, warnings=? WHERE id=? AND job_id=?",
                (text, int(approve), json.dumps(warns), answer_id, job_id))
    con.commit()
    return _panel(request, con, job_id)


@app.post("/job/{job_id}/status", response_class=HTMLResponse)
def set_status(request: Request, job_id: int, status: str = Form(...)):
    _require_htmx(request)
    con = db.connect()
    _job(con, job_id)
    if status == "queue":
        con.execute("DELETE FROM applications WHERE job_id=?", (job_id,))
    elif status in STATUSES:
        con.execute(
            """INSERT INTO applications(job_id, status, applied_at) VALUES (?, ?, CASE WHEN ?='applied' THEN datetime('now') END)
               ON CONFLICT(job_id) DO UPDATE SET status=excluded.status, updated_at=datetime('now'),
                 applied_at=COALESCE(applications.applied_at, excluded.applied_at)""", (job_id, status, status))
    else:
        raise HTTPException(400, "unknown status")
    con.commit()
    resp = _panel(request, con, job_id)
    resp.headers["HX-Trigger"] = json.dumps({"jobDecided": {"id": job_id, "status": status}})
    return resp


@app.get("/applications", response_class=HTMLResponse)
def applications_page(request: Request, status: str = ""):
    con = db.connect()
    where, args = ("WHERE a.status=?", [status]) if status else ("WHERE a.status != 'skipped'", [])
    rows = con.execute(
        f"""SELECT j.*, s.score, a.status, a.updated_at FROM applications a JOIN jobs j ON j.id=a.job_id
            LEFT JOIN scores s ON s.job_id=j.id {where} ORDER BY a.updated_at DESC""", args).fetchall()
    counts = dict(con.execute("SELECT status, count(*) FROM applications GROUP BY status").fetchall())
    return T.TemplateResponse(request, "applications.html", {
        "nav": _nav(con), "rows": rows, "status": status, "statuses": STATUSES, "counts": counts})


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: str = ""):
    con = db.connect()
    return T.TemplateResponse(request, "settings.html", {
        "nav": _nav(con), "threshold": db.get_setting(con, "threshold"), "presets": match.THRESHOLDS,
        "watchlist": "\n".join(db.get_setting(con, "watchlist")), "saved": saved})


@app.post("/settings")
def save_settings(request: Request, threshold: str = Form(...), watchlist: str = Form("")):
    _require_htmx(request)
    con = db.connect()
    value = threshold if threshold in match.THRESHOLDS else int(threshold)
    if not isinstance(value, str) and not 0 <= value <= 100:
        raise HTTPException(400, "threshold must be 0-100")
    db.set_setting(con, "threshold", value)
    new_watch = [w.strip() for w in watchlist.splitlines() if w.strip()]
    if new_watch != db.get_setting(con, "watchlist"):
        db.set_setting(con, "watchlist", new_watch)
        from ..sources import run
        run.refilter(con)  # re-mark dream companies
    resp = RedirectResponse("/settings?saved=1", status_code=303)
    resp.headers["HX-Redirect"] = "/settings?saved=1"
    return resp

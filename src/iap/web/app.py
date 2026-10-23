"""Local dashboard: queue, job details with score breakdown, approve/skip, applications, settings.

Run with `iap dashboard` (binds 127.0.0.1 only). Pages are server-rendered; HTMX swaps the job
detail pane and decision buttons without full reloads.
"""
import json
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import db, match

app = FastAPI(title="Internship Autopilot", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
T = Jinja2Templates(directory=Path(__file__).parent / "templates")
T.env.filters["fromjson"] = json.loads

PAGE = 100
ROLES = ["software", "security", "ai/ml/data", "embedded/hardware", "quant", "it/networking"]
STATUSES = ["approved", "skipped", "applied", "oa", "interview", "offer", "rejected"]


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
        """SELECT j.*, s.score, s.reasons, s.missing, a.status FROM jobs j LEFT JOIN scores s ON s.job_id=j.id
           LEFT JOIN applications a ON a.job_id=j.id WHERE j.id=?""", (job_id,)).fetchone()
    if not r:
        raise HTTPException(404, "no such job")
    sources = con.execute("SELECT DISTINCT source FROM job_sources WHERE job_id=?", (job_id,)).fetchall()
    return r, [s["source"] for s in sources]


@app.get("/job/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: int):
    con = db.connect()
    r, sources = _job(con, job_id)
    # HTMX click from a list → just the panel; direct visit/bookmark → full page around it.
    tpl = "_job.html" if request.headers.get("HX-Request") else "job_page.html"
    return T.TemplateResponse(request, tpl, {"j": r, "sources": sources, "statuses": STATUSES, "nav": _nav(con)})


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
    r, sources = _job(con, job_id)
    resp = T.TemplateResponse(request, "_job.html", {"j": r, "sources": sources, "statuses": STATUSES, "nav": _nav(con)})
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

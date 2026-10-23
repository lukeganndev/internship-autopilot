"""One sourcing pass: lists → discover boards → poll boards → enrich new jobs → filter → store → notify."""
import asyncio
import json
import re
from datetime import date, timedelta

from .. import db, filters, match, notify, reminders
from .. import profile as prof
from . import ats, lists
from .common import Job, Ref, client, html_to_text, is_gone, parse_url

FIXED_BOARDS = [("amazon", "Amazon"), ("microsoft", "Microsoft")]
BOARD_ATS = {"greenhouse", "lever", "ashby", "workday", "smartrecruiters", "oracle"}
DISCOVERY_WINDOW_DAYS = 400  # only poll boards that posted an internship to a list within this window


def grad_year(con) -> int | None:
    if year := db.get_setting(con, "grad_year"):
        return int(year)
    try:
        ed = prof.load()["education"]
    except FileNotFoundError:
        return None
    m = re.search(r"20\d\d", str(ed[0].get("end", ""))) if ed else None
    return int(m.group()) if m else None


def register_boards(con, list_jobs: list[Job]) -> None:
    cutoff = (date.today() - timedelta(days=DISCOVERY_WINDOW_DAYS)).isoformat()
    for j in list_jobs:
        if (ref := parse_url(j.url)) and ref.ats in BOARD_ATS and (j.posted_at or cutoff) >= cutoff:
            db.add_board(con, ref, j.company)
    for name, company in FIXED_BOARDS:
        db.add_board(con, Ref(name, name, ""), company)


# A run must finish well inside the 2-hour cadence. Work cut off by a budget is picked up next run.
BOARD_BUDGET_S = 8 * 60
DETAIL_BUDGET_S = 5 * 60
BACKFILL_LIMIT = 400  # stored jobs still missing a description, enriched per run


async def _bounded(coros, budget: float) -> list:
    """Run concurrently; each slot is the result, the exception, or None if the budget cut it off."""
    tasks = [asyncio.ensure_future(c) for c in coros]
    if not tasks:
        return []
    _, pending = await asyncio.wait(tasks, timeout=budget)
    for t in pending:
        t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    return [None if t in pending else (t.exception() or t.result()) for t in tasks]


async def _poll_boards(c, boards, watch) -> tuple[list[Job], list[tuple[str, bool]], list[str]]:
    # Dream-company boards first, then the ones polled longest ago, so a budget cutoff rotates
    # instead of always skipping the same boards. Semaphores hand out slots in arrival order.
    boards = sorted(boards, key=lambda b: (not filters.is_dream(b["company"], watch), b["last_ok"] or ""))
    results = await _bounded((ats.list_board(c, b) for b in boards), BOARD_BUDGET_S)
    jobs, status, errors = [], [], []
    for b, r in zip(boards, results):
        if r is None:
            continue  # not reached this run; no penalty
        if not isinstance(r, Exception):
            status.append((b["key"], True))
            jobs += r
            continue
        if is_gone(r):  # only "this board doesn't exist" counts toward retiring it; rate limits don't
            status.append((b["key"], False))
        errors.append(f"{b['key']}: {type(r).__name__} {str(r)[:120]}")
    return jobs, status, errors


async def _enrich(c, jobs: list[Job]) -> int:
    async def one(j):
        if not (ref := parse_url(j.url)):
            return False
        for k, v in (await ats.details(c, ref, j.url)).items():
            if v and not getattr(j, k):
                setattr(j, k, v)
        return True

    return sum(r is True for r in await _bounded((one(j) for j in jobs), DETAIL_BUDGET_S))


async def _collect(con, use_boards: bool, watch):
    ats.reset_cache()
    async with client() as c:
        list_jobs, errors = await lists.fetch_lists(c)
        register_boards(con, list_jobs)
        con.commit()
        board_jobs, status = [], []
        if use_boards:
            board_jobs, status, board_errors = await _poll_boards(c, db.boards(con), watch)
            errors += board_errors
        for key, ok in status:
            db.board_result(con, key, ok)

        known = db.known_keys(con)
        db.touch(con, [j.key for j in list_jobs if not j.active and j.key in known], active=False)
        candidates = [j for j in board_jobs + list_jobs if j.active]
        db.touch(con, {j.key for j in candidates if j.key in known})
        relocated = db.update_locations(con, [(j.key, j.location) for j in board_jobs if j.key in known and j.location
                                              and not re.fullmatch(r"\d+ locations?", j.location, re.I)])

        groups: dict[str, list[Job]] = {}
        for j in candidates:
            if j.key not in known and filters.is_internship(j.title) and filters.any_us(j.location):
                groups.setdefault(j.key, []).append(j)
        # Prefer the copy with a description (boards) over list stubs; keep the rest as aliases.
        new = [sorted(g, key=lambda j: -len(j.description)) for g in groups.values()]
        # Dream companies get their details first, in case the budget runs out.
        need = sorted((g[0] for g in new if not g[0].description), key=lambda j: not filters.is_dream(j.company, watch))
        enriched = await _enrich(c, need)
    return new, enriched, errors, len(board_jobs), len(list_jobs), relocated


def _verdict(job: Job, gy, watch):
    job.season = job.season or filters.season_of(job.title, job.description)
    job.deadline = job.deadline or filters.deadline_in(job.description)
    return filters.exclusion(job, gy), filters.flags(job), filters.is_dream(job.company, watch)


async def _backfill(con, gy, watch) -> int:
    """Fetch descriptions for stored jobs that don't have one yet, then re-check their filters."""
    rows = con.execute(
        """SELECT * FROM jobs WHERE description='' AND active=1 AND excluded_reason IS NULL AND ats != 'url'
           ORDER BY is_dream DESC, first_seen DESC LIMIT ?""", (BACKFILL_LIMIT,)).fetchall()
    jobs = [Job("backfill", r["company"], r["title"], r["url"], location=r["location"], season=r["season"],
                key=r["key"], deadline=r["deadline"]) for r in rows]
    ats.reset_cache()
    async with client() as c:
        done = await _enrich(c, jobs)
    for r, j in zip(rows, jobs):
        if j.description:
            reason, flags, _ = _verdict(j, gy, watch)
            con.execute("UPDATE jobs SET description=?, location=?, season=?, deadline=?, excluded_reason=?, flags=? WHERE id=?",
                        (j.description, j.location, j.season, j.deadline, reason, json.dumps(flags), r["id"]))
    return done


def refilter(con, job_ids: list[int] | None = None) -> dict:
    """Re-apply filters, flags and the watchlist to stored jobs (all, or just `job_ids`).
    Manually added jobs keep being eligible, as on import."""
    gy, watch = grad_year(con), db.get_setting(con, "watchlist")
    changed = 0
    rows = (con.execute(f"SELECT * FROM jobs WHERE id IN ({','.join('?' * len(job_ids))})", job_ids).fetchall()
            if job_ids else con.execute("SELECT * FROM jobs").fetchall())
    for r in rows:
        manual = con.execute("SELECT 1 FROM job_sources WHERE job_id=? AND source='manual'", (r["id"],)).fetchone()
        j = Job("refilter", r["company"], r["title"], r["url"], location=r["location"],
                description=r["description"], season=r["season"], key=r["key"], deadline=r["deadline"])
        reason, flags, dream = _verdict(j, gy, watch)
        new = (None if manual else reason, json.dumps(flags), int(dream), j.deadline)
        if new != (r["excluded_reason"], r["flags"], r["is_dream"], r["deadline"]):
            con.execute("UPDATE jobs SET excluded_reason=?, flags=?, is_dream=?, deadline=? WHERE id=?", (*new, r["id"]))
            changed += 1
    con.commit()
    return {"changed": changed}


def run(con=None, use_boards: bool | None = None) -> dict:
    con = con or db.connect()
    run_id = con.execute("INSERT INTO runs(kind) VALUES ('source')").lastrowid
    con.commit()
    use_boards = db.get_setting(con, "direct_boards") if use_boards is None else use_boards
    gy, watch = grad_year(con), db.get_setting(con, "watchlist")
    new, enriched, errors, n_board, n_list, relocated = asyncio.run(_collect(con, use_boards, watch))
    if relocated:  # boards are authoritative: a job whose real office turns out to be abroad gets re-filtered
        refilter(con, relocated)

    inserted, eligible, dream_hits = 0, 0, []
    for job, *aliases in new:
        reason, flags, dream = _verdict(job, gy, watch)
        _, is_new = db.upsert_job(con, job, aliases, excluded=reason, flags=flags, dream=dream)
        inserted += is_new
        eligible += is_new and reason is None
        if is_new and dream and reason is None:
            dream_hits.append(job)
    con.commit()
    _notify_dream(dream_hits)  # before backfill: speed matters most here

    backfilled = asyncio.run(_backfill(con, gy, watch))
    con.execute("UPDATE jobs SET active=0 WHERE last_seen < datetime('now', '-30 days')")
    stats = {"list_jobs": n_list, "board_jobs": n_board, "new": inserted, "new_eligible": eligible,
             "enriched": enriched, "backfilled": backfilled, "dream": len(dream_hits),
             "errors": len(errors), "error_sample": errors[:15]}
    try:
        stats["scoring"] = match.score_all(con, prof.load())
    except FileNotFoundError:
        stats["scoring"] = "skipped: no profile yet (run `iap init`)"
    # Deadlines pass between fetches; take those jobs out, then remind about what's close.
    con.execute("""UPDATE jobs SET excluded_reason='deadline passed (' || deadline || ')'
                   WHERE deadline != '' AND deadline < date('now', 'localtime') AND excluded_reason IS NULL""")
    stats["reminders"] = reminders.remind(con)
    con.execute("UPDATE runs SET finished=datetime('now'), stats=? WHERE id=?", (json.dumps(stats), run_id))
    con.commit()
    return stats


# Sites whose terms ban automated access. `iap add` never fetches these; the user pastes the details.
NO_FETCH_HOSTS = ("linkedin.com", "joinhandshake.com", "handshake.com", "indeed.com", "glassdoor.com")


def no_fetch(url: str) -> bool:
    host = re.sub(r"^https?://", "", url).split("/")[0].lower()
    return any(host == h or host.endswith("." + h) for h in NO_FETCH_HOSTS)


async def _fetch_listing(url: str) -> dict:
    async with client() as c:
        if ref := parse_url(url):
            info = await ats.details(c, ref, url)
            info.setdefault("company", "")
            info["company"] = info["company"] or ref.account.replace("-", " ").title()
            return info
        r = await c.get(url, headers={"Accept": "text/html"})
        r.raise_for_status()
        page = r.text
        title = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', page) or re.search(r"<title>(.*?)</title>", page, re.S)
        body = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", "", page)
        return {"title": html_to_text(title.group(1)) if title else "", "description": html_to_text(body)[:20000]}


def fetch_listing(url: str) -> dict:
    """Title/company/description for a pasted URL. Never called for NO_FETCH_HOSTS."""
    if no_fetch(url):
        raise ValueError("refusing to fetch a no-automation site")
    return asyncio.run(_fetch_listing(url))


def import_job(con, job: Job) -> tuple[int, bool, str | None]:
    """Store a manually added job. Never auto-excluded (you chose it); returns the filter verdict as a warning."""
    job.season = job.season or filters.season_of(job.title, job.description)
    reason = filters.exclusion(job, grad_year(con))
    jid, is_new = db.upsert_job(con, job, flags=filters.flags(job),
                                dream=filters.is_dream(job.company, db.get_setting(con, "watchlist")))
    con.commit()
    return jid, is_new, reason


def _notify_dream(dream_hits: list[Job]) -> None:
    if len(dream_hits) == 1:
        j = dream_hits[0]
        notify.toast(f"Dream company posted: {j.company}", j.title, j.url)
    elif dream_hits:
        names = ", ".join(sorted({j.company for j in dream_hits}))[:180]
        notify.toast(f"{len(dream_hits)} new dream-company internships", names)

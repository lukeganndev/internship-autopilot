"""SQLite store: jobs deduped across sources, board registry, run log, settings."""
import json
import re
import sqlite3
from pathlib import Path

from . import config

DEFAULT_SETTINGS = {
    "direct_boards": True,  # poll every company board discovered from the GitHub lists
    "grad_year": None,      # None = read from profile education
    "threshold": "balanced",  # strict (70) | balanced (60) | broad (45) | any number 0-100
    "tailor_model": "sonnet",  # model alias for `claude -p`; sonnet keeps subscription usage low
    "tailor_batch": 10,        # max jobs tailored per batch (runs twice a day)
    "mail_last_sync": None,    # ISO timestamp of the last Gmail/Outlook scan
    # "|" separates aliases. Matched as whole words against the company name.
    "watchlist": [
        "Google", "Microsoft", "Amazon|AWS", "Meta", "Apple", "NVIDIA",
        "CrowdStrike", "Palo Alto Networks", "Cloudflare", "Fortinet", "Zscaler",
        "Lockheed Martin",
        "Raymond James", "Jabil", "ReliaQuest", "KnowBe4", "Citi|Citigroup|Citibank",
        "JPMorgan|JP Morgan|J.P. Morgan|JPMorganChase|JPMorgan Chase|JP Morgan Chase",
    ],
}


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(path or config.DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript((Path(__file__).parent / "schema.sql").read_text(encoding="utf-8"))
    return con


def get_setting(con, key: str):
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else DEFAULT_SETTINGS[key]


def set_setting(con, key: str, value) -> None:
    con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, json.dumps(value)))
    con.commit()


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def norm_company(s: str) -> str:
    return " ".join(re.sub(r"\b(inc|llc|ltd|corp|corporation|co|company|the|plc)\b", "", norm(s)).split())


def known_keys(con) -> set[str]:
    return {r[0] for r in con.execute("SELECT key FROM job_sources")}


def touch(con, keys, active: bool = True) -> None:
    con.executemany(
        "UPDATE jobs SET last_seen=datetime('now'), active=? WHERE id=(SELECT job_id FROM job_sources WHERE key=? LIMIT 1)",
        [(int(active), k) for k in keys],
    )


def update_locations(con, pairs: list[tuple[str, str]]) -> list[int]:
    """Set the location of already-stored jobs from board data; returns ids whose location changed."""
    changed = []
    for key, loc in pairs:
        row = con.execute("SELECT j.id, j.location FROM jobs j JOIN job_sources s ON s.job_id=j.id WHERE s.key=? LIMIT 1",
                          (key,)).fetchone()
        if row and row["location"] != loc:
            con.execute("UPDATE jobs SET location=? WHERE id=?", (loc, row["id"]))
            changed.append(row["id"])
    return changed


def upsert_job(con, job, aliases=(), excluded=None, flags=(), dream=False) -> tuple[int, bool]:
    """Insert a job or merge it into an existing one. Returns (job id, is_new).

    Exact match on any known key first. Jobs whose key is only a URL (no ATS id) also merge on
    normalized company + title, so a list entry pointing at a custom careers page joins the
    ATS posting for the same role.
    """
    keys = [job.key, *(a.key for a in aliases)]
    cn, tn = norm_company(job.company), norm(job.title)
    row = con.execute(f"SELECT job_id FROM job_sources WHERE key IN ({','.join('?' * len(keys))})", keys).fetchone()
    if not row:
        row = con.execute(
            "SELECT id AS job_id FROM jobs WHERE company_norm=? AND title_norm=? AND (key LIKE 'url:%' OR ?) LIMIT 1",
            (cn, tn, job.key.startswith("url:")),
        ).fetchone()
    is_new = row is None
    if is_new:
        jid = con.execute(
            """INSERT INTO jobs(key, company, company_norm, title, title_norm, location, url, ats, description,
                                salary, season, posted_at, excluded_reason, flags, is_dream)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (job.key, job.company, cn, job.title, tn, job.location, job.url, job.key.split(":")[0],
             job.description, job.salary, job.season, job.posted_at, excluded, json.dumps(list(flags)), int(dream)),
        ).lastrowid
    else:
        jid = row["job_id"]
        con.execute(
            """UPDATE jobs SET last_seen=datetime('now'), active=1,
                 description=CASE WHEN description='' THEN ? ELSE description END,
                 location=CASE WHEN location='' THEN ? ELSE location END,
                 salary=CASE WHEN salary='' THEN ? ELSE salary END,
                 season=CASE WHEN season='' THEN ? ELSE season END,
                 posted_at=CASE WHEN posted_at='' THEN ? ELSE posted_at END
               WHERE id=?""",
            (job.description, job.location, job.salary, job.season, job.posted_at, jid),
        )
    con.executemany(
        "INSERT OR IGNORE INTO job_sources(key, job_id, source, url) VALUES (?,?,?,?)",
        [(j.key, jid, j.source, j.url) for j in (job, *aliases)],
    )
    return jid, is_new


def add_board(con, ref, company: str) -> None:
    con.execute(
        "INSERT OR IGNORE INTO boards(key, ats, account, host, site, company) VALUES (?,?,?,?,?,?)",
        (ref.board_key, ref.ats, ref.account, ref.host, ref.site, company),
    )


def boards(con, max_fails: int = 3) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM boards WHERE fails < ?", (max_fails,)).fetchall()


def board_result(con, key: str, ok: bool) -> None:
    if ok:
        con.execute("UPDATE boards SET fails=0, last_ok=datetime('now') WHERE key=?", (key,))
    else:
        con.execute("UPDATE boards SET fails=fails+1 WHERE key=?", (key,))

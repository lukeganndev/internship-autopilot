"""Deadline reminders: what's due soon among jobs you still mean to apply to."""
from datetime import date, timedelta

from . import db, match, notify

PENDING = ("approved", "tailored")


def due(con, days: int = 7, today: date | None = None) -> list:
    """Jobs with a deadline in the next `days` days that you haven't applied to: approved/tailored ones,
    plus queue jobs (no decision yet) that are dream companies or above your threshold."""
    today = today or date.today()
    return con.execute(
        f"""SELECT j.*, a.status, s.score FROM jobs j
            LEFT JOIN applications a ON a.job_id=j.id LEFT JOIN scores s ON s.job_id=j.id
            WHERE j.deadline != '' AND j.deadline BETWEEN ? AND ? AND j.active=1 AND j.excluded_reason IS NULL
              AND (a.status IN ({','.join('?' * len(PENDING))})
                   OR (a.job_id IS NULL AND (j.is_dream=1 OR s.score >= ?)))
            ORDER BY j.deadline, a.status IS NULL, s.score DESC""",
        (today.isoformat(), (today + timedelta(days=days)).isoformat(), *PENDING, match.threshold(con))).fetchall()


def remind(con, today: date | None = None) -> int:
    """One Windows notification per job per day for approved/tailored jobs due within 3 days."""
    today = today or date.today()
    seen = db.get_setting(con, "reminded")
    soon = [r for r in due(con, 3, today) if r["status"] in PENDING and seen.get(str(r["id"])) != today.isoformat()]
    for r in soon[:3]:
        left = (date.fromisoformat(r["deadline"]) - today).days
        when = "today" if left == 0 else "tomorrow" if left == 1 else f"in {left} days"
        notify.toast(f"Deadline {when}: {r['company']}", r["title"])
    if len(soon) > 3:
        notify.toast("More deadlines soon", f"{len(soon) - 3} more applications are due within 3 days")
    if soon:
        db.set_setting(con, "reminded", {**seen, **{str(r["id"]): today.isoformat() for r in soon}})
    return len(soon)

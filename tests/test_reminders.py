import sqlite3
from datetime import date

import pytest

from iap import config, db, filters, reminders
from iap.sources.common import Job

TODAY = date(2026, 10, 24)


@pytest.mark.parametrize("text, deadline", [
    ("Applications close November 15, 2026. Apply early!", "2026-11-15"),
    ("Deadline: Dec. 1st, 2026", "2026-12-01"),
    ("Please apply by 11/30/2026 to be considered.", "2026-11-30"),
    ("Applications accepted until 2027-01-10.", "2027-01-10"),
    ("Sept 30, 2026 is the posting closes date", ""),          # a date with no deadline wording
    ("We were founded on March 3, 2010.", ""),
])
def test_deadline_in(text, deadline):
    assert filters.deadline_in(text) == deadline


def test_passed_deadline_is_excluded():
    j = Job("t", "Acme", "Software Engineering Intern", "https://a.com/1", location="Austin, TX", deadline="2026-10-01")
    assert filters.exclusion(j, 2029, TODAY) == "deadline passed (2026-10-01)"
    j.deadline = "2026-11-01"
    assert filters.exclusion(j, 2029, TODAY) is None


def test_migration_adds_deadline_to_an_old_database(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL, company TEXT NOT NULL, "
                "company_norm TEXT NOT NULL, title TEXT NOT NULL, title_norm TEXT NOT NULL, location TEXT NOT NULL DEFAULT '', "
                "url TEXT NOT NULL, ats TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', salary TEXT NOT NULL DEFAULT '', "
                "season TEXT NOT NULL DEFAULT '', posted_at TEXT NOT NULL DEFAULT '', first_seen TEXT NOT NULL DEFAULT (datetime('now')), "
                "last_seen TEXT NOT NULL DEFAULT (datetime('now')), active INTEGER NOT NULL DEFAULT 1, excluded_reason TEXT, "
                "flags TEXT NOT NULL DEFAULT '[]', is_dream INTEGER NOT NULL DEFAULT 0)")
    old.execute("INSERT INTO jobs(key, company, company_norm, title, title_norm, url) VALUES ('k', 'A', 'a', 'T', 't', 'u')")
    old.commit()
    old.close()
    con = db.connect(path)
    assert con.execute("SELECT deadline FROM jobs").fetchone()[0] == ""


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB", tmp_path / "t.db")
    c = db.connect()
    rows = [("approved_soon", "2026-10-26", "approved", False), ("tailored_today", "2026-10-24", "tailored", False),
            ("applied_soon", "2026-10-25", "applied", False), ("approved_later", "2026-11-20", "approved", False),
            ("dream_queue", "2026-10-28", None, True), ("plain_queue", "2026-10-28", None, False)]
    ids = {}
    for key, deadline, status, dream in rows:
        ids[key], _ = db.upsert_job(c, Job("t", key, "Intern", f"https://x.com/{key}", deadline=deadline), dream=dream)
        if status:
            c.execute("INSERT INTO applications(job_id, status) VALUES (?, ?)", (ids[key], status))
    c.commit()
    return c, ids


def test_due_soon_covers_pending_and_dream_queue_jobs(con):
    c, ids = con
    got = [r["company"] for r in reminders.due(c, 7, TODAY)]
    assert got == ["tailored_today", "approved_soon", "dream_queue"]  # applied, later and unscored queue jobs left out


def test_remind_once_per_job_per_day(con, monkeypatch):
    c, _ = con
    shown = []
    monkeypatch.setattr(reminders.notify, "toast", lambda title, body, url=None: shown.append(title))
    assert reminders.remind(c, TODAY) == 2
    assert shown == ["Deadline today: tailored_today", "Deadline in 2 days: approved_soon"]
    assert reminders.remind(c, TODAY) == 0                                # same day: quiet
    assert reminders.remind(c, date(2026, 10, 25)) == 1                   # next day: approved_soon again (tomorrow)
    assert shown[-1] == "Deadline tomorrow: approved_soon"

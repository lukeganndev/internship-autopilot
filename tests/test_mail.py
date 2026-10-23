import httpx
import pytest

from iap import config, db, mail, outreach, profile as prof, vault
from iap.sources.common import Job

P = prof.load(config.EXAMPLES / "sample_profile.yaml")


@pytest.mark.parametrize("subject, body, status", [
    ("Your application to Acme", "Thank you for applying! We've received your application.", "applied"),
    ("Update on your application", "Thank you for applying. Unfortunately, we've decided to move forward with other candidates.", "rejected"),
    ("Acme Online Assessment", "As the next step in our interview process, please complete the HackerRank assessment.", "oa"),
    ("Next steps", "We'd like to schedule a 30-minute phone screen. Please share your availability.", "interview"),
    ("Congratulations!", "We are pleased to extend you an offer for the Summer 2027 internship.", "offer"),
    ("Your application status", "We regret to inform you that the position has been filled.", "rejected"),
    ("Acme newsletter", "Check out our new product launch.", None),
])
def test_classify(subject, body, status):
    assert mail.classify(subject, body) == status


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB", tmp_path / "t.db")
    monkeypatch.setattr(mail.notify, "toast", lambda *a, **k: None)
    c = db.connect()
    ids = {}
    for key, company, title, status in [("cf", "Cloudflare", "Software Engineer Intern", "applied"),
                                        ("pan_swe", "Palo Alto Networks", "Software Engineer Intern", "applied"),
                                        ("pan_sec", "Palo Alto Networks", "Security Research Intern", "applied"),
                                        ("skip", "Acme", "Marketing Intern", "skipped")]:
        ids[key], _ = db.upsert_job(c, Job("t", company, title, f"https://x.com/{key}"))
        c.execute("INSERT INTO applications(job_id, status) VALUES (?, ?)", (ids[key], status))
    c.commit()
    return c, ids


def test_match_by_company_name_domain_and_title(con):
    con, ids = con
    assert mail.match_job(con, "Cloudflare Recruiting <no-reply@greenhouse.io>", "Your application", "")["id"] == ids["cf"]
    assert mail.match_job(con, "Talent <talent@cloudflare.com>", "Interview", "")["id"] == ids["cf"]
    assert mail.match_job(con, "no-reply@myworkday.com", "Security Research Intern at Palo Alto Networks", "")["id"] == ids["pan_sec"]
    assert mail.match_job(con, "jobs@acme.com", "Thanks for applying", "") is None  # skipped jobs aren't tracked
    assert mail.match_job(con, "news@cloudflarestatus.io", "Weekly digest", "") is None


def test_statuses_only_move_forward_except_rejection(con):
    con, ids = con
    jid = ids["cf"]
    assert mail.advance(con, jid, "interview", "e1")
    assert not mail.advance(con, jid, "oa", "late OA email")       # no going backwards
    assert not mail.advance(con, jid, "applied", "receipt")
    assert mail.advance(con, jid, "rejected", "e2")
    assert not mail.advance(con, jid, "offer", "e3")               # rejected is final
    events = con.execute("SELECT old, new FROM status_events ORDER BY id").fetchall()
    assert [tuple(e) for e in events] == [("applied", "interview"), ("interview", "rejected")]


def test_process_dedupes_logs_and_undo_restores(con):
    con, ids = con
    msgs = [{"id": "m1", "sender": "Cloudflare <jobs@cloudflare.com>", "subject": "Interview invitation",
             "body": "We'd like to schedule a technical screen.", "received_at": "2026-10-24T10:00:00+00:00"},
            {"id": "m2", "sender": "Someone <x@y.com>", "subject": "Unfortunately", "body": "not moving forward",
             "received_at": "2026-10-24T11:00:00+00:00"}]
    changes = mail.process(con, "gmail", msgs)
    assert [(c["company"], c["status"]) for c in changes] == [("Cloudflare", "interview")]
    assert mail.process(con, "gmail", msgs) == []                  # each email is handled once
    unmatched = con.execute("SELECT classification, job_id FROM emails WHERE message_id='m2'").fetchone()
    assert tuple(unmatched) == ("rejected", None)
    ev = con.execute("SELECT id, evidence FROM status_events").fetchone()
    assert "Interview invitation" in ev["evidence"]
    mail.undo(con, ev["id"])
    assert con.execute("SELECT status FROM applications WHERE job_id=?", (ids["cf"],)).fetchone()[0] == "applied"


def test_vault_splits_long_values(monkeypatch):
    store = {}
    monkeypatch.setattr(vault.keyring, "set_password", lambda s, k, v: store.__setitem__(k, v))
    monkeypatch.setattr(vault.keyring, "get_password", lambda s, k: store.get(k))
    def fake_delete(s, k):
        if k not in store:
            raise vault.PasswordDeleteError(k)
        del store[k]
    monkeypatch.setattr(vault.keyring, "delete_password", fake_delete)
    big = "x" * 5000 + "end"
    vault.put("outlook-cache", big)
    assert vault.get("outlook-cache") == big and store["outlook-cache#n"] == "3"
    vault.put("outlook-cache", "short")
    assert vault.get("outlook-cache") == "short" and "outlook-cache#2" not in store
    vault.delete("outlook-cache")
    assert vault.get("outlook-cache") is None and store == {}


# ---------- outreach ----------

def _github(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")


def test_github_org_must_match_the_company():
    def handler(req):
        if req.url.path == "/search/users":
            return httpx.Response(200, json={"items": [{"login": "cloudflare-fans"}, {"login": "cloudflare"}]})
        names = {"cloudflare-fans": "Fans of CF", "cloudflare": "Cloudflare"}
        return httpx.Response(200, json={"login": req.url.path.split("/")[-1], "name": names[req.url.path.split("/")[-1]],
                                         "html_url": "https://github.com/x"})
    with _github(handler) as c:
        assert outreach.github_org(c, "Cloudflare")["login"] == "cloudflare"


def test_draft_uses_only_profile_facts_and_links_are_not_fetched():
    job = {"company": "Cloudflare", "title": "Software Engineer Intern"}
    text = outreach.draft(P, job, {"name": "Jamie Doe"}, P["projects"][0])
    assert text.startswith("Hi Jamie,") and "State University" in text and "Software Engineer Intern" in text
    assert "Most recently, on PacketScope, I built a home-network traffic analyzer" in text
    assert text.rstrip().endswith("Alex") and "recently applied for" in text
    assert "am applying for" in outreach.draft(P, job, {"name": "Jamie"}, None, applied=False)
    links = dict(outreach.search_links(job))
    assert all(u.startswith(("https://www.linkedin.com/search/", "https://www.google.com/search")) for u in links.values())

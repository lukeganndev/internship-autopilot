import pytest
from fastapi.testclient import TestClient

from iap import config, db, match, profile as prof
from iap.sources.common import Job

HX = {"HX-Request": "true"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB", tmp_path / "t.db")
    con = db.connect()
    ids = {
        "swe": db.upsert_job(con, Job("t", "Acme", "Software Engineering Intern", "https://a.com/1",
                                      description="Python SQL Linux Git Docker unit tests <script>x</script>"))[0],
        "dream": db.upsert_job(con, Job("t", "Google", "STEP Intern", "https://g.com/1"), dream=True)[0],
        "weak": db.upsert_job(con, Job("t", "Acme", "Marketing Intern", "https://a.com/2"))[0],
    }
    con.commit()
    match.score_all(con, prof.load(config.EXAMPLES / "sample_profile.yaml"))
    from iap.web.app import app
    c = TestClient(app)
    c.ids = ids
    return c


def test_queue_lists_matches_and_hides_weak_jobs(client):
    html = client.get("/").text
    assert "Software Engineering Intern" in html and "STEP Intern" in html
    assert "Marketing Intern" not in html
    assert "Queue (2)" in html


def test_queue_filters(client):
    assert "STEP Intern" not in client.get("/?q=Acme").text
    assert "Software Engineering Intern" not in client.get("/?dream=true").text
    assert "Marketing Intern" in client.get("/?min_score=0").text


def test_job_detail_shows_breakdown_and_escapes_description(client):
    html = client.get(f"/job/{client.ids['swe']}", headers=HX).text
    assert "+40" in html and "role: software" in html and "Approve" in html
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html
    assert "<header>" not in html                       # HTMX gets just the panel
    assert "<header>" in client.get(f"/job/{client.ids['swe']}").text  # direct visit gets the full page


def test_status_changes_require_htmx_header(client):
    jid = client.ids["swe"]
    assert client.post(f"/job/{jid}/status", data={"status": "approved"}).status_code == 403
    r = client.post(f"/job/{jid}/status", data={"status": "approved"}, headers=HX)
    assert r.status_code == 200 and "jobDecided" in r.headers["HX-Trigger"]
    assert "Software Engineering Intern" not in client.get("/").text   # left the queue
    assert "Software Engineering Intern" in client.get("/applications").text
    client.post(f"/job/{jid}/status", data={"status": "queue"}, headers=HX)
    assert "Software Engineering Intern" in client.get("/").text       # undo puts it back
    assert client.post(f"/job/{jid}/status", data={"status": "bogus"}, headers=HX).status_code == 400


def _has_tectonic():
    from iap import resume
    try:
        resume.find_tectonic()
        return True
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not _has_tectonic(), reason="tectonic not installed")
def test_review_screen_and_verified_bullet_edits(client, tmp_path, monkeypatch):
    from iap import tailor

    sample = config.EXAMPLES / "sample_profile.yaml"
    monkeypatch.setattr(config, "PROFILE", sample)
    monkeypatch.setattr(config, "OUT", tmp_path / "out")
    p = prof.load(sample)
    con = db.connect()
    jid = client.ids["swe"]
    job = con.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    spec, _ = tailor.build_spec(p, {"projects": ["packetscope"], "bullets": []})
    tailor.render_and_store(con, job, p, spec, [], ["Splunk"])
    con.execute("INSERT INTO applications(job_id, status) VALUES (?, 'tailored')", (jid,))
    con.execute("INSERT INTO answers(job_id, question, draft) VALUES (?, 'Why Acme?', 'Because PacketScope.')", (jid,))
    con.commit()

    html = client.get(f"/job/{jid}", headers=HX).text
    assert "ATS" in html and "Splunk" in html and "Why Acme?" in html and "Because PacketScope." in html
    assert client.get(f"/job/{jid}/resume.pdf").headers["content-type"] == "application/pdf"

    ok = "Analyzed home-network traffic to flag port scans and unusual DNS lookups using Scapy"
    bad = "Built a Kubernetes traffic analyzer that flags port scans and unusual DNS lookups using Scapy"
    html = client.post(f"/job/{jid}/bullets", data={"b:packetscope.1": bad, "b:packetscope.2": ok.replace("Analyzed", "Stored")},
                       headers=HX).text
    assert "edit not applied (it added Kubernetes)" in html
    html = client.post(f"/job/{jid}/bullets", data={"b:packetscope.1": ok}, headers=HX).text
    assert "packetscope.1: your edit applied" in html and ok in html

    client.post(f"/job/{jid}/answer/1", data={"text": "Edited answer.", "approve": "true"}, headers=HX)
    a = db.connect().execute("SELECT final, approved FROM answers WHERE id=1").fetchone()
    assert tuple(a) == ("Edited answer.", 1)


def test_email_page_lists_changes_and_undo(client, monkeypatch):
    from iap import mail

    monkeypatch.setattr(mail.notify, "toast", lambda *a, **k: None)
    monkeypatch.setattr(mail, "accounts", lambda: [])
    con = db.connect()
    jid = client.ids["swe"]
    con.execute("INSERT INTO applications(job_id, status) VALUES (?, 'applied')", (jid,))
    mail.advance(con, jid, "interview", "2026-10-24 · Acme <jobs@acme.com> · Interview invitation")
    con.commit()
    html = client.get("/email").text
    assert "Interview invitation" in html and "interview" in html and "none connected" in html
    assert client.post("/events/1/undo").status_code == 403
    client.post("/events/1/undo", headers=HX)
    assert db.connect().execute("SELECT status FROM applications WHERE job_id=?", (jid,)).fetchone()[0] == "applied"


def test_tracked_job_shows_outreach_links(client):
    con = db.connect()
    con.execute("INSERT INTO applications(job_id, status) VALUES (?, 'applied')", (client.ids["swe"],))
    con.commit()
    html = client.get(f"/job/{client.ids['swe']}", headers=HX).text
    assert "Outreach" in html and "linkedin.com/search/results/people" in html and "Find people" in html


def test_settings_save_threshold_and_watchlist(client):
    assert client.post("/settings", data={"threshold": "strict", "watchlist": "Acme"}).status_code == 403
    r = client.post("/settings", data={"threshold": "30", "watchlist": "Acme\n"}, headers=HX, follow_redirects=False)
    assert r.headers["HX-Redirect"] == "/settings?saved=1"
    con = db.connect()
    assert db.get_setting(con, "threshold") == 30 and db.get_setting(con, "watchlist") == ["Acme"]
    assert con.execute("SELECT is_dream FROM jobs WHERE id=?", (client.ids["swe"],)).fetchone()[0] == 1
    assert "Marketing Intern" in client.get("/").text  # threshold 30 lets it in

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


def test_settings_save_threshold_and_watchlist(client):
    assert client.post("/settings", data={"threshold": "strict", "watchlist": "Acme"}).status_code == 403
    r = client.post("/settings", data={"threshold": "30", "watchlist": "Acme\n"}, headers=HX, follow_redirects=False)
    assert r.headers["HX-Redirect"] == "/settings?saved=1"
    con = db.connect()
    assert db.get_setting(con, "threshold") == 30 and db.get_setting(con, "watchlist") == ["Acme"]
    assert con.execute("SELECT is_dream FROM jobs WHERE id=?", (client.ids["swe"],)).fetchone()[0] == 1
    assert "Marketing Intern" in client.get("/").text  # threshold 30 lets it in

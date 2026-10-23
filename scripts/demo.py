"""A fully fake demo: fictional companies + the sample "Alex Rivera" profile, in its own data folder.

    uv run python scripts/demo.py                 # build demo data and open the dashboard on it
    uv run python scripts/demo.py --screenshots   # rebuild docs/screenshots/*.png (headless)

Nothing here touches your real profile, database or accounts, and no Claude call is made: the
tailored resume comes from a canned proposal run through the same verifier as real tailoring.
"""
import os
import shutil
import sys
import tempfile
import threading
import time
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEMO = Path(tempfile.gettempdir()) / "internship-autopilot-demo"
os.environ["IAP_DATA_DIR"] = str(DEMO)  # must be set before iap.config is imported
sys.path.insert(0, str(REPO / "src"))

from iap import config, db, mail, match, profile as prof, tailor  # noqa: E402
from iap.sources.common import Job  # noqa: E402

SOON = (date.today() + timedelta(days=4)).isoformat()
LATER = (date.today() + timedelta(days=21)).isoformat()
SWE = ("You'll build backend services in Python and Go, write SQL against Postgres, ship with Git and Docker on Linux, "
       "and add unit tests. Experience with data structures and algorithms expected. Sophomores and juniors welcome.")
SEC = ("Join the detection team: analyze network traffic with Wireshark, write Python tooling, triage alerts in a SIEM, "
       "and learn penetration testing. Linux and networking fundamentals required. Ability to obtain a security clearance.")
HW = ("Write embedded C for STM32 microcontrollers, bring up boards designed in KiCad, debug with an oscilloscope, "
      "and automate tests in Python.")
ML = ("Train and evaluate machine learning models with PyTorch, build data pipelines in Python and SQL, "
      "and report statistics to the research team. Experience with LLMs is a plus.")
JOBS = [  # (company, title, location, season, description, deadline, dream)
    ("Northwind Systems", "Software Engineering Intern", "Seattle, WA", "Summer 2027", SWE, SOON, True),
    ("Helix Security", "Security Engineering Intern", "Austin, TX", "Summer 2027", SEC, LATER, True),
    ("Brightline Robotics", "Embedded Software Intern", "Pittsburgh, PA", "Summer 2027", HW, "", False),
    ("Quarry Data", "Machine Learning Intern", "Remote, United States", "Summer 2027", ML, "", False),
    ("Cobalt Labs", "Backend Engineer Intern", "New York, NY", "Summer 2027", SWE, LATER, False),
    ("Meridian Bank", "Technology Summer Analyst", "Charlotte, NC", "Summer 2027", SWE, "", False),
    ("Summit Aerospace", "Flight Software Intern", "Huntsville, AL", "Summer 2027", HW, SOON, False),
    ("Pinecrest Health", "Data Engineering Intern", "Nashville, TN", "Summer 2027", ML, "", False),
    ("Lattice Cloud", "Site Reliability Intern", "Denver, CO", "Summer 2027", SWE, "", False),
    ("Ironwood Defense", "Cyber Operations Intern", "Arlington, VA", "Summer 2027", SEC, "", False),
    ("Tidal Games", "Gameplay Programmer Intern", "Orlando, FL", "Summer 2027", SWE, "", False),
    ("Granite Analytics", "Marketing Intern", "Chicago, IL", "Summer 2027", "Run social campaigns and events.", "", False),
]


def build() -> None:
    if DEMO.exists():
        shutil.rmtree(DEMO)
    DEMO.mkdir(parents=True)
    shutil.copy(config.EXAMPLES / "sample_profile.yaml", config.PROFILE)
    shutil.copy(config.EXAMPLES / "sample_answers.yaml", config.ANSWERS)
    con = db.connect()
    db.set_setting(con, "watchlist", ["Northwind Systems", "Helix Security"])
    ids = {}
    for i, (company, title, loc, season, desc, deadline, dream) in enumerate(JOBS):
        job = Job("demo", company, title, f"https://careers.example.com/{i}", location=loc, season=season,
                  description=desc, deadline=deadline, posted_at=(date.today() - timedelta(days=i)).isoformat())
        ids[company], _ = db.upsert_job(con, job, flags=["sophomore-friendly"] if "Sophomores" in desc else [], dream=dream)
    con.commit()
    p = prof.load()
    match.score_all(con, p)

    # One approved + tailored application, verified exactly like a real Claude proposal.
    jid = ids["Northwind Systems"]
    con.execute("INSERT INTO applications(job_id, status) VALUES (?, 'tailored')", (jid,))
    proposal = {
        "projects": ["packetscope", "ctf-writeup-generator"], "experience": ["state-university-systems-lab"],
        "activities": ["cybersecurity-club"], "awards": ["Dean's List"], "certifications": [],
        "coursework": ["Data Structures & Algorithms", "Computer Organization", "Discrete Math", "Linear Algebra"],
        "skills": {"Languages": ["Python", "Go", "SQL", "C", "Bash"], "Developer Tools": ["Git", "Linux", "Docker"],
                   "Frameworks & Libraries": ["pytest", "Flask"]},
        "bullets": [
            {"source_id": "packetscope.1", "text": "Built a home-network traffic analyzer in Python that flags port scans and unusual DNS lookups using Scapy"},
            {"source_id": "packetscope.2", "text": "Stored 2M+ packet summaries in SQLite with indexed queries, cutting dashboard load time from 9s to under 1s"},
            {"source_id": "packetscope.3", "text": "Wrote 40 pytest cases covering the detection rules and parser edge cases"},
            {"source_id": "ctf-writeup-generator.1", "text": "Wrote a Go CLI that turns terminal session logs into formatted CTF writeups for the campus security club"},
            {"source_id": "state-university-systems-lab.2", "text": "Automated benchmark runs with Bash and Python on Linux, replacing a manual 2-hour process per run"},
            {"source_id": "state-university-systems-lab.1", "text": "Led benchmarking of three Linux sandboxing approaches for a faculty research project"},
        ],
        "gaps": ["Postgres in production"],
    }
    spec, notes = tailor.build_spec(p, proposal)
    job = con.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    tailor.render_and_store(con, job, p, spec, notes, proposal["gaps"])
    con.execute("INSERT INTO answers(job_id, question, required, draft, warnings) VALUES (?,?,?,?,?)",
                (jid, "Why do you want to intern at Northwind Systems?", 1,
                 "I built PacketScope, a Python traffic analyzer that stores 2M+ packet summaries in SQLite, and I'd like to "
                 "work on backend services at a larger scale. Your team's focus on reliable Python and Go services is a "
                 "direct next step from that project.", "[]"))

    # Tracker + an email-driven change so the Email page has something to show.
    for company, status in [("Cobalt Labs", "applied"), ("Helix Security", "approved"), ("Quarry Data", "applied"),
                            ("Granite Analytics", "skipped")]:
        con.execute("INSERT INTO applications(job_id, status) VALUES (?, ?)", (ids[company], status))
    mail.advance(con, ids["Quarry Data"], "oa", f"{date.today()} · Quarry Data <recruiting@quarry.example> · Your online assessment")
    mail.advance(con, ids["Cobalt Labs"], "interview", f"{date.today()} · Cobalt Labs <talent@cobalt.example> · Interview invitation")
    con.execute("INSERT INTO contacts(job_id, name, role, source_url, draft) VALUES (?,?,?,?,?)",
                (jid, "Jordan Lee", "Backend engineer @ Northwind", "https://github.com/example",
                 "Hi Jordan,\n\nI'm Alex Rivera, a computer science student at State University. I am applying for the Software "
                 "Engineering Intern role at Northwind Systems and saw you're part of Northwind Systems's GitHub organization. "
                 "Most recently, on PacketScope, I built a home-network traffic analyzer that flags port scans and unusual DNS "
                 "lookups using Scapy. If you have a few minutes, I'd really appreciate any advice on what the team looks for "
                 "in interns.\n\nThanks,\nAlex"))
    con.execute("INSERT INTO runs(kind, finished, stats) VALUES ('source', datetime('now'), '{}')")
    con.commit()
    shutil.copy(Path(con.execute("SELECT pdf_path FROM tailored").fetchone()[0]), REPO / "docs" / "sample_tailored_resume.pdf")
    print(f"Demo data in {DEMO}")


def serve(port: int):
    import uvicorn

    server = uvicorn.Server(uvicorn.Config("iap.web.app:app", host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    time.sleep(2)
    return server


def screenshots(port: int = 8799) -> None:
    from playwright.sync_api import sync_playwright

    out = REPO / "docs" / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    serve(port)
    base = f"http://127.0.0.1:{port}"
    con = db.connect()
    jid = con.execute("SELECT id FROM jobs WHERE company='Northwind Systems'").fetchone()[0]
    shown = con.execute("SELECT id FROM jobs WHERE company='Summit Aerospace'").fetchone()[0]  # a queue job
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1360, "height": 860}, color_scheme="light")
        page.goto(base + "/")
        page.click(f"#row-{shown}")
        page.wait_for_selector("#detail h2")
        page.screenshot(path=str(out / "queue.png"))
        page.goto(f"{base}/job/{jid}")
        # Headless Chromium can't draw PDFs in an iframe; hide it (the PDF itself is docs/sample_tailored_resume.pdf)
        # and open the verifier notes, which show a rewrite being reverted.
        page.evaluate("""() => { document.querySelectorAll('iframe').forEach(f => f.remove());
                                  document.querySelector('header').style.position = 'static';
                                  document.querySelectorAll('details').forEach(d => d.open = d.textContent.includes('Verifier')); }""")
        page.locator(".panel").first.screenshot(path=str(out / "review.png"))
        page.goto(base + "/applications")
        page.screenshot(path=str(out / "tracker.png"))
        page.goto(base + "/email")
        page.screenshot(path=str(out / "email.png"))
        b.close()
    print(f"Screenshots in {out}")


if __name__ == "__main__":
    build()
    if "--screenshots" in sys.argv:
        screenshots()
    else:
        import webbrowser

        serve(8766)
        webbrowser.open("http://127.0.0.1:8766")
        print("Demo dashboard at http://127.0.0.1:8766 (Ctrl+C to stop)")
        while True:
            time.sleep(3600)

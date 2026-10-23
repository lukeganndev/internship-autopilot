import json

import pytest

from iap import config, db, profile as prof, resume, tailor
from iap.sources.common import Job

P = prof.load(config.EXAMPLES / "sample_profile.yaml")
PACKET = P["projects"][0]
SRC = PACKET["bullets"][1]["text"]  # "Stored 2M+ packet summaries in SQLite ... from 9s to under 1s"


# ---------- the never-invent verifier ----------

@pytest.mark.parametrize("text, problem", [
    ("Stored 2M+ packet summaries in SQLite with indexed queries, reducing dashboard load from 9s to under 1s", None),
    ("Indexed 2M+ packet summaries in SQLite, cutting dashboard load time from 9s to under 1s", None),
    ("Stored 5M+ packet summaries in SQLite, cutting load time from 9s to under 1s", "added the number 5"),
    ("Stored 2M+ packet summaries in PostgreSQL on AWS, cutting load time from 9s to under 1s", "added AWS"),
    ("Led a team that stored 2M+ packet summaries in SQLite, cutting load from 9s to under 1s", "added the claim 'led'"),
    ("Stored 2M+ packet summaries in SQLite for 300 users, cutting load from 9s to under 1s", "added the number 300"),
    ("", "empty"),
])
def test_check_bullet(text, problem):
    got = tailor.check_bullet(text, SRC, PACKET, P)
    assert got == problem if problem is None else problem in got


def test_project_tech_list_counts_as_known():
    # Flask is in PacketScope's tech list, so naming it in a rewording isn't new information.
    assert tailor.check_bullet("Built a Flask traffic analyzer that flags port scans and unusual DNS lookups using Scapy",
                               PACKET["bullets"][0]["text"], PACKET, P) is None


def claude_out(**over):
    out = {
        "projects": ["packetscope", "ctf-writeup-generator"], "experience": ["state-university-systems-lab"],
        "activities": [], "awards": ["Dean's List"], "certifications": [],
        "coursework": ["Intro to Security", "Data Structures & Algorithms"],
        "skills": {"Languages": ["Python", "C"], "Security": ["Wireshark", "Metasploit"]},
        "bullets": [
            {"source_id": "packetscope.2", "text": "Indexed 2M+ packet summaries in SQLite, cutting dashboard load time from 9s to under 1s"},
            {"source_id": "packetscope.1", "text": "Built a traffic analyzer in Python on AWS that flags port scans"},
            {"source_id": "ctf-writeup-generator.1", "text": "Wrote a Go CLI that turns terminal logs into CTF writeups for the security club"},
            {"source_id": "made-up.1", "text": "Invented"},
        ],
        "gaps": ["Splunk"], "cover_letter": None, "answers": [],
    }
    return {**out, **over}


def test_build_spec_keeps_only_profile_content_and_reverts_bad_rewrites():
    spec, notes = tailor.build_spec(P, claude_out())
    assert [x["id"] for x in spec["projects"]] == ["packetscope", "ctf-writeup-generator"]
    pk = spec["projects"][0]["bullets"]
    assert pk[0] == {"id": "packetscope.2", "text": "Indexed 2M+ packet summaries in SQLite, cutting dashboard load time from 9s to under 1s"}
    assert pk[1]["text"] == PACKET["bullets"][0]["text"]                   # AWS rewrite reverted
    assert spec["experience"][0]["bullets"] == P["experience"][0]["bullets"][:3]  # no bullets given → originals
    assert spec["skills"] == {"Languages": ["Python", "C"], "Security": ["Wireshark"]}  # Metasploit dropped
    assert spec["education"][0]["coursework"] == ["Intro to Security", "Data Structures & Algorithms"]
    assert [a["name"] for a in spec["awards"]] == ["Dean's List"]
    joined = " | ".join(notes)
    assert "packetscope.1: kept your original wording" in joined and "Metasploit" in joined and "made-up.1" in joined


def test_build_spec_ignores_unknown_ids_and_courses():
    spec, notes = tailor.build_spec(P, claude_out(projects=["packetscope", "nope"], coursework=["Quantum Gravity"]))
    assert [x["id"] for x in spec["projects"]] == ["packetscope"]
    assert spec["education"][0]["coursework"] == P["education"][0]["coursework"]
    assert any("nope" in n for n in notes) and any("Quantum Gravity" in n for n in notes)


def test_parse_json_object_tolerates_fences_and_prose():
    assert tailor.parse_json_object('Here you go:\n```json\n{"a": {"b": 1}}\n```') == {"a": {"b": 1}}
    with pytest.raises(ValueError):
        tailor.parse_json_object("no json here")


def test_free_text_warnings():
    warns = tailor.free_text_warnings("I used Kubernetes to serve 37 clusters at Rivera Lawn Care.", P, "We run 12 clusters.")
    assert any("37" in w for w in warns) and any("Kubernetes" in w for w in warns) and any("excluded" in w for w in warns)
    assert tailor.free_text_warnings("I built PacketScope in Python and stored 2M+ packet summaries.", P, "") == []


def test_greenhouse_question_parsing():
    d = {"questions": [
        {"label": "First Name", "required": True, "fields": [{"name": "first_name", "type": "input_text"}]},
        {"label": "Cover Letter", "required": True, "fields": [{"name": "cover_letter", "type": "input_file"}]},
        {"label": "Why do you want to work at Acme?", "required": True, "fields": [{"name": "question_1", "type": "textarea"}]},
        {"label": "Describe a project you're proud of", "required": False, "fields": [{"name": "question_2", "type": "input_text"}]},
        {"label": "LinkedIn Profile", "required": False, "fields": [{"name": "question_3", "type": "input_text"}]},
        {"label": "Are you legally authorized to work in the US?", "fields": [{"name": "question_4", "type": "multi_value_single_select"}]},
    ]}
    assert tailor.parse_greenhouse_questions(d) == (True, [("Why do you want to work at Acme?", True),
                                                          ("Describe a project you're proud of", False)])


# ---------- one page + ATS ----------

def _has_tectonic():
    try:
        resume.find_tectonic()
        return True
    except FileNotFoundError:
        return False


needs_tectonic = pytest.mark.skipif(not _has_tectonic(), reason="tectonic not installed")


@needs_tectonic
def test_overlong_spec_is_trimmed_to_one_page(tmp_path):
    spec, _ = tailor.build_spec(P, claude_out())
    filler = P["projects"][0]["bullets"][0]["text"]
    for i in range(6):  # pile on long items until it spills to page 2
        spec["projects"].append({**P["projects"][0], "id": f"x{i}", "bullets": [{"id": f"x{i}.{n}", "text": filler * 2} for n in range(3)]})
    pdf, pages, trimmed = tailor.fit_one_page(spec, tmp_path, "t")
    assert pages == 1 and trimmed and all(t.startswith("Trimmed to fit one page") for t in trimmed)


@needs_tectonic
def test_ats_check_reads_the_pdf(tmp_path):
    pdf, _ = resume.build(P, tmp_path, "t")
    score, matched, missing = tailor.ats_check("We use Python, SQL, Docker and Kubernetes.", pdf)
    assert matched == ["Docker", "Python", "SQL"] and missing == ["Kubernetes"] and score == 75


@needs_tectonic
def test_tailor_job_end_to_end_with_stubbed_claude(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB", tmp_path / "t.db")
    monkeypatch.setattr(config, "OUT", tmp_path / "out")
    monkeypatch.setattr(tailor, "ask_claude", lambda data, model: claude_out(answers=[]))
    con = db.connect()
    jid, _ = db.upsert_job(con, Job("t", "Acme", "Security Engineering Intern", "https://acme.com/jobs/1",
                                    description="Python, SQL, Wireshark and Kubernetes."))
    con.execute("INSERT INTO applications(job_id, status) VALUES (?, 'approved')", (jid,))
    con.execute("INSERT INTO answers(job_id, question) VALUES (?, 'Why Acme?')", (jid,))
    con.commit()
    job = con.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()

    sent = {}
    monkeypatch.setattr(tailor, "ask_claude", lambda data, model: sent.update(data) or claude_out(
        answers=[{"question": "Why Acme?", "answer": "I built PacketScope in Python.", "source_ids": ["packetscope"]}]))
    result = tailor.tailor_job(con, job, P, "sonnet")

    assert result["pages"] == 1 and result["questions"] == 1
    assert "phone" not in json.dumps(sent) and P["contact"]["email"] not in json.dumps(sent)  # contact info stays local
    t = con.execute("SELECT * FROM tailored WHERE job_id=?", (jid,)).fetchone()
    assert "Kubernetes (not in your profile)" in json.loads(t["gaps"]) and "Splunk" in json.loads(t["gaps"])
    assert con.execute("SELECT status FROM applications WHERE job_id=?", (jid,)).fetchone()[0] == "tailored"
    a = con.execute("SELECT * FROM answers WHERE job_id=?", (jid,)).fetchone()
    assert a["draft"] == "I built PacketScope in Python." and a["approved"] == 0 and a["final"] is None

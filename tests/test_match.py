from types import SimpleNamespace

import pytest

from iap import config, db, match, profile as prof
from iap.sources.common import Job

HAVE = match.profile_terms(prof.load(config.EXAMPLES / "sample_profile.yaml"))


def job(title="Software Engineering Intern", desc="", season="Summer 2027", flags=()):
    return SimpleNamespace(title=title, description=desc, season=season, flags=list(flags))


def test_sample_profile_terms_come_from_skills_tech_coursework_and_bullets():
    assert {"Python", "SQL", "Linux", "Docker", "Wireshark", "Data Structures", "Testing"} <= HAVE
    assert "Kubernetes" not in HAVE


def test_strong_swe_match_clears_balanced_threshold():
    desc = "You will write Python and SQL services on Linux, use Git and Docker, and add unit tests."
    s, reasons, missing = match.score(job(desc=desc), HAVE)
    assert s >= match.THRESHOLDS["balanced"]
    assert reasons[0] == "+40 role: software" and missing == []


def test_missing_skills_are_listed_not_counted():
    s_full, _, _ = match.score(job(desc="Python, SQL, Linux, Git"), HAVE)
    s_gap, reasons, missing = match.score(job(desc="Python, Kubernetes, Rust, Kotlin, Azure"), HAVE)
    assert s_gap < s_full
    assert set(missing) == {"Kubernetes", "Rust", "Kotlin", "Azure"}
    assert "1 of 5 matched" in reasons[1]


@pytest.mark.parametrize("title, role", [
    ("Cybersecurity Intern", "security"), ("SOC Analyst Intern", "security"),
    ("Machine Learning Intern", "ai/ml/data"), ("Firmware Engineering Intern", "embedded/hardware"),
    ("Software Engineer Intern", "software"), ("Mechanical Engineering Intern", "other engineering"),
    ("Engineering Intern", "general engineering"), ("Marketing Intern", "non-technical"),
    ("IT Support Intern", "it/networking"), ("Quantitative Trading Intern", "quant"),
])
def test_role_classification(title, role):
    assert match.role_of(title, "")[0] == role


def test_generic_title_falls_back_to_description():
    assert match.role_of("Summer Analyst", "You will do software engineering on trading systems.")[:2] == ("software", 25)


def test_non_technical_roles_fall_below_threshold():
    s, _, _ = match.score(job(title="Marketing Intern", desc="Social media and campaigns."), HAVE)
    assert s < match.THRESHOLDS["broad"]


def test_class_year_signals():
    soph, _, _ = match.score(job(flags=["sophomore-friendly"]), HAVE)
    plain, _, _ = match.score(job(), HAVE)
    phd, reasons, _ = match.score(job(desc="Candidates must be currently pursuing a PhD in CS."), HAVE)
    research, _, _ = match.score(job(title="Research Scientist Intern"), HAVE)
    assert soph > plain > research and plain > phd
    assert any("grad students" in r for r in reasons)
    step, _, _ = match.score(job(title="STEP Intern"), HAVE)
    assert step == soph


def test_next_cycle_and_borderline_titles_score_low():
    from datetime import date

    assert match.recruiting_summer(date(2026, 10, 23)) == 2027 and match.recruiting_summer(date(2027, 3, 1)) == 2027
    now, _, _ = match.score(job(season="Summer 2027"), HAVE)
    later, reasons, _ = match.score(job(season="Summer 2028"), HAVE)
    assert now - later == 10 and "next year's recruiting cycle" in reasons[3]
    assert match.role_of("Quality Engineering Intern", "")[0] == "other engineering"
    assert match.role_of("2027 Summer Intern", "TV programming and events")[0] == "non-technical"


def test_season_coop_and_clearance():
    summer, _, _ = match.score(job(), HAVE)
    spring, _, _ = match.score(job(season="Spring 2027"), HAVE)
    coop, _, _ = match.score(job(flags=["co-op"]), HAVE)
    clearance, reasons, _ = match.score(job(flags=["clearance required"]), HAVE)
    assert summer > spring > coop and summer - clearance == 10
    assert reasons[-1] == "-10 requires an active security clearance"


def test_single_letter_languages_do_not_false_match():
    assert match.terms_in("Section C of the handbook; be a go-getter; plan R&D.") == set()
    assert match.terms_in("C++ and C# required") == {"C++", "C#"}
    assert "Java" not in match.terms_in("JavaScript only")


def test_queue_uses_threshold_and_always_includes_dream_companies(tmp_path):
    con = db.connect(tmp_path / "t.db")
    good, _ = db.upsert_job(con, Job("t", "Acme", "Software Engineering Intern", "https://a.com/1",
                                     description="Python SQL Linux Git Docker unit tests"))
    dream, _ = db.upsert_job(con, Job("t", "Google", "Marketing Intern", "https://g.com/1"), dream=True)
    weak, _ = db.upsert_job(con, Job("t", "Acme", "Marketing Intern", "https://a.com/2"))
    stats = match.score_all(con, prof.load(config.EXAMPLES / "sample_profile.yaml"))
    assert stats["scored"] == 3
    assert [r["id"] for r in match.queue(con)] == [dream, good]

    con.execute("INSERT INTO applications(job_id, status) VALUES (?, 'skipped')", (good,))
    assert [r["id"] for r in match.queue(con)] == [dream]  # decided jobs leave the queue

    db.set_setting(con, "threshold", "strict")
    assert match.threshold(con) == 70
    db.set_setting(con, "threshold", 30)
    assert weak in [r["id"] for r in match.queue(con)]

from iap import db
from iap.sources.common import Job


def con(tmp_path):
    return db.connect(tmp_path / "t.db")


def test_same_ats_key_from_two_sources_merges(tmp_path):
    c = con(tmp_path)
    board = Job("greenhouse", "Stripe", "SWE Intern", "https://boards.greenhouse.io/stripe/jobs/1", description="Full text")
    stub = Job("simplify", "Stripe", "Software Engineer Intern", "https://job-boards.greenhouse.io/stripe/jobs/1?gh_src=x")
    a, new_a = db.upsert_job(c, board)
    b, new_b = db.upsert_job(c, stub)
    assert (a, new_a, new_b) == (b, True, False)
    assert {r["source"] for r in c.execute("SELECT source FROM job_sources")} == {"greenhouse", "simplify"}


def test_url_only_listing_merges_on_company_and_title(tmp_path):
    c = con(tmp_path)
    a, _ = db.upsert_job(c, Job("greenhouse", "Acme, Inc.", "Security Intern", "https://boards.greenhouse.io/acme/jobs/9"))
    b, is_new = db.upsert_job(c, Job("simplify", "Acme", "Security Intern", "https://careers.acme.com/jobs/9", location="NYC"))
    assert a == b and not is_new
    assert c.execute("SELECT location FROM jobs WHERE id=?", (a,)).fetchone()[0] == "NYC"  # filled from the second source


def test_distinct_ats_postings_with_same_title_stay_separate(tmp_path):
    c = con(tmp_path)
    a, _ = db.upsert_job(c, Job("workday", "NVIDIA", "Software Intern", "https://nvidia.wd5.myworkdayjobs.com/s/job/CA/Intern_JR1"))
    b, is_new = db.upsert_job(c, Job("workday", "NVIDIA", "Software Intern", "https://nvidia.wd5.myworkdayjobs.com/s/job/TX/Intern_JR2"))
    assert a != b and is_new


def test_settings_default_and_override(tmp_path):
    c = con(tmp_path)
    assert db.get_setting(c, "direct_boards") is True
    db.set_setting(c, "direct_boards", False)
    assert db.get_setting(c, "direct_boards") is False

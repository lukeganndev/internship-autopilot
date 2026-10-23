from pathlib import Path

import pytest
import yaml

from iap import config, db
from iap.autofill import fields as F
from iap.sources.common import Job

FORMS = Path(__file__).parent / "fixtures" / "forms"
ANSWERS = yaml.safe_load((config.EXAMPLES / "sample_answers.yaml").read_text(encoding="utf-8"))


def ctx(tmp_path=None, **kw):
    resume = None
    if tmp_path:
        resume = tmp_path / "Alex_Rivera_Resume.pdf"
        resume.write_bytes(b"%PDF-1.4 test")
    return F.Context(answers=ANSWERS, resume_pdf=resume, **kw)


# ---------- field → value (no browser) ----------

@pytest.mark.parametrize("label, kind, expected", [
    ("First Name *", "text", "Alex"), ("Family Name", "text", "Rivera"), ("Preferred First Name", "text", "Alex"),
    ("Email *", "text", "alex.rivera@example.com"), ("School Email (.edu)", "text", "arivera@student.example.edu"),
    ("Phone", "text", "(555) 010-4477"), ("LinkedIn Profile", "text", "https://www.linkedin.com/in/example-alex"),
    ("School or University", "text", "State University"), ("GPA", "text", "3.72"),
    ("Expected graduation date", "text", "May 2029"), ("Major / field of study", "text", "Computer Science"),
    ("Are you legally authorized to work in the United States?", "select", "Yes"),
    ("Will you now or in the future require visa sponsorship?", "select", "No"),
    ("Do you currently hold an active security clearance?", "select", "No"),
    ("Are you able to obtain a security clearance?", "select", "Yes"),
    ("How did you hear about us?", "select", "Company careers website"),
    ("Gender", "select", "Decline to self-identify"), ("Veteran Status", "select", "I am not a protected veteran"),
    ("Are you Hispanic/Latino?", "select", "Decline to self-identify"),
    ("Are you currently enrolled in a university and will return to the program upon completion?", "select", "Yes"),
    ("Current Employer", "text", F.SKIP), ("Desired salary", "text", F.SKIP), ("Pronouns", "text", F.SKIP),
    ("Favorite color", "text", None),
])
def test_value_for_labels(label, kind, expected):
    assert F.value_for({"kind": kind, "label": label}, ctx()) == expected


def test_free_text_only_uses_approved_answers():
    c = ctx(approved=[("Why do you want to work at Acme?", "Because of PacketScope.")])
    assert F.value_for({"kind": "textarea", "label": "Why do you want to work at Acme? *"}, c) == "Because of PacketScope."
    assert F.value_for({"kind": "textarea", "label": "Describe a challenge you overcame"}, c) is None
    assert F.value_for({"kind": "checkbox", "label": "I agree", "question": "Privacy policy"}, c) is F.SKIP


def test_files():
    c = F.Context(answers=ANSWERS, resume_pdf=Path("r.pdf"))
    assert F.value_for({"kind": "file", "label": "Resume/CV *"}, c) == "r.pdf"
    assert F.value_for({"kind": "file", "label": "Attach", "question": "Resume/CV * Attach Dropbox"}, c) == "r.pdf"
    assert F.value_for({"kind": "file", "label": "Attach", "question": "Cover Letter Attach Dropbox"}, c) is None
    assert F.value_for({"kind": "file", "label": "Unofficial transcript"}, c) is None
    assert F.value_for({"kind": "file", "label": "Attach", "question": ""}, c) is None  # unidentified upload: yours


@pytest.mark.parametrize("options, desired, expected", [
    (["Select...", "Yes", "No"], "Yes", "Yes"),
    (["Yes, I am authorized", "No, I am not"], "No", "No, I am not"),
    (["Male", "Female", "Decline To Self Identify"], "Male", "Male"),
    (["Female", "Male"], "Male", "Male"),
    (["Male", "Female", "I don't wish to answer"], "Decline to self-identify", "I don't wish to answer"),
    (["Alabama", "Alaska"], "AL", "Alabama"), (["AL", "AK"], "Alabama", "AL"),
    (["United States of America", "Canada"], "United States", "United States of America"),
    (["High School", "Bachelor's Degree", "Master's Degree"], "B.S. in Computer Science", "Bachelor's Degree"),
    (["2027", "2028", "2029"], "May 2029", "2029"),
    (["U.S. Citizen", "Permanent Resident", "Other"], "U.S. Citizen", "U.S. Citizen"),
    (["Tuscaloosa, Alabama, United States", "Tucson, Arizona"], "Tuscaloosa, AL", "Tuscaloosa, Alabama, United States"),
    (["LinkedIn", "Indeed"], "Company careers website", None),
    (["LinkedIn", "Cloudflare Website", "Referral"], "Company careers website", "Cloudflare Website"),
    (["College/University Career Fair or Career Website", "LinkedIn"], "Company careers website", None),
    (["Job board", "Company Website"], "Company careers website", "Company Website"),
    (["High school", "Undergraduate", "Graduate"], "B.S. in Computer Science", "Undergraduate"),
    (["Select one"], "Yes", None),
])
def test_pick_option(options, desired, expected):
    assert F.pick_option(options, desired) == expected


# ---------- real browser against fixture forms ----------

def _chromium_ok():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


browser_test = pytest.mark.skipif(not _chromium_ok(), reason="Playwright Chromium not installed")


@pytest.fixture
def page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        yield pg
        b.close()


@pytest.fixture
def con_job(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB", tmp_path / "t.db")
    con = db.connect()
    jid, _ = db.upsert_job(con, Job("t", "Acme", "Software Engineering Intern", "https://example.com/1"))
    con.commit()
    return con, con.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()


@browser_test
def test_greenhouse_like_form_fills_and_never_submits(page, con_job, tmp_path):
    from iap.autofill import runner

    con, job = con_job
    page.goto((FORMS / "greenhouse_like.html").as_uri())
    state = runner.new_state()
    c = ctx(tmp_path, approved=[("Why do you want to work at Acme?", "Because of PacketScope.")])
    for _ in range(2):  # two ticks, like the watch loop
        runner.step(page, con, job, c, state, "greenhouse")

    v = lambda sel: page.input_value(sel)
    assert (v("#first_name"), v("#last_name"), v("#email"), v("#school"), v("#gpa")) == \
           ("Alex", "Rivera", "alex.rivera@example.com", "State University", "3.72")
    assert v("#phone") == "(000) 000-0000"                      # never overwrites what's already there
    assert page.eval_on_selector("#resume", "e => e.files[0].name") == "Alex_Rivera_Resume.pdf"
    assert page.eval_on_selector("#cover", "e => e.files.length") == 0  # "Attach" label, box says Cover Letter
    assert v("#hear") == "Company Careers Website"                      # placeholder "Select..." skipped for the real label
    sel = lambda s: page.eval_on_selector(s, "e => e.options[e.selectedIndex].text")
    assert (sel("#auth"), sel("#sponsor"), sel("#state"), sel("#gender"), sel("#vet")) == \
           ("Yes", "No", "Select...", "Decline To Self Identify", "I am not a protected veteran")
    assert v("#why") == "Because of PacketScope." and v("#challenge") == "" and v("#salary") == ""
    assert page.is_checked("#consent") is False                 # consent stays yours
    assert page.evaluate("window.__submitted") is False and page.evaluate("window.__clicked") == []
    assert "Describe a technical challenge you overcame and what you learned." in state["new_questions"]
    assert con.execute("SELECT count(*) FROM answers WHERE question LIKE 'Describe a technical%'").fetchone()[0] == 1
    needs = " | ".join(state["needs"].values())
    assert "Describe a technical challenge" in needs and "privacy policy" in needs
    assert "Filled" in page.inner_text("#iap-banner") and "click Submit yourself" in page.inner_text("#iap-banner")


@browser_test
def test_workday_like_dropdowns_radios_combobox_without_clicking_next(page, con_job, tmp_path):
    from iap.autofill import runner

    con, job = con_job
    page.goto((FORMS / "workday_like.html").as_uri())
    state = runner.new_state()
    runner.step(page, con, job, ctx(tmp_path), state, "workday")

    assert page.input_value("#fn") == "Alex" and page.input_value("#ln") == "Rivera" and page.input_value("#city") == "Springfield"
    assert page.inner_text("#hear") == "Company Careers Website"
    assert page.is_checked("input[name=adult][value=y]")
    assert page.input_value("#school") == "State University"
    clicked = page.evaluate("window.__clicked")
    assert "next" not in clicked and "submit" not in clicked     # you move between steps, not autofill


@browser_test
def test_safe_click_refuses_submit_like_buttons(page):
    from iap.autofill import runner

    page.goto((FORMS / "workday_like.html").as_uri())
    for sel in ("#next", "#submit"):
        with pytest.raises(runner.SubmitGuard):
            runner.safe_click(page.locator(sel))
    assert page.evaluate("window.__clicked") == []


@browser_test
@pytest.mark.parametrize("html, blocker", [
    ('<form><input type="email"><input type="password"><button>Sign In</button></form>', "login"),
    ('<h1>Create Account</h1><input type="password"><input type="password">', "account"),
    ('<iframe src="https://www.google.com/recaptcha/api2/bframe" width="400" height="500"></iframe>', "captcha"),
    ('<form><input id="a"></form>', ""),
])
def test_blockers(page, html, blocker):
    from iap.autofill import runner

    page.set_content(html)
    page.evaluate(runner.JS)
    assert page.evaluate("window.__iap.blockers()") == blocker


@browser_test
def test_blocked_page_is_not_filled_and_confirmation_marks_applied(page, con_job, tmp_path, monkeypatch):
    from iap.autofill import runner

    monkeypatch.setattr(runner.notify, "toast", lambda *a, **k: None)
    con, job = con_job
    page.set_content('<h1>Create Account</h1><label for="e">Email</label><input id="e"><input type="password"><input type="password">')
    state = runner.new_state()
    runner.step(page, con, job, ctx(tmp_path), state, "workday")
    assert page.input_value("#e") == "" and "never creates accounts" in page.inner_text("#iap-banner")

    page.set_content("<h1>Thank you for applying!</h1><p>We've received your application.</p>")
    runner.step(page, con, job, ctx(tmp_path), state, "workday")
    row = con.execute("SELECT status, applied_at FROM applications WHERE job_id=?", (job["id"],)).fetchone()
    assert row["status"] == "applied" and row["applied_at"]


def test_form_urls():
    from iap.autofill.runner import form_url

    assert form_url("https://boards.greenhouse.io/acme/jobs/123?gh_jid=123") == ("https://job-boards.greenhouse.io/acme/jobs/123", "greenhouse")
    assert form_url("https://jobs.lever.co/acme/abc-1") == ("https://jobs.lever.co/acme/abc-1/apply", "lever")
    assert form_url("https://jobs.ashbyhq.com/acme/xyz") == ("https://jobs.ashbyhq.com/acme/xyz/application", "ashby")
    assert form_url("https://acme.wd5.myworkdayjobs.com/en-US/Ext/job/X/Intern_R1")[1] == "workday"
    assert form_url("https://careers-acme.icims.com/jobs/1/intern/job")[1] == "icims"
    assert form_url("https://careers.acme.com/1")[1] == "generic"

from datetime import date, timedelta

import pytest

from iap.sources import lists
from iap.sources.common import html_to_text, iso_date, job_key, parse_url


@pytest.mark.parametrize("url, key", [
    ("https://boards.greenhouse.io/stripe/jobs/123?gh_src=Simplify", "greenhouse:stripe:123"),
    ("https://job-boards.greenhouse.io/stripe/jobs/123", "greenhouse:stripe:123"),
    ("https://boards.greenhouse.io/embed/job_app?for=stripe&token=123", "greenhouse:stripe:123"),
    ("https://jobs.lever.co/rigetti/45c4-ab/apply", "lever:rigetti:45c4-ab"),
    ("https://jobs.ashbyhq.com/handshake/792a-cd/application", "ashby:handshake:792a-cd"),
    ("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Intern_JR2026530",
     "workday:nvidia:intern_jr2026530"),
    ("https://nvidia.wd5.myworkdayjobs.com/nvidiaexternalcareersite/job/US-CA-Santa-Clara/Intern_JR2026530/apply",
     "workday:nvidia:intern_jr2026530"),
    ("https://jobs.smartrecruiters.com/BoschGroup/744000111461185-software-intern", "smartrecruiters:boschgroup:744000111461185"),
    ("https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210512345", "oracle:jpmc:210512345"),
    ("https://www.amazon.jobs/en/jobs/10503471/sde-intern", "amazon:amazon:10503471"),
    ("https://apply.careers.microsoft.com/careers/job/1970393557041731", "microsoft:microsoft:1970393557041731"),
    ("https://www.google.com/about/careers/applications/jobs/results/123-step/", "url:google.com/about/careers/applications/jobs/results/123-step"),
])
def test_same_posting_gets_same_key_from_any_url_form(url, key):
    assert job_key(url) == key


def test_workday_ref_keeps_host_and_site_for_api_calls():
    ref = parse_url("https://citi.wd5.myworkdayjobs.com/en-US/2/job/Tampa-Florida/Tech-Intern_25812345")
    assert (ref.host, ref.site, ref.board_key) == ("citi.wd5.myworkdayjobs.com", "2", "workday:citi:2")


def test_listings_json_keeps_visible_and_reads_terms():
    items = [
        {"company_name": "Meta", "title": "SWE Intern", "url": "https://www.metacareers.com/jobs/1", "locations": ["Menlo Park, CA", "NYC"],
         "terms": ["Summer 2027"], "date_posted": 1792679304, "active": True, "is_visible": True},
        {"company_name": "Hidden", "title": "X", "url": "https://x.com/1", "is_visible": False},
        {"company_name": "Old", "title": "Y Intern", "url": "https://y.com/1", "active": False, "terms": ["N/A"]},
    ]
    jobs = lists.from_listings_json("simplify", items)
    assert [j.company for j in jobs] == ["Meta", "Old"]
    assert jobs[0].location == "Menlo Park, CA; NYC" and jobs[0].season == "Summer 2027"
    assert jobs[0].posted_at == "2026-10-22" and not jobs[1].active


README = """
<!-- TABLE_FAANG_START -->
| Company | Position | Location | Salary | Posting | Age |
|---|---|---|---|---|---|
| <a href="https://www.nvidia.com"><strong>NVIDIA</strong></a> | Systems Software Intern - Summer 2027 | Santa Clara, CA | $62/hr | <a href="https://nvidia.wd5.myworkdayjobs.com/en-US/site/job/US-CA/Intern_JR1"><img src="x.png" alt="Apply"/></a> | 2d |
| ↳ | Hardware Intern | Austin, TX | $55/hr | <a href="https://nvidia.wd5.myworkdayjobs.com/en-US/site/job/US-TX/HW_JR2">Apply</a> | 1mo |
| **Closed Co** | Old Intern | Remote | | 🔒 | 3mo |

| Company | Role | Location | Application/Link | Date Posted |
| --- | --- | --- | :---: | --- |
| [Acme](https://acme.com) | SWE Intern | NYC | [Apply](https://jobs.lever.co/acme/abc) | Oct 01 |
"""


def test_markdown_tables_map_columns_by_header_and_follow_arrows():
    jobs = lists.from_markdown_tables("speedyapply", README)
    assert [(j.company, j.title) for j in jobs] == [
        ("NVIDIA", "Systems Software Intern - Summer 2027"), ("NVIDIA", "Hardware Intern"), ("Acme", "SWE Intern")]
    assert jobs[0].salary == "$62/hr" and jobs[0].key == "workday:nvidia:intern_jr1"
    assert jobs[1].posted_at == (date.today() - timedelta(days=30)).isoformat()
    assert jobs[2].key == "lever:acme:abc"


def test_iso_date_formats():
    today = date.today()
    assert iso_date("2026-10-21T14:43:01-04:00") == "2026-10-21"
    assert iso_date(1792679304) == iso_date(1792679304000) == "2026-10-22"
    assert iso_date("Posted 3 Days Ago") == (today - timedelta(days=3)).isoformat()
    assert iso_date("Posted 30+ Days Ago") == (today - timedelta(days=30)).isoformat()
    assert iso_date("August 17, 2026") == "2026-08-17"
    assert iso_date("Posted Today") == today.isoformat()
    assert iso_date("whenever") == ""


def test_bounded_returns_results_errors_and_none_for_cutoffs():
    import asyncio
    from iap.sources.run import _bounded

    async def ok():
        return [1]

    async def boom():
        raise ValueError("x")

    async def slow():
        await asyncio.sleep(5)

    ok_r, err_r, slow_r = asyncio.run(_bounded([ok(), boom(), slow()], budget=0.2))
    assert ok_r == [1] and isinstance(err_r, ValueError) and slow_r is None


def test_only_missing_boards_count_as_gone():
    import httpx
    from iap.sources.common import is_gone

    def err(code):
        req = httpx.Request("GET", "https://x")
        return httpx.HTTPStatusError("e", request=req, response=httpx.Response(code, request=req))

    assert is_gone(err(404)) and is_gone(err(422))
    assert not is_gone(err(429)) and not is_gone(err(503)) and not is_gone(httpx.ReadTimeout("t"))


def test_html_to_text_handles_double_escaped_greenhouse_content():
    s = "&lt;p&gt;Build &amp;amp; ship&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;li&gt;C++&lt;/li&gt;&lt;/ul&gt;"
    assert html_to_text(s) == "Build & ship\n- Python\n- C++"

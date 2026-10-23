"""Public ATS endpoints: list a company's internships, and fetch one posting's details.

All endpoints are the same unauthenticated JSON the companies' own career pages call.
"""
import asyncio
import re

from .common import Job, Ref, fetch, html_to_text, iso_date

INTERN_QUERY = "intern"
_INTERNISH = re.compile(r"intern|co-?op|summer|student|apprentice", re.I)  # cheap pre-filter on big boards


async def list_board(c, b) -> list[Job]:
    """b: a boards row (ats, account, host, site, company)."""
    return await LISTERS[b["ats"]](c, b)


def _gh_location(j: dict) -> str:
    """Greenhouse's display location is often vague ("In-Office", "Hybrid"); the real office lives in a
    "Job Posting Location"-style metadata field or, on the detail endpoint, in `offices`."""
    meta = [v for m in j.get("metadata") or [] if "location" in str(m.get("name", "")).lower()
            for v in (m.get("value") if isinstance(m.get("value"), list) else [m.get("value")]) if v]
    offices = [o["name"] for o in j.get("offices") or [] if o.get("name")]
    return "; ".join(dict.fromkeys(meta or offices)) or (j.get("location") or {}).get("name", "")


async def _greenhouse(c, b):
    d = (await fetch(c, "GET", f"https://boards-api.greenhouse.io/v1/boards/{b['account']}/jobs")).json()
    return [
        Job("greenhouse", j.get("company_name") or b["company"], j["title"], j["absolute_url"],
            location=_gh_location(j),
            posted_at=iso_date(j.get("first_published") or j.get("updated_at")),
            deadline=iso_date(j.get("application_deadline")),
            key=Ref("greenhouse", b["account"], str(j["id"])).key)
        for j in d["jobs"] if _INTERNISH.search(j["title"])
    ]


def _lever_job(j, account, company):
    lists = "\n".join(f"{x.get('text', '')}\n{html_to_text(x.get('content', ''))}" for x in j.get("lists", []))
    sal = j.get("salaryRange") or {}
    return Job(
        "lever", company, j["text"], j["hostedUrl"],
        location=(j.get("categories") or {}).get("location", ""),
        description="\n".join(filter(None, [j.get("descriptionPlain"), lists, j.get("additionalPlain")])),
        posted_at=iso_date(j.get("createdAt")),
        salary=f"{sal.get('min')}-{sal.get('max')} {sal.get('currency', '')}/{sal.get('interval', '')}" if sal else "",
        key=Ref("lever", account, j["id"]).key,
    )


async def _lever(c, b):
    d = (await fetch(c, "GET", f"https://api.lever.co/v0/postings/{b['account']}?mode=json")).json()
    return [_lever_job(j, b["account"], b["company"]) for j in d if _INTERNISH.search(j["text"])]


async def _ashby(c, b):
    url = f"https://api.ashbyhq.com/posting-api/job-board/{b['account']}?includeCompensation=true"
    d = (await fetch(c, "GET", url)).json()
    return [
        Job("ashby", b["company"], j["title"], j["jobUrl"],
            location=", ".join(filter(None, [j.get("location")] + [s.get("location") for s in j.get("secondaryLocations", [])])),
            description=j.get("descriptionPlain", ""),
            posted_at=iso_date(j.get("publishedAt")),
            salary=((j.get("compensation") or {}).get("compensationTierSummary") or ""),
            key=Ref("ashby", b["account"], j["id"]).key)
        for j in d["jobs"] if _INTERNISH.search(j["title"])
    ]


async def _workday(c, b, pages: int = 3):
    # ponytail: search is relevance-ranked and capped at 60 hits per site; raise `pages` if big employers get missed
    base = f"https://{b['host']}/wday/cxs/{b['account']}/{b['site']}"
    jobs = []
    for page in range(pages):
        body = {"appliedFacets": {}, "limit": 20, "offset": page * 20, "searchText": INTERN_QUERY}
        d = (await fetch(c, "POST", f"{base}/jobs", json=body)).json()
        hits = [j for j in d.get("jobPostings", []) if "externalPath" in j and _INTERNISH.search(j.get("title", ""))]
        jobs += [Job("workday", b["company"], j["title"], f"https://{b['host']}/{b['site']}{j['externalPath']}",
                     location=j.get("locationsText", ""), posted_at=iso_date(j.get("postedOn")),
                     key=Ref("workday", b["account"], j["externalPath"].rsplit("/", 1)[-1]).key)
                 for j in hits]
        if not hits or (page + 1) * 20 >= d.get("total", 0):
            break  # relevance-ranked: a page with no internships means the rest won't have any
    return jobs


async def _smartrecruiters(c, b):
    url = f"https://api.smartrecruiters.com/v1/companies/{b['account']}/postings?q={INTERN_QUERY}&limit=100&country=us"
    d = (await fetch(c, "GET", url)).json()
    return [
        Job("smartrecruiters", (j.get("company") or {}).get("name") or b["company"], j["name"],
            f"https://jobs.smartrecruiters.com/{b['account']}/{j['id']}",
            location=(j.get("location") or {}).get("fullLocation", ""), posted_at=iso_date(j.get("releasedDate")),
            key=Ref("smartrecruiters", b["account"], j["id"]).key)
        for j in d.get("content", []) if _INTERNISH.search(j["name"])
    ]


async def _oracle(c, b):
    url = (f"https://{b['host']}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
           f"&expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber={b['site']},keyword={INTERN_QUERY},limit=100")
    items = (await fetch(c, "GET", url)).json().get("items") or [{}]
    return [
        Job("oracle", b["company"], j["Title"], f"https://{b['host']}/hcmUI/CandidateExperience/en/sites/{b['site']}/job/{j['Id']}",
            location=j.get("PrimaryLocation", ""), posted_at=iso_date(j.get("PostedDate")),
            key=Ref("oracle", b["account"], j["Id"]).key)
        for j in items[0].get("requisitionList", []) if _INTERNISH.search(j["Title"])
    ]


async def _amazon(c, b):
    url = f"https://www.amazon.jobs/en/search.json?base_query={INTERN_QUERY}&result_limit=100&country=USA&sort=recent"
    d = (await fetch(c, "GET", url)).json()
    return [
        Job("amazon", "Amazon", j["title"], f"https://www.amazon.jobs{j['job_path']}",
            location=j.get("normalized_location") or j.get("location", ""),
            description=html_to_text("\n".join(j.get(k) or "" for k in ("description", "basic_qualifications", "preferred_qualifications"))),
            posted_at=iso_date(j.get("posted_date")), key=Ref("amazon", "amazon", j["id_icims"]).key)
        for j in d.get("jobs", [])
    ]


async def _microsoft(c, b):
    jobs, start = [], 0
    while start < 200:
        url = (f"https://apply.careers.microsoft.com/api/pcsx/search?domain=microsoft.com&query={INTERN_QUERY}"
               f"&location=United%20States&start={start}&num=10")
        d = (await fetch(c, "GET", url)).json()["data"]
        page = d.get("positions") or []
        jobs += [
            Job("microsoft", "Microsoft", p["name"], f"https://apply.careers.microsoft.com/careers/job/{p['id']}",
                location="; ".join(p.get("standardizedLocations") or p.get("locations") or []),
                posted_at=iso_date(p.get("postedTs")), key=Ref("microsoft", "microsoft", str(p["id"])).key)
            for p in page
        ]
        start += len(page)
        if not page or start >= (d.get("count") or 0):
            break
    return jobs


LISTERS = {
    "greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby, "workday": _workday,
    "smartrecruiters": _smartrecruiters, "oracle": _oracle, "amazon": _amazon, "microsoft": _microsoft,
}


_whole_board: dict = {}  # (ats, account) → task listing the board; reset each run


def reset_cache() -> None:
    _whole_board.clear()


async def _board_once(c, ats_name: str, account: str, lister) -> list[Job]:
    """Ashby/Amazon have no single-posting endpoint: list the board once per run, share it across jobs."""
    key = (ats_name, account)
    if key not in _whole_board:
        _whole_board[key] = asyncio.ensure_future(lister(c, {"account": account, "company": ""}))
    return await _whole_board[key]


async def details(c, ref: Ref, url: str) -> dict:
    """Description (and location when known) for one posting. {} if this ATS has no detail endpoint."""
    a = ref
    if a.ats == "greenhouse":
        j = (await fetch(c, "GET", f"https://boards-api.greenhouse.io/v1/boards/{a.account}/jobs/{a.job_id}")).json()
        return {"title": j.get("title", ""), "company": j.get("company_name", ""),
                "description": html_to_text(j.get("content", "")), "location": _gh_location(j)}
    if a.ats == "lever":
        j = (await fetch(c, "GET", f"https://api.lever.co/v0/postings/{a.account}/{a.job_id}")).json()
        job = _lever_job(j, a.account, "")
        return {"title": job.title, "description": job.description, "location": job.location, "salary": job.salary}
    if a.ats == "ashby":
        for job in await _board_once(c, "ashby", a.account, _ashby):
            if job.key == a.key:
                return {"title": job.title, "description": job.description, "location": job.location, "salary": job.salary}
        return {}
    if a.ats == "workday":
        path = url[url.index("/job/"):].split("?")[0].removesuffix("/apply")
        j = (await fetch(c, "GET", f"https://{a.host}/wday/cxs/{a.account}/{a.site}{path}")).json()["jobPostingInfo"]
        return {"title": j.get("title", ""), "description": html_to_text(j.get("jobDescription", "")),
                "location": j.get("location", ""), "posted_at": iso_date(j.get("startDate"))}
    if a.ats == "smartrecruiters":
        j = (await fetch(c, "GET", f"https://api.smartrecruiters.com/v1/companies/{a.account}/postings/{a.job_id}")).json()
        sections = (j.get("jobAd") or {}).get("sections") or {}
        return {"title": j.get("name", ""), "company": (j.get("company") or {}).get("name", ""),
                "description": html_to_text("\n".join((s or {}).get("text", "") for s in sections.values())),
                "location": (j.get("location") or {}).get("fullLocation", "")}
    if a.ats == "oracle":
        u = (f"https://{a.host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all&onlyData=true"
             f'&finder=ById;Id="{a.job_id}",siteNumber={a.site}')
        j = ((await fetch(c, "GET", u)).json().get("items") or [{}])[0]
        text = "\n".join(j.get(k) or "" for k in ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr"))
        return {"title": j.get("Title", ""), "description": html_to_text(text), "location": j.get("PrimaryLocation", "")}
    if a.ats == "microsoft":
        u = f"https://apply.careers.microsoft.com/api/pcsx/position_details?position_id={a.job_id}&domain=microsoft.com&hl=en"
        j = (await fetch(c, "GET", u)).json().get("data") or {}
        return {"title": j.get("name", ""), "company": "Microsoft",
                "description": html_to_text(j.get("jobDescription") or j.get("description") or "")}
    if a.ats == "amazon":
        for job in await _board_once(c, "amazon", "amazon", _amazon):
            if job.key == a.key:
                return {"title": job.title, "company": "Amazon", "description": job.description, "location": job.location}
    return {}

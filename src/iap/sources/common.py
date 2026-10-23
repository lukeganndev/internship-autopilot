"""Shared pieces for sourcing: the Job record, ATS URL parsing (the dedupe backbone), HTTP, HTML→text."""
import asyncio
import html
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx

UA = "internship-autopilot/0.1 (personal job search; github.com/lukeganndev/internship-autopilot)"


@dataclass(frozen=True)
class Ref:
    """A job's identity on its ATS. Same posting from any source → same key."""
    ats: str
    account: str  # board slug / Workday tenant / company id
    job_id: str
    host: str = ""
    site: str = ""

    @property
    def key(self) -> str:
        return f"{self.ats}:{self.account}:{self.job_id}".lower()

    @property
    def board_key(self) -> str:
        return f"{self.ats}:{self.account}:{self.site}".lower()


@dataclass
class Job:
    source: str
    company: str
    title: str
    url: str
    location: str = ""
    description: str = ""
    posted_at: str = ""  # YYYY-MM-DD
    season: str = ""
    salary: str = ""
    active: bool = True
    key: str = ""

    def __post_init__(self):
        self.company, self.title = self.company.strip(), " ".join(self.title.split())
        if not self.key:
            self.key = job_key(self.url)


_LOCALE = re.compile(r"[a-z]{2}-[A-Z]{2}")


def parse_url(url: str) -> Ref | None:
    u = urlsplit(url.strip())
    host = u.netloc.lower().removeprefix("www.")
    parts = [p for p in u.path.split("/") if p]
    while parts and parts[-1].lower() in ("apply", "application", "applymanually"):
        parts.pop()
    q = parse_qs(u.query)
    if host.endswith("greenhouse.io"):
        if "for" in q and "token" in q:
            return Ref("greenhouse", q["for"][0], q["token"][0])
        if len(parts) >= 3 and parts[1] == "jobs":
            return Ref("greenhouse", parts[0], parts[2])
    elif host == "jobs.lever.co" and len(parts) >= 2:
        return Ref("lever", parts[0], parts[1])
    elif host == "jobs.ashbyhq.com" and len(parts) >= 2:
        return Ref("ashby", parts[0], parts[1])
    elif host.endswith("myworkdayjobs.com") and "job" in parts:
        site = [p for p in parts[: parts.index("job")] if not _LOCALE.fullmatch(p)]
        if site:
            return Ref("workday", host.split(".")[0], parts[-1], host, site[-1])
    elif host == "jobs.smartrecruiters.com" and len(parts) >= 2 and (m := re.match(r"\d+", parts[1])):
        return Ref("smartrecruiters", parts[0], m.group())
    elif host.endswith("oraclecloud.com") and "sites" in parts and "job" in parts:
        i = parts.index("job")
        if i + 1 < len(parts):
            return Ref("oracle", host.split(".")[0], parts[i + 1], host, parts[parts.index("sites") + 1])
    elif host == "amazon.jobs" and (m := re.search(r"/jobs/(\d+)", u.path)):
        return Ref("amazon", "amazon", m.group(1))
    elif host.endswith("careers.microsoft.com") and (m := re.search(r"/job/(\d+)", u.path)):
        return Ref("microsoft", "microsoft", m.group(1))
    return None


def job_key(url: str) -> str:
    if ref := parse_url(url):
        return ref.key
    u = urlsplit(url.strip())
    return "url:" + (u.netloc.lower().removeprefix("www.") + u.path.rstrip("/")).lower()


def html_to_text(s: str) -> str:
    s = html.unescape(html.unescape(s or ""))  # Greenhouse double-escapes
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h\d|tr)>", "\n", s)
    s = re.sub(r"(?i)<li[^>]*>", "- ", s)
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    lines = (" ".join(line.split()) for line in s.splitlines())
    return "\n".join(line for line in lines if line)


def iso_date(value) -> str:
    """Epoch seconds/ms, ISO strings, 'Posted 3 Days Ago', 'August 17, 2026', '2d' → YYYY-MM-DD ('' if unknown)."""
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc).date().isoformat()
    s = str(value).strip()
    if m := re.match(r"\d{4}-\d{2}-\d{2}", s):
        return m.group()
    today = date.today()
    if re.search(r"today|just", s, re.I):
        return today.isoformat()
    if re.search(r"yesterday", s, re.I):
        return (today - timedelta(days=1)).isoformat()
    if m := re.search(r"(\d+)\+?\s*(d|day|days|mo|month|months|w|week|weeks)\b", s, re.I):
        n, unit = int(m.group(1)), m.group(2).lower()
        days = n * (30 if unit.startswith("mo") else 7 if unit.startswith("w") else 1)
        return (today - timedelta(days=days)).isoformat()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return ""


_LIMITS: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(6))
_LIMITS["apply.careers.microsoft.com"] = asyncio.Semaphore(2)  # rate-limits (429) at 6
_LIMITS["www.amazon.jobs"] = asyncio.Semaphore(2)

# A board that answers with one of these is gone or misconfigured; anything else (429, 5xx, timeouts) is transient.
GONE = {404, 410, 422}


def is_gone(err: Exception) -> bool:
    return isinstance(err, httpx.HTTPStatusError) and err.response.status_code in GONE


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(15, pool=None), follow_redirects=True, headers={"User-Agent": UA, "Accept": "application/json"},
        limits=httpx.Limits(max_connections=32),
    )


def _limit_key(host: str) -> str:
    # Workday rate-limits per data center (nvidia.wd5... and disney.wd5... share wd5), not per company.
    if m := re.search(r"\.(wd\d+)\.myworkdayjobs\.com$", host):
        return f"workday-{m.group(1)}"
    return host


_LIMITS.update({f"workday-wd{n}": asyncio.Semaphore(4) for n in (1, 3, 5, 12, 103, 108)})


async def fetch(c: httpx.AsyncClient, method: str, url: str, **kw) -> httpx.Response:
    """Polite request: at most 6 in flight per host (4 per Workday data center, 2 for Microsoft/Amazon)."""
    async with _LIMITS[_limit_key(urlsplit(url).netloc)]:
        r = await c.request(method, url, **kw)
        r.raise_for_status()
        return r

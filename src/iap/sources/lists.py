"""Community internship lists on GitHub.

Simplify-format repos publish the listings.json their README is generated from, which is more
reliable than scraping the rendered table. Repos that only have a README get the markdown-table
parser, which maps columns by header name so it works across list formats.
"""
import re

from .common import Job, fetch, iso_date

# (source name, raw listings.json URL)
JSON_LISTS = [
    ("simplify", "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json"),
    ("vanshb03", "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/.github/scripts/listings.json"),
]
# (source name, raw README URL)
README_LISTS = [
    ("speedyapply", "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/README.md"),
]


def from_listings_json(source: str, items: list[dict]) -> list[Job]:
    jobs = []
    for j in items:
        if not j.get("is_visible", True) or not j.get("url"):
            continue
        terms = [t for t in j.get("terms") or [] if t and t != "N/A"]
        jobs.append(Job(
            source, j["company_name"], j["title"], j["url"],
            location="; ".join(j.get("locations") or []),
            posted_at=iso_date(j.get("date_posted")),
            season=", ".join(terms) or (j.get("season") or ""),
            active=bool(j.get("active", True)),
        ))
    return jobs


_HREF = re.compile(r'href="([^"]+)"|\]\((https?://[^)\s]+)\)')


def _cell_text(cell: str) -> str:
    cell = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cell)  # markdown links → text
    cell = re.sub(r"<[^>]+>", " ", cell).replace("**", "")
    return " ".join(cell.split())


def from_markdown_tables(source: str, md: str) -> list[Job]:
    """Parse every table that has company/role/link columns. '↳' rows reuse the previous company."""
    jobs, cols, company = [], None, ""
    for line in md.splitlines():
        if not line.startswith("|"):
            cols = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cols is None:
            names = [_cell_text(c).lower() for c in cells]
            find = lambda *opts: next((i for i, n in enumerate(names) if any(o in n for o in opts)), None)
            cols = {"company": find("company"), "title": find("position", "role", "title"),
                    "location": find("location"), "salary": find("salary", "pay"),
                    "link": find("posting", "application", "apply", "link"), "age": find("age", "date", "posted")}
            if cols["company"] is None or cols["title"] is None:
                cols = {}
            continue
        if not cols or set("".join(cells)) <= set("-: "):
            continue
        get = lambda k: cells[cols[k]] if cols.get(k) is not None and cols[k] < len(cells) else ""
        name = _cell_text(get("company"))
        company = company if name in ("↳", "") else name
        hrefs = [a or b for a, b in _HREF.findall(get("link"))]
        if not hrefs or "🔒" in line:  # closed
            continue
        jobs.append(Job(source, company, _cell_text(get("title")), hrefs[0],
                        location=_cell_text(get("location")), salary=_cell_text(get("salary")),
                        posted_at=iso_date(_cell_text(get("age")))))
    return jobs


async def fetch_lists(c) -> tuple[list[Job], list[str]]:
    jobs, errors = [], []
    for name, url in JSON_LISTS:
        try:
            jobs += from_listings_json(name, (await fetch(c, "GET", url)).json())
        except Exception as e:
            errors.append(f"{name}: {e}")
    for name, url in README_LISTS:
        try:
            jobs += from_markdown_tables(name, (await fetch(c, "GET", url)).text)
        except Exception as e:
            errors.append(f"{name}: {e}")
    return jobs, errors

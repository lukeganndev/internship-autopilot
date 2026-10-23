from datetime import date

import pytest

from iap import filters
from iap.sources.common import Job

TODAY = date(2026, 10, 23)


def job(title="Software Engineering Intern", desc="", location="Austin, TX", season=""):
    return Job("test", "Acme", title, "https://example.com/1", location=location, description=desc, season=season)


def reason(**kw):
    return filters.exclusion(job(**kw), grad_year=2029, today=TODAY)


def test_plain_summer_internship_passes():
    assert reason(title="Software Engineer Intern - Summer 2027") is None


@pytest.mark.parametrize("title, ok", [
    ("Software Engineering Intern", True), ("Security Co-op", True), ("STEP Intern", True),
    ("Software Engineer Program - Summer Analyst", True), ("Internal Audit Analyst", False),
    ("Senior Software Engineer", False),
])
def test_internship_titles(title, ok):
    assert filters.is_internship(title) is ok


def test_grad_level_titles_excluded():
    assert reason(title="PhD Research Intern") == "graduate-level role"
    assert reason(title="MBA Summer Intern") == "graduate-level role"
    assert reason(title="Undergraduate Research Intern") is None
    assert reason(title="2027 Rendezvous and Proximity Operations Grad Intern") == "graduate-level role"
    assert reason(title="Graduate Intern - Commercial Title Underwriting") == "graduate-level role"


@pytest.mark.parametrize("loc, us", [
    ("Austin, TX", True), ("US, CA, Santa Clara", True), ("Remote", True), ("New York, New York", True),
    ("Toronto, ON, CA", False), ("Bengaluru, IN", False), ("London, UK", False), ("China, Shanghai", False),
    ("2 Locations", True), ("Remote - Canada", False), ("Remote, United States", True),
    ("GER-Frankfurt", False), ("HK.Kowloon_Harbour City", False), ("Prejmer, RO", False), ("SGP - Woodlands", False),
    ("Tashkent", False), ("Tijuana, Baja California", False), ("Columbus, IN, United States", True),
    ("Fairfax VA", True), ("Austin,TX", True), ("US-OH-Middleburg Hts. (Nuclear)", True),
])
def test_us_locations(loc, us):
    assert filters.is_us(loc) is us


def test_multi_location_passes_if_any_is_us():
    assert reason(location="Toronto, ON; Seattle, WA") is None
    assert reason(location="Toronto, ON; London, UK") == "outside the US"


def test_unpaid_excluded():
    assert reason(desc="This is an unpaid internship for academic credit.") == "unpaid"


def test_seasons():
    assert reason(season="Fall 2026") == "Fall 2026 already started"
    assert reason(title="Summer 2026 Intern") == "Summer 2026 already started"
    assert reason(season="Fall 2026, Spring 2027") is None
    assert reason(season="Summer 2027") is None
    assert filters.season_of("SWE Intern (Summer '27)") == "Summer 2027"


@pytest.mark.parametrize("desc, excluded", [
    ("Candidates must graduate between December 2026 and June 2028.", "requires graduation by 2028"),
    ("Pursuing a BS with an expected graduation date of May 2029 or later.", None),
    ("Graduating between Dec 2027 and June 2029.", None),
    ("Open to students graduating in 2028 or 2029.", None),
    ("We hire graduating students into our Summer 2027 program.", None),
    ("Must be a rising senior.", "requires senior standing"),
    ("Open to rising juniors and seniors.", None),
    ("Freshmen and sophomores encouraged to apply. Class of 2027 also welcome.", None),
    ("Must be currently a junior or senior in a BS program.", "requires senior standing"),
])
def test_class_year_rules(desc, excluded):
    assert reason(desc=desc) == excluded


def test_flags():
    j = job(title="Cyber Co-op", desc="Must have an active Secret clearance. Sophomores welcome.")
    assert filters.flags(j) == ["sophomore-friendly", "co-op", "clearance required"]
    assert "clearance-eligible" in filters.flags(job(desc="Ability to obtain a security clearance."))


@pytest.mark.parametrize("company, dream", [
    ("Google", True), ("Google LLC", True), ("Meta", True), ("Metabase", False), ("Apple", True),
    ("Applied Materials", False), ("Citi", True), ("Citizens Bank", False), ("JP Morgan Chase", True),
    ("JPMorganChase", True), ("J.P. Morgan", True), ("Amazon Web Services", True), ("NVIDIA", True),
    ("Apple Bank", False), ("Amazon.com Services LLC", True), ("Citibank N.A.", True), ("Meta Platforms, Inc.", True),
    ("NVIDIA Corporation", True), ("Palo Alto Networks", True), ("Google DeepMind", True), ("Cloudflare, Inc.", True),
])
def test_watchlist_matching(company, dream):
    from iap.db import DEFAULT_SETTINGS
    assert filters.is_dream(company, DEFAULT_SETTINGS["watchlist"]) is dream

"""Which saved answer belongs in a form field, and which dropdown option matches it.

Pure functions (no browser), so the mapping is unit-tested. A field is described by its label, the
question around it (for radios), its kind and its options. Anything not confidently matched is left
empty for you, never guessed.
"""
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

SKIP = object()  # "leave this for the human" (salary, pronouns, consent boxes, ...)

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico",
}
COUNTRY_ALIASES = ["united states", "united states of america", "usa", "us", "u.s.", "u.s.a."]
DECLINE = re.compile(r"decline|prefer not|don.?t wish|do not wish|not wish to|choose not|rather not|"
                     r"not to (answer|disclose|say|identify)|i don.?t want|not specified", re.I)
PLACEHOLDER = re.compile(r"^(select|choose|please select|--|—|-)(\b.*)?$", re.I)
LONG_QUESTION = re.compile(r"\?|\bwhy\b|describe|tell us|explain|what (excites|interests|motivates|draws)|share|example", re.I)


@dataclass
class Context:
    answers: dict                       # answers.yaml
    resume_pdf: Path | None = None
    cover_pdf: Path | None = None
    cover_text: str | None = None       # only when you approved it
    approved: list[tuple[str, str]] = field(default_factory=list)  # (question, approved answer)

    def a(self, *path, default=""):
        cur = self.answers
        for p in path:
            cur = cur.get(p) if isinstance(cur, dict) else None
        return default if cur in (None, "") else cur


def _yn(flag) -> str:
    return "Yes" if flag else "No"


def _major(degree: str) -> str:
    m = re.search(r"\bin ([A-Za-z &]+?)(?:,|;|$)", degree or "")
    return m.group(1).strip() if m else ""


# (pattern on the field's text, value). Order matters: first match wins.
RULES = [
    (r"company|employer|current title|job title", lambda c: SKIP),
    (r"preferred (first )?name|nickname|goes by", lambda c: c.a("preferred_name")),
    (r"first name|given name|legal first", lambda c: c.a("legal_first_name")),
    (r"middle (name|initial)", lambda c: SKIP),
    (r"last name|surname|family name|legal last", lambda c: c.a("legal_last_name")),
    (r"school e-?mail|\.edu|university e-?mail|student e-?mail", lambda c: c.a("email_school")),
    (r"e-?mail", lambda c: c.a("email_personal")),
    (r"phone|mobile|telephone|cell", lambda c: c.a("phone")),
    (r"linkedin", lambda c: c.a("links", "linkedin")),
    (r"github", lambda c: c.a("links", "github")),
    (r"portfolio|personal (web)?site|website|other url", lambda c: c.a("links", "portfolio") or c.a("links", "github")),
    (r"pronoun", lambda c: SKIP),
    (r"salary|compensation|pay expectation|desired pay|hourly rate", lambda c: SKIP),
    (r"address line 2|apartment|apt\b|suite|unit\b", lambda c: SKIP),
    (r"current location|where are you (currently )?(located|based)|location \(city", lambda c: f"{c.a('address', 'city')}, {c.a('address', 'state')}"),
    (r"street|address line 1|^address\b|mailing address|home address", lambda c: c.a("address", "street")),
    (r"\bcity\b|\btown\b", lambda c: c.a("address", "city")),
    (r"\bstate\b|province|region", lambda c: c.a("address", "state")),
    (r"zip|postal", lambda c: c.a("address", "zip")),
    (r"country", lambda c: c.a("address", "country", default="United States")),
    (r"hispanic|latin[oa]", lambda c: c.a("eeo", "hispanic_latino")),
    (r"\brace\b|ethnic", lambda c: c.a("eeo", "race_ethnicity")),
    (r"gender|\bsex\b", lambda c: c.a("eeo", "gender")),
    (r"veteran", lambda c: c.a("eeo", "veteran")),
    (r"disabilit", lambda c: c.a("eeo", "disability")),
    (r"sponsor|visa", lambda c: _yn(c.a("needs_sponsorship", default=False))),
    (r"authori[sz]ed to work|legally (authorized|eligible|able) to work|right to work|eligible to work", lambda c: _yn(c.a("authorized_to_work_us", default=True))),
    (r"(active|current|hold|possess)\w*.{0,40}clearance", lambda c: "No"),
    (r"(eligib|able|willing|obtain)\w*.{0,40}clearance", lambda c: _yn(c.a("us_citizen", default=False))),
    (r"u\.?s\.? citizen|citizenship", lambda c: "U.S. Citizen" if c.a("us_citizen", default=False) else SKIP),
    (r"relocat", lambda c: _yn(c.a("willing_to_relocate", default=False))),
    (r"18 years|at least 18|over 18|legal age", lambda c: "Yes"),
    (r"previously (worked|been employed)|former employee|worked (here|for us|at \w+) before|ever been employed by", lambda c: "No"),
    (r"currently enrolled|return to (school|the program|your program|university)|continuing (student|your studies)", lambda c: "Yes"),
    (r"\bgpa\b|grade point", lambda c: c.a("gpa")),
    (r"graduation|graduate (date|year)|expected (completion|grad)|when do you (graduate|expect)", lambda c: c.a("graduation")),
    (r"major|field of study|discipline|area of study|concentration", lambda c: _major(c.a("degree")) or SKIP),
    (r"degree|level of education|education level", lambda c: c.a("degree")),
    (r"school|university|college|institution", lambda c: c.a("school")),
    (r"start date|available to start|earliest start|availability|when can you start", lambda c: c.a("earliest_start")),
    (r"how did you (hear|find|learn)|where did you (hear|find|see)|referral source|\bsource\b", lambda c: c.a("how_did_you_hear")),
    (r"full name|^name\b|legal name|your name|^name$", lambda c: f"{c.a('legal_first_name')} {c.a('legal_last_name')}".strip()),
]
RULES = [(re.compile(p, re.I), fn) for p, fn in RULES]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def approved_answer(text: str, ctx: Context) -> str | None:
    """An approved draft whose question matches this field's label."""
    t = _norm(text)
    best, score = None, 0.0
    for q, a in ctx.approved:
        nq = _norm(q)
        s = 1.0 if (nq and (nq in t or t in nq)) else SequenceMatcher(None, nq, t).ratio()
        if s > score:
            best, score = a, s
    return best if score >= 0.82 else None


def value_for(f: dict, ctx: Context):
    """Value for a collected field, SKIP, or None (no idea → leave for the human, highlight if required)."""
    kind, label = f["kind"], f.get("label") or ""
    text = f"{f.get('question') or ''} {label}".strip() if kind in ("radio", "checkbox", "file") else label
    low = text.lower()

    if kind == "file":  # `question` holds the upload box's surrounding text here
        if re.search(r"cover", low):
            return str(ctx.cover_pdf) if ctx.cover_pdf else None
        if re.search(r"resume|\bcv\b|curriculum", low):
            return str(ctx.resume_pdf) if ctx.resume_pdf else None
        return None  # transcript, portfolio, or an upload we can't identify: yours
    if kind == "checkbox":
        return SKIP  # consent/acknowledgement boxes are yours to tick
    if re.search(r"cover letter", low):
        return ctx.cover_text or None
    if kind == "textarea" or (kind == "text" and len(label) > 40 and LONG_QUESTION.search(label)):
        if found := approved_answer(text, ctx):
            return found
        if kind == "textarea":
            return None  # free-text question with no approved answer: never improvise
    for rx, fn in RULES:
        if rx.search(text):
            return fn(ctx)
    return None


def pick_option(options: list[str], desired: str) -> str | None:
    """The option text that means `desired`, or None. Exact beats prefix beats whole-word beats fuzzy."""
    opts = [o for o in options if o and o.strip() and not PLACEHOLDER.match(o.strip())]
    if not opts or not desired:
        return None
    d = _norm(desired)
    if DECLINE.search(desired):
        return next((o for o in opts if DECLINE.search(o)), None)
    norms = [(_norm(o), o) for o in opts]
    if d in ("yes", "no"):
        return next((o for n, o in norms if n == d or n.startswith(d + " ")), None)

    wanted = {d}
    if desired.strip().upper() in STATES:
        wanted.add(_norm(STATES[desired.strip().upper()]))
    elif (abbr := next((k for k, v in STATES.items() if _norm(v) == d), None)):
        wanted.add(abbr.lower())
    if d in COUNTRY_ALIASES:
        wanted |= {_norm(a) for a in COUNTRY_ALIASES}
    if re.match(r"(b\.?s\.?|b\.?a\.?|bachelor)", desired.strip(), re.I):
        wanted |= {"bachelor s degree", "bachelors", "bachelor s", "bachelor of science", "bachelor", "undergraduate"}
    if re.search(r"careers? (website|site|page)", desired, re.I):
        # "Company Website", "Cloudflare Careers Site": one name + website. Not "Career Fair or Career Website".
        if hit := next((o for n, o in norms if re.fullmatch(r"(company|\w+) (careers? )?(website|site|page)", n)), None):
            return hit
    if "citizen" in d:
        wanted |= {"u s citizen", "us citizen", "citizen", "united states citizen"}

    for test in (lambda n: n in wanted,
                 lambda n: any(n.startswith(w + " ") or w.startswith(n + " ") for w in wanted if len(n) > 2),
                 lambda n: any(re.search(rf"\b{re.escape(w)}\b", n) for w in wanted if len(w) > 2)):
        if hit := next((o for n, o in norms if test(n)), None):
            return hit
    if years := re.findall(r"\b20\d\d\b", desired):  # "May 2029" vs a year-only dropdown
        if hit := next((o for n, o in norms if years[0] in n and (n == years[0] or _norm(desired.split()[0]) in n)), None):
            return hit
        if hit := next((o for n, o in norms if n == years[0]), None):
            return hit
    if "," in desired:  # "Tuscaloosa, AL" vs an autocomplete's "Tuscaloosa, Alabama, United States"
        city = _norm(desired.split(",")[0])
        if hit := next((o for n, o in norms if n == city or n.startswith(city + " ")), None):
            return hit
    best = max(norms, key=lambda no: SequenceMatcher(None, no[0], d).ratio())
    return best[1] if SequenceMatcher(None, best[0], d).ratio() >= 0.8 else None

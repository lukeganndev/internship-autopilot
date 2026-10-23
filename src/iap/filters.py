"""Hard eligibility filters (a job either qualifies or gets an exclusion reason) and soft flags.

Fit *scoring* is Phase 3; this only removes what can't work: non-internships, grad-level roles,
non-US, unpaid, seasons already started, and explicit class-year requirements.
"""
import re
from datetime import date

INTERNSHIP = re.compile(r"\bintern(s|ship|ships)?\b|\bco-?op\b|summer (analyst|associate|engineer|program)|\bstep\b|explore program", re.I)
GRAD_LEVEL = re.compile(r"\b(ph\.?\s?d|mba|master'?s|graduate student|postdoc\w*|doctoral)\b", re.I)

_STATE_NAMES = ("Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|"
                "Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|"
                "Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|"
                "North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|"
                "South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming")
US_STRONG = re.compile(rf"\b(?:US|USA|U\.S\.A?\.?)\b|(?i:united states|\bnyc\b|bay area|\b(?:{_STATE_NAMES})\b)")
NON_US = re.compile(
    r"\b(canada|ontario|toronto|montr[eé]al|qu[eé]bec|british columbia|alberta|calgary|waterloo|ottawa|"
    r"united kingdom|\buk\b|london|england|scotland|ireland|dublin|germany|berlin|munich|france|paris|netherlands|"
    r"amsterdam|spain|madrid|barcelona|italy|milan|switzerland|zurich|sweden|stockholm|poland|warsaw|krak[oó]w|"
    r"romania|czech|prague|israel|tel aviv|india|bangalore|bengaluru|hyderabad|pune|chennai|mumbai|delhi|noida|"
    r"gurgaon|china|shanghai|beijing|shenzhen|hong kong|taiwan|taipei|hsinchu|japan|tokyo|korea|seoul|singapore|"
    r"malaysia|vietnam|philippines|australia|sydney|melbourne|mexico|guadalajara|brazil|s[aã]o paulo|argentina|"
    r"colombia|costa rica|chile|emea|apac|latam|indonesia|jakarta|thailand|bangkok|uzbekistan|tashkent|frankfurt|"
    r"hamburg|stuttgart|kowloon|macau|manila|kuala lumpur|penang|hanoi|ho chi minh|tijuana|monterrey|baja california|"
    r"hungary|budapest|portugal|lisbon|belgium|brussels|austria|denmark|copenhagen|norway|oslo|finland|helsinki|"
    r"turkey|istanbul|egypt|cairo|south africa|nigeria|kenya|pakistan|bangladesh|dubai|abu dhabi|saudi|qatar|"
    r"new zealand|auckland|lima|peru|bogot[aá]|buenos aires)\b", re.I)
# Country codes as Workday/Oracle write them ("GER-Frankfurt", "HK.Kowloon", "Prejmer, RO"). Case-sensitive;
# codes that collide with US places (IN=Indiana, CA, AUS=Austin airport) are deliberately left out.
NON_US_CODE = re.compile(r"(?<![A-Za-z])(GER|DEU|HK|RO|SGP|CHN|CAN|MEX|GBR|FRA|ESP|ITA|POL|JPN|KOR|TWN|ISR|IRL|NLD|CHE|"
                         r"SWE|BRA|ARG|PHL|MYS|THA|IDN|VNM|NZL|ARE|CZE|HUN|PRT|BEL|AUT|DNK|NOR|FIN|TUR|ZAF)(?![A-Za-z])")

UNPAID = re.compile(r"\bunpaid\b|academic credit only|for (?:college |academic )?credit only|no (?:monetary )?compensation|"
                    r"volunteer (?:position|role|opportunity|internship)", re.I)
SOPH_OK = re.compile(r"sophomore|rising junior|first[- ]year|freshm[ae]n|underclass|all (?:class )?years|second[- ]year", re.I)
UPPER_ONLY = re.compile(r"rising senior|senior standing|final[- ]year|completed (?:your |their |the )?junior year|"
                        r"current(?:ly)? (?:an? )?(?:junior|senior)\b|(?:junior|senior)s? only", re.I)
GRAD_WINDOW = re.compile(r"(?:graduat\w*|class of)[^.;\n]{0,90}", re.I)
LOWER_BOUND = re.compile(r"or later|or after|\bafter\b|beyond|no earlier|onward", re.I)
YEAR_NOT_SEASON = re.compile(r"(?<!summer )(?<!fall )(?<!spring )(?<!winter )\b(20\d\d)\b", re.I)
SEASON = re.compile(r"\b(summer|fall|autumn|spring|winter)\s*(?:of\s*)?'?(20\d\d|\d\d)\b", re.I)
SEASON_START = {"spring": 1, "summer": 5, "fall": 8, "autumn": 8, "winter": 12}
CLEARANCE_REQ = re.compile(r"(?:active|current|existing)\s+(?:ts|top secret|secret|security)(?:/sci)?\s+clearance|"
                           r"must (?:possess|hold|have) an? (?:active )?(?:\w+ )?clearance|clearance (?:is )?required", re.I)
CLEARANCE_ELIG = re.compile(r"(?:ability|able|eligib\w*) to obtain (?:and maintain )?an? (?:\w+ )?(?:security )?clearance", re.I)
COOP = re.compile(r"\bco-?op\b", re.I)


def is_internship(title: str) -> bool:
    return bool(INTERNSHIP.search(title))


def is_us(location: str) -> bool:
    """Explicit US wins; then any foreign place loses (", CA"/", IN" can be Canada/India); unknown → US."""
    location = re.sub(r"(?i)baja california", "Mexico", location)  # the one foreign region named after a state
    if not location.strip() or US_STRONG.search(location):
        return True
    # ponytail: unknown places default to US; add names/codes to NON_US(_CODE) as they show up
    return not (NON_US.search(location) or NON_US_CODE.search(location))


def any_us(location: str) -> bool:
    return any(is_us(loc) for loc in re.split(r";|\n| / ", location or ""))


def _terms(text: str) -> list[tuple[str, int]]:
    return [(n.lower().replace("autumn", "fall"), int(y if len(y) == 4 else "20" + y)) for n, y in SEASON.findall(text or "")]


def season_of(*texts: str) -> str:
    for t in texts:
        if terms := _terms(t):
            return ", ".join(f"{n.title()} {y}" for n, y in dict.fromkeys(terms))
    return ""


def season_started(season: str, today: date) -> bool:
    """True when every term named has already begun (e.g. 'Fall 2026' in late October 2026)."""
    terms = _terms(season)
    return bool(terms) and all(
        date(y - (n == "winter"), SEASON_START[n], 1) < today.replace(day=1) for n, y in terms
    )


def grad_deadline(text: str) -> int | None:
    """Latest graduation year a posting allows, if it states an upper bound."""
    years = []
    for m in GRAD_WINDOW.finditer(text):
        if not LOWER_BOUND.search(m.group()):
            years += [int(y) for y in YEAR_NOT_SEASON.findall(m.group())]
    return max(years) if years else None


def exclusion(job, grad_year: int | None, today: date | None = None) -> str | None:
    today = today or date.today()
    text = f"{job.title}\n{job.description}"
    if not is_internship(job.title):
        return "not an internship"
    if GRAD_LEVEL.search(job.title):
        return "graduate-level role"
    if not any_us(job.location):
        return "outside the US"
    if UNPAID.search(text):
        return "unpaid"
    if season_started(season := job.season or season_of(job.title), today):
        return f"{season} already started"
    if not SOPH_OK.search(text):
        if grad_year and (deadline := grad_deadline(text)) and deadline < grad_year:
            return f"requires graduation by {deadline}"
        if UPPER_ONLY.search(text):
            return "requires senior standing"
    return None


def flags(job) -> list[str]:
    text = f"{job.title}\n{job.description}"
    out = []
    if SOPH_OK.search(text):
        out.append("sophomore-friendly")
    if COOP.search(job.title):
        out.append("co-op")
    if CLEARANCE_REQ.search(text):
        out.append("clearance required")
    elif CLEARANCE_ELIG.search(text):
        out.append("clearance-eligible")
    if not job.description:
        out.append("no description")
    return out


# Words allowed after a watchlist name: "Amazon Web Services", "Meta Platforms", "Google LLC" match,
# but "Apple Bank" or "Metabase" don't.
CORPORATE_SUFFIX = set("inc llc ltd corp corporation co com company group holdings plc na n a us usa the "
                       "platforms web services technologies technology systems networks deepmind research".split())


def _words(s: str) -> list[str]:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).split()


def is_dream(company: str, watchlist: list[str]) -> bool:
    name = _words(company)
    for entry in watchlist:
        for alias in filter(None, (_words(a) for a in entry.split("|"))):
            if name[: len(alias)] == alias and set(name[len(alias):]) <= CORPORATE_SUFFIX:
                return True
    return False

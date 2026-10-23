"""Fit score 0-100 for an eligible job against the master profile, with a reason for every point.

  role fit      0-40   what kind of job the title (or description) says it is
  skills        0-30   tech terms the description asks for vs. what the profile has
  class year    0-20   sophomore-friendly vs. grad-student-leaning
  season        0-10   Summer 2027 best, co-ops low
  clearance     -10    an *active* clearance is required

Rule-based on purpose: free to run on every job every 2 hours, and every score is explainable.
"""
import json
import re
from datetime import date

from . import db

# Calibrated on ~11k real postings (Oct 2026): 60+ is roughly "a technical internship you could get";
# the 50s were mostly mixed-track or non-CS engineering postings.
THRESHOLDS = {"strict": 70, "balanced": 60, "broad": 45}

# (category, points, title pattern). First match wins, so order matters.
ROLES = [
    ("security", 40, r"secur|cyber|infosec|\bsoc\b|threat|vulnerab|penetration|pen ?test|red team|blue team|"
                     r"incident response|forensic|malware|cryptograph|appsec|offensive|detection engineer"),
    ("ai/ml/data", 40, r"machine learning|\bml\b|\bai\b|artificial intelligence|data scien|data engineer|deep learning|"
                       r"\bnlp\b|computer vision|\bllm|applied scien|research engineer|data analy|analytics engineer"),
    ("embedded/hardware", 40, r"embedded|firmware|hardware|\basic\b|fpga|vlsi|\brtl\b|design verification|silicon|"
                              r"semiconductor|electrical|electronics|\bpcb\b|robotics|signal processing|\bdsp\b|soc design|chip"),
    ("software", 40, r"software|\bswe\b|\bsde\b|developer|full[- ]?stack|back[- ]?end|front[- ]?end|\bweb\b|mobile|"
                     r"\bios\b|android|platform|devops|site reliability|\bsre\b|cloud|infrastructure|programmer|"
                     r"computer science|\bcoding\b|application engineer|\bstep\b|explore (program|microsoft)"),
    ("quant", 25, r"quant|trading|trader|algorithmic"),
    ("it/networking", 20, r"\bit\b|information technology|help ?desk|network|system administrat|technical support|tech support"),
    ("technical product", 15, r"technical program|product manag|program manag|solutions engineer|sales engineer"),
    ("other engineering", 5, r"mechanical|civil|chemical|structural|manufacturing|process engineer|industrial|"
                             r"environmental|biomedical|aerospace|materials|nuclear|petroleum|mining|quality|"
                             r"field engineer|facilities|construction|project engineer|optical|packaging|reliability"),
    ("general engineering", 15, r"systems engineer|engineering intern|engineer intern|technology intern|\btech\b"),
]
ROLE_RES = [(name, pts, re.compile(p, re.I)) for name, pts, p in ROLES]
# "programming" alone also means TV/event programming, so the fallback needs an unambiguous phrase.
TECH_IN_DESC = re.compile(r"software (engineering|development|engineer)|writing code|computer science", re.I)
RESEARCH_TITLE = re.compile(r"research scientist|research intern|\bscientist\b", re.I)
GRAD_LEANING = re.compile(r"(currently )?pursuing an? (ph\.?d|master'?s|doctoral)|ph\.?d\.? (student|candidate)|"
                          r"master'?s or ph\.?d|ms/ph\.?d|graduate degree (program|student)", re.I)
BACHELORS = re.compile(r"bachelor'?s|\bb\.?s\.?\b|undergraduate|\bbs/ms\b", re.I)
STUDENT_PROGRAMS = re.compile(r"\bstep\b|explore (program|microsoft)|first[- ]year|sophomore|freshm", re.I)

# Tech terms worth matching. Each: canonical name → regex. Single letters (C, R, Go) are left out on purpose:
# they false-match ordinary text ("Section C", "go-getter").
_TERMS = {
    "Python": r"python", "Java": r"java(?!script)", "C++": r"c\+\+", "C#": r"c#|\.net", "JavaScript": r"javascript|\bjs\b",
    "TypeScript": r"typescript", "Golang": r"golang", "Rust": r"\brust\b", "Kotlin": r"kotlin", "Swift": r"\bswift\b",
    "SQL": r"\bsql\b|postgres|mysql|sqlite", "Bash": r"\bbash\b|shell script", "PowerShell": r"powershell",
    "Lua": r"\blua\b", "MATLAB": r"matlab", "Verilog": r"verilog|systemverilog", "VHDL": r"vhdl", "Assembly": r"assembly language|\bassembly\b",
    "React": r"\breact\b", "Next.js": r"next\.js|nextjs", "Node.js": r"node\.js|nodejs", "HTML/CSS": r"\bhtml\b|\bcss\b",
    "Django": r"django", "Flask": r"flask", "FastAPI": r"fastapi", "Spring": r"\bspring\b", "Tailwind CSS": r"tailwind",
    "AWS": r"\baws\b|amazon web services", "Azure": r"\bazure\b", "GCP": r"\bgcp\b|google cloud", "Docker": r"docker|container",
    "Kubernetes": r"kubernetes|\bk8s\b", "Linux": r"linux|unix", "Git": r"\bgit\b|github|gitlab|version control",
    "REST APIs": r"\brest(ful)?\b|\bapis?\b", "CI/CD": r"ci/cd|continuous integration", "Testing": r"unit test|pytest|junit|test automation",
    "Machine Learning": r"machine learning|\bml\b", "PyTorch": r"pytorch", "TensorFlow": r"tensorflow", "LLMs": r"\bllms?\b|large language model",
    "Data Analysis": r"data analy|pandas|numpy", "Statistics": r"statistic", "Linear Algebra": r"linear algebra",
    "Networking": r"tcp/ip|networking|\bdns\b|\bhttp\b", "Wireshark": r"wireshark", "Nmap": r"\bnmap\b", "Burp Suite": r"burp",
    "SIEM": r"\bsiem\b|splunk", "Cryptography": r"cryptograph|encryption", "Penetration Testing": r"penetration test|pen ?test",
    "Microcontrollers": r"microcontroller|\bmcu\b|arduino|\bstm32\b|\barm\b", "Embedded C": r"embedded c\b|bare[- ]metal|rtos",
    "Digital Logic": r"digital logic|digital design|logic design", "Circuits": r"circuit|schematic|oscilloscope|breadboard",
    "FPGA": r"fpga", "PCB Design": r"\bpcb\b|altium|kicad", "Data Structures": r"data structures?", "Algorithms": r"algorithms?",
    "Object-Oriented Programming": r"object[- ]oriented|\boop\b", "Software Engineering": r"software engineering|software development life",
    "Agile": r"\bagile\b|scrum", "Discrete Math": r"discrete math",
}
_TERM_RES = {name: re.compile(p, re.I) for name, p in _TERMS.items()}


def terms_in(text: str) -> set[str]:
    return {name for name, rx in _TERM_RES.items() if rx.search(text)}


def profile_terms(p: dict) -> set[str]:
    """Every tech term the profile can honestly back up: skills, project tech, coursework, bullet text."""
    words = [s for v in p["skills"].values() for s in v]
    words += [t for x in p["projects"] for t in x.get("tech", [])]
    words += [c for e in p["education"] for c in e.get("coursework", [])]
    words += [b["text"] for sec in ("projects", "experience", "activities") for x in p[sec] for b in x["bullets"]]
    have = terms_in("\n".join(words))
    if any(re.search(r"\bc\b|c\+\+", w, re.I) for w in p["skills"].get("Languages", [])):
        have.add("C++")
    return have


def role_of(title: str, description: str) -> tuple[str, int, str]:
    for name, pts, rx in ROLE_RES:
        if rx.search(title):
            return name, pts, f"+{pts} role: {name}"
    if TECH_IN_DESC.search(description[:3000]):
        return "software", 25, "+25 role: likely software (from the description; title is generic)"
    return "non-technical", 0, "+0 role: not a technical role"


def recruiting_summer(today: date | None = None) -> int:
    """The summer being recruited for now: from September on, it's next year's."""
    today = today or date.today()
    return today.year + (today.month >= 9)


def score(job, have: set[str]) -> tuple[int, list[str], list[str]]:
    """Returns (score, reasons, missing skills). `job` needs title, description, season, flags (list)."""
    reasons, flags = [], set(job.flags)
    role, pts, why = role_of(job.title, job.description)
    total = pts
    reasons.append(why)

    if job.description:
        wanted = terms_in(job.description)
        matched, missing = sorted(wanted & have), sorted(wanted - have)
        if wanted:
            skill = round(30 * min(1.0, len(matched) / max(4, min(len(wanted), 10))))
            reasons.append(f"+{skill} skills: {len(matched)} of {len(wanted)} matched"
                           + (f" ({', '.join(matched[:8])})" if matched else ""))
        else:
            skill = 15
            reasons.append("+15 skills: no specific tech listed")
    else:
        skill, missing = 15, []
        reasons.append("+15 skills: no description available, not checked")
    total += skill

    text = f"{job.title}\n{job.description}"
    if "sophomore-friendly" in flags or STUDENT_PROGRAMS.search(job.title):
        year, why = 20, "+20 class year: open to sophomores / early-career program"
    elif GRAD_LEANING.search(job.description) and not BACHELORS.search(job.description):
        year, why = 4, "+4 class year: written for grad students"
    elif RESEARCH_TITLE.search(job.title):
        year, why = 6, "+6 class year: research roles usually go to grad students"
    else:
        year, why = 12, "+12 class year: no class-year preference stated"
    total += year
    reasons.append(why)

    season = (job.season or "").lower()
    target = recruiting_summer()
    later = [int(y) for y in re.findall(r"20\d\d", season) if int(y) > target]
    if "co-op" in flags:
        s, why = 2, "+2 season: co-op (you asked to skip these unless the fit is great)"
    elif f"summer {target}" in season or season == "summer":
        s, why = 10, f"+10 season: Summer {target}"
    elif later and not re.search(rf"\b{target}\b", season):
        s, why = 0, f"+0 season: {job.season} is next year's recruiting cycle"
    elif not season:
        s, why = 6, "+6 season: not stated (most current postings are summer)"
    else:
        s, why = 4, f"+4 season: {job.season} (during the school year)"
    total += s
    reasons.append(why)

    if "clearance required" in flags:
        total -= 10
        reasons.append("-10 requires an active security clearance")
    elif "clearance-eligible" in flags:
        reasons.append("+0 clearance-eligible: you qualify as a US citizen")

    return max(0, min(100, total)), reasons, missing[:12]


def threshold(con) -> int:
    t = db.get_setting(con, "threshold")
    return THRESHOLDS.get(t, t) if isinstance(t, str) else int(t)


class _Row:
    """Adapter so score() reads a jobs row like a Job."""
    def __init__(self, r):
        self.title, self.description, self.season = r["title"], r["description"], r["season"]
        self.flags = json.loads(r["flags"])


def score_all(con, profile: dict) -> dict:
    """(Re)score every active eligible job. ponytail: rescores all ~12k each run (a few seconds);
    track a profile hash + job updated_at if this ever gets slow."""
    have = profile_terms(profile)
    rows = con.execute("SELECT * FROM jobs WHERE active=1 AND excluded_reason IS NULL").fetchall()
    out = []
    for r in rows:
        s, reasons, missing = score(_Row(r), have)
        out.append((r["id"], s, json.dumps(reasons), json.dumps(missing)))
    con.execute("DELETE FROM scores WHERE job_id NOT IN (SELECT id FROM jobs WHERE active=1 AND excluded_reason IS NULL)")
    con.executemany(
        """INSERT INTO scores(job_id, score, reasons, missing, scored_at) VALUES (?,?,?,?,datetime('now'))
           ON CONFLICT(job_id) DO UPDATE SET score=excluded.score, reasons=excluded.reasons,
             missing=excluded.missing, scored_at=excluded.scored_at""", out)
    con.commit()
    t = threshold(con)
    return {"scored": len(out), "threshold": t, "queued": sum(s >= t for _, s, _, _ in out)}


def queue(con, limit: int = 50) -> list:
    """Jobs waiting for a decision: score at/above the threshold, or any dream company."""
    return con.execute(
        """SELECT j.*, s.score, s.reasons, s.missing FROM jobs j JOIN scores s ON s.job_id = j.id
           LEFT JOIN applications a ON a.job_id = j.id
           WHERE j.active=1 AND j.excluded_reason IS NULL AND a.job_id IS NULL AND (s.score >= ? OR j.is_dream=1)
           ORDER BY j.is_dream DESC, s.score DESC, j.first_seen DESC LIMIT ?""", (threshold(con), limit)).fetchall()

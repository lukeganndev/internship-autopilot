"""Phase 5: open an application in a visible browser, fill it from your saved answers, and STOP.

Hard rules, enforced here rather than trusted to each adapter:
- Nothing is ever submitted. Every click goes through safe_click(), which refuses submit-type
  elements and any button whose text says submit/apply/next/continue/review/save/sign up/create.
  Enter is never pressed. You move between steps and you click Submit.
- No accounts are created and CAPTCHAs are never touched: the run pauses, tells you, and resumes
  once you've dealt with it.
- Only empty fields are filled; consent checkboxes and anything uncertain are left for you and
  highlighted. Free-text questions only get answers you approved in the dashboard; new ones are
  saved so the next tailoring batch drafts them.
"""
import json
import re
import time
from pathlib import Path

import yaml
from playwright.sync_api import Error as PWError
from playwright.sync_api import sync_playwright

from .. import config, db, notify, resume
from .. import profile as prof
from ..sources.common import parse_url
from .fields import LONG_QUESTION, SKIP, Context, pick_option, value_for

JS = (Path(__file__).parent / "page.js").read_text(encoding="utf-8")
SUBMITLIKE = re.compile(r"\b(submit|send|apply|finish|complete|confirm|next|continue|review|save|sign ?up|register|create)\b", re.I)
PAUSES = {
    "login": "This site wants you to sign in. Sign in yourself (this browser remembers it next time); autofill continues after.",
    "account": "This site wants a new account. Autofill never creates accounts: create it yourself if you want to apply, then autofill continues.",
    "captcha": "There's a CAPTCHA. Solve it yourself; autofill never touches CAPTCHAs.",
}


class SubmitGuard(Exception):
    pass


# ---------- adapters: where each portal's form lives ----------

def form_url(url: str) -> tuple[str, str]:
    """(url to open, portal name). Greenhouse/Lever/Ashby forms open directly; Workday, iCIMS and others
    start on the posting and you click Apply (autofill fills every step as it appears)."""
    ref = parse_url(url)
    ats = ref.ats if ref else ""
    if ats == "greenhouse":
        return f"https://job-boards.greenhouse.io/{ref.account}/jobs/{ref.job_id}", "greenhouse"
    if ats == "lever":
        return f"https://jobs.lever.co/{ref.account}/{ref.job_id}/apply", "lever"
    if ats == "ashby":
        return f"https://jobs.ashbyhq.com/{ref.account}/{ref.job_id}/application", "ashby"
    if ats == "workday":
        return url, "workday"
    if "icims.com" in url:
        return url, "icims"
    return url, ats or "generic"


START_HINT = {
    "workday": "Click Apply (then Apply Manually or Use My Last Application) and sign in if asked. I fill each step as it appears; you click Next and Submit.",
    "icims": "Click Apply and sign in if asked. I fill each step as it appears; you click Next and Submit.",
    "generic": "If this is the job posting rather than the form, click Apply yourself. I fill what I can recognize and highlight the rest.",
}


# ---------- the only way anything gets clicked ----------

def safe_click(loc) -> None:
    info = loc.evaluate("""e => ({tag: e.tagName, type: (e.getAttribute('type') || '').toLowerCase(),
        role: e.getAttribute('role') || '', text: (e.innerText || e.value || '').trim().slice(0, 80),
        aria: e.getAttribute('aria-label') || ''})""")
    buttonish = info["tag"] in ("BUTTON", "A") or info["role"] in ("button", "link") or info["type"] in ("submit", "button", "image")
    if info["type"] == "submit" or (buttonish and SUBMITLIKE.search(f"{info['text']} {info['aria']}")):
        raise SubmitGuard(f"refused to click {info['text'] or info['aria']!r}")
    loc.click(timeout=3000)


def _pick_visible_option(frame, value: str) -> bool:
    opts = frame.locator("[role=option]:visible")
    texts = [t.strip() for t in opts.all_inner_texts()]
    choice = pick_option(texts, value)
    if choice is None:
        return False
    safe_click(opts.nth(texts.index(choice)))
    return True


def put(frame, f: dict, value) -> bool:
    """Write one value into one field. Returns whether it stuck."""
    loc = frame.locator(f'[data-iap-id="{f["id"]}"]')
    kind = f["kind"]
    if kind == "file":
        loc.set_input_files(value)
    elif kind in ("text", "textarea"):
        loc.fill(str(value))
    elif kind == "date":
        return False  # formats vary too much to guess; you pick the date
    elif kind == "select":
        choice = pick_option(f["options"] or [], str(value))
        if choice is None:
            return False
        loc.select_option(label=choice)
    elif kind == "combobox":
        # Open the list and pick from it first; typing filters options by *their* wording, so the full
        # saved phrase can hide the right one ("Company careers website" vs "Cloudflare Website").
        safe_click(loc)
        frame.wait_for_timeout(500)
        if not _pick_visible_option(frame, str(value)):
            for typed in dict.fromkeys([str(value), str(value).split(",")[0].split()[0]]):
                loc.fill(typed)
                frame.wait_for_timeout(700)
                if _pick_visible_option(frame, str(value)):
                    break
            else:
                loc.fill("")
                loc.press("Escape")
                return False
    elif kind == "listbox":
        safe_click(loc)
        frame.wait_for_timeout(500)
        if not _pick_visible_option(frame, str(value)):
            loc.press("Escape")
            return False
    else:
        return False
    return True


def fill_frame(frame, ctx: Context, state: dict) -> None:
    frame.evaluate(JS)
    fields = frame.evaluate("() => window.__iap.collect()")
    done, needs = [], []

    radios: dict[str, list[dict]] = {}
    for f in fields:
        if f["kind"] == "radio":
            radios.setdefault(f["group"] or f["question"], []).append(f)
            continue
        if f["filled"]:
            done.append(f["id"])
            continue
        if f["id"] in state["tried"]:  # already attempted: just wait for you to fill it
            continue
        state["tried"].add(f["id"])
        value = value_for(f, ctx)
        ok = False
        if value not in (None, "", SKIP):
            try:
                ok = put(frame, f, value)
            except SubmitGuard as e:
                state["guarded"].append(str(e))
            except PWError:
                ok = False
        if ok:
            done.append(f["id"])
            state["filled"] += 1
            continue
        label = f["label"] or f["question"] or "(unlabeled field)"
        question = f["kind"] in ("textarea", "text") and value is None and len(label) > 25 and bool(LONG_QUESTION.search(label))
        if question:
            state["new_questions"].add(label.rstrip(" *"))
        if f["required"] or question:
            needs.append(f["id"])
            state["needs"][f["id"]] = label[:90]

    for group in radios.values():
        if any(r["filled"] for r in group):
            done += [r["id"] for r in group]
            continue
        if group[0]["id"] in state["tried"]:
            continue
        state["tried"].add(group[0]["id"])
        value = value_for(group[0], ctx)
        choice = pick_option([r["label"] for r in group], str(value)) if value not in (None, "", SKIP) else None
        if choice is not None:
            try:
                frame.locator(f'[data-iap-id="{group[[r["label"] for r in group].index(choice)]["id"]}"]').check(force=True, timeout=3000)
                done += [r["id"] for r in group]
                state["filled"] += 1
                continue
            except PWError:
                pass
        if any(r["required"] for r in group) or value is None:
            needs += [r["id"] for r in group]
            state["needs"][group[0]["id"]] = (group[0]["question"] or group[0]["label"])[:90]

    frame.evaluate("([d, n]) => { window.__iap.mark(d, false); window.__iap.mark(n, true); }", [done, needs])
    for f in fields:  # anything you've filled since drops off the "needs you" list
        if f["id"] in done or (f["kind"] == "radio" and any(r["filled"] for r in radios.get(f["group"] or f["question"], []))):
            state["needs"].pop(f["id"], None)


# ---------- one tick of the watch loop ----------

def _each_frame(page, script: str):
    for frame in page.frames:
        try:
            frame.evaluate(JS)
            yield frame, frame.evaluate(script)
        except PWError:
            continue


def step(page, con, job, ctx: Context, state: dict, portal: str) -> None:
    blocker = next((b for _, b in _each_frame(page, "() => window.__iap.blockers()") if b), "")
    if blocker:
        page.evaluate("m => window.__iap.banner(m, 'stop')", PAUSES[blocker])
        if blocker not in state["notified"]:
            state["notified"].add(blocker)
            notify.toast("Autofill paused", PAUSES[blocker])
        return
    state["notified"].clear()

    if any(a for _, a in _each_frame(page, "() => window.__iap.applied()")):
        if not state["applied"]:
            state["applied"] = True
            from ..mail import advance  # same logged, undoable path as email-driven changes
            advance(con, job["id"], "applied", f"confirmation page {page.url}", source="autofill")
            con.commit()
            notify.toast("Marked as applied", f"{job['company']}: {job['title']}")
        page.evaluate("m => window.__iap.banner(m, 'ok')", "Looks submitted. Marked as applied in your tracker. You can close this window.")
        return

    for frame in page.frames:
        try:
            fill_frame(frame, ctx, state)
        except PWError:
            continue
    _save_new_questions(con, job["id"], state)

    items = list(state["needs"].values())[:12]
    if state["new_questions"]:
        items.append(f"{len(state['new_questions'])} new question(s) saved; they'll be drafted in the next tailoring batch (or click Re-tailor).")
    if state["guarded"]:
        items.append(f"Safety: {len(state['guarded'])} click(s) refused because they looked like submit/next buttons.")
    hint = START_HINT.get(portal, "") if not state["filled"] else ""
    msg = (f"Filled {state['filled']} field(s). " + (f"{len(state['needs'])} need you (orange). " if state["needs"] else "")
           + "Review everything, then click Submit yourself. " + hint)
    page.evaluate("([m, t, i]) => window.__iap.banner(m, t, i)", [msg, "warn" if state["needs"] else "ok", items])


def _save_new_questions(con, job_id: int, state: dict) -> None:
    for q in state["new_questions"] - state["saved_questions"]:
        con.execute("INSERT OR IGNORE INTO answers(job_id, question) VALUES (?, ?)", (job_id, q))
        state["saved_questions"].add(q)
    con.commit()


# ---------- context + main loop ----------

def context_for(con, job) -> Context:
    answers = yaml.safe_load(config.ANSWERS.read_text(encoding="utf-8")) if config.ANSWERS.exists() else {}
    p = prof.load()
    t = con.execute("SELECT * FROM tailored WHERE job_id=?", (job["id"],)).fetchone()
    resume_pdf = Path(t["pdf_path"]) if t and Path(t["pdf_path"]).exists() else None
    if resume_pdf is None:
        master = config.OUT / "master" / f"{p['name'].replace(' ', '_')}_Resume.pdf"
        resume_pdf = master if master.exists() else None
    cover_text = t["cover_letter"] if t and t["cover_letter_approved"] and t["cover_letter"] else None
    cover_pdf = resume.build_cover_letter(cover_text, p, config.OUT / "jobs" / str(job["id"])) if cover_text else None
    approved = [(r["question"], r["final"]) for r in
                con.execute("SELECT question, final FROM answers WHERE job_id=? AND approved=1 AND final IS NOT NULL", (job["id"],))]
    return Context(answers=answers, resume_pdf=resume_pdf, cover_pdf=cover_pdf, cover_text=cover_text, approved=approved)


def new_state() -> dict:
    return {"filled": 0, "needs": {}, "new_questions": set(), "saved_questions": set(), "guarded": [],
            "notified": set(), "applied": False, "tried": set()}


def run(job_id: int, headless: bool = False, start_url: str | None = None, max_seconds: float | None = None,
        ctx: Context | None = None) -> dict:
    con = db.connect()
    job = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        raise ValueError(f"no job {job_id}")
    ctx = ctx or context_for(con, job)
    url, portal = (start_url, "generic") if start_url else form_url(job["url"])
    state = new_state()
    with sync_playwright() as pw:
        browser = pw.chromium.launch_persistent_context(str(config.BROWSER_PROFILE), headless=headless, no_viewport=True,
                                                        args=["--start-maximized"])
        try:
            page = browser.pages[0] if browser.pages else browser.new_page()
            page.goto(url, wait_until="domcontentloaded")
            started = time.time()
            while browser.pages and (max_seconds is None or time.time() - started < max_seconds):
                page = browser.pages[-1]  # follow a portal that opens the form in a new tab
                try:
                    step(page, con, job, ctx, state, portal)
                    page.wait_for_timeout(1500)
                except PWError:
                    time.sleep(1)  # mid-navigation or the tab just closed; try again
        finally:
            try:
                browser.close()
            except PWError:
                pass
    return {**state, "needs": list(state["needs"].values()), "new_questions": sorted(state["new_questions"])}

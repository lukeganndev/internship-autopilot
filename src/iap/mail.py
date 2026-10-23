"""Phase 7a: read-only Gmail / Outlook scanning that moves tracked applications along.

Only applications you're tracking (approved through interview) are touched. Statuses only move
forward (applied â†’ OA â†’ interview â†’ offer), except that a rejection always applies. Every change is
logged in status_events with the email that caused it, shown in the dashboard and undoable there.

Connectors: Gmail API (scope gmail.readonly) and Microsoft Graph (Mail.Read). Tokens live in
Windows Credential Manager. If the UA tenant blocks third-party access, forward UA mail to Gmail.
"""
import base64
import json
import re
from datetime import datetime, timedelta, timezone

import httpx

from . import db, notify, vault

# Highest priority first: a rejection that thanks you for applying is a rejection.
RULES = [
    ("offer", r"pleased to (extend|offer)|offer letter|extend(ing)? (you )?an? offer|congratulations[^.]{0,80}\boffer\b"),
    ("rejected", r"unfortunately|not (to )?(be )?mov(e|ing) forward|decided (to|not to) (pursue|proceed|move forward with other)|"
                 r"other candidates|(have not|haven't|were not|was not) (been )?selected|position has been filled|regret to inform|"
                 r"no longer (under consideration|being considered)|will not be (proceeding|moving)"),
    ("oa", r"online assessment|coding (challenge|assessment|test|exercise)|hackerrank|codesignal|codility|hirevue|karat|"
           r"assessment (invitation|invite|link)|complete (the|your|an|our) (online )?assessment|take[- ]home"),
    ("interview", r"\binterview(s|ing)?\b|phone screen|schedule (a|your) (call|time|chat|conversation|meeting)|"
                  r"(your|share your) availability|meet (with )?(the|our) team|technical screen|superday"),
    ("applied", r"thank you for (applying|your application|your interest)|we('ve| have) received your application|"
                r"application (was |has been )?(received|submitted)|successfully (submitted|applied)"),
]
RULES = [(s, re.compile(p, re.I)) for s, p in RULES]
PROGRESS = ["approved", "tailored", "applied", "oa", "interview", "offer"]
TRACKED = ("approved", "tailored", "applied", "oa", "interview")
# Mail from these comes on a company's behalf; the company name is in the subject/body instead.
ATS_DOMAINS = {"greenhouse.io", "greenhouse-mail.io", "lever.co", "hire.lever.co", "ashbyhq.com", "myworkday.com",
               "workday.com", "icims.com", "smartrecruiters.com", "hackerrank.com", "codesignal.com", "hirevue.com",
               "gmail.com", "outlook.com", "successfactors.com", "taleo.net", "oraclecloud.com", "jobvite.com"}
SEARCH = ('application OR applying OR interview OR assessment OR offer OR unfortunately OR candidacy '
          'OR hackerrank OR codesignal OR "next steps"')


def classify(subject: str, body: str) -> str | None:
    text = f"{subject}\n{body[:4000]}"
    return next((status for status, rx in RULES if rx.search(text)), None)


def _domain(sender: str) -> str:
    m = re.search(r"@([\w.-]+)", sender or "")
    return ".".join(m.group(1).lower().split(".")[-2:]) if m else ""


def match_job(con, sender: str, subject: str, body: str):
    """The tracked application this email is about, or None. Ties go to the title mentioned, then the newest."""
    hay = db.norm(f"{sender} {subject} {body[:2000]}")
    domain = _domain(sender)
    compact_domain = domain.split(".")[0] if domain and domain not in ATS_DOMAINS else ""
    rows = con.execute(
        f"""SELECT j.*, a.status, a.updated_at FROM applications a JOIN jobs j ON j.id=a.job_id
            WHERE a.status IN ({','.join('?' * len(TRACKED))})""", TRACKED).fetchall()
    hits = []
    for r in rows:
        cn = r["company_norm"]
        if cn and (re.search(rf"\b{re.escape(cn)}\b", hay) or (compact_domain and compact_domain == cn.replace(" ", ""))):
            hits.append(r)
    if len(hits) > 1:
        titled = [r for r in hits if r["title_norm"] and r["title_norm"] in hay]
        hits = titled or hits
    return max(hits, key=lambda r: r["updated_at"]) if hits else None


def advance(con, job_id: int, new: str, evidence: str, source: str = "email") -> bool:
    """Apply a status change if it moves forward (or is a rejection). Logs it. Returns whether it changed."""
    row = con.execute("SELECT status FROM applications WHERE job_id=?", (job_id,)).fetchone()
    old = row["status"] if row else None
    if old == new or old in ("offer", "rejected", "skipped"):
        return False
    if new != "rejected" and old in PROGRESS and PROGRESS.index(new) <= PROGRESS.index(old):
        return False
    con.execute("""INSERT INTO applications(job_id, status, applied_at) VALUES (?, ?, CASE WHEN ?='applied' THEN datetime('now') END)
                   ON CONFLICT(job_id) DO UPDATE SET status=excluded.status, updated_at=datetime('now'),
                     applied_at=COALESCE(applications.applied_at, excluded.applied_at)""", (job_id, new, new))
    con.execute("INSERT INTO status_events(job_id, old, new, source, evidence) VALUES (?,?,?,?,?)",
                (job_id, old, new, source, evidence))
    return True


def undo(con, event_id: int) -> None:
    e = con.execute("SELECT * FROM status_events WHERE id=? AND undone=0", (event_id,)).fetchone()
    if not e:
        return
    if e["old"]:
        con.execute("UPDATE applications SET status=?, updated_at=datetime('now') WHERE job_id=?", (e["old"], e["job_id"]))
    else:
        con.execute("DELETE FROM applications WHERE job_id=?", (e["job_id"],))
    con.execute("UPDATE status_events SET undone=1 WHERE id=?", (event_id,))
    con.commit()


def process(con, account: str, messages: list[dict]) -> list[dict]:
    """messages: {id, sender, subject, body, received_at}. Returns the status changes made."""
    changes = []
    for m in messages:
        if con.execute("SELECT 1 FROM emails WHERE account=? AND message_id=?", (account, m["id"])).fetchone():
            continue
        status = classify(m["subject"], m["body"])
        job = match_job(con, m["sender"], m["subject"], m["body"]) if status else None
        con.execute("INSERT INTO emails VALUES (?,?,?,?,?,?,?,?)",
                    (account, m["id"], m["sender"], m["subject"], m["body"][:300], m["received_at"],
                     job["id"] if job else None, status))
        if job and advance(con, job["id"], status, f"{m['received_at'][:10]} Â· {m['sender']} Â· {m['subject']}"):
            changes.append({"job": job["id"], "company": job["company"], "title": job["title"], "status": status,
                            "subject": m["subject"]})
    con.commit()
    return changes


# ---------- Gmail ----------

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def gmail_setup(client_secret_file: str) -> str:
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, GMAIL_SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True, prompt="consent")
    vault.put("gmail", creds.to_json())
    return "Gmail connected (read-only). You can delete the downloaded client secret file now."


def _gmail_creds():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    raw = vault.get("gmail")
    if not raw:
        return None
    creds = Credentials.from_authorized_user_info(json.loads(raw), GMAIL_SCOPES)
    if not creds.valid:
        creds.refresh(Request())
        vault.put("gmail", creds.to_json())
    return creds


def _gmail_body(payload: dict) -> str:
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"] + "==").decode("utf-8", "replace")
    return "\n".join(filter(None, (_gmail_body(p) for p in payload.get("parts") or [])))


def gmail_messages(days: int, limit: int = 300) -> list[dict]:
    creds = _gmail_creds()
    if not creds:
        return []
    h = {"Authorization": f"Bearer {creds.token}"}
    base = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
    ids, page = [], None
    with httpx.Client(timeout=30, headers=h) as c:
        while len(ids) < limit:
            params = {"q": f"newer_than:{days}d ({SEARCH})", "maxResults": 100, **({"pageToken": page} if page else {})}
            d = c.get(base, params=params).raise_for_status().json()
            ids += [m["id"] for m in d.get("messages", [])]
            if not (page := d.get("nextPageToken")):
                break
        out = []
        for mid in ids[:limit]:
            m = c.get(f"{base}/{mid}", params={"format": "full"}).raise_for_status().json()
            headers = {x["name"].lower(): x["value"] for x in m["payload"].get("headers", [])}
            out.append({"id": mid, "sender": headers.get("from", ""), "subject": headers.get("subject", ""),
                        "body": _gmail_body(m["payload"]) or m.get("snippet", ""),
                        "received_at": datetime.fromtimestamp(int(m["internalDate"]) / 1000, timezone.utc).isoformat()})
    return out


# ---------- Outlook (Microsoft Graph) ----------

GRAPH_SCOPES = ["Mail.Read"]
OUTLOOK_HELP = (
    "Your school account may block third-party apps (common for university Microsoft 365 tenants).\n"
    "Fallback: in Outlook on the web â†’ Settings â†’ Mail â†’ Forwarding, forward your crimson.ua.edu mail to your\n"
    "Gmail (keep a copy). Gmail scanning then covers both inboxes. That's a setting you change yourself."
)


def _msal_app(client_id: str):
    import msal

    cache = msal.SerializableTokenCache()
    if raw := vault.get("outlook-cache"):
        cache.deserialize(raw)
    return msal.PublicClientApplication(client_id, authority="https://login.microsoftonline.com/common", token_cache=cache), cache


def outlook_setup(client_id: str) -> str:
    app, cache = _msal_app(client_id)
    result = app.acquire_token_interactive(GRAPH_SCOPES)
    if "access_token" not in result:
        err = f"{result.get('error')}: {result.get('error_description', '')[:300]}"
        if re.search(r"AADSTS(65001|90094|90095|500011|700016|53003)|admin|consent|policy", err, re.I):
            return f"Outlook was blocked: {err}\n\n{OUTLOOK_HELP}"
        return f"Outlook sign-in failed: {err}"
    vault.put("outlook-client-id", client_id)
    vault.put("outlook-cache", cache.serialize())
    return "Outlook connected (read-only)."


def outlook_messages(days: int, limit: int = 300) -> list[dict]:
    client_id = vault.get("outlook-client-id")
    if not client_id:
        return []
    app, cache = _msal_app(client_id)
    accounts = app.get_accounts()
    result = app.acquire_token_silent(GRAPH_SCOPES, account=accounts[0]) if accounts else None
    if not result or "access_token" not in result:
        raise RuntimeError("Outlook token expired; run `iap mail setup outlook <client-id>` again")
    if cache.has_state_changed:
        vault.put("outlook-cache", cache.serialize())
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = ("https://graph.microsoft.com/v1.0/me/messages?$select=id,subject,from,body,bodyPreview,receivedDateTime"
           f"&$filter=receivedDateTime ge {since}&$orderby=receivedDateTime desc&$top=50")
    out = []
    with httpx.Client(timeout=30, headers={"Authorization": f"Bearer {result['access_token']}", "Prefer": 'outlook.body-content-type="text"'}) as c:
        while url and len(out) < limit:
            d = c.get(url).raise_for_status().json()
            for m in d.get("value", []):
                frm = (m.get("from") or {}).get("emailAddress") or {}
                out.append({"id": m["id"], "sender": f"{frm.get('name', '')} <{frm.get('address', '')}>",
                            "subject": m.get("subject") or "", "body": (m.get("body") or {}).get("content") or m.get("bodyPreview", ""),
                            "received_at": m.get("receivedDateTime", "")})
            url = d.get("@odata.nextLink")
    return out


# ---------- sync ----------

def accounts() -> list[str]:
    return [a for a, key in (("gmail", "gmail"), ("outlook", "outlook-client-id")) if vault.get(key)]


def sync(con=None, days: int | None = None) -> dict:
    con = con or db.connect()
    last = db.get_setting(con, "mail_last_sync")
    if days is None:
        days = 30 if not last else max(2, (datetime.now(timezone.utc) - datetime.fromisoformat(last)).days + 2)
    changes, errors = [], []
    for account, fetch in (("gmail", gmail_messages), ("outlook", outlook_messages)):
        if account not in accounts():
            continue
        try:
            changes += process(con, account, fetch(days))
        except Exception as e:  # one broken account must not stop the other
            errors.append(f"{account}: {type(e).__name__}: {str(e)[:200]}")
    db.set_setting(con, "mail_last_sync", datetime.now(timezone.utc).isoformat())
    for c in changes[:5]:
        notify.toast(f"{c['company']}: {c['status']}", c["subject"][:120])
    return {"accounts": accounts(), "days": days, "changes": changes, "errors": errors}

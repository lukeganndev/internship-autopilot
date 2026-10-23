PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,              -- first dedupe key seen; all keys live in job_sources
    company TEXT NOT NULL,
    company_norm TEXT NOT NULL,
    title TEXT NOT NULL,
    title_norm TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    ats TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    salary TEXT NOT NULL DEFAULT '',
    season TEXT NOT NULL DEFAULT '',
    posted_at TEXT NOT NULL DEFAULT '',
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen TEXT NOT NULL DEFAULT (datetime('now')),
    active INTEGER NOT NULL DEFAULT 1,
    excluded_reason TEXT,                  -- NULL = eligible
    flags TEXT NOT NULL DEFAULT '[]',      -- JSON list, e.g. ["co-op", "sophomore-friendly"]
    is_dream INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS jobs_company_title ON jobs(company_norm, title_norm);

CREATE TABLE IF NOT EXISTS job_sources (
    key TEXT NOT NULL,                     -- e.g. greenhouse:stripe:123 or url:example.com/jobs/1
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (key, source)
);

CREATE TABLE IF NOT EXISTS boards (
    key TEXT PRIMARY KEY,                  -- ats:account:site
    ats TEXT NOT NULL,
    account TEXT NOT NULL,
    host TEXT NOT NULL DEFAULT '',
    site TEXT NOT NULL DEFAULT '',
    company TEXT NOT NULL DEFAULT '',
    fails INTEGER NOT NULL DEFAULT 0,
    last_ok TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    started TEXT NOT NULL DEFAULT (datetime('now')),
    finished TEXT,
    stats TEXT
);

CREATE TABLE IF NOT EXISTS scores (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
    score INTEGER NOT NULL,                -- 0-100
    reasons TEXT NOT NULL,                 -- JSON list, one line per scoring component
    missing TEXT NOT NULL DEFAULT '[]',    -- JSON list of skills the job asks for that the profile lacks
    scored_at TEXT NOT NULL
);

-- A row exists once you act on a job (skip/approve/apply...). No row = still in the queue.
CREATE TABLE IF NOT EXISTS applications (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
    status TEXT NOT NULL,                  -- skipped | approved | tailored | applied | oa | interview | offer | rejected
    deadline TEXT,
    applied_at TEXT,
    notes TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL                    -- JSON
);

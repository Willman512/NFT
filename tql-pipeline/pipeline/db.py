"""SQLite store: prospects, outcome history, and every model version."""
import json
import sqlite3

from .config import DATA_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS prospects (
    domain        TEXT PRIMARY KEY,
    url           TEXT NOT NULL,
    name          TEXT,
    source        TEXT,
    notes         TEXT,
    added_at      TEXT,
    status        TEXT DEFAULT 'new',      -- new|researched|queued|skipped|suppressed|error|contacted|won
    verdict       TEXT,                    -- GO|MAYBE|SKIP
    score         REAL,
    reasons       TEXT,                    -- json list
    features      TEXT,                    -- json {feature: 0/1}
    research      TEXT,                    -- json card data (phones, emails, pages, detail…)
    page_text     TEXT,
    researched_at TEXT,
    filter_reason TEXT,
    contacted_at  TEXT,
    touches       INTEGER DEFAULT 0,
    last_outcome  TEXT,
    last_outcome_at TEXT,
    model_version INTEGER
);
CREATE TABLE IF NOT EXISTS outcomes (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    domain  TEXT NOT NULL,
    outcome TEXT NOT NULL,
    at      TEXT NOT NULL,
    note    TEXT,
    verdict TEXT,                          -- verdict when the outcome was logged (honest scoreboard)
    score   REAL,
    model_version INTEGER
);
CREATE TABLE IF NOT EXISTS models (
    version    INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    bias       REAL NOT NULL,
    weights    TEXT NOT NULL,              -- json {feature: weight}
    go         REAL NOT NULL,
    skip       REAL NOT NULL,
    n_pos      INTEGER DEFAULT 0,
    n_neg      INTEGER DEFAULT 0,
    note       TEXT
);
CREATE TABLE IF NOT EXISTS learned_terms (
    term     TEXT PRIMARY KEY,
    added_at TEXT NOT NULL,
    log_odds REAL,
    active   INTEGER DEFAULT 1
);
"""


def connect(path=None):
    if path is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / "prospects.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def jload(value, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def get_prospect(conn, domain):
    return conn.execute("SELECT * FROM prospects WHERE domain=?", (domain,)).fetchone()


def latest_model(conn):
    return conn.execute("SELECT * FROM models ORDER BY version DESC LIMIT 1").fetchone()


def active_terms(conn):
    return [r["term"] for r in conn.execute("SELECT term FROM learned_terms WHERE active=1")]

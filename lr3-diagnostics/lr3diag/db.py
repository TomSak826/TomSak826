"""SQLite storage shared by every part of the tool.

Everything lives in a single file (default ``~/.lr3diag/lr3.db``, or set the
``LR3_DB`` environment variable), so it is easy to back up.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

SCHEMA = """
-- Workshop manual: one row per page/section, with a full-text index.
CREATE TABLE IF NOT EXISTS manual_docs (
    id        INTEGER PRIMARY KEY,
    source    TEXT NOT NULL,        -- file the text came from
    location  TEXT NOT NULL,        -- page number or section heading
    title     TEXT NOT NULL,
    body      TEXT NOT NULL,
    UNIQUE(source, location)
);
CREATE VIRTUAL TABLE IF NOT EXISTS manual_fts USING fts5(
    title, body, content='manual_docs', content_rowid='id',
    tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS manual_ai AFTER INSERT ON manual_docs BEGIN
    INSERT INTO manual_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;
CREATE TRIGGER IF NOT EXISTS manual_ad AFTER DELETE ON manual_docs BEGIN
    INSERT INTO manual_fts(manual_fts, rowid, title, body)
    VALUES ('delete', old.id, old.title, old.body);
END;

-- Maintenance: what was done, when, at what mileage.
CREATE TABLE IF NOT EXISTS service_log (
    id        INTEGER PRIMARY KEY,
    item      TEXT NOT NULL,        -- key from the maintenance schedule
    date      TEXT NOT NULL,        -- ISO date
    odometer  INTEGER NOT NULL,     -- miles
    notes     TEXT DEFAULT '',
    cost      REAL
);

-- Odometer readings, so "due" can be computed without asking every time.
CREATE TABLE IF NOT EXISTS odometer (
    id        INTEGER PRIMARY KEY,
    date      TEXT NOT NULL,
    miles     INTEGER NOT NULL
);
"""


def default_path() -> Path:
    env = os.environ.get("LR3_DB")
    if env:
        return Path(env)
    return Path.home() / ".lr3diag" / "lr3.db"


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(path) if path else default_path()
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn

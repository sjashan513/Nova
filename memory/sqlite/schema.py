import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "sessions_dev.db"

DDL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    domain      TEXT NOT NULL DEFAULT 'dev',
    ts_start    INTEGER NOT NULL,
    ts_end      INTEGER
);

CREATE TABLE IF NOT EXISTS turns (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    input_text  TEXT NOT NULL,
    output_text TEXT,
    ts          INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS plans (
    id               TEXT PRIMARY KEY,
    turn_id          TEXT NOT NULL REFERENCES turns(id),
    task             TEXT NOT NULL,
    status           TEXT NOT NULL,
    worker_sequence  TEXT NOT NULL DEFAULT '[]',
    ts_start         INTEGER NOT NULL,
    ts_end           INTEGER
);

CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);
CREATE INDEX IF NOT EXISTS idx_plans_turn    ON plans(turn_id);
"""


def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(DDL)
    conn.commit()
    return conn

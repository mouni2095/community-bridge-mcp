"""SQLite storage: connection setup, schema, transactions (NFR-1, spec §3)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE nonprofit (
    id   TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE staff (
    id           TEXT PRIMARY KEY,
    nonprofit_id TEXT NOT NULL REFERENCES nonprofit(id),
    display_name TEXT NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('case_worker', 'program_manager', 'finance')),
    active       INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

CREATE TABLE participant (
    id           TEXT PRIMARY KEY,
    nonprofit_id TEXT NOT NULL REFERENCES nonprofit(id),
    first_name   TEXT NOT NULL,
    last_name    TEXT NOT NULL,
    UNIQUE (id, nonprofit_id)
);

CREATE TABLE fund (
    id               TEXT PRIMARY KEY,
    nonprofit_id     TEXT NOT NULL REFERENCES nonprofit(id),
    name             TEXT NOT NULL,
    allocation_cents INTEGER NOT NULL CHECK (allocation_cents >= 0),
    currency         TEXT NOT NULL DEFAULT 'USD' CHECK (currency = 'USD'),
    UNIQUE (id, nonprofit_id)
);

CREATE TABLE assistance_request (
    id              TEXT PRIMARY KEY,
    nonprofit_id    TEXT NOT NULL REFERENCES nonprofit(id),
    participant_id  TEXT NOT NULL,
    fund_id         TEXT NOT NULL,
    purpose         TEXT NOT NULL CHECK (purpose IN ('rent', 'utilities', 'transportation', 'other')),
    description     TEXT NOT NULL,
    amount_cents    INTEGER NOT NULL CHECK (amount_cents > 0),
    status          TEXT NOT NULL CHECK (status IN ('Pending', 'Approved', 'Rejected', 'Paid')),
    created_by      TEXT NOT NULL REFERENCES staff(id),
    created_at      TEXT NOT NULL,
    idempotency_key TEXT,
    decided_by      TEXT REFERENCES staff(id),
    decided_at      TEXT,
    decision_note   TEXT,
    paid_by         TEXT REFERENCES staff(id),
    paid_at         TEXT,
    -- A request can only reference a participant/fund of its own nonprofit (SEC-2).
    FOREIGN KEY (participant_id, nonprofit_id) REFERENCES participant(id, nonprofit_id),
    FOREIGN KEY (fund_id, nonprofit_id) REFERENCES fund(id, nonprofit_id),
    UNIQUE (nonprofit_id, created_by, idempotency_key)
);
CREATE INDEX ix_request_fund_status ON assistance_request (fund_id, status);
CREATE INDEX ix_request_np_created ON assistance_request (nonprofit_id, created_at);

CREATE TABLE payment (
    id                  TEXT PRIMARY KEY,
    request_id          TEXT NOT NULL UNIQUE REFERENCES assistance_request(id),  -- BR-5: at most once
    nonprofit_id        TEXT NOT NULL REFERENCES nonprofit(id),
    amount_cents        INTEGER NOT NULL CHECK (amount_cents > 0),
    simulated_reference TEXT NOT NULL,
    recorded_by         TEXT NOT NULL REFERENCES staff(id),
    recorded_at         TEXT NOT NULL
);

CREATE TABLE audit_event (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    nonprofit_id   TEXT NOT NULL REFERENCES nonprofit(id),
    actor_staff_id TEXT NOT NULL REFERENCES staff(id),
    action         TEXT NOT NULL,
    request_id     TEXT REFERENCES assistance_request(id),
    details_json   TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    # isolation_level=None: we issue BEGIN/COMMIT ourselves so that writes use
    # BEGIN IMMEDIATE (spec §3.4).
    conn = sqlite3.connect(str(db_path), isolation_level=None, timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def write_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE acquires the database write lock before any read, so a
    read-check-write sequence cannot interleave with another writer, even in
    another process. Any exception rolls back everything (BR-9)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)

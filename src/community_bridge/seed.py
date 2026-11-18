"""Setup / reset command (NFR-2). Deletes the database and recreates it with
synthetic seed data. Separate from server startup by design.

    python -m community_bridge.seed            # uses CB_DB_PATH or ./data/community_bridge.db
    python -m community_bridge.seed --db path/to/file.db
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import db
from .config import default_db_path

NONPROFITS = [
    ("np_community_bridge", "Community Bridge"),
    ("np_harbor_hope", "Harbor Hope Services"),
]

# Fixture identities (trusted, selected via CB_STAFF_ID).
STAFF = [
    ("cb-case-worker", "np_community_bridge", "Casey Alvarez", "case_worker", 1),
    ("cb-program-manager", "np_community_bridge", "Morgan Lee", "program_manager", 1),
    ("cb-finance", "np_community_bridge", "Frances Okafor", "finance", 1),
    ("cb-former-staff", "np_community_bridge", "Riley Former", "program_manager", 0),  # inactive
    ("hh-case-worker", "np_harbor_hope", "Jordan Patel", "case_worker", 1),
    ("hh-program-manager", "np_harbor_hope", "Sam Nguyen", "program_manager", 1),
    ("hh-finance", "np_harbor_hope", "Avery Brooks", "finance", 1),
]

PARTICIPANTS = [
    ("p-cb-001", "np_community_bridge", "Alex", "Rivera"),
    ("p-cb-002", "np_community_bridge", "Taylor", "Kim"),
    ("p-cb-003", "np_community_bridge", "Jamie", "Osei"),
    ("p-hh-001", "np_harbor_hope", "Drew", "Martinez"),
    ("p-hh-002", "np_harbor_hope", "Quinn", "Hughes"),
]

FUNDS = [
    ("fund-cb-rent", "np_community_bridge", "Emergency Rent Relief 2026", 10_000_00),
    ("fund-cb-utilities", "np_community_bridge", "Utility Assistance Grant", 2_500_00),
    ("fund-cb-transport", "np_community_bridge", "Transportation Micro-Grant", 500_00),
    ("fund-hh-general", "np_harbor_hope", "Harbor Hope General Assistance", 5_000_00),
]


def reset_database(db_path: str | Path) -> Path:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    conn = db.connect(path)
    try:
        db.create_schema(conn)
        with db.write_transaction(conn):
            conn.executemany("INSERT INTO nonprofit (id, name) VALUES (?, ?)", NONPROFITS)
            conn.executemany(
                "INSERT INTO staff (id, nonprofit_id, display_name, role, active) VALUES (?, ?, ?, ?, ?)", STAFF
            )
            conn.executemany(
                "INSERT INTO participant (id, nonprofit_id, first_name, last_name) VALUES (?, ?, ?, ?)",
                PARTICIPANTS,
            )
            conn.executemany(
                "INSERT INTO fund (id, nonprofit_id, name, allocation_cents) VALUES (?, ?, ?, ?)", FUNDS
            )
    finally:
        conn.close()
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reset the Community Bridge database with synthetic seed data.")
    parser.add_argument("--db", default=None, help="Database path (default: CB_DB_PATH or ./data/community_bridge.db)")
    args = parser.parse_args(argv)
    path = reset_database(args.db or default_db_path())
    print(f"Database reset and seeded: {path.resolve()}", file=sys.stderr)
    print("Fixture identities (set CB_STAFF_ID):", file=sys.stderr)
    for staff_id, np_id, name, role, active in STAFF:
        flag = "" if active else "  [inactive]"
        print(f"  {staff_id:<20} {role:<16} {np_id}{flag}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

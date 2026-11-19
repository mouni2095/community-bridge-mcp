"""Environment configuration and logging (FR-1, NFR-3)."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

ENV_STAFF_ID = "CB_STAFF_ID"
ENV_DB_PATH = "CB_DB_PATH"
ENV_LOG_LEVEL = "CB_LOG_LEVEL"
ENV_LOG_FILE = "CB_LOG_FILE"


def default_db_path() -> Path:
    return Path(os.environ.get(ENV_DB_PATH) or Path.cwd() / "data" / "community_bridge.db")


def configured_staff_id() -> str | None:
    value = os.environ.get(ENV_STAFF_ID, "").strip()
    return value or None


def setup_logging() -> None:
    """Logs go to stderr (and optionally a file), never stdout, which carries
    the MCP stdio protocol."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    log_file = os.environ.get(ENV_LOG_FILE)
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(
        level=os.environ.get(ENV_LOG_LEVEL, "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
        force=True,
    )

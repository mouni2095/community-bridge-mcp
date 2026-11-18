from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from community_bridge.errors import DomainError, ErrorCode
from community_bridge.seed import reset_database
from community_bridge.service import AssistanceService

TABLES = ("nonprofit", "staff", "participant", "fund", "assistance_request", "payment", "audit_event")


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return reset_database(tmp_path / "test.db")


@pytest.fixture
def svc(db_path: Path) -> AssistanceService:
    return AssistanceService(db_path)


@pytest.fixture
def who(svc: AssistanceService):
    """who('cb-program-manager') -> Caller, resolved exactly as the server does."""
    return svc.authenticate


def snapshot(db_path: Path) -> dict[str, list[tuple]]:
    """Full dump of business state, for asserting BR-9 (failure changes nothing)."""
    conn = sqlite3.connect(db_path)
    try:
        return {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in TABLES}
    finally:
        conn.close()


def expect_error(code: ErrorCode, fn, *args, **kwargs) -> DomainError:
    with pytest.raises(DomainError) as info:
        fn(*args, **kwargs)
    assert info.value.code == code, f"expected {code}, got {info.value.code}: {info.value.message}"
    return info.value


@pytest.fixture
def make_request(svc, who):
    def _make(amount: str = "300.00", fund_id: str = "fund-cb-rent", participant_id: str = "p-cb-001", **kw):
        return svc.create_request(
            who("cb-case-worker"),
            participant_id=participant_id,
            fund_id=fund_id,
            purpose=kw.pop("purpose", "rent"),
            amount=amount,
            description=kw.pop("description", "March rent shortfall"),
            **kw,
        ).request

    return _make

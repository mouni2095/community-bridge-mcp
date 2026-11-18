"""Business rules: identity, permissions, tenant scoping, state transitions and
fund accounting. Transport-agnostic — the MCP layer is a thin adapter over this.

Check order for every operation (spec §2.3):
authenticate -> authorize role -> validate input -> load (tenant-scoped)
-> state check -> funds check -> write (single BEGIN IMMEDIATE transaction).
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import db
from .errors import DomainError, ErrorCode
from .models import (
    AssistanceRequest,
    Caller,
    CreateRequestResult,
    DecisionResult,
    FundingSummary,
    FundingTotals,
    FundList,
    FundSummary,
    Money,
    Participant,
    ParticipantList,
    Payment,
    PaymentResult,
    RequestCounts,
    RequestList,
    RequestResult,
    Role,
    Status,
    WhoAmI,
)
from .money import money, parse_amount

# SEC-1: role matrix (spec §5).
PERMISSIONS: dict[Role, frozenset[str]] = {
    Role.CASE_WORKER: frozenset({"read", "create_request"}),
    Role.PROGRAM_MANAGER: frozenset({"read", "approve_request", "reject_request"}),
    Role.FINANCE: frozenset({"read", "record_payment"}),
}

PURPOSES = ("rent", "utilities", "transportation", "other")
MAX_TEXT = 500
MAX_ID = 100
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")

_REQUEST_SELECT = """
SELECT r.*, p.first_name || ' ' || p.last_name AS participant_name, f.name AS fund_name,
       pay.id AS payment_id
FROM assistance_request r
JOIN participant p ON p.id = r.participant_id AND p.nonprofit_id = r.nonprofit_id
JOIN fund f ON f.id = r.fund_id AND f.nonprofit_id = r.nonprofit_id
LEFT JOIN payment pay ON pay.request_id = r.id
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _m(cents: int) -> Money:
    return Money(**money(cents))


class AssistanceService:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    # ------------------------------------------------------------------ infra
    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        # One short-lived connection per operation: safe across threads and
        # processes; SQLite does the locking.
        conn = db.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    # --------------------------------------------------------------- identity
    def authenticate(self, staff_id: str | None) -> Caller:
        """FR-1: resolve a trusted fixture identity to role + nonprofit."""
        if not staff_id or not staff_id.strip():
            raise DomainError(ErrorCode.UNAUTHENTICATED, "No staff identity configured (CB_STAFF_ID)")
        with self._conn() as conn:
            row = conn.execute(
                """SELECT s.id, s.display_name, s.role, s.active, n.id AS np_id, n.name AS np_name
                   FROM staff s JOIN nonprofit n ON n.id = s.nonprofit_id WHERE s.id = ?""",
                (staff_id.strip(),),
            ).fetchone()
        if row is None or not row["active"]:
            # Same response for unknown and inactive: do not reveal which IDs exist.
            raise DomainError(ErrorCode.UNAUTHENTICATED, "Unknown or inactive staff identity")
        return Caller(row["id"], row["display_name"], Role(row["role"]), row["np_id"], row["np_name"])

    @staticmethod
    def _require(caller: Caller, permission: str) -> None:
        if permission not in PERMISSIONS[caller.role]:
            raise DomainError(
                ErrorCode.FORBIDDEN,
                f"Role '{caller.role}' is not permitted to {permission.replace('_', ' ')}",
                {"role": str(caller.role), "required_permission": permission},
            )

    # ------------------------------------------------------------- validation
    @staticmethod
    def _text(value: Any, field: str, *, required: bool, max_len: int = MAX_TEXT) -> str | None:
        if value is None:
            if required:
                raise DomainError(ErrorCode.VALIDATION_ERROR, f"{field} is required", {"field": field})
            return None
        if not isinstance(value, str):
            raise DomainError(ErrorCode.VALIDATION_ERROR, f"{field} must be a string", {"field": field})
        text = value.strip()
        if not text:
            if required:
                raise DomainError(ErrorCode.VALIDATION_ERROR, f"{field} must not be blank", {"field": field})
            return None
        if len(text) > max_len:
            raise DomainError(
                ErrorCode.VALIDATION_ERROR, f"{field} must be at most {max_len} characters", {"field": field}
            )
        return text

    def _id(self, value: Any, field: str) -> str:
        return self._text(value, field, required=True, max_len=MAX_ID)  # type: ignore[return-value]

    @staticmethod
    def _limit(value: Any) -> int:
        if value is None:
            return 50
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 200:
            raise DomainError(ErrorCode.VALIDATION_ERROR, "limit must be an integer 1-200", {"field": "limit"})
        return value

    # ---------------------------------------------------------------- loaders
    @staticmethod
    def _request_row(conn: sqlite3.Connection, caller: Caller, request_id: str) -> sqlite3.Row:
        row = conn.execute(
            _REQUEST_SELECT + " WHERE r.id = ? AND r.nonprofit_id = ?", (request_id, caller.nonprofit_id)
        ).fetchone()
        if row is None:
            # SEC-2: other nonprofits' requests are indistinguishable from missing ones.
            raise DomainError(ErrorCode.NOT_FOUND, "Request not found", {"request_id": request_id})
        return row

    @staticmethod
    def _to_request(row: sqlite3.Row) -> AssistanceRequest:
        return AssistanceRequest(
            id=row["id"],
            participant_id=row["participant_id"],
            participant_name=row["participant_name"],
            fund_id=row["fund_id"],
            fund_name=row["fund_name"],
            purpose=row["purpose"],
            description=row["description"],
            amount=_m(row["amount_cents"]),
            status=Status(row["status"]),
            created_by=row["created_by"],
            created_at=row["created_at"],
            decided_by=row["decided_by"],
            decided_at=row["decided_at"],
            decision_note=row["decision_note"],
            paid_by=row["paid_by"],
            paid_at=row["paid_at"],
            payment_id=row["payment_id"],
        )

    @staticmethod
    def _fund_summaries(conn: sqlite3.Connection, caller: Caller, fund_id: str | None = None) -> list[FundSummary]:
        """BR-8: totals derived from request rows, never stored counters."""
        sql = """
            SELECT f.id, f.name, f.allocation_cents,
                   COALESCE(SUM(CASE WHEN r.status = 'Approved' THEN r.amount_cents END), 0) AS committed,
                   COALESCE(SUM(CASE WHEN r.status = 'Paid' THEN r.amount_cents END), 0) AS paid,
                   COUNT(CASE WHEN r.status = 'Pending' THEN 1 END) AS n_pending,
                   COUNT(CASE WHEN r.status = 'Approved' THEN 1 END) AS n_approved,
                   COUNT(CASE WHEN r.status = 'Rejected' THEN 1 END) AS n_rejected,
                   COUNT(CASE WHEN r.status = 'Paid' THEN 1 END) AS n_paid
            FROM fund f
            LEFT JOIN assistance_request r ON r.fund_id = f.id AND r.nonprofit_id = f.nonprofit_id
            WHERE f.nonprofit_id = ?"""
        params: list[Any] = [caller.nonprofit_id]
        if fund_id is not None:
            sql += " AND f.id = ?"
            params.append(fund_id)
        sql += " GROUP BY f.id ORDER BY f.name"
        out = []
        for r in conn.execute(sql, params).fetchall():
            available = r["allocation_cents"] - r["committed"] - r["paid"]
            out.append(
                FundSummary(
                    id=r["id"],
                    name=r["name"],
                    allocation=_m(r["allocation_cents"]),
                    committed=_m(r["committed"]),
                    paid=_m(r["paid"]),
                    available=_m(available),
                    request_counts=RequestCounts(
                        Pending=r["n_pending"], Approved=r["n_approved"], Rejected=r["n_rejected"], Paid=r["n_paid"]
                    ),
                )
            )
        if fund_id is not None and not out:
            raise DomainError(ErrorCode.NOT_FOUND, "Fund not found", {"fund_id": fund_id})
        return out

    def _fund_summary(self, conn: sqlite3.Connection, caller: Caller, fund_id: str) -> FundSummary:
        return self._fund_summaries(conn, caller, fund_id)[0]

    @staticmethod
    def _audit(conn: sqlite3.Connection, caller: Caller, action: str, request_id: str, details: dict) -> None:
        conn.execute(
            """INSERT INTO audit_event (nonprofit_id, actor_staff_id, action, request_id, details_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (caller.nonprofit_id, caller.staff_id, action, request_id, json.dumps(details, sort_keys=True), _now()),
        )

    # ------------------------------------------------------------------ reads
    def whoami(self, caller: Caller) -> WhoAmI:
        return WhoAmI(
            staff_id=caller.staff_id,
            display_name=caller.display_name,
            role=caller.role,
            nonprofit_id=caller.nonprofit_id,
            nonprofit_name=caller.nonprofit_name,
            permissions=sorted(PERMISSIONS[caller.role]),
        )

    def list_participants(self, caller: Caller, search: str | None = None, limit: int | None = None) -> ParticipantList:
        self._require(caller, "read")
        term = self._text(search, "search", required=False, max_len=100)
        lim = self._limit(limit)
        sql = "SELECT id, first_name, last_name FROM participant WHERE nonprofit_id = ?"
        params: list[Any] = [caller.nonprofit_id]
        if term:
            sql += " AND (first_name || ' ' || last_name LIKE ? ESCAPE '\\' OR id = ?)"
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params += [f"%{escaped}%", term]
        sql += " ORDER BY last_name, first_name LIMIT ?"
        params.append(lim)
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return ParticipantList(participants=[Participant(**dict(r)) for r in rows])

    def list_funds(self, caller: Caller) -> FundList:
        self._require(caller, "read")
        with self._conn() as conn:
            return FundList(funds=self._fund_summaries(conn, caller))

    def funding_summary(self, caller: Caller, fund_id: str | None = None) -> FundingSummary:
        self._require(caller, "read")
        fid = self._id(fund_id, "fund_id") if fund_id is not None else None
        with self._conn() as conn:
            funds = self._fund_summaries(conn, caller, fid)
        totals = {k: sum(getattr(f, k).cents for f in funds) for k in ("allocation", "committed", "paid", "available")}
        return FundingSummary(funds=funds, totals=FundingTotals(**{k: _m(v) for k, v in totals.items()}))

    def get_request(self, caller: Caller, request_id: str) -> RequestResult:
        self._require(caller, "read")
        rid = self._id(request_id, "request_id")
        with self._conn() as conn:
            return RequestResult(request=self._to_request(self._request_row(conn, caller, rid)))

    def list_requests(
        self,
        caller: Caller,
        status: str | None = None,
        fund_id: str | None = None,
        participant_id: str | None = None,
        limit: int | None = None,
    ) -> RequestList:
        self._require(caller, "read")
        sql = _REQUEST_SELECT + " WHERE r.nonprofit_id = ?"
        params: list[Any] = [caller.nonprofit_id]
        if status is not None:
            if status not in {s.value for s in Status}:
                raise DomainError(ErrorCode.VALIDATION_ERROR, "Unknown status", {"field": "status"})
            sql += " AND r.status = ?"
            params.append(status)
        if fund_id is not None:
            sql += " AND r.fund_id = ?"
            params.append(self._id(fund_id, "fund_id"))
        if participant_id is not None:
            sql += " AND r.participant_id = ?"
            params.append(self._id(participant_id, "participant_id"))
        sql += " ORDER BY r.created_at DESC, r.id DESC LIMIT ?"
        params.append(self._limit(limit))
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return RequestList(requests=[self._to_request(r) for r in rows])

    # ----------------------------------------------------------------- writes
    def create_request(
        self,
        caller: Caller,
        participant_id: str,
        fund_id: str,
        purpose: str,
        amount: str,
        description: str,
        idempotency_key: str | None = None,
    ) -> CreateRequestResult:
        """FR-4, BR-2 (no reservation), BR-10 (idempotency key)."""
        self._require(caller, "create_request")
        pid = self._id(participant_id, "participant_id")
        fid = self._id(fund_id, "fund_id")
        if purpose not in PURPOSES:
            raise DomainError(
                ErrorCode.VALIDATION_ERROR, f"purpose must be one of {', '.join(PURPOSES)}", {"field": "purpose"}
            )
        cents = parse_amount(amount)
        desc = self._text(description, "description", required=True)
        key = None
        if idempotency_key is not None:
            if not isinstance(idempotency_key, str) or not _IDEMPOTENCY_RE.fullmatch(idempotency_key):
                raise DomainError(
                    ErrorCode.VALIDATION_ERROR,
                    "idempotency_key must be 1-100 characters of A-Z a-z 0-9 _ . : -",
                    {"field": "idempotency_key"},
                )
            key = idempotency_key

        with self._conn() as conn, db.write_transaction(conn):
            if key is not None:
                existing = conn.execute(
                    _REQUEST_SELECT + " WHERE r.nonprofit_id = ? AND r.created_by = ? AND r.idempotency_key = ?",
                    (caller.nonprofit_id, caller.staff_id, key),
                ).fetchone()
                if existing is not None:
                    same = (
                        existing["participant_id"] == pid
                        and existing["fund_id"] == fid
                        and existing["purpose"] == purpose
                        and existing["amount_cents"] == cents
                        and existing["description"] == desc
                    )
                    if not same:
                        raise DomainError(
                            ErrorCode.IDEMPOTENCY_CONFLICT,
                            "idempotency_key was already used for a different request",
                            {"request_id": existing["id"]},
                        )
                    return CreateRequestResult(request=self._to_request(existing), replayed=True)

            if conn.execute(
                "SELECT 1 FROM participant WHERE id = ? AND nonprofit_id = ?", (pid, caller.nonprofit_id)
            ).fetchone() is None:
                raise DomainError(ErrorCode.NOT_FOUND, "Participant not found", {"participant_id": pid})
            if conn.execute(
                "SELECT 1 FROM fund WHERE id = ? AND nonprofit_id = ?", (fid, caller.nonprofit_id)
            ).fetchone() is None:
                raise DomainError(ErrorCode.NOT_FOUND, "Fund not found", {"fund_id": fid})

            rid = "req_" + uuid.uuid4().hex
            conn.execute(
                """INSERT INTO assistance_request
                   (id, nonprofit_id, participant_id, fund_id, purpose, description, amount_cents,
                    status, created_by, created_at, idempotency_key)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'Pending', ?, ?, ?)""",
                (rid, caller.nonprofit_id, pid, fid, purpose, desc, cents, caller.staff_id, _now(), key),
            )
            self._audit(conn, caller, "request.created", rid, {"amount_cents": cents, "fund_id": fid})
            row = self._request_row(conn, caller, rid)
        return CreateRequestResult(request=self._to_request(row), replayed=False)

    def _transition_error(self, row: sqlite3.Row, action: str) -> DomainError:
        return DomainError(
            ErrorCode.INVALID_TRANSITION,
            f"Cannot {action} a request in status {row['status']}",
            {"request_id": row["id"], "current_status": row["status"], "request": self._to_request(row).model_dump()},
        )

    def approve_request(self, caller: Caller, request_id: str, note: str | None = None) -> DecisionResult:
        """FR-5, BR-3: funds check and reservation in one IMMEDIATE transaction."""
        self._require(caller, "approve_request")
        rid = self._id(request_id, "request_id")
        note_text = self._text(note, "note", required=False)
        with self._conn() as conn, db.write_transaction(conn):
            row = self._request_row(conn, caller, rid)
            if row["status"] != Status.PENDING:
                raise self._transition_error(row, "approve")
            fund = self._fund_summary(conn, caller, row["fund_id"])
            if fund.available.cents < row["amount_cents"]:
                raise DomainError(
                    ErrorCode.INSUFFICIENT_FUNDS,
                    f"Fund '{fund.name}' has {fund.available.amount} available; request needs "
                    f"{money(row['amount_cents'])['amount']}",
                    {
                        "request_id": rid,
                        "fund_id": fund.id,
                        "available": fund.available.model_dump(),
                        "requested": money(row["amount_cents"]),
                    },
                )
            cur = conn.execute(
                """UPDATE assistance_request SET status = 'Approved', decided_by = ?, decided_at = ?, decision_note = ?
                   WHERE id = ? AND nonprofit_id = ? AND status = 'Pending'""",
                (caller.staff_id, _now(), note_text, rid, caller.nonprofit_id),
            )
            if cur.rowcount != 1:  # pragma: no cover - guarded by the IMMEDIATE lock; defence in depth
                raise self._transition_error(self._request_row(conn, caller, rid), "approve")
            self._audit(conn, caller, "request.approved", rid, {"amount_cents": row["amount_cents"]})
            result = DecisionResult(
                request=self._to_request(self._request_row(conn, caller, rid)),
                fund=self._fund_summary(conn, caller, row["fund_id"]),
            )
        return result

    def reject_request(self, caller: Caller, request_id: str, reason: str) -> DecisionResult:
        """FR-6, BR-4: no funding effect."""
        self._require(caller, "reject_request")
        rid = self._id(request_id, "request_id")
        reason_text = self._text(reason, "reason", required=True)
        with self._conn() as conn, db.write_transaction(conn):
            row = self._request_row(conn, caller, rid)
            if row["status"] != Status.PENDING:
                raise self._transition_error(row, "reject")
            conn.execute(
                """UPDATE assistance_request SET status = 'Rejected', decided_by = ?, decided_at = ?, decision_note = ?
                   WHERE id = ? AND nonprofit_id = ? AND status = 'Pending'""",
                (caller.staff_id, _now(), reason_text, rid, caller.nonprofit_id),
            )
            self._audit(conn, caller, "request.rejected", rid, {})
            result = DecisionResult(
                request=self._to_request(self._request_row(conn, caller, rid)),
                fund=self._fund_summary(conn, caller, row["fund_id"]),
            )
        return result

    def record_payment(self, caller: Caller, request_id: str) -> PaymentResult:
        """FR-7, BR-5: full amount, once; committed -> paid."""
        self._require(caller, "record_payment")
        rid = self._id(request_id, "request_id")
        with self._conn() as conn, db.write_transaction(conn):
            row = self._request_row(conn, caller, rid)
            if row["status"] != Status.APPROVED:
                raise self._transition_error(row, "pay")
            now = _now()
            pay_id = "pay_" + uuid.uuid4().hex
            reference = "SIM-" + uuid.uuid4().hex[:12].upper()
            cur = conn.execute(
                """UPDATE assistance_request SET status = 'Paid', paid_by = ?, paid_at = ?
                   WHERE id = ? AND nonprofit_id = ? AND status = 'Approved'""",
                (caller.staff_id, now, rid, caller.nonprofit_id),
            )
            if cur.rowcount != 1:  # pragma: no cover - defence in depth
                raise self._transition_error(self._request_row(conn, caller, rid), "pay")
            # UNIQUE(request_id) is a second, storage-level guard against double payment.
            conn.execute(
                """INSERT INTO payment (id, request_id, nonprofit_id, amount_cents, simulated_reference,
                                        recorded_by, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (pay_id, rid, caller.nonprofit_id, row["amount_cents"], reference, caller.staff_id, now),
            )
            self._audit(conn, caller, "request.paid", rid, {"amount_cents": row["amount_cents"], "payment_id": pay_id})
            result = PaymentResult(
                request=self._to_request(self._request_row(conn, caller, rid)),
                payment=Payment(
                    id=pay_id,
                    request_id=rid,
                    amount=_m(row["amount_cents"]),
                    simulated_reference=reference,
                    recorded_by=caller.staff_id,
                    recorded_at=now,
                ),
                fund=self._fund_summary(conn, caller, row["fund_id"]),
            )
        return result

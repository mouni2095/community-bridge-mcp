"""BR-3 / AC-17: competing approvals cannot overspend a fund.

Fund "fund-cb-transport" has $500.00. We create 10 Pending requests of $100.00
and release 10 approvers simultaneously (separate OS processes, each with its
own SQLite connection, like separate MCP server processes sharing the file).
Exactly 5 must succeed and 5 must fail with INSUFFICIENT_FUNDS.

A control test runs the same race against a deliberately non-atomic approval
(check and write in separate transactions) and shows it overspends, proving
the race test is capable of detecting the bug it guards against.
"""

from __future__ import annotations

import multiprocessing as mp
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from community_bridge import db
from community_bridge.errors import DomainError, ErrorCode
from community_bridge.service import AssistanceService

from .concurrency_worker import approve_worker

N = 10
AMOUNT = "100.00"
FUND = "fund-cb-transport"  # $500.00


def _create_requests(make_request, n=N):
    return [make_request(AMOUNT, fund_id=FUND, purpose="transportation").id for _ in range(n)]


def _assert_no_overspend(svc, who, outcomes):
    f = svc.funding_summary(who("cb-finance"), FUND).funds[0]
    assert sorted(outcomes) == ["INSUFFICIENT_FUNDS"] * 5 + ["ok"] * 5, outcomes
    assert f.committed.amount == "500.00"
    assert f.available.cents == 0
    assert f.request_counts.Approved == 5 and f.request_counts.Pending == 5


@pytest.mark.parametrize("run", range(5))
def test_ac17_concurrent_approvals_across_processes(svc, who, make_request, db_path, run):
    ids = _create_requests(make_request)
    ctx = mp.get_context("spawn")
    barrier, results = ctx.Barrier(N), ctx.Queue()
    procs = [ctx.Process(target=approve_worker, args=(str(db_path), rid, barrier, results)) for rid in ids]
    for p in procs:
        p.start()
    outcomes = [results.get(timeout=60)[1] for _ in procs]
    for p in procs:
        p.join(timeout=30)
    assert not any(o.startswith("CRASH") for o in outcomes), outcomes
    _assert_no_overspend(svc, who, outcomes)


@pytest.mark.parametrize("run", range(20))
def test_ac17_concurrent_approvals_across_threads(svc, who, make_request, run):
    ids = _create_requests(make_request)
    pm = who("cb-program-manager")
    barrier = threading.Barrier(N)

    def approve(rid):
        barrier.wait()
        try:
            svc.approve_request(pm, rid)
            return "ok"
        except DomainError as err:
            return str(err.code)

    with ThreadPoolExecutor(N) as pool:
        outcomes = list(pool.map(approve, ids))
    _assert_no_overspend(svc, who, outcomes)


def test_concurrent_double_approval_of_same_request(svc, who, make_request):
    rid = make_request("300.00")
    pm = who("cb-program-manager")
    barrier = threading.Barrier(8)

    def approve(_):
        barrier.wait()
        try:
            svc.approve_request(pm, rid.id)
            return "ok"
        except DomainError as err:
            return str(err.code)

    with ThreadPoolExecutor(8) as pool:
        outcomes = list(pool.map(approve, range(8)))
    assert sorted(outcomes) == ["INVALID_TRANSITION"] * 7 + ["ok"]
    assert svc.funding_summary(who("cb-finance"), "fund-cb-rent").funds[0].committed.amount == "300.00"


def test_concurrent_double_payment_of_same_request(svc, who, make_request, db_path):
    rid = make_request("300.00").id
    svc.approve_request(who("cb-program-manager"), rid)
    fin = who("cb-finance")
    barrier = threading.Barrier(8)

    def pay(_):
        barrier.wait()
        try:
            svc.record_payment(fin, rid)
            return "ok"
        except DomainError as err:
            return str(err.code)

    with ThreadPoolExecutor(8) as pool:
        outcomes = list(pool.map(pay, range(8)))
    assert sorted(outcomes) == ["INVALID_TRANSITION"] * 7 + ["ok"]
    conn = db.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM payment WHERE request_id = ?", (rid,)).fetchone()[0] == 1
    conn.close()


# --- Control: prove the race test detects a non-atomic implementation -----------------
class NonAtomicService(AssistanceService):
    """Check-then-act WITHOUT a surrounding write transaction: the bug BR-3 forbids."""

    def approve_request(self, caller, request_id, note=None):
        with self._conn() as conn:
            row = self._request_row(conn, caller, request_id)
            fund = self._fund_summary(conn, caller, row["fund_id"])
            if fund.available.cents < row["amount_cents"]:
                raise DomainError(ErrorCode.INSUFFICIENT_FUNDS, "insufficient")
            time.sleep(0.05)  # widen the window between check and write
            conn.execute(
                "UPDATE assistance_request SET status='Approved', decided_by=?, decided_at='t' WHERE id=?",
                (caller.staff_id, request_id),
            )


def test_control_non_atomic_approval_overspends(db_path, who, make_request):
    ids = _create_requests(make_request)
    unsafe = NonAtomicService(db_path)
    pm = who("cb-program-manager")
    barrier = threading.Barrier(N)

    def approve(rid):
        barrier.wait()
        try:
            unsafe.approve_request(pm, rid)
            return "ok"
        except DomainError as err:
            return str(err.code)

    with ThreadPoolExecutor(N) as pool:
        outcomes = list(pool.map(approve, ids))
    f = AssistanceService(db_path).funding_summary(pm, FUND).funds[0]
    # The unsafe version approves more than the fund holds; the real one never does.
    assert outcomes.count("ok") > 5
    assert f.available.cents < 0

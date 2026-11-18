"""SEC-1 role matrix, SEC-2 tenant isolation, FR-1 identity (AC-11..13)."""

from __future__ import annotations

import pytest

from community_bridge.errors import ErrorCode
from community_bridge.models import Status

from .conftest import expect_error, snapshot

CREATE = dict(participant_id="p-cb-001", fund_id="fund-cb-rent", purpose="rent", amount="10.00", description="x")


# --- FR-1 / AC-13 -----------------------------------------------------------------------
@pytest.mark.parametrize("staff_id", [None, "", "   ", "nobody", "CB-CASE-WORKER", "cb-former-staff"])
def test_ac13_missing_unknown_inactive_identity(svc, staff_id):
    err = expect_error(ErrorCode.UNAUTHENTICATED, svc.authenticate, staff_id)
    assert "cb-former-staff" not in err.message  # no hint about which IDs exist


def test_identity_resolves_role_and_nonprofit_from_server_data(who):
    c = who("hh-finance")
    assert (c.role, c.nonprofit_id) == ("finance", "np_harbor_hope")


# --- SEC-1 / AC-11 ----------------------------------------------------------------------
@pytest.mark.parametrize("staff_id", ["cb-program-manager", "cb-finance"])
def test_only_case_workers_create(svc, who, db_path, staff_id):
    before = snapshot(db_path)
    expect_error(ErrorCode.FORBIDDEN, svc.create_request, who(staff_id), **CREATE)
    assert snapshot(db_path) == before


@pytest.mark.parametrize("staff_id", ["cb-case-worker", "cb-finance"])
def test_only_program_managers_decide(svc, who, make_request, db_path, staff_id):
    req = make_request()
    before = snapshot(db_path)
    expect_error(ErrorCode.FORBIDDEN, svc.approve_request, who(staff_id), req.id)
    expect_error(ErrorCode.FORBIDDEN, svc.reject_request, who(staff_id), req.id, reason="r")
    assert snapshot(db_path) == before


@pytest.mark.parametrize("staff_id", ["cb-case-worker", "cb-program-manager"])
def test_only_finance_pays(svc, who, make_request, db_path, staff_id):
    req = make_request()
    svc.approve_request(who("cb-program-manager"), req.id)
    before = snapshot(db_path)
    expect_error(ErrorCode.FORBIDDEN, svc.record_payment, who(staff_id), req.id)
    assert snapshot(db_path) == before


def test_forbidden_checked_before_input_validation(svc, who):
    # A finance user gets FORBIDDEN for create even with garbage input: no probing via validation errors.
    expect_error(ErrorCode.FORBIDDEN, svc.create_request, who("cb-finance"), **{**CREATE, "amount": "abc"})


@pytest.mark.parametrize("staff_id", ["cb-case-worker", "cb-program-manager", "cb-finance"])
def test_all_roles_can_read(svc, who, make_request, staff_id):
    req = make_request()
    c = who(staff_id)
    assert svc.get_request(c, req.id).request.id == req.id
    assert len(svc.list_requests(c).requests) == 1
    assert len(svc.list_funds(c).funds) == 3
    assert len(svc.list_participants(c).participants) == 3
    assert svc.funding_summary(c).totals.allocation.amount == "13000.00"
    assert svc.whoami(c).nonprofit_id == "np_community_bridge"


# --- SEC-2 / AC-12 ----------------------------------------------------------------------
def test_ac12_other_nonprofit_cannot_act_on_or_see_requests(svc, who, make_request, db_path):
    req = make_request()
    before = snapshot(db_path)
    expect_error(ErrorCode.NOT_FOUND, svc.approve_request, who("hh-program-manager"), req.id)
    expect_error(ErrorCode.NOT_FOUND, svc.reject_request, who("hh-program-manager"), req.id, reason="r")
    expect_error(ErrorCode.NOT_FOUND, svc.get_request, who("hh-case-worker"), req.id)
    svc.approve_request(who("cb-program-manager"), req.id)
    expect_error(ErrorCode.NOT_FOUND, svc.record_payment, who("hh-finance"), req.id)
    assert svc.list_requests(who("hh-finance")).requests == []
    assert snapshot(db_path)["payment"] == before["payment"]


def test_ac12_cannot_use_other_nonprofits_fund_or_participant(svc, who, db_path):
    hh = who("hh-case-worker")
    before = snapshot(db_path)
    # Harbor Hope participant + Community Bridge fund, and vice versa.
    expect_error(ErrorCode.NOT_FOUND, svc.create_request, hh, **{**CREATE, "participant_id": "p-hh-001"})
    expect_error(ErrorCode.NOT_FOUND, svc.create_request, hh, **{**CREATE, "fund_id": "fund-hh-general"})
    assert snapshot(db_path) == before


def test_ac12_reads_are_scoped(svc, who):
    hh = who("hh-program-manager")
    assert [f.id for f in svc.list_funds(hh).funds] == ["fund-hh-general"]
    assert {p.id for p in svc.list_participants(hh).participants} == {"p-hh-001", "p-hh-002"}
    assert svc.list_participants(hh, search="p-cb-001").participants == []
    expect_error(ErrorCode.NOT_FOUND, svc.funding_summary, hh, "fund-cb-rent")
    assert svc.list_requests(hh, fund_id="fund-cb-rent").requests == []


def test_not_found_is_identical_for_foreign_and_missing_ids(svc, who, make_request):
    req = make_request()
    hh = who("hh-program-manager")
    foreign = expect_error(ErrorCode.NOT_FOUND, svc.get_request, hh, req.id)
    missing = expect_error(ErrorCode.NOT_FOUND, svc.get_request, hh, "req_" + "0" * 32)
    assert foreign.message == missing.message


def test_storage_rejects_cross_tenant_reference(db_path):
    """Composite FKs: even raw SQL cannot attach a CB request to an HH fund."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO assistance_request (id, nonprofit_id, participant_id, fund_id, purpose, description,
               amount_cents, status, created_by, created_at)
               VALUES ('r', 'np_community_bridge', 'p-cb-001', 'fund-hh-general', 'rent', 'x', 1, 'Pending',
                       'cb-case-worker', 'now')"""
        )
    conn.close()


def test_hh_workflow_isolated_from_cb_funds(svc, who, make_request):
    make_request("300.00")
    hh_req = svc.create_request(
        who("hh-case-worker"), participant_id="p-hh-001", fund_id="fund-hh-general",
        purpose="utilities", amount="125.50", description="Electric bill",
    ).request
    res = svc.approve_request(who("hh-program-manager"), hh_req.id)
    assert res.request.status == Status.APPROVED
    assert res.fund.available.amount == "4874.50"
    assert svc.funding_summary(who("cb-finance"), "fund-cb-rent").funds[0].committed.cents == 0

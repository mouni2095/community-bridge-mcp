"""NFR-2: reset is repeatable and wipes prior state."""

from community_bridge.seed import reset_database
from community_bridge.service import AssistanceService

from .conftest import snapshot


def test_reset_is_repeatable_and_wipes_requests(tmp_path, ):
    path = reset_database(tmp_path / "a.db")
    fresh = snapshot(path)
    svc = AssistanceService(path)
    svc.create_request(svc.authenticate("cb-case-worker"), participant_id="p-cb-001", fund_id="fund-cb-rent",
                       purpose="rent", amount="1.00", description="x")
    assert snapshot(path) != fresh
    reset_database(path)
    assert snapshot(path) == fresh


def test_seed_has_two_nonprofits_with_staff_participants_funds(tmp_path):
    s = snapshot(reset_database(tmp_path / "b.db"))
    assert len(s["nonprofit"]) == 2
    for np_id in ("np_community_bridge", "np_harbor_hope"):
        roles = {r[3] for r in s["staff"] if r[1] == np_id and r[4] == 1}
        assert roles == {"case_worker", "program_manager", "finance"}
        assert any(p[1] == np_id for p in s["participant"])
        assert any(f[1] == np_id for f in s["fund"])

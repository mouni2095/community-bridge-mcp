"""End-to-end over the real MCP stdio transport (AC-19, NFR-3, NFR-5).

Each session spawns `python -m community_bridge` as a subprocess with a
different CB_STAFF_ID, exactly as an MCP client launcher would.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

pytestmark = pytest.mark.e2e

EXPECTED_TOOLS = {
    "whoami", "list_participants", "list_funds", "get_funding_summary", "create_request",
    "approve_request", "reject_request", "record_payment", "get_request", "list_requests",
}


def _env(db_path: Path, staff_id: str | None, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CB_")}
    env["CB_DB_PATH"] = str(db_path)
    env["CB_LOG_LEVEL"] = "WARNING"
    if staff_id is not None:
        env["CB_STAFF_ID"] = staff_id
    env.update(extra)
    return env


@asynccontextmanager
async def session(db_path: Path, staff_id: str | None, **extra_env: str):
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "community_bridge"], env=_env(db_path, staff_id, **extra_env)
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            yield s


async def call_ok(s: ClientSession, tool: str, args: dict | None = None) -> dict:
    res = await s.call_tool(tool, args or {})
    assert not res.is_error, res.content[0].text
    assert res.structured_content is not None
    return res.structured_content


async def call_err(s: ClientSession, tool: str, args: dict | None = None) -> dict:
    res = await s.call_tool(tool, args or {})
    assert res.is_error, res
    return json.loads(res.content[0].text)["error"]


async def test_tools_are_discoverable_with_schemas(db_path):
    async with session(db_path, "cb-case-worker") as s:
        tools = (await s.list_tools()).tools
    assert {t.name for t in tools} == EXPECTED_TOOLS
    by_name = {t.name: t for t in tools}
    for t in tools:
        assert t.description and t.input_schema["type"] == "object" and t.output_schema, t.name
        assert "staff_id" not in t.input_schema.get("properties", {})  # SEC-3: identity is never an argument
        assert "nonprofit_id" not in t.input_schema.get("properties", {})
    create = by_name["create_request"].input_schema
    assert set(create["required"]) == {"participant_id", "fund_id", "purpose", "amount", "description"}
    assert by_name["approve_request"].annotations.read_only_hint is False
    assert by_name["list_funds"].annotations.read_only_hint is True


async def test_ac19_full_workflow_over_mcp(db_path):
    async with session(db_path, "cb-case-worker") as cw:
        assert (await call_ok(cw, "whoami"))["role"] == "case_worker"
        created = await call_ok(cw, "create_request", {
            "participant_id": "p-cb-001", "fund_id": "fund-cb-rent", "purpose": "rent",
            "amount": "300.00", "description": "November rent gap", "idempotency_key": "e2e-1",
        })
        rid = created["request"]["id"]
        assert created["request"]["status"] == "Pending" and created["replayed"] is False

    async with session(db_path, "cb-program-manager") as pm:
        approved = await call_ok(pm, "approve_request", {"request_id": rid})
        assert approved["fund"]["available"]["amount"] == "9700.00"
        assert approved["fund"]["committed"]["amount"] == "300.00"
        again = await call_err(pm, "approve_request", {"request_id": rid})
        assert again["code"] == "INVALID_TRANSITION"

    async with session(db_path, "cb-finance") as fin:
        paid = await call_ok(fin, "record_payment", {"request_id": rid})
        assert paid["request"]["status"] == "Paid"
        retry = await call_err(fin, "record_payment", {"request_id": rid})
        assert retry["code"] == "INVALID_TRANSITION" and retry["details"]["current_status"] == "Paid"
        summary = await call_ok(fin, "get_funding_summary", {"fund_id": "fund-cb-rent"})
        f = summary["funds"][0]
        assert (f["paid"]["amount"], f["committed"]["amount"], f["available"]["amount"]) == ("300.00", "0.00", "9700.00")
        got = await call_ok(fin, "get_request", {"request_id": rid})
        assert got["request"]["payment_id"] == paid["payment"]["id"]


@pytest.mark.parametrize("staff_id", [None, "nobody", "cb-former-staff"])
async def test_ac13_identity_rejected_over_mcp(db_path, staff_id):
    async with session(db_path, staff_id) as s:
        for tool, args in [("whoami", {}), ("list_funds", {}), ("get_request", {"request_id": "x"})]:
            assert (await call_err(s, tool, args))["code"] == "UNAUTHENTICATED"


async def test_role_and_tenant_enforced_over_mcp(db_path):
    async with session(db_path, "cb-case-worker") as cw:
        rid = (await call_ok(cw, "create_request", {
            "participant_id": "p-cb-002", "fund_id": "fund-cb-utilities", "purpose": "utilities",
            "amount": "80.25", "description": "Gas bill",
        }))["request"]["id"]
        assert (await call_err(cw, "approve_request", {"request_id": rid}))["code"] == "FORBIDDEN"
    async with session(db_path, "hh-program-manager") as hh:
        assert (await call_err(hh, "approve_request", {"request_id": rid}))["code"] == "NOT_FOUND"
        assert (await call_ok(hh, "list_requests"))["requests"] == []
    async with session(db_path, "cb-finance") as fin:
        assert (await call_ok(fin, "get_request", {"request_id": rid}))["request"]["status"] == "Pending"


async def test_schema_validation_over_mcp(db_path):
    async with session(db_path, "cb-case-worker") as cw:
        base = {"participant_id": "p-cb-001", "fund_id": "fund-cb-rent", "purpose": "rent", "description": "x"}
        for bad in (300, "300.001", "-1", "abc"):
            res = await cw.call_tool("create_request", {**base, "amount": bad})
            assert res.is_error, bad
        res = await cw.call_tool("create_request", {**base, "amount": "1.00", "purpose": "groceries"})
        assert res.is_error
        # Passes the schema regex but fails the domain rule (> 0): structured domain error.
        assert (await call_err(cw, "create_request", {**base, "amount": "0.00"}))["code"] == "VALIDATION_ERROR"
        assert (await call_ok(cw, "list_requests"))["requests"] == []


def test_server_refuses_to_start_without_database(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "community_bridge"], env=_env(tmp_path / "missing.db", "cb-case-worker"),
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2
    assert proc.stdout == ""  # nothing on the protocol channel
    assert "community_bridge.seed" in proc.stderr
    assert not (tmp_path / "missing.db").exists()  # NFR-2: startup never creates data


async def test_unknown_fund_summary_over_mcp(db_path):
    async with session(db_path, "hh-finance") as s:
        # Exists, but belongs to the other nonprofit.
        assert (await call_err(s, "get_funding_summary", {"fund_id": "fund-cb-rent"}))["code"] == "NOT_FOUND"


async def test_log_file_has_audit_lines_but_no_participant_data(db_path, tmp_path):
    log_file = tmp_path / "server.log"
    async with session(db_path, "cb-case-worker", CB_LOG_LEVEL="INFO", CB_LOG_FILE=str(log_file)) as cw:
        rid = (await call_ok(cw, "create_request", {
            "participant_id": "p-cb-001", "fund_id": "fund-cb-rent", "purpose": "rent",
            "amount": "12.00", "description": "SENSITIVE-DETAIL eviction notice",
        }))["request"]["id"]
        await call_err(cw, "approve_request", {"request_id": rid})
    text = log_file.read_text()
    assert f"tool=create_request staff=cb-case-worker outcome=ok request={rid}" in text
    assert f"tool=approve_request staff=cb-case-worker outcome=FORBIDDEN" in text
    assert "SENSITIVE-DETAIL" not in text
    assert "Rivera" not in text  # participant surname

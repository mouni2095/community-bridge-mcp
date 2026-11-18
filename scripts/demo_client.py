"""Reproducible MCP client demonstration (no language model involved).

Spawns the server over stdio once per fixture identity and runs:
discover tools -> create (case worker) -> approve (program manager)
-> record payment (finance) -> final funding summary.

    python -m community_bridge.seed          # reset first for a clean run
    python scripts/demo_client.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

DB = os.environ.get("CB_DB_PATH") or str(Path.cwd() / "data" / "community_bridge.db")


def params(staff_id: str) -> StdioServerParameters:
    env = {**os.environ, "CB_DB_PATH": DB, "CB_STAFF_ID": staff_id, "CB_LOG_LEVEL": "WARNING"}
    return StdioServerParameters(command=sys.executable, args=["-m", "community_bridge"], env=env)


async def as_staff(staff_id: str, steps):
    async with stdio_client(params(staff_id)) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        me = (await s.call_tool("whoami", {})).structured_content
        print(f"\n=== Connected as {me['staff_id']} ({me['role']}, {me['nonprofit_name']}) ===")
        return await steps(s)


async def call(s: ClientSession, tool: str, args: dict) -> dict:
    res = await s.call_tool(tool, args)
    if res.is_error:
        raise SystemExit(f"{tool} failed: {res.content[0].text}")
    print(f"-> {tool}({json.dumps(args)})")
    return res.structured_content


def show_fund(f: dict) -> None:
    print(f"   {f['name']}: allocation {f['allocation']['amount']} | committed {f['committed']['amount']} | "
          f"paid {f['paid']['amount']} | available {f['available']['amount']}")


async def main() -> None:
    async def discover_and_create(s):
        tools = (await s.list_tools()).tools
        print(f"Discovered {len(tools)} tools: {', '.join(sorted(t.name for t in tools))}")
        funds = await call(s, "list_funds", {})
        show_fund(next(f for f in funds["funds"] if f["id"] == "fund-cb-rent"))
        out = await call(s, "create_request", {
            "participant_id": "p-cb-001", "fund_id": "fund-cb-rent", "purpose": "rent",
            "amount": "300.00", "description": "November rent shortfall",
        })
        print(f"   created {out['request']['id']} status={out['request']['status']}")
        return out["request"]["id"]

    rid = await as_staff("cb-case-worker", discover_and_create)

    async def approve(s):
        out = await call(s, "approve_request", {"request_id": rid, "note": "Lease and ledger verified"})
        print(f"   status={out['request']['status']}")
        show_fund(out["fund"])

    await as_staff("cb-program-manager", approve)

    async def pay_and_summarise(s):
        out = await call(s, "record_payment", {"request_id": rid})
        print(f"   status={out['request']['status']} payment={out['payment']['id']} "
              f"ref={out['payment']['simulated_reference']}")
        retry = await s.call_tool("record_payment", {"request_id": rid})
        print(f"-> record_payment retry: isError={retry.is_error} "
              f"code={json.loads(retry.content[0].text)['error']['code']}")
        summary = await call(s, "get_funding_summary", {"fund_id": "fund-cb-rent"})
        print("\nFinal funding summary:")
        show_fund(summary["funds"][0])

    await as_staff("cb-finance", pay_and_summarise)


if __name__ == "__main__":
    if not Path(DB).exists():
        raise SystemExit(f"Database not found at {DB}. Run: python -m community_bridge.seed")
    asyncio.run(main())

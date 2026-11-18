"""MCP adapter (spec §4). Each tool: resolve caller from server config ->
call the service -> return a typed model, or an isError result whose text is
the JSON error envelope. Identity is never a tool argument (SEC-3)."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from .config import configured_staff_id, default_db_path, setup_logging
from .errors import DomainError, ErrorCode
from .models import (
    CreateRequestResult,
    DecisionResult,
    FundingSummary,
    FundList,
    ParticipantList,
    PaymentResult,
    RequestList,
    RequestResult,
    WhoAmI,
)
from .money import AMOUNT_PATTERN
from .service import AssistanceService

log = logging.getLogger("community_bridge.server")

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)

RequestId = Annotated[str, Field(min_length=1, max_length=100, description="Assistance request ID, e.g. req_…")]
FundId = Annotated[str, Field(min_length=1, max_length=100, description="Fund ID from list_funds")]
ParticipantId = Annotated[str, Field(min_length=1, max_length=100, description="Participant ID from list_participants")]
Limit = Annotated[int, Field(ge=1, le=200, description="Maximum rows to return (default 50)")]

INSTRUCTIONS = (
    "Emergency assistance funding for a nonprofit. Your identity, role and nonprofit are fixed by the server "
    "configuration; call whoami to see them. Workflow: case_worker create_request -> program_manager "
    "approve_request or reject_request -> finance record_payment. Amounts are USD decimal strings like \"300.00\". "
    "Errors are returned as JSON {\"error\": {\"code\", \"message\", \"details\"}}."
)


def _error_result(err: DomainError) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(err.to_dict()))], is_error=True)


def create_server(db_path: str | Path, staff_id: str | None) -> MCPServer:
    service = AssistanceService(db_path)
    mcp = MCPServer("community-bridge", instructions=INSTRUCTIONS, version="1.0.0")

    def run(tool: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        caller_id = staff_id or "<none>"
        try:
            caller = service.authenticate(staff_id)  # FR-1: re-resolved on every call
            result = fn(caller, *args, **kwargs)
            log.info("tool=%s staff=%s outcome=ok", tool, caller_id)
            return result
        except DomainError as err:
            log.info("tool=%s staff=%s outcome=%s", tool, caller_id, err.code)
            return _error_result(err)
        except Exception:
            log.exception("tool=%s staff=%s outcome=INTERNAL_ERROR", tool, caller_id)
            return _error_result(DomainError(ErrorCode.INTERNAL_ERROR, "Internal error; see server logs"))

    @mcp.tool(annotations=READ)
    def whoami() -> WhoAmI:
        """Return the staff identity this server acts as: role, nonprofit and permissions."""
        return run("whoami", service.whoami)

    @mcp.tool(annotations=READ)
    def list_participants(
        search: Annotated[str | None, Field(max_length=100, description="Name fragment or exact participant ID")] = None,
        limit: Limit | None = None,
    ) -> ParticipantList:
        """List participants of your nonprofit, optionally filtered by name or ID."""
        return run("list_participants", service.list_participants, search, limit)

    @mcp.tool(annotations=READ)
    def list_funds() -> FundList:
        """List your nonprofit's funds with allocation, committed, paid and available amounts."""
        return run("list_funds", service.list_funds)

    @mcp.tool(annotations=READ)
    def get_funding_summary(fund_id: FundId | None = None) -> FundingSummary:
        """Funding summary for one fund (if fund_id is given) or all funds, with totals.
        available = allocation - committed (approved, unpaid) - paid."""
        return run("get_funding_summary", service.funding_summary, fund_id)

    @mcp.tool(annotations=WRITE)
    def create_request(
        participant_id: ParticipantId,
        fund_id: FundId,
        purpose: Annotated[Literal["rent", "utilities", "transportation", "other"], Field(description="Expense type")],
        amount: Annotated[
            str, Field(pattern=AMOUNT_PATTERN, description='USD amount as a decimal string, e.g. "300.00"')
        ],
        description: Annotated[str, Field(min_length=1, max_length=500, description="What the money is for")],
        idempotency_key: Annotated[
            str | None,
            Field(
                min_length=1,
                max_length=100,
                pattern=r"^[A-Za-z0-9_.:-]+$",
                description="Optional client key; retrying with the same key and payload returns the original request",
            ),
        ] = None,
    ) -> CreateRequestResult:
        """Submit an assistance request (case_worker only). Starts Pending; does not reserve funds.
        Requests cannot be edited after submission."""
        return run(
            "create_request",
            service.create_request,
            participant_id,
            fund_id,
            purpose,
            amount,
            description,
            idempotency_key,
        )

    @mcp.tool(annotations=WRITE)
    def approve_request(
        request_id: RequestId,
        note: Annotated[str | None, Field(max_length=500, description="Optional approval note")] = None,
    ) -> DecisionResult:
        """Approve a Pending request (program_manager only). Reserves the full amount against its fund;
        fails with INSUFFICIENT_FUNDS if the fund's available balance is too low."""
        return run("approve_request", service.approve_request, request_id, note)

    @mcp.tool(annotations=WRITE)
    def reject_request(
        request_id: RequestId,
        reason: Annotated[str, Field(min_length=1, max_length=500, description="Reason for rejection")],
    ) -> DecisionResult:
        """Reject a Pending request (program_manager only). Funding is unchanged."""
        return run("reject_request", service.reject_request, request_id, reason)

    @mcp.tool(annotations=WRITE)
    def record_payment(request_id: RequestId) -> PaymentResult:
        """Record a simulated payment of the full amount for an Approved request (finance only).
        Moves the amount from committed to paid; available is unchanged. Succeeds once per request."""
        return run("record_payment", service.record_payment, request_id)

    @mcp.tool(annotations=READ)
    def get_request(request_id: RequestId) -> RequestResult:
        """Get one assistance request of your nonprofit by ID."""
        return run("get_request", service.get_request, request_id)

    @mcp.tool(annotations=READ)
    def list_requests(
        status: Literal["Pending", "Approved", "Rejected", "Paid"] | None = None,
        fund_id: FundId | None = None,
        participant_id: ParticipantId | None = None,
        limit: Limit | None = None,
    ) -> RequestList:
        """List your nonprofit's assistance requests, newest first, with optional filters."""
        return run("list_requests", service.list_requests, status, fund_id, participant_id, limit)

    return mcp


def main() -> int:
    setup_logging()
    db_path = default_db_path()
    if not db_path.exists():
        log.error("Database not found at %s. Run the setup command first: python -m community_bridge.seed", db_path)
        return 2
    staff_id = configured_staff_id()
    if staff_id is None:
        log.warning("CB_STAFF_ID is not set; every tool call will return UNAUTHENTICATED")
    log.info("Starting community-bridge MCP server (stdio) db=%s staff=%s", db_path, staff_id)
    server = create_server(db_path, staff_id)
    setup_logging()  # re-assert stderr-only logging in case the SDK configured handlers
    server.run()  # stdio
    return 0


if __name__ == "__main__":
    sys.exit(main())

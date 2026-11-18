"""Typed results. These pydantic models become the tools' published output
schemas (NFR-5)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Role(StrEnum):
    CASE_WORKER = "case_worker"
    PROGRAM_MANAGER = "program_manager"
    FINANCE = "finance"


class Status(StrEnum):
    PENDING = "Pending"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    PAID = "Paid"


Purpose = Literal["rent", "utilities", "transportation", "other"]


@dataclass(frozen=True)
class Caller:
    """Server-resolved identity. Only ever built from the staff table (SEC-3)."""

    staff_id: str
    display_name: str
    role: Role
    nonprofit_id: str
    nonprofit_name: str


class Money(BaseModel):
    cents: int = Field(description="Exact amount in integer cents")
    amount: str = Field(description='Decimal string, e.g. "300.00"')
    currency: Literal["USD"] = "USD"


class WhoAmI(BaseModel):
    staff_id: str
    display_name: str
    role: Role
    nonprofit_id: str
    nonprofit_name: str
    permissions: list[str]


class Participant(BaseModel):
    id: str
    first_name: str
    last_name: str


class ParticipantList(BaseModel):
    participants: list[Participant]


class RequestCounts(BaseModel):
    Pending: int = 0
    Approved: int = 0
    Rejected: int = 0
    Paid: int = 0


class FundSummary(BaseModel):
    id: str
    name: str
    allocation: Money
    committed: Money = Field(description="Approved, not yet paid")
    paid: Money
    available: Money = Field(description="allocation - committed - paid")
    request_counts: RequestCounts


class FundList(BaseModel):
    funds: list[FundSummary]


class FundingTotals(BaseModel):
    allocation: Money
    committed: Money
    paid: Money
    available: Money


class FundingSummary(BaseModel):
    funds: list[FundSummary]
    totals: FundingTotals


class AssistanceRequest(BaseModel):
    id: str
    participant_id: str
    participant_name: str
    fund_id: str
    fund_name: str
    purpose: Purpose
    description: str
    amount: Money
    status: Status
    created_by: str
    created_at: str
    decided_by: str | None = None
    decided_at: str | None = None
    decision_note: str | None = Field(None, description="Approval note or rejection reason")
    paid_by: str | None = None
    paid_at: str | None = None
    payment_id: str | None = None


class RequestResult(BaseModel):
    request: AssistanceRequest


class CreateRequestResult(BaseModel):
    request: AssistanceRequest
    replayed: bool = Field(description="True if an earlier request with the same idempotency_key was returned")


class DecisionResult(BaseModel):
    request: AssistanceRequest
    fund: FundSummary


class Payment(BaseModel):
    id: str
    request_id: str
    amount: Money
    simulated_reference: str
    recorded_by: str
    recorded_at: str


class PaymentResult(BaseModel):
    request: AssistanceRequest
    payment: Payment
    fund: FundSummary


class RequestList(BaseModel):
    requests: list[AssistanceRequest]

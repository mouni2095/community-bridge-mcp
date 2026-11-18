# Community Bridge — Emergency Assistance MCP Server

A headless MCP server (stdio) for submitting, approving and paying emergency assistance requests against fixed grant funds. It covers multiple nonprofits, enforces roles and never overspends a fund.

| Document | Purpose |
|---|---|
| [`docs/SPEC.md`](docs/SPEC.md) | Specification: requirements (IDs), state machine, data model, MCP interface, permissions, acceptance criteria, plan, production security, open questions, revision history |
| [`docs/TRACEABILITY.md`](docs/TRACEABILITY.md) | Requirement → code → test mapping, mutation checks, what wasn't verified |
| [`docs/AI_USAGE.md`](docs/AI_USAGE.md) | Three AI-assisted examples with evidence |
| [`docs/tool-schemas.json`](docs/tool-schemas.json) | Generated input/output JSON Schemas for all 10 tools |
| [`docs/test-results.txt`](docs/test-results.txt), [`docs/demo-output.txt`](docs/demo-output.txt) | Actual test and demo output |

`git log` shows the spec (v1.0) was committed before any implementation code.

## Setup (Python 3.12+; developed on 3.13)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.lock          # exact pinned versions (mcp==2.3.0, pytest==8.4.2, …)
pip install -e . --no-deps
python -m community_bridge.seed           # setup/reset: deletes and re-seeds ./data/community_bridge.db
```

Reset is a separate command and is safe to repeat. The server never creates or seeds data: it exits with code 2 if the database is missing. Override the location with `CB_DB_PATH` or `--db`.

## Run

```bash
CB_STAFF_ID=cb-case-worker python -m community_bridge     # stdio MCP server
```

| Env var | Meaning |
|---|---|
| `CB_STAFF_ID` | Fixture identity the server acts as (required for any tool to succeed) |
| `CB_DB_PATH` | SQLite file (default `./data/community_bridge.db`) |
| `CB_LOG_LEVEL`, `CB_LOG_FILE` | Logging. Logs go to stderr and/or the file, never stdout. |

**Fixture identities**

| Nonprofit | Case worker | Program manager | Finance |
|---|---|---|---|
| Community Bridge | `cb-case-worker` | `cb-program-manager` | `cb-finance` |
| Harbor Hope Services | `hh-case-worker` | `hh-program-manager` | `hh-finance` |

`cb-former-staff` is inactive, for testing.

**MCP client config:** see [`examples/mcp_client_config.json`](examples/mcp_client_config.json). It defines one server entry per role and works with Claude Desktop or any stdio MCP client.

**Demo (no LLM):** `python -m community_bridge.seed && python scripts/demo_client.py`. The script discovers tools, then runs create (case worker) → approve (program manager) → pay (finance), including a payment retry, and prints the final summary: paid $300, committed $0, available $9,700.

## Test

```bash
pytest            # 125 tests: unit, service, permissions, concurrency, MCP end-to-end (~20 s)
pytest -m e2e     # only the end-to-end tests over real stdio
```

Latest run: **125 passed, 0 failed, 0 skipped** (`docs/test-results.txt`). I also ran the concurrency file 5 more times; it passed every time.

## Key design decisions

- **Service layer owns all rules; the MCP layer is a thin adapter.** It resolves the caller, calls the service, and maps `DomainError` to an `isError` result with a JSON `{"error":{code,message,details}}` body. The same rules are tested directly and through MCP.
- **Identity comes from server config, never from tool input.** `CB_STAFF_ID` is re-resolved against the `staff` table on every call. No tool has an identity, role or nonprofit parameter. Free text is stored only as data.
- **Tenant isolation is enforced at two layers:**
  - Every query is filtered by the caller's `nonprofit_id`, and foreign IDs return `NOT_FOUND`.
  - Composite foreign keys stop the database from linking a request to another nonprofit's fund or participant.
- **Money:** amounts arrive as decimal strings, are parsed with `Decimal` and stored as integer cents. Output is `{cents, amount, currency}`.
- **Totals are derived, not stored:** `committed`, `paid` and `available` are computed from request rows, so they can't drift.
- **Concurrency:** every write runs in a SQLite `BEGIN IMMEDIATE` transaction (WAL, 5 s busy timeout). The availability check and the status change are serialised across threads *and processes*. Verification:
  - 10 approvers are released at once against a $500 fund with $100 requests, in separate processes (5 runs) and threads (20 runs). Exactly 5 succeed every time.
  - A control test shows a non-atomic version does overspend under the same race.
- **Retries:**
  - Repeated approve/pay calls get `INVALID_TRANSITION` with the current status.
  - The payment table has `UNIQUE(request_id)` as a second guard.
  - `create_request` accepts an optional `idempotency_key`.

## Assumptions

Spec §1.4 and §10 list all of them. The main ones:

- Each staff member has one nonprofit and one role.
- Funds have no purpose restrictions or expiry.
- A request may be submitted above current availability; the check happens at approval.
- There's a $1,000,000 cap per request.

## Limitations and next priorities

- **Single-file SQLite:** fine for local stdio and several processes on one machine. Not suitable for network file systems or high write concurrency. Next step would be Postgres with `SELECT … FOR UPDATE` on the fund row.
- **No way to release a commitment** (cancel or expire an approved request). This was out of scope, and it's the first business gap I'd raise (open question 6).
- **Pagination is a simple `limit`** with no cursor.
- **Production auth (OAuth resource server) is designed, not built** (spec §9). The service's `Caller` boundary is where it would plug in.
- **Audit events are written but no tool reads them.**
- Not done: load testing, input fuzzing, and a run with a real LLM client.

## Time spent

**[Candidate: fill in actual active time.]** The work was done in one AI-assisted session, in this order: spec → spike → implementation → tests → mutation checks → docs (see `git log`).

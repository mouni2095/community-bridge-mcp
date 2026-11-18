# AI-assisted workflow

**Tool:** Claude (Opus 5.5) as a coding agent with a shell, in one session. Input: the assessment brief. The agent wrote the spec, code, tests and docs. Each claim below is backed by evidence you can re-run from the repository.

## 1. Requirements analysis / specification

- **Context:** The brief says "prevent duplicate financial effects" and "staff must only access records belonging to their nonprofit", but doesn't say how retries should behave or what a foreign ID should return.
- **AI suggestion:** The spec made these decisions explicit instead of leaving them to the implementer:
  - Repeated approve/pay returns `INVALID_TRANSITION` with the current status, not a silent success (spec §2.4, open question 9).
  - `create_request` gets an optional idempotency key, because that's the one call where a retry would create a duplicate.
  - Foreign IDs return `NOT_FOUND`, identical to missing ones, so existence doesn't leak.
  - Amounts are JSON *strings*, because a JSON number may already have been rounded through binary floating point before the server sees it.
  - Fund totals are derived from request rows rather than stored counters, so they can't drift.
- **Independent check:** Every AC row in spec §6 maps to a named test (`docs/TRACEABILITY.md`). `test_not_found_is_identical_for_foreign_and_missing_ids` asserts identical messages. `test_schema_validation_over_mcp` sends JSON number `300` over the real protocol and asserts rejection.

## 2. Implementation planning / coding

- **Context:** The plan assumed the familiar `mcp.server.fastmcp.FastMCP` API. The latest SDK was 2.3.0.
- **What happened:** Before writing the error contract into the spec, the agent inspected the installed package. It found that `mcp.server.fastmcp` in 2.x is a stub that raises `ModuleNotFoundError` ("FastMCP was renamed to MCPServer"). It then ran a 2-minute throwaway spike (a server with one typed tool and one returning `CallToolResult(is_error=True)`, plus a stdio client) and confirmed three things:
  1. Typed returns publish `outputSchema` and `structuredContent`.
  2. A returned error result passes through unchanged.
  3. Schema violations come back as SDK-formatted `isError` text.
- **Resulting change:** The spec pins `mcp==2.3.0` (v1.1). The error envelope is a JSON body inside an `isError` result. The spec also documents that schema-level errors use SDK text while domain errors use the JSON envelope.
- **Independent check:** Spike output (outputSchema present, `is_error=True` with the JSON text intact). Now covered permanently by `test_tools_are_discoverable_with_schemas` and the e2e error assertions.

## 3. Debugging / verification

- **Context:** All 87 service tests passed on the first run. Passing on the first run is a reason to question the tests, not to trust them.
- **What happened:**
  - The agent wrote a control test. A deliberately non-atomic `NonAtomicService` does the check and the write in separate transactions. The control test asserts that this version **does** overspend under the same 10-way race (`test_control_non_atomic_approval_overspends`). That shows the race test can detect the bug.
  - The agent then ran seven source mutations (`docs/TRACEABILITY.md`). Six were caught. One survived (allowing payment from `Pending`). Running that mutant by hand showed it was still blocked by the conditional `UPDATE … WHERE status='Approved'`. So it's an equivalent mutant, not a test gap.
  - The `BEGIN` mutation showed something the spec hadn't said. SQLite prevents the overspend even without `IMMEDIATE`, but contending approvers crash with `database is locked` rather than getting a business error.
- **Resulting change:** Spec §3.4 and the revision history were updated with that finding (v1.1).
- **Corrections made along the way:** While writing `money.py`, the agent changed the amount regex from `\d` to `[0-9]`. Python's `\d` matches Arabic-Indic and other Unicode digits, which `Decimal` would accept. A test case (`"٣٠٠"`) now covers it. No other corrections occurred, and none are invented here.

## How AI output was reviewed

- Money, authorization and transaction code is meant to be read line by line against spec §2–§5.
- Tests are checked against the AC table, not against the code's own behaviour.
- Every "done" claim is backed by a command run in the session (`pytest -v` output in `docs/test-results.txt`, demo output in `docs/demo-output.txt`).

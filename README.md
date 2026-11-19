# Community Bridge: Emergency Assistance MCP Server

A headless MCP server (stdio) for Community Bridge's emergency assistance funding. Case workers submit requests, program managers approve or reject them, and finance records simulated payments. Every fund's committed, paid and available amounts stay correct throughout. Data is in SQLite, and two synthetic nonprofits are seeded.

Where things are:

- `docs/SPEC.md`: the spec. Requirements, states, data model, tools, permissions, acceptance criteria, plan, production auth design, open questions and revision history. v1.0 was committed before any code.
- `docs/TRACEABILITY.md`: requirement to code to test mapping, and what I didn't verify.
- `docs/AI_USAGE.md`: how AI was used, with three examples.
- `docs/tool-schemas.json`: generated input/output schemas for all 10 tools.
- `docs/test-results.txt`, `docs/demo-output.txt`: actual output from the last run.

## Setup

Python 3.12 or newer (tested on 3.12.3 and 3.13).

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.lock      # pinned versions, including mcp==2.3.0
pip install -e . --no-deps
python -m community_bridge.seed       # creates/resets ./data/community_bridge.db
```

The seed command deletes the database and recreates it, so you can run it whenever you want a clean slate. The server itself never creates data. If the database is missing it exits with code 2 and tells you to run the seed command. Use `CB_DB_PATH` (or `--db` on the seed command) to put the file somewhere else.

## Running

```bash
CB_STAFF_ID=cb-case-worker python -m community_bridge
```

`CB_STAFF_ID` picks which fixture user the server acts as. The server looks it up in the database on every call to get the role and nonprofit. Other settings: `CB_DB_PATH`, `CB_LOG_LEVEL` (default INFO) and `CB_LOG_FILE`. Logs go to stderr or the file, never stdout, because stdout carries the MCP protocol.

Fixture users:

| | Case worker | Program manager | Finance |
|---|---|---|---|
| Community Bridge | `cb-case-worker` | `cb-program-manager` | `cb-finance` |
| Harbor Hope Services | `hh-case-worker` | `hh-program-manager` | `hh-finance` |

`cb-former-staff` is a deactivated account, used in tests.

For a desktop MCP client or any other stdio client, `examples/mcp_client_config.json` has one server entry per role. Replace the placeholder paths with yours.

To see the whole flow without an LLM:

```bash
python -m community_bridge.seed
python scripts/demo_client.py
```

The demo starts the server once per role. As the case worker it lists the tools and creates a $300 request. As the program manager it approves it. As finance it pays it, tries to pay it again (rejected), and prints the fund: $300 paid, $0 committed, $9,700 available.

## Tests

```bash
pytest            # all 128 tests, about 20 seconds
pytest -m e2e     # just the end-to-end tests over stdio
```

Last run: 128 passed, none failed or skipped, on both Python versions. I also ran the concurrency tests five more times in a row; they passed every time.

## Design notes

The full reasoning is in the spec; these are the decisions a reviewer will probably ask about.

- **The rules live in `service.py`.** `server.py` only resolves the caller, calls the service, and turns errors into `isError` results with a JSON body.
- **Identity comes from config, not from tool input.** No tool takes a staff ID, role or nonprofit. They come from the database row for `CB_STAFF_ID`, looked up on every call.
- **Other nonprofits' records look like they don't exist.** Every query filters on the caller's `nonprofit_id`, and a foreign ID gets the same `NOT_FOUND` as a missing one. Composite foreign keys enforce this in the database as well.
- **Money is exact.** Amounts arrive as strings like `"300.00"` and are stored as integer cents. Committed, paid and available are calculated from the requests, not stored as counters.
- **Approvals can't overspend.** Every write runs in a SQLite `BEGIN IMMEDIATE` transaction, so checking the balance and approving happen as one step, even across server processes.
  - The test releases 10 approvers at once against a $500 fund with ten $100 requests. Exactly 5 succeed, across 5 multi-process runs and 20 threaded runs.
  - A deliberately broken version, which checks and writes separately, does overspend under the same test.
- **Retries are safe.** A repeated approve or pay returns `INVALID_TRANSITION` with the current status. Payments are also unique per request in the database. `create_request` takes an optional `idempotency_key`.

Assumptions are in spec sections 1.4 and 10. The main ones: one role and one nonprofit per person; no purpose limits on funds; over-budget requests can be submitted but not approved.

## Limitations and what I'd do next

- Money can't be released from an approved request that never gets paid. Cancellation was out of scope, but it's the first thing I'd raise with the business (open question 6).
- SQLite works for local use and a few processes on one machine. It isn't suitable for network drives or heavy write load. For a hosted version I'd move to Postgres and lock the fund row (`SELECT ... FOR UPDATE`) during approval.
- OAuth is designed (spec section 9) but not built. It would replace the `CB_STAFF_ID` lookup that produces the `Caller` object; the service wouldn't change.
- Lists use a simple `limit` with no cursor paging. Audit events are recorded but no tool exposes them yet.
- Not done: load testing, input fuzzing, and a session with a real LLM client.

## Time spent

| Activity | Time |
|---|---|
| Spec, implementation, tests and first docs (AI-assisted, from git timestamps) | ~15 min |
| Closing gaps and revising docs (AI-assisted) | ~10 min |
| Reading and understanding the code, checking it against the spec, running the demo and tests myself | ~2 h |
| **Total** | **~2 h 25 min** |

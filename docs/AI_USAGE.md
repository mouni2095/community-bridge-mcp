# How AI was used

I used Claude (Opus 5.5) as a coding agent with shell access for the whole exercise. I gave it the assessment brief. It drafted the spec, code, tests and these docs, and ran everything in a sandbox. Below are three examples. Each one names the evidence you can re-run yourself, so nothing rests on the agent's say-so.

## 1. Requirements and spec

The brief says to prevent "duplicate financial effects" and keep each nonprofit's records private. It doesn't say what a retry should return, or what a user should see when they ask for another nonprofit's record.

Claude's draft settled these in the spec rather than leaving them for whoever writes the code:

- A repeated approve or pay returns `INVALID_TRANSITION` with the current status, rather than quietly succeeding.
- `create_request` gets an optional idempotency key, since it's the only call where a retry creates a duplicate.
- Another nonprofit's IDs return `NOT_FOUND`, exactly like IDs that don't exist.
- Amounts are sent as strings, not JSON numbers, so they never pass through floating point.
- Fund totals are calculated from the requests instead of kept as counters.

How it was checked: every acceptance criterion in spec section 6 has a named test (see `TRACEABILITY.md`). For example:
- `test_not_found_is_identical_for_foreign_and_missing_ids` compares the two error messages.
- `test_schema_validation_over_mcp` sends the number `300` over the real protocol and expects a rejection.

## 2. Planning and coding

The plan assumed the usual `FastMCP` class from the Python MCP SDK. The current release turned out to be 2.3.0. There, `mcp.server.fastmcp` is only a stub that raises an error saying FastMCP was renamed to `MCPServer`.

Before writing the error format into the spec, Claude built a throwaway server with two tools and a stdio client to try it. It confirmed three things:
- Typed return values produce `outputSchema` and `structuredContent`.
- Returning a `CallToolResult(is_error=True)` reaches the client unchanged.
- Bad arguments are rejected by the SDK with its own error text.

Result: the spec pins `mcp==2.3.0` and uses a JSON error body inside an `isError` result. It also notes that schema errors use the SDK's text format (revision 1.1).

How it was checked: the spike output at the time. Since then, `test_tools_are_discoverable_with_schemas` and the end-to-end error assertions cover the same points permanently.

## 3. Debugging and verification

All 87 service tests passed on the first run. That's a reason to doubt the tests, not trust them, so Claude checked them two ways:

- **A control for the concurrency test.** A deliberately broken `NonAtomicService` checks the balance and writes in separate steps. Under the same 10-way race it does overspend (`test_control_non_atomic_approval_overspends`). So the race test really can detect the bug it's there for.
- **Mutation checks.** Claude broke seven protections in the source one at a time (list in `TRACEABILITY.md`):
  - Six were caught by the tests.
  - One (allowing payment from `Pending`) survived. Running it by hand showed the conditional `UPDATE ... WHERE status='Approved'` still blocked it, so it's a harmless duplicate guard rather than a missing test.
  - Switching to a plain `BEGIN` turned up something the spec hadn't said. SQLite still stops the overspend, but the competing approvers crash with `database is locked` instead of getting a proper error. The spec now explains this (revision 1.1).

## Corrections

These are the actual corrections; none are made up.

- **Unicode digits in amounts.** While writing the money parser, the regex changed from `\d` to `[0-9]`. Python's `\d` matches Arabic-Indic and other digits, which `Decimal` would happily accept. There's now a test for `"٣٠٠"`.
- **Request ID missing from logs.** On a second review against the spec, the logs turned out not to include the request ID, even though section 8 required it. That's now fixed. A new test reads the log file to confirm the ID is there and that descriptions and participant names aren't. The test fails on the old code and passes on the new.
- **Python 3.12 untested.** The README claimed 3.12 support, but everything had only run on 3.13. The suite has now been run on 3.12.3 as well.

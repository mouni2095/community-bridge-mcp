# Requirements to code to tests

Code paths are under `src/community_bridge/`; tests are under `tests/`. All requirements below pass in the last run: 128 passed, 0 failed, 0 skipped (`docs/test-results.txt`).

| Req | Where it's implemented | Tests |
|---|---|---|
| FR-1 identity | `service.authenticate`, called on every tool in `server.create_server.run` | `test_permissions::test_ac13_*`, `test_identity_resolves_*`, `test_mcp_e2e::test_ac13_identity_rejected_over_mcp` |
| FR-2 participants | `service.list_participants` | `test_workflow::test_participant_search`, `test_permissions::test_ac12_reads_are_scoped` |
| FR-3, FR-9 funds and summary | `service._fund_summaries`, `list_funds`, `funding_summary` | `test_workflow::test_ac1_3_*`, `test_funding_summary_totals`, `test_mcp_e2e::test_unknown_fund_summary_over_mcp` |
| FR-4 create | `service.create_request` | `test_workflow::test_ac1_3_*`, `test_invalid_create_fields`, `test_unknown_participant_or_fund` |
| FR-5 approve | `service.approve_request` | `test_workflow::test_ac1_3_*`, `test_ac5_*`, `test_ac6_*` |
| FR-6 reject | `service.reject_request` | `test_workflow::test_ac4_*`, `test_reject_requires_reason` |
| FR-7 pay | `service.record_payment` | `test_workflow::test_ac1_3_*`, `test_ac8_*`, `test_ac9_*` |
| FR-8 get and list | `service.get_request`, `list_requests` | `test_workflow::test_list_requests_filters_and_order` |
| FR-10 whoami | `service.whoami` | `test_permissions::test_all_roles_can_read`, end-to-end workflow |
| BR-1 states | status checks plus `UPDATE ... WHERE status=?`; `CHECK` constraint on status | `test_ac7` to `test_ac10`, `test_cannot_reject_approved_request`, `test_concurrent_approve_and_reject_same_request` |
| BR-2 no reservation on submit | create only inserts; totals count Approved and Paid only | `test_ac1_3_*`, `test_submission_may_exceed_available_but_approval_blocks` |
| BR-3 atomic approval | `db.write_transaction` (`BEGIN IMMEDIATE`) around the check and the update | `test_concurrency::test_ac17_*` (5 multi-process runs, 20 threaded), control test `test_control_non_atomic_approval_overspends` |
| BR-4 reject has no funding effect | `reject_request` | `test_ac4_*` |
| BR-5 pay once | status check plus `UNIQUE(payment.request_id)` | `test_ac8_*`, `test_storage_rejects_second_payment_row`, `test_concurrent_double_payment_of_same_request` |
| BR-6 no edits | no edit tool exists; updates only touch status, decision and payment columns | `test_tools_are_discoverable_with_schemas` checks the exact tool list |
| BR-7 money | `money.parse_amount` (Decimal to integer cents), plus a `pattern` in the schema | `test_money.*`, `test_ac14_*`, `test_ac15_*`, `test_schema_validation_over_mcp` |
| BR-8 formula | SQL in `_fund_summaries` | `test_ac1_3_*`, `test_paid_amounts_still_count_against_fund` |
| BR-9 failures change nothing | validation happens before writes; any exception rolls back the transaction | every failure test compares a full database snapshot before and after |
| BR-10 retries | state checks; `UNIQUE(nonprofit_id, created_by, idempotency_key)` plus a payload comparison | `test_ac7`, `test_ac8`, `test_ac16_idempotent_create`, concurrent double approve and double pay tests |
| SEC-1 roles | `service.PERMISSIONS`, `_require` (runs before validation) | `test_permissions::test_only_*`, `test_forbidden_checked_before_input_validation`, end-to-end role test |
| SEC-2 tenant isolation | every query filters on `caller.nonprofit_id`; composite foreign keys | `test_ac12_*`, `test_not_found_is_identical_*`, `test_storage_rejects_cross_tenant_reference`, end-to-end tests |
| SEC-3 no permissions from input | no identity parameters on any tool; `Caller` is only built from the database | schema check in the end-to-end tests, `test_ac18_prompt_injection_text_is_inert` |
| SEC-4 audit | `service._audit`, written in the same transaction | `test_audit_trail_records_actor`, `test_log_file_has_audit_lines_but_no_participant_data` |
| NFR-1 persistence | SQLite file (`db.py`) | end-to-end tests carry state across separate server processes |
| NFR-2 separate reset | `seed.py`; `server.main` exits with code 2 if the database is missing | `test_seed.*`, `test_server_refuses_to_start_without_database` |
| NFR-3 stdio, logs not on stdout | `config.setup_logging` writes to stderr or a file | end-to-end tests (stray stdout would break the protocol), the missing-database test checks stdout is empty, the log file test |
| NFR-5 schemas | pydantic return models; `docs/tool-schemas.json` | `test_tools_are_discoverable_with_schemas` |

## Mutation checks

Run by hand once; every change was reverted afterwards.

| Change made to the code | Result |
|---|---|
| Removed the `nonprofit_id` filter when loading a request | caught by the tenant tests |
| Let case workers approve | caught by the role tests |
| Removed the funds check | caught by `test_ac5` |
| Changed `BEGIN IMMEDIATE` to `BEGIN` | caught by the concurrency tests (`database is locked` crashes) |
| Left `paid` out of the available calculation | caught by `test_paid_amounts_still_count_against_fund` |
| Let inactive staff in | caught by `test_ac13` |
| Allowed payment from `Pending` in the status check | not caught, but harmless: the conditional `UPDATE ... WHERE status='Approved'` still blocks it. It's a second guard on purpose. |
| Reverted the request-ID logging fix (v1.2) | caught by the log file test |

## Not verified

- No load or performance testing, and no fuzzing of tool inputs.
- Concurrency was only tested on Linux with a local disk. SQLite locking isn't reliable on network file systems, so that setup isn't supported.
- The Claude Desktop config is included but I haven't run it with a real LLM client. The end-to-end tests and the demo use the MCP Python client directly.

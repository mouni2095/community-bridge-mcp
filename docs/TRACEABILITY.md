# Requirement → Implementation → Test mapping

Code paths are relative to `src/community_bridge/`; tests to `tests/`. Results are from `docs/test-results.txt` (125 passed, 0 failed, 0 skipped).

| Req | Implementation | Tests | Result |
|---|---|---|---|
| FR-1 identity | `service.authenticate`; called on every tool in `server.create_server.run` | `test_permissions::test_ac13_*`, `test_identity_resolves_*`; `test_mcp_e2e::test_ac13_identity_rejected_over_mcp` | ✅ |
| FR-2 participants | `service.list_participants` (LIKE wildcards escaped) | `test_workflow::test_participant_search`, `test_permissions::test_ac12_reads_are_scoped` | ✅ |
| FR-3 / FR-9 funds & summary | `service._fund_summaries`, `list_funds`, `funding_summary` | `test_workflow::test_ac1_3_*`, `test_funding_summary_totals`; e2e workflow | ✅ |
| FR-4 create | `service.create_request` | `test_workflow::test_ac1_3_*`, `test_invalid_create_fields`, `test_unknown_participant_or_fund` | ✅ |
| FR-5 approve | `service.approve_request` | `test_workflow::test_ac1_3_*`, `test_ac5_*`, `test_ac6_*` | ✅ |
| FR-6 reject | `service.reject_request` | `test_workflow::test_ac4_*`, `test_reject_requires_reason` | ✅ |
| FR-7 pay | `service.record_payment` | `test_workflow::test_ac1_3_*`, `test_ac8_*`, `test_ac9_*` | ✅ |
| FR-8 get/list | `service.get_request`, `list_requests` | `test_workflow::test_list_requests_filters_and_order` | ✅ |
| FR-10 whoami | `service.whoami` | `test_permissions::test_all_roles_can_read`; e2e workflow | ✅ |
| BR-1 state machine | status checks + `UPDATE … WHERE status=?` in approve/reject/pay; DB `CHECK` on status | `test_ac7`–`test_ac10`, `test_cannot_reject_approved_request` | ✅ |
| BR-2 no reservation on submit | create inserts only; totals derived from Approved/Paid | `test_ac1_3_*`, `test_submission_may_exceed_available_but_approval_blocks` | ✅ |
| BR-3 atomic approval | `db.write_transaction` (`BEGIN IMMEDIATE`) around check + update | `test_concurrency::test_ac17_*` (5× multi-process, 20× multi-thread), control `test_control_non_atomic_approval_overspends` | ✅ |
| BR-4 reject no effect | `reject_request` | `test_ac4_*` | ✅ |
| BR-5 pay once, committed→paid | status guard + `UNIQUE(payment.request_id)` | `test_ac8_*`, `test_storage_rejects_second_payment_row`, `test_concurrent_double_payment_of_same_request` | ✅ |
| BR-6 immutable | no edit tool; updates touch only status/decision/payment columns | `test_mcp_e2e::test_tools_are_discoverable_with_schemas` (tool set is exact) | ✅ |
| BR-7 money | `money.parse_amount` (Decimal → int cents), schema `pattern` | `test_money.*`, `test_ac14_*`, `test_ac15_*`, e2e `test_schema_validation_over_mcp` | ✅ |
| BR-8 formula | `_fund_summaries` SQL | `test_ac1_3_*`, `test_paid_amounts_still_count_against_fund` | ✅ |
| BR-9 failures change nothing | validation before writes; any exception in `write_transaction` rolls back | every failure test compares `snapshot(db)` before/after | ✅ |
| BR-10 retries / idempotency | transition guards; `UNIQUE(nonprofit_id, created_by, idempotency_key)` + payload compare | `test_ac7`, `test_ac8`, `test_ac16_idempotent_create`, concurrent double approve/pay | ✅ |
| SEC-1 roles | `service.PERMISSIONS`, `_require` (checked before validation) | `test_permissions::test_only_*`, `test_forbidden_checked_before_input_validation`, e2e role test | ✅ |
| SEC-2 tenant isolation | every query filters `caller.nonprofit_id`; composite FKs | `test_ac12_*`, `test_not_found_is_identical_*`, `test_storage_rejects_cross_tenant_reference`, e2e | ✅ |
| SEC-3 no permission from input | no identity params on any tool; `Caller` only built from DB | e2e schema test (no `staff_id`/`nonprofit_id` params), `test_ac18_prompt_injection_text_is_inert` | ✅ |
| SEC-4 audit | `service._audit` in the same transaction | `test_audit_trail_records_actor` | ✅ |
| NFR-1 persistence | `db.py` SQLite file | e2e (state carried across separate server processes) | ✅ |
| NFR-2 separate reset | `seed.py`; `server.main` exits 2 if DB missing | `test_seed.*`, `test_server_refuses_to_start_without_database` | ✅ |
| NFR-3 stdio, logs off stdout | `config.setup_logging` → stderr/file | e2e passes (protocol would break on stray stdout); missing-DB test asserts empty stdout | ✅ |
| NFR-5 schemas & structured output | pydantic return models; `docs/tool-schemas.json` | `test_tools_are_discoverable_with_schemas` | ✅ |

## Additional verification

**Mutation checks** (manual, run once; each change reverted afterwards):

| Mutation | Caught by |
|---|---|
| Remove `nonprofit_id` filter when loading a request | tenant tests (failed) |
| Grant `approve_request` to case workers | role tests (failed) |
| Remove the funds check | `test_ac5` (failed) |
| `BEGIN IMMEDIATE` → `BEGIN` | concurrency tests (failed: `database is locked` crashes) |
| Exclude `paid` from the available calculation | `test_paid_amounts_still_count_against_fund` (failed) |
| Allow inactive staff | `test_ac13` (failed) |
| Allow payment from `Pending` in the status check | **survived**: equivalent mutant. The conditional `UPDATE … WHERE status='Approved'` still blocks it and returns `INVALID_TRANSITION`. Kept as intended defence in depth. |

**Not run / limitations of verification:** no load or performance testing; no fuzzing of tool inputs; concurrency verified on Linux with a local file system only (SQLite locking on network file systems is unreliable, so that setup isn't supported); no test with a real LLM-driven client (Claude Desktop config is provided but wasn't exercised in CI).

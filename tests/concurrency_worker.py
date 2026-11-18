"""Worker entry point for multi-process approval races (importable by spawn)."""

from __future__ import annotations


def approve_worker(db_path: str, request_id: str, barrier, results) -> None:
    from community_bridge.errors import DomainError
    from community_bridge.service import AssistanceService

    svc = AssistanceService(db_path)
    caller = svc.authenticate("cb-program-manager")
    barrier.wait()  # release all processes at once
    try:
        svc.approve_request(caller, request_id)
        results.put((request_id, "ok"))
    except DomainError as err:
        results.put((request_id, str(err.code)))
    except Exception as err:  # surfaced to the test as a failure
        results.put((request_id, f"CRASH {type(err).__name__}: {err}"))

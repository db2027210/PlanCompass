#!/usr/bin/env python3
"""Restore the completed JOB terminal state after the legacy audit false positive."""

from __future__ import annotations

import copy
from datetime import datetime
import json
from pathlib import Path

from scripts import audit_mysql_algorithm_c_global_incumbent_chain_v3 as audit


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / (
    "history/mysql_four_scene_20260809/"
    "v1255_to_c_full_docker_path_resilience_v111_20260906_job"
)
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260907_v118/"
    "algorithm_c_pg_latest_outer_contract.json"
)
ERROR = (
    "RuntimeError: global incumbent chain audit failed: job_memory outer 20 "
    "reference 1.17712480869104 does not continue incumbent 1.13297573343432; "
    "job_memory outer 21 reference 1.17712480869104 does not continue incumbent "
    "1.13297573343432; job_memory final incumbent 1.2223562437494 differs from "
    "selected chain 1.18235069221754"
)


def now() -> str:
    return datetime.now().astimezone().isoformat()


def main() -> int:
    state_path = RUN / "run_state.json"
    old_state = audit.predecessor.load(state_path)
    decision = audit.predecessor.load(RUN / "outer_023/job_memory.decision.json")
    contract = audit.predecessor.load(CONTRACT)
    result = audit.audit_run(RUN)
    convergence = dict(decision.get("interval_convergence") or {})
    confirmation = dict(decision.get("post_target_stability_confirmation") or {})
    incumbent = dict((old_state.get("incumbents") or {}).get("job_memory") or {})
    if (
        contract.get("release_version") != "v118-semantic-state-incumbent-audit"
        or old_state.get("status") != "FAILED_CLOSED"
        or old_state.get("stage") != "FAILED"
        or old_state.get("error") != ERROR
        or old_state.get("async_scene") != "job_memory"
        or int(old_state.get("outer_attempt") or 0) != 23
        or int((old_state.get("accepted_rounds") or {}).get("job_memory") or 0) != 10
        or int((old_state.get("rejected_outer_attempts") or {}).get("job_memory") or 0) != 11
        or float(incumbent.get("db_qps") or 0) != 1.2223562437494015
        or decision.get("accepted") is not True
        or decision.get("formal_evidence_eligible") is not True
        or convergence.get("converged") is not True
        or float(convergence.get("normalized_width") or 1) > 0.01
        or confirmation.get("confirmation_ordinal") != 2
        or result.get("status") != "PASS"
        or result.get("failures") != []
    ):
        raise RuntimeError("JOB v118 terminal recovery boundary differs")

    legacy_path = RUN / "run_state_legacy_semantic_audit_failure.json"
    corrected_audit_path = RUN / "global_incumbent_chain_audit_v3.json"
    recovery_path = RUN / "semantic_incumbent_audit_recovery_v118.json"
    if any(path.exists() or path.is_symlink() for path in (
        legacy_path, corrected_audit_path, recovery_path,
    )):
        raise RuntimeError("fresh JOB v118 recovery artifacts required")
    audit.atomic(legacy_path, old_state)
    audit.atomic(corrected_audit_path, result)
    recovery = {
        "schema_version": "mysql_algorithm_c_job_semantic_audit_recovery_v118",
        "status": "PASS",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "measurement_replayed": False,
        "optimizer_state_mutated": False,
        "accepted_or_rejected_credit_mutated": False,
        "completed_outer_attempts_preserved": 23,
        "legacy_state": {"path": str(legacy_path), "sha256": audit.predecessor.alg.sha(legacy_path)},
        "corrected_audit": {"path": str(corrected_audit_path), "sha256": audit.predecessor.alg.sha(corrected_audit_path)},
        "contract": {"path": str(CONTRACT), "sha256": audit.predecessor.alg.sha(CONTRACT)},
        "recovered_terminal_scene": "job_memory",
    }
    audit.atomic(recovery_path, recovery)

    state = copy.deepcopy(old_state)
    state["status"] = "COMPLETE"
    state["stage"] = "FINISHED"
    state["error"] = None
    state["completed_at"] = now()
    state["converged"]["job_memory"] = True
    state["semantic_incumbent_audit_recovery"] = {
        "status": "PASS",
        "path": str(recovery_path),
        "sha256": audit.predecessor.alg.sha(recovery_path),
        "legacy_error": ERROR,
    }
    audit.atomic(state_path, state)
    print(json.dumps({
        "status": state["status"], "stage": state["stage"],
        "scene": "job_memory", "outer_attempt": 23,
        "accepted_rounds": 10, "rejected_outer_attempts": 11,
        "corrected_audit": str(corrected_audit_path),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

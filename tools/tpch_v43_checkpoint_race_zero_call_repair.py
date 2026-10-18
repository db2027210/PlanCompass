#!/usr/bin/env python3
"""Repair the sealed TPC-H v42 concurrent-checkpoint delivery failure.

This tool never invokes EXPLAIN or a workload.  It verifies the exact failed
cycle004 boundary, records five physical EXPLAIN results that were cached but
not delivered to estimator workers, and makes the checkpoint acceptable to the
thread-safe writer.  Formal state, accepted counter, and controller state are
read-only authorities throughout this transaction.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any, Dict, Mapping


B = Path(
    "/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/"
    "pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/"
    "experience_sgd/attempt48_global_replay_cap5_20260726"
)
ROOT = B / "tpch_final_algorithm_fresh0_v41_robust4_handoff_20260803"
LOOP = ROOT / "target10_loop_v41"
LOCK = LOOP / "controller.lock"
STATE = LOOP / "controller_state.json"
COUNTER = ROOT / "formal_search_accepted_counter.txt"
CYCLE = LOOP / "cycle_004"
FORMAL = ROOT / "adopted_v40_formal/formal_run/formal_state.json"
DYNAMIC = CYCLE / "dynamic_expansion_v32"
CHECKPOINT = DYNAMIC / "explain_checkpoint.json"
FAILURE_LOG = DYNAMIC / "expand_a.log"
AUDIT = DYNAMIC / "v43_checkpoint_race_repair_audit.json"
CONTRACT = "tpch_v43_checkpoint_race_zero_call_repair_v1"
EXPECTED_FAILED_COUNT = 5
EXPECTED_QUERY_COUNT = 22


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def validate(
    expected_state_sha: str,
    expected_formal_sha: str,
    expected_counter_sha: str,
    expected_checkpoint_sha: str,
    expected_log_sha: str,
) -> Dict[str, Any]:
    expected_files = (
        (STATE, expected_state_sha, "controller state"),
        (FORMAL, expected_formal_sha, "formal state"),
        (COUNTER, expected_counter_sha, "accepted counter"),
        (CHECKPOINT, expected_checkpoint_sha, "checkpoint"),
        (FAILURE_LOG, expected_log_sha, "failure log"),
    )
    for path, expected, label in expected_files:
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"{label} SHA mismatch: {path}")
    state = load(STATE)
    formal = load(FORMAL)
    checkpoint = load(CHECKPOINT)
    recovery = state.get("v42_recovery") or {}
    results = checkpoint.get("psql_results") or {}
    if not (
        state.get("status") == "preparing_cycle"
        and int(state.get("accepted_count", -1)) == 1
        and int(state.get("accepted_target", -1)) == 10
        and int(state.get("next_cycle", -1)) == 4
        and state.get("cycles") == []
        and Path(str(state.get("resume_state") or "")) == FORMAL
        and recovery.get("formal_state_sha256") == expected_formal_sha
        and recovery.get("accepted_counter_sha256") == expected_counter_sha
        and formal.get("status") == "complete"
        and formal.get("baseline_restored") is True
        and int(formal.get("accepted_count", -1)) == 1
        and int(formal.get("physical_call_count", -1)) == 5
        and int(formal.get("valid_replay_count", -1)) == 5
        and int(formal.get("technical_failure_count", -1)) == 0
        and COUNTER.read_text(encoding="utf-8").strip() == "1"
        and (CYCLE / "config.a.json").read_bytes()
        == (CYCLE / "config.b.json").read_bytes()
        and checkpoint.get("schema_version")
        == "tpch_explain_expansion_checkpoint_v1"
        and int(checkpoint.get("live_explain_invocations", -1))
        == EXPECTED_QUERY_COUNT
        and isinstance(results, dict)
        and len(results) == EXPECTED_QUERY_COUNT
        and checkpoint.get("lost_checkpoint_results") in {None, 0}
    ):
        raise RuntimeError("v42 failed checkpoint authority mismatch")
    log = FAILURE_LOG.read_text(encoding="utf-8")
    if not (
        f"failed={EXPECTED_FAILED_COUNT}/{EXPECTED_QUERY_COUNT}" in log
        and ".explain_checkpoint.json.tmp." in log
        and "No such file or directory" in log
        and "ratio-scale estimator requires complete EXPLAIN coverage" in log
    ):
        raise RuntimeError("failure log does not prove the concurrent temp-file race")
    forbidden = [
        str(path.relative_to(CYCLE))
        for path in CYCLE.rglob("*")
        if path.is_dir()
        and (
            path.name.startswith("attempt_")
            or path.name.startswith("diagnostic_")
            or path.name.startswith("formal_")
        )
    ]
    if forbidden:
        raise RuntimeError(f"cycle004 is no longer zero-workload: {forbidden}")
    return {
        "state": state,
        "checkpoint": checkpoint,
        "formal_sha256": expected_formal_sha,
        "counter_sha256": expected_counter_sha,
        "state_sha256": expected_state_sha,
        "checkpoint_sha256": expected_checkpoint_sha,
        "failure_log_sha256": expected_log_sha,
    }


def report(authority: Mapping[str, Any], status: str) -> Dict[str, Any]:
    return {
        "contract": CONTRACT,
        "status": status,
        "accepted_count_before": 1,
        "accepted_count_after": 1,
        "controller_state_sha256": authority["state_sha256"],
        "formal_state_sha256": authority["formal_sha256"],
        "accepted_counter_sha256": authority["counter_sha256"],
        "checkpoint_sha256_before": authority["checkpoint_sha256"],
        "failure_log_sha256": authority["failure_log_sha256"],
        "physical_explain_invocations_observed": EXPECTED_QUERY_COUNT,
        "cached_explain_result_count": EXPECTED_QUERY_COUNT,
        "lost_result_physical_explain_invocations": EXPECTED_FAILED_COUNT,
        "lost_result_outputs_present_in_cache": True,
        "new_explain_invocations": 0,
        "new_workload_calls": 0,
        "formal_state_mutated": False,
        "accepted_counter_mutated": False,
        "controller_state_mutated": False,
    }


def apply(authority: Mapping[str, Any]) -> Dict[str, Any]:
    if AUDIT.exists():
        raise RuntimeError("checkpoint repair audit already exists; refuse reapply")
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    backup = CHECKPOINT.with_name(
        f"explain_checkpoint.pre_v43_race_repair.{timestamp}.json"
    )
    if backup.exists():
        raise RuntimeError("checkpoint repair backup already exists")
    shutil.copy2(CHECKPOINT, backup)
    checkpoint = dict(authority["checkpoint"])
    checkpoint["lost_checkpoint_results"] = 0
    checkpoint["lost_result_physical_explain_invocations"] = (
        EXPECTED_FAILED_COUNT
    )
    checkpoint["lost_result_outputs_present_in_cache"] = True
    checkpoint["checkpoint_writer_contract"] = (
        "thread_safe_lock_singleflight_unique_temp_v1"
    )
    checkpoint["v43_zero_call_repair"] = {
        "contract": CONTRACT,
        "source_checkpoint_sha256": authority["checkpoint_sha256"],
        "source_failure_log_sha256": authority["failure_log_sha256"],
        "backup": str(backup),
        "backup_sha256": sha256(backup),
        "physical_explain_invocations_observed": EXPECTED_QUERY_COUNT,
        "cached_explain_result_count": EXPECTED_QUERY_COUNT,
        "estimator_delivery_failures": EXPECTED_FAILED_COUNT,
        "new_explain_invocations": 0,
        "new_workload_calls": 0,
    }
    atomic_json(CHECKPOINT, checkpoint)
    result = {
        **report(authority, "checkpoint_repair_applied_zero_call"),
        "checkpoint_backup": str(backup),
        "checkpoint_backup_sha256": sha256(backup),
        "checkpoint_sha256_after": sha256(CHECKPOINT),
    }
    atomic_json(AUDIT, result)
    result["audit_sha256"] = sha256(AUDIT)
    if not (
        sha256(STATE) == authority["state_sha256"]
        and sha256(FORMAL) == authority["formal_sha256"]
        and sha256(COUNTER) == authority["counter_sha256"]
    ):
        raise RuntimeError("checkpoint repair mutated formal/controller authority")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--apply-repair-only", action="store_true")
    parser.add_argument("--expected-controller-state-sha256", required=True)
    parser.add_argument("--expected-formal-state-sha256", required=True)
    parser.add_argument("--expected-counter-sha256", required=True)
    parser.add_argument("--expected-checkpoint-sha256", required=True)
    parser.add_argument("--expected-failure-log-sha256", required=True)
    args = parser.parse_args()
    with LOCK.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        authority = validate(
            args.expected_controller_state_sha256,
            args.expected_formal_state_sha256,
            args.expected_counter_sha256,
            args.expected_checkpoint_sha256,
            args.expected_failure_log_sha256,
        )
        if args.preflight_only:
            print(
                json.dumps(
                    report(authority, "audit_passed_ready_to_repair"),
                    sort_keys=True,
                )
            )
            return 0
        print(json.dumps(apply(authority), sort_keys=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

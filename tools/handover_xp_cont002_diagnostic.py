#!/usr/bin/env python3
"""Finalize XP cont002 as state-neutral diagnostic evidence after its children exit."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys


REQUIRED_CPUSET = frozenset(
    list(range(32, 52)) + list(range(288, 308))
)


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.handover.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("batch", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("bundle_root", type=Path)
    parser.add_argument("restore_script", type=Path)
    parser.add_argument("--expected-old-controller-pid", type=int, required=True)
    args = parser.parse_args()

    try:
        os.kill(args.expected_old_controller_pid, 0)
    except ProcessLookupError:
        pass
    else:
        raise RuntimeError("old XP controller is still alive")
    if not args.restore_script.is_file():
        raise FileNotFoundError(args.restore_script)
    bundle_root = args.bundle_root.resolve()
    if not (bundle_root / "xp_round9_serial_replay.py").is_file():
        raise FileNotFoundError(bundle_root)
    sys.path.insert(0, str(bundle_root))
    import xp_round9_serial_replay as serial

    batch = args.batch.resolve()
    selection_path = batch / "selection.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    candidates = list(selection.get("top5") or [])
    if len(candidates) != 5:
        raise RuntimeError("cont002 selection is not sealed Top-5")
    selected = {
        str((item.get("three_level_fingerprint") or {}).get("id")): item
        for item in candidates
    }
    if len(selected) != 5 or "" in selected:
        raise RuntimeError("cont002 selection fingerprints are invalid")

    ledger = []
    for rank in range(1, 6):
        point = batch / "serial_replay" / f"rank_{rank:02d}"
        result_path = point / "result.json"
        if result_path.is_file():
            result = json.loads(result_path.read_text(encoding="utf-8"))
        else:
            candidate = json.loads(
                (point / "candidate.json").read_text(encoding="utf-8")
            )
            evidence = json.loads(
                (point / "evidence.json").read_text(encoding="utf-8")
            )
            result = serial.validate_evidence(
                evidence,
                candidate,
                origin="serial_runner",
                execution="ran_once",
            )
            result["selection_role"] = candidate.get("selection_role")
            atomic_json(result_path, result)
        fingerprint = str(result.get("fingerprint_id") or "")
        if (
            int(result.get("rank", -1)) != rank
            or fingerprint not in selected
            or result.get("physical_call") is not True
            or result.get("execution") != "ran_once"
        ):
            raise RuntimeError(f"cont002 result contract mismatch: rank={rank}")
        result["accepted"] = False
        result["state_commit_eligible"] = False
        result["updates_incumbent_range_or_accepted"] = False
        result["gradient_update_applied"] = False
        result["incumbent_update_applied"] = False
        result["range_update_applied"] = False
        result["accepted_update_applied"] = False
        result["diagnostic_only"] = True
        result["state_patch"] = {}
        ledger.append(result)

    previous_affinity = os.sched_getaffinity(0)
    try:
        os.sched_setaffinity(0, REQUIRED_CPUSET)
        environment = os.environ.copy()
        environment["XP_RESTORE_PHASE"] = "cont002_diagnostic_handover_finally"
        completed = subprocess.run(
            [str(args.restore_script.resolve())],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=900,
            env=environment,
        )
    finally:
        os.sched_setaffinity(0, previous_affinity)
    restore_log = batch / "handover_final_restore.log"
    restore_log.write_text(completed.stdout, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(
            f"XP baseline handover restore failed: rc={completed.returncode}"
        )

    successful = [
        item for item in ledger if item.get("technical_failure") is not True
    ]
    technical = [
        item for item in ledger if item.get("technical_failure") is True
    ]
    rejected = [
        item
        for item in successful
        if item.get("rejected") is True
        or item.get("algorithmic_gate_rejection") is True
    ]
    summary = {
        "contract": "xp_unattended_continuation_v1",
        "selection_contract": selection.get("selection_contract"),
        "batch_mode": "diagnostic_validation",
        "successful_real_replay_count": len(successful),
        "technical_failure_count": len(technical),
        "maximum_successful_real_replays": 5,
        "maximum_physical_workload_invocations": 5,
        "physical_workload_invocation_count": 5,
        "rank1_imported_without_rerun": False,
        "diagnostic_only": True,
        "accepted_update_applied": False,
        "gradient_update_applied": False,
        "serial_ranks_executed": [1, 2, 3, 4, 5],
        "successful": successful,
        "technical_failures": technical,
        "rejected": rejected,
        "rejected_candidates_update_incumbent_range_or_accepted": False,
        "technical_failures_count_as_replay": False,
        "qps_used_for_inner_scoring": False,
        "qps_role": "outer_acceptance_and_audit_only",
        "qps_audit": [item.get("qps_audit", {}) for item in successful],
        "incumbent_updated": False,
        "range_updated": False,
        "accepted_count_delta": 0,
        "state_owner": "xp_cont002_diagnostic_handover",
        "state_mutations_applied_by_this_bundle": False,
        "ledger": ledger,
        "cpuset_cpus": "32-51,288-307",
        "baseline_restore_verified": True,
        "handover_restore_log": str(restore_log),
    }
    atomic_json(batch / "serial_replay" / "replay_summary.json", summary)
    atomic_json(batch / "replay_summary.json", summary)

    state_path = args.state.resolve()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if (
        int(state.get("accepted_count", -1)) != 4
        or int(state.get("next_batch_index", -1)) != 2
        or [item.get("batch_id") for item in state.get("batch_history") or []]
        != ["cont001"]
    ):
        raise RuntimeError("XP authoritative state is not the expected pre-cont002 state")
    backup = state_path.with_name(
        f"{state_path.name}.pre_cont002_handover_"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )
    descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(state_path.read_bytes())
        handle.flush()
        os.fsync(handle.fileno())
    old_error = state.pop("last_error", None)
    if old_error:
        state.setdefault("quarantined_controller_errors", []).append(
            {
                "error": old_error,
                "reason": "duplicate_controller_explain_only_race",
                "workload_calls": 0,
            }
        )
    fingerprints = sorted(
        str(item["fingerprint_id"]) for item in ledger
    )
    state["replayed_fingerprints"] = sorted(
        set(state.get("replayed_fingerprints") or []) | set(fingerprints)
    )
    state["next_batch_index"] = 3
    state["status"] = "running"
    state["last_batch"] = {
        "batch_id": "cont002",
        "mode": "diagnostic_validation",
        "status": "complete",
        "physical_calls": 5,
        "accepted": 0,
        "technical_failures": len(technical),
        "rejected": len(rejected),
        "diagnostic_state_neutral": True,
    }
    state["batch_history"].append(copy.deepcopy(state["last_batch"]))
    state["handover"] = {
        "schema_version": "xp_cont002_diagnostic_handover_v1",
        "baseline_restored": True,
        "accepted_count_before": 4,
        "accepted_count_after": 4,
        "accepted_delta": 0,
        "backup": str(backup),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_json(state_path, state)
    print(
        json.dumps(
            {
                "status": "complete",
                "accepted_count": state["accepted_count"],
                "physical_calls": 5,
                "valid_replays": len(successful),
                "technical_failures": len(technical),
                "next_batch_index": 3,
                "backup": str(backup),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

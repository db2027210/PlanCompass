#!/usr/bin/env python3
"""Reclassify partial XP cont020 after enforcing the score-span hard gate."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any, Dict, Mapping


FORMAL_FIELDS = (
    "accepted_count",
    "accepted_fingerprints",
    "incumbent",
    "incumbent_params",
    "incumbent_score",
    "parameter_ranges",
    "range_state",
    "training_state_sha256",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def formal_projection(state: Mapping[str, Any]) -> Dict[str, Any]:
    return {name: copy.deepcopy(state.get(name)) for name in FORMAL_FIELDS}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--expected-state-sha256", required=True)
    parser.add_argument("--expected-selection-sha256", required=True)
    parser.add_argument("--expected-result-sha256", required=True)
    parser.add_argument("--baseline-restore-log", type=Path, required=True)
    parser.add_argument("--expected-restore-log-sha256", required=True)
    args = parser.parse_args()

    campaign = args.campaign.resolve()
    state_path = campaign / "state.json"
    batch = campaign / "batches" / "cont020"
    selection_path = batch / "selection.json"
    # v20's serial adapter seals a completed invocation as evidence.json;
    # result.json is not part of that schema.
    result_path = batch / "serial_replay" / "rank_01" / "evidence.json"
    summary_path = batch / "replay_summary.json"
    audit_path = batch / "invalid_formal_entry_reclassification.json"
    for path, expected in (
        (state_path, args.expected_state_sha256),
        (selection_path, args.expected_selection_sha256),
        (result_path, args.expected_result_sha256),
        (args.baseline_restore_log, args.expected_restore_log_sha256),
    ):
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"sealed XP recovery input changed: {path}")
    if summary_path.exists() or audit_path.exists():
        raise RuntimeError("cont020 was already summarized or reclassified")
    extra_results = list((batch / "serial_replay").glob("rank_*/evidence.json"))
    if extra_results != [result_path]:
        raise RuntimeError(
            f"expected exactly the completed rank1 result, found {extra_results}"
        )

    state = load(state_path)
    selection = load(selection_path)
    result = load(result_path)
    gate = (selection.get("formal_admission") or {}).get(
        "diagnostic_entry_recheck"
    ) or {}
    checks = gate.get("checks") or {}
    history = state.get("batch_history") or []
    if not (
        int(state.get("accepted_count", -1)) == 2
        and int(state.get("accepted_target", -1)) == 10
        and int(state.get("next_batch_index", -1)) == 20
        and state.get("status") == "running"
        and (state.get("last_batch") or {}).get("batch_id") == "cont019"
        and selection.get("batch_mode") == "formal"
        and gate.get("passed") is True
        and checks.get("score_span_exceeds_replay_noise_bound") is False
        and float(gate.get("predicted_score_span"))
        <= float(gate.get("replay_noise_bound_2mae"))
        and result.get("technical_failure") is False
        and result.get("physical_call") is True
        and result.get("execution") == "ran_once"
        and result.get("workload_returncode") == 0
        and result.get("state_commit_eligible") is False
        and result.get("cpuset_cpus") == "32-51,288-307"
        and result.get("mysql_reserved_cpus") == "184-199,440-455"
        and (result.get("measurement_protocol") or {}).get("stable") is True
        and (result.get("measurement_protocol") or {}).get(
            "correctness_passed"
        ) is True
        and result.get("baseline_restore_verified") is True
    ):
        raise RuntimeError("XP cont020 does not match invalid score-span entry")
    fingerprint_id = str(
        (result.get("three_level_fingerprint") or {}).get("id") or ""
    )
    selected_by_id = {
        str((item.get("three_level_fingerprint") or {}).get("id") or ""): item
        for item in selection.get("top5") or []
    }
    selected = selected_by_id.get(fingerprint_id)
    if (
        not fingerprint_id
        or not isinstance(selected, dict)
        or int(result.get("rank", -1)) != 1
        or int(selected.get("rank", -1)) != 1
        or selected.get("candidate_params") != result.get("candidate_params")
        or selected.get("workload_plan_fingerprint")
        != result.get("workload_plan_fingerprint")
    ):
        raise RuntimeError("completed result is not bound to cont020 Top-5")

    before = formal_projection(state)
    replay_summary = {
        "contract": "xp_invalid_formal_entry_reclassified_diagnostic_v1",
        "batch_mode": "diagnostic_validation",
        "diagnostic_only": True,
        "physical_workload_invocation_count": 1,
        "successful_real_replay_count": 1,
        "technical_failure_count": 0,
        "successful": [copy.deepcopy(result)],
        "technical_failures": [],
        "baseline_restore_verified": True,
        "accepted_count_delta": 0,
        "accepted_update_applied": False,
        "incumbent_updated": False,
        "range_updated": False,
        "gradient_update_applied": False,
        "state_mutations_applied_by_this_bundle": False,
        "qps_used_for_inner_scoring": False,
        "reclassification_reason": (
            "predicted_score_span_not_above_empirical_replay_noise"
        ),
        "original_selection_sha256": sha256(selection_path),
        "hard_gate_contract": "xp_fresh0_formal_entry_recheck_score_span_hard_v3",
    }
    atomic_json(summary_path, replay_summary)

    recovered = copy.deepcopy(state)
    replayed = set(str(item) for item in recovered["replayed_fingerprints"])
    if fingerprint_id in replayed:
        raise RuntimeError("partial cont020 fingerprint was already accounted")
    replayed.add(fingerprint_id)
    recovered["replayed_fingerprints"] = sorted(replayed)
    batch_record = {
        "batch_id": "cont020",
        "mode": "diagnostic_validation",
        "status": "reclassified_invalid_formal_entry_gate",
        "physical_calls": 1,
        "accepted": 0,
        "technical_failures": 0,
        "rejected": 0,
        "fingerprint_digest": hashlib.sha256(
            json.dumps([fingerprint_id], separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "invalid_formal_entry_reclassified": True,
    }
    recovered["batch_history"] = [*history, batch_record]
    recovered["last_batch"] = batch_record
    recovered["next_batch_index"] = 21
    recovered["last_error"] = None
    recovered["status"] = "running"
    if formal_projection(recovered) != before:
        raise RuntimeError("reclassification would mutate formal state")

    timestamp = time.strftime("%Y%m%dT%H%M%S%z")
    backup_dir = campaign / f"quarantine_cont020_invalid_score_span_{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(state_path, backup_dir / "state_before_reclassification.json")
    shutil.copy2(selection_path, backup_dir / "selection.json")
    shutil.copy2(result_path, backup_dir / "rank1_evidence.json")
    atomic_json(state_path, recovered)
    audit = {
        "contract": "xp_cont020_score_span_hardgate_reclassification_v1",
        "status": "applied_and_verified",
        "accepted_count": 2,
        "accepted_target": 10,
        "batch_added_accepted": 0,
        "physical_workload_calls": 1,
        "valid_replays": 1,
        "technical_failures": 0,
        "baseline_restored": True,
        "state_sha256_before": args.expected_state_sha256,
        "state_sha256_after": sha256(state_path),
        "selection_sha256": args.expected_selection_sha256,
        "rank1_evidence_sha256": args.expected_result_sha256,
        "replay_summary_sha256": sha256(summary_path),
        "baseline_restore_log_sha256": args.expected_restore_log_sha256,
        "score_span": float(gate["predicted_score_span"]),
        "noise_bound": float(gate["replay_noise_bound_2mae"]),
        "formal_state_mutated": False,
        "next_batch_index": 21,
        "quarantine_audit_root": str(backup_dir),
    }
    atomic_json(audit_path, audit)
    atomic_json(backup_dir / "recovery_audit.json", audit)
    observed = load(state_path)
    if observed != recovered or formal_projection(observed) != before:
        raise RuntimeError("XP reclassification verification failed")
    print(json.dumps(audit, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

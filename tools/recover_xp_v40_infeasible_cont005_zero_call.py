#!/usr/bin/env python3
"""Skip XP cont005's sealed zero-call partial attempt and prepare cont006."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any


PROJECT = Path("/home/anonymous/program/resource_adaptive")
DEFAULT_ROOT = PROJECT / "history/xp_final_algorithm_fresh10_v38_20260803"
EXPECTED_STATE_SHA256 = (
    "669f60e63c4a3a775c33f76208f57ab557cdf2664bf1e26b27405eb2240dc3aa"
)
EXPECTED_ARTIFACTS = {
    "batches/cont004/selection.json": (
        "c54d45696f26549c870a13719270f8aa430ad54c95d1646bb4ce1bb7b5d8bc07"
    ),
    "batches/cont004/replay_summary.json": (
        "b6644b3f064776f24f0ceed66bc1bff0fc52ee942860ed6ba4413cdd88c1d259"
    ),
    "batches/cont005/score_metadata_normalization/selection.json": (
        "c54d45696f26549c870a13719270f8aa430ad54c95d1646bb4ce1bb7b5d8bc07"
    ),
    "batches/cont005/score_metadata_normalization/audit.json": (
        "ed11b566020c15a8abf21d5b97c8ae1e12d80133565141e0632acb8b64a8423f"
    ),
    "batches/cont005/formal_acceptance_infeasible_reroute.json": (
        "5bac3060eb1ec135606caa55086907dcdae267f533ea0ba1cb2bae6c9a5956eb"
    ),
}
PROTECTED_FIELDS = (
    "accepted_count",
    "accepted_fingerprints",
    "incumbent",
    "incumbent_score",
    "incumbent_params",
    "parameter_ranges",
    "range_state",
    "gradient_state",
    "batch_history",
    "last_batch",
    "replayed_fingerprints",
)
FORMAL_SNAPSHOT_SHA256 = (
    "bd8034585cbfe0740a1960889edf2120bee0eb53734d939c2ebc1dccac6439d1"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def validate_source(root: Path) -> tuple[dict[str, Any], dict[str, str]]:
    state_path = root / "state.json"
    if not state_path.is_file() or sha256(state_path) != EXPECTED_STATE_SHA256:
        raise RuntimeError("XP v40 recovery source state SHA mismatch")
    artifact_hashes = {}
    for relative, expected in EXPECTED_ARTIFACTS.items():
        path = root / relative
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"XP v40 sealed artifact mismatch: {relative}")
        artifact_hashes[relative] = expected
    if (root / "batches/cont005/selection.json").exists():
        raise RuntimeError("XP v40 cont005 unexpectedly has a replayable selection")
    if (root / "batches/cont005/replay_summary.json").exists():
        raise RuntimeError("XP v40 cont005 unexpectedly has replay results")
    if list((root / "batches/cont005").rglob("workload.log")):
        raise RuntimeError("XP v40 cont005 unexpectedly contains workload evidence")

    state = json.loads(state_path.read_text(encoding="utf-8"))
    phase = state.get("active_phase") or {}
    if (
        state.get("status") != "blocked_preworkload"
        or state.get("last_error")
        != "formal_acceptance_infeasible_no_fresh_lcb_above_incumbent"
        or int(state.get("accepted_count", -1)) != 0
        or int(state.get("accepted_target", -1)) != 10
        or int(state.get("next_batch_index", -1)) != 5
        or int((state.get("gradient_state") or {}).get("update_count", -1)) != 0
        or (state.get("last_batch") or {}).get("batch_id") != "cont004"
        or int((state.get("last_batch") or {}).get("physical_calls", -1)) != 5
        or int((state.get("last_batch") or {}).get("technical_failures", -1)) != 0
        or phase.get("batch_id") != "cont005"
        or phase.get("stage") != "offline_reexploration"
        or int(phase.get("physical_workload_calls", -1)) != 0
        or phase.get("manual_candidate_injection") is not False
        or phase.get("formal_state_sha256_before") != FORMAL_SNAPSHOT_SHA256
    ):
        raise RuntimeError("XP v40 recovery observed state mismatch")
    reroute = json.loads(
        (root / "batches/cont005/formal_acceptance_infeasible_reroute.json").read_text(
            encoding="utf-8"
        )
    )
    feasibility = reroute.get("acceptance_feasibility_gate") or {}
    if (
        reroute.get("reason")
        != "no_trusted_candidate_can_beat_incumbent_after_one_additional_observation"
        or feasibility.get("passed") is not False
        or feasibility.get("feasible_trusted_ranks") != []
        or reroute.get("state_mutation") is not False
        or int(reroute.get("new_workload_calls", -1)) != 0
        or (reroute.get("entry_gate") or {}).get("passed") is not True
    ):
        raise RuntimeError("XP v40 infeasible reroute evidence mismatch")
    return state, artifact_hashes


def recover(root: Path) -> dict[str, Any]:
    state_path = root / "state.json"
    state, artifact_hashes = validate_source(root)
    protected = {name: copy.deepcopy(state.get(name)) for name in PROTECTED_FIELDS}
    protected_sha = digest(protected)
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    backup = root / f"state.before_v40_cont005_skip.{timestamp}.json"
    audit_path = root / f"v40_cont005_zero_call_recovery.{timestamp}.json"
    if backup.exists() or audit_path.exists():
        raise RuntimeError("XP v40 recovery timestamp collision")
    shutil.copy2(state_path, backup)
    if sha256(backup) != EXPECTED_STATE_SHA256:
        raise RuntimeError("XP v40 state backup SHA mismatch")

    state["next_batch_index"] = 6
    state["status"] = "offline_reexploration"
    state.pop("last_error", None)
    state["active_phase"] = {
        "contract": "xp_v38_atomic_offline_reexploration_phase_state_v1",
        "stage": "offline_reexploration",
        "batch_id": "cont006",
        "reason": "formal_infeasible_routes_to_deterministic_novel_explain_reexploration",
        "formal_state_sha256_before": FORMAL_SNAPSHOT_SHA256,
        "physical_workload_calls": 0,
        "manual_candidate_injection": False,
        "cont005_zero_call_partial_preserved": True,
    }
    recovery_audit = {
        "contract": "xp_v40_cont005_infeasible_zero_call_schedule_recovery_v1",
        "source_state_sha256": EXPECTED_STATE_SHA256,
        "source_artifacts_sha256": artifact_hashes,
        "source_batch_id": "cont005",
        "next_batch_id": "cont006",
        "reason": "formal_acceptance_mathematically_infeasible",
        "action": "existing_deterministic_novel_explain_reexploration",
        "backup": str(backup.resolve()),
        "backup_sha256": sha256(backup),
        "protected_state_sha256": protected_sha,
        "workload_calls_added": 0,
        "accepted_incumbent_range_gradient_mutations": 0,
        "replay_or_global_counter_decrements": 0,
        "manual_candidate_injection": False,
        "cont005_artifacts_overwritten": False,
    }
    state["xp_v40_zero_call_infeasible_reexploration_recovery"] = copy.deepcopy(
        recovery_audit
    )
    if digest({name: state.get(name) for name in PROTECTED_FIELDS}) != protected_sha:
        raise RuntimeError("XP v40 recovery would mutate protected state")
    atomic_json(state_path, state)

    recovery_audit["recovered_state_sha256"] = sha256(state_path)
    atomic_json(audit_path, recovery_audit)
    if any(sha256(root / relative) != expected for relative, expected in artifact_hashes.items()):
        raise RuntimeError("XP v40 recovery overwrote sealed evidence")
    return {
        "status": "recovered_zero_call",
        "campaign": str(root.resolve()),
        "state": str(state_path.resolve()),
        "state_sha256": sha256(state_path),
        "backup": str(backup.resolve()),
        "backup_sha256": sha256(backup),
        "audit": str(audit_path.resolve()),
        "audit_sha256": sha256(audit_path),
        "accepted_count": 0,
        "accepted_target": 10,
        "next_batch_index": 6,
        "workload_calls_added": 0,
        "manual_candidate_injection": False,
        "protected_state_sha256": protected_sha,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    result = recover(args.campaign_root.resolve())
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

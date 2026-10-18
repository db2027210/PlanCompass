#!/usr/bin/env python3
"""Atomically prepare XP cont006 to resume its sealed EXPLAIN pool."""

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

try:
    import xp_v41_excluded_ids_sealed_pool_resume_patch as v41
except ModuleNotFoundError:
    from tools import xp_v41_excluded_ids_sealed_pool_resume_patch as v41


PROJECT = Path("/home/anonymous/program/resource_adaptive")
DEFAULT_ROOT = PROJECT / "history/xp_final_algorithm_fresh10_v38_20260803"
EXPECTED_STATE_SHA256 = (
    "163200e9dcf0a95c27cf2e73aed066229980c85d574c7bc9b2367c1bdf075e75"
)
EXPECTED_ARTIFACTS = {
    "batches/cont006/explain_only/explain_only_generation.json": v41.EXPECTED_POOL_SHA256,
    "batches/cont006/score_metadata_normalization/selection.json": (
        "c54d45696f26549c870a13719270f8aa430ad54c95d1646bb4ce1bb7b5d8bc07"
    ),
    "batches/cont006/score_metadata_normalization/audit.json": (
        "d2ec7abd72e8531bec9abd44e1f6ec736e1abd85ccd9bed96fe4a47d1a183b76"
    ),
    "batches/cont006/formal_acceptance_infeasible_reroute.json": (
        "5bac3060eb1ec135606caa55086907dcdae267f533ea0ba1cb2bae6c9a5956eb"
    ),
    "batches/cont006/accepted_incumbent.json": (
        "bd1d45a446dc2db337c584d29372a1ac87244bd89132519f1c6a661036fd4ca3"
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
    "next_batch_index",
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
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def validate_source(root: Path) -> tuple[dict[str, Any], dict[str, str]]:
    state_path = root / "state.json"
    if not state_path.is_file() or sha256(state_path) != EXPECTED_STATE_SHA256:
        raise RuntimeError("XP v41 recovery source state SHA mismatch")
    hashes = {}
    for relative, expected in EXPECTED_ARTIFACTS.items():
        path = root / relative
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"XP v41 sealed artifact mismatch: {relative}")
        hashes[relative] = expected
    cont006 = root / "batches/cont006"
    if (cont006 / "diagnostic_bundle").exists():
        raise RuntimeError("XP v41 cont006 unexpectedly has a diagnostic bundle")
    for relative in ("selection.json", "replay_summary.json", "serial_replay"):
        if (cont006 / relative).exists():
            raise RuntimeError(f"XP v41 cont006 unexpectedly has {relative}")
    if list(cont006.rglob("workload.log")):
        raise RuntimeError("XP v41 cont006 unexpectedly contains workload evidence")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    phase = state.get("active_phase") or {}
    if (
        state.get("status") != "blocked_preworkload"
        or state.get("last_error") != "AttributeError: 'str' object has no attribute 'get'"
        or int(state.get("accepted_count", -1)) != 0
        or int(state.get("accepted_target", -1)) != 10
        or int(state.get("next_batch_index", -1)) != 6
        or int((state.get("gradient_state") or {}).get("update_count", -1)) != 0
        or (state.get("last_batch") or {}).get("batch_id") != "cont004"
        or phase.get("batch_id") != "cont006"
        or phase.get("stage") != "offline_reexploration"
        or int(phase.get("physical_workload_calls", -1)) != 0
        or phase.get("manual_candidate_injection") is not False
        or phase.get("formal_state_sha256_before") != FORMAL_SNAPSHOT_SHA256
    ):
        raise RuntimeError("XP v41 recovery observed state mismatch")
    return state, hashes


def recover(root: Path) -> dict[str, Any]:
    state_path = root / "state.json"
    state, hashes = validate_source(root)
    protected = {key: copy.deepcopy(state.get(key)) for key in PROTECTED_FIELDS}
    protected_sha = digest(protected)
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    backup = root / f"state.before_v41_cont006_pool_resume.{timestamp}.json"
    audit_path = root / f"v41_cont006_pool_resume_recovery.{timestamp}.json"
    if backup.exists() or audit_path.exists():
        raise RuntimeError("XP v41 recovery timestamp collision")
    shutil.copy2(state_path, backup)
    if sha256(backup) != EXPECTED_STATE_SHA256:
        raise RuntimeError("XP v41 backup SHA mismatch")
    state["status"] = "offline_reexploration"
    state.pop("last_error", None)
    state["active_phase"] = {
        "contract": "xp_v38_atomic_offline_reexploration_phase_state_v1",
        "stage": "offline_reexploration",
        "batch_id": "cont006",
        "reason": "resume_sealed_explain_pool_after_type_safe_excluded_id_fix",
        "formal_state_sha256_before": FORMAL_SNAPSHOT_SHA256,
        "physical_workload_calls": 0,
        "manual_candidate_injection": False,
        "sealed_pool_reuse_required": True,
    }
    audit = {
        "contract": "xp_v41_cont006_sealed_pool_zero_call_recovery_v1",
        "source_state_sha256": EXPECTED_STATE_SHA256,
        "source_artifacts_sha256": hashes,
        "backup": str(backup.resolve()),
        "backup_sha256": sha256(backup),
        "protected_state_sha256": protected_sha,
        "next_batch_id": "cont006",
        "workload_calls_added": 0,
        "explain_calls_repeated": 0,
        "accepted_incumbent_range_gradient_mutations": 0,
        "manual_candidate_injection": False,
    }
    state["xp_v41_cont006_sealed_pool_recovery"] = copy.deepcopy(audit)
    if digest({key: state.get(key) for key in PROTECTED_FIELDS}) != protected_sha:
        raise RuntimeError("XP v41 recovery would mutate protected state")
    atomic_json(state_path, state)
    audit["recovered_state_sha256"] = sha256(state_path)
    atomic_json(audit_path, audit)
    if any(sha256(root / relative) != expected for relative, expected in hashes.items()):
        raise RuntimeError("XP v41 recovery overwrote sealed evidence")
    return {
        "status": "recovered_zero_call",
        "state_sha256": sha256(state_path),
        "backup": str(backup.resolve()),
        "audit": str(audit_path.resolve()),
        "accepted_count": 0,
        "accepted_target": 10,
        "next_batch_index": 6,
        "workload_calls_added": 0,
        "explain_calls_repeated": 0,
        "manual_candidate_injection": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    print(json.dumps(recover(args.campaign_root.resolve()), indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

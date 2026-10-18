#!/usr/bin/env python3
"""Fail-closed zero-call recovery for XP v42 cont006."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any, Mapping


PROJECT = Path("/home/anonymous/program/resource_adaptive")
DEFAULT_ROOT = PROJECT / "history/xp_final_algorithm_fresh10_v38_20260803"
EXPECTED_STATE_SHA256 = (
    "dee1c2a508980bbd105c006ef3d4943b5c577290a8f2ad01218446e39f2313db"
)
FORMAL_SNAPSHOT_SHA256 = (
    "bd8034585cbfe0740a1960889edf2120bee0eb53734d939c2ebc1dccac6439d1"
)
EXPECTED_ARTIFACTS = {
    "batches/cont006/explain_only/explain_only_generation.json": (
        "aa326f82f2f5727fa5288a1484be6eaa3b9fd2be2b373b94f710e5a4ed747751"
    ),
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
    "batches/cont006/v41_sealed_pool_resume_audit.json": (
        "dafc53ed681de42d424485602e54bedd96fd5c0ee65470f5cf7fa3d573cd6df8"
    ),
    "v36_safe_probe_fallback_budget.json": (
        "5cd4c824ed124ffb82fe7b9f1cca5553c22b8d35ed81e11c8c847f9e1cb798dc"
    ),
}
EXPECTED_BLOCKED_BUNDLE = {
    "manifest.json": "b0afc740ec1d22fd5003df48ca60c9a6254b92bbaac1c9f51967d74af5c40d5d",
    "selection.json": "cef5539fac003d6d6ef61299330e339b43588b33ac354f9d6b37240179001a55",
    "config_overlay.json": "c020a52517aa6655514d6713a090b40c0a06d9ec8cfb67489854970a3b34c22a",
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


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _verify_bundle(directory: Path) -> None:
    if not directory.is_dir():
        raise RuntimeError(f"XP v42 missing blocked diagnostic bundle: {directory}")
    names = {path.name for path in directory.iterdir() if path.is_file()}
    if names != set(EXPECTED_BLOCKED_BUNDLE):
        raise RuntimeError("XP v42 blocked diagnostic bundle file set mismatch")
    for name, expected in EXPECTED_BLOCKED_BUNDLE.items():
        if sha256(directory / name) != expected:
            raise RuntimeError(f"XP v42 blocked diagnostic bundle SHA mismatch: {name}")


def validate_source(root: Path) -> dict[str, Any]:
    state_path = root / "state.json"
    if not state_path.is_file() or sha256(state_path) != EXPECTED_STATE_SHA256:
        raise RuntimeError("XP v42 recovery source state SHA mismatch")
    for relative, expected in EXPECTED_ARTIFACTS.items():
        path = root / relative
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"XP v42 sealed artifact mismatch: {relative}")
    cont006 = root / "batches/cont006"
    _verify_bundle(cont006 / "diagnostic_bundle")
    for relative in ("selection.json", "replay_summary.json", "serial_replay"):
        if (cont006 / relative).exists():
            raise RuntimeError(f"XP v42 unexpected pre-replay artifact: {relative}")
    if list(cont006.rglob("workload.log")):
        raise RuntimeError("XP v42 cont006 unexpectedly contains workload evidence")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    phase = state.get("active_phase") or {}
    if (
        state.get("status") != "blocked_preworkload"
        or state.get("last_error")
        != "RuntimeError: XP v36 fallback budget contract mismatch"
        or int(state.get("accepted_count", -1)) != 0
        or int(state.get("accepted_target", -1)) != 10
        or int(state.get("next_batch_index", -1)) != 6
        or int((state.get("gradient_state") or {}).get("update_count", -1)) != 0
        or (state.get("last_batch") or {}).get("batch_id") != "cont004"
        or int((state.get("last_batch") or {}).get("physical_calls", -1)) != 5
        or phase.get("batch_id") != "cont006"
        or phase.get("stage") != "offline_reexploration"
        or int(phase.get("physical_workload_calls", -1)) != 0
        or phase.get("manual_candidate_injection") is not False
        or phase.get("formal_state_sha256_before") != FORMAL_SNAPSHOT_SHA256
    ):
        raise RuntimeError("XP v42 recovery observed state mismatch")
    return state


def recover(root: Path) -> dict[str, Any]:
    state_path = root / "state.json"
    state = validate_source(root)
    protected = {key: copy.deepcopy(state.get(key)) for key in PROTECTED_FIELDS}
    protected_sha = digest(protected)
    cont006 = root / "batches/cont006"
    blocked = cont006 / "diagnostic_bundle"
    archive = cont006 / "diagnostic_bundle_v41_blocked_sha_b0afc740"
    if archive.exists():
        _verify_bundle(archive)
    else:
        shutil.copytree(blocked, archive)
        _verify_bundle(archive)

    timestamp = time.strftime("%Y%m%dT%H%M%S")
    backup = root / f"state.before_v42_cont006_transition.{timestamp}.json"
    audit_path = root / f"v42_cont006_transition_recovery.{timestamp}.json"
    if backup.exists() or audit_path.exists():
        raise RuntimeError("XP v42 recovery timestamp collision")
    shutil.copy2(state_path, backup)
    if sha256(backup) != EXPECTED_STATE_SHA256:
        raise RuntimeError("XP v42 state backup SHA mismatch")
    state["status"] = "offline_reexploration"
    state.pop("last_error", None)
    state["active_phase"] = {
        "contract": "xp_v38_atomic_offline_reexploration_phase_state_v1",
        "stage": "offline_reexploration",
        "batch_id": "cont006",
        "reason": "automatic_strict_safe_frontier_transition_family_v42",
        "formal_state_sha256_before": FORMAL_SNAPSHOT_SHA256,
        "physical_workload_calls": 0,
        "manual_candidate_injection": False,
        "sealed_pool_reuse_required": True,
    }
    audit = {
        "contract": "xp_v42_cont006_automatic_transition_zero_call_recovery_v1",
        "source_state_sha256": EXPECTED_STATE_SHA256,
        "backup": str(backup.resolve()),
        "backup_sha256": sha256(backup),
        "blocked_bundle_archive": str(archive.resolve()),
        "blocked_bundle_sha256": copy.deepcopy(EXPECTED_BLOCKED_BUNDLE),
        "protected_state_sha256": protected_sha,
        "next_batch_id": "cont006",
        "workload_calls_added": 0,
        "explain_calls_repeated": 0,
        "accepted_incumbent_range_gradient_mutations": 0,
        "manual_candidate_injection": False,
    }
    state["xp_v42_cont006_transition_recovery"] = copy.deepcopy(audit)
    if digest({key: state.get(key) for key in PROTECTED_FIELDS}) != protected_sha:
        raise RuntimeError("XP v42 recovery would mutate protected state")
    atomic_json(state_path, state)
    audit["recovered_state_sha256"] = sha256(state_path)
    atomic_json(audit_path, audit)
    _verify_bundle(blocked)
    _verify_bundle(archive)
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

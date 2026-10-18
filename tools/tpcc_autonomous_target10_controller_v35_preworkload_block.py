#!/usr/bin/env python3
"""TPC-C v35 controller with explicit pre-workload acquisition blocking."""

from __future__ import annotations

import copy
import fcntl
import sys
import time
from pathlib import Path
from typing import Any, Dict, Mapping

try:
    import tpcc_autonomous_target10_controller_v25 as v25
    import tpcc_autonomous_target10_controller_v31_cross_run_stability as v31
    import tpcc_autonomous_target10_controller_v34_heterogeneous_repeat as v34
except ModuleNotFoundError:
    from tools import tpcc_autonomous_target10_controller_v25 as v25
    from tools import tpcc_autonomous_target10_controller_v31_cross_run_stability as v31
    from tools import tpcc_autonomous_target10_controller_v34_heterogeneous_repeat as v34


BLOCKED_STATUS = "blocked_preworkload_no_novel_top5"
PREPARING_STATUSES = {
    "preparing_next_batch",
    "validation_rankability_failed_preparing_diagnostic",
    "prelaunch_fingerprint_collision_preparing_diagnostic",
    "formal_pool_exhausted_preparing_diagnostic",
}


def _latest_group5_failure(state: Mapping[str, Any]) -> Mapping[str, Any]:
    audits = list(state.get("audits") or [])
    if not audits:
        raise RuntimeError("v35 handoff has no completed audit")
    latest = audits[-1]
    gate = state.get("last_quality_gate") or {}
    if (
        latest.get("mode") != "diagnostic_validation"
        or latest.get("clean") is not True
        or int(latest.get("physical_calls", -1)) != v25.CAP
        or int(latest.get("valid_replays", -1)) != v25.CAP
        or int(latest.get("technical_failures", -1)) != 0
        or latest.get("baseline_restored") is not True
        or int(latest.get("complete_same_group_batch_count", -1))
        != v31.MAX_COMPLETE_RUN_REPEATS
        or gate.get("passed") is True
        or gate.get("formal_start_authorized") is True
        or int(gate.get("complete_same_group_batch_count", -1))
        != v31.MAX_COMPLETE_RUN_REPEATS
    ):
        raise RuntimeError("v35 handoff requires a clean failed fifth group batch")
    return latest


def _preparation_path(manifest: Mapping[str, Any], state: Mapping[str, Any]) -> Path:
    ordinal = int(state.get("next_ordinal", -1))
    if ordinal < 1:
        raise RuntimeError("v35 handoff has invalid next ordinal")
    return Path(manifest["controller_root"]).resolve() / f"prepare_{ordinal:02d}"


def _validate_preworkload_boundary(
    manifest: Mapping[str, Any],
    state: Mapping[str, Any],
) -> Dict[str, Any]:
    latest = _latest_group5_failure(state)
    formal_path = Path(manifest["formal_state_path"]).resolve()
    if (
        state.get("current_run") is not None
        or state.get("next_mode") != "diagnostic"
        or state.get("status") not in PREPARING_STATUSES
        or not formal_path.is_file()
        or state.get("formal_state_sha256") != v25.sha256(formal_path)
    ):
        raise RuntimeError("v35 handoff is not at a state-neutral pre-workload boundary")
    preparation = _preparation_path(manifest, state)
    if not preparation.is_dir():
        raise RuntimeError("v35 handoff preparation directory is absent")
    allowed = {
        "evidence_manifest.json",
        "exact_regime_calibration.json",
    }
    names = {item.name for item in preparation.iterdir()}
    if not names.issubset(allowed) or "top5.json" in names or "sealed_run" in names:
        raise RuntimeError(
            f"v35 handoff has ambiguous prepared output: {sorted(names)}"
        )
    return {
        "latest_audit": latest,
        "formal_state_path": formal_path,
        "preparation_path": preparation,
        "observed_names": sorted(names),
    }


def handoff_eligibility(
    manifest_path: Path,
    *,
    require_unlocked_controller: bool,
) -> Dict[str, Any]:
    manifest_path = manifest_path.resolve()
    manifest = v25.load_json(manifest_path)
    state_path = Path(manifest["controller_state_path"]).resolve()
    state = v25.load_json(state_path)
    boundary = _validate_preworkload_boundary(manifest, state)
    if require_unlocked_controller:
        lock_path = Path(manifest["controller_root"]).resolve() / "controller.lock"
        with lock_path.open("a+", encoding="utf-8") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("v35 handoff controller is still active") from exc
            finally:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
    return {
        "passed": True,
        "status": state["status"],
        "next_mode": state["next_mode"],
        "next_ordinal": int(state["next_ordinal"]),
        "complete_same_group_batch_count": int(
            boundary["latest_audit"]["complete_same_group_batch_count"]
        ),
        "formal_state_sha256": v25.sha256(boundary["formal_state_path"]),
        "preparation_path": str(boundary["preparation_path"]),
        "current_run": None,
        "workload_calls_added": 0,
    }


def _is_acquisition_failure(error: BaseException) -> bool:
    text = str(error)
    return bool(
        "command failed rc=" in text
        or "globally used fingerprints" in text
        or "does not contain exactly five" in text
        or "no candidates" in text.lower()
        or "no top-5" in text.lower()
    )


def record_blocked_preworkload(
    manifest_path: Path,
    error: BaseException,
) -> Dict[str, Any]:
    """Atomically record an unambiguous zero-call acquisition failure."""
    if not _is_acquisition_failure(error):
        raise RuntimeError("v35 refuses to classify a non-acquisition failure") from error
    manifest = v25.load_json(manifest_path.resolve())
    root = Path(manifest["controller_root"]).resolve()
    state_path = Path(manifest["controller_state_path"]).resolve()
    lock_path = root / "controller.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = v25.load_json(state_path)
        boundary = _validate_preworkload_boundary(manifest, state)
        formal_sha = v25.sha256(boundary["formal_state_path"])
        block = {
            "contract": "tpcc_zero_call_no_novel_top5_block_v35",
            "status": BLOCKED_STATUS,
            "timestamp": time.strftime("%Y%m%dT%H%M%S"),
            "error": str(error),
            "next_mode": "diagnostic",
            "next_ordinal": int(state["next_ordinal"]),
            "preparation_path": str(boundary["preparation_path"]),
            "preparation_files": boundary["observed_names"],
            "complete_same_group_batch_count": int(
                boundary["latest_audit"]["complete_same_group_batch_count"]
            ),
            "formal_state_sha256": formal_sha,
            "workload_calls_added": 0,
            "manual_candidate_injection": False,
            "formal_start_authorized": False,
            "gate_bypassed": False,
            "active_replay": False,
            "recovery_action": (
                "supply_new_offline_candidate_pool_then_explicitly_resume"
            ),
        }
        state.setdefault("acquisition_transitions", []).append(
            copy.deepcopy(block)
        )
        state["blocked_preworkload"] = copy.deepcopy(block)
        state["status"] = BLOCKED_STATUS
        state["next_mode"] = "diagnostic"
        state["current_run"] = None
        state["formal_state_sha256"] = formal_sha
        state["controller_runtime_state"] = "stopped_fail_closed"
        v25.atomic_json(state_path, state)
        return block


def main() -> int:
    if len(sys.argv) < 2:
        raise RuntimeError("v35 manifest argument is required")
    manifest_path = Path(sys.argv[1]).resolve()
    handoff_eligibility(manifest_path, require_unlocked_controller=False)
    try:
        return v34.main()
    except RuntimeError as exc:
        record_blocked_preworkload(manifest_path, exc)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

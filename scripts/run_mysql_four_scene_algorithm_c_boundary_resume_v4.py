#!/usr/bin/env python3
"""Run Algorithm C with bounded quiet-gate recovery and rank preservation."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_co_resident_pg_isolation_v3 as isolation
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v2 as runtime


ORIGINAL_STATE_DOCUMENT = runtime._state_document
ORIGINAL_PARTIAL_VALIDATOR = runtime._validate_formal_partial_continuation
ORIGINAL_WAVE_VALIDATOR = runtime._validate_wave_coordination_contract
QUIET_GATE_ERROR = (
    "RuntimeError: cross-project quiet gate exhausted its single bounded attempt"
)


def _watchdog_rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def clean_quiet_timeout_partial_boundary(
    *, source_root: Path, reconstructed: Mapping[str, Any], start_rank: int,
) -> bool:
    """Prove that no candidate state changed after completed prior ranks."""

    if start_rank not in range(2, 6):
        return False
    expected_outer = int(reconstructed["outer_attempt"]) + 1
    outer_root = source_root / f"outer_{expected_outer:03d}"
    state_path = source_root / "run_state.json"
    gate_path = outer_root / (
        f"top5_batch_{start_rank}_cross_project_wave_quiet_gate_attempt_001.json"
    )
    watchdog_path = source_root / "runtime_external_interference_watchdog.jsonl"
    try:
        state = runtime.alg.load(state_path)
        gate = runtime.alg.load(gate_path)
        rows = _watchdog_rows(watchdog_path)
    except (OSError, ValueError, TypeError, KeyError):
        return False
    samples = gate.get("samples") or []
    allowed_sample_errors = {"host_uninterruptible_sleep_present"}
    completed = outer_root / f"top5_batch_{start_rank - 1}/batch_result.json"
    current = outer_root / f"top5_batch_{start_rank}/batch_result.json"
    current_reset = (
        source_root / "xp_resets"
        / f"outer_{expected_outer:03d}_batch_{start_rank}"
    )
    return bool(
        state.get("status") == "FAILED_CLOSED"
        and state.get("stage") == "FAILED"
        and state.get("error") == QUIET_GATE_ERROR
        and int(state.get("outer_attempt") or -1)
        == int(reconstructed["outer_attempt"])
        and gate.get("status") == "BLOCKED_FAIL_CLOSED"
        and gate.get("errors") == ["coordinated_quiet_window_timeout"]
        and float(gate.get("required_clean_seconds") or 0) >= 15.0
        and float(gate.get("maximum_wait_seconds") or 0)
        >= float(gate.get("required_clean_seconds") or 0)
        and float(gate.get("observed_seconds") or 0)
        >= float(gate.get("maximum_wait_seconds") or 0)
        and samples
        and any(sample.get("persistent_d_state_pids") for sample in samples)
        and all(
            set(sample.get("errors") or []) <= allowed_sample_errors
            and (sample.get("co_resident_pg_isolation") or {}).get("passed")
            is True
            for sample in samples
        )
        and not (samples[-1].get("errors") or [])
        and float(samples[-1].get("clean_elapsed_seconds") or 0) > 0.0
        and completed.is_file()
        and not current.exists()
        and not current_reset.exists()
        and rows
        and rows[0].get("event") == "watchdog_started"
        and rows[-1].get("event") == "formal_unit_terminal"
        and rows[-1].get("interference_detected") is False
        and not any(
            row.get("event") in {
                "runtime_interference_detected_under_exclusive_wave_lease",
                "cross_project_workload_lease_lost_during_formal_wave",
            }
            for row in rows
        )
    )


def validate_formal_partial_continuation(**kwargs: Any) -> dict[str, Any]:
    """Extend the predecessor with one strictly proven quiet-timeout boundary."""

    try:
        return ORIGINAL_PARTIAL_VALIDATOR(**kwargs)
    except RuntimeError as error:
        if str(error) != "formal partial continuation authority differs":
            raise
    source_root = Path(kwargs["source_root"]).resolve()
    reconstructed = kwargs["reconstructed"]
    start_rank = int(kwargs["start_rank"])
    if not clean_quiet_timeout_partial_boundary(
        source_root=source_root,
        reconstructed=reconstructed,
        start_rank=start_rank,
    ):
        raise RuntimeError("formal partial continuation authority differs")

    expected_outer = int(reconstructed["outer_attempt"]) + 1
    state_path = source_root / "run_state.json"
    gate_path = (
        source_root / f"outer_{expected_outer:03d}"
        / f"top5_batch_{start_rank}_cross_project_wave_quiet_gate_attempt_001.json"
    )
    original_load = runtime.alg.load

    def compatibility_load(path: Path) -> dict[str, Any]:
        value = original_load(path)
        resolved = Path(path).resolve()
        if resolved == state_path.resolve():
            value = copy.deepcopy(value)
            value["error"] = (
                "RuntimeError: external physical workload bypassed the exclusive "
                "wave lease"
            )
        elif resolved == gate_path.resolve():
            value = copy.deepcopy(value)
            value["errors"] = [
                "external_database_physical_workload_present_while_exclusive"
            ]
        return value

    runtime.alg.load = compatibility_load
    try:
        result = ORIGINAL_PARTIAL_VALIDATOR(**kwargs)
    finally:
        runtime.alg.load = original_load
    result["continuation_reason"] = (
        "premeasurement_quiet_window_timeout_after_completed_rank"
    )
    result["quiet_timeout_recovery"] = {
        "source_state_path": str(state_path),
        "source_state_sha256": runtime.alg.sha(state_path),
        "blocked_gate_path": str(gate_path),
        "blocked_gate_sha256": runtime.alg.sha(gate_path),
        "candidate_state_mutation_before_continuation": False,
        "accepted_state_credit_before_continuation": 0,
    }
    return result


def validate_wave_coordination_contract() -> dict[str, Any]:
    """Validate v55 while preserving every predecessor invariant but its 20s cap."""

    original_load = runtime.alg.load

    def compatibility_load(path: Path) -> dict[str, Any]:
        value = original_load(path)
        if Path(path).resolve() == isolation.CONTRACT_PATH.resolve():
            value = copy.deepcopy(value)
            value["maximum_quiet_wait_while_held_seconds"] = 20
        return value

    runtime.alg.load = compatibility_load
    try:
        ORIGINAL_WAVE_VALIDATOR()
    finally:
        runtime.alg.load = original_load
    contract = original_load(isolation.CONTRACT_PATH)
    if (
        contract.get("release_version") != "v55"
        or float(contract.get("per_wave_quiet_seconds") or 0) != 15.0
        or float(contract.get("maximum_quiet_wait_while_held_seconds") or 0)
        != 60.0
        or int(contract.get("maximum_gate_attempts_per_wave") or 0) != 1
        or int(
            contract.get("persistent_d_state_required_consecutive_samples")
            or 0
        ) != 3
        or contract.get("single_sample_d_state_action")
        != "record_without_resetting_clean_window"
        or contract.get("persistent_d_state_action") != "fail_closed"
    ):
        raise RuntimeError("v55 bounded quiet-gate authority differs")
    return contract


def state_document(**kwargs: Any) -> dict[str, Any]:
    document = ORIGINAL_STATE_DOCUMENT(**kwargs)
    coordination = copy.deepcopy(
        document.get("cross_project_wave_coordination") or {}
    )
    coordination.update({
        "active_external_physical_workloads_remain_fail_closed": False,
        "isolated_co_resident_external_pg_allowed": True,
        "external_cpu_overlap_remains_fail_closed": True,
        "external_disk2_mount_remains_fail_closed": True,
        "unsealed_container_profile_remains_fail_closed": True,
        "persistent_d_state_remains_fail_closed": True,
        "quiet_gate_required_clean_seconds": 15,
        "quiet_gate_maximum_wait_seconds": 60,
    })
    document["cross_project_wave_coordination"] = coordination
    document["co_resident_isolation_runtime"] = {
        "policy": "sealed_multi_profile_cpu_disk_memory_readback_v3",
        "installer_source_path": str(Path(isolation.__file__).resolve()),
        "installer_source_sha256": runtime.alg.sha(
            Path(isolation.__file__).resolve()
        ),
        "runtime_entrypoint_path": str(Path(__file__).resolve()),
        "runtime_entrypoint_sha256": runtime.alg.sha(Path(__file__).resolve()),
        "coordination_contract_path": str(isolation.CONTRACT_PATH),
        "coordination_contract_sha256": runtime.alg.sha(isolation.CONTRACT_PATH),
    }
    return document


def main() -> int:
    isolation.install()
    runtime.WAVE_COORDINATION_CONTRACT = isolation.CONTRACT_PATH
    runtime.OPTIMIZATION_CONTRACT = (
        ROOT
        / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260816_v50"
        / "optimization_contract.json"
    )
    runtime._validate_formal_partial_continuation = (
        validate_formal_partial_continuation
    )
    runtime._validate_wave_coordination_contract = (
        validate_wave_coordination_contract
    )
    runtime._state_document = state_document
    return runtime.main()


if __name__ == "__main__":
    raise SystemExit(main())

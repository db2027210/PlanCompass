#!/usr/bin/env python3
"""No-lease rolling-gate measurement adapter for independent Algorithm B."""

from __future__ import annotations

import copy
from pathlib import Path
import re
from typing import Any, Mapping

from scripts import mysql_cross_project_wave_coordination_v4 as coordination


alg: Any = None


def _campaign(run_id: str) -> str:
    for suffix in ("_job", "_tpch", "_xp_cpu", "_xp_io"):
        if run_id.endswith(suffix):
            value = run_id[:-len(suffix)]
            if value:
                return value
    raise RuntimeError("independent Top-4 run id lacks a formal scene suffix")


def _update(
    *, state_path: Path, state: dict[str, Any], stage: str,
    phase: str, extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    state = copy.deepcopy(state)
    state["stage"] = stage
    state["stage_updated_at"] = alg.now()
    row = dict(state.get("cross_project_wave_coordination") or {})
    row.update({"phase": phase, "updated_at": alg.now()})
    if extra:
        row.update(copy.deepcopy(dict(extra)))
    state["cross_project_wave_coordination"] = row
    alg.atomic(state_path, state)
    return state


def measure_batch(
    *, freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
    assignments: Mapping[str, Mapping[str, Any]], output: Path,
    reset_token: str, state_path: Path, state: dict[str, Any],
    stage_prefix: str, run_id: str, contract: Mapping[str, Any],
    **_kwargs: Any,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if alg is None:
        raise RuntimeError("Algorithm B no-lease adapter is not installed")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    gate_path = output.parent / f"{output.name}_dynamic_noninterference_gate.json"
    if gate_path.exists() or gate_path.is_symlink():
        raise FileExistsError(f"fresh dynamic gate required: {gate_path}")
    state = _update(
        state_path=state_path, state=state,
        phase="dynamic_noninterference_gate",
        stage=f"{stage_prefix}_DYNAMIC_NONINTERFERENCE_GATE",
        extra={
            "batch_output": str(output), "lease_mode": "none",
            "shared_nvme_mutual_exclusion": False,
            "numa_exclusive_scheduler_lock": False,
            "whole_round_resource_lease": False,
            "other_scene_completion_required": False,
            "external_process_control_or_stop_performed": False,
        },
    )
    gate = coordination.rolling_quiet_gate(
        run_id=_campaign(run_id),
        required_seconds=float(contract["per_wave_quiet_seconds"]),
        maximum_wait_seconds=float(contract["maximum_quiet_wait_seconds"]),
        maximum_load1=float(contract.get("maximum_idle_load1", 16.0)),
        target_cpus={
            cpu for worker in workers.values()
            for cpu in alg.r0mod._cpus(str(worker["database_cpuset"]))
            | alg.r0mod._cpus(str(worker["client_cpuset"]))
        },
    )
    gate.update({
        "run_identity_scope": "independent_mysql_scene",
        "run_identity": _campaign(run_id), "lease_mode": "none",
        "external_process_control_or_stop_performed": False,
    })
    alg.atomic(gate_path, gate)
    if gate.get("status") != "PASS":
        raise RuntimeError("dynamic non-interference gate failed closed")
    state = _update(
        state_path=state_path, state=state, phase="measuring",
        stage=f"{stage_prefix}_MEASURING",
        extra={
            "quiet_gate_path": str(gate_path),
            "quiet_gate_sha256": alg.sha(gate_path),
            "four_scene_wave_barrier": False,
            "other_scene_completion_required": False, "lease_mode": "none",
        },
    )
    measured = alg.measure_batch(
        freeze, workers, assignments, output, reset_token,
        state_path=state_path, stage_prefix=stage_prefix,
    )
    if state_path.is_file():
        state = alg.load(state_path)
    state = _update(
        state_path=state_path, state=state, phase="measurement_complete",
        stage=f"{stage_prefix}_MEASUREMENT_COMPLETE",
        extra={"last_gate_status": gate.get("status"), "lease_mode": "none"},
    )
    return measured, state


__all__ = ["alg", "measure_batch"]

#!/usr/bin/env python3
"""Resume Algorithm A with external workloads recorded but never gated."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_a_all_scene_parallel_policy_v1 as parallel_v1
from scripts import mysql_external_workload_diagnostic_gate_v1 as diagnostic_gate
from scripts import mysql_job_buffer_pool_preconditioned_measurement_v10 as job_v10
from scripts import run_mysql_algorithm_a_async_scene_v3 as base
from scripts import run_mysql_algorithm_a_partial_round_codefix_resume_v2 as partial
from scripts import run_mysql_algorithm_a_same_cohort_codefix_resume_v1 as restart


CONTRACT_PATH = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_external_diagnostic_resume_20260823_v1"
    / "transition_contract.json"
)
PREFLIGHT_SOURCE = (
    ROOT / "scripts/preflight_mysql_four_scene_external_diagnostic_only_v1.py"
)
_ORIGINAL_FRESH_STATE = base.fresh_state
_ORIGINAL_VALIDATE = base.validate_fresh_authority
_ACTIVE_SCENE: str | None = None
_ACTIVE_CONTRACT: dict[str, Any] | None = None


def sha(path: Path) -> str:
    return base.alg.sha(path)


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def regular_file(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path.is_symlink() or not path.is_file():
        raise argparse.ArgumentTypeError(f"regular file required: {path}")
    return path


def validate_contract(scene: str, predecessor_path: Path) -> dict[str, Any]:
    contract = load(CONTRACT_PATH)
    row = (contract.get("scenes") or {}).get(scene) or {}
    frozen_predecessor = ROOT / str(row.get("predecessor_state") or "")
    if (
        CONTRACT_PATH.is_symlink()
        or contract.get("schema_version")
        != "mysql_algorithm_a_external_diagnostic_resume_contract_v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("algorithm") != "A"
        or contract.get("accepted_target") != 10
        or contract.get("all_four_scenes_concurrent") is not True
        or contract.get("external_workloads_diagnostic_only") is not True
        or contract.get("external_workload_wait_required") is not False
        or contract.get("external_process_control_or_stop_prohibited") is not True
        or contract.get("mysql_fixed_cpu_affinity_retained") is not True
        or contract.get("numa_exclusive_scheduler_lock_required") is not False
        or row.get("physical_replay_parallelism") != 2
        or frozen_predecessor.resolve() != predecessor_path.resolve()
        or frozen_predecessor.is_symlink()
        or not frozen_predecessor.is_file()
        or sha(frozen_predecessor) != row.get("predecessor_state_sha256")
    ):
        raise RuntimeError("external-diagnostic Algorithm A contract differs")
    if scene == "job_memory":
        if (
            row.get("interrupted_round_repeated_from_rank_one") is not True
            or row.get("cached_candidate_measurements_reused") is not False
            or row.get("job_cache_precondition_v10_required") is not True
            or row.get("scored_suite_hard_timeout_seconds") != 900
        ):
            raise RuntimeError("external-diagnostic JOB transition differs")
    elif (
        row.get("cached_completed_candidate_prefix_reused") is not True
        or row.get("completed_candidate_measurements_replayed") is not False
    ):
        raise RuntimeError("external-diagnostic partial transition differs")
    for binding in (contract.get("source_bindings") or {}).values():
        source = ROOT / str(binding.get("path") or "")
        if source.is_symlink() or not source.is_file() or sha(source) != binding.get("sha256"):
            raise RuntimeError(f"external-diagnostic source differs: {source}")
    return contract


def validate_fresh_authority(**kwargs: Any):
    previous_source = base.alg.r0mod.PREFLIGHT_SOURCE
    base.alg.r0mod.PREFLIGHT_SOURCE = PREFLIGHT_SOURCE
    try:
        result = _ORIGINAL_VALIDATE(**kwargs)
    finally:
        base.alg.r0mod.PREFLIGHT_SOURCE = previous_source
    base.runtime.coordination.rolling_quiet_gate = diagnostic_gate.rolling_quiet_gate
    if kwargs.get("scene") == "job_memory":
        job_v10._validate_contract()
        base.runtime.alg.measure_batch = job_v10.measure_batch
    return result


def fresh_state(**kwargs: Any) -> dict[str, Any]:
    if _ACTIVE_SCENE is None or _ACTIVE_CONTRACT is None:
        raise RuntimeError("external-diagnostic scene was not selected")
    state = parallel_v1.annotate_state(_ORIGINAL_FRESH_STATE(**kwargs))
    state["external_workload_policy"] = {
        "contract_path": str(CONTRACT_PATH),
        "contract_sha256": sha(CONTRACT_PATH),
        "external_workloads_diagnostic_only": True,
        "external_workload_wait_required": False,
        "external_process_control_or_stop_prohibited": True,
        "mysql_fixed_cpu_affinity_retained": True,
        "numa_exclusive_scheduler_lock_required": False,
        "all_four_scenes_concurrent": True,
    }
    # Supersede the v1 annotation's old external-gate statement explicitly.
    state["all_scene_parallel_execution"][
        "rolling_dynamic_noninterference_gate_required"
    ] = False
    state["all_scene_parallel_execution"][
        "external_workload_inventory_diagnostic_only"
    ] = True
    return state


def run(args: argparse.Namespace) -> int:
    global _ACTIVE_SCENE, _ACTIVE_CONTRACT
    scene = str(args.scene)
    _ACTIVE_SCENE = scene
    _ACTIVE_CONTRACT = validate_contract(scene, args.predecessor_state)
    parallel_v1.validate_contract()
    base.runtime.coordination.rolling_quiet_gate = diagnostic_gate.rolling_quiet_gate
    base.validate_fresh_authority = validate_fresh_authority
    base.fresh_state = fresh_state
    if scene == "job_memory":
        job_v10._validate_contract()
        base.runtime.alg.measure_batch = job_v10.measure_batch
        return restart.run(args)
    return partial.run(args)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scene", choices=base.SCENES, required=True)
    parser.add_argument("--predecessor-state", type=regular_file, required=True)
    parser.add_argument("--runtime-preflight", type=regular_file, required=True)
    parser.add_argument("--prepared-binding", type=regular_file, required=True)
    parser.add_argument("--benchmark-accounts", type=regular_file, required=True)
    parser.add_argument("--fresh-r0", type=regular_file, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Resume four-worker JOB without repeating a slot-expansion calibration."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_external_workload_diagnostic_gate_v1 as diagnostic_gate
from scripts import mysql_job_buffer_pool_preconditioned_measurement_v11 as job_v11
from scripts import run_mysql_algorithm_a_async_scene_v3 as base
from scripts import run_mysql_algorithm_a_job_buffer_pool_dynamic_external_diagnostic_resume_v1 as dynamic_external
from scripts import run_mysql_algorithm_a_same_cohort_codefix_resume_v1 as resume


CONFIG = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_job4_no_recalibration_stagger_resume_20260824_v1"
)
CONTRACT_PATH = CONFIG / "resume_contract.json"
PREDECESSOR_PATH = CONFIG / "resume_predecessor_snapshot.json"
ORIGINAL_BOUNDARY = (
    ROOT
    / "history/mysql_four_scene_20260809/v99_to_a_external_diagnostic_resume_20260823_job_dynamic_slots_v1"
    / "job_memory.predecessor_boundary_snapshot.json"
)
AUTHORITY_PATH = (
    ROOT
    / "history/mysql_four_scene_20260809/v99_to_a_external_diagnostic_resume_20260823_job_dynamic_slots_v1"
    / "job_memory.transition_authority.json"
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def _sha(path: Path) -> str:
    return base.alg.sha(path)


def _validate_contract(predecessor_path: Path) -> dict[str, Any]:
    contract = _load(CONTRACT_PATH)
    if (
        CONTRACT_PATH.is_symlink()
        or contract.get("schema_version")
        != "mysql_algorithm_a_job4_no_recalibration_stagger_resume_v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("algorithm") != "A"
        or contract.get("scene") != "job_memory"
        or contract.get("accepted_target") != 10
        or contract.get("preserved_accepted_count") != 2
        or contract.get("physical_replay_parallelism") != 4
        or contract.get("slot_expansion_recalibration_repeated") is not False
        or contract.get("interrupted_round_restarts_from_rank_one") is not True
        or contract.get("candidate_set_changed") is not False
        or contract.get("outer_loop_semantics_changed") is not False
        or contract.get("resource_envelope_changed") is not False
        or contract.get("external_workloads_diagnostic_only") is not True
        or contract.get("external_workload_wait_required") is not False
        or contract.get("external_process_control_or_stop_prohibited") is not True
        or predecessor_path.resolve() != PREDECESSOR_PATH.resolve()
        or contract.get("predecessor_snapshot_sha256") != _sha(PREDECESSOR_PATH)
        or contract.get("transition_authority_sha256") != _sha(AUTHORITY_PATH)
        or contract.get("original_boundary_sha256") != _sha(ORIGINAL_BOUNDARY)
    ):
        raise RuntimeError("JOB4 no-recalibration resume contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        source = ROOT / str(binding.get("path") or "")
        if source.is_symlink() or not source.is_file() or _sha(source) != binding.get("sha256"):
            raise RuntimeError(f"JOB4 no-recalibration source differs: {source}")
    job_v11._validate_contract()
    return contract


def run(args: Any) -> int:
    contract = _validate_contract(args.predecessor_state)
    scene = str(args.scene)
    if scene != "job_memory":
        raise RuntimeError("JOB4 no-recalibration continuation is JOB-only")

    active, _ranges = base.scene_parameter_space(scene)
    algorithm_contract = base.validate_a_contract(scene, active)
    freeze, workers, authority = dynamic_external.validate_authority(
        AUTHORITY_PATH, ORIGINAL_BOUNDARY, scene,
    )
    freeze_path = Path(str(authority["resource_freeze_path"]))
    dynamic_external.configure_runtime(
        freeze_path=freeze_path, workers=workers, scene=scene,
    )
    base.REPLAY_PARALLELISM = 4
    base.alg.WORKER_IDS = tuple(sorted(workers))
    base.runtime.alg.measure_batch = job_v11.measure_batch
    base.runtime.coordination.rolling_quiet_gate = diagnostic_gate.rolling_quiet_gate

    original_fresh_state = base.fresh_state

    def four_worker_fresh_state(**kwargs: Any) -> dict[str, Any]:
        state = copy.deepcopy(original_fresh_state(**kwargs))
        state["physical_replay_parallelism"] = 4
        state["external_workload_policy"] = {
            "contract_path": str(CONTRACT_PATH),
            "contract_sha256": _sha(CONTRACT_PATH),
            "external_workloads_diagnostic_only": True,
            "external_workload_wait_required": False,
            "external_process_control_or_stop_prohibited": True,
            "mysql_fixed_cpu_affinity_retained": True,
            "numa_exclusive_scheduler_lock_required": False,
            "all_four_scenes_concurrent_before_slot_handoff": True,
        }
        state["job4_no_recalibration_resume"] = {
            "slot_expansion_recalibration_repeated": False,
            "baseline_mapping": copy.deepcopy(contract["baseline_mapping"]),
            "suite_start_stagger_seconds": 90,
            "interrupted_round_restarts_from_rank_one": True,
        }
        return state

    base.fresh_state = four_worker_fresh_state
    base.validate_a_contract = lambda _scene, _active: algorithm_contract
    base.validate_fresh_authority = lambda **_kwargs: (
        freeze, copy.deepcopy(workers), copy.deepcopy(authority)
    )
    args.runtime_preflight = AUTHORITY_PATH
    args.prepared_binding = AUTHORITY_PATH
    args.benchmark_accounts = AUTHORITY_PATH
    args.fresh_r0 = AUTHORITY_PATH
    return resume.run(args)


def main() -> int:
    parser = resume.argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scene", choices=("job_memory",), required=True)
    parser.add_argument("--predecessor-state", type=resume.regular_file, required=True)
    parser.add_argument("--runtime-preflight", type=resume.regular_file, required=True)
    parser.add_argument("--prepared-binding", type=resume.regular_file, required=True)
    parser.add_argument("--benchmark-accounts", type=resume.regular_file, required=True)
    parser.add_argument("--fresh-r0", type=resume.regular_file, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())

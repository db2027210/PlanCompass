#!/usr/bin/env python3
"""Resume accepted-6 JOB round 11 on all four frozen JOB workers."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v4 as dynamic_gate
from scripts import mysql_job_buffer_pool_preconditioned_measurement_v10 as job_v10
from scripts import preflight_mysql_four_scene_v7 as preflight_v7
from scripts import run_mysql_algorithm_a_async_scene_v3 as base
from scripts import run_mysql_algorithm_a_job_buffer_pool_dynamic_external_diagnostic_resume_v1 as dynamic
from scripts import run_mysql_algorithm_a_same_cohort_codefix_resume_v1 as resume


CONFIG = ROOT / "config/formal/frozen_mysql_algorithm_a_job4_round11_20260825_v1"
CONTRACT = CONFIG / "resume_contract.json"
SNAPSHOT = CONFIG / "resume_predecessor_snapshot.json"
AUTHORITY = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v99_to_a_external_diagnostic_resume_20260823_job_dynamic_slots_v1"
    / "job_memory.transition_authority.json"
)
ORIGINAL_BOUNDARY = AUTHORITY.with_name("job_memory.predecessor_boundary_snapshot.json")


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def validate_contract(predecessor: Path) -> dict[str, Any]:
    contract = load(CONTRACT)
    if (
        contract.get("schema_version") != "mysql_algorithm_a_job4_round11_resume_v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("algorithm") != "A"
        or contract.get("scene") != "job_memory"
        or contract.get("preserved_accepted_count") != 6
        or contract.get("preserved_normalized_width") != 0.015625
        or contract.get("resumed_round_index") != 11
        or contract.get("physical_replay_parallelism") != 4
        or contract.get("slot_expansion_recalibration_repeated") is not False
        or contract.get("interrupted_precondition_credit") != 0
        or contract.get("candidate_set_changed") is not False
        or contract.get("outer_loop_semantics_changed") is not False
        or contract.get("resource_envelope_changed") is not False
        or contract.get("cross_scene_completion_dependency") is not False
        or predecessor.resolve() != SNAPSHOT.resolve()
        or base.alg.sha(SNAPSHOT) != contract.get("predecessor_snapshot_sha256")
        or base.alg.sha(AUTHORITY) != contract.get("transition_authority_sha256")
    ):
        raise RuntimeError("JOB4 round-11 resume contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding.get("path") or "")
        if path.is_symlink() or not path.is_file() or base.alg.sha(path) != binding.get("sha256"):
            raise RuntimeError(f"JOB4 round-11 source differs: {path}")
    job_v10._validate_contract()
    return contract


def run(args: Any) -> int:
    contract = validate_contract(args.predecessor_state)
    active, _ranges = base.scene_parameter_space("job_memory")
    algorithm_contract = base.validate_a_contract("job_memory", active)
    freeze, workers, authority = dynamic.validate_authority(
        AUTHORITY, ORIGINAL_BOUNDARY, "job_memory",
    )
    if set(workers) != {"job_w1", "job_w2", "job_w3", "job_w4"}:
        raise RuntimeError("exact four-worker JOB cohort required")
    freeze_path = Path(str(authority["resource_freeze_path"]))
    dynamic.configure_runtime(
        freeze_path=freeze_path, workers=workers, scene="job_memory",
    )
    base.REPLAY_PARALLELISM = 4
    base.alg.WORKER_IDS = tuple(sorted(workers))
    base.runtime.alg.measure_batch = job_v10.measure_batch
    base.alg.r0mod.PREFLIGHT_SOURCE = Path(preflight_v7.__file__).resolve()
    base.runtime.coordination.rolling_quiet_gate = dynamic_gate.rolling_quiet_gate

    original_fresh_state = base.fresh_state

    def four_worker_fresh_state(**kwargs: Any) -> dict[str, Any]:
        state = copy.deepcopy(original_fresh_state(**kwargs))
        state["physical_replay_parallelism"] = 4
        state["job4_round11_transition"] = {
            "contract_path": str(CONTRACT),
            "contract_sha256": base.alg.sha(CONTRACT),
            "slot_expansion_recalibration_repeated": False,
            "interrupted_precondition_credit": 0,
            "candidate_measurement_replayed": False,
            "round_restarted_from_rank_one": True,
            "baseline_mapping": copy.deepcopy(contract["baseline_mapping"]),
            "cross_scene_completion_dependency": False,
        }
        return state

    base.fresh_state = four_worker_fresh_state
    base.validate_a_contract = lambda _scene, _active: algorithm_contract
    base.validate_fresh_authority = lambda **_kwargs: (
        freeze, copy.deepcopy(workers), copy.deepcopy(authority)
    )
    args.runtime_preflight = AUTHORITY
    args.prepared_binding = AUTHORITY
    args.benchmark_accounts = AUTHORITY
    args.fresh_r0 = AUTHORITY
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

#!/usr/bin/env python3
"""Resume JOB A from its rank-12 checkpoint with the v12 suite timer."""

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
from scripts import mysql_job_buffer_pool_preconditioned_measurement_v12 as job_v12
from scripts import run_mysql_algorithm_a_async_scene_v3 as base
from scripts import run_mysql_algorithm_a_job_buffer_pool_dynamic_external_diagnostic_resume_v1 as dynamic_external
from scripts import run_mysql_algorithm_a_partial_round_codefix_resume_v2 as partial


CONFIG = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_partial_suite_timer_resume_20260824_v1"
)
CONTRACT_PATH = CONFIG / "resume_contract.json"
PREDECESSOR_PATH = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v113_to_a_job4_no_recalibration_stagger_resume_20260824_attempt001_job"
    / "algorithm_a_state.json"
)
ORIGINAL_BOUNDARY = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v99_to_a_external_diagnostic_resume_20260823_job_dynamic_slots_v1"
    / "job_memory.predecessor_boundary_snapshot.json"
)
AUTHORITY_PATH = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v99_to_a_external_diagnostic_resume_20260823_job_dynamic_slots_v1"
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
        != "mysql_algorithm_a_job_partial_suite_timer_resume_v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("algorithm") != "A"
        or contract.get("scene") != "job_memory"
        or contract.get("accepted_target") != 10
        or contract.get("preserved_accepted_count") != 2
        or contract.get("physical_replay_parallelism") != 4
        or contract.get("cached_attempted_ranks") != list(range(1, 13))
        or contract.get("new_physical_work_starts_at_rank") != 13
        or contract.get("slot_expansion_recalibration_repeated") is not False
        or contract.get("cached_prefix_physically_replayed") is not False
        or contract.get("candidate_set_changed") is not False
        or contract.get("outer_loop_semantics_changed") is not False
        or contract.get("resource_envelope_changed") is not False
        or predecessor_path.resolve() != PREDECESSOR_PATH.resolve()
        or contract.get("predecessor_state_sha256") != _sha(PREDECESSOR_PATH)
        or contract.get("transition_authority_sha256") != _sha(AUTHORITY_PATH)
    ):
        raise RuntimeError("JOB partial suite-timer resume contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        source = ROOT / str(binding.get("path") or "")
        if source.is_symlink() or not source.is_file() or _sha(source) != binding.get("sha256"):
            raise RuntimeError(f"JOB partial suite-timer source differs: {source}")
    job_v12._validate_contract()
    return contract


def run(args: Any) -> int:
    contract = _validate_contract(args.predecessor_state)
    if str(args.scene) != "job_memory":
        raise RuntimeError("JOB partial suite-timer continuation is JOB-only")
    active, _ranges = base.scene_parameter_space("job_memory")
    algorithm_contract = base.validate_a_contract("job_memory", active)
    freeze, workers, authority = dynamic_external.validate_authority(
        AUTHORITY_PATH, ORIGINAL_BOUNDARY, "job_memory",
    )
    dynamic_external.configure_runtime(
        freeze_path=Path(str(authority["resource_freeze_path"])),
        workers=workers, scene="job_memory",
    )
    base.REPLAY_PARALLELISM = 4
    base.alg.WORKER_IDS = tuple(sorted(workers))
    base.runtime.alg.measure_batch = job_v12.measure_batch
    base.runtime.coordination.rolling_quiet_gate = diagnostic_gate.rolling_quiet_gate

    original_rows = partial.measurement_rows
    original_atomic = base.atomic

    def reconciled_rows(document: Mapping[str, Any], expected_workers: set[str] | None):
        return original_rows(job_v12.reconcile_document(document), expected_workers)

    def reconciled_atomic(path: Path, value: Mapping[str, Any]) -> None:
        document = (
            job_v12.reconcile_document(value)
            if path.name == "batch_result.json" else value
        )
        original_atomic(path, document)

    partial.measurement_rows = reconciled_rows
    base.atomic = reconciled_atomic
    base.validate_a_contract = lambda _scene, _active: algorithm_contract
    base.validate_fresh_authority = lambda **_kwargs: (
        freeze, copy.deepcopy(workers), copy.deepcopy(authority)
    )
    args.runtime_preflight = AUTHORITY_PATH
    args.prepared_binding = AUTHORITY_PATH
    args.benchmark_accounts = AUTHORITY_PATH
    args.fresh_r0 = AUTHORITY_PATH
    try:
        return partial.run(args)
    finally:
        partial.measurement_rows = original_rows
        base.atomic = original_atomic


def main() -> int:
    parser = partial.argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scene", choices=("job_memory",), required=True)
    parser.add_argument("--predecessor-state", type=partial.regular_file, required=True)
    parser.add_argument("--runtime-preflight", type=partial.regular_file, required=True)
    parser.add_argument("--prepared-binding", type=partial.regular_file, required=True)
    parser.add_argument("--benchmark-accounts", type=partial.regular_file, required=True)
    parser.add_argument("--fresh-r0", type=partial.regular_file, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())

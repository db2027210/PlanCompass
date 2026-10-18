#!/usr/bin/env python3
"""Resume JOB round 10 wave 7 while reusing its completed cache primer."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v2 as enforced_gate
from scripts import mysql_job_buffer_pool_preconditioned_measurement_v10 as job_v10
from scripts import preflight_mysql_four_scene_v5 as preflight_v5
from scripts import run_mysql_algorithm_a_job_dynamic_resume_v3 as dynamic
from scripts import run_mysql_algorithm_a_partial_round_codefix_resume_v2 as partial
from scripts import run_mysql_algorithm_a_job_round10_wave7_interference_resume_v1 as v1


CONFIG = (
    ROOT / "config/formal"
    / "frozen_mysql_algorithm_a_job_round10_wave7_interference_resume_20260825_v2"
)
CONTRACT = CONFIG / "resume_contract.json"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def validate_contract(predecessor: Path) -> dict[str, Any]:
    # Retain every v1 authority and cached-wave check, then add the one-time
    # cache-primer reuse authority created after the interrupted measurement.
    v1.validate_contract(predecessor)
    contract = load(CONTRACT)
    primer_path = Path(str(contract.get("reused_precondition_path") or "")).resolve()
    interrupted_state_path = Path(
        str(contract.get("interrupted_state_path") or "")
    ).resolve()
    if (
        contract.get("schema_version")
        != "mysql_algorithm_a_job_wave7_interference_resume_contract_v2"
        or contract.get("release_version") != "v2"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("algorithm") != "A"
        or contract.get("scene") != "job_memory"
        or contract.get("cached_valid_waves") != [1, 2, 3, 4, 5, 6]
        or contract.get("cached_valid_ranks") != list(range(1, 13))
        or contract.get("first_new_physical_wave") != 7
        or contract.get("first_new_physical_rank") != 13
        or contract.get("reused_completed_cache_precondition") is not True
        or contract.get("reused_precondition_formal_credit") != 0
        or contract.get("reused_precondition_metrics_used_for_scoring") is not False
        or contract.get("scored_measurement_replayed_from_start") is not True
        or contract.get("partial_scored_measurement_reused") is not False
        or contract.get("dynamic_non_interference_gate_required") is not True
        or predecessor.resolve() != Path(str(contract["checkpoint_path"])).resolve()
        or partial.sha(predecessor) != contract.get("checkpoint_sha256")
        or partial.sha(primer_path) != contract.get("reused_precondition_sha256")
        or partial.sha(interrupted_state_path)
        != contract.get("interrupted_state_sha256")
    ):
        raise RuntimeError("JOB wave-7 primer-reuse resume contract differs")
    primer = load(primer_path)
    coverage = primer.get("coverage") or {}
    interrupted = load(interrupted_state_path)
    if (
        primer.get("status") != "PASS"
        or primer.get("scene") != "job_memory"
        or primer.get("required_query_count") != 113
        or primer.get("metrics_used_for_scoring") is not False
        or primer.get("accepted_state_credit") != 0
        or set(coverage) != {"job_w1", "job_w2"}
        or any(
            row.get("completed_query_count") != 113
            or row.get("valid") is not True
            for row in coverage.values()
        )
        or interrupted.get("status") != "INTERRUPTED_FAIL_CLOSED"
        or interrupted.get("stage") != "FAILED"
        or interrupted.get("round_index") != 10
        or interrupted.get("error")
        != "RuntimeError: runtime_noninterference_failure_during_job_measurement"
        or len(interrupted.get("accepted_history") or []) != 5
        or interrupted.get("rejected_rounds") != 4
    ):
        raise RuntimeError("JOB wave-7 primer/interruption evidence differs")
    for source in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(source.get("path") or "")
        if (
            path.is_symlink()
            or not path.is_file()
            or partial.sha(path) != source.get("sha256")
        ):
            raise RuntimeError(f"JOB primer-reuse binding differs: {path}")
    return contract


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
    args = parser.parse_args()
    contract = validate_contract(args.predecessor_state)
    authority_path = Path(str(contract["authority_path"])).resolve()
    freeze_path = Path(str(contract["resource_freeze_path"])).resolve()
    primer_path = Path(str(contract["reused_precondition_path"])).resolve()
    authority = load(authority_path)
    freeze = load(freeze_path)
    all_workers = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in authority.get("workers") or []
    }
    workers = {worker_id: all_workers[worker_id] for worker_id in ("job_w1", "job_w2")}
    if (
        partial.sha(authority_path) != contract.get("authority_sha256")
        or partial.sha(freeze_path) != contract.get("resource_freeze_sha256")
        or set(all_workers) != {"job_w1", "job_w2", "job_w3", "job_w4"}
    ):
        raise RuntimeError("JOB primer-reuse runtime authority differs")

    primer = load(primer_path)

    def reuse_precondition(
        runtime_workers: Mapping[str, Mapping[str, Any]],
        assignments: Mapping[str, Mapping[str, Any]],
        _output: Path,
        *, state_path: Path | None = None,
        stage_prefix: str = "FORMAL_POINT",
    ) -> dict[str, Any]:
        del state_path
        if (
            set(runtime_workers) != {"job_w1", "job_w2"}
            or set(assignments) != {"job_w1", "job_w2"}
            or "ROUND_010_WAVE_007_ATTEMPT_01" not in stage_prefix
        ):
            raise RuntimeError("JOB primer reuse requested outside frozen wave 7")
        evidence = copy.deepcopy(primer)
        evidence["reused_completed_precondition"] = True
        evidence["reused_precondition_path"] = str(primer_path)
        evidence["reused_precondition_sha256"] = partial.sha(primer_path)
        evidence["reuse_contract_path"] = str(CONTRACT)
        evidence["reuse_contract_sha256"] = partial.sha(CONTRACT)
        evidence["scored_measurement_replayed_from_start"] = True
        evidence["partial_scored_measurement_reused"] = False
        evidence["accepted_state_credit"] = 0
        evidence["interval_shrink_credit"] = 0
        evidence["metrics_used_for_scoring"] = False
        return evidence

    dynamic.adapter.configure_runtime(
        freeze_path=freeze_path, workers=workers, scene="job_memory",
    )
    partial.base.REPLAY_PARALLELISM = 2
    partial.base.runtime.alg.measure_batch = job_v10.measure_batch
    partial.base.alg.r0mod.PREFLIGHT_SOURCE = Path(preflight_v5.__file__).resolve()
    partial.base.runtime.coordination.rolling_quiet_gate = enforced_gate.rolling_quiet_gate
    context = {
        "coordination_contract": dynamic.current.c49.validate_v65_wave_coordination_contract(),
        "resource_freeze_path": str(freeze_path),
        "resource_freeze_sha256": partial.sha(freeze_path),
        "interference_resume_contract_path": str(CONTRACT),
        "interference_resume_contract_sha256": partial.sha(CONTRACT),
        "external_workload_gate_enforced": True,
        "external_process_control_or_stop_prohibited": True,
        "cached_valid_waves": [1, 2, 3, 4, 5, 6],
        "first_new_physical_wave": 7,
        "reused_completed_cache_precondition": True,
        "reused_precondition_path": str(primer_path),
        "reused_precondition_sha256": partial.sha(primer_path),
    }
    partial.base.validate_fresh_authority = lambda **_kwargs: (
        freeze, copy.deepcopy(workers), copy.deepcopy(context)
    )

    original_validate_cached = partial.validate_cached_round
    original_precondition = job_v10.v9._precondition
    source_state = Path(str(contract["source_evidence_root"])) / "algorithm_a_state.json"

    def validate_cached_from_source(
        predecessor: Mapping[str, Any], _predecessor_path: Path,
        interrupted_round: int,
    ):
        return original_validate_cached(predecessor, source_state, interrupted_round)

    partial.validate_cached_round = validate_cached_from_source
    job_v10.v9._precondition = reuse_precondition
    try:
        return partial.run(args)
    finally:
        job_v10.v9._precondition = original_precondition
        partial.validate_cached_round = original_validate_cached


if __name__ == "__main__":
    raise SystemExit(main())

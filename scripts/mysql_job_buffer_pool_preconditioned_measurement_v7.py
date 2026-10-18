#!/usr/bin/env python3
"""JOB measurement with a frozen non-scoring InnoDB working-set primer."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

from scripts import mysql_job_cache_preconditioned_measurement_v1 as legacy

alg = legacy.alg
predecessor = legacy.predecessor
ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT / "config/formal/frozen_mysql_job_cache_state_20260823_v7"
    / "cache_state_contract.json"
)
PRIMER_PARAMS = {
    "disk_temptable_create_cost": 20,
    "disk_temptable_row_cost": 0.5,
    "io_block_read_cost": 0.575439937337,
    "key_compare_cost": 0.05,
    "memory_block_read_cost": 0.434450207187,
    "memory_temptable_create_cost": 1,
    "memory_temptable_row_cost": 0.173780082875,
    "row_evaluate_cost": 0.0575439937337,
}


def _validate_contract() -> dict[str, Any]:
    if CONTRACT_PATH.is_symlink() or not CONTRACT_PATH.is_file():
        raise RuntimeError(f"JOB buffer-pool contract absent: {CONTRACT_PATH}")
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if (
        contract.get("schema_version") != "mysql_job_cache_state_contract_v7"
        or contract.get("release_version") != "v7"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("scene") != "job_memory"
        or contract.get("precondition_mode")
        != "fixed_optimizer_plan_full_job113_innodb_working_set_primer"
        or contract.get("precondition_parameter_vector") != PRIMER_PARAMS
        or contract.get("precondition_query_count") != 113
        or contract.get("precondition_replays_per_scored_measurement") != 1
        or contract.get("precondition_metrics_used_for_scoring") is not False
        or contract.get("candidate_parameter_application_after_precondition") is not True
        or contract.get("formal_measurement_replays_per_candidate") != 1
        or contract.get("outer_loop_semantics_changed") is not False
    ):
        raise RuntimeError("JOB buffer-pool v7 contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding["path"])
        if path.is_symlink() or not path.is_file() or legacy._sha(path) != binding["sha256"]:
            raise RuntimeError(f"JOB buffer-pool v7 source differs: {path}")
    return contract


def _precondition(
    workers: dict[str, dict[str, Any]],
    assignments: Mapping[str, Mapping[str, Any]], output: Path,
    *, state_path: Path | None, stage_prefix: str,
) -> dict[str, Any]:
    original_defaults = alg.DEFAULTS
    try:
        alg.DEFAULTS = copy.deepcopy(PRIMER_PARAMS)
        evidence = legacy._precondition(
            workers, assignments, output,
            state_path=state_path, stage_prefix=stage_prefix,
        )
    finally:
        alg.DEFAULTS = original_defaults
    artifact = Path(str(evidence["path"]))
    document = alg.load(artifact)
    document["schema_version"] = "mysql_job_buffer_pool_precondition_result_v7"
    document["precondition_mode"] = (
        "fixed_optimizer_plan_full_job113_innodb_working_set_primer"
    )
    document["precondition_parameter_role"] = (
        "cache_priming_only_not_algorithm_default_or_scored_candidate"
    )
    document["precondition_parameter_vector"] = copy.deepcopy(PRIMER_PARAMS)
    document.pop("default_params", None)
    document["contract_path"] = str(CONTRACT_PATH)
    document["contract_sha256"] = legacy._sha(CONTRACT_PATH)
    alg.atomic(artifact, document)
    return {
        "path": str(artifact), "sha256": legacy._sha(artifact),
        "worker_count": len(assignments),
    }


def measure_batch(
    freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
    assignments: Mapping[str, Mapping[str, Any]], output: Path,
    reset_token: str, *, state_path: Path | None = None,
    stage_prefix: str = "FORMAL_POINT",
) -> dict[str, dict[str, Any]]:
    contract = _validate_contract()
    scenes = {str(workers[worker_id]["scene"]) for worker_id in assignments}
    precondition = None
    if scenes == {"job_memory"}:
        precondition = _precondition(
            workers, assignments, output,
            state_path=state_path, stage_prefix=stage_prefix,
        )
    measured = predecessor.measure_batch(
        freeze, workers, assignments, output, reset_token,
        state_path=state_path, stage_prefix=stage_prefix,
    )
    batch_path = output / "batch_result.json"
    document = alg.load(batch_path)
    document["job_cache_state_contract"] = {
        "path": str(CONTRACT_PATH), "sha256": legacy._sha(CONTRACT_PATH),
        "release_version": contract["release_version"],
        "applied": precondition is not None,
    }
    document["job_buffer_pool_precondition"] = precondition
    document["precondition_metrics_used_for_scoring"] = False
    document["outer_loop_semantics_changed"] = False
    alg.atomic(batch_path, document)
    return measured


__all__ = ["CONTRACT_PATH", "PRIMER_PARAMS", "measure_batch"]

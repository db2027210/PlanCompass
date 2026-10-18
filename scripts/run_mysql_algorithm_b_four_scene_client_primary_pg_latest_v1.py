#!/usr/bin/env python3
"""Run fresh client-primary Algorithm B through the latest PG-aligned C outer loop.

The executable outer state machine is exactly the MySQL C v41 runtime.  This
adapter replaces only the offline candidate generator with Algorithm B's
deterministic 15-point coordinate grid and exhaustive 2--4 parameter
interaction search.  Physical replay, current-interval coverage, strict-QPS
acceptance, two-rejection incumbent shrink, and convergence remain owned by
the shared outer runtime.
"""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from types import ModuleType
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_four_scene_algorithm_b_pg_grid_v30 as inner
from scripts import mysql_algorithm_c_measurement_client_primary_v1 as client_measurement
from scripts import mysql_single_scene_measurement_v2 as single_scene_measurement
from scripts import mysql_outer_rejection_cycle_v2 as rejection_cycle
from scripts import mysql_c_external_storage_inventory_only_v2 as external_inventory_v2
from scripts import reset_mysql_xp_data_planes_client_primary_v3 as xp_reset
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v11 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v4 as boundary_v4
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v41 as outer


METHOD = "mysql_algorithm_b_pg_grid_shared_outer_v41_v1"
STATE_SCHEMA = "mysql_algorithm_b_pg_latest_outer_state_v1"
RANKING_SCHEMA = "mysql_algorithm_b_pg_latest_outer_rankings_v1"
CONTRACT_ROOT = ROOT / (
    "config/formal/frozen_mysql_algorithm_b_pg_latest_outer_20260905_v104"
)
CONTRACT = CONTRACT_ROOT / "algorithm_b_pg_latest_outer_contract.json"
OPTIMIZATION_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260904_v95/"
    "outer_runtime_optimization.json"
)


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def strategy_contract() -> dict[str, Any]:
    return {
        "algorithm": "B",
        "method": METHOD,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "outer_runtime": "mysql_algorithm_c_pg_latest_outer_v41",
        "only_algorithmic_difference_from_c": "inner_candidate_generation",
        "inner_candidate_generation": inner.search_contract(),
        "local_real_replay_limit": outer.LOCAL_REAL_REPLAY_LIMIT,
        "global_coverage_sample_count": outer.GLOBAL_COVERAGE_SAMPLE_COUNT,
        "global_coverage_real_replay_limit": (
            outer.GLOBAL_COVERAGE_REAL_REPLAY_LIMIT
        ),
        "coverage_after_every_exhausted_local_round": True,
        "coverage_domain": "current_formal_interval_only",
        "original_domain_reopened_after_shrink": False,
        "strict_higher_qps_acceptance": True,
        "two_rejections_before_incumbent_shrink": True,
        "post_search_stability_confirmation": False,
        "algorithm_a_prior_used": False,
    }


def validate_contract() -> dict[str, Any]:
    contract = outer.alg.load(CONTRACT)
    strategy = mapping(contract.get("strategy"), "B strategy")
    pg_b = mapping(contract.get("postgresql_b_reference"), "PG B reference")
    pg_c = mapping(contract.get("postgresql_outer_reference"), "PG outer reference")
    if (
        contract.get("release_version") != "v104-pg-latest-outer"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or strategy != strategy_contract()
    ):
        raise RuntimeError("Algorithm B v104 PG-latest contract differs")
    for label, reference in (("PG B", pg_b), ("PG outer", pg_c)):
        path = Path(str(reference.get("path") or "")).resolve()
        if (
            not path.is_file()
            or path.is_symlink()
            or outer.alg.sha(path) != str(reference.get("sha256") or "")
        ):
            raise RuntimeError(f"Algorithm B v104 {label} reference differs")
    for name, raw in mapping(
        contract.get("source_bindings"), "source bindings"
    ).items():
        row = mapping(raw, f"source binding {name}")
        path = (ROOT / str(row.get("path") or "")).resolve()
        if (
            not path.is_file()
            or path.is_symlink()
            or outer.alg.sha(path) != str(row.get("sha256") or "")
        ):
            raise RuntimeError(f"Algorithm B v104 source differs: {name}: {path}")
    return contract


def main() -> int:
    validate_contract()
    old_runtime = boundary_v4.runtime
    rebound: list[tuple[ModuleType, Any]] = []
    for module in tuple(sys.modules.values()):
        if (
            isinstance(module, ModuleType)
            and str(getattr(module, "__name__", "")).startswith(
                "scripts.run_mysql_four_scene_algorithm_c_boundary_resume_v"
            )
            and getattr(module, "runtime", None) is old_runtime
        ):
            rebound.append((module, old_runtime))
            module.runtime = outer

    original = {
        "boundary_state": boundary_v4.ORIGINAL_STATE_DOCUMENT,
        "boundary_partial": boundary_v4.ORIGINAL_PARTIAL_VALIDATOR,
        "boundary_wave": boundary_v4.ORIGINAL_WAVE_VALIDATOR,
        "outer_main": outer.main,
        "outer_state": outer._state_document,
        "rank_scene": outer._rank_scene,
        "candidate_rankings": outer.alg.candidate_rankings,
        "merge_candidate_rankings": outer.alg.merge_candidate_rankings,
        "atomic": outer.alg.atomic,
        "optimization": outer.OPTIMIZATION_CONTRACT,
        "physical_measurement": client_measurement._ORIGINAL_MEASURE_BATCH,
    }

    def state_document(**kwargs: Any) -> dict[str, Any]:
        document = original["outer_state"](**kwargs)
        document.update({
            "schema_version": STATE_SCHEMA,
            "algorithm": "B",
            "method": METHOD,
            "algorithm_b_strategy_contract": strategy_contract(),
            "algorithm_b_controller_source_path": str(Path(__file__).resolve()),
            "algorithm_b_controller_source_sha256": outer.alg.sha(
                Path(__file__).resolve()
            ),
            "algorithm_b_inner_source_path": str(Path(inner.__file__).resolve()),
            "algorithm_b_inner_source_sha256": outer.alg.sha(Path(inner.__file__)),
        })
        return document

    def atomic(path: Path, value: Mapping[str, Any]) -> None:
        document = copy.deepcopy(dict(value))
        if path.name == "run_state.json":
            document.update({
                "schema_version": STATE_SCHEMA,
                "algorithm": "B",
                "method": METHOD,
                "algorithm_b_strategy_contract": strategy_contract(),
            })
        elif path.name == "predicted_rankings.json":
            document.update({
                "schema_version": RANKING_SCHEMA,
                "algorithm": "B",
                "method": METHOD,
                "inner_candidate_generation": inner.search_contract(),
                "outer_runtime": "mysql_algorithm_c_pg_latest_outer_v41",
            })
        elif path.name.endswith(".decision.json"):
            document.update({
                "algorithm": "B",
                "method": METHOD,
                "outer_runtime": "mysql_algorithm_c_pg_latest_outer_v41",
            })
        original["atomic"](path, document)

    outer._rank_scene = inner.rank_scene
    outer.alg.candidate_rankings = inner.candidate_rankings
    outer.alg.merge_candidate_rankings = inner.merge_candidate_rankings
    outer._state_document = state_document
    outer.alg.atomic = atomic
    boundary_v4.ORIGINAL_STATE_DOCUMENT = state_document
    boundary_v4.ORIGINAL_PARTIAL_VALIDATOR = (
        outer._validate_formal_partial_continuation
    )
    boundary_v4.ORIGINAL_WAVE_VALIDATOR = (
        outer._validate_wave_coordination_contract
    )
    outer.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT

    def final_outer_main() -> int:
        # Reassert bindings at the final execution boundary because legacy
        # client-primary wrappers update these module globals immediately
        # before dispatch.
        outer.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
        outer.rejection_cycle = rejection_cycle
        outer.alg.reset = xp_reset
        outer.alg.r0mod.PREFLIGHT_SOURCE = Path(
            external_inventory_v2.__file__
        ).resolve()
        outer._rank_scene = inner.rank_scene
        outer.alg.candidate_rankings = inner.candidate_rankings
        outer.alg.merge_candidate_rankings = inner.merge_candidate_rankings
        outer._state_document = state_document
        outer.alg.atomic = atomic
        client_measurement._ORIGINAL_MEASURE_BATCH = (
            single_scene_measurement.measure_batch
        )
        return int(original["outer_main"]())

    outer.main = final_outer_main
    try:
        return int(predecessor.main())
    finally:
        boundary_v4.ORIGINAL_STATE_DOCUMENT = original["boundary_state"]
        boundary_v4.ORIGINAL_PARTIAL_VALIDATOR = original["boundary_partial"]
        boundary_v4.ORIGINAL_WAVE_VALIDATOR = original["boundary_wave"]
        outer.main = original["outer_main"]
        outer._state_document = original["outer_state"]
        outer._rank_scene = original["rank_scene"]
        outer.alg.candidate_rankings = original["candidate_rankings"]
        outer.alg.merge_candidate_rankings = original["merge_candidate_rankings"]
        outer.alg.atomic = original["atomic"]
        outer.OPTIMIZATION_CONTRACT = original["optimization"]
        client_measurement._ORIGINAL_MEASURE_BATCH = original[
            "physical_measurement"
        ]
        for module, runtime in rebound:
            module.runtime = runtime


if __name__ == "__main__":
    raise SystemExit(main())

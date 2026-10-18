#!/usr/bin/env python3
"""Run fresh client-primary C with the current PG outer-search flow."""

from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v11 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v4 as boundary_v4
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v41 as latest_runtime
from scripts import mysql_outer_rejection_cycle_v2 as rejection_cycle
from scripts import reset_mysql_xp_data_planes_client_primary_v3 as xp_reset
from scripts import mysql_algorithm_c_measurement_client_primary_v1 as client_measurement
from scripts import mysql_single_scene_measurement_v2 as single_scene_measurement
from scripts import mysql_c_external_storage_inventory_only_v2 as external_inventory_v2


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260904_v103/"
    "algorithm_c_pg_latest_outer_contract.json"
)
OPTIMIZATION_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260904_v95/"
    "outer_runtime_optimization.json"
)


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def validate_contract() -> dict[str, Any]:
    alg = latest_runtime.alg
    contract = alg.load(CONTRACT)
    flow = mapping(contract.get("outer_flow"), "outer flow")
    pg_reference = mapping(contract.get("postgresql_reference"), "PG reference")
    boundary = mapping(
        contract.get("algorithm_a_independent_check_boundary"), "A boundary"
    )
    if (
        contract.get("release_version") != "v103-pg-latest-outer"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or flow.get("local_real_replay_limit") != 10
        or flow.get("global_coverage_sample_count") != 256
        or flow.get("global_coverage_real_replay_limit") != 10
        or flow.get("coverage_after_every_exhausted_local_round") is not True
        or flow.get("coverage_precedes_rejection_credit") is not True
        or flow.get("two_rejections_before_incumbent_shrink") is not True
        or flow.get("coverage_domain") != "current_formal_interval_only"
        or flow.get("original_domain_reopened_after_shrink") is not False
        or flow.get("qps_used_for_generation_or_ranking") is not False
        or flow.get("strict_higher_qps_acceptance") is not True
        or flow.get("post_search_stability_confirmation") is not False
        or boundary.get("algorithm_a_role") != "independent_posthoc_check_only"
        or boundary.get("algorithm_a_feedback_to_c") is not False
        or boundary.get("algorithm_a_parameters_used_by_c") is not False
        or boundary.get("algorithm_a_qps_used_by_c") is not False
    ):
        raise RuntimeError("Algorithm C v103 PG-latest outer contract differs")
    pg_source = Path(str(pg_reference.get("path") or "")).resolve()
    if (
        not pg_source.is_file()
        or pg_source.is_symlink()
        or alg.sha(pg_source) != str(pg_reference.get("sha256") or "")
    ):
        raise RuntimeError("Algorithm C v103 PostgreSQL reference differs")
    for name, raw in mapping(
        contract.get("source_bindings"), "source bindings"
    ).items():
        row = mapping(raw, f"source binding {name}")
        path = (ROOT / str(row.get("path") or "")).resolve()
        if (
            not path.is_file()
            or path.is_symlink()
            or alg.sha(path) != str(row.get("sha256") or "")
        ):
            raise RuntimeError(f"Algorithm C v103 source differs: {name}: {path}")
    return contract


def main() -> int:
    validate_contract()
    old_runtime = boundary_v4.runtime
    original_latest_main = latest_runtime.main
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
            module.runtime = latest_runtime
    old = {
        "state": boundary_v4.ORIGINAL_STATE_DOCUMENT,
        "partial": boundary_v4.ORIGINAL_PARTIAL_VALIDATOR,
        "wave": boundary_v4.ORIGINAL_WAVE_VALIDATOR,
        "optimization": latest_runtime.OPTIMIZATION_CONTRACT,
        "physical_measurement": client_measurement._ORIGINAL_MEASURE_BATCH,
    }
    boundary_v4.ORIGINAL_STATE_DOCUMENT = latest_runtime._state_document
    boundary_v4.ORIGINAL_PARTIAL_VALIDATOR = (
        latest_runtime._validate_formal_partial_continuation
    )
    boundary_v4.ORIGINAL_WAVE_VALIDATOR = (
        latest_runtime._validate_wave_coordination_contract
    )
    latest_runtime.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT

    def final_latest_main() -> int:
        # Legacy v34 writes its own optimization path immediately before it
        # calls ``runtime.main``.  Reassert the v103-bound client-primary
        # optimization at the actual execution boundary.
        latest_runtime.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
        latest_runtime.rejection_cycle = rejection_cycle
        latest_runtime.alg.reset = xp_reset
        # Retry the complete, unchanged isolation inventory when Docker's
        # per-container inspect endpoint transiently times out.  No blocker is
        # waived; the preflight succeeds only on a fully checked snapshot.
        latest_runtime.alg.r0mod.PREFLIGHT_SOURCE = Path(
            external_inventory_v2.__file__
        ).resolve()
        # Independent per-scene controllers must submit JOB or TPC-H directly;
        # the legacy producer submitted relational work only when both sibling
        # scenes were present in one controller.
        client_measurement._ORIGINAL_MEASURE_BATCH = (
            single_scene_measurement.measure_batch
        )
        return int(original_latest_main())

    latest_runtime.main = final_latest_main
    try:
        return int(predecessor.main())
    finally:
        boundary_v4.ORIGINAL_STATE_DOCUMENT = old["state"]
        boundary_v4.ORIGINAL_PARTIAL_VALIDATOR = old["partial"]
        boundary_v4.ORIGINAL_WAVE_VALIDATOR = old["wave"]
        latest_runtime.OPTIMIZATION_CONTRACT = old["optimization"]
        client_measurement._ORIGINAL_MEASURE_BATCH = old["physical_measurement"]
        latest_runtime.main = original_latest_main
        for module, runtime in rebound:
            module.runtime = runtime


if __name__ == "__main__":
    raise SystemExit(main())

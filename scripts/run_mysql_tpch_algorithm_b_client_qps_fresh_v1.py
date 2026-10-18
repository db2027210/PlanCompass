#!/usr/bin/env python3
"""Fresh TPC-H Algorithm B using full-suite client-QPS outer progress.

This is a TPC-H-only successor.  The B grid/model remains unchanged.  Its
Top-5 diversity stage spans the complete ranked pool so a narrow model-score
window cannot hide a multi-parameter interaction basin.
"""

from __future__ import annotations

import copy
import math
import os
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_tpch_client_qps_measurement_v1 as client_qps
from scripts import mysql_cross_project_wave_coordination_v24 as gate_v24
from scripts import mysql_outer_rejection_cycle_v2 as rejection_cycle_v2
from scripts import mysql_safe_host_pressure_v1 as safe_pressure
from scripts import mysql_tpch_disk2_two_worker_runtime_authority_v1 as runtime_authority
from scripts import preflight_mysql_four_scene_live_v8 as preflight
from scripts import run_mysql_algorithm_b_four_scene_top4_two_worker_v8 as predecessor
from scripts import run_mysql_algorithm_b_xp_top4_async_v30 as legacy_release


CONTRACT = (
    ROOT / "config/formal/frozen_mysql_tpch_bc_client_qps_20260830_v1"
    / "algorithm_b_tpch_successor_contract.json"
)
FREEZE = (
    ROOT / "config/formal/frozen_mysql_tpch_bc_disk2_two_worker_20260830_v1"
    / "four_scene_effective_resource_successor.json"
)
AUTHORITY_ROOT = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v1115_tpch_bc_disk2_two_worker_live_authority_20260830"
)


def _validate_successor_contract(alg: Any) -> dict[str, Any]:
    value = alg.load(CONTRACT)
    if (
        value.get("schema_version")
        != "mysql_tpch_algorithm_b_client_qps_successor_v1"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("scene") != client_qps.SCENE
        or value.get("qps_metric_contract") != client_qps.contract()
        or value.get("candidate_diversity")
        != "best_model_point_plus_four_maximin_points_from_complete_ranked_pool"
        or value.get("external_algorithm_evidence_used") is not False
    ):
        raise RuntimeError("TPC-H B client-QPS successor contract differs")
    for binding in (value.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("TPC-H B successor binding is not an object")
        path = ROOT / str(binding.get("path") or "")
        if (
            not path.is_file() or path.is_symlink()
            or alg.sha(path) != binding.get("sha256")
        ):
            raise RuntimeError(f"TPC-H B successor binding differs: {path}")
    return value


def install_runtime() -> Any:
    # The v37 predecessor freezes an older hash of the shared C measurement
    # implementation.  This successor validates the current implementation
    # directly in its own contract, so the stale predecessor hash must not
    # reject the new, independently frozen campaign.
    legacy_release.validate_contract = lambda: {"superseded_by": str(CONTRACT)}
    core = predecessor.install_runtime()
    impl = core._impl
    impl["rejection_cycle"] = rejection_cycle_v2
    alg = impl["alg"]
    if client_qps.SCENE not in impl["TOP4_TABLES"]:
        raise RuntimeError("TPC-H Top-4 table is absent")

    original_state_document = impl["state_document"]
    original_configure_runtime = impl["configure_runtime"]
    measurement_module = impl["single_measurement"]
    base_measure_batch = measurement_module.measure_batch
    diversity_module = impl["candidate_diversity"]
    base_select_diverse = diversity_module.select_diverse_points

    measurement_module.measure_batch = client_qps.wrap_measure_batch(
        base_measure_batch, load=alg.load, atomic=alg.atomic,
    )

    def select_complete_pool_diversity(
        ranked: Any, *, ranges: Mapping[str, Any], active: Any,
        top_k: int, negative_count: Any, pool_size: int = 64,
        score_window: float = 0.05,
    ) -> Any:
        del score_window
        return base_select_diverse(
            ranked,
            ranges=ranges,
            active=active,
            top_k=top_k,
            negative_count=negative_count,
            pool_size=pool_size,
            score_window=math.inf,
        )

    diversity_module.select_diverse_points = select_complete_pool_diversity

    def configure_runtime() -> Mapping[str, Any]:
        contract = original_configure_runtime()
        configured_root = Path(
            os.environ.get("MYSQL_TWO_WORKER_AUTHORITY_ROOT", "")
        ).expanduser().resolve()
        if configured_root != AUTHORITY_ROOT.resolve():
            raise RuntimeError("TPC-H B disk2 authority root differs")
        freeze = alg.load(FREEZE)
        target_cpus: set[int] = set()
        for row in freeze["workers"]:
            if row["scene"] != client_qps.SCENE:
                continue
            for field in ("database_cpuset", "client_cpuset"):
                target_cpus.update(alg.r0mod._cpus(str(row[field])))
        safe_pressure.configure_target_cpus(target_cpus)
        preflight.host_pressure = safe_pressure.host_pressure
        alg.FREEZE = FREEZE
        impl["FREEZE"] = FREEZE
        alg.r0mod.FREEZE = FREEZE
        alg.r0mod.FREEZE_SCHEMA = str(freeze["schema_version"])
        alg.r0mod.PREFLIGHT = AUTHORITY_ROOT / "transition_preflight.json"
        alg.r0mod.RUNTIME_PREFLIGHT = alg.r0mod.PREFLIGHT
        alg.r0mod.PREPARED = AUTHORITY_ROOT / "prepared_binding.json"
        alg.r0mod.BENCHMARK_ACCOUNTS = (
            AUTHORITY_ROOT / "benchmark_accounts_binding.json"
        )
        alg.r0mod.dynamic_gate = gate_v24
        impl["reset_v7"]._v6.FREEZE = FREEZE
        impl["reset_v7"]._v6.PREPARED = alg.r0mod.PREPARED
        impl["reset_v7"]._v6.RESET_SAME_FREEZE = True
        impl["no_lease_runtime"].coordination = gate_v24
        alg.r0mod._validate_authority = lambda: runtime_authority.validate(
            freeze_path=FREEZE, authority_root=AUTHORITY_ROOT,
        )
        return contract

    def validate_release(scene: str) -> dict[str, Any]:
        if scene != client_qps.SCENE:
            raise RuntimeError("client-QPS successor is TPC-H-only")
        contract = _validate_successor_contract(alg)
        expected = list(alg.load(
            impl["TOP4_ROOT"] / impl["TOP4_TABLES"][scene]
        )["parameter_space"]["active_params"])
        if (
            contract.get("active_parameters") != expected
            or int(impl["PHYSICAL_REPLAY_PARALLELISM"]) != 2
            or int(impl["FIXED_TOP_K"]) != 5
        ):
            raise RuntimeError("TPC-H B successor runtime shape differs")
        return contract

    def state_document(run_id: str) -> dict[str, Any]:
        document = original_state_document(run_id)
        document.update({
            "controller_source_path": str(Path(__file__).resolve()),
            "controller_source_sha256": alg.sha(Path(__file__).resolve()),
            "tpch_client_qps_successor_contract_path": str(CONTRACT),
            "tpch_client_qps_successor_contract_sha256": alg.sha(CONTRACT),
            "qps_metric_contract": copy.deepcopy(client_qps.contract()),
            "candidate_diversity": (
                "best_model_point_plus_four_maximin_points_from_complete_ranked_pool"
            ),
            "algorithm_a_parameter_or_measurement_read": False,
            "freeze_path": str(FREEZE),
            "freeze_sha256": alg.sha(FREEZE),
            "runtime_preflight_path": str(
                AUTHORITY_ROOT / "transition_preflight.json"
            ),
            "runtime_preflight_sha256": alg.sha(
                AUTHORITY_ROOT / "transition_preflight.json"
            ),
            "prepared_binding_path": str(
                AUTHORITY_ROOT / "prepared_binding.json"
            ),
            "prepared_binding_sha256": alg.sha(
                AUTHORITY_ROOT / "prepared_binding.json"
            ),
            "benchmark_accounts_path": str(
                AUTHORITY_ROOT / "benchmark_accounts_binding.json"
            ),
            "benchmark_accounts_sha256": alg.sha(
                AUTHORITY_ROOT / "benchmark_accounts_binding.json"
            ),
            "dynamic_gate_source_path": str(Path(gate_v24.__file__).resolve()),
            "dynamic_gate_source_sha256": alg.sha(
                Path(gate_v24.__file__).resolve()
            ),
            "host_pressure_source_path": str(
                Path(safe_pressure.__file__).resolve()
            ),
            "host_pressure_source_sha256": alg.sha(
                Path(safe_pressure.__file__).resolve()
            ),
            "target_storage_mounts": ["/disk2"],
            "external_postgresql_storage_mounts": ["/disk1"],
        })
        return document

    impl["validate_release"] = validate_release
    impl["configure_runtime"] = configure_runtime
    impl["state_document"] = state_document
    return core


def main() -> int:
    core = install_runtime()
    return int(core._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())

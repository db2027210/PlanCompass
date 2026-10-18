#!/usr/bin/env python3
"""Run fresh four-scene Top-4 B with client-primary XP QPS semantics."""

from __future__ import annotations

import copy
import os
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_single_scene_measurement_client_primary_v1 as measurement
from scripts import reset_mysql_xp_data_planes_client_primary_v1 as xp_reset
from scripts import run_mysql_algorithm_b_four_scene_top4_two_worker_v17 as predecessor
from scripts import run_mysql_algorithm_b_xp_top4_two_worker_v1 as two_worker_base


HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = ROOT / (
    "config/formal/frozen_mysql_four_scene_two_worker_20260903_v20/"
    "four_scene_effective_resource_successor.json"
)
FREEZE_SCHEMA = "mysql_four_scene_two_worker_client_primary_successor_v20"
CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_b_client_primary_20260903_v67"
CONTRACT = CONTRACT_ROOT / "algorithm_b_top4_contract.json"
COORDINATION = CONTRACT_ROOT / "coordination_contract.json"


def authority_root() -> Path:
    raw = os.environ.get("MYSQL_TWO_WORKER_AUTHORITY_ROOT", "")
    value = Path(raw).expanduser().resolve()
    required = (
        value / "transition_preflight.json",
        value / "prepared_binding.json",
        value / "benchmark_accounts_binding.json",
    )
    if (
        not raw or HISTORY.resolve() not in value.parents or value.is_symlink()
        or any(not path.is_file() or path.is_symlink() for path in required)
    ):
        raise RuntimeError("client-primary eight-worker authority root differs")
    return value


def install_runtime() -> Any:
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.CONTRACT = CONTRACT
    predecessor.COORDINATION = COORDINATION
    original_legacy_validate = two_worker_base.predecessor.validate_contract

    def superseded_legacy_validate() -> dict[str, Any]:
        return {
            "status": "SUPERSEDED_BY_V65_COMPLETE_SOURCE_BINDINGS",
            "runtime_or_candidate_validation_bypassed": False,
        }

    two_worker_base.predecessor.validate_contract = superseded_legacy_validate
    try:
        core = predecessor.install_runtime()
    finally:
        two_worker_base.predecessor.validate_contract = original_legacy_validate
    impl = core._impl
    alg = impl["alg"]
    complete_authority_validator = alg.r0mod._validate_authority
    runtime_globals = core._implementation_main.__globals__
    runtime_globals["single_measurement"] = measurement
    runtime_globals["FREEZE"] = FREEZE
    impl["single_measurement"] = measurement
    impl["FREEZE"] = FREEZE
    original_configure = impl["configure_runtime"]
    original_state_document = impl["state_document"]

    def configure_runtime() -> Mapping[str, Any]:
        contract = original_configure()
        root = authority_root()
        prepared = alg.load(root / "prepared_binding.json")
        rows = {
            str(row["worker_id"]): copy.deepcopy(dict(row))
            for row in prepared.get("workers") or []
        }
        xp_rows = {
            worker_id: row for worker_id, row in rows.items()
            if row.get("scene") in {"hybench_xp_cpu", "hybench_xp_io"}
        }
        if len(rows) != 8 or set(xp_rows) != {
            "xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2",
        }:
            raise RuntimeError("client-primary prepared worker cohort differs")
        xp_reset.configure(
            freeze=FREEZE, prepared=root / "prepared_binding.json",
            xp_workers=xp_rows,
        )
        alg.FREEZE = FREEZE
        alg.reset = xp_reset
        alg.WORKER_IDS = tuple(sorted(rows))
        alg.XP_IDS = tuple(sorted(xp_rows))
        alg.XP_RESET_MAX_WORKERS = 2
        r0 = alg.r0mod
        r0.FREEZE = FREEZE
        r0.FREEZE_SCHEMA = FREEZE_SCHEMA
        r0.PREFLIGHT = root / "transition_preflight.json"
        r0.RUNTIME_PREFLIGHT = r0.PREFLIGHT
        r0.PREPARED = root / "prepared_binding.json"
        r0.BENCHMARK_ACCOUNTS = root / "benchmark_accounts_binding.json"
        r0.AUTHORITY_SCENE_SCOPE = impl["SCENES"][0]
        # The v57 wrapper checks the layout of an obsolete 15-second handoff
        # artifact.  This successor runs the complete v5 authority validator
        # against the new 60-second v20 authority instead.
        r0._validate_authority = complete_authority_validator
        impl["runtime"].alg.reset = xp_reset
        impl["no_lease_runtime"].alg = alg
        return contract

    def state_document(run_id: str) -> dict[str, Any]:
        value = original_state_document(run_id)
        root = authority_root()
        value.update({
            "freeze_path": str(FREEZE),
            "freeze_sha256": alg.sha(FREEZE),
            "runtime_preflight_path": str(root / "transition_preflight.json"),
            "runtime_preflight_sha256": alg.sha(root / "transition_preflight.json"),
            "prepared_binding_path": str(root / "prepared_binding.json"),
            "prepared_binding_sha256": alg.sha(root / "prepared_binding.json"),
            "benchmark_accounts_path": str(root / "benchmark_accounts_binding.json"),
            "benchmark_accounts_sha256": alg.sha(root / "benchmark_accounts_binding.json"),
            "xp_outer_primary_metric": "native_hybench_xp_client_xap_qps",
            "xp_server_questions_qps_role": "audit_only",
            "xp_metric_policy": measurement.POLICY_ID,
            "all_four_scenes_started_in_same_formal_launch": True,
            "physical_workers_per_scene": 2,
            "maximum_concurrent_mysql_workers": 8,
        })
        return value

    impl["configure_runtime"] = configure_runtime
    impl["state_document"] = state_document
    return core


def main() -> int:
    return int(install_runtime()._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())

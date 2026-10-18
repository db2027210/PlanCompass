#!/usr/bin/env python3
"""Validate the live disk2 TPC-H pair without a scheduler lease."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Mapping

from scripts import mysql_cross_project_wave_coordination_v24 as gate_v24
from scripts import mysql_safe_host_pressure_v1 as safe_pressure
from scripts import preflight_mysql_four_scene_live_v8 as preflight
from scripts import refresh_mysql_tpch_disk2_two_worker_authority_v1 as sealed
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg


SCENE = "tpch_sf4_memory"
EXPECTED_FREEZE_SCHEMA = "mysql_tpch_bc_disk2_two_worker_successor_v1"


def _cpus(value: str) -> set[int]:
    result: set[int] = set()
    for token in value.split(","):
        lower, separator, upper = token.strip().partition("-")
        result.update(
            range(int(lower), int(upper) + 1) if separator else [int(lower)]
        )
    return result


def validate(
    *, freeze_path: Path, authority_root: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    freeze_path = freeze_path.resolve()
    authority_root = authority_root.resolve()
    preflight_path = authority_root / "transition_preflight.json"
    prepared_path = authority_root / "prepared_binding.json"
    accounts_path = authority_root / "benchmark_accounts_binding.json"
    freeze = alg.load(freeze_path)
    rolling = alg.load(preflight_path)
    prepared = alg.load(prepared_path)
    accounts = alg.load(accounts_path)
    frozen = {str(row["worker_id"]): dict(row) for row in freeze["workers"]}
    prepared_rows = {
        str(row["worker_id"]): dict(row) for row in prepared["workers"]
    }
    account_rows = {
        str(row["worker_id"]): dict(row) for row in accounts["workers"]
    }
    target_ids = {
        worker_id for worker_id, row in frozen.items()
        if row["scene"] == SCENE
    }
    gate_document = rolling.get("rolling_dynamic_noninterference_gate") or {}
    if (
        freeze.get("schema_version") != EXPECTED_FREEZE_SCHEMA
        or freeze.get("formal_scope") != "mysql_only"
        or freeze.get("cross_engine_comparison_prohibited") is not True
        or len(frozen) != 8
        or target_ids != {"tpch_w1", "tpch_w2"}
        or rolling.get("status") != "PASS"
        or rolling.get("formal_launch_authorized") is not True
        or float(rolling.get("observed_seconds") or 0.0) < 60.0
        or rolling.get("freeze_sha256") != alg.sha(freeze_path)
        or any((rolling.get("accepted_rounds") or {}).values())
        or gate_document.get("status") != "PASS"
        or prepared.get("status") != "PREPARED"
        or prepared.get("freeze_sha256") != alg.sha(freeze_path)
        or prepared.get("preflight_sha256") != alg.sha(preflight_path)
        or prepared.get("workload_started") is not False
        or prepared.get("fresh_r0_complete") is not False
        or prepared.get("formal_outer_started") is not False
        or accounts.get("status") != "PASS"
        or accounts.get("freeze_sha256") != alg.sha(freeze_path)
        or accounts.get("prepared_binding_sha256") != alg.sha(prepared_path)
        or accounts.get("workload_started") is not False
        or accounts.get("passwords_emitted") is not False
        or set(prepared_rows) != set(frozen)
        or set(account_rows) != set(frozen)
    ):
        raise RuntimeError("TPC-H disk2 two-worker sealed lineage differs")

    target_cpus = {
        cpu
        for worker_id in target_ids
        for field in ("database_cpuset", "client_cpuset")
        for cpu in _cpus(str(frozen[worker_id][field]))
    }
    safe_pressure.configure_target_cpus(target_cpus)
    preflight.host_pressure = safe_pressure.host_pressure
    live_rows: dict[str, dict[str, Any]] = {}
    for worker_id in sorted(target_ids):
        live = sealed.implementation["read_live_worker"](
            freeze, frozen[worker_id]
        )
        if (
            live.get("container_id")
            != prepared_rows[worker_id].get("container_id")
            or live.get("external_database") != "tpch"
            or live.get("external_current_user")
            != account_rows[worker_id].get("external_current_user")
        ):
            raise RuntimeError(f"{worker_id} current authority differs")
        live_rows[worker_id] = {
            **copy.deepcopy(frozen[worker_id]),
            **copy.deepcopy(prepared_rows[worker_id]),
            **copy.deepcopy(account_rows[worker_id]),
            **copy.deepcopy(live),
        }

    gate = gate_v24.rolling_quiet_gate(
        run_id=f"mysql-tpch-disk2-runtime-authority-{os.getpid()}",
        required_seconds=15.0,
        maximum_wait_seconds=90.0,
        maximum_load1=512.0,
        target_cpus=target_cpus,
    )
    if gate.get("status") != "PASS":
        raise RuntimeError(
            f"TPC-H disk2 runtime non-interference gate failed: "
            f"{gate.get('errors')}"
        )
    point_gate = {
        "schema_version": "mysql_tpch_disk2_runtime_point_gate_v1",
        "passed": True,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "target_worker_ids": sorted(target_ids),
        "target_cpus": sorted(target_cpus),
        "target_storage_mounts": ["/disk2"],
        "external_postgresql_storage_mounts": ["/disk1"],
        "rolling_predecessor_path": str(preflight_path),
        "rolling_predecessor_sha256": alg.sha(preflight_path),
        "current_dynamic_gate": gate,
        "lease_mode": "none",
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
    }
    return freeze, live_rows, point_gate


__all__ = ["EXPECTED_FREEZE_SCHEMA", "SCENE", "validate"]

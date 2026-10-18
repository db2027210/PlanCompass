from __future__ import annotations

import json
from pathlib import Path

from build_hybench_xp_algorithm_b_optimizer_config import build_xp_config


def _fixture_root(tmp_path: Path) -> Path:
    freeze = "frozen_mysql_workloads_and_parameters_20260723_v3"
    registry = tmp_path / f"config/formal/{freeze}/plan_workload_registries/hybench_xp.production_binding_capture_contract_v1.json"
    table = tmp_path / "config/formal/frozen_mysql_operator_physics_experience_tables_20260724_v1/T.hybench_xp_io.operator_physics_v1.json"
    r0 = tmp_path / "config/formal/frozen_mysql_r0_calibration_20260724_v1/hybench_xp_io.r0_calibration_v1.json"
    factory = tmp_path / "mysql_algorithm_b_production_services.py"
    for path in (registry, table, r0, factory):
        path.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps({
        "benchmark": "hybench_xp", "freeze_id": freeze,
        "execution_authorized": False,
        "strict_plan_decision": {"strict_cluster_usable_now": False},
    }))
    import hashlib
    registry_sha = hashlib.sha256(registry.read_bytes()).hexdigest()
    active = ["io_block_read_cost", "memory_block_read_cost", "row_evaluate_cost", "key_compare_cost", "disk_temptable_create_cost", "disk_temptable_row_cost", "memory_temptable_row_cost"]
    table.write_text(json.dumps({
        "scenario": "hybench_xp_io", "benchmark": "hybench_xp", "workload_freeze_id": freeze,
        "workload_registry_sha256": registry_sha,
        "parameter_space": {"active_params": active, "ranges": {name: {"lower": 0.01, "upper": 1.0} for name in active}},
        "table": {name: {"cpu": 0.1, "memory": 0.1, "io": -0.1} for name in active},
    }))
    r0.write_text(json.dumps({
        "scenario": "hybench_xp_io", "benchmark": "hybench_xp", "passed": True,
        "correctness": {"passed": True}, "resource_utilization": {"cpu": 0.45, "memory": 0.49, "io": 0.94},
        "stability": {"stable": False},
    }))
    factory.write_text("# XP factory fixture\n")
    return tmp_path


def test_xp_config_is_fixed_top5_and_fail_closed(tmp_path: Path) -> None:
    config = build_xp_config(_fixture_root(tmp_path))
    assert config["formal_outer_protocol"]["formal_search_initial_replay_top_k"] == 5
    assert config["formal_outer_protocol"]["formal_search_max_replay_top_k"] == 5
    assert config["formal_outer_protocol"]["plan_basin_escape"]["maximum_rejected_batches"] == 5
    assert config["target_resource"] == "io"
    assert config["workload_registry"]["execution_authorized"] is False
    assert config["execution_authorized"] is False
    assert config["host02_job_xp_mutex"]["scenario"] == "hybench_xp_io"
    assert "qps" not in json.dumps(config["optimizer"]).lower()
    assert "qps" not in json.dumps(config["scoring"]).lower()
    assert config["evidence"]["raw_explain_required"] is True


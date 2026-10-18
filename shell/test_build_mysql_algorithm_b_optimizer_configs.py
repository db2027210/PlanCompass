from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from build_mysql_algorithm_b_optimizer_configs import FREEZE_ID, build_job_config
from mysql_parameter_optimizer import validate_config


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _fixture_root(tmp_path: Path) -> Path:
    registry_path = tmp_path / f"config/formal/{FREEZE_ID}/plan_workload_registries/job.mysql_plan_workload_registry_v1.json"
    registry = {
        "benchmark": "job",
        "freeze_id": FREEZE_ID,
        "immutable": True,
        "queries": [{"query_id": "job_1a", "dynamic": False, "representative_bindings": []}],
    }
    _write(registry_path, registry)
    registry_sha = hashlib.sha256(registry_path.read_bytes()).hexdigest()
    _write(
        registry_path.with_name("job.trained_estimator_feature_source.json"),
        {
            "benchmark_id": "job",
            "registry_query_ids": ["job_1a"],
            "require_strict_plan_registry": True,
            "workload_freeze_id": FREEZE_ID,
            "workload_registry_path": str(registry_path),
            "workload_registry_sha256": registry_sha,
            "queries": [{"query_id": "job_1a", "sql": "SELECT 1", "weight": 1.0}],
        },
    )
    active = [
        "io_block_read_cost",
        "memory_block_read_cost",
        "row_evaluate_cost",
        "memory_temptable_row_cost",
    ]
    _write(
        tmp_path / "config/formal/frozen_mysql_operator_physics_experience_tables_20260724_v1/T.job_memory.operator_physics_v1.json",
        {
            "scenario": "job_memory",
            "benchmark": "job",
            "workload_freeze_id": FREEZE_ID,
            "workload_registry_sha256": registry_sha,
            "parameter_space": {
                "active_params": active,
                "ranges": {
                    "io_block_read_cost": {"lower": 0.1, "upper": 10.0},
                    "memory_block_read_cost": {"lower": 0.025, "upper": 2.5},
                    "row_evaluate_cost": {"lower": 0.01, "upper": 1.0},
                    "memory_temptable_row_cost": {"lower": 0.01, "upper": 1.0},
                },
            },
            "table": {name: {"cpu": 0.1, "memory": -0.1, "io": 0.1} for name in active},
        },
    )
    _write(
        tmp_path / "config/formal/frozen_mysql_r0_calibration_20260724_v1/job_memory.r0_calibration_v1.json",
        {
            "scenario": "job_memory",
            "benchmark": "job",
            "passed": True,
            "correctness": {"passed": True},
            "resource_utilization": {"cpu": 0.4, "memory": 0.9, "io": 0.4},
            "stability": {"stable": False},
        },
    )
    return tmp_path


def test_job_builder_emits_valid_fixed_top5_qps_outer_only_config(tmp_path: Path) -> None:
    config = build_job_config(_fixture_root(tmp_path))
    validate_config(config)
    assert config["formal_outer_protocol"]["formal_search_max_replay_top_k"] == 5
    assert config["formal_outer_protocol"]["plan_basin_escape"]["maximum_rejected_batches"] == 5
    assert config["host02_job_xp_mutex"]["required"] is True
    assert config["host02_job_xp_mutex"]["scenario"] == "job_memory"
    assert config["estimator"]["uncalibrated_transition_log_step"] == 0.05
    assert config["evidence"]["formal_incumbent_requires_fresh_measurement"] is True
    assert config["evidence"]["historical_r0_stable"] is False
    assert "qps" not in json.dumps(config["optimizer"]).lower()
    assert "qps" not in json.dumps(config["scoring"]).lower()


def test_job_builder_rejects_registry_tampering(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    registry_path = root / f"config/formal/{FREEZE_ID}/plan_workload_registries/job.mysql_plan_workload_registry_v1.json"
    registry_path.write_text(registry_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        build_job_config(root)

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from typing import Any


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "shell"
    / "manual_mysql_sgd_lane_controller.py"
)
SPEC = importlib.util.spec_from_file_location("manual_mysql_sgd_lane_controller", MODULE_PATH)
assert SPEC and SPEC.loader
controller = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(controller)


def pointer(root: Path, name: str) -> dict[str, str]:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"evidence:{name}\n", encoding="utf-8")
    return {
        "path": str(path.relative_to(root)),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def valid_request(root: Path) -> dict[str, Any]:
    evidence = pointer(root, "evidence/gate.json")
    envelope = {
        "cpu_quota_cores": 4,
        "cpuset_cpus": "0-3",
        "cpuset_mems": "0",
        "memory_bytes": 8_589_934_592,
        "memory_swap_bytes": 0,
        "io_read_bps": 104_857_600,
        "io_write_bps": 104_857_600,
        "pids_max": 4096,
        "cpu_isolation_degree": "exclusive_cpuset",
        "numa_isolation_degree": "fixed_memnode",
    }
    return {
        "schema_version": "manual_mysql_sgd_launch_request_v1",
        "scenario": "tpcc_cpu",
        "method": "experience_table_all_subset_sgd",
        "formal_scope": "mysql_only",
        "ready": True,
        "preflight_status": "PASS",
        "argv": [
            "python",
            "shell/formal_mysql_sgd_campaign.py",
            "--method",
            "experience_table",
        ],
        "bottleneck_construction": {
            "status": "PASS",
            "target_resource": "cpu",
            "target_utilization": 0.90,
            "non_target_utilization": {"memory": 0.40, "io": 0.35},
            "attempts": [{"status": "PASS"}],
            "evidence": evidence,
        },
        "pre_tuning_snapshot": {
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "captured_at": "2026-07-23T23:59:00+08:00",
            "host_id": "host04",
            "hostname": "host04",
            "device": "/dev/nvme3n1",
            "configured_resource_envelope": envelope,
            "effective_resource_readback": {
                "values": dict(envelope),
                "evidence": evidence,
            },
            "config_json": evidence,
            "process_inventory": {
                "complete": True,
                "database_and_benchmark_processes": [],
                "evidence": evidence,
            },
            "dynamic_interference": {
                "passed": True,
                "window_seconds": 180,
                "evidence": evidence,
            },
        },
    }


def test_valid_request_requires_and_accepts_full_pre_tuning_gate(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(controller, "ROOT", tmp_path)
    request = valid_request(tmp_path)
    argv, errors = controller.validate_launch_request(request, "tpcc_cpu")
    assert argv == request["argv"]
    assert errors == []


def test_failed_bottleneck_attempt_requires_concrete_adjustment(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(controller, "ROOT", tmp_path)
    request = valid_request(tmp_path)
    request["bottleneck_construction"]["attempts"] = [
        {"status": "FAIL"},
        {"status": "PASS"},
    ]
    argv, errors = controller.validate_launch_request(request, "tpcc_cpu")
    assert argv is None
    assert any("lacks a concrete resource/config adjustment" in error for error in errors)


def test_effective_resource_readback_must_equal_configured_envelope(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(controller, "ROOT", tmp_path)
    request = valid_request(tmp_path)
    request["pre_tuning_snapshot"]["effective_resource_readback"]["values"][
        "cpu_quota_cores"
    ] = 3
    argv, errors = controller.validate_launch_request(request, "tpcc_cpu")
    assert argv is None
    assert "configured resource envelope differs from effective read-back" in errors


def test_missing_bottleneck_proof_is_fail_closed(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(controller, "ROOT", tmp_path)
    request = valid_request(tmp_path)
    del request["bottleneck_construction"]
    argv, errors = controller.validate_launch_request(request, "tpcc_cpu")
    assert argv is None
    assert "bottleneck_construction is missing" in errors


def test_auto_launch_requires_global_readiness() -> None:
    facts = {
        "optimizer_config": True,
        "experience_table_t": True,
        "production_factory": True,
        "bottleneck_construction": True,
        "pre_tuning_snapshot": True,
        "static_isolation": True,
        "dynamic_interference": True,
        "r0": True,
        "exclusive_lease": True,
        "global_readiness": False,
    }
    assert controller.phase(facts, None) == "WAITING_GLOBAL_READINESS"
    facts["global_readiness"] = True
    assert controller.phase(facts, None) == "READY_TO_AUTO_LAUNCH"


def test_auto_launch_requires_registered_formal_service_factory(
    tmp_path: Path, monkeypatch
) -> None:
    config = tmp_path / "optimizer.json"
    entry = tmp_path / "formal_mysql_sgd_campaign.py"
    manifest = tmp_path / "readiness.json"
    for path in (config, entry, manifest):
        path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(controller, "ROOT", tmp_path)
    monkeypatch.setattr(controller, "PRODUCTION_ENTRY", entry)
    monkeypatch.setattr(controller, "READINESS_MANIFEST", manifest)
    monkeypatch.setitem(
        controller.AUTO_LAUNCH_REGISTRY,
        "tpcc_cpu",
        {
            "config": config,
            "journal_root": tmp_path / "journals",
            "service_factory": None,
        },
    )
    argv, errors = controller.auto_argv("tpcc_cpu")
    assert argv is None
    assert errors == [
        "formal production service factory is not registered: tpcc_cpu"
    ]


def test_controllers_select_algorithm_c_optimizer_configs() -> None:
    assert controller.AUTO_LAUNCH_REGISTRY["job_memory"]["config"].name == (
        "job_memory.algorithm_c.optimizer.json"
    )
    assert all(
        registration["config"].name == f"{scenario}.algorithm_c.optimizer.json"
        and registration["service_factory"]
        == "mysql_algorithm_c_production_services:build"
        for scenario, registration in controller.AUTO_LAUNCH_REGISTRY.items()
    )

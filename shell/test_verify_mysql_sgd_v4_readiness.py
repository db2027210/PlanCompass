#!/usr/bin/env python3
"""Pure filesystem tests for the MySQL-only v4 readiness verifier."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile

from verify_mysql_sgd_v4_readiness import DEFAULT_MANIFEST, REPO_ROOT, verify_document


def _pointer(path: Path) -> dict[str, str]:
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _json_artifact(root: Path, name: str, payload: object) -> dict[str, str]:
    path = root / f"{name}.json"
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_name(path.name + ".sha256").write_text(
        f"{digest}  {path.name}\n", encoding="ascii"
    )
    return _pointer(path)


def _text_artifact(root: Path, name: str, payload: str = "verified\n") -> dict[str, str]:
    path = root / name
    path.write_text(payload, encoding="utf-8")
    return _pointer(path)


def _current() -> dict:
    return json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))


def _ready_document(root: Path) -> dict:
    document = copy.deepcopy(_current())
    document["status"] = "READY"
    document["launch_authorized"] = True
    for pointer in document["global_requirements"]["method_bundle"]["registered_sources"]:
        source_path = REPO_ROOT / pointer["path"]
        pointer["sha256"] = hashlib.sha256(source_path.read_bytes()).hexdigest()
    limits = document["policy"]["co_resident_interference_limits"]
    envelope = {
        "cpu_quota_cores": 4,
        "memory_bytes": 8589934592,
        "memory_swap_bytes": 8589934592,
        "io_read_bps": 1073741824,
        "io_write_bps": 1073741824,
        "pids_max": 4096,
        "cpu_isolation_degree": 4,
        "numa_isolation_degree": 1,
    }
    for index, (name, scenario) in enumerate(document["scenarios"].items(), 1):
        environment = scenario["environment"]
        if not environment["allocated"]:
            environment.update({
                "allocated": True,
                "host_id": "05",
                "host_address": "192.0.2.62",
                "device": "/dev/nvme9n1",
                "mount": "/mnt/mysql_tpcc",
                "current_state": "allocated_and_verified",
            })
        environment["deployment_evidence"] = _json_artifact(
            root, f"{index}_deployment", {"scenario": name, "passed": True}
        )
        scenario["template"] = {
            "ready": True,
            "manifest": _json_artifact(
                root,
                f"{index}_template",
                {"scenario": name, "template_id": f"template_{name}_v1", "passed": True},
            ),
            "deployment_verification": environment["deployment_evidence"],
        }
        mysql_freeze = f"mysql_only_envelope_{name}_v1"
        source_mysql_readback = _json_artifact(
            root, f"{index}_source_mysql_readback",
            {"scenario": name, "engine": "mysql", "normalized_envelope": envelope},
        )
        mysql_readback = _json_artifact(
            root, f"{index}_mysql_readback",
            {
                "scenario": name,
                "engine": "mysql",
                "formal_scope_name": "mysql_only",
                "freeze_id": mysql_freeze,
                "postgresql_counterpart_required": False,
                "cross_engine_envelope_equality_required": False,
                "normalized_envelope": envelope,
                "source_mysql_readback": source_mysql_readback,
            },
        )
        scenario["mysql_envelope"] = {
            "freeze_id": mysql_freeze,
            "readback": mysql_readback,
            "normalized": copy.deepcopy(envelope),
        }
        scenario.pop("postgresql_envelope", None)
        scenario["static_isolation"] = {
            "passed": True,
            "evidence": _json_artifact(
                root,
                f"{index}_static",
                {
                    "scenario": name,
                    "passed": True,
                    "cpu_nonoverlap": True,
                    "numa_nonoverlap": True,
                    "memory_swap_nonoverlap": True,
                    "physical_disk_nonoverlap": True,
                    "host_admission_passed": True,
                    "process_inventory_complete": True,
                    "target_runtime_currently_stopped_or_isolated": True,
                    "same_host_scenario_mutex_enforced": True,
                    "no_active_benchmark_driver_pressure": True,
                },
            ),
        }
        scenario["dynamic_interference"] = {
            "passed": True,
            "evidence": _json_artifact(
                root,
                f"{index}_dynamic",
                {
                    "scenario": name,
                    "passed": True,
                    "latest_window_seconds": 180,
                    "latest_window_count": 6,
                    "max_continuous_seconds": 600,
                    "sample_coverage": 0.99,
                    "limits": limits,
                    "mode": "co_resident_active",
                    "co_resident_process_inventory_complete": True,
                    "bidirectional_measurement_passed": True,
                    "observed_max": {
                        "throughput_drop_ratio": 0.01,
                        "p95_increase_ratio": 0.02,
                        "p99_increase_ratio": 0.02,
                        "target_resource_shift_percentage_points": 1.0,
                    },
                },
            ),
        }
        target = scenario["bottleneck"]
        resource_utilization = {resource: 0.40 for resource in ("cpu", "memory", "io")}
        resource_utilization[target] = 0.90
        scenario["r0_calibration"] = {
            "passed": True,
            "evidence": _json_artifact(
                root,
                f"{index}_r0",
                {
                    "scenario": name,
                    "passed": True,
                    "latest_window_seconds": 180,
                    "latest_window_count": 6,
                    "sample_coverage": 0.99,
                    "resource_utilization": resource_utilization,
                },
            ),
        }
        table_version = f"T_{name}_v1"
        point_evidence = _json_artifact(
            root, f"{index}_t_point", {"database": "mysql", "scenario": name, "complete": True}
        )
        scenario["experience_table"] = {
            "version": table_version,
            "artifact": _json_artifact(
                root,
                f"{index}_table",
                {
                    "schema_version": "mysql_experience_table_t_v1",
                    "database": "mysql",
                    "scenario": name,
                    "benchmark": scenario["benchmark"],
                    "benchmark_version": "benchmark-v1",
                    "version": table_version,
                    "workload_freeze_id": "workload-v1",
                    "workload_registry_sha256": "a" * 64,
                    "estimator": {"id": "mysql-estimator", "version": "v1", "sha256": "b" * 64},
                    "parameter_space": {
                        "version": "space-v1", "sha256": "c" * 64,
                        "active_params": ["row_evaluate_cost"],
                        "ranges": {"row_evaluate_cost": {"lower": 0.01, "upper": 1.0}},
                    },
                    "resource_columns": ["cpu", "memory", "io"],
                    "table": {"row_evaluate_cost": {"cpu": -0.25, "memory": 0.10, "io": 0.0}},
                    "effect_evidence": {
                        "row_evaluate_cost": {
                            resource: {
                                "direction": "decrease" if value < 0 else "increase" if value > 0 else "neutral",
                                "signed_effect_full_range": value,
                                "observed_median_effect_full_range": value,
                                "magnitude_full_range": abs(value),
                                "confidence": {"level": "MODERATE", "decision": "SUPPORTED"},
                            }
                            for resource, value in {"cpu": -0.25, "memory": 0.10, "io": 0.0}.items()
                        }
                    },
                    "calibration": {"method": "paired_two_sided_formal_oat_log_slope_v1", "source": "real_measurement", "real_measurement": True},
                    "provenance": {
                        "request_sha256": "d" * 64,
                        "evidence_class": "mysql_complete_formal_two_sided_oat",
                        "source": "real_measurement", "real_measurement": True,
                        "cross_engine_prior_used": False,
                        "point_artifacts": [point_evidence],
                    },
                },
            ),
        }
        lease_id = f"lease_{name}_v1"
        scenario["lane_lease"] = {
            "lease_id": lease_id,
            "exclusive": True,
            "evidence": _json_artifact(
                root,
                f"{index}_lease",
                {
                    "scenario": name,
                    "lease_id": lease_id,
                    "exclusive": True,
                    "active": True,
                    "method": "experience_table_all_subset_sgd",
                },
            ),
        }

    globals_raw = document["global_requirements"]
    globals_raw["method_bundle"].update({
        "ready": True,
        "method_manifest": _json_artifact(
            root,
            "method_manifest",
            {
                "draft_id": document["draft_id"],
                "method": "experience_table_all_subset_sgd",
                "formal_scope": "mysql_only",
                "cross_engine_comparison_authorized": False,
            },
        ),
        "formal_config": _json_artifact(root, "formal_config", {"schema_version": 1}),
    })
    capabilities = {key: True for key in globals_raw["session_adapter"]["capabilities"]}
    globals_raw["session_adapter"] = {
        "ready": True,
        "evidence": _json_artifact(
            root, "session_adapter", {"passed": True, "capabilities": capabilities}
        ),
        "capabilities": capabilities,
    }
    logging_fields = {
        "section23_complete": True,
        "section26_complete": True,
        "trace_sha256_complete": True,
        "recovery_journal_complete": True,
        "failure_injection_tested": True,
        "resume_tested": True,
    }
    globals_raw["logging_contract"] = {
        "ready": True,
        "evidence": _json_artifact(root, "logging", {"passed": True, **logging_fields}),
        **logging_fields,
    }
    mutex_fields = {
        "job_xp_pressure_mutually_exclusive": True,
        "zero_pressure_handoff_verified": True,
    }
    mutex_sources = {
        name: _pointer(REPO_ROOT / name)
        for name in (
            "host02_job_xp_mutex.py",
            "formal_mysql_sgd_campaign.py",
            "shell/test_host02_job_xp_mutex.py",
            "shell/test_mysql_formal_outer_campaign.py",
            "shell/verify_mysql_sgd_v2_readiness.py",
            "shell/test_verify_mysql_sgd_v2_readiness.py",
        )
    }
    mutex_tests = {
        name: True
        for name in (
            "preflight_zero_pressure_rejection",
            "atomic_handoff_journal",
            "second_holder_rejected",
            "crashed_owner_kernel_release",
            "stale_state_audited",
            "both_launch_paths_same_lock",
            "full_scope_through_recovery_checksum",
            "journal_checksum_chain",
            "aborted_scope_zero_pressure_before_unlock",
        )
    }
    globals_raw["host02_job_xp_mutex"] = {
        "ready": True,
        "evidence": _json_artifact(
            root,
            "mutex",
            {
                "passed": True,
                **mutex_fields,
                "implementation": "posix_flock_exclusive_nonblocking",
                "lock_path": "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/host02_control/job_xp_pressure.lock",
                "journal_path": "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/host02_control/job_xp_lease_journal.jsonl",
                "pressure_scope": "before_R0_through_recovery_and_checksum",
                "preflight_zero_pressure_required": True,
                "postrun_zero_pressure_required": True,
                "atomic_handoff_journal": True,
                "journal_sha256_chain": True,
                "launch_paths": {
                    "job_memory": "formal_mysql_sgd_campaign.run_production_campaign",
                    "hybench_xp_io": "formal_mysql_sgd_campaign.run_production_campaign",
                },
                "sources": mutex_sources,
                "tests": mutex_tests,
            },
        ),
        **mutex_fields,
    }
    globals_raw["bundle_publication"] = {
        "ready": True,
        "checksums": _text_artifact(root, "checksums.sha256"),
        "independent_audit": _json_artifact(
            root, "audit",
            {
                "passed": True,
                "formal_scope": "mysql_only",
                "cross_engine_comparison_authorized": False,
            },
        ),
        "ready_file": _json_artifact(
            root,
            "ready",
            {
                "draft_id": document["draft_id"],
                "status": "READY",
                "launch_authorized": True,
                "formal_scope": "mysql_only",
                "cross_engine_comparison_authorized": False,
            },
        ),
        "ready_file_authorizes_launch": True,
    }
    return document


def test_current_manifest_is_not_ready_with_all_requirement_groups_reported() -> None:
    checks = verify_document(_current(), REPO_ROOT)
    assert not all(check.passed for check in checks)
    reported_groups = {check.requirement for check in checks}
    assert {f"R{index:02d}" for index in range(1, 12)} <= reported_groups
    assert any(
        check.item == "mysql_only_scope_policy" and check.passed
        for check in checks
    )
    assert any(check.item == "tpcc_cpu.environment_allocation" and check.passed for check in checks)
    assert any(check.item == "tpcc_cpu.template_ready" and check.passed for check in checks)
    assert any(check.item == "hybench_xp_io.template_ready" and check.passed for check in checks)


def test_placeholder_freeze_id_cannot_be_ready() -> None:
    with tempfile.TemporaryDirectory() as raw:
        document = _ready_document(Path(raw))
        document["scenarios"]["job_memory"]["mysql_envelope"]["freeze_id"] = "TBD"
        checks = verify_document(document, REPO_ROOT)
        target = next(check for check in checks if check.item == "job_memory.mysql_freeze_id")
        assert not target.passed
        assert "TBD" in target.reason
        assert not all(check.passed for check in checks)


def test_complete_synthetic_evidence_is_ready() -> None:
    with tempfile.TemporaryDirectory() as raw:
        checks = verify_document(_ready_document(Path(raw)), REPO_ROOT)
        failures = [check for check in checks if not check.passed]
        assert failures == [], "\n".join(f"{item.requirement} {item.item}: {item.reason}" for item in failures)


def test_formal_postgresql_envelope_is_forbidden_in_mysql_only_scope() -> None:
    with tempfile.TemporaryDirectory() as raw:
        document = _ready_document(Path(raw))
        scenario = document["scenarios"]["job_memory"]
        scenario["postgresql_envelope"] = {
            "freeze_id": "irrelevant_pg_profile",
            "normalized": copy.deepcopy(scenario["mysql_envelope"]["normalized"]),
            "readback": None,
        }
        checks = verify_document(document, REPO_ROOT)
        target = next(
            check for check in checks if check.item == "job_memory.postgresql_envelope_absent"
        )
        assert not target.passed
        assert not all(check.passed for check in checks)


def test_mysql_only_dynamic_gate_checks_observed_values() -> None:
    with tempfile.TemporaryDirectory() as raw:
        document = _ready_document(Path(raw))
        dynamic = document["scenarios"]["job_memory"]["dynamic_interference"]
        path = Path(dynamic["evidence"]["path"])
        payload = json.loads(path.read_text())
        payload["observed_max"]["throughput_drop_ratio"] = 0.051
        path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        dynamic["evidence"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        checks = verify_document(document, REPO_ROOT)
        target = next(
            check for check in checks
            if check.item == "job_memory.dynamic_interference_semantics"
        )
        assert not target.passed


def test_idle_co_resident_service_uses_explicit_not_applicable_metrics() -> None:
    with tempfile.TemporaryDirectory() as raw:
        document = _ready_document(Path(raw))
        dynamic = document["scenarios"]["job_memory"]["dynamic_interference"]
        path = Path(dynamic["evidence"]["path"])
        payload = json.loads(path.read_text())
        payload.pop("observed_max")
        payload.pop("bidirectional_measurement_passed")
        payload.update({
            "mode": "exclusive_host_no_co_resident_pressure",
            "host_admission_no_unregistered_pressure": True,
            "co_resident_background_health_unchanged": True,
            "mysql_resource_stability_passed": True,
            "not_applicable_metrics": [
                "throughput_drop_ratio", "p95_increase_ratio",
                "p99_increase_ratio", "target_resource_shift_percentage_points",
            ],
            "not_applicable_reason": "no_co_resident_workload_or_latency_stream",
        })
        path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        dynamic["evidence"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        failures = [check for check in verify_document(document, REPO_ROOT) if not check.passed]
        assert failures == [], "\n".join(
            f"{item.requirement} {item.item}: {item.reason}" for item in failures
        )


def test_idle_co_resident_service_requires_background_health() -> None:
    with tempfile.TemporaryDirectory() as raw:
        document = _ready_document(Path(raw))
        dynamic = document["scenarios"]["job_memory"]["dynamic_interference"]
        path = Path(dynamic["evidence"]["path"])
        payload = json.loads(path.read_text())
        payload.update({
            "mode": "exclusive_host_no_co_resident_pressure",
            "host_admission_no_unregistered_pressure": True,
            "co_resident_background_health_unchanged": False,
            "mysql_resource_stability_passed": True,
            "not_applicable_metrics": [
                "throughput_drop_ratio", "p95_increase_ratio",
                "p99_increase_ratio", "target_resource_shift_percentage_points",
            ],
            "not_applicable_reason": "no_co_resident_workload_or_latency_stream",
        })
        path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        dynamic["evidence"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        checks = verify_document(document, REPO_ROOT)
        assert not next(
            check for check in checks
            if check.item == "job_memory.dynamic_interference_semantics"
        ).passed


def test_mysql_only_pg_equality_policy_cannot_be_reenabled() -> None:
    with tempfile.TemporaryDirectory() as raw:
        document = _ready_document(Path(raw))
        document["policy"]["cross_engine_envelope_equality_required"] = True
        checks = verify_document(document, REPO_ROOT)
        assert not next(
            check for check in checks if check.item == "mysql_only_scope_policy"
        ).passed


def test_complete_estimator_backed_t_provenance_is_ready() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        document = _ready_document(root)
        for index, scenario in enumerate(document["scenarios"].values(), 1):
            pointer = scenario["experience_table"]["artifact"]
            path = Path(pointer["path"])
            payload = json.loads(path.read_text())
            payload["sql_registry"] = {"id": "sql-v1", "sha256": "f" * 64}
            payload["estimator"]["model_artifact_sha256"] = {
                "cpu": "1" * 64, "memory": "2" * 64, "io": "3" * 64,
            }
            payload["calibration"].update(
                source="trained_estimator_experience", real_measurement=False
            )
            payload["provenance"] = {
                "request_sha256": "d" * 64,
                "evidence_class": "mysql_trained_estimator_complete_endpoint_features",
                "source": "trained_estimator_experience", "real_measurement": False,
                "cross_engine_prior_used": False,
                "endpoint_evidence": _json_artifact(
                    root, f"estimator_endpoint_{index}",
                    {"database": "mysql", "source": "trained_estimator_experience"},
                ),
                "endpoint_artifacts": {"anchor": {}},
            }
            for resource in ("cpu", "memory", "io"):
                item = payload["effect_evidence"]["row_evaluate_cost"][resource]
                confidence = item["confidence"]
                confidence.update(
                    level="MODEL_BACKED", source="trained_estimator_experience",
                    real_measurement=False, query_coverage_ratio=1.0,
                )
                if payload["table"]["row_evaluate_cost"][resource] == 0:
                    item.update(
                        evidence_status="NO_DETECTABLE_EFFECT",
                        parameter_effect_status="DETECTED_EFFECT",
                        observed_delta=0.0, tolerance=1e-9,
                    )
                    confidence["decision"] = "NO_DETECTABLE_EFFECT"
            path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            pointer["sha256"] = digest
            path.with_name(path.name + ".sha256").write_text(
                f"{digest}  {path.name}\n", encoding="ascii"
            )
        failures = [check for check in verify_document(document, REPO_ROOT) if not check.passed]
        assert failures == [], "\n".join(f"{item.requirement} {item.item}: {item.reason}" for item in failures)


def test_post_cutoff_historical_real_replay_t_is_ready() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        document = _ready_document(root)
        scenario = document["scenarios"]["hybench_ap_memory"]
        pointer = scenario["experience_table"]["artifact"]
        path = Path(pointer["path"])
        payload = json.loads(path.read_text())
        active = payload["parameter_space"]["active_params"]
        points = []
        for index, value in enumerate((0.01, 1.0), 1):
            points.append(_json_artifact(
                root, f"historical_point_{index}",
                {
                    "engine": "mysql",
                    "scenario": "hybench_ap_memory",
                    "log_timestamp": f"2026-07-21T0{index}:00:00+08:00",
                    "technical_failure": False,
                    "parameter_readback_complete": True,
                    "parameter_values": {active[0]: value},
                    "dbqps_source": "database",
                    "dbqps": 10.0 + index,
                    "resource_utilization": {"cpu": 0.4, "memory": 0.9, "io": 0.3},
                    "resource_stability_qualification": "current_contract_pass",
                    "envelope_qualification": "exact_target_envelope",
                },
            ))
        payload["calibration"].update(
            source="historical_real_replay", real_measurement=True
        )
        payload["provenance"] = {
            "source": "historical_real_replay",
            "real_measurement": True,
            "cross_engine_prior_used": False,
            "evidence_class": "mysql_historical_real_replay_identifiable_regression_v1",
            "selection_policy": "historical_real_replay_first_plan_inference_second_targeted_gap_fill",
            "source_policy": _pointer(
                REPO_ROOT
                / "config/formal/frozen_mysql_experience_table_source_policy_20260723_v1/source_policy.json"
            ),
            "point_artifacts": points,
            "regression": {
                "transform": "log",
                "design_rank": 2,
                "design_column_count": 2,
                "projected_to_current_frozen_ranges": True,
                "heldout_abs_error_max": {"cpu": 0.02, "memory": 0.01, "io": 0.03},
            },
            "per_parameter_coverage": {
                active[0]: {"identified": True, "projected_to_current_range": True}
            },
        }
        for resource in ("cpu", "memory", "io"):
            payload["effect_evidence"][active[0]][resource]["confidence"].update(
                source="historical_real_replay", real_measurement=True
            )
        path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        new_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        pointer["sha256"] = new_digest
        path.with_name(path.name + ".sha256").write_text(
            f"{new_digest}  {path.name}\n", encoding="ascii"
        )
        failures = [check for check in verify_document(document, REPO_ROOT) if not check.passed]
        assert failures == [], "\n".join(
            f"{item.requirement} {item.item}: {item.reason}" for item in failures
        )


def test_pre_cutoff_historical_replay_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        document = _ready_document(root)
        scenario = document["scenarios"]["hybench_ap_memory"]
        pointer = scenario["experience_table"]["artifact"]
        path = Path(pointer["path"])
        payload = json.loads(path.read_text())
        active = payload["parameter_space"]["active_params"]
        point = _json_artifact(
            root, "pre_cutoff_historical_point",
            {
                "engine": "mysql", "scenario": "hybench_ap_memory",
                "log_timestamp": "2026-07-20T23:59:59+08:00",
                "technical_failure": False, "parameter_readback_complete": True,
                "parameter_values": {active[0]: 0.1},
                "dbqps_source": "database", "dbqps": 10.0,
                "resource_utilization": {"cpu": 0.4, "memory": 0.9, "io": 0.3},
                "resource_stability_qualification": "current_contract_pass",
                "envelope_qualification": "exact_target_envelope",
            },
        )
        payload["calibration"]["source"] = "historical_real_replay"
        payload["provenance"] = {
            "source": "historical_real_replay", "real_measurement": True,
            "cross_engine_prior_used": False,
            "evidence_class": "mysql_historical_real_replay_identifiable_regression_v1",
            "selection_policy": "historical_real_replay_first_plan_inference_second_targeted_gap_fill",
            "source_policy": _pointer(
                REPO_ROOT
                / "config/formal/frozen_mysql_experience_table_source_policy_20260723_v1/source_policy.json"
            ),
            "point_artifacts": [point],
            "regression": {
                "transform": "log", "design_rank": 2, "design_column_count": 2,
                "projected_to_current_frozen_ranges": True,
                "heldout_abs_error_max": {"cpu": 0.0, "memory": 0.0, "io": 0.0},
            },
            "per_parameter_coverage": {
                active[0]: {"identified": True, "projected_to_current_range": True}
            },
        }
        for resource in ("cpu", "memory", "io"):
            payload["effect_evidence"][active[0]][resource]["confidence"].update(
                source="historical_real_replay", real_measurement=True
            )
        path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        new_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        pointer["sha256"] = new_digest
        path.with_name(path.name + ".sha256").write_text(
            f"{new_digest}  {path.name}\n", encoding="ascii"
        )
        checks = verify_document(document, REPO_ROOT)
        assert not next(
            check for check in checks
            if check.item == "hybench_ap_memory.historical_replay_point_semantics[0]"
        ).passed


def test_t_sidecar_is_mandatory() -> None:
    with tempfile.TemporaryDirectory() as raw:
        document = _ready_document(Path(raw))
        pointer = document["scenarios"]["job_memory"]["experience_table"]["artifact"]
        path = Path(pointer["path"])
        path.with_name(path.name + ".sha256").unlink()
        checks = verify_document(document, REPO_ROOT)
        target = next(check for check in checks if check.item == "job_memory.experience_table_sidecar")
        assert not target.passed
        assert not all(check.passed for check in checks)


def test_host02_mutex_cannot_be_claimed_with_tmp_lock_or_missing_crash_test() -> None:
    with tempfile.TemporaryDirectory() as raw:
        document = _ready_document(Path(raw))
        pointer = document["global_requirements"]["host02_job_xp_mutex"]["evidence"]
        path = Path(pointer["path"])
        payload = json.loads(path.read_text())
        payload["lock_path"] = "/tmp/fake-host02.lock"
        payload["tests"]["crashed_owner_kernel_release"] = False
        path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        pointer["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        checks = verify_document(document, REPO_ROOT)
        assert not next(
            check for check in checks
            if check.item == "host02_mutex.non_tmp_control_paths"
        ).passed
        assert not next(
            check for check in checks
            if check.item == "host02_mutex.enforcement_tests"
        ).passed
        assert not next(
            check for check in checks
            if check.item == "host02_mutex.semantics"
        ).passed


if __name__ == "__main__":
    test_current_manifest_is_not_ready_with_all_requirement_groups_reported()
    test_placeholder_freeze_id_cannot_be_ready()
    test_complete_synthetic_evidence_is_ready()
    test_host02_mutex_cannot_be_claimed_with_tmp_lock_or_missing_crash_test()
    print("mysql SGD v2 readiness verifier tests passed")

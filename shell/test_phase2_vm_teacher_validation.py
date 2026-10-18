#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "shell" / "run_bonect_phase2_vm_teacher_validation.py"
SPEC = importlib.util.spec_from_file_location("phase2_vm_teacher_validation", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def point(
    role: str,
    *,
    qps: float,
    cpu: float,
    memory: float,
    io: float,
    cpu_pct: float,
    memory_pct: float,
    io_pct: float,
) -> Dict[str, Any]:
    return {
        "role": role,
        "run_id": f"test_{role}",
        "db_qps": qps,
        "resource_normalized": {
            "cpu": cpu,
            "memory": memory,
            "io": io,
            "io_read": io * 0.55,
            "io_write": io * 0.45,
        },
        "utilization_pct": {
            "cpu": cpu_pct,
            "memory": memory_pct,
            "io": io_pct,
        },
        "io_read_mib_s": io * 0.55,
        "io_write_mib_s": io * 0.45,
        "resource_sources": {
            "cpu_source": "vm_runtime_ssh",
            "memory_source": "vm_meminfo_used",
            "io_source": "vm_disk_device",
            "io_disk_devices": "vda",
        },
        "stable_sample_count": {"cpu": 90, "memory": 90, "io": 90},
        "generated_initial_param_keys": list(MODULE.COST_PARAM_KEYS),
        "benchmark": {"latency_p95": 100.0, "latency_p99": 200.0},
        "plan_hashes": [{"queryid": "1", "plan_mode": "generic", "plan_hash": role}],
    }


def evaluate(
    scenario: str,
    scenario_cfg: Dict[str, Any],
    gates: Dict[str, Any],
    before: Dict[str, Any],
    teacher: Dict[str, Any],
    after: Dict[str, Any],
) -> Dict[str, Any]:
    result = MODULE.evaluate_scenario(
        scenario=scenario,
        scenario_cfg=scenario_cfg,
        gates=gates,
        before=before,
        teacher=teacher,
        after=after,
    )
    assert result["strict_passed"], result
    assert result["db_qps"]["gain_pct"] >= 15.0
    assert result["telemetry_gate"]["passed"]
    return result


def persisted_point(
    role: str,
    *,
    scenario: str = "cpu",
    qps: float = 100.0,
    cpu_pct: float = 90.0,
    memory_pct: float = 50.0,
    io_pct: float = 20.0,
) -> Dict[str, Any]:
    record = point(
        role,
        qps=qps,
        cpu=max(0.1, cpu_pct / 100.0 * 4.0),
        memory=max(0.1, memory_pct / 100.0 * 8.0),
        io=max(0.1, io_pct / 100.0 * 800.0),
        cpu_pct=cpu_pct,
        memory_pct=memory_pct,
        io_pct=io_pct,
    )
    record.update(
        {
            "event": "teacher_validation_point_complete",
            "scenario": scenario,
            "db_xact_tps": 50.0,
            "benchmark": {
                "xp_qps": "1.0",
                "xp_tps": "50.0",
                "latency_p95": 100.0,
                "latency_p99": 200.0,
            },
            "plan_capture_count": 4,
            "all_sql_count": 8,
            "plan_feature_count": 10,
            "plan_feature_signature": f"signature_{scenario}_{role}",
            "workload_health": {
                "passed": True,
                "counts": {
                    "too_many_clients": 0,
                    "connection_failures": 0,
                    "null_pointer_exceptions": 0,
                },
            },
            "baseline_only_gate_passed": True,
            "strong_none_gate": {
                "passed": True,
                "target_resource": scenario,
            },
        }
    )
    return record


def main() -> None:
    manifest = MODULE.read_json(MODULE.DEFAULT_MANIFEST)
    MODULE.validate_manifest(manifest)
    gates = manifest["gates"]
    scenarios = manifest["scenarios"]
    assert scenarios["cpu"]["vm_profile"]["cpu"] == 3
    assert scenarios["cpu"]["capability"]["cpu"] == 3.0
    assert scenarios["cpu"]["capability"]["io"] == 800.0
    assert scenarios["cpu"]["tp_clients"] == 60
    assert scenarios["cpu"]["statement_timeout_ms"] == 420000
    assert all(scenarios[name]["run_mins"] >= 5 for name in MODULE.SCENARIO_ORDER)

    healthy_workload = MODULE.workload_health_from_text(
        "XP Workload is done\n====================Test Summary========================\n",
        gates,
    )
    assert healthy_workload["passed"]
    unhealthy_workload = MODULE.workload_health_from_text(
        "FATAL: sorry, too many clients already\n"
        "Getting connection failed\n"
        "java.lang.NullPointerException\n"
        "XP Workload is done\nTest Summary\n",
        gates,
    )
    assert not unhealthy_workload["passed"]
    assert unhealthy_workload["counts"] == {
        "too_many_clients": 1,
        "connection_failures": 1,
        "null_pointer_exceptions": 1,
    }

    valid_default = persisted_point("default_before")
    assert MODULE.point_measurement_issues(valid_default, gates) == []
    assert MODULE.default_baseline_gate(valid_default, gates)["passed"]
    overloaded_default = persisted_point(
        "default_before_overloaded",
        io_pct=120.0,
    )
    overloaded_default["workload_health"]["passed"] = False
    overloaded_gate = MODULE.default_baseline_gate(overloaded_default, gates)
    assert not overloaded_gate["passed"]
    assert "workload_health" in overloaded_gate["issues"]
    assert "utilization_pct.io.non_target_above_max" in overloaded_gate["issues"]

    preferred_relief = evaluate(
        "cpu",
        scenarios["cpu"],
        gates,
        point(
            "before", qps=100, cpu=7.2, memory=4.0, io=58,
            cpu_pct=90, memory_pct=50, io_pct=77.3,
        ),
        point(
            "teacher", qps=120, cpu=6.0, memory=4.2, io=55,
            cpu_pct=75, memory_pct=52.5, io_pct=73.3,
        ),
        point(
            "after", qps=102, cpu=7.1, memory=4.05, io=57,
            cpu_pct=88.75, memory_pct=50.6, io_pct=76.0,
        ),
    )
    assert preferred_relief["resource_goal"]["target_relief_preferred"]

    moderate_relief = evaluate(
        "cpu",
        scenarios["cpu"],
        gates,
        point(
            "before_moderate", qps=100, cpu=6.8, memory=5.6, io=20.0,
            cpu_pct=85.0, memory_pct=70.0, io_pct=26.7,
        ),
        point(
            "teacher_moderate", qps=120, cpu=6.32, memory=5.68, io=20.1,
            cpu_pct=79.0, memory_pct=71.0, io_pct=26.8,
        ),
        point(
            "after_moderate", qps=100, cpu=6.8, memory=5.6, io=20.0,
            cpu_pct=85.0, memory_pct=70.0, io_pct=26.7,
        ),
    )
    assert moderate_relief["resource_goal"]["target_relief_util_pp"] == 6.0
    assert moderate_relief["resource_goal"]["target_relief_large_enough"]
    assert not moderate_relief["resource_goal"]["target_relief_preferred"]

    near_boundary = MODULE.evaluate_scenario(
        scenario="cpu",
        scenario_cfg=scenarios["cpu"],
        gates=gates,
        before=point(
            "before_boundary", qps=100, cpu=6.416, memory=5.6, io=20,
            cpu_pct=80.2, memory_pct=70.0, io_pct=26.7,
        ),
        teacher=point(
            "teacher_boundary", qps=120, cpu=6.384, memory=5.608, io=20.1,
            cpu_pct=79.8, memory_pct=70.1, io_pct=26.8,
        ),
        after=point(
            "after_boundary", qps=102, cpu=6.416, memory=5.6, io=20,
            cpu_pct=80.2, memory_pct=70.0, io_pct=26.7,
        ),
    )
    assert not near_boundary["strict_passed"]
    assert near_boundary["resource_goal"]["target_relief_util_pp"] < 5.0
    assert not near_boundary["resource_goal"]["target_relief_large_enough"]

    below_target_sweet = evaluate(
        "cpu",
        scenarios["cpu"],
        gates,
        point(
            "before_below_sweet", qps=100, cpu=7.2, memory=5.6, io=20,
            cpu_pct=90.0, memory_pct=70.0, io_pct=26.7,
        ),
        point(
            "teacher_below_sweet", qps=120, cpu=4.8, memory=5.68, io=20.1,
            cpu_pct=60.0, memory_pct=71.0, io_pct=26.8,
        ),
        point(
            "after_below_sweet", qps=102, cpu=7.12, memory=5.6, io=20,
            cpu_pct=89.0, memory_pct=70.0, io_pct=26.7,
        ),
    )
    assert "cpu" not in below_target_sweet["resource_goal"]["sweet_resources"]
    evaluate(
        "memory",
        scenarios["memory"],
        gates,
        point(
            "before", qps=100, cpu=4.0, memory=2.7, io=20,
            cpu_pct=50, memory_pct=90, io_pct=26.7,
        ),
        point(
            "teacher", qps=120, cpu=5.6, memory=2.3, io=22,
            cpu_pct=70, memory_pct=76.7, io_pct=29.3,
        ),
        point(
            "after", qps=102, cpu=4.1, memory=2.68, io=20.5,
            cpu_pct=51.25, memory_pct=89.3, io_pct=27.3,
        ),
    )
    evaluate(
        "io",
        scenarios["io"],
        gates,
        point(
            "before", qps=100, cpu=2.0, memory=1.0, io=100,
            cpu_pct=66.7, memory_pct=68.1, io_pct=133.3,
        ),
        point(
            "teacher", qps=120, cpu=2.2, memory=1.1, io=55,
            cpu_pct=73.3, memory_pct=74.9, io_pct=73.3,
        ),
        point(
            "after", qps=102, cpu=2.05, memory=1.02, io=98,
            cpu_pct=68.3, memory_pct=69.5, io_pct=130.7,
        ),
    )

    bad_single_bottleneck = MODULE.evaluate_scenario(
        scenario="cpu",
        scenario_cfg=scenarios["cpu"],
        gates=gates,
        before=point(
            "before_bad", qps=100, cpu=7.2, memory=7.0, io=20,
            cpu_pct=90, memory_pct=87.5, io_pct=26.7,
        ),
        teacher=point(
            "teacher_bad", qps=120, cpu=6.0, memory=7.2, io=55,
            cpu_pct=75, memory_pct=90, io_pct=73.3,
        ),
        after=point(
            "after_bad", qps=102, cpu=7.1, memory=7.05, io=20.5,
            cpu_pct=88.75, memory_pct=88.1, io_pct=27.3,
        ),
    )
    assert not bad_single_bottleneck["strict_passed"]
    assert not bad_single_bottleneck["resource_goal"]["exactly_one_baseline_bottleneck"]
    evaluate(
        "none",
        scenarios["none"],
        gates,
        point(
            "before", qps=100, cpu=2.4, memory=2.4, io=20,
            cpu_pct=30, memory_pct=30, io_pct=26.7,
        ),
        point(
            "teacher", qps=120, cpu=5.6, memory=2.5, io=21,
            cpu_pct=70, memory_pct=31.25, io_pct=28,
        ),
        point(
            "after", qps=102, cpu=2.45, memory=2.42, io=20.4,
            cpu_pct=30.6, memory_pct=30.25, io_pct=27.2,
        ),
    )

    for scenario in MODULE.SCENARIO_ORDER:
        cmd = MODULE.runner_command(
            run_id=f"test_{scenario}",
            scenario=scenario,
            scenario_cfg=scenarios[scenario],
            params=manifest["default_params"],
            execute=False,
        )
        assert cmd.count("--bonect-initial-param") == 7
        assert "--run" not in cmd
        assert "binary_plan_resource_tuner.py" not in " ".join(cmd)
        timeout_index = cmd.index("--hybench-statement-timeout-ms") + 1
        expected_timeout = int(scenarios[scenario].get("statement_timeout_ms", 300000))
        assert cmd[timeout_index] == str(expected_timeout)
        assert cmd[cmd.index("--vm-runtime-cpu-mode") + 1] == "vm_busy"
        assert cmd[cmd.index("--vm-runtime-memory-mode") + 1] == "vm_used"
        assert cmd[cmd.index("--vm-runtime-io-mode") + 1] == "disk_device"

    with tempfile.TemporaryDirectory() as tmpdir:
        state_path = Path(tmpdir) / "state.jsonl"

        def valid_predecessor_record(index: int) -> Dict[str, Any]:
            common = {
                "db_qps": 400.0,
                "db_xact_tps": 100.0,
                "resource_normalized": {"cpu": 18.0, "memory": 4.5, "io": 10.0},
                "utilization_pct": {"cpu": 22.0, "memory": 21.0, "io": 28.0},
                "resource_sources": {
                    "cpu_source": "vm_pg_proc_cpu",
                    "memory_source": "vm_pg_proc_rss",
                    "io_source": "vm_disk_device",
                    "io_disk_devices": "vda",
                },
                "stable_sample_count": {"cpu": 90, "memory": 90, "io": 90},
                "plan_feature_count": 10,
                "plan_feature_signature": f"signature_{index}",
            }
            return {
                "event": "calibration_point_complete",
                "point_index": index,
                "run_id": f"nonexistent_test_run_{index}",
                "paired_baseline_valid": True,
                "measurement_valid": True,
                "benchmark": {"xp_qps": "20.0"},
                "plan_capture_count": 4,
                "all_sql_count": 8,
                "history_path": f"/tmp/test_history_{index}.jsonl",
                "paired_baseline": dict(common),
                **common,
            }

        with state_path.open("w", encoding="utf-8") as handle:
            for index in (2, 3):
                if index == 2:
                    handle.write(
                        json.dumps(
                            {
                                "event": "calibration_paired_baseline_failed",
                                "point_index": index,
                                "run_id": "superseded_failed_attempt",
                            }
                        )
                        + "\n"
                    )
                handle.write(json.dumps(valid_predecessor_record(index)) + "\n")
        predecessor = MODULE.validate_predecessor(
            {
                "predecessor": {
                    "state_path": str(state_path),
                    "required_point_indices": [2, 3],
                    "require_paired_baseline_valid": True,
                    "reject_failed_events": True,
                    "reject_control_decisions": True,
                }
            }
        )
        assert predecessor["passed"]

        invalid_state_path = Path(tmpdir) / "invalid_state.jsonl"
        invalid_record = valid_predecessor_record(2)
        invalid_record["db_qps"] = None
        invalid_record["resource_normalized"] = {}
        invalid_state_path.write_text(json.dumps(invalid_record) + "\n", encoding="utf-8")
        try:
            MODULE.validate_predecessor(
                {
                    "gates": {"min_stable_samples": 45},
                    "predecessor": {
                        "state_path": str(invalid_state_path),
                        "required_point_indices": [2],
                        "require_paired_baseline_valid": True,
                        "reject_failed_events": True,
                        "reject_control_decisions": True,
                    },
                }
            )
        except RuntimeError as exc:
            assert "invalid_measurements" in str(exc)
        else:
            raise AssertionError("incomplete predecessor measurement must fail")

        teacher_state = Path(tmpdir) / "teacher_state.jsonl"
        invalid_complete = persisted_point("default_before")
        invalid_complete["workload_health"]["passed"] = False
        teacher_state.write_text(
            json.dumps(invalid_complete) + "\n",
            encoding="utf-8",
        )
        assert MODULE.load_completed_points(teacher_state, gates) == {}
        with teacher_state.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "event": "teacher_validation_default_baseline_failed",
                        "scenario": "cpu",
                        "role": "default_before",
                    }
                )
                + "\n"
            )
            handle.write(json.dumps(valid_default) + "\n")
        loaded = MODULE.load_completed_points(teacher_state, gates)
        assert ("cpu", "default_before") in loaded
    print("phase2 VM teacher validation tests passed", flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Tests for the post-terminal adaptive-C common-domain handoff."""

from __future__ import annotations

import inspect
import json
import math
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell import run_pg_common_domain_frozen_champions as common  # noqa: E402
from shell import verify_pg_common_domain_frozen_champions as common_verifier  # noqa: E402


PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def main() -> int:
    main_source = inspect.getsource(common.main)
    assert "methods: Dict[str, Any] = {}" not in main_source
    assert common_verifier.valid_method_order(
        ("algorithm_c", "algorithm_a", "algorithm_b")
    )
    assert common_verifier.valid_method_order(tuple(reversed(common_verifier.METHODS)))
    assert common_verifier.valid_method_order(("algorithm_a", "algorithm_b"))
    assert common_verifier.valid_method_order(("algorithm_c",))
    assert not common_verifier.valid_method_order(())
    assert not common_verifier.valid_method_order(
        ("algorithm_a", "algorithm_b", "algorithm_b")
    )
    assert common_verifier.expected_baseline_stages(3) == [
        "common_baseline_00",
        "common_baseline_01",
        "common_baseline_02",
        "common_baseline_03",
    ]
    assert common.host_ssd_is_informational_for_scene(
        {
            "target": "memory",
            "cfg": {
                "algorithm_c": {
                    "ssd_replay_qualification": {
                        "non_target_shared_ssd_policy": "container_io_capacity"
                    }
                }
            },
        }
    )
    assert not common.host_ssd_is_informational_for_scene(
        {
            "target": "io",
            "cfg": {
                "algorithm_c": {
                    "ssd_replay_qualification": {
                        "non_target_shared_ssd_policy": "container_io_capacity"
                    }
                }
            },
        }
    )
    source_cfg = {
        "algorithm_c": {
            "ssd_replay_qualification": {
                "io_target_baseline_minimum_ssd_avg_pct": 65.0,
            }
        }
    }
    io_contracts = {
        "xp_io": {
            "target": "io",
            "config_path": "/source/xp_io/optimizer_config.json",
            "cfg": source_cfg,
            "workers": [{"name": "w1"}],
        }
    }
    scaling = common.apply_worker_scaled_io_baseline_ssd_gate(
        io_contracts,
        enabled=True,
        calibration_workers=2,
    )
    assert scaling["contract"] == common.IO_BASELINE_SSD_WORKER_SCALING_CONTRACT
    assert scaling["source_minimum_ssd_avg_pct"] == 65.0
    assert scaling["scale_factor"] == 0.5
    assert scaling["effective_minimum_ssd_avg_pct"] == 32.5
    assert source_cfg["algorithm_c"]["ssd_replay_qualification"][
        "io_target_baseline_minimum_ssd_avg_pct"
    ] == 65.0
    assert io_contracts["xp_io"]["cfg"]["algorithm_c"][
        "ssd_replay_qualification"
    ]["io_target_baseline_minimum_ssd_avg_pct"] == 32.5
    disabled_contracts = json.loads(json.dumps(io_contracts))
    disabled_contracts["xp_io"]["cfg"] = source_cfg
    disabled = common.apply_worker_scaled_io_baseline_ssd_gate(
        disabled_contracts,
        enabled=False,
        calibration_workers=2,
    )
    assert disabled == {
        "contract": common.IO_BASELINE_SSD_WORKER_SCALING_CONTRACT,
        "enabled": False,
    }
    assert disabled_contracts["xp_io"]["cfg"] is source_cfg
    try:
        common.apply_worker_scaled_io_baseline_ssd_gate(
            {**io_contracts, "job_memory": {"target": "memory"}},
            enabled=True,
            calibration_workers=2,
        )
    except ValueError as exc:
        assert "isolated xp_io" in str(exc)
    else:
        raise AssertionError("worker scaling must be isolated to XP-I/O")
    container_contracts = {
        "xp_io": {
            "workers": [{"container": "declared_pg", "port": 59515}],
        }
    }
    inspect_document = json.dumps(
        [{"Name": "/declared_pg", "State": {"Running": False}}]
    )
    completed = common.subprocess.CompletedProcess
    with patch.object(
        common.subprocess,
        "run",
        side_effect=[
            completed(["docker", "inspect"], 0, inspect_document, ""),
            completed(["docker", "start"], 0, "declared_pg\n", ""),
            completed(["docker", "exec"], 0, "accepting connections\n", ""),
        ],
    ) as docker_run:
        start_receipt = common.ensure_declared_containers_running(
            container_contracts,
            readiness_timeout_s=1.0,
        )
    assert docker_run.call_count == 3
    assert start_receipt["passed"] is True
    assert start_receipt["declared_containers"] == ["declared_pg"]
    assert start_receipt["initially_running_containers"] == []
    assert start_receipt["started_containers"] == ["declared_pg"]
    assert start_receipt["ready_containers"] == ["declared_pg"]
    assert start_receipt["postgres_ports"] == {"declared_pg": 59515}
    assert start_receipt["readiness_probe_timeouts"] == {"declared_pg": 0}
    with patch.object(
        common.subprocess,
        "run",
        side_effect=[
            completed(["docker", "inspect"], 0, inspect_document, ""),
            completed(["docker", "start"], 0, "declared_pg\n", ""),
            common.subprocess.TimeoutExpired(["docker", "exec"], 15.0),
            completed(["docker", "exec"], 0, "accepting connections\n", ""),
        ],
    ), patch.object(common.time, "sleep", return_value=None):
        timeout_receipt = common.ensure_declared_containers_running(
            container_contracts,
            readiness_timeout_s=30.0,
        )
    assert timeout_receipt["passed"] is True
    assert timeout_receipt["readiness_attempts"] == {"declared_pg": 2}
    assert timeout_receipt["readiness_probe_timeouts"] == {"declared_pg": 1}
    external_rows = common.parse_external_workload_processes(
        "123 wangxr 42 java -jar benchbase.jar -b chbenchmark -c x.xml\n"
        "124 systemd+ 40 postgres: postgres benchbase 192.0.2.48(1) SELECT\n"
        "125 anonymous 38 java -jar benchbase.jar -b tpch -c tpch.xml\n"
        "126 systemd+ 30 postgres: postgres fullgrid_tpch_sf5 127.0.0.1 SELECT\n"
        "127 wangxr 29 java -jar benchbase.jar -b tpcc,chbenchmark -c mix.xml\n"
        "128 wangxr 20 java -jar benchbase.jar -b=tpcc -c tpcc.xml\n"
        "129 wangxr 15 bash run-join.sh\n"
        "130 wangxr 12 zsh -c RUN_WORKLOADS=tpcc bash run-join.sh\n"
    )
    assert [row["pid"] for row in external_rows] == [
        123,
        124,
        127,
        128,
        129,
        130,
    ]
    idle_rows = common.parse_external_workload_processes(
        "201 postgres 120 postgres: postgres benchbase [local] idle\n"
        "202 postgres 120 postgres: postgres benchbase [local] idle in transaction\n"
        "203 postgres 1 postgres: postgres benchbase [local] EXPLAIN\n"
        "204 anonymous 1 python -c postgres: postgres benchbase [local] SELECT\n"
    )
    assert [row["pid"] for row in idle_rows] == [202, 203]
    assert common.parse_external_workload_processes(
        "205 postgres 0 postgres: postgres benchbase [local] SELECT\n"
    ) == []
    assert common.parse_external_workload_processes(
        "206 postgres 999 postgres: postgres benchbase [local] SELECT\n"
    ) == []  # Process age is not query-state duration.
    assert [row["pid"] for row in common.parse_external_workload_processes(
        "207 postgres 0 postgres: postgres benchbase [local] SELECT\n"
        "208 postgres 0 postgres: postgres benchbase [local] UPDATE\n"
    )] == [207, 208]
    persistent_candidates = common._parse_external_workload_candidates(
        "209 postgres 999 postgres: postgres benchbase [local] SELECT\n"
    )
    persistent_seen = {}
    assert common._filter_external_workload_candidates(
        persistent_candidates, now=0.0,
        standalone_backend_first_seen=persistent_seen,
    ) == []
    assert common._filter_external_workload_candidates(
        persistent_candidates, now=1.0,
        standalone_backend_first_seen=persistent_seen,
    ) == []
    assert [row["pid"] for row in common._filter_external_workload_candidates(
        persistent_candidates, now=2.0,
        standalone_backend_first_seen=persistent_seen,
    )] == [209]
    assert common._filter_external_workload_candidates(
        [], now=3.0, standalone_backend_first_seen=persistent_seen,
    ) == []
    assert persistent_seen == {}
    with tempfile.TemporaryDirectory() as temporary:
        elapsed = [0.0]

        def advance(seconds: float) -> None:
            elapsed[0] += seconds

        samples = [
            (idle_rows, []), ([], []), (idle_rows, []),
            ([], ["scan failed"]), ([], []), ([], []),
        ]
        with patch.object(common, "scan_external_workload_processes", side_effect=samples), \
             patch.object(common.time, "monotonic", side_effect=lambda: elapsed[0]), \
             patch.object(common.time, "sleep", side_effect=advance):
            assert common.wait_for_external_quiet(
                Path(temporary), "test", quiet_s=2.0, should_stop=lambda: False,
            )
        status = common.read_json(Path(temporary) / "external_wait_status.json")
        assert status["phase"] == "released"
        assert status["quiet_elapsed_s"] == 2.0
        assert elapsed[0] == 5.0  # Activity and scan errors reset the quiet timer.
        assert not common.wait_for_external_quiet(
            Path(temporary), "cancelled", quiet_s=2.0, should_stop=lambda: True,
        )
    with tempfile.TemporaryDirectory() as temporary:
        def contaminated_monitor(_stop, receipt, _sample_s):
            receipt.update({"contaminated": True, "passed": False,
                            "detection_sample_count": 1})

        with patch.object(common, "monitor_external_workloads", side_effect=contaminated_monitor), \
             patch.object(common, "restore_all_workers", return_value={}), \
             patch.object(common, "scan_external_workload_processes", return_value=(idle_rows, [])), \
             patch.object(common, "run_real_validation_safe") as workload:
            try:
                common.run_stage_attempt(
                    {}, Path(temporary), "algorithm_c", 1, {},
                    ssd_device="test", post_restore_ssd_sample_s=1.0,
                    maximum_post_restore_ssd_util_pct=5.0,
                    post_restore_ssd_maximum_wait_s=1.0,
                )
            except common.RetryableInterference:
                pass
            else:
                raise AssertionError("restore interference must trigger a safe retry")
            workload.assert_not_called()
        assert (Path(temporary) / "interference_receipts/algorithm_c_a1_restore.json").exists()
    original_scan = common.scan_external_workload_processes
    try:
        common.scan_external_workload_processes = lambda: (external_rows[:1], [])
        stop_event = threading.Event()
        stop_event.set()
        monitor_receipt = {
            "contract": "pg_external_workload_contamination_guard_v3"
        }
        common.monitor_external_workloads(stop_event, monitor_receipt, 0.1)
    finally:
        common.scan_external_workload_processes = original_scan
    assert monitor_receipt["passed"] is False
    assert monitor_receipt["contaminated"] is True
    assert monitor_receipt["detection_sample_count"] == 1
    assert [row["pid"] for row in monitor_receipt["detections"]] == [123]

    original_quiet_audit = common.audit_pre_stage_ssd_quiet
    quiet_samples = iter(
        (
            {"passed": False, "observed_util_pct": 80.0},
            {"passed": True, "observed_util_pct": 0.0},
        )
    )
    try:
        common.audit_pre_stage_ssd_quiet = lambda **_kwargs: next(quiet_samples)
        post_restore_quiet = common.wait_for_post_restore_ssd_quiet(
            device="test",
            sample_s=0.1,
            maximum_util_pct=5.0,
            maximum_wait_s=10.0,
        )
    finally:
        common.audit_pre_stage_ssd_quiet = original_quiet_audit
    assert post_restore_quiet["passed"] is True
    assert post_restore_quiet["observation_count"] == 2
    assert post_restore_quiet["contract"] == (
        "pg_common_domain_post_restore_ssd_quiet_v1"
    )

    methods = ("algorithm_a", "algorithm_b", "algorithm_c")
    stages = (
        "common_baseline_00",
        "algorithm_a",
        "common_baseline_01",
        "algorithm_b",
        "common_baseline_02",
        "algorithm_c",
        "common_baseline_03",
    )
    brackets = {
        "algorithm_a": ("common_baseline_00", "common_baseline_01"),
        "algorithm_b": ("common_baseline_01", "common_baseline_02"),
        "algorithm_c": ("common_baseline_02", "common_baseline_03"),
    }
    qps_by_stage = {
        "common_baseline_00": 100.0,
        "algorithm_a": 105.0,
        "common_baseline_01": 100.0,
        "algorithm_b": 110.0,
        "common_baseline_02": 100.0,
        "algorithm_c": 120.0,
        "common_baseline_03": 100.0,
    }
    state = {
        "stage_results": {
            stage: {
                "scene_results": {
                    "job_memory": {
                        "aggregate": {"db_qps": qps, "params": PARAMS}
                    }
                }
            }
            for stage, qps in qps_by_stage.items()
        }
    }
    comparison, drift_errors = common.build_scene_comparison(
        state=state,
        scene="job_memory",
        stages=stages,
        methods=methods,
        method_baseline_brackets=brackets,
        maximum_baseline_drift_pct=10.0,
    )
    assert drift_errors == []
    assert set(comparison["methods"]) == set(methods)
    assert math.isclose(
        comparison["methods"]["algorithm_c"]["qps_improvement_pct"],
        20.0,
    )
    assert comparison["methods"]["algorithm_c"]["qps_reference_stage"] == (
        "common_baseline_00"
    )

    common.validate_database_cohort_fingerprints(
        {
            "job_memory": {
                "w1": {"semantic_sha256": "same"},
                "w2": {"semantic_sha256": "same"},
            }
        }
    )
    try:
        common.validate_database_cohort_fingerprints(
            {
                "tpch_memory": {
                    "w1": {
                        "semantic_sha256": "one",
                        "semantic": {"exact_table_cardinalities": {"public.lineitem": 10}},
                    },
                    "w2": {
                        "semantic_sha256": "two",
                        "semantic": {"exact_table_cardinalities": {"public.lineitem": 11}},
                    },
                }
            }
        )
    except RuntimeError as exc:
        assert "worker database fingerprints differ" in str(exc)
        assert "public.lineitem" in str(exc)
    else:
        raise AssertionError("different worker databases must fail closed")

    common.validate_database_plan_cohort(
        {
            "job_memory": {
                "w1": {"points": {"baseline": {"plan_signatures_sha256": "same"}}},
                "w2": {"points": {"baseline": {"plan_signatures_sha256": "same"}}},
            }
        }
    )
    try:
        common.validate_database_plan_cohort(
            {
                "tpch_memory": {
                    "w1": {"points": {"baseline": {"plan_signatures_sha256": "one"}}},
                    "w2": {"points": {"baseline": {"plan_signatures_sha256": "two"}}},
                }
            }
        )
    except RuntimeError as exc:
        assert "worker plan fingerprints differ" in str(exc)
    else:
        raise AssertionError("different worker plans must fail closed")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        initial_paths = []
        method_paths = []
        for worker, initial_qps, method_qps in (
            ("w1", 100.0, 120.0),
            ("w2", 200.0, 220.0),
        ):
            initial_path = root / f"initial_{worker}.json"
            method_path = root / f"method_{worker}.json"
            write_json(initial_path, {"worker_key": worker, "db_qps": initial_qps})
            write_json(method_path, {"worker_key": worker, "db_qps": method_qps})
            initial_paths.append(str(initial_path))
            method_paths.append(str(method_path))
        paired_state = json.loads(json.dumps(state))
        paired_state["stage_results"]["common_baseline_00"]["scene_results"][
            "job_memory"
        ]["row_paths"] = initial_paths
        paired_state["stage_results"]["algorithm_c"]["scene_results"][
            "job_memory"
        ]["row_paths"] = method_paths
        paired_state["stage_results"]["common_baseline_00"]["scene_results"][
            "job_memory"
        ]["aggregate"]["db_qps"] = 150.0
        paired_state["stage_results"]["algorithm_c"]["scene_results"][
            "job_memory"
        ]["aggregate"]["db_qps"] = 170.0
        paired, _ = common.build_scene_comparison(
            state=paired_state,
            scene="job_memory",
            stages=stages,
            methods=methods,
            method_baseline_brackets=brackets,
            maximum_baseline_drift_pct=10.0,
        )
        # Mean of same-worker ratios: (1.2 + 1.1) / 2 = 1.15.
        assert math.isclose(
            paired["methods"]["algorithm_c"]["qps_improvement_pct"],
            15.0,
        )
        assert math.isclose(
            paired["methods"]["algorithm_c"]["normalized_db_qps"],
            172.5,
        )

    formal_result = {
        "scene": "job_memory",
        "status": "validated",
        "trial": {
            "valid_for_quality_or_efficiency_claim": True,
            "finalist": {"params": PARAMS},
            "validation": {"passed": True},
        },
    }
    assert common.champion_params(formal_result, "algorithm_c") == PARAMS
    invalid = json.loads(json.dumps(formal_result))
    invalid["trial"]["validation"]["passed"] = False
    try:
        common.champion_params(invalid, "algorithm_c")
    except RuntimeError as exc:
        assert "not validated" in str(exc)
    else:
        raise AssertionError("invalid adaptive C champion must be rejected")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        write_json(root / "launcher_status.json", {"phase": "launched"})
        try:
            common.default_sources(
                methods=("algorithm_a", "algorithm_b", "algorithm_c"),
                algorithm_c_formal_root=root,
            )
        except RuntimeError as exc:
            assert "successfully terminal" in str(exc)
        else:
            raise AssertionError("A/B handoff must be blocked before C is terminal")

        write_json(root / "launcher_status.json", {
            "phase": "finished",
            "returncodes": {scene: 0 for scene, _target in common.SCENES},
        })
        sources = common.default_sources(
            methods=("algorithm_a", "algorithm_b", "algorithm_c"),
            algorithm_c_formal_root=root,
        )
        assert set(sources["authorities"]) == {
            "algorithm_a",
            "algorithm_b",
            "algorithm_c",
        }
        for scene, _target in common.SCENES:
            assert sources["configs"][scene] == root / scene / "optimizer_config.json"
            assert sources["authorities"]["algorithm_c"][scene] == (
                root / scene / "budgeted_formal_result.json"
            )

        execution_root = root / "current_execution_contract"
        paper_sources = common.default_sources(
            methods=("algorithm_a", "algorithm_b", "algorithm_c"),
            config_root=execution_root,
        )
        for scene, _target in common.SCENES:
            assert paper_sources["configs"][scene] == (
                execution_root / scene / "optimizer_config.json"
            )
            assert paper_sources["authorities"]["algorithm_c"][scene] != (
                execution_root / scene / "budgeted_formal_result.json"
            )
            assert "20260823_algorithm_c_training_boundary_clean_r75" in str(
                paper_sources["authorities"]["algorithm_c"][scene]
            )

    print("PASS: adaptive C post-terminal common-domain handoff", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

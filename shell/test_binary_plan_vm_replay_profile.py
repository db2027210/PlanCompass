#!/usr/bin/env python3
"""Regression test proving VM replay honors target-specific HyBench semantics."""

from __future__ import annotations

import copy
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "shell") not in sys.path:
    sys.path.insert(0, str(ROOT / "shell"))

import bonect_parallel_vm_replay  # noqa: E402
import bonect_real_validate_candidates  # noqa: E402
from binary_plan_resource_tuner import (  # noqa: E402
    resolve_vm_replay_db_qps,
    run_vm_replay_validation,
)


class VmReplayProfileTest(unittest.TestCase):
    def test_qps_resolution_prefers_db_counter_and_audits_benchmark_fallback(self) -> None:
        self.assertEqual(
            (120.0, "pg_stat_statements"),
            resolve_vm_replay_db_qps(
                {"db_qps": 120.0, "db_xact_tps": 80.0},
                {"throughput_qps": 100.0},
            ),
        )
        self.assertEqual(
            (2376.5, "benchmark_throughput_qps"),
            resolve_vm_replay_db_qps(
                {"db_qps": 0.0, "db_xact_tps": 2281.0},
                {"throughput_qps": 2376.5, "goodput_qps": 2388.0},
            ),
        )

    def test_none_target_override_is_materialized_into_real_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_text:
            temp_dir = Path(temp_dir_text)
            props_path = temp_dir / "historical_none.props"
            props_path.write_text(
                "\n".join(
                    [
                        "sf=10x",
                        "xtpclient=6",
                        "xapclient=4",
                        "xpRunMins=5",
                        "apRunMins=5",
                        "ap_query_ids=5,6,8,11",
                        "ap_query_weights=35,45,10,10",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            statement_path = temp_dir / "historical_statements.toml"
            statement_path.write_text(
                '[AP-6]\nsql = """select 1"""\n\n[AP-8]\nsql = """select 8"""\n',
                encoding="utf-8",
            )
            ap6_override = temp_dir / "none_ap6.sql"
            ap6_override.write_text("select 600 + 300;\n", encoding="utf-8")
            setup_sql = temp_dir / "none_setup.sql"
            setup_sql.write_text("select 42;\n", encoding="utf-8")

            scenario_config = temp_dir / "none.json"
            scenario_config.write_text(
                json.dumps(
                    {
                        "postgres": {
                            "host": "192.0.2.4",
                            "port": 25432,
                            "user": "postgres",
                            "password": "secret",
                            "dbname": "hybench_sf10x",
                        },
                        "capability": {"cpu": 8, "memory": 4, "io": 200},
                        "metrics": {"vm_runtime": {"host": "192.0.2.4"}},
                        "benchmark": {
                            "workload_cmd": (
                                f"cd {temp_dir} && bash hybench -t runxp "
                                f"-c {props_path} -f {statement_path}"
                            ),
                            "validation_warmup_enabled": False,
                        },
                    }
                ),
                encoding="utf-8",
            )
            worker_config = temp_dir / "worker.json"
            worker_config.write_text(
                json.dumps(
                    {
                        "worker_id": "none-test-vm",
                        "vm_name": "none-test-vm",
                        "scenario_configs": {"none": str(scenario_config)},
                        "scenario_overrides": {"none": {}},
                        "vm_resource": {
                            "vm_name": "none-test-vm",
                            "disk_dev": "vda",
                            "cpu": 8,
                            "memory_gb": 4,
                            "read_mb": 100,
                            "write_mb": 100,
                        },
                    }
                ),
                encoding="utf-8",
            )
            cfg: Dict[str, Any] = {
                "binary_plan_tuning": {
                    "workload_name": "hybenchxp",
                    "validation": {
                        "docker_runtime": {"min_stable_samples": 1},
                        "vm_replay": {
                            "enabled": True,
                            "run_mins": 3,
                            "workload_timeout_s": 300,
                            "worker_configs": {"none": str(worker_config)},
                        },
                        "hybench_xp": {
                            "enabled": True,
                            "hybench_dir": str(temp_dir),
                            "launcher": str(temp_dir / "hybench"),
                            "target_overrides": {
                                "none": {
                                    "xtpclient": 1,
                                    "xapclient": 1,
                                    "ap_query_ids": "6",
                                    "ap_query_weights": "100",
                                    "ap_query_sequence": "6",
                                    "setup_sql_files": [str(setup_sql)],
                                    "statement_sql_files": {"AP-6": str(ap6_override)},
                                }
                            },
                        },
                    },
                }
            }
            captured: Dict[str, Any] = {}

            def fake_validate_candidate(
                candidate: Dict[str, Any],
                context: Dict[str, Any],
                *,
                run_mins: float,
                workload_timeout_s: float,
            ) -> Dict[str, Any]:
                captured["candidate"] = copy.deepcopy(candidate)
                captured["context"] = copy.deepcopy(context)
                workload_cmd = str((context["config"].get("benchmark") or {}).get("workload_cmd") or "")
                props_match = re.search(r"(?:^|\s)-c\s+([^\s]+)", workload_cmd)
                statement_match = re.search(r"(?:^|\s)-f\s+([^\s]+)", workload_cmd)
                captured["workload_cmd"] = workload_cmd
                captured["props"] = Path(props_match.group(1)).read_text(encoding="utf-8")
                captured["statement"] = Path(statement_match.group(1)).read_text(encoding="utf-8")
                setup_paths = (context["config"].get("benchmark") or {}).get("pre_workload_sql_files") or []
                captured["setup_paths"] = list(setup_paths)
                return {
                    "candidate_id": candidate["candidate_id"],
                    "scenario": "none",
                    "source": "binary_plan_vm_replay",
                    "params": copy.deepcopy(candidate["params"]),
                    "status": "ok",
                    "run_mins_override": run_mins,
                    "elapsed_s": 180.0,
                    "actual_resource": {"cpu": 1.0, "memory": 2.0, "io": 3.0},
                    "actual_utilization_pct": {"cpu": 10.0, "memory": 20.0, "io": 30.0},
                    "raw_resource_stats": {
                        "stable_sample_count": {"cpu": 3, "memory": 3, "io": 3},
                        "cpu_source": "test",
                        "memory_source": "test",
                        "io_source": "test",
                        "workload_exit": {"returncode": 0},
                        "vm_host": {"value": "192.0.2.4"},
                    },
                    "db_delta": {"db_qps": 120.0, "db_xact_tps": 100.0},
                    "benchmark": {
                        "xp_qps": 1.0,
                        "xp_tps": 1.0,
                        "latency_p90": 1.0,
                        "latency_p95": 2.0,
                        "latency_p99": 3.0,
                    },
                    "benchmark_latency": {},
                    "pre_workload_setup": [
                        {"path": setup_paths[0], "status": "ok", "elapsed_s": 0.1}
                    ],
                    "validation_warmup": {
                        "enabled": False,
                        "executed": False,
                        "success": True,
                    },
                    "workload_cmd": workload_cmd,
                }

            output_dir = temp_dir / "run"
            with (
                mock.patch.object(
                    bonect_parallel_vm_replay,
                    "resolve_restore_io_boost",
                    return_value=None,
                ),
                mock.patch.object(
                    bonect_parallel_vm_replay,
                    "effective_worker_vm_resource",
                    return_value={},
                ),
                mock.patch.object(
                    bonect_parallel_vm_replay,
                    "restore_worker_database",
                    return_value={"status": "ok", "host": "192.0.2.4"},
                ),
                mock.patch.object(
                    bonect_parallel_vm_replay,
                    "drop_worker_cache",
                    return_value={"status": "ok"},
                ),
                mock.patch.object(
                    bonect_real_validate_candidates,
                    "validate_candidate",
                    side_effect=fake_validate_candidate,
                ),
            ):
                result = run_vm_replay_validation(
                    cfg,
                    {"seq_page_cost": 1.0},
                    output_dir=output_dir,
                    label="none_baseline",
                )

            run_config = result["hybench"]["run_config"]
            self.assertEqual(1, run_config["xtpclient"])
            self.assertEqual(1, run_config["xapclient"])
            self.assertEqual("6", run_config["ap_query_ids"])
            self.assertEqual("100", run_config["ap_query_weights"])
            self.assertEqual("6", run_config["ap_query_sequence"])
            self.assertEqual(3, run_config["xpRunMins"])
            self.assertEqual(1, run_config["setup_sql_count"])
            self.assertTrue(run_config["statement_meta"]["generated"])
            self.assertIn("AP-6", run_config["statement_meta"]["statement_sql_file_sections"])
            self.assertIn("xtpclient=1", captured["props"])
            self.assertIn("xapclient=1", captured["props"])
            self.assertIn("ap_query_ids=6", captured["props"])
            self.assertIn("ap_query_weights=100", captured["props"])
            self.assertIn("ap_query_sequence=6", captured["props"])
            self.assertIn("select 600 + 300;", captured["statement"])
            self.assertEqual(1, len(captured["setup_paths"]))
            self.assertEqual("select 42;\n", Path(captured["setup_paths"][0]).read_text(encoding="utf-8"))
            self.assertIn(run_config["props_path"], captured["workload_cmd"])
            self.assertIn(run_config["statement_file"], captured["workload_cmd"])
            self.assertEqual(
                {"cpu": 10.0, "memory": 20.0, "io": 30.0},
                result["actual_utilization_pct"],
            )
            self.assertEqual(
                {"cpu": 8.0, "memory": 4.0, "io": 200.0},
                result["runtime_capability"],
            )


if __name__ == "__main__":
    unittest.main()

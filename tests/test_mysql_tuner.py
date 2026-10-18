from __future__ import annotations

import re
import os
import tempfile
from pathlib import Path
import unittest

from mysql_cost_adapter import (
    ENGINE_COST_KEYS, MYSQL_COST_KEYS, SERVER_COST_KEYS, SYSTEM_VARIABLE_KEYS,
    MySQLCostAdapter,
)
from mysql_parameter_optimizer import run_optimization, validate_config
from mysql_resource_monitor import run_and_monitor
from mysql_full_grid_replay import MySQLFullGridReplay
from mysql_test_orchestrator import MySQLTestOrchestrator
from ranking_metrics import evaluate_top15_gate


class FakeClient:
    def __init__(self) -> None:
        self.defaults = {name: float(index + 1) for index, name in enumerate(MYSQL_COST_KEYS)}
        self.raw = {name: None for name in MYSQL_COST_KEYS}
        self.globals = {name: float(index + 1) for index, name in enumerate(SYSTEM_VARIABLE_KEYS)}
        self.flush_count = 0

    def execute(self, sql: str) -> str:
        if sql.startswith("SELECT cost_name,cost_value,default_value FROM mysql.server_cost"):
            return "\n".join(
                f"{name}\t{self._raw(name)}\t{self.defaults[name]}" for name in sorted(SERVER_COST_KEYS)
            ) + "\n"
        if sql.startswith("SELECT cost_name,cost_value,default_value,engine_name"):
            return "\n".join(
                f"{name}\t{self._raw(name)}\t{self.defaults[name]}\tdefault\t0"
                for name in sorted(ENGINE_COST_KEYS)
            ) + "\n"
        if sql.startswith("SELECT @@GLOBAL."):
            return "\t".join(str(self.globals[name]) for name in SYSTEM_VARIABLE_KEYS) + "\n"
        pattern = re.compile(
            r"UPDATE mysql\.(?:server_cost|engine_cost) SET cost_value=([^ ]+) "
            r"WHERE cost_name='([^']+)'"
        )
        for value, name in pattern.findall(sql):
            self.raw[name] = None if value == "NULL" else float(value)
        for name, value in re.findall(r"SET GLOBAL ([A-Za-z0-9_]+)=([0-9]+)", sql):
            self.globals[name] = float(value)
        self.flush_count += sql.count("FLUSH OPTIMIZER_COSTS")
        return ""

    def _raw(self, name: str) -> str:
        value = self.raw[name]
        return "NULL" if value is None else str(value)


class CostAdapterTest(unittest.TestCase):
    def test_apply_and_exact_null_restore(self) -> None:
        client = FakeClient()
        adapter = MySQLCostAdapter(client)
        snapshot = adapter.snapshot()
        adapter.apply({"io_block_read_cost": 3.5, "row_evaluate_cost": 0.75})
        self.assertEqual(client.raw["io_block_read_cost"], 3.5)
        self.assertEqual(client.raw["row_evaluate_cost"], 0.75)
        adapter.restore(snapshot)
        self.assertTrue(all(value is None for value in client.raw.values()))
        self.assertEqual(client.flush_count, 2)

    def test_rejects_non_mysql_parameter(self) -> None:
        adapter = MySQLCostAdapter(FakeClient())
        with self.assertRaisesRegex(ValueError, "unsupported MySQL"):
            adapter.apply({"seq_page_cost": 1.0})

    def test_system_variable_is_exactly_restored(self) -> None:
        client = FakeClient()
        adapter = MySQLCostAdapter(client)
        snapshot = adapter.snapshot()
        original = client.globals["innodb_flush_log_at_trx_commit"]
        adapter.apply({"innodb_flush_log_at_trx_commit": 2})
        self.assertEqual(client.globals["innodb_flush_log_at_trx_commit"], 2)
        adapter.restore(snapshot)
        self.assertEqual(client.globals["innodb_flush_log_at_trx_commit"], original)

    def test_buffer_pool_apply_waits_for_requested_global_value(self) -> None:
        client = FakeClient()
        adapter = MySQLCostAdapter(client)
        adapter.apply({"innodb_buffer_pool_size": 335544320})
        self.assertEqual(client.globals["innodb_buffer_pool_size"], 335544320)


class OptimizerIntegrationTest(unittest.TestCase):
    def test_validate_config_accepts_plan_physics_with_r0_baseline(self) -> None:
        config = {
            "capability": {"cpu": 10.0, "memory": 10.0, "io": 10.0},
            "cost_parameters": {
                "active": ["io_block_read_cost"],
                "ranges": {
                    "io_block_read_cost": {
                        "min_multiplier": 0.5,
                        "max_multiplier": 2.0,
                    }
                },
            },
            "optimizer": {"max_iter": 1},
            "estimator": {
                "type": "plan_physics",
                "feature_source": {
                    "queries": [
                        {"query_id": "q1", "sql": "SELECT 1", "weight": 1}
                    ]
                },
                "baseline_real": {
                    "cpu": 3.5,
                    "memory": 2.3e9,
                    "io": 45e6,
                },
            },
        }

        validate_config(config)

    def test_optimizer_restores_original_costs_by_default(self) -> None:
        client = FakeClient()
        with tempfile.TemporaryDirectory() as temporary_dir:
            config = {
                "mysql": {"engine_name": "default", "device_type": 0},
                "capability": {"cpu": 10.0, "memory": 10.0, "io": 10.0},
                "cost_parameters": {
                    "active": ["io_block_read_cost", "row_evaluate_cost"],
                    "ranges": {
                        "io_block_read_cost": {"min_multiplier": 0.5, "max_multiplier": 2.0},
                        "row_evaluate_cost": {"min_multiplier": 0.5, "max_multiplier": 2.0},
                    },
                },
                "sensitivity_table": {
                    "io_block_read_cost": {"cpu": 0.1, "memory": 0.0, "io": -0.3},
                    "row_evaluate_cost": {"cpu": -0.2, "memory": 0.0, "io": -0.1},
                },
                "scoring": {"overload_threshold": 0.8},
                "optimizer": {"max_iter": 1, "patience": 1, "no_update_patience": 1},
                "estimator": {"type": "python", "callable": "tests.fake_estimator:estimate"},
                "output": {"directory": temporary_dir, "verbose": False},
                "commit_best": False,
            }
            validate_config(config)
            result = run_optimization(config, client=client)
            self.assertIsNotNone(result.optimized_params)
            self.assertTrue(all(value is None for value in client.raw.values()))
            self.assertTrue(Path(result.result_path).exists())
            self.assertTrue(Path(result.snapshot_path).exists())

    def test_optimizer_bad_baseline_is_measured_then_original_is_restored(self) -> None:
        client = FakeClient()
        with tempfile.TemporaryDirectory() as temporary_dir:
            config = {
                "mysql": {"engine_name": "default", "device_type": 0},
                "capability": {"cpu": 10.0, "memory": 10.0, "io": 10.0},
                "cost_parameters": {
                    "active": ["io_block_read_cost"],
                    "baseline_values": {"io_block_read_cost": 10.0},
                    "ranges": {"io_block_read_cost": {"min_value": .01, "max_value": 10.0}},
                },
                "sensitivity_table": {"io_block_read_cost": {"cpu": .1, "memory": 0, "io": -.3}},
                "scoring": {"overload_threshold": .8},
                "optimizer": {"max_iter": 1, "patience": 1, "no_update_patience": 1},
                "estimator": {"type": "python", "callable": "tests.fake_estimator:estimate"},
                "output": {"directory": temporary_dir, "verbose": False},
                "commit_best": False,
            }
            result = run_optimization(config, client=client)
            self.assertEqual(result.initial_params["io_block_read_cost"], 10.0)
            self.assertTrue(all(value is None for value in client.raw.values()))

    def test_mysql_process_monitor_with_explicit_pid(self) -> None:
        resource = run_and_monitor(
            ["python", "-c", "import time; time.sleep(0.12)"],
            pids=[os.getpid()],
            interval_sec=0.05,
            warmup_samples=0,
        )
        self.assertEqual(
            set(resource),
            {"cpu", "memory", "io", "measurement_elapsed_sec", "stability"},
        )
        self.assertGreaterEqual(resource["measurement_elapsed_sec"], 0.0)
        self.assertIsInstance(resource["stability"], dict)
        self.assertGreater(resource["memory"], 0.0)


class MatrixOrchestrationTest(unittest.TestCase):
    def test_three_resource_scenarios_and_both_algorithms(self) -> None:
        endpoints = {}

        def client_factory(mysql_cfg):
            endpoint = int(mysql_cfg["port"])
            return endpoints.setdefault(endpoint, FakeClient())

        scenarios = {
            name: {
                "benchmarks": [name],
                "capability": {"cpu": 10.0, "memory": 10.0, "io": 10.0},
                "scoring": {"overload_threshold": 0.8, "score_mode": "threshold"},
                "optimizer": {"max_iter": 1, "patience": 1, "no_update_patience": 1},
            }
            for name in ("cpu", "memory", "io")
        }
        benchmarks = {
            name: {
                "mysql": {"port": 3400 + index, "engine_name": "default"},
                "search_estimator": {
                    "type": "python",
                    "callable": "tests.matrix_estimators:estimate",
                },
                "validation_estimator": {
                    "type": "python",
                    "callable": "tests.matrix_estimators:estimate",
                },
            }
            for index, name in enumerate(("cpu", "memory", "io"))
        }
        config = {
            "cost_parameters": {
                "active": ["io_block_read_cost", "row_evaluate_cost"],
                "ranges": {
                    "io_block_read_cost": {"min_multiplier": 0.5, "max_multiplier": 2.0},
                    "row_evaluate_cost": {"min_multiplier": 0.5, "max_multiplier": 2.0},
                },
            },
            "sensitivity_table": {
                "io_block_read_cost": {"cpu": 0.1, "memory": 0.0, "io": -0.3},
                "row_evaluate_cost": {"cpu": -0.2, "memory": 0.0, "io": -0.1},
            },
            "scenarios": scenarios,
            "benchmarks": benchmarks,
            "workflow": {
                "algorithms": ["gradient", "bruteforce"],
                "top_k": 15,
                "max_search_candidates": 15,
                "bruteforce": {
                    "grid_points": 15,
                    "combination_max_size": 2,
                    "combination_values_per_param": 2,
                },
                "quality_gate": {
                    "min_samples": 15,
                    "max_relative_error_p95": 0.01,
                    "max_relative_error_max": 0.01,
                    "min_r2": 0.99,
                    "max_pairwise_inversion_rate": 0.0,
                    "score_tie_margin": 0.0,
                },
            },
        }
        with tempfile.TemporaryDirectory() as temporary_dir:
            summary = MySQLTestOrchestrator(
                config,
                client_factory=client_factory,
            ).run(Path(temporary_dir), max_parallel=3)
            self.assertEqual(summary["status"], "completed")
            self.assertEqual(summary["task_count"], 3)
            self.assertTrue(summary["all_quality_gates_passed"])
            for task in summary["tasks"]:
                self.assertEqual(
                    {item["algorithm"] for item in task["algorithms"]},
                    {"gradient", "bruteforce"},
                )
            self.assertTrue(all(all(value is None for value in client.raw.values()) for client in endpoints.values()))

    def test_top15_gate_rejects_reversed_order(self) -> None:
        rows = []
        for index in range(15):
            rows.append(
                {
                    "predicted_resource": {"cpu": index + 1, "memory": index + 1, "io": index + 1},
                    "actual_resource": {"cpu": index + 1, "memory": index + 1, "io": index + 1},
                    "predicted_score": float(index),
                    "actual_score": float(14 - index),
                }
            )
        gate = evaluate_top15_gate(rows, {"max_pairwise_inversion_rate": 0.10})
        self.assertFalse(gate["passed"])
        self.assertEqual(gate["top15_ordering"]["inversion_rate"], 1.0)


class FullGridReplayTest(unittest.TestCase):
    def test_all_factorial_points_are_replayed_and_restored(self) -> None:
        client = FakeClient()
        config = {
            "cost_parameters": {
                "active": ["io_block_read_cost", "row_evaluate_cost"],
                "ranges": {
                    "io_block_read_cost": {"min_multiplier": 0.5, "max_multiplier": 2.0},
                    "row_evaluate_cost": {"min_multiplier": 0.5, "max_multiplier": 2.0},
                },
            },
            "scenarios": {
                "cpu": {
                    "benchmarks": ["h"],
                    "capability": {"cpu": 10.0, "memory": 10.0, "io": 10.0},
                    "scoring": {"score_mode": "threshold", "overload_threshold": 0.8},
                }
            },
            "benchmarks": {
                "h": {
                    "mysql": {"port": 3500, "engine_name": "default"},
                    "search_estimator": {
                        "type": "python",
                        "callable": "tests.matrix_estimators:estimate",
                    },
                    "validation_estimator": {
                        "type": "python",
                        "callable": "tests.matrix_estimators:estimate",
                    },
                }
            },
            "workflow": {
                "quality_gate": {
                    "min_samples": 15,
                    "score_tie_margin": 0.0,
                    "max_pairwise_inversion_rate": 0.0,
                },
                "full_replay": {
                    "grid_points": 3,
                    "predict_before_replay": True,
                    "checkpoint_every": 1,
                    "commit_best": False,
                },
            },
        }
        with tempfile.TemporaryDirectory() as temporary_dir:
            output = Path(temporary_dir)
            controller = MySQLFullGridReplay(config, client_factory=lambda _: client)
            prepared = controller.run(output, prepare_only=True)
            self.assertEqual(prepared["total_real_replays"], 9)
            summary = controller.run(output, max_parallel=1)
            self.assertEqual(summary["status"], "completed")
            task = summary["tasks"][0]
            self.assertEqual(task["full_grid_candidate_count"], 9)
            self.assertEqual(task["attempted_this_run"], 9)
            self.assertEqual(task["unique_success_count"], 9)
            results_path = Path(task["results_path"])
            self.assertEqual(len(results_path.read_text(encoding="utf-8").splitlines()), 9)
            self.assertTrue(all(value is None for value in client.raw.values()))
            resumed = controller.run(output, max_parallel=1, resume=True)
            self.assertEqual(resumed["total_attempted_this_run"], 0)
            self.assertEqual(len(results_path.read_text(encoding="utf-8").splitlines()), 9)

    def test_full_grid_shard_count_is_exact(self) -> None:
        config = {
            "cost_parameters": {
                "active": ["io_block_read_cost", "row_evaluate_cost"],
                "ranges": {},
            },
            "scenarios": {
                "io": {
                    "benchmarks": ["h"],
                    "capability": {"cpu": 1, "memory": 1, "io": 1},
                    "scoring": {},
                }
            },
            "benchmarks": {"h": {"mysql": {"port": 3501}}},
            "workflow": {"full_replay": {"grid_points": 3}},
        }
        with tempfile.TemporaryDirectory() as temporary_dir:
            summary = MySQLFullGridReplay(config).run(
                Path(temporary_dir),
                shard_count=2,
                shard_index=1,
                prepare_only=True,
            )
            self.assertEqual(summary["tasks"][0]["full_grid_candidate_count"], 9)
            self.assertEqual(summary["tasks"][0]["this_shard_candidate_count"], 4)


if __name__ == "__main__":
    unittest.main()

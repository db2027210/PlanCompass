from __future__ import annotations

import fcntl
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmark_estimator import CommandResourceEstimator
from experiment_matrix_mysql import LogicalRun, evaluate_acceptance, load_manifest
from generate_mysql_gd_configs import build_config
from run_mysql_gd_matrix import (
    Worker, apply_calibration_start_costs, apply_hybench_client_overrides,
    apply_hybench_query_override, apply_optimizer_step_overrides,
    apply_capability_overrides, apply_non_hybench_load_overrides, apply_parameter_profile,
    apply_workload_profile, bind_config,
    claim_compatible, dispatch_worker_tasks, initial_buffer_pool_size, load_workers, set_buffer_pool,
    is_worker_unavailable_error,
)


class DynamicMatrixSchedulerTest(unittest.TestCase):

    def test_xp_q9_pool_topk_uses_one_64m_chunk_step(self):
        run = LogicalRun("A30", "hybench_xp", "io", 1, "hybench_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "xp_io_q9_pool_topk")
        costs = config["cost_parameters"]
        self.assertEqual(costs["baseline_values"]["innodb_buffer_pool_size"], 256 * 1024 ** 2)
        self.assertEqual(costs["start_values"]["innodb_buffer_pool_size"], 320 * 1024 ** 2)
        self.assertEqual(costs["start_values"]["io_block_read_cost"], 100)
        self.assertEqual(config["sensitivity_table"]["innodb_buffer_pool_size"]["io"], 0)

    def test_explicit_buffer_pool_baseline_wins_over_generic_setup(self):
        config = {"cost_parameters": {"baseline_values": {
            "innodb_buffer_pool_size": 2 * 1024**3,
        }}}
        self.assertEqual(
            initial_buffer_pool_size(config, "job", "replicated", 256),
            2 * 1024**3,
        )

    def test_xp_setup_pool_is_used_without_explicit_baseline(self):
        config = {"cost_parameters": {"baseline_values": {}}}
        self.assertEqual(
            initial_buffer_pool_size(config, "hybench_xp", "replicated", 256),
            256 * 1024**2,
        )

    def test_worker_unavailable_error_classifies_connection_loss_only(self):
        self.assertTrue(is_worker_unavailable_error(
            "ERROR 2003 (HY000): Can't connect to MySQL server on 127.0.0.1",
        ))
        self.assertTrue(is_worker_unavailable_error("Communications link failure: connection refused"))
        self.assertFalse(is_worker_unavailable_error("benchmark measurement quorum failed: qps=0"))

    @patch("run_mysql_gd_matrix.fcntl.flock")
    def test_unavailable_worker_stops_before_draining_queue(self, flock):
        worker = Worker("h1", frozenset({"hybench_xp"}), 57755, 111, "hybench")
        tasks = [
            LogicalRun("A30", "hybench_xp", "io", 1, "hybench_worker"),
            LogicalRun("B9", "hybench_xp", "io", 1, "hybench_worker"),
        ]

        def claim(_worker):
            return tasks.pop(0) if tasks else None

        def execute(_worker, run):
            return {
                "claimed_runs": [run.run_id], "stop_worker": True,
                "stop_reason": "worker_unavailable",
            }

        with tempfile.TemporaryDirectory() as directory:
            result = dispatch_worker_tasks(
                worker, claim, execute, lock_root=Path(directory),
            )

        self.assertEqual(result["claimed_runs"], ["A30-r1"])
        self.assertEqual(result["stop_reason"], "worker_unavailable")
        self.assertEqual([run.run_id for run in tasks], ["B9-r1"])
        self.assertEqual(len(flock.call_args_list), 2)

    @patch("run_mysql_gd_matrix.fcntl.flock")
    def test_cross_process_worker_lock_is_released_between_tasks(self, flock):
        worker = Worker("h1", frozenset({"hybench_tp"}), 57751, 111, "hybench")
        tasks = [
            LogicalRun("A28", "hybench_tp", "cpu", 1, "hybench_worker"),
            LogicalRun("B7", "hybench_tp", "cpu", 2, "hybench_worker"),
        ]
        executed = []

        def claim(_worker):
            return tasks.pop(0) if tasks else None

        def execute(_worker, run):
            executed.append(run.run_id)
            return {"claimed_runs": [run.run_id]}

        with tempfile.TemporaryDirectory() as directory:
            result = dispatch_worker_tasks(
                worker, claim, execute, lock_root=Path(directory),
            )

        self.assertEqual(executed, ["A28-r1", "B7-r2"])
        self.assertEqual(result["claimed_runs"], executed)
        self.assertEqual(
            [call.args[1] for call in flock.call_args_list],
            [
                fcntl.LOCK_EX, fcntl.LOCK_UN,
                fcntl.LOCK_EX, fcntl.LOCK_UN,
                fcntl.LOCK_EX, fcntl.LOCK_UN,
            ],
        )

    def test_capability_override_records_prior_machine_calibration(self):
        config = {"capability": {"cpu": 4.0, "memory": 8.0, "io": 100.0}, "dispatch": {}}
        apply_capability_overrides(config, cpu=.94)
        self.assertEqual(config["capability"]["cpu"], .94)
        self.assertEqual(config["dispatch"]["capability_overrides"], {"cpu": .94})
    @patch("benchmark_estimator.subprocess.run")
    def test_command_estimator_uses_median_of_real_repeats(self, run):
        payloads = [
            {"resource": {"cpu": 9.0, "memory": 3.0, "io": 7.0}, "qps": 10.0},
            {"resource": {"cpu": 1.0, "memory": 2.0, "io": 5.0}, "qps": 30.0},
            {"resource": {"cpu": 3.0, "memory": 1.0, "io": 6.0}, "qps": 20.0},
        ]
        run.side_effect = [
            type("Result", (), {"returncode": 0, "stdout": json.dumps(payload) + "\n", "stderr": ""})()
            for payload in payloads
        ]

        class Adapter:
            def apply(self, params):
                self.params = params

        estimator = CommandResourceEstimator(Adapter(), ["benchmark"], measurement_repeats=3)
        resource = estimator(None, {"row_evaluate_cost": .1})

        self.assertEqual(resource, {"cpu": 3.0, "memory": 2.0, "io": 6.0})
        self.assertEqual(estimator.measurements[0]["qps"], 20.0)
        self.assertEqual(len(estimator.last_metadata["measurement_replicates"]), 3)

    @patch("benchmark_estimator.subprocess.run")
    def test_command_estimator_tolerates_one_failed_repeat_with_quorum(self, run):
        ok = lambda qps: type("Result", (), {
            "returncode": 0,
            "stdout": json.dumps({"resource": {"cpu": qps, "memory": 2.0, "io": 3.0}, "qps": qps}) + "\n",
            "stderr": "",
        })()
        failed = type("Result", (), {"returncode": 255, "stdout": "", "stderr": "transient warmup failure"})()
        run.side_effect = [ok(10.0), failed, ok(20.0)]

        class Adapter:
            def apply(self, params):
                pass

        estimator = CommandResourceEstimator(
            Adapter(), ["benchmark"], measurement_repeats=3, measurement_min_success=2,
        )
        resource = estimator(None, {"row_evaluate_cost": .1})

        self.assertEqual(resource["cpu"], 15.0)
        self.assertEqual(estimator.last_metadata["measurement_successes"], 2)
        self.assertEqual(estimator.last_metadata["measurement_failures"][0]["repeat_index"], 2)

    @patch("benchmark_estimator.subprocess.run")
    def test_command_estimator_rejects_when_repeat_quorum_is_not_met(self, run):
        failed = type("Result", (), {"returncode": 255, "stdout": "", "stderr": "failed"})()
        ok = type("Result", (), {
            "returncode": 0,
            "stdout": json.dumps({"resource": {"cpu": 1.0, "memory": 2.0, "io": 3.0}, "qps": 1.0}) + "\n",
            "stderr": "",
        })()
        run.side_effect = [failed, ok, failed]

        class Adapter:
            def apply(self, params):
                pass

        estimator = CommandResourceEstimator(
            Adapter(), ["benchmark"], measurement_repeats=3, measurement_min_success=2,
        )
        with self.assertRaisesRegex(RuntimeError, "quorum failed"):
            estimator(None, {"row_evaluate_cost": .1})

    @patch("run_mysql_gd_matrix.time.sleep")
    @patch("run_mysql_gd_matrix.subprocess.run")
    def test_buffer_pool_resize_polls_until_mysql_applies_request(self, run, sleep):
        requested = 256 * 1024 * 1024
        run.side_effect = [
            type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
            type("Result", (), {"returncode": 0, "stdout": "1073741824\n", "stderr": ""})(),
            type("Result", (), {"returncode": 0, "stdout": f"{requested}\n", "stderr": ""})(),
        ]
        config = {"mysql": {"mysql_binary": "mysql", "host": "127.0.0.1", "port": 57755, "user": "root", "password": "CHANGE_ME"}}

        set_buffer_pool(config, requested, timeout_sec=5, poll_interval_sec=.01)

        self.assertEqual(run.call_count, 3)
        sleep.assert_called_once()

    @patch("run_mysql_gd_matrix.subprocess.run")
    def test_buffer_pool_resize_fails_fast_when_mysql_rejects_request(self, run):
        run.side_effect = [
            type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
            type("Result", (), {
                "returncode": 0,
                "stdout": "1073741824\nInnodb_buffer_pool_resize_status\tFailed to validate requested buffer pool size.\n",
                "stderr": "",
            })(),
        ]
        config = {"mysql": {"mysql_binary": "mysql", "host": "127.0.0.1", "port": 57755, "user": "root", "password": "CHANGE_ME"}}

        with self.assertRaisesRegex(RuntimeError, "resize rejected"):
            set_buffer_pool(config, 256 * 1024 * 1024)

        self.assertEqual(run.call_count, 2)

    def test_oldest_compatible_task_is_claimed_without_fixed_lane(self):
        pending = [
            LogicalRun("A26", "job", "memory", 1, "job_worker"),
            LogicalRun("A25", "hybench_ap", "memory", 1, "hybench_worker"),
            LogicalRun("A28", "hybench_tp", "cpu", 1, "hybench_worker"),
        ]
        hybench = Worker("h1", frozenset({"hybench_ap", "hybench_tp", "hybench_xp"}), 57751, 111, "hybench")
        self.assertEqual(claim_compatible(pending, hybench).run_id, "A25-r1")
        self.assertEqual([run.run_id for run in pending], ["A26-r1", "A28-r1"])
        self.assertEqual(claim_compatible(pending, hybench).run_id, "A28-r1")

    def test_worker_binding_rewrites_every_runtime_endpoint(self):
        run = LogicalRun("B7", "hybench_tp", "cpu", 2, "hybench_worker")
        base = build_config(run, {name: 1 for name in ("hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc")})
        worker = Worker("h4", frozenset({"hybench_ap", "hybench_tp", "hybench_xp"}), 57756, 999, "hybench")
        with tempfile.TemporaryDirectory() as directory:
            bound = bind_config(base, run, worker, Path(directory))
        command = bound["estimator"]["command"]
        self.assertEqual(bound["mysql"]["port"], 57756)
        self.assertEqual(command[command.index("--port") + 1], "57756")
        self.assertEqual(command[command.index("--mysqld-pid") + 1], "999")
        self.assertIn("B7-r2", bound["output"]["directory"])

    def test_stable_profile_raises_duration_and_scenario_concurrency(self):
        run = LogicalRun("B8", "hybench_ap", "memory", 1, "hybench_worker")
        base = build_config(run, {name: 1 for name in ("hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc")})
        worker = Worker("h1", frozenset({"hybench_ap", "hybench_tp", "hybench_xp"}), 57751, 111, "hybench")
        bound = bind_config(base, run, worker, Path("/tmp/retry"))
        apply_workload_profile(bound, run.benchmark, "stable")
        command = bound["estimator"]["command"]
        self.assertEqual(command[command.index("--duration-sec") + 1], "30")
        self.assertEqual(command[command.index("--warmup-duration-sec") + 1], "10")
        self.assertEqual(command[command.index("--ap-clients") + 1], "8")

    def test_stabilized_profile_uses_long_window_and_avoids_tpcc_overload(self):
        run = LogicalRun("A29", "tpcc", "cpu", 1, "tpcc_worker")
        base = build_config(run, {name: 1 for name in ("hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc")})
        worker = Worker("t1", frozenset({"tpcc"}), 57750, 111, "tpcc10")
        bound = bind_config(base, run, worker, Path("/tmp/retry"))
        apply_workload_profile(bound, run.benchmark, "stabilized")
        command = bound["estimator"]["command"]
        self.assertEqual(command[command.index("--duration-sec") + 1], "60")
        self.assertEqual(command[command.index("--warmup-duration-sec") + 1], "30")
        self.assertEqual(command[command.index("--terminals") + 1], "16")

    def test_replicated_profile_keeps_stabilized_load_with_short_windows(self):
        run = LogicalRun("A29", "tpcc", "cpu", 1, "tpcc_worker")
        base = build_config(run, {name: 1 for name in ("hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc")})
        worker = Worker("t1", frozenset({"tpcc"}), 57750, 111, "tpcc10")
        bound = bind_config(base, run, worker, Path("/tmp/retry"))
        apply_workload_profile(bound, run.benchmark, "replicated")
        command = bound["estimator"]["command"]
        self.assertEqual(command[command.index("--duration-sec") + 1], "30")
        self.assertEqual(command[command.index("--warmup-duration-sec") + 1], "10")
        self.assertEqual(command[command.index("--terminals") + 1], "16")

    def test_replicated_xp_varies_seed_to_avoid_cross_candidate_cache_hits(self):
        run = LogicalRun("B9", "hybench_xp", "io", 2, "hybench_worker")
        base = build_config(run, {name: 1 for name in ("hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc")})
        worker = Worker("xp", frozenset({"hybench_xp"}), 57755, 111, "hybench")
        bound = bind_config(base, run, worker, Path("/tmp/retry"))
        apply_workload_profile(bound, run.benchmark, "replicated")
        self.assertIn("--vary-random-seed", bound["estimator"]["command"])

    def test_hybench_client_override_is_recorded_without_changing_profile(self):
        run = LogicalRun("B8", "hybench_ap", "memory", 1, "hybench_worker")
        base = build_config(run, {name: 1 for name in ("hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc")})
        worker = Worker("h2", frozenset({"hybench_ap", "hybench_tp"}), 57754, 111, "hybench")
        bound = bind_config(base, run, worker, Path("/tmp/retry"))
        apply_workload_profile(bound, run.benchmark, "replicated")
        apply_hybench_client_overrides(bound, run.benchmark, ap_clients=32, tp_clients=14)
        command = bound["estimator"]["command"]
        self.assertEqual(command[command.index("--ap-clients") + 1], "32")
        self.assertEqual(bound["dispatch"]["workload_profile"], "replicated")
        self.assertEqual(bound["dispatch"]["workload_overrides"], {"ap_clients": 32})

    def test_hybench_xp_client_overrides_are_independent(self):
        run = LogicalRun("E30", "hybench_xp", "none", 1, "hybench_worker")
        base = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        worker = Worker("xp", frozenset({"hybench_xp"}), 57755, 111, "hybench")
        bound = bind_config(base, run, worker, Path("/tmp/retry"))
        apply_workload_profile(bound, run.benchmark, "replicated")
        apply_hybench_client_overrides(
            bound, run.benchmark, xap_clients=7, xtp_clients=14,
        )
        command = bound["estimator"]["command"]
        self.assertEqual(command[command.index("--xap-clients") + 1], "7")
        self.assertEqual(command[command.index("--xtp-clients") + 1], "14")
        self.assertEqual(bound["dispatch"]["workload_overrides"], {
            "xap_clients": 7, "xtp_clients": 14,
        })

    def test_hybench_ap_query_sequence_is_recorded(self):
        run = LogicalRun("E25", "hybench_ap", "none", 1, "hybench_worker")
        base = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        worker = Worker("h1", frozenset({"hybench_ap"}), 57751, 111, "hybench")
        bound = bind_config(base, run, worker, Path("/tmp/retry"))
        apply_hybench_query_override(
            bound, run.benchmark, "11, 9",
            prewarm_sql="SELECT SUM(id) FROM transfer",
        )
        command = bound["estimator"]["command"]
        self.assertEqual(command[command.index("--ap-query-sequence") + 1], "11,9")
        self.assertEqual(
            bound["dispatch"]["workload_overrides"]["ap_query_sequence"], "11,9",
        )
        self.assertEqual(
            command[command.index("--prewarm-sql") + 1],
            "SELECT SUM(id) FROM transfer",
        )
        self.assertEqual(
            bound["dispatch"]["workload_overrides"]["hybench_prewarm_sql"],
            "SELECT SUM(id) FROM transfer",
        )

    def test_hybench_xp_rejects_ap_query_sequence(self):
        run = LogicalRun("E26", "hybench_xp", "none", 1, "hybench_worker")
        base = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        worker = Worker("xp", frozenset({"hybench_xp"}), 57755, 111, "hybench")
        bound = bind_config(base, run, worker, Path("/tmp/retry"))
        with self.assertRaisesRegex(ValueError, "native uniform-random IQ1-IQ7"):
            apply_hybench_query_override(bound, run.benchmark, "9")

    def test_hybench_tp17_and_spin_profile_are_recorded(self):
        run = LogicalRun("B7", "hybench_tp", "cpu", 2, "hybench_worker")
        base = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        worker = Worker("h2", frozenset({"hybench_tp"}), 57754, 111, "hybench")
        bound = bind_config(base, run, worker, Path("/tmp/retry"))
        apply_hybench_query_override(bound, run.benchmark, None, tp_transaction_id=17)
        apply_parameter_profile(bound, "spin_wait")
        command = bound["estimator"]["command"]
        self.assertEqual(command[command.index("--tp-transaction-id") + 1], "17")
        self.assertEqual(bound["cost_parameters"]["active"], [
            "innodb_spin_wait_delay", "innodb_sync_spin_loops",
        ])
        self.assertEqual(bound["dispatch"]["workload_overrides"]["tp_transaction_id"], 17)

    def test_spin_wait_800_replays_observed_tp_candidate(self):
        run = LogicalRun("A28", "hybench_tp", "cpu", 1, "hybench_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "spin_wait_800")
        self.assertEqual(config["cost_parameters"]["start_values"], {
            "innodb_spin_wait_delay": 800.0,
            "innodb_sync_spin_loops": 800.0,
        })

    def test_spin_wait_min_matches_screened_endpoint(self):
        run = LogicalRun("A28", "hybench_tp", "cpu", 1, "hybench_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "spin_wait_min")
        start = config["cost_parameters"]["start_values"]
        self.assertEqual(start["innodb_spin_wait_delay"], 6)
        self.assertEqual(start["innodb_sync_spin_loops"], 30)

    def test_tp_cpu_hybrid_topk_combines_flush_and_low_spin(self):
        run = LogicalRun("A28", "hybench_tp", "cpu", 1, "hybench_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "tp_cpu_hybrid_topk")
        costs = config["cost_parameters"]
        self.assertEqual(costs["baseline_values"]["innodb_flush_log_at_trx_commit"], 1)
        self.assertEqual(costs["start_values"]["innodb_flush_log_at_trx_commit"], 2)
        self.assertEqual(costs["start_values"]["innodb_spin_wait_delay"], 6)
        self.assertEqual(costs["start_values"]["innodb_sync_spin_loops"], 30)
        self.assertEqual(config["sensitivity_table"]["innodb_spin_wait_delay"]["cpu"], 0)

    def test_cpu_hybrid_800_topk_uses_moderate_spin_endpoint(self):
        run = LogicalRun("A28", "hybench_tp", "cpu", 1, "hybench_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "cpu_hybrid_800_topk")
        costs = config["cost_parameters"]
        self.assertEqual(costs["baseline_values"], {
            "innodb_flush_log_at_trx_commit": 1.0,
            "innodb_spin_wait_delay": 1000.0,
            "innodb_sync_spin_loops": 1000.0,
        })
        self.assertEqual(costs["start_values"], {
            "innodb_flush_log_at_trx_commit": 2.0,
            "innodb_spin_wait_delay": 800.0,
            "innodb_sync_spin_loops": 800.0,
        })
        self.assertEqual(config["sensitivity_table"]["innodb_spin_wait_delay"]["cpu"], 0)

    def test_cpu_hybrid_350_topk_uses_empirical_bracket_endpoint(self):
        run = LogicalRun("A28", "hybench_tp", "cpu", 1, "hybench_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "cpu_hybrid_350_topk")
        costs = config["cost_parameters"]
        self.assertEqual(costs["start_values"], {
            "innodb_flush_log_at_trx_commit": 2.0,
            "innodb_spin_wait_delay": 350.0,
            "innodb_sync_spin_loops": 350.0,
        })
        self.assertEqual(config["sensitivity_table"]["innodb_spin_wait_delay"]["cpu"], 0)

    def test_cpu_hybrid_refined_endpoints_are_frozen(self):
        run = LogicalRun("B7", "hybench_tp", "cpu", 1, "hybench_worker")
        for profile, endpoint in (
            ("cpu_hybrid_280_topk", 280.0),
            ("cpu_hybrid_250_topk", 250.0),
        ):
            config = build_config(run, {name: 1 for name in (
                "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
            )})
            config["dispatch"] = {}
            apply_parameter_profile(config, profile)
            self.assertEqual(
                config["cost_parameters"]["start_values"]["innodb_spin_wait_delay"],
                endpoint,
            )
            self.assertEqual(config["sensitivity_table"]["innodb_spin_wait_delay"]["cpu"], 0)

    def test_job_concurrency_override_is_recorded(self):
        run = LogicalRun("E26", "job", "none", 1, "job_worker")
        base = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        worker = Worker("job", frozenset({"job"}), 57752, 111, "job")
        bound = bind_config(base, run, worker, Path("/tmp/retry"))
        apply_workload_profile(bound, run.benchmark, "replicated")
        apply_non_hybench_load_overrides(bound, run.benchmark, job_concurrency=4)
        command = bound["estimator"]["command"]
        self.assertEqual(command[command.index("--concurrency") + 1], "4")
        self.assertEqual(
            bound["dispatch"]["workload_overrides"]["job_concurrency"], 4,
        )

    def test_tpch_prewarm_override_is_recorded(self):
        run = LogicalRun("A27", "tpch", "memory", 1, "tpch_worker")
        base = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        worker = Worker("tpch", frozenset({"tpch"}), 57753, 222, "tpch")
        bound = bind_config(base, run, worker, Path("/tmp/retry"))
        apply_non_hybench_load_overrides(
            bound,
            run.benchmark,
            tpch_terminals=16,
            tpch_prewarm_sql="SELECT SUM(l_orderkey) FROM lineitem",
        )
        command = bound["estimator"]["command"]
        self.assertEqual(command[command.index("--terminals") + 1], "16")
        self.assertEqual(
            command[command.index("--prewarm-sql") + 1],
            "SELECT SUM(l_orderkey) FROM lineitem",
        )
        self.assertEqual(
            bound["dispatch"]["workload_overrides"]["tpch_prewarm_sql"],
            "SELECT SUM(l_orderkey) FROM lineitem",
        )

    def test_tpcc_stocklevel_weight_override_is_recorded(self):
        run = LogicalRun("A29", "tpcc", "cpu", 1, "tpcc_worker")
        base = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        worker = Worker("tpcc", frozenset({"tpcc"}), 57750, 111, "tpcc10")
        bound = bind_config(base, run, worker, Path("/tmp/retry"))
        apply_non_hybench_load_overrides(
            bound, run.benchmark, tpcc_terminals=1, tpcc_weights="0,0,0,0,100",
        )
        command = bound["estimator"]["command"]
        self.assertEqual(command[command.index("--terminals") + 1], "1")
        self.assertEqual(command[command.index("--tpcc-weights") + 1], "0,0,0,0,100")
        self.assertEqual(
            bound["dispatch"]["workload_overrides"]["tpcc_weights"], "0,0,0,0,100",
        )

    def test_optimizer_step_override_keeps_gradient_framework_and_shrinks_jump(self):
        run = LogicalRun("B9", "hybench_xp", "io", 2, "hybench_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_optimizer_step_overrides(
            config, eta=.5, min_delta_pct=1.0, max_delta_pct=3.0,
            max_delta_ratio=.03, max_iter=2,
        )
        self.assertEqual(config["optimizer"]["eta"], .5)
        self.assertEqual(config["optimizer"]["min_param_delta_rel_pct"], 1.0)
        self.assertEqual(config["optimizer"]["max_param_delta_rel_pct"], 3.0)
        self.assertEqual(config["optimizer"]["max_param_delta_ratio"], .03)
        self.assertEqual(config["optimizer"]["max_iter"], 2)
        self.assertIn("optimizer_step_overrides", config["dispatch"])

    def test_wide_read_cost_profile_sets_bad_baseline_and_absolute_range(self):
        run = LogicalRun("E25", "hybench_ap", "none", 1, "hybench_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "wide_read_cost")
        self.assertEqual(config["cost_parameters"]["active"], [
            "io_block_read_cost", "memory_block_read_cost",
        ])
        self.assertEqual(config["cost_parameters"]["baseline_values"], {
            "io_block_read_cost": 100.0, "memory_block_read_cost": 100.0,
        })
        self.assertEqual(
            config["cost_parameters"]["ranges"]["io_block_read_cost"]["min_value"], .01,
        )
        self.assertEqual(config["dispatch"]["parameter_profile"], "wide_read_cost")

    def test_tpcc_flush_profile_uses_same_gradient_config(self):
        run = LogicalRun("A29", "tpcc", "cpu", 1, "tpcc_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "tpcc_flush")
        self.assertEqual(config["cost_parameters"]["active"], [
            "innodb_flush_log_at_trx_commit",
        ])
        self.assertEqual(
            config["cost_parameters"]["baseline_values"]["innodb_flush_log_at_trx_commit"], 1,
        )
        self.assertEqual(
            config["cost_parameters"]["start_values"]["innodb_flush_log_at_trx_commit"], 2,
        )
        self.assertIn("innodb_flush_log_at_trx_commit", config["sensitivity_table"])

    def test_memory_buffer_profile_has_high_bad_baseline(self):
        run = LogicalRun("B8", "hybench_ap", "memory", 2, "hybench_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "memory_buffers")
        self.assertEqual(config["cost_parameters"]["active"], [
            "join_buffer_size", "sort_buffer_size",
        ])
        self.assertEqual(config["cost_parameters"]["baseline_values"]["join_buffer_size"], 67108864)
        self.assertEqual(config["cost_parameters"]["ranges"]["sort_buffer_size"]["min_value"], 262144)

    def test_cpu_hybrid_780_topk_uses_narrow_tpcc_bracket(self):
        run = LogicalRun("A29", "tpcc", "cpu", 1, "tpcc_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "cpu_hybrid_780_topk")
        costs = config["cost_parameters"]
        self.assertEqual(costs["baseline_values"]["innodb_spin_wait_delay"], 1000)
        self.assertEqual(costs["start_values"]["innodb_spin_wait_delay"], 780)
        self.assertEqual(costs["start_values"]["innodb_sync_spin_loops"], 780)
        for name in costs["active"]:
            self.assertEqual(config["sensitivity_table"][name]["cpu"], 0)

    def test_job_memory_topk_keeps_distinct_real_baseline_and_candidate(self):
        run = LogicalRun("A26", "job", "memory", 1, "job_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "job_memory_topk")
        self.assertEqual(config["cost_parameters"]["baseline_values"]["io_block_read_cost"], 10000)
        self.assertEqual(config["cost_parameters"]["start_values"]["io_block_read_cost"], .01)
        self.assertEqual(config["cost_parameters"]["start_values"]["read_rnd_buffer_size"], 4194304)

    def test_memory_pool_topk_targets_a_256_mib_persistent_drop(self):
        run = LogicalRun("A26", "job", "memory", 1, "job_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "memory_pool_topk")
        baseline = config["cost_parameters"]["baseline_values"]
        start = config["cost_parameters"]["start_values"]
        self.assertEqual(baseline["innodb_buffer_pool_size"], 2147483648)
        self.assertEqual(start["innodb_buffer_pool_size"], 1879048192)
        self.assertEqual(
            baseline["innodb_buffer_pool_size"] - start["innodb_buffer_pool_size"],
            268435456,
        )
        self.assertIn("innodb_buffer_pool_size", config["sensitivity_table"])

    def test_memory_deep_topk_combines_pool_and_session_memory(self):
        run = LogicalRun("A26", "job", "memory", 1, "job_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "memory_deep_topk")
        baseline = config["cost_parameters"]["baseline_values"]
        start = config["cost_parameters"]["start_values"]
        self.assertEqual(baseline["join_buffer_size"], 67108864)
        self.assertEqual(start["join_buffer_size"], 8388608)
        self.assertEqual(start["innodb_buffer_pool_size"], 1879048192)
        self.assertIn("read_buffer_size", config["sensitivity_table"])

    def test_xp_io_pool_topk_adds_64_mib_cache(self):
        run = LogicalRun("A30", "hybench_xp", "io", 1, "hybench_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "xp_io_pool_topk")
        baseline = config["cost_parameters"]["baseline_values"]["innodb_buffer_pool_size"]
        start = config["cost_parameters"]["start_values"]["innodb_buffer_pool_size"]
        self.assertEqual(baseline, 268435456)
        self.assertEqual(start - baseline, 67108864)
        self.assertLess(config["sensitivity_table"]["innodb_buffer_pool_size"]["io"], 0)

    def test_xp_io_pool_mild_topk_uses_one_chunk_step(self):
        run = LogicalRun("A30", "hybench_xp", "io", 1, "hybench_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "xp_io_pool_mild_topk")
        baseline = config["cost_parameters"]["baseline_values"]["innodb_buffer_pool_size"]
        start = config["cost_parameters"]["start_values"]["innodb_buffer_pool_size"]
        self.assertEqual(baseline, 268435456)
        self.assertEqual(start - baseline, 134217728)
        self.assertEqual(config["cost_parameters"]["ranges"]["innodb_buffer_pool_size"]["max_value"], 536870912)

    def test_xp_io_q9_pool336_topk_uses_midpoint_chunk(self):
        run = LogicalRun("A30", "hybench_xp", "io", 1, "hybench_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "xp_io_q9_pool336_topk")
        costs = config["cost_parameters"]
        self.assertEqual(costs["baseline_values"]["innodb_buffer_pool_size"], 256 * 1024 ** 2)
        self.assertEqual(costs["start_values"]["innodb_buffer_pool_size"], 336 * 1024 ** 2)
        for name in costs["active"]:
            self.assertEqual(config["sensitivity_table"][name]["io"], 0)

    def test_xp_io_q9_pool288_topk_uses_two_16m_chunks(self):
        run = LogicalRun("A30", "hybench_xp", "io", 1, "hybench_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "xp_io_q9_pool288_topk")
        costs = config["cost_parameters"]
        self.assertEqual(costs["baseline_values"]["innodb_buffer_pool_size"], 256 * 1024 ** 2)
        self.assertEqual(costs["start_values"]["innodb_buffer_pool_size"], 288 * 1024 ** 2)
        for name in costs["active"]:
            self.assertEqual(config["sensitivity_table"][name]["io"], 0)

    def test_xp_io_q9_small_odirect_topk_boundaries_are_aligned(self):
        run = LogicalRun("A30", "hybench_xp", "io", 1, "hybench_worker")
        for profile, pool_mb in (
            ("xp_io_q9_pool264_topk", 264),
            ("xp_io_q9_pool272_topk", 272),
            ("xp_io_q9_pool280_topk", 280),
        ):
            config = build_config(run, {name: 1 for name in (
                "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
            )})
            config["dispatch"] = {}
            apply_parameter_profile(config, profile)
            costs = config["cost_parameters"]
            self.assertEqual(costs["baseline_values"]["innodb_buffer_pool_size"], 256 * 1024 ** 2)
            self.assertEqual(costs["start_values"]["innodb_buffer_pool_size"], pool_mb * 1024 ** 2)
            for name in costs["active"]:
                self.assertEqual(config["sensitivity_table"][name]["io"], 0)

    def test_xp_io_q9_pool272_cost_screens_keep_pool_and_scale_costs(self):
        run = LogicalRun("A30", "hybench_xp", "io", 1, "hybench_worker")
        for profile, endpoint in (
            ("xp_io_q9_pool272_cost25_topk", 25),
            ("xp_io_q9_pool272_cost50_topk", 50),
            ("xp_io_q9_pool272_cost75_topk", 75),
        ):
            config = build_config(run, {name: 1 for name in (
                "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
            )})
            config["dispatch"] = {}
            apply_parameter_profile(config, profile)
            start = config["cost_parameters"]["start_values"]
            self.assertEqual(start["innodb_buffer_pool_size"], 272 * 1024 ** 2)
            self.assertEqual(start["io_block_read_cost"], endpoint)
            self.assertEqual(start["memory_block_read_cost"], endpoint)
            for name in config["cost_parameters"]["active"]:
                self.assertEqual(config["sensitivity_table"][name]["io"], 0)

    def test_xp_io_q9_pool362_topk_uses_interpolated_boundary(self):
        run = LogicalRun("A30", "hybench_xp", "io", 1, "hybench_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "xp_io_q9_pool362_topk")
        costs = config["cost_parameters"]
        self.assertEqual(costs["baseline_values"]["innodb_buffer_pool_size"], 256 * 1024 ** 2)
        self.assertEqual(costs["start_values"]["innodb_buffer_pool_size"], 362 * 1024 ** 2)
        for name in costs["active"]:
            self.assertEqual(config["sensitivity_table"][name]["io"], 0)
        self.assertEqual(config["sensitivity_table"]["innodb_buffer_pool_size"]["io"], 0)

    def test_xp_io_q9_pool360_topk_uses_realized_boundary(self):
        run = LogicalRun("A30", "hybench_xp", "io", 1, "hybench_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "xp_io_q9_pool360_topk")
        costs = config["cost_parameters"]
        self.assertEqual(costs["baseline_values"]["innodb_buffer_pool_size"], 256 * 1024 ** 2)
        self.assertEqual(costs["start_values"]["innodb_buffer_pool_size"], 360 * 1024 ** 2)
        for name in costs["active"]:
            self.assertEqual(config["sensitivity_table"][name]["io"], 0)

    def test_cpu_hybrid_785_topk_uses_interpolated_spin_candidate(self):
        run = LogicalRun("A29", "tpcc", "cpu", 1, "tpcc_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "cpu_hybrid_785_topk")
        start = config["cost_parameters"]["start_values"]
        self.assertEqual(start["innodb_flush_log_at_trx_commit"], 2)
        self.assertEqual(start["innodb_spin_wait_delay"], 785)
        self.assertEqual(start["innodb_sync_spin_loops"], 785)

    def test_ap_memory_topk_uses_qps_cost_candidate_and_moderate_buffer_drop(self):
        run = LogicalRun("A25", "hybench_ap", "memory", 1, "hybench_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "ap_memory_topk")
        self.assertEqual(config["cost_parameters"]["baseline_values"]["io_block_read_cost"], 100)
        self.assertEqual(config["cost_parameters"]["start_values"]["io_block_read_cost"], .01)
        self.assertEqual(config["cost_parameters"]["start_values"]["join_buffer_size"], 58720256)
        self.assertIn("read_rnd_buffer_size", config["sensitivity_table"])

    def test_ap_q11_plan_topk_matches_screened_pair(self):
        run = LogicalRun("E25", "hybench_ap", "none", 1, "hybench_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "ap_q11_plan_topk")
        costs = config["cost_parameters"]
        self.assertEqual(costs["baseline_values"]["io_block_read_cost"], 100)
        self.assertEqual(costs["start_values"]["io_block_read_cost"], .01)
        self.assertEqual(costs["baseline_values"]["join_buffer_size"], 67108864)
        self.assertEqual(costs["start_values"]["join_buffer_size"], 262144)
        self.assertNotIn("innodb_buffer_pool_size", costs["active"])

    def test_ap_q9_reverse_topk_matches_screened_direction(self):
        run = LogicalRun("E25", "hybench_ap", "none", 1, "hybench_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "ap_q9_reverse_topk")
        costs = config["cost_parameters"]
        self.assertEqual(costs["baseline_values"]["io_block_read_cost"], .01)
        self.assertEqual(costs["start_values"]["io_block_read_cost"], 100)
        self.assertEqual(config["sensitivity_table"]["io_block_read_cost"]["cpu"], 0)

    def test_ap_q9_smallpool_topk_uses_exact_single_instance_boundary(self):
        run = LogicalRun("A25", "hybench_ap", "memory", 1, "hybench_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "ap_q9_smallpool_topk")
        costs = config["cost_parameters"]
        self.assertEqual(costs["baseline_values"]["innodb_buffer_pool_size"], 1073741824)
        self.assertEqual(costs["start_values"]["innodb_buffer_pool_size"], 671088640)
        self.assertEqual(costs["start_values"]["io_block_read_cost"], 100)
        self.assertEqual(config["sensitivity_table"]["innodb_buffer_pool_size"]["memory"], 0)

    def test_ap_q9_pool960_prewarm_uses_64m_chunk_boundary(self):
        run = LogicalRun("A25", "hybench_ap", "memory", 1, "hybench_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "ap_q9_pool960_prewarm_topk")
        costs = config["cost_parameters"]
        self.assertEqual(costs["baseline_values"]["innodb_buffer_pool_size"], 1024 ** 3)
        self.assertEqual(costs["start_values"]["innodb_buffer_pool_size"], 960 * 1024 ** 2)
        self.assertEqual(costs["start_values"]["io_block_read_cost"], 100)
        self.assertEqual(config["sensitivity_table"]["innodb_buffer_pool_size"]["memory"], 0)

    def test_ap_q9_pool896_prewarm_moves_two_64m_chunks(self):
        run = LogicalRun("B8", "hybench_ap", "memory", 1, "hybench_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "ap_q9_pool896_prewarm_topk")
        costs = config["cost_parameters"]
        self.assertEqual(costs["baseline_values"]["innodb_buffer_pool_size"], 1024 ** 3)
        self.assertEqual(costs["start_values"]["innodb_buffer_pool_size"], 896 * 1024 ** 2)
        self.assertEqual(costs["start_values"]["io_block_read_cost"], 100)
        self.assertEqual(config["sensitivity_table"]["innodb_buffer_pool_size"]["memory"], 0)

    def test_ap_q9_pool864_prewarm_adds_targeted_32m_release(self):
        run = LogicalRun("B8", "hybench_ap", "memory", 2, "hybench_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "ap_q9_pool864_prewarm_topk")
        costs = config["cost_parameters"]
        self.assertEqual(costs["baseline_values"]["innodb_buffer_pool_size"], 1024 ** 3)
        self.assertEqual(costs["start_values"]["innodb_buffer_pool_size"], 864 * 1024 ** 2)
        self.assertEqual(costs["start_values"]["io_block_read_cost"], 100)
        self.assertEqual(config["sensitivity_table"]["innodb_buffer_pool_size"]["memory"], 0)

    def test_ap_q9_pool2g_prewarm_uses_proven_256m_release(self):
        run = LogicalRun("A25", "hybench_ap", "memory", 1, "hybench_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "ap_q9_pool2g_prewarm_topk")
        costs = config["cost_parameters"]
        self.assertEqual(costs["baseline_values"]["innodb_buffer_pool_size"], 2 * 1024 ** 3)
        self.assertEqual(costs["start_values"]["innodb_buffer_pool_size"], 1792 * 1024 ** 2)
        self.assertEqual(costs["baseline_values"]["io_block_read_cost"], .01)
        self.assertEqual(costs["start_values"]["io_block_read_cost"], 100)
        self.assertEqual(config["sensitivity_table"]["innodb_buffer_pool_size"]["memory"], 0)

    def test_job_prewarm_buffers_profile_uses_one_pool_chunk_and_small_sessions(self):
        run = LogicalRun("A26", "job", "memory", 1, "job_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "memory_prewarm_buffers_128m_topk")
        costs = config["cost_parameters"]
        self.assertEqual(costs["baseline_values"]["innodb_buffer_pool_size"], 2 * 1024 ** 3)
        self.assertEqual(costs["start_values"]["innodb_buffer_pool_size"], 1920 * 1024 ** 2)
        self.assertEqual(costs["baseline_values"]["join_buffer_size"], 128 * 1024 ** 2)
        self.assertEqual(costs["start_values"]["join_buffer_size"], 256 * 1024)
        for name in costs["active"]:
            self.assertEqual(config["sensitivity_table"][name]["memory"], 0)

    def test_ap_q9_heavy_release_combines_screened_qps_and_memory_endpoints(self):
        run = LogicalRun("A25", "hybench_ap", "memory", 1, "hybench_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "ap_q9_heavy_release_topk")
        costs = config["cost_parameters"]
        self.assertEqual(costs["baseline_values"]["io_block_read_cost"], .01)
        self.assertEqual(costs["start_values"]["io_block_read_cost"], 100)
        self.assertEqual(costs["baseline_values"]["innodb_buffer_pool_size"], 2 * 1024 ** 3)
        self.assertEqual(costs["start_values"]["innodb_buffer_pool_size"], 1 * 1024 ** 3)
        self.assertEqual(costs["baseline_values"]["join_buffer_size"], 64 * 1024 ** 2)
        self.assertEqual(costs["start_values"]["join_buffer_size"], 256 * 1024)
        for name in costs["active"]:
            self.assertEqual(config["sensitivity_table"][name]["memory"], 0)

    def test_ap_q11_session_release_uses_committed_session_memory(self):
        run = LogicalRun("B8", "hybench_ap", "memory", 1, "hybench_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "ap_q11_session_release_topk")
        costs = config["cost_parameters"]
        self.assertEqual(costs["baseline_values"]["io_block_read_cost"], 100)
        self.assertEqual(costs["start_values"]["io_block_read_cost"], .01)
        self.assertEqual(costs["baseline_values"]["join_buffer_size"], 96 * 1024 ** 2)
        self.assertEqual(costs["start_values"]["join_buffer_size"], 256 * 1024)
        self.assertEqual(costs["baseline_values"]["innodb_buffer_pool_size"], 2 * 1024 ** 3)
        self.assertEqual(costs["start_values"]["innodb_buffer_pool_size"], 1 * 1024 ** 3)
        for name in costs["active"]:
            self.assertEqual(config["sensitivity_table"][name]["memory"], 0)

    def test_memory_pool_tradeoff_uses_valid_one_gib_pool_step(self):
        run = LogicalRun("A27", "tpch", "memory", 1, "tpch_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "memory_pool_tradeoff_topk")
        costs = config["cost_parameters"]
        self.assertEqual(costs["baseline_values"]["innodb_buffer_pool_size"], 2147483648)
        self.assertEqual(costs["start_values"]["innodb_buffer_pool_size"], 1073741824)
        self.assertEqual(costs["baseline_values"]["join_buffer_size"], 1048576)
        self.assertEqual(costs["start_values"]["join_buffer_size"], 67108864)
        self.assertGreater(config["sensitivity_table"]["join_buffer_size"]["memory"], 0)

    def test_memory_pool_valid_topk_does_not_offset_pool_drop(self):
        run = LogicalRun("A27", "tpch", "memory", 1, "tpch_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "memory_pool_valid_topk")
        costs = config["cost_parameters"]
        self.assertEqual(costs["start_values"]["innodb_buffer_pool_size"], 1073741824)
        self.assertEqual(costs["baseline_values"]["join_buffer_size"], 1048576)
        self.assertEqual(costs["start_values"]["join_buffer_size"], 262144)

    def test_memory_runtime_release_topk_reduces_live_query_memory(self):
        run = LogicalRun("A26", "job", "memory", 1, "job_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "memory_runtime_release_topk")
        costs = config["cost_parameters"]
        self.assertEqual(costs["baseline_values"]["tmp_table_size"], 134217728)
        self.assertEqual(costs["start_values"]["tmp_table_size"], 16777216)
        self.assertEqual(costs["baseline_values"]["join_buffer_size"], 16777216)
        self.assertEqual(costs["start_values"]["join_buffer_size"], 262144)
        self.assertEqual(config["sensitivity_table"]["tmp_table_size"]["memory"], 0)

    def test_memory_runtime_release_heavy_topk_raises_baseline_buffers(self):
        run = LogicalRun("A26", "job", "memory", 1, "job_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "memory_runtime_release_heavy_topk")
        costs = config["cost_parameters"]
        for name in (
            "join_buffer_size", "sort_buffer_size",
            "read_buffer_size", "read_rnd_buffer_size",
        ):
            self.assertEqual(costs["baseline_values"][name], 64 * 1024 * 1024)
            self.assertEqual(costs["start_values"][name], 256 * 1024)
            self.assertEqual(config["sensitivity_table"][name]["memory"], 0)
        self.assertEqual(costs["baseline_values"]["innodb_buffer_pool_size"], 2 * 1024 * 1024 * 1024)
        self.assertEqual(costs["start_values"]["innodb_buffer_pool_size"], 1 * 1024 * 1024 * 1024)

    def test_job11d_memory_profile_includes_screened_row_cost(self):
        run = LogicalRun("A26", "job", "memory", 1, "job_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "job11d_memory_valid_topk")
        costs = config["cost_parameters"]
        self.assertEqual(costs["baseline_values"]["row_evaluate_cost"], 100)
        self.assertEqual(costs["start_values"]["row_evaluate_cost"], .1)
        self.assertEqual(costs["start_values"]["innodb_buffer_pool_size"], 1073741824)

    def test_ap_screen_topk_matches_extreme_screened_candidate(self):
        run = LogicalRun("B8", "hybench_ap", "memory", 2, "hybench_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "ap_screen_topk")
        self.assertEqual(config["cost_parameters"]["baseline_values"]["memory_block_read_cost"], 100)
        self.assertEqual(config["cost_parameters"]["start_values"]["memory_block_read_cost"], .01)
        self.assertEqual(config["cost_parameters"]["start_values"]["join_buffer_size"], 262144)
        self.assertIn("innodb_buffer_pool_size", config["sensitivity_table"])

    def test_tpch_memory_topk_reduces_session_buffers_partially(self):
        run = LogicalRun("A27", "tpch", "memory", 1, "tpch_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["dispatch"] = {}
        apply_parameter_profile(config, "tpch_memory_topk")
        self.assertEqual(config["cost_parameters"]["baseline_values"]["join_buffer_size"], 67108864)
        self.assertEqual(config["cost_parameters"]["start_values"]["join_buffer_size"], 50331648)
        self.assertEqual(config["cost_parameters"]["start_values"]["io_block_read_cost"], .01)

    def test_optimizer_step_override_rejects_inverted_bounds(self):
        config = {"optimizer": {
            "eta": 2.0, "min_param_delta_rel_pct": 10.0,
            "max_param_delta_rel_pct": 20.0, "max_param_delta_ratio": .2,
        }, "dispatch": {}}
        with self.assertRaisesRegex(ValueError, "cannot exceed"):
            apply_optimizer_step_overrides(config, min_delta_pct=5, max_delta_pct=3)

    @patch("run_mysql_gd_matrix.MySQLCostAdapter")
    @patch("run_mysql_gd_matrix.MySQLCLIClient")
    def test_seeded_calibration_applies_exact_active_start_values(self, client, adapter):
        run = LogicalRun("A28", "hybench_tp", "cpu", 1, "hybench_worker")
        config = build_config(run, {name: 1 for name in ("hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc")})
        config["cost_parameters"]["start_values"] = {
            name: float(index + 1) for index, name in enumerate(config["cost_parameters"]["active"])
        }

        apply_calibration_start_costs(config)

        client.assert_called_once_with(config["mysql"])
        adapter.return_value.apply.assert_called_once_with(config["cost_parameters"]["start_values"])

    @patch("run_mysql_gd_matrix.MySQLCostAdapter")
    @patch("run_mysql_gd_matrix.MySQLCLIClient")
    def test_calibration_prefers_explicit_baseline_values_over_seed(self, client, adapter):
        run = LogicalRun("A25", "hybench_ap", "memory", 1, "hybench_worker")
        config = build_config(run, {name: 1 for name in (
            "hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc"
        )})
        config["cost_parameters"].update({
            "active": ["io_block_read_cost", "memory_block_read_cost"],
            "baseline_values": {"io_block_read_cost": 100.0, "memory_block_read_cost": 100.0},
            "start_values": {"io_block_read_cost": 2.0, "memory_block_read_cost": 1.0},
        })

        apply_calibration_start_costs(config)

        adapter.return_value.apply.assert_called_once_with(config["cost_parameters"]["baseline_values"])

    def test_worker_registry_rejects_duplicate_ports(self):
        rows = {"workers": [
            {"worker_id": "a", "benchmarks": ["job"], "port": 1, "mysqld_pid": 11, "database": "job"},
            {"worker_id": "b", "benchmarks": ["tpch"], "port": 1, "mysqld_pid": 12, "database": "tpch"},
        ]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workers.json"
            path.write_text(json.dumps(rows), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "port"):
                load_workers(path)

    def test_dedicated_manifest_requires_no_bottleneck_for_none(self):
        manifest = load_manifest("config/mysql_experiment_manifest_A25_E25_B7.json")
        document = {
            "meta": {"exp_id": "E25", "benchmark": "hybench_ap", "db": "mysql8", "method": "gradient_descent", "bottleneck": "none", "bottleneck_target": None, "repeat_id": 1, "machine_spec": {"cpu_cores": 4, "mem_gb": 8, "disk_bw_mb": 100}},
            "outer_loop": [
                {"iter": 1, "params": {}, "qps": 100.0, "latency_p99_ms": 1.0, "latency_p95_ms": 1.0, "resource": {"cpu": .2, "mem": .2, "io": .2}, "utility": .1, "explain_samples": []},
                {"iter": 2, "params": {}, "qps": 116.0, "latency_p99_ms": 1.0, "latency_p95_ms": 1.0, "resource": {"cpu": .2, "mem": .2, "io": .2}, "utility": .2, "explain_samples": []},
            ],
            "inner_loop_summary": {"total_iterations": 1, "wall_time_ms": 1.0, "best_estimated_utility": .2, "best_params": {}},
            "result": {"final_params": {}, "delta_u_pct": 1.0, "qps_before": 100.0, "qps_after": 116.0, "p99_before_ms": 1.0, "p99_after_ms": 1.0, "bad_config": False, "converge_outer_iters": 1, "total_wall_time_s": 1.0},
        }
        document["outer_loop"][-1]["resource"]["mem"] = .21
        self.assertTrue(evaluate_acceptance(document, manifest["acceptance"])["passed"])
        document["outer_loop"][-1]["resource"]["cpu"] = .91
        self.assertFalse(evaluate_acceptance(document, manifest["acceptance"])["passed"])


if __name__ == "__main__":
    unittest.main()

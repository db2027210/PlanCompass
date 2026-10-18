"""Unit tests for goal_20260720/update_goal_monitor_status.py shipped helpers."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GOAL = ROOT / "goal_20260720"
sys.path.insert(0, str(GOAL))

import update_goal_monitor_status as monitor  # noqa: E402


class GoalMonitorStatusTests(unittest.TestCase):
    def test_attempt_outcome_formal_complete_uses_real_decision(self) -> None:
        fixture = Path(self.id().replace(".", "_"))
        # use temp under test dir via TemporaryDirectory pattern without HOME hacks
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            attempt = Path(tmp) / "attempt_01"
            attempt.mkdir()
            formal = {
                "result": {
                    "qps_before": 1.0,
                    "qps_after": 1.2,
                    "bad_config": False,
                }
            }
            (attempt / "B6U3_r1.json").write_text(json.dumps(formal), encoding="utf-8")
            (attempt / "run_summary.json").write_text(
                json.dumps({
                    "final_goal_decision": {
                        "successful": True,
                        "reason": "final_success",
                        "qps_gain_ratio": 0.2,
                        "target_drop_in_range": True,
                    }
                }),
                encoding="utf-8",
            )
            outcome = monitor.attempt_outcome(attempt)
            self.assertEqual(outcome["state"], "formal_complete")
            self.assertTrue(outcome["session_target_met"])
            self.assertEqual(outcome["qps_before"], 1.0)
            self.assertEqual(outcome["qps_after"], 1.2)

    def test_attempt_outcome_failure_and_in_progress(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            failed = Path(tmp) / "attempt_01"
            failed.mkdir()
            (failed / "run_failure.json").write_text(
                json.dumps({"error_type": "TimeoutError", "error": "workload exceeded 420.0s"}),
                encoding="utf-8",
            )
            out = monitor.attempt_outcome(failed)
            self.assertEqual(out["state"], "failed")
            self.assertEqual(out["error_type"], "TimeoutError")
            self.assertFalse(out["session_target_met"])

            running = Path(tmp) / "attempt_02"
            running.mkdir()
            (running / "real_validations").mkdir()
            (running / "real_validations" / "0001_warmup_1").mkdir()
            out2 = monitor.attempt_outcome(running)
            self.assertEqual(out2["state"], "in_progress")
            self.assertIn("0001_warmup_1", out2["recent_measure_dirs"])

    def test_build_status_with_injected_batch_tree(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batches = root / "formal_batches"
            b5 = batches / "B5_memory_calibrated_n3_20260720"
            b5.mkdir(parents=True)
            (b5 / "protocol.json").write_text(
                json.dumps({"protocol_id": "B5_memory_calibrated_n3_20260720", "exp_id": "B5C"}),
                encoding="utf-8",
            )
            (b5 / "batch_summary.json").write_text(
                json.dumps({
                    "protocol_id": "B5_memory_calibrated_n3_20260720",
                    "attempt_limit": 3,
                    "passed_attempts": 3,
                    "majority_passed": True,
                    "attempts": [],
                }),
                encoding="utf-8",
            )
            (b5 / "acceptance_audit.json").write_text(
                json.dumps({"acceptance_met": True, "passed_attempts": 3}),
                encoding="utf-8",
            )
            a1 = b5 / "attempt_01"
            a1.mkdir()
            (a1 / "B5C_r1.json").write_text(
                json.dumps({"result": {"qps_before": 0.1, "qps_after": 0.2, "bad_config": False}}),
                encoding="utf-8",
            )
            (a1 / "run_summary.json").write_text(
                json.dumps({"final_goal_decision": {"successful": True}}),
                encoding="utf-8",
            )

            b6 = batches / "B6_io_explicitcap_n2v3_20260720"
            b6.mkdir()
            (b6 / "protocol.json").write_text(
                json.dumps({
                    "protocol_id": "B6_io_explicitcap_n2v3_20260720",
                    "exp_id": "B6U3",
                    "runner_args": ["--mysqld-pid", "4242"],
                }),
                encoding="utf-8",
            )
            a6 = b6 / "attempt_01"
            a6.mkdir()
            (a6 / "real_point_warmups").mkdir()
            (a6 / "real_point_warmups" / "0002_baseline_warmup_1").mkdir()

            status = monitor.build_status(
                goal_dir=root,
                batch_root=batches,
                container_name="ra_mysql_goal_hybench_xp",
                port=13366,
                processes=[{
                    "pid": 1,
                    "ppid": 0,
                    "etime": "00:01",
                    "cmd": (
                        "python mysql_double_loop_grid_runner.py "
                        "--protocol-id B6_io_explicitcap_n2v3_20260720 "
                        f"--output-dir {a6}"
                    ),
                }],
                container={
                    "name": "ra_mysql_goal_hybench_xp",
                    "status": "running",
                    "running": True,
                    "mysqld_pid": 4242,
                    "mysqld_pid_alive": True,
                    "ports": "13366",
                },
            )
            self.assertTrue(status["liveness"]["experiment_flow_live"])
            self.assertTrue(status["b5_summary"]["accepted"])
            self.assertFalse(status["b6_summary"]["accepted"])
            self.assertEqual(status["active"]["protocol_id"], "B6_io_explicitcap_n2v3_20260720")
            self.assertEqual(status["active"]["attempt"], "attempt_01")
            self.assertIn("remaining_matrix", status)
            self.assertGreaterEqual(len(status["remaining_matrix"]), 10)
            self.assertIn("updated_at", status)
            self.assertEqual(status["session_goal"]["method"], "outer_binary_inner_estimated_grid")

    def test_update_once_writes_status_file_via_real_entry(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batches = root / "formal_batches"
            batches.mkdir()
            status_path = root / "goal_monitor_status.json"
            # empty tree still produces a durable status document
            written = monitor.update_once(
                goal_dir=root,
                status_path=status_path,
                batch_root=batches,
                container_name="missing_container_for_unit_test",
                port=13366,
            )
            self.assertTrue(status_path.is_file())
            loaded = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["updated_at"], written["updated_at"])
            self.assertIn("b5_summary", loaded)
            self.assertIn("b6_summary", loaded)
            self.assertIn("remaining_matrix", loaded)


if __name__ == "__main__":
    unittest.main()

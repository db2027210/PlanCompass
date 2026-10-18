from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools/audit_job_v49_live_point.py"


def import_module():
    spec = importlib.util.spec_from_file_location("job_live_point_audit_test", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import JOB live-point auditor")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit = import_module()


class JobV49LivePointAuditTest(unittest.TestCase):
    def test_current_process_and_artifacts_are_captured_read_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_live_point_") as raw:
            root = Path(raw)
            replay = root / "replay"
            replay.mkdir()
            events = [
                {"event": "job_round8_candidate_invocation_started", "rank": 4},
                {"event": "job_round8_candidate_replay_complete", "rank": 3},
            ]
            (replay / "events.jsonl").write_text(
                "".join(json.dumps(item) + "\n" for item in events),
                encoding="utf-8",
            )
            (replay / "rank_04").mkdir()
            (replay / "rank_04/marker.txt").write_text("running\n", encoding="utf-8")
            counter = root / "global.txt"
            counter.write_text("390\n", encoding="utf-8")
            args = type(
                "Args",
                (),
                {
                    "runner_pid": os.getpid(),
                    "child_pid": os.getpid(),
                    "replay_root": replay,
                    "global_counter": counter,
                    "previous_snapshot": None,
                    "hard_timeout_seconds": 1800,
                },
            )()
            before = {
                path: path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
            result = audit.run(args)
            after = {
                path: path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
            self.assertEqual(before, after)
            self.assertEqual(result["global_counter"], 390)
            self.assertEqual(result["events"]["last_started_rank"], 4)
            self.assertEqual(
                result["events"]["counts"][
                    "job_round8_candidate_invocation_started"
                ],
                1,
            )
            self.assertTrue(result["runner"])
            self.assertTrue(result["child"])
            self.assertEqual(result["classification"], "in_flight_within_timeout")

    def test_delta_detects_cpu_io_counter_and_artifact_progress(self) -> None:
        previous = {
            "captured_epoch": 100.0,
            "global_counter": 390,
            "events": {"mtime_ns": 1},
            "process_tree": [
                {
                    "pid": 5,
                    "started_epoch": 50.0,
                    "elapsed_seconds": 50.0,
                    "cpu_ticks": 10,
                    "io": {"read_bytes": 100, "write_bytes": 200, "syscr": 2, "syscw": 3},
                }
            ],
            "artifacts": [{"path": "rank_04/sample.json", "size": 10, "mtime_ns": 1}],
        }
        current = {
            "captured_epoch": 160.0,
            "global_counter": 390,
            "events": {"mtime_ns": 1},
            "process_tree": [
                {
                    "pid": 5,
                    "started_epoch": 50.0,
                    "elapsed_seconds": 110.0,
                    "cpu_ticks": 15,
                    "io": {"read_bytes": 100, "write_bytes": 205, "syscr": 2, "syscw": 4},
                }
            ],
            "artifacts": [{"path": "rank_04/sample.json", "size": 20, "mtime_ns": 2}],
        }
        result = audit.delta(current, previous)
        self.assertTrue(result["observable_progress"])
        self.assertEqual(result["changed_artifacts"], ["rank_04/sample.json"])
        self.assertEqual(result["process_deltas"][0]["cpu_ticks_delta"], 5)

    def test_hard_timeout_threshold_uses_child_elapsed_not_runner_elapsed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="job_live_timeout_") as raw:
            root = Path(raw)
            replay = root / "replay"
            replay.mkdir()
            counter = root / "global.txt"
            counter.write_text("390\n", encoding="utf-8")
            runner = {
                "pid": 1,
                "ppid": 0,
                "state": "S",
                "started_epoch": time.time() - 3000,
                "elapsed_seconds": 3000.0,
                "cpu_ticks": 1,
                "io": {},
                "wchan": "wait",
                "command": "runner",
            }
            child = {**runner, "pid": 2, "ppid": 1, "elapsed_seconds": 1861.1}
            args = type(
                "Args",
                (),
                {
                    "runner_pid": 1,
                    "child_pid": 2,
                    "replay_root": replay,
                    "global_counter": counter,
                    "previous_snapshot": None,
                    "hard_timeout_seconds": 1800,
                },
            )()
            with mock.patch.object(
                audit,
                "proc_snapshot",
                side_effect=[runner, child],
            ), mock.patch.object(audit, "process_tree", return_value=[child]):
                result = audit.run(args)
            self.assertEqual(result["classification"], "hard_timeout_cleanup_anomaly")


if __name__ == "__main__":
    unittest.main()

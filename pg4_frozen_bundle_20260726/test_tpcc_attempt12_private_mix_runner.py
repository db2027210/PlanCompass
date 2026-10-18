import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest
from contextlib import redirect_stderr
from unittest import mock


MODULE_PATH = Path(__file__).with_name("run_tpcc_sensitive_mix.py")
if not MODULE_PATH.exists():
    MODULE_PATH = Path(__file__).with_name("tpcc_attempt12_private_mix_runner.py")
SPEC = importlib.util.spec_from_file_location("tpcc_attempt12_private_mix_runner", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class FakeTimeoutProcess:
    def __init__(self, pid: int = 42420) -> None:
        self.pid = pid
        self.wait_calls = []

    def poll(self):
        return None

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if len(self.wait_calls) == 1:
            raise subprocess.TimeoutExpired(["fake-parent", "fake-child"], timeout)
        return -RUNNER.SIGKILL


class FakeUnreapedProcess(FakeTimeoutProcess):
    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        raise subprocess.TimeoutExpired(["fake-parent", "fake-child"], timeout)


class FakePollSequence:
    def __init__(self, values):
        self.pid = 42422
        self.values = iter(values)
        self.last = None

    def poll(self):
        try:
            self.last = next(self.values)
        except StopIteration:
            pass
        return self.last


class MixRunnerProcessTests(unittest.TestCase):
    def test_technical_failure_does_not_consume_or_mutate_outer_state(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            RUNNER.emit_technical_failure("fixture")

        prefix = "TPCC_TECHNICAL_FAILURE_JSON="
        output = stderr.getvalue().strip()
        self.assertTrue(output.startswith(prefix))
        payload = json.loads(output[len(prefix):])
        self.assertFalse(payload["counts_as_candidate_replay"])
        self.assertFalse(payload["updates_incumbent"])
        self.assertFalse(payload["increments_accepted_shrink"])

    def test_normal_process_exits_before_deadline(self) -> None:
        process = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            start_new_session=True,
        )
        timed_out, returncodes = RUNNER.wait_for_processes_until(
            [("native", process)],
            time.monotonic() + 5.0,
        )
        self.assertFalse(timed_out)
        self.assertEqual(returncodes, {"native": 0})
        self.assertEqual(process.wait(timeout=1.0), 0)

    def test_timeout_process_tree_terms_then_kills_and_reaps(self) -> None:
        process = FakeTimeoutProcess()
        with mock.patch.object(RUNNER, "_send_group_signal") as send_group_signal:
            result = RUNNER.terminate_process_group(process, 0.2, 0.3)

        self.assertEqual(
            send_group_signal.call_args_list,
            [
                mock.call(process.pid, RUNNER.SIGTERM),
                mock.call(process.pid, RUNNER.SIGKILL),
            ],
        )
        self.assertEqual(process.wait_calls, [0.2, 0.3])
        self.assertEqual(result["returncode"], -RUNNER.SIGKILL)
        self.assertEqual(
            result["actions"],
            ["term_group", "kill_group", "reaped_after_kill"],
        )

    def test_sixty_second_deadline_and_cleanup_fit_outer_timeout(self) -> None:
        env = {
            "TPCC_MIX_STARTUP_EXIT_GRACE_SECONDS": "210",
            "TPCC_MIX_TERM_WAIT_SECONDS": "10",
            "TPCC_MIX_KILL_WAIT_SECONDS": "5",
            "TPCC_MIX_OUTER_TIMEOUT_SECONDS": "300",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            policy = RUNNER.deadline_policy(60)
        self.assertEqual(policy["deadline_seconds"], 270.0)
        self.assertLess(
            policy["deadline_seconds"]
            + policy["term_wait_seconds"]
            + policy["kill_wait_seconds"],
            policy["outer_timeout_seconds"],
        )

    def test_invalid_deadline_fails_before_child_start(self) -> None:
        env = {
            "TPCC_MIX_STARTUP_EXIT_GRACE_SECONDS": "230",
            "TPCC_MIX_TERM_WAIT_SECONDS": "10",
            "TPCC_MIX_KILL_WAIT_SECONDS": "5",
            "TPCC_MIX_OUTER_TIMEOUT_SECONDS": "300",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaisesRegex(ValueError, "cleanup headroom"):
                RUNNER.deadline_policy(60)

    def test_unreaped_process_tree_is_structured_not_exception(self) -> None:
        process = FakeUnreapedProcess(pid=42421)
        with mock.patch.object(RUNNER, "_send_group_signal") as send_group_signal:
            result = RUNNER.terminate_process_group(process, 0.2, 0.3)

        self.assertEqual(send_group_signal.call_count, 2)
        self.assertFalse(result["reaped"])
        self.assertIsNone(result["returncode"])
        self.assertEqual(result["actions"][-1], "reap_timeout_after_kill")

    def test_fresh_summary_ends_wait_with_resident_native_parent(self) -> None:
        native = mock.Mock()
        native.poll.return_value = None
        sensitive = mock.Mock()
        sensitive.poll.return_value = 0

        timed_out, returncodes, summary_completed = RUNNER.wait_for_workload_until(
            [("native", native), ("sensitive", sensitive)],
            time.monotonic() + 5.0,
            completion_probe=lambda: True,
        )

        self.assertFalse(timed_out)
        self.assertTrue(summary_completed)
        self.assertEqual(returncodes, {"native": None, "sensitive": 0})

    def test_native_completion_ends_wait_with_blocked_sensitive_process(self) -> None:
        native = mock.Mock()
        native.poll.return_value = 0
        sensitive = mock.Mock()
        sensitive.poll.return_value = None

        timed_out, returncodes, summary_completed = RUNNER.wait_for_workload_until(
            [("native", native), ("sensitive", sensitive)],
            time.monotonic() + 5.0,
            completion_probe=lambda: True,
        )

        self.assertFalse(timed_out)
        self.assertTrue(summary_completed)
        self.assertEqual(returncodes, {"native": 0, "sensitive": None})

    def test_deadline_cleanup_rechecks_summary_before_technical_failure(self) -> None:
        cleanup = {
            "native": {
                "returncode": 0,
                "actions": ["term_group", "reaped_after_term"],
                "reaped": True,
            }
        }

        self.assertTrue(
            RUNNER.cleanup_finished_completed_workload(cleanup, lambda: True)
        )
        cleanup["native"]["reaped"] = False
        self.assertFalse(
            RUNNER.cleanup_finished_completed_workload(cleanup, lambda: True)
        )

    def test_phase_finish_releases_blocked_native_once(self) -> None:
        native = FakePollSequence([None, None, 0])
        release = mock.Mock()

        timed_out, returncodes, summary_completed = RUNNER.wait_for_workload_until(
            [("native", native)],
            time.monotonic() + 5.0,
            completion_probe=lambda: False,
            phase_finished_probe=lambda: True,
            phase_release=release,
            poll_seconds=0.001,
        )

        self.assertFalse(timed_out)
        self.assertFalse(summary_completed)
        self.assertEqual(returncodes, {"native": 0})
        release.assert_called_once_with()

    def test_native_termination_marker_is_recordable_before_sensitive_exit(self) -> None:
        lines = [
            "PHASE START :: Workload=TPCC",
            "TERMINATE :: Waiting for all terminals to finish ..",
        ]
        self.assertTrue(RUNNER.native_termination_marker_seen(lines))
        self.assertTrue(
            RUNNER.native_termination_marker_seen(
                ["Waiting for all terminals to finish"]
            )
        )
        self.assertFalse(RUNNER.native_termination_marker_seen(["MEASURE :: running"]))

    def test_sensitive_start_barrier_waits_for_native_measurement(self) -> None:
        native = mock.Mock()
        native.poll.return_value = None
        marker_lines = [
            ["DBWorkload configuration"],
            ["PHASE START :: Workload=TPCC"],
        ]
        with mock.patch.object(RUNNER, "tail_lines", side_effect=marker_lines):
            ready, returncode = RUNNER.wait_for_native_measurement_start(
                native,
                Path("native.log"),
                time.monotonic() + 5.0,
                poll_seconds=0.001,
            )

        self.assertTrue(ready)
        self.assertIsNone(returncode)
        self.assertEqual(native.poll.call_count, 2)

    def test_sensitive_start_barrier_stops_when_native_exits(self) -> None:
        native = mock.Mock()
        native.poll.return_value = 7
        with mock.patch.object(RUNNER, "tail_lines") as tail:
            ready, returncode = RUNNER.wait_for_native_measurement_start(
                native,
                Path("native.log"),
                time.monotonic() + 5.0,
            )

        self.assertFalse(ready)
        self.assertEqual(returncode, 7)
        tail.assert_not_called()


if __name__ == "__main__":
    unittest.main()

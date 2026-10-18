from __future__ import annotations

import unittest

from formal_job_continuous_session import FormalSamplingBoundaryError
from formal_mysql_database_qps import FormalDatabaseQPSError
from formal_mysql_sgd_orchestration import (
    FormalSGDPointError,
    ROLLING_DECISIONS,
    _run_continuous_session,
)


class BoundarySession:
    def __init__(self, *, stable_at=240, wait_failures=(), boundary_failures=()):
        self.stable_at = stable_at
        self.wait_failures = set(wait_failures)
        self.boundary_failures = set(boundary_failures)
        self.events = []

    def start(self, *, warmup_seconds, maximum_measurement_seconds):
        self.events.append(("start", warmup_seconds, maximum_measurement_seconds))

    def wait_until_measurement(self, measurement_seconds):
        self.events.append(("wait", measurement_seconds))
        if measurement_seconds in self.wait_failures:
            raise FormalSamplingBoundaryError(
                f"timed out waiting for {measurement_seconds}s resource boundary"
            )

    def observation(self, measurement_seconds):
        if measurement_seconds in self.boundary_failures:
            raise FormalDatabaseQPSError(
                "database counter boundary coverage is too sparse"
            )
        stable = measurement_seconds >= self.stable_at
        windows = [
            {
                "index": index,
                "sample_coverage": 1.0,
                "stable": stable,
                "metrics": {
                    name: {"stable": stable}
                    for name in ("cpu", "memory", "io")
                },
            }
            for index in range(1, measurement_seconds // 30 + 1)
        ]
        return {
            "measurement_seconds": measurement_seconds,
            "throughput_evidence": {
                "reliable": True,
                "source": "database_status_counter",
                "db_qps": 100.0,
            },
            "stability": {
                "stable": stable,
                "decision_window_seconds": 180,
                "windows": windows,
            },
            "client_throughput_evidence": {"completion_event_count": 0},
        }

    def stop(self, measurement_seconds):
        self.events.append(("stop", measurement_seconds))
        return {"samples_path": "artifact://samples"}


class FormalJobBoundaryRecoveryTest(unittest.TestCase):
    def test_recovery_uses_only_legal_fixed_stop_boundary(self):
        session = BoundarySession(stable_at=240, wait_failures={210})
        result = _run_continuous_session(
            session, maximum_measurement_seconds=300,
            minimum_selection_seconds=180,
        )
        control = result["rolling_measurement_control"]
        self.assertEqual(control["selected_measurement_seconds"], 240)
        self.assertEqual(
            control["skipped_database_boundaries"][0]["measurement_seconds"],
            210,
        )
        stops = [event[1] for event in session.events if event[0] == "stop"]
        self.assertEqual(stops, [240])
        self.assertTrue(all(value in ROLLING_DECISIONS for value in stops))

    def test_no_legal_boundary_through_600_fails_closed(self):
        session = BoundarySession(boundary_failures=set(ROLLING_DECISIONS))
        with self.assertRaisesRegex(
            FormalSGDPointError,
            "no valid formal measurement boundary through 600s",
        ):
            _run_continuous_session(
                session, maximum_measurement_seconds=600,
                minimum_selection_seconds=180,
            )
        waits = [event[1] for event in session.events if event[0] == "wait"]
        self.assertEqual(waits, list(ROLLING_DECISIONS))
        self.assertFalse(any(event[0] == "stop" for event in session.events))
        with self.assertRaisesRegex(
            FormalSGDPointError, "one of 180/210/.../600"
        ):
            _run_continuous_session(
                BoundarySession(), maximum_measurement_seconds=1200,
                minimum_selection_seconds=180,
            )


if __name__ == "__main__":
    unittest.main()

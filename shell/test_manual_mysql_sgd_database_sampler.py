#!/usr/bin/env python3
"""Regression tests for staggered database-counter evidence merging."""

from __future__ import annotations

import io
from pathlib import Path
import sys
import subprocess
import threading
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from formal_mysql_database_qps import FormalDatabaseQPSError, canonical_database_qps
from formal_mysql_sgd_orchestration import _run_continuous_session
from shell.manual_mysql_sgd_r0_campaign import (
    Campaign,
    DATABASE_COUNTER_READ_TIMEOUT_SECONDS,
    _run_database_counter_sample,
    _run_processlist_sample,
)


def test_persistent_counter_timeout_preserves_gap_margin() -> None:
    assert 0 < DATABASE_COUNTER_READ_TIMEOUT_SECONDS <= 3.0


def test_staggered_database_samples_remain_strictly_timed_and_monotonic() -> None:
    campaign = Campaign.__new__(Campaign)
    campaign.rows_lock = threading.Lock()
    campaign.database_rows = []
    output = io.StringIO()
    base = {
        "source": "database_status_counter",
        "attribution_scope": "dedicated_mysql_instance",
        "digest_set_sha256": None,
        "counter_name": "Com_insert+Com_update+Com_delete",
    }

    assert campaign._append_database_sample(
        output, {**base, "epoch_ms": 1000, "completed_total": 10}
    )
    assert campaign._append_database_sample(
        output, {**base, "epoch_ms": 1500, "completed_total": 12}
    )
    assert not campaign._append_database_sample(
        output, {**base, "epoch_ms": 1400, "completed_total": 11}
    )
    assert not campaign._append_database_sample(
        output, {**base, "epoch_ms": 1600, "completed_total": 9}
    )
    assert campaign._append_database_sample(
        output, {**base, "epoch_ms": 2000, "completed_total": 12}
    )

    assert [row["epoch_ms"] for row in campaign.database_rows] == [1000, 1500, 2000]
    assert [row["completed_total"] for row in campaign.database_rows] == [10, 12, 12]
    assert len(output.getvalue().splitlines()) == 3


def test_single_timeout_is_recorded_and_recovers_with_short_reconnect() -> None:
    calls = []

    def runner(argv, **kwargs):
        calls.append(kwargs["timeout"])
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return SimpleNamespace(returncode=0, stdout="Com_select\t42\n", stderr="")

    counter, errors = _run_database_counter_sample(
        ["mysql"], "SHOW GLOBAL STATUS LIKE 'Com_select'", 1, runner=runner
    )
    assert counter == 42
    assert len(errors) == 1
    assert errors[0]["sample_error"].startswith("TimeoutExpired:")
    assert calls == [5, 1]


def test_consecutive_timeouts_leave_missing_boundary_and_fail_closed() -> None:
    def runner(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    counter, errors = _run_database_counter_sample(
        ["mysql"], "SHOW GLOBAL STATUS LIKE 'Com_select'", 1, runner=runner
    )
    assert counter is None
    assert len(errors) == 2
    rows = [
        {
            "epoch_ms": 9_000,
            "completed_total": 99,
            "source": "database_status_counter",
            "attribution_scope": "dedicated_mysql_instance",
            "digest_set_sha256": None,
            "counter_name": "Com_select",
        },
        {
            "epoch_ms": 10_000,
            "completed_total": 100,
            "source": "database_status_counter",
            "attribution_scope": "dedicated_mysql_instance",
            "digest_set_sha256": None,
            "counter_name": "Com_select",
        },
    ]
    with pytest.raises(FormalDatabaseQPSError, match="bracket"):
        canonical_database_qps(rows, start_epoch_ms=10_000, end_epoch_ms=20_000)


def test_processlist_timeout_is_missing_sample_and_next_sample_continues() -> None:
    calls = []

    def runner(argv, **kwargs):
        calls.append(kwargs["timeout"])
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return SimpleNamespace(returncode=0, stdout="123\n", stderr="")

    ids, error = _run_processlist_sample(
        ["mysql"], "SELECT ID FROM information_schema.PROCESSLIST", runner=runner
    )
    assert ids == []
    assert error is not None
    assert error["sample_status"] == "missing"
    ids, error = _run_processlist_sample(
        ["mysql"], "SELECT ID FROM information_schema.PROCESSLIST", runner=runner
    )
    assert ids == [123]
    assert error is None
    assert calls == [10, 10]


def test_stable_boundary_is_accepted_before_maximum() -> None:
    class Session:
        def start(self, **kwargs):
            self.started = kwargs

        def wait_until_measurement(self, seconds):
            self.waited = seconds

        def observation(self, seconds):
            metrics = {
                name: {"stable": True} for name in ("cpu", "memory", "io")
            }
            windows = [
                {
                    "index": index,
                    "sample_coverage": 1.0,
                    "metrics": metrics,
                }
                for index in range(1, seconds // 30 + 1)
            ]
            return {
                "measurement_seconds": seconds,
                "throughput_evidence": {
                    "reliable": True,
                    "source": "database_status_counter",
                    "db_qps": 1.0,
                },
                "client_throughput_evidence": {"completion_event_count": 0},
                "stability": {
                    "stable": True,
                    "decision_window_seconds": 180,
                    "windows": windows,
                },
            }

        def stop(self, seconds):
            return {"stopped_at": seconds}

    result = _run_continuous_session(
        Session(), maximum_measurement_seconds=600, minimum_selection_seconds=180
    )
    assert result["stopped_at"] == 180
    assert result["rolling_measurement_control"]["stop_reason"] == (
        "latest_six_30_second_windows_stable"
    )


def test_600_second_boundary_fails_closed_when_no_stable_window() -> None:
    class Session:
        def start(self, **kwargs):
            self.started = kwargs

        def wait_until_measurement(self, seconds):
            self.waited = seconds

        def observation(self, seconds):
            metrics = {
                name: {"stable": False} for name in ("cpu", "memory", "io")
            }
            windows = [
                {
                    "index": index,
                    "sample_coverage": 1.0,
                    "metrics": metrics,
                }
                for index in range(1, seconds // 30 + 1)
            ]
            return {
                "measurement_seconds": seconds,
                "throughput_evidence": {
                    "reliable": True,
                    "source": "database_status_counter",
                    "db_qps": 1.0,
                },
                "client_throughput_evidence": {"completion_event_count": 0},
                "stability": {
                    "stable": False,
                    "decision_window_seconds": 180,
                    "windows": windows,
                },
            }

        def stop(self, seconds):
            return {"stopped_at": seconds}

    result = _run_continuous_session(
        Session(), maximum_measurement_seconds=600, minimum_selection_seconds=180
    )
    assert result["stopped_at"] == 600
    assert result["rolling_measurement_control"]["stop_reason"] == (
        "maximum_measurement_seconds_reached_unstable"
    )


if __name__ == "__main__":
    test_staggered_database_samples_remain_strictly_timed_and_monotonic()
    print("staggered database sampler tests passed", flush=True)

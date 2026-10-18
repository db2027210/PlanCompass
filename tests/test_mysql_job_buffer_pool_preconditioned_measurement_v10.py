from __future__ import annotations

import subprocess

import pytest

from scripts import mysql_job_buffer_pool_preconditioned_measurement_v10 as v10


class Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


def test_suite_deadline_clamps_each_query_to_shared_remaining_time() -> None:
    clock = Clock()
    seen: list[float] = []

    def delegate(_argv, **kwargs):
        seen.append(float(kwargs["timeout"]))
        return subprocess.CompletedProcess([], 0, "", "")

    runner = v10.SuiteDeadlineRunner(delegate, 900.0, clock=clock)
    clock.value = 250.0
    runner(["mysql"], timeout=900.0)
    assert seen == [750.0]
    assert runner.evidence(output_dir="x", elapsed_seconds=150.0)[
        "deadline_triggered"
    ] is False


def test_suite_deadline_actively_rejects_new_query_after_900_seconds() -> None:
    clock = Clock()
    called = False

    def delegate(_argv, **_kwargs):
        nonlocal called
        called = True

    runner = v10.SuiteDeadlineRunner(delegate, 900.0, clock=clock)
    clock.value = 1000.001
    with pytest.raises(subprocess.TimeoutExpired):
        runner(["mysql"], timeout=900.0)
    assert called is False
    evidence = runner.evidence(output_dir="x", elapsed_seconds=900.001)
    assert evidence["deadline_triggered"] is True
    assert evidence["deadline_trigger_count"] == 1

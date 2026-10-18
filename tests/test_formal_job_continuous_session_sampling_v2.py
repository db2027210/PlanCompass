from __future__ import annotations

import json
from pathlib import Path

import pytest

from shell.formal_job_continuous_session_sampling_v2 import (
    FormalJobContinuousSession,
    FormalJobSessionError,
)


def protocol():
    return {
        "immutable": True,
        "timing": {
            "sample_interval_seconds": 1,
            "stability_window_seconds": 30,
            "required_consecutive_stability_windows": 6,
            "minimum_measurement_seconds": 180,
            "maximum_measurement_seconds": 600,
            "sample_coverage_min": 0.9,
        },
        "stability": {
            "calculation": "within_each_window_from_one_second_samples",
            "all_cpu_memory_io_resources_must_be_stable": True,
            "cpu_cv_max": 0.1,
            "memory_cv_max": 0.1,
            "io_cv_max": 0.1,
            "normal_throughput_cv_max": 0.1,
            "xp_xap_qps_cv_max": 0.1,
            "xp_xtp_tps_cv_max": 0.1,
            "low_utilization_ratio_threshold": 0.01,
            "low_utilization_absolute_range_max": 0.01,
        },
    }


class FakeProcess:
    def __init__(self, stdout, stop_file: Path, *, returncode: int = 0):
        self.stdout = stdout
        self.stop_file = stop_file
        self.final_returncode = returncode
        self.returncode = None
        self.terminated = False
        self.killed = False
        stdout.write(
            'FORMAL_SQL_SESSION_JSON={"started_epoch_ms":0,'
            '"measurement_start_epoch_ms":60000,"warmup_seconds":60,'
            '"maximum_measurement_seconds":600}\n'
        )
        stdout.flush()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        assert self.stop_file.read_text(encoding="ascii").strip() in {"180", "210"}
        self.returncode = self.final_returncode
        if self.returncode == 0:
            seconds = int(self.stop_file.read_text(encoding="ascii").strip())
            self.stdout.write(
                "FORMAL_SQL_METRICS_JSON="
                + json.dumps({
                    "measurement_seconds": seconds,
                    "completed": seconds,
                    "stop_requested": True,
                    "right_censored_count": 1,
                    "latency_p95_ms": 10.0,
                    "latency_p99_ms": 10.0,
                })
                + "\n"
            )
            self.stdout.flush()
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9


def make_session(tmp_path: Path, *, source="formal_sql_completion_jsonl", rc=0):
    start = 60000
    resources = []
    events = []
    for second in range(1, 211):
        # First window is unstable; 30..210 is stable, so 180 fails and 210 passes.
        cpu = 0.5 + (0.2 if second <= 30 and second % 2 else 0.0)
        epoch = start + second * 1000
        resources.append({
            "epoch_ms": epoch,
            "cpu_utilization_ratio": cpu,
            "memory_utilization_ratio": 0.9,
            "io_utilization_ratio": 0.4,
        })
        events.append({
            "epoch_ms": epoch,
            "status": "complete",
            "latency_ms": 10.0,
        })
    created = {}

    def builder(stop_file: Path):
        created["stop_file"] = stop_file
        return ["fake-java"]

    def factory(command, **kwargs):
        process = FakeProcess(kwargs["stdout"], created["stop_file"], returncode=rc)
        created["process"] = process
        return process

    session = FormalJobContinuousSession(
        output=tmp_path,
        command_builder=builder,
        resource_samples=lambda: resources,
        database_qps_samples=lambda: [
            {
                "epoch_ms": start + second * 1000,
                "completed_total": float(second * 2),
                "source": "database_status_counter",
                "attribution_scope": "dedicated_mysql_instance",
                "counter_name": "registered_statement_completions",
            }
            for second in range(0, 212)
        ],
        measurement_protocol=protocol(),
        process_factory=factory,
        throughput_source=source,
    )
    session.output.mkdir(parents=True, exist_ok=True)
    session.events_path.write_text(
        "".join(json.dumps(row) + "\n" for row in events), encoding="utf-8"
    )
    return session, created


def test_one_session_extends_from_180_to_stable_210_and_stops_atomically(tmp_path: Path) -> None:
    session, created = make_session(tmp_path)
    session.start(warmup_seconds=60, maximum_measurement_seconds=600)

    session.wait_until_measurement(180)
    first = session.observation(180)
    session.wait_until_measurement(210)
    second = session.observation(210)
    result = session.stop(210)

    assert first["stability"]["stable"] is False
    assert second["stability"]["stable"] is True
    assert second["resource_means"]["memory"] == pytest.approx(0.9)
    assert created["stop_file"].read_text(encoding="ascii") == "210\n"
    assert not list(tmp_path.glob("formal_job.stop.tmp.*"))
    assert result["metrics"]["right_censored_count"] == 1
    assert result["throughput_evidence"]["source"] == "database_status_counter"
    assert result["throughput_evidence"]["db_qps"] == 2.0
    assert result["client_throughput_evidence"]["source"] == "formal_sql_completion_jsonl"


def test_isolated_sampler_delay_is_filtered_then_coverage_is_evaluated(tmp_path: Path) -> None:
    session, _ = make_session(tmp_path)
    original_provider = session.resource_samples_provider
    rows = list(original_provider())
    delayed = [dict(row) for row in rows[:89]]
    previous = int(delayed[-1]["epoch_ms"])
    for offset, row in enumerate(rows[89:], start=0):
        current = dict(row)
        if offset == 0:
            previous += 2800
        elif offset == 1:
            previous += 10
        else:
            previous += 1000
        current["epoch_ms"] = previous
        delayed.append(current)
    session.resource_samples_provider = lambda: delayed

    session.start(warmup_seconds=60, maximum_measurement_seconds=600)
    result = session.observation(180)

    assert result["resource_cadence_audit"]["rejected_sample_count"] == 2
    assert result["resource_cadence_audit"]["recovered_catchup_pair_count"] == 1
    assert result["resource_cadence_audit"]["usable_one_second_sample_count"] >= 162
    assert result["resource_cadence_audit"]["elapsed_coverage"] >= 0.9
    assert result["stability"]["sample_coverage"] >= 0.9


def test_cadence_filter_still_fails_closed_below_frozen_coverage(tmp_path: Path) -> None:
    session, _ = make_session(tmp_path)
    rows = list(session.resource_samples_provider())
    epoch = 60000
    for index, row in enumerate(rows):
        epoch += 100 if index % 2 == 0 else 900
        row["epoch_ms"] = epoch
    session.resource_samples_provider = lambda: rows

    session.start(warmup_seconds=60, maximum_measurement_seconds=600)
    with pytest.raises(FormalJobSessionError, match="coverage below 0.9"):
        session.observation(180)


def test_catchup_pair_must_recover_absolute_nominal_boundary(
    tmp_path: Path,
) -> None:
    session, _ = make_session(tmp_path)
    rows = [dict(row) for row in session.resource_samples_provider()]
    previous = int(rows[88]["epoch_ms"])
    for offset, row in enumerate(rows[89:]):
        if offset == 0:
            previous += 2600
        elif offset == 1:
            previous += 10
        else:
            previous += 1000
        row["epoch_ms"] = previous
    session.resource_samples_provider = lambda: rows

    session.start(warmup_seconds=60, maximum_measurement_seconds=600)
    with pytest.raises(
        FormalJobSessionError,
        match="did not recover a nominal boundary",
    ):
        session.observation(180)


def test_large_gap_is_not_recoverable_even_with_short_following_tick(
    tmp_path: Path,
) -> None:
    session, _ = make_session(tmp_path)
    rows = [dict(row) for row in session.resource_samples_provider()]
    previous = int(rows[88]["epoch_ms"])
    for offset, row in enumerate(rows[89:]):
        if offset == 0:
            previous += 5800
        elif offset == 1:
            previous += 200
        else:
            previous += 1000
        row["epoch_ms"] = previous
    session.resource_samples_provider = lambda: rows

    session.start(warmup_seconds=60, maximum_measurement_seconds=600)
    with pytest.raises(
        FormalJobSessionError,
        match="unpaired delayed resource sample",
    ):
        session.observation(180)


def test_dense_rows_covering_only_half_the_elapsed_boundary_fail_closed(
    tmp_path: Path,
) -> None:
    session, _ = make_session(tmp_path)
    rows = [dict(row) for row in session.resource_samples_provider()[:180]]
    epoch = 60000
    for row in rows:
        epoch += 500
        row["epoch_ms"] = epoch
    session.resource_samples_provider = lambda: rows

    session.start(warmup_seconds=60, maximum_measurement_seconds=600)
    with pytest.raises(FormalJobSessionError, match="coverage below 0.9"):
        session.observation(180)


def test_questions_source_is_rejected_before_launch(tmp_path: Path) -> None:
    session, _ = make_session(tmp_path, source="performance_schema.questions")
    with pytest.raises(FormalJobSessionError, match="Questions is not workload throughput"):
        session.start(warmup_seconds=60, maximum_measurement_seconds=600)


def test_nonzero_java_exit_fails_closed_and_closes_logs(tmp_path: Path) -> None:
    session, _ = make_session(tmp_path, rc=2)
    session.start(warmup_seconds=60, maximum_measurement_seconds=600)
    session.wait_until_measurement(180)
    session.observation(180)
    with pytest.raises(FormalJobSessionError, match="graceful stop: rc=2"):
        session.stop(180)
    assert session._stdout_handle.closed
    assert session._stderr_handle.closed


def test_bounded_job_point_keeps_missing_query_coverage_as_audit(tmp_path: Path) -> None:
    session, _ = make_session(tmp_path)
    session.required_query_names = ("01.sql", "02.sql")
    rows = [
        {
            "epoch_ms": 61000,
            "status": "complete",
            "latency_ms": 10.0,
            "query": "01.sql",
        }
    ]
    session.events_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    session.start(warmup_seconds=60, maximum_measurement_seconds=600)
    result = session.stop(180)

    assert result["correctness"]["passed"] is True
    assert result["correctness"]["coverage_complete"] is False
    assert result["correctness"]["missing_queries"] == ["02.sql"]
    assert result["correctness"]["coverage_role"] == "audit_only_per_bounded_point"

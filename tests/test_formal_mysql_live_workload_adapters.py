import json

import pytest

from formal_mysql_live_workload_adapters import (
    LiveWorkloadEvidenceError,
    latency_percentiles_ms,
    parse_benchbase_formal_progress,
    parse_hybench_progress,
    parse_job_completion_events,
    progress_interval_rates,
)


def test_hybench_ap_xp_native_counters_become_interval_rates() -> None:
    text = "\n".join(
        "HYBENCH_FORMAL_SAMPLE_JSON=" + json.dumps(row)
        for row in (
            {"epoch_ms": 1000, "measurement_phase": False, "xap_or_ap_total": 99, "xtp_total": 99},
            {"epoch_ms": 2000, "measurement_phase": True, "xap_or_ap_total": 2, "xtp_total": 3},
            {"epoch_ms": 3000, "measurement_phase": True, "xap_or_ap_total": 7, "xtp_total": 5},
        )
    )
    rates = progress_interval_rates(parse_hybench_progress(text))
    assert rates[0].primary_rate == 5.0
    assert rates[0].secondary_rate == 2.0


def test_job_events_supply_completion_latency_without_questions() -> None:
    text = "\n".join(
        json.dumps(row)
        for row in (
            {"epoch_ms": 1500, "status": "complete", "latency_ms": 10.0},
            {"epoch_ms": 2000, "status": "error", "latency_ms": 20.0},
            {"epoch_ms": 2500, "status": "complete", "latency_ms": 30.0},
        )
    )
    rows = parse_job_completion_events(text, start_epoch_ms=1000, end_epoch_ms=2600)
    assert len(rows) == 2
    assert latency_percentiles_ms(rows) == pytest.approx((29.0, 29.8))


def test_benchbase_requires_structured_epoch_and_cumulative_completion_marker() -> None:
    with pytest.raises(LiveWorkloadEvidenceError, match="at least two"):
        parse_benchbase_formal_progress("Throughput: 123.0 txn/sec")

    text = "\n".join(
        (
            'BENCHBASE_FORMAL_SAMPLE_JSON={"epoch_ms":1000,"completed_total":4}',
            'BENCHBASE_FORMAL_SAMPLE_JSON={"epoch_ms":2000,"completed_total":9}',
        )
    )
    assert progress_interval_rates(parse_benchbase_formal_progress(text))[0].primary_rate == 5.0


def test_progress_counter_regression_fails_closed() -> None:
    text = "\n".join(
        (
            'BENCHBASE_FORMAL_SAMPLE_JSON={"epoch_ms":1000,"completed_total":9}',
            'BENCHBASE_FORMAL_SAMPLE_JSON={"epoch_ms":2000,"completed_total":4}',
        )
    )
    with pytest.raises(LiveWorkloadEvidenceError, match="regressed"):
        parse_benchbase_formal_progress(text)


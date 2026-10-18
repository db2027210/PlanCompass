from __future__ import annotations

import pytest

from formal_mysql_database_qps import (
    FormalDatabaseQPSError,
    canonical_database_qps,
)


def _samples(*, scope="dedicated_mysql_instance", digest=None):
    return [
        {
            "epoch_ms": 10_000 + second * 1000,
            "completed_total": 100 + second * 5,
            "source": "database_status_counter",
            "attribution_scope": scope,
            "digest_set_sha256": digest,
            "counter_name": "performance_schema_digest_sum_count_star",
        }
        for second in range(0, 12)
    ]


def test_dedicated_database_counter_is_canonical_dbqps():
    result = canonical_database_qps(
        _samples(), start_epoch_ms=10_000, end_epoch_ms=20_000
    )
    assert result["source"] == "database_status_counter"
    assert result["db_qps"] == 5.0
    assert result["completed_delta"] == 50.0


def test_registered_digest_counter_requires_and_retains_identity():
    digest = "a" * 64
    result = canonical_database_qps(
        _samples(scope="registered_workload_digest_set", digest=digest),
        start_epoch_ms=10_000, end_epoch_ms=20_000,
    )
    assert result["digest_set_sha256"] == digest
    with pytest.raises(FormalDatabaseQPSError, match="digest_set_sha256"):
        canonical_database_qps(
            _samples(scope="registered_workload_digest_set"),
            start_epoch_ms=10_000, end_epoch_ms=20_000,
        )


@pytest.mark.parametrize(
    "mutation, error",
    [
        (lambda rows: rows[0].update(source="client_completion"), "source"),
        (lambda rows: rows[0].pop("attribution_scope"), "attribution"),
        (lambda rows: rows[5].update(completed_total=0), "monotonic"),
        (lambda rows: rows.__setitem__(slice(None), rows[3:]), "bracket"),
    ],
)
def test_unattributable_reset_or_unbracketed_counter_fails_closed(mutation, error):
    rows = _samples()
    mutation(rows)
    with pytest.raises(FormalDatabaseQPSError, match=error):
        canonical_database_qps(rows, start_epoch_ms=10_000, end_epoch_ms=20_000)


def test_sparse_boundary_coverage_fails_closed():
    rows = _samples()
    rows[0]["epoch_ms"] = 8_000
    with pytest.raises(FormalDatabaseQPSError, match="too sparse"):
        canonical_database_qps(rows, start_epoch_ms=10_000, end_epoch_ms=20_000)


def test_end_boundary_coverage_fails_closed_without_relaxing_tolerance():
    rows = _samples()
    rows[10]["epoch_ms"] = 26_001
    rows[11]["epoch_ms"] = 27_001
    with pytest.raises(FormalDatabaseQPSError, match="too sparse"):
        canonical_database_qps(rows, start_epoch_ms=10_000, end_epoch_ms=20_000)

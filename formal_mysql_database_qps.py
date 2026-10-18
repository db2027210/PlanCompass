"""Canonical database-side DBQPS calculation for formal MySQL sessions.

The workload clients retain their native completion/progress rates for audit,
but those rates are never allowed to become the optimizer's canonical QPS.
Formal callers must inject snapshots of one attributable, monotonic MySQL
database-side counter.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence


DATABASE_QPS_SOURCE = "database_status_counter"
# The sampler is nominally one second, but an SSH reconnect or a busy client
# can delay one status snapshot.  Ten seconds is still only 1.67% of the
# shortest 600-second formal interval; the exact boundary distances remain in
# every result artifact and the interval must still be bracketed fail-closed.
FORMAL_DATABASE_BOUNDARY_TOLERANCE_MS = 10000
ALLOWED_ATTRIBUTION_SCOPES = {
    "dedicated_mysql_instance",
    "registered_workload_digest_set",
}


class FormalDatabaseQPSError(RuntimeError):
    """Database-side throughput cannot be attributed or calculated safely."""


def canonical_database_qps(
    samples: Sequence[Mapping[str, Any]], *, start_epoch_ms: int,
    end_epoch_ms: int, boundary_tolerance_ms: int = 1500,
) -> Mapping[str, Any]:
    """Return a canonical DBQPS interval from database-side counter snapshots.

    Each snapshot must contain ``epoch_ms``, monotonic ``completed_total``,
    ``source=database_status_counter`` and one explicit attribution scope.  A
    registered digest set must additionally carry its 64-character SHA-256.
    Endpoints outside the requested boundary tolerance fail closed.
    """
    if end_epoch_ms <= start_epoch_ms:
        raise FormalDatabaseQPSError("database counter interval is not positive")
    rows: list[dict[str, Any]] = []
    for raw in samples:
        if not isinstance(raw, Mapping):
            raise FormalDatabaseQPSError("database counter sample is not a mapping")
        if raw.get("source") != DATABASE_QPS_SOURCE:
            raise FormalDatabaseQPSError(
                "database counter source must be database_status_counter"
            )
        scope = raw.get("attribution_scope")
        if scope not in ALLOWED_ATTRIBUTION_SCOPES:
            raise FormalDatabaseQPSError("database counter attribution is unavailable")
        digest = raw.get("digest_set_sha256")
        if scope == "registered_workload_digest_set" and not (
            isinstance(digest, str)
            and len(digest) == 64
            and all(character in "0123456789abcdef" for character in digest)
        ):
            raise FormalDatabaseQPSError(
                "registered digest attribution requires digest_set_sha256"
            )
        epoch = raw.get("epoch_ms")
        counter = raw.get("completed_total")
        if (
            isinstance(epoch, bool) or not isinstance(epoch, (int, float))
            or isinstance(counter, bool) or not isinstance(counter, (int, float))
            or not math.isfinite(float(epoch))
            or not math.isfinite(float(counter))
            or float(counter) < 0.0
        ):
            raise FormalDatabaseQPSError("database counter sample is not finite")
        rows.append({**dict(raw), "epoch_ms": int(epoch), "completed_total": float(counter)})
    rows.sort(key=lambda row: row["epoch_ms"])
    if len(rows) < 2:
        raise FormalDatabaseQPSError("database counter requires at least two samples")
    if any(
        newer["epoch_ms"] <= older["epoch_ms"]
        or newer["completed_total"] < older["completed_total"]
        for older, newer in zip(rows, rows[1:])
    ):
        raise FormalDatabaseQPSError("database counter is not strictly timed and monotonic")
    identities = {
        (row["attribution_scope"], row.get("digest_set_sha256"), row.get("counter_name"))
        for row in rows
    }
    if len(identities) != 1:
        raise FormalDatabaseQPSError("database counter identity changed within one point")

    starts = [row for row in rows if row["epoch_ms"] <= start_epoch_ms]
    ends = [row for row in rows if row["epoch_ms"] >= end_epoch_ms]
    if not starts or not ends:
        raise FormalDatabaseQPSError("database counter does not bracket the selected window")
    start = starts[-1]
    end = ends[0]
    if (
        start_epoch_ms - start["epoch_ms"] > boundary_tolerance_ms
        or end["epoch_ms"] - end_epoch_ms > boundary_tolerance_ms
    ):
        raise FormalDatabaseQPSError("database counter boundary coverage is too sparse")
    elapsed_seconds = (end["epoch_ms"] - start["epoch_ms"]) / 1000.0
    if elapsed_seconds <= 0.0:
        raise FormalDatabaseQPSError("database counter endpoints are not ordered")
    completed = end["completed_total"] - start["completed_total"]
    db_qps = completed / elapsed_seconds
    if not math.isfinite(db_qps) or db_qps < 0.0:
        raise FormalDatabaseQPSError("calculated DBQPS is invalid")
    scope, digest, counter_name = next(iter(identities))
    return {
        "reliable": True,
        "source": DATABASE_QPS_SOURCE,
        "attribution_scope": scope,
        "digest_set_sha256": digest,
        "counter_name": counter_name,
        "requested_start_epoch_ms": start_epoch_ms,
        "requested_end_epoch_ms": end_epoch_ms,
        "boundary_tolerance_ms": boundary_tolerance_ms,
        "start_boundary_distance_ms": start_epoch_ms - start["epoch_ms"],
        "end_boundary_distance_ms": end["epoch_ms"] - end_epoch_ms,
        "start_epoch_ms": start["epoch_ms"],
        "end_epoch_ms": end["epoch_ms"],
        "start_completed_total": start["completed_total"],
        "end_completed_total": end["completed_total"],
        "completed_delta": completed,
        "elapsed_seconds": elapsed_seconds,
        "db_qps": db_qps,
    }

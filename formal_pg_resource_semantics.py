"""Frozen PostgreSQL resource-utilization semantics for the PG5 campaign."""

from __future__ import annotations

import datetime as dt
import math
import statistics
from typing import Any, Dict, List, Mapping, Sequence, Tuple


SEMANTICS_NAME = "pg5_io_total_throughput_utilization_v1"


class FormalPgResourceError(RuntimeError):
    """Raised when a frozen resource sample is missing, negative, or non-finite."""


def _positive_profile_value(profile: Mapping[str, Any], key: str) -> float:
    try:
        value = float(profile[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise FormalPgResourceError(f"missing finite positive resource cap: {key}") from exc
    if not math.isfinite(value) or value <= 0.0:
        raise FormalPgResourceError(f"resource cap must be finite and positive: {key}={value!r}")
    return value


def derive_resource_samples(
    rows: Sequence[Mapping[str, Any]],
    profile: Mapping[str, Any],
) -> List[Dict[str, float]]:
    """Derive per-interval utilization with the frozen sum/total formula."""
    cpu_quota_cores = _positive_profile_value(profile, "cpu_quota_cores")
    memory_bytes = _positive_profile_value(profile, "memory_bytes")
    io_read_bps = _positive_profile_value(profile, "io_read_bps")
    io_write_bps = _positive_profile_value(profile, "io_write_bps")
    io_total_bps = io_read_bps + io_write_bps
    values: List[Dict[str, float]] = []
    for previous, current in zip(rows, rows[1:]):
        seconds = (int(current["monotonic_ns"]) - int(previous["monotonic_ns"])) / 1e9
        if seconds <= 0:
            continue
        read_bps = (int(current["io_read_bytes"]) - int(previous["io_read_bytes"])) / seconds
        write_bps = (int(current["io_write_bytes"]) - int(previous["io_write_bytes"])) / seconds
        total_bps = read_bps + write_bps
        io_utilization = total_bps / io_total_bps
        cpu_raw = (
            (int(current["cpu_usage_ns"]) - int(previous["cpu_usage_ns"])) / 1e9
        ) / seconds / cpu_quota_cores
        memory_working_set = int(
            current.get("memory_working_set_bytes", current["memory_current_bytes"])
        )
        memory_raw = memory_working_set / memory_bytes
        memory_current_raw = int(current["memory_current_bytes"]) / memory_bytes
        raw_utilizations = {
            "cpu": cpu_raw,
            "memory": memory_raw,
            "io": io_utilization,
        }
        invalid_raw = {
            name: value
            for name, value in raw_utilizations.items()
            if not math.isfinite(value) or value < 0.0
        }
        if invalid_raw:
            raise FormalPgResourceError(
                f"resource utilization counters are non-finite or negative: {invalid_raw}"
            )
        values.append(
            {
                "start_epoch": dt.datetime.fromisoformat(
                    str(previous["timestamp_utc"]).replace("Z", "+00:00")
                ).timestamp(),
                "end_epoch": dt.datetime.fromisoformat(
                    str(current["timestamp_utc"]).replace("Z", "+00:00")
                ).timestamp(),
                "seconds": seconds,
                "cpu_utilization": cpu_raw,
                "memory_utilization": memory_raw,
                "io_utilization": io_utilization,
                "cpu_utilization_raw": cpu_raw,
                "memory_utilization_raw": memory_raw,
                "memory_current_utilization_raw": memory_current_raw,
                "memory_working_set_bytes": float(memory_working_set),
                "memory_utilization_definition": "(usage_in_bytes-total_inactive_file)/limit_in_bytes",
                "io_utilization_raw": io_utilization,
                "read_bps": read_bps,
                "write_bps": write_bps,
                "total_bps": total_bps,
            }
        )
    return values


def normalize_derived_resource_samples(
    samples: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Normalize persisted runner samples before producing formal aggregates.

    The frozen point runner persists ``read_bps`` and ``write_bps`` but older
    runner workspaces may omit their redundant sum, ``total_bps``.  Formal
    aggregation requires the explicit sum so that the I/O identity remains
    auditable.  Fill only that derived field and fail closed on missing,
    negative, non-finite, or internally inconsistent resource values.
    """
    normalized: List[Dict[str, Any]] = []
    required_fields = (
        "cpu_utilization",
        "memory_utilization",
        "io_utilization",
        "read_bps",
        "write_bps",
        "memory_working_set_bytes",
    )
    for index, sample in enumerate(samples):
        row = dict(sample)
        parsed: Dict[str, float] = {}
        for key in required_fields:
            try:
                value = float(row[key])
            except (KeyError, TypeError, ValueError) as exc:
                raise FormalPgResourceError(
                    f"derived resource sample {index} is missing a finite field: {key}"
                ) from exc
            if not math.isfinite(value) or value < 0.0:
                raise FormalPgResourceError(
                    f"derived resource sample {index} field is invalid: {key}={value!r}"
                )
            parsed[key] = value

        expected_total_bps = parsed["read_bps"] + parsed["write_bps"]
        if "total_bps" in row:
            try:
                actual_total_bps = float(row["total_bps"])
            except (TypeError, ValueError) as exc:
                raise FormalPgResourceError(
                    f"derived resource sample {index} has invalid total_bps"
                ) from exc
            if not math.isfinite(actual_total_bps) or actual_total_bps < 0.0:
                raise FormalPgResourceError(
                    f"derived resource sample {index} has invalid total_bps={actual_total_bps!r}"
                )
            if not math.isclose(
                actual_total_bps,
                expected_total_bps,
                rel_tol=1e-12,
                abs_tol=1e-9,
            ):
                raise FormalPgResourceError(
                    f"derived resource sample {index} total_bps differs from read_bps + write_bps"
                )
        row.update(parsed)
        row["total_bps"] = expected_total_bps
        normalized.append(row)
    return normalized


def summarize_resource_samples(
    samples: Sequence[Mapping[str, float]],
    profile: Mapping[str, Any],
) -> Dict[str, Any]:
    """Return the formal aggregate persisted by the wrapper and optimizer."""
    if not samples:
        raise FormalPgResourceError("formal resource sample set is empty")

    def average(key: str) -> float:
        values = [float(item[key]) for item in samples]
        if not values or any(not math.isfinite(value) for value in values):
            raise FormalPgResourceError(f"formal resource sample field is invalid: {key}")
        return statistics.fmean(values)

    caps = {
        "cpu_quota_cores": _positive_profile_value(profile, "cpu_quota_cores"),
        "memory_bytes": _positive_profile_value(profile, "memory_bytes"),
        "read_cap_bps": _positive_profile_value(profile, "io_read_bps"),
        "write_cap_bps": _positive_profile_value(profile, "io_write_bps"),
    }
    caps["io_total_cap_bps"] = caps["read_cap_bps"] + caps["write_cap_bps"]
    return {
        "semantics": SEMANTICS_NAME,
        "raw_range_validation": "finite_and_nonnegative_no_upper_clip",
        "io_formula": "(read_bps + write_bps) / (read_cap_bps + write_cap_bps)",
        "caps": caps,
        "sample_count": len(samples),
        "aggregate": {
            "cpu_utilization": average("cpu_utilization"),
            "memory_utilization": average("memory_utilization"),
            "io_utilization": average("io_utilization"),
            "read_bps": average("read_bps"),
            "write_bps": average("write_bps"),
            "total_bps": average("total_bps"),
            "memory_working_set_bytes": average("memory_working_set_bytes"),
        },
    }


def normalize_runtime_resource(
    formal_resource: Mapping[str, Any],
    capability: Mapping[str, float],
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Normalize one persisted formal aggregate for optimizer acceptance."""
    if formal_resource.get("semantics") != SEMANTICS_NAME:
        raise FormalPgResourceError("formal resource semantics marker is missing or changed")
    aggregate = formal_resource.get("aggregate")
    caps = formal_resource.get("caps")
    if not isinstance(aggregate, Mapping) or not isinstance(caps, Mapping):
        raise FormalPgResourceError("formal resource aggregate or caps are missing")
    for key in ("read_cap_bps", "write_cap_bps", "io_total_cap_bps"):
        _positive_profile_value(caps, key)
    expected_total_cap = float(caps["read_cap_bps"]) + float(caps["write_cap_bps"])
    if not math.isclose(float(caps["io_total_cap_bps"]), expected_total_cap, rel_tol=0.0, abs_tol=1e-9):
        raise FormalPgResourceError("formal total I/O cap differs from read cap plus write cap")
    utilizations: Dict[str, float] = {}
    for key in ("cpu", "memory", "io"):
        try:
            raw = float(aggregate[f"{key}_utilization"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FormalPgResourceError(f"missing formal aggregate utilization: {key}") from exc
        if not math.isfinite(raw) or raw < 0.0:
            raise FormalPgResourceError(
                f"formal aggregate utilization is non-finite or negative: {key}={raw!r}"
            )
        utilizations[key] = raw * 100.0
    expected_io = float(aggregate["total_bps"]) / expected_total_cap
    if not math.isclose(utilizations["io"] / 100.0, expected_io, rel_tol=1e-12, abs_tol=1e-12):
        raise FormalPgResourceError("formal aggregate I/O utilization differs from raw total/cap total")
    normalized = {
        "cpu": utilizations["cpu"] / 100.0 * float(capability["cpu"]),
        "memory": utilizations["memory"] / 100.0 * float(capability["memory"]),
        "io": utilizations["io"] / 100.0 * float(capability["io"]),
        "io_read": float(aggregate["read_bps"]) / 1024 ** 2,
        "io_write": float(aggregate["write_bps"]) / 1024 ** 2,
    }
    return normalized, utilizations

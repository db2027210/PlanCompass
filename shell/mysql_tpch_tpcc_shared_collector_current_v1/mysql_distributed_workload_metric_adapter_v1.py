#!/usr/bin/env python3
"""Exact native-marker adapters for Algorithm C debug qualification.

HyBench and BenchBase must emit one cumulative completion marker per second
with native cumulative latency percentiles.  This adapter never derives
throughput from MySQL counters and never invents latency.  A missing, stale,
partial, or non-finite native marker fails closed.
"""

from __future__ import print_function

import sys
sys.dont_write_bytecode = True

import json
import math
import os
from pathlib import Path
import re
import time


HYBENCH = re.compile(r"HYBENCH_FORMAL_SAMPLE_JSON=(\{[^\n]+\})")
BENCHBASE = re.compile(r"BENCHBASE_FORMAL_SAMPLE_JSON=(\{[^\n]+\})")
SQL_QUERY_NATIVE = re.compile(
    r"DEBUG_SQL_METRIC_SAMPLE_JSON=(\{[^\n]+\})"
)
HYBENCH_SESSION = re.compile(r"HYBENCH_FORMAL_SESSION_JSON=(\{[^\n]+\})")
HYBENCH_RESET = re.compile(r"HYBENCH_FORMAL_WARMUP_RESET=(\{[^\n]+\})")
BENCHBASE_SESSION = re.compile(r"BENCHBASE_FORMAL_SESSION_JSON=(\{[^\n]+\})")
BENCHBASE_RESET = re.compile(r"BENCHBASE_FORMAL_WARMUP_RESET=(\{[^\n]+\})")


class WorkloadMetricAdapterError(RuntimeError):
    pass


def validate_measurement_transition(kind, native_stdout_path):
    text = Path(native_stdout_path).read_text(
        encoding="utf-8", errors="strict"
    )
    if kind == "hybench_xp":
        sessions = HYBENCH_SESSION.findall(text)
        resets = HYBENCH_RESET.findall(text)
    elif kind == "benchbase_tpcc":
        sessions = BENCHBASE_SESSION.findall(text)
        resets = BENCHBASE_RESET.findall(text)
    else:
        raise WorkloadMetricAdapterError("unsupported native workload adapter")
    if len(sessions) != 1 or len(resets) != 1:
        raise WorkloadMetricAdapterError(
            "native warmup session/reset cardinality differs"
        )
    try:
        session = json.loads(sessions[0])
        reset = json.loads(resets[0])
    except ValueError as exc:
        raise WorkloadMetricAdapterError(
            "native warmup session/reset JSON invalid: %s" % exc
        )
    started = _integer(session.get("started_epoch_ms"), "session start")
    expected = _integer(
        session.get("measurement_start_epoch_ms"),
        "session measurement start",
    )
    reset_epoch = _integer(reset.get("epoch_ms"), "warmup reset epoch")
    if (
        _integer(session.get("warmup_seconds"), "session warmup") != 60
        or _integer(
            session.get("maximum_measurement_seconds"),
            "maximum measurement",
        ) != 600
        or _integer(reset.get("warmup_seconds"), "reset warmup") != 60
        or expected != started + 60000
        or reset_epoch - started < 59000
        or reset_epoch - started > 61000
    ):
        raise WorkloadMetricAdapterError(
            "native warmup timing differs from frozen 60 seconds"
        )
    if kind == "hybench_xp":
        if (
            _integer(reset.get("xap_or_ap_total"), "reset XAP/AP total") != 0
            or _integer(reset.get("xtp_total"), "reset XTP total") != 0
        ):
            raise WorkloadMetricAdapterError(
                "native HyBench warmup counters were not reset"
            )
    elif _integer(
        reset.get("completed_total"), "reset completion total"
    ) != 0:
        raise WorkloadMetricAdapterError(
            "native BenchBase warmup counters were not reset"
        )
    return {
        "started_epoch_ms": started,
        "measurement_start_epoch_ms": expected,
        "reset_epoch_ms": reset_epoch,
    }


def _integer(value, label):
    if isinstance(value, bool):
        raise WorkloadMetricAdapterError("%s is not an integer" % label)
    try:
        converted = int(value)
    except (TypeError, ValueError):
        raise WorkloadMetricAdapterError("%s is not an integer" % label)
    if converted < 0 or float(value) != converted:
        raise WorkloadMetricAdapterError("%s is not a non-negative integer" % label)
    return converted


def _number(value, label):
    if isinstance(value, bool):
        raise WorkloadMetricAdapterError("%s is not numeric" % label)
    try:
        converted = float(value)
    except (TypeError, ValueError):
        raise WorkloadMetricAdapterError("%s is not numeric" % label)
    if not math.isfinite(converted) or converted < 0:
        raise WorkloadMetricAdapterError("%s is not finite/non-negative" % label)
    return converted


def _last_marker(kind, text, attempt_id=None):
    if kind == "hybench_xp":
        matches = HYBENCH.findall(text)
    elif kind == "benchbase_tpcc":
        matches = BENCHBASE.findall(text)
    elif kind == "sql_query_native":
        matches = SQL_QUERY_NATIVE.findall(text)
    else:
        raise WorkloadMetricAdapterError("unsupported native workload adapter")
    if not matches:
        raise WorkloadMetricAdapterError("native workload marker is absent")
    try:
        row = json.loads(matches[-1])
    except ValueError as exc:
        raise WorkloadMetricAdapterError("native workload marker JSON invalid: %s" % exc)
    if not isinstance(row, dict):
        raise WorkloadMetricAdapterError("native workload marker is not an object")
    if kind == "sql_query_native":
        if (
            row.get("schema_version")
            != "mysql_algorithm_c_debug_workload_metric_sample_v1"
            or row.get("qualification_attempt_id") != attempt_id
        ):
            raise WorkloadMetricAdapterError(
                "native SQL workload marker attempt/schema differs"
            )
        epoch_seconds = _number(
            row.get("epoch_seconds"), "native SQL epoch_seconds"
        )
        epoch_ms = int(round(epoch_seconds * 1000.0))
        completed = _integer(
            row.get("completed_total"), "native SQL completion"
        )
        _integer(row.get("sequence"), "native SQL sequence")
        percentile_keys = (
            "latency_p50_ms",
            "latency_p95_ms",
            "latency_p99_ms",
        )
        if all(key in row for key in percentile_keys):
            latency = {
                key: _number(row.get(key), key)
                for key in percentile_keys
            }
            if not (
                latency["latency_p50_ms"] <= latency["latency_p95_ms"]
                <= latency["latency_p99_ms"]
            ):
                raise WorkloadMetricAdapterError(
                    "native SQL latency percentiles are unordered"
                )
        elif "query_duration_ms" in row:
            latency = {
                "query_duration_ms": _number(
                    row.get("query_duration_ms"), "query_duration_ms"
                )
            }
        else:
            raise WorkloadMetricAdapterError(
                "native SQL latency evidence is absent"
            )
        return epoch_ms, completed, latency
    if row.get("measurement_phase") is not True:
        raise WorkloadMetricAdapterError("native workload is outside measurement phase")
    epoch_ms = _integer(row.get("epoch_ms"), "native epoch_ms")
    if kind == "hybench_xp":
        completed = _integer(
            row.get("xap_or_ap_total"), "HyBench XAP completion"
        ) + _integer(row.get("xtp_total"), "HyBench XTP completion")
    else:
        completed = _integer(row.get("completed_total"), "BenchBase completion")
    latency = {
        key: _number(row.get(key), key)
        for key in ("latency_p50_ms", "latency_p95_ms", "latency_p99_ms")
    }
    if not (
        latency["latency_p50_ms"] <= latency["latency_p95_ms"]
        <= latency["latency_p99_ms"]
    ):
        raise WorkloadMetricAdapterError("native latency percentiles are unordered")
    return epoch_ms, completed, latency


def _append_jsonl(path, value):
    path = Path(path)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise WorkloadMetricAdapterError("metric stream path is unsafe")
    payload = (
        json.dumps(
            value, ensure_ascii=True, sort_keys=True, allow_nan=False
        )
        + "\n"
    ).encode("utf-8")
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(str(path), flags, 0o600)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def emit_fresh_sample(
    kind,
    native_stdout_path,
    metric_stream_path,
    attempt_id,
    sequence,
    previous_native_epoch_ms,
    timeout_seconds=2.5,
):
    deadline = time.monotonic() + float(timeout_seconds)
    last_error = None
    while True:
        try:
            source = Path(native_stdout_path)
            if source.is_symlink() or not source.is_file():
                raise WorkloadMetricAdapterError("native stdout is absent")
            epoch_ms, completed, latency = _last_marker(
                kind,
                source.read_text(encoding="utf-8", errors="strict"),
                attempt_id,
            )
            if (
                previous_native_epoch_ms is not None
                and epoch_ms <= previous_native_epoch_ms
            ):
                raise WorkloadMetricAdapterError("native workload marker is stale")
            if abs(time.time() * 1000.0 - epoch_ms) > 1500.0:
                raise WorkloadMetricAdapterError("native workload marker age exceeded")
            row = {
                "schema_version":
                    "mysql_algorithm_c_debug_workload_metric_sample_v1",
                "qualification_attempt_id": attempt_id,
                "sequence": int(sequence),
                "epoch_seconds": epoch_ms / 1000.0,
                "completed_total": completed,
                "native_adapter_kind": kind,
                "native_marker_epoch_ms": epoch_ms,
            }
            row.update(latency)
            _append_jsonl(metric_stream_path, row)
            return epoch_ms
        except (OSError, UnicodeError, WorkloadMetricAdapterError) as exc:
            last_error = exc
        if time.monotonic() >= deadline:
            raise WorkloadMetricAdapterError(
                "native workload metric missing/stale/timed out: %s" % last_error
            )
        time.sleep(0.02)

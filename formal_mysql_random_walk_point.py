#!/usr/bin/env python3
"""Execute one fresh-template MySQL point for Session 5.

This module deliberately does not acquire or release the campaign host lock.
The complete optimization Run owns that lock; callers must pass the frozen Run
attempt id and retain the lock across every invocation.
"""

from __future__ import annotations

from dataclasses import asdict
import csv
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import time
from typing import Any, Mapping, Sequence

import formal_mysql_campaign_20260721 as common
from formal_acceptance_policy import LivePoint
from formal_baseline_gates import PressureEvidence
from formal_mysql_random_walk_metrics import (
    MeasurementMetricError,
    cumulative_interval_rates,
    event_interval_rates,
    summarize_live_point,
)
from formal_random_walk_artifacts import (
    append_jsonl,
    atomic_write_json,
    fsync_artifact_tree,
    sha256_file,
    write_checksum_manifest,
)


WARMUP_SECONDS = 60
MEASUREMENT_SECONDS = 600
ATTRIBUTION_CLASSES = Path(__file__).resolve().parent / "formal_random_walk_clients_20260721/classes"
ATTRIBUTION_SOURCE = (
    Path(__file__).resolve().parent
    / "formal_random_walk_clients_20260721/formal/mysql/AttributingMysqlDriver.java"
)
ATTRIBUTION_CLASS = ATTRIBUTION_CLASSES / "formal/mysql/AttributingMysqlDriver.class"
POINT_ID = re.compile(r"(?:baseline_search|candidate_[1-9][0-9]?|default_validation_[123]|final_validation_[123])")


class FormalPointError(RuntimeError):
    """A point cannot produce a policy-eligible live replay."""


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise FormalPointError(f"JSON object required: {path}")
    return value


def _validate_params(params: Mapping[str, float]) -> dict[str, float]:
    search = _json(common.SHARED_FROZEN_ROOT / "search_spaces.json")["mysql"]
    if set(params) != set(common.COST_NAMES):
        raise FormalPointError("point does not contain the exact frozen eight costs")
    result: dict[str, float] = {}
    for name in common.COST_NAMES:
        value = float(params[name])
        bounds = search["parameters"][name]
        if (
            bounds.get("type") != "continuous"
            or bounds.get("transform") != "log"
            or not math.isfinite(value)
            or value < float(bounds["min"])
            or value > float(bounds["max"])
        ):
            raise FormalPointError(f"cost value is outside the frozen log space: {name}={value}")
        result[name] = value
    return result


def _cost_table_snapshot(port: int) -> dict[str, Any]:
    server = common._tabular(
        port,
        "SELECT cost_name,cost_value,last_update,comment,default_value "
        "FROM mysql.server_cost ORDER BY cost_name;",
    )
    engine = common._tabular(
        port,
        "SELECT engine_name,device_type,cost_name,cost_value,last_update,comment,default_value "
        "FROM mysql.engine_cost ORDER BY engine_name,device_type,cost_name;",
    )
    if not server or not engine:
        raise FormalPointError("full MySQL cost-table snapshot is empty")
    return {
        "server_cost_columns": ["cost_name", "cost_value", "last_update", "comment", "default_value"],
        "server_cost_rows": server,
        "engine_cost_columns": [
            "engine_name", "device_type", "cost_name", "cost_value", "last_update", "comment", "default_value"
        ],
        "engine_cost_rows": engine,
    }


def _apply_costs(port: int, params: Mapping[str, float], output: Path) -> dict[str, Any]:
    requested = _validate_params(params)
    before = _cost_table_snapshot(port)
    atomic_write_json(output / "cost_tables_before.json", before, replace=False)
    counts = {
        "server_target_rows": int(
            common._mysql(
                port,
                "SELECT COUNT(*) FROM mysql.server_cost WHERE cost_name IN "
                "('row_evaluate_cost','key_compare_cost','disk_temptable_create_cost',"
                "'disk_temptable_row_cost','memory_temptable_create_cost','memory_temptable_row_cost');",
            ).strip()
        ),
        "engine_target_rows": int(
            common._mysql(
                port,
                "SELECT COUNT(*) FROM mysql.engine_cost WHERE engine_name='default' AND device_type=0 "
                "AND cost_name IN ('io_block_read_cost','memory_block_read_cost');",
            ).strip()
        ),
    }
    if counts != {"server_target_rows": 6, "engine_target_rows": 2}:
        raise FormalPointError(f"cost update target cardinality differs: {counts}")
    evidence = common.apply_costs(port, requested)
    if evidence.get("status") != "passed" or set(evidence.get("values", {})) != set(common.COST_NAMES):
        raise FormalPointError("shared cost apply did not return exact passed evidence")
    after = _cost_table_snapshot(port)
    atomic_write_json(output / "cost_tables_after.json", after, replace=False)
    result = {**evidence, "target_cardinality": counts, "requested": requested}
    atomic_write_json(output / "cost_apply.json", result, replace=False)
    return result


def _counter_delta(samples: Sequence[Mapping[str, Any]], name: str) -> int:
    if len(samples) < 2:
        raise FormalPointError("database counter coverage is insufficient")
    result = int(samples[-1]["status_counters"][name]) - int(samples[0]["status_counters"][name])
    if result < 0:
        raise FormalPointError(f"database counter regressed: {name}")
    return result


def _hybench_command(
    output: Path,
    benchmark_config: Mapping[str, Any],
    workload: str,
    duration: int,
) -> tuple[list[str | Path], Path]:
    if not ATTRIBUTION_SOURCE.is_file() or not ATTRIBUTION_CLASS.is_file():
        raise FormalPointError("frozen-run per-AT attribution adapter is absent")
    runtime, config = common._hybench_runtime(output, benchmark_config, workload, duration)
    text = config.read_text(encoding="utf-8")
    original_url = str(benchmark_config["jdbc_url"])
    formal_url = original_url.replace("jdbc:mysql:", "jdbc:formal-mysql:", 1)
    replacements = {
        "classname=com.mysql.cj.jdbc.Driver\n": "classname=formal.mysql.AttributingMysqlDriver\n",
        f"url={original_url}\n": f"url={formal_url}\n",
    }
    for old, new in replacements.items():
        if text.count(old) != 1:
            raise FormalPointError(f"HyBench runtime adaptation target cardinality differs: {old.strip()}")
        text = text.replace(old, new)
    descriptor = os.open(config, os.O_WRONLY | os.O_TRUNC | os.O_CLOEXEC)
    try:
        data = text.encode("utf-8")
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise FormalPointError("short HyBench private-config rewrite")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    mode = "xp" if workload == "hybench_xp" else "ap"
    classpath = os.pathsep.join(
        (
            str(ATTRIBUTION_CLASSES),
            str(common.FORMAL_CLASSES),
            str(common.MYSQL_CONNECTOR),
            str(common.HYBENCH_JAR),
        )
    )
    return [
        "java", "-cp", classpath, "com.hybench.HyBenchFormalMain",
        mode, config, common.HYBENCH_ROOT / "conf/stmt_mysql.toml",
    ], runtime


def _percentile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise FormalPointError("latency series is empty")
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * probability
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] * (high - position) + ordered[high] * (position - low)


def _hybench_metrics(
    output: Path,
    sampling: Mapping[str, Any],
    workload: str,
) -> tuple[dict[str, Any], Sequence[float] | None, Sequence[float] | None, Sequence[float] | None, dict[str, Any]]:
    stdout = Path(str(sampling["stdout_path"])).read_text(encoding="utf-8")
    progress = [
        json.loads(item)
        for item in re.findall(r"HYBENCH_FORMAL_SAMPLE_JSON=(\{[^\n]+\})", stdout)
    ]
    resets = [
        json.loads(item)
        for item in re.findall(r"HYBENCH_FORMAL_WARMUP_RESET=(\{[^\n]+\})", stdout)
    ]
    finals = [
        json.loads(item)
        for item in re.findall(r"HYBENCH_FORMAL_METRICS_JSON=(\{[^\n]+\})", stdout)
    ]
    if len(resets) != 1 or len(finals) != 1:
        raise FormalPointError(
            f"HyBench marker cardinality differs: reset={len(resets)} final={len(finals)}"
        )
    start_ms = int(resets[0]["epoch_ms"])
    end_ms = start_ms + MEASUREMENT_SECONDS * 1000
    measured = [
        row
        for row in progress
        if row.get("measurement_phase") is True and start_ms <= int(row["epoch_ms"]) <= end_ms
    ]
    if len(measured) < math.ceil(MEASUREMENT_SECONDS * 0.9):
        raise FormalPointError("HyBench one-second progress coverage is below 90%")
    resource_epochs = [int(row["epoch_ms"]) for row in sampling["resource_samples"]]
    ap_rates = cumulative_interval_rates(
        resource_epochs,
        [(int(row["epoch_ms"]), float(row["xap_or_ap_total"])) for row in measured],
        start_ms=start_ms,
        end_ms=end_ms,
    )
    tp_rates = cumulative_interval_rates(
        resource_epochs,
        [(int(row["epoch_ms"]), float(row["xtp_total"])) for row in measured],
        start_ms=start_ms,
        end_ms=end_ms,
    )
    final = finals[0]
    latency_p95 = float(final["latency_p95_ms"])
    latency_p99 = float(final["latency_p99_ms"])
    if latency_p95 <= 0.0 or latency_p99 < latency_p95:
        raise FormalPointError("HyBench did not produce valid post-warmup latency percentiles")
    last = measured[-1]
    metrics: dict[str, Any] = {
        "qps": statistics.fmean(ap_rates) if workload == "hybench_ap" else None,
        "xap_qps": statistics.fmean(ap_rates) if workload == "hybench_xp" else None,
        "xtp_tps": statistics.fmean(tp_rates) if workload == "hybench_xp" else None,
        "query_count": int(last["xap_or_ap_total"]),
        "transaction_count": int(last["xtp_total"]),
        "latency_p95_ms": latency_p95,
        "latency_p99_ms": latency_p99,
        "progress_sample_count": len(measured),
        "measurement_start_epoch_ms": start_ms,
        "measurement_end_epoch_ms": end_ms,
    }
    attribution: dict[str, Any] = {
        "status": "not_applicable" if workload != "hybench_xp" else "failed_closed",
        "at1_to_at6": None,
    }
    if workload == "hybench_xp":
        event_path = output / "at_outcomes.jsonl"
        if event_path.is_file() and not event_path.is_symlink():
            counts = {
                f"AT{index}": {"commits": 0, "rollbacks": 0, "timeouts": 0, "errors": 0}
                for index in range(1, 7)
            }
            boundary_transaction_outcomes = 0
            for line in event_path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                epoch = int(row["epoch_ms"])
                if not start_ms < epoch <= end_ms:
                    continue
                at_type = row.get("at_type")
                outcome = row.get("outcome")
                if at_type not in counts or outcome not in counts[at_type]:
                    raise FormalPointError("invalid per-AT attribution event")
                counts[at_type][outcome] += 1
                if outcome in {"commits", "rollbacks"} and (
                    epoch <= start_ms + 1000 or epoch > end_ms - 1000
                ):
                    boundary_transaction_outcomes += 1
            missing = [
                name for name, row in counts.items()
                if int(row["commits"]) + int(row["rollbacks"]) == 0
            ]
            transaction_outcomes = sum(
                int(row["commits"]) + int(row["rollbacks"]) for row in counts.values()
            )
            progress_total = int(last["xtp_total"])
            reconciliation_delta = transaction_outcomes - progress_total
            boundary_tolerance = boundary_transaction_outcomes + 16
            reconciliation_passed = abs(reconciliation_delta) <= boundary_tolerance
            attribution = {
                "status": "passed" if not missing and reconciliation_passed else "failed_closed",
                "at1_to_at6": counts,
                "event_path": str(event_path),
                "missing_transaction_outcome_types": missing,
                "transaction_outcome_total": transaction_outcomes,
                "hybench_progress_transaction_total": progress_total,
                "reconciliation_delta": reconciliation_delta,
                "boundary_transaction_outcomes": boundary_transaction_outcomes,
                "reconciliation_tolerance_at_boundary": boundary_tolerance,
                "reconciliation_passed": reconciliation_passed,
            }
        else:
            attribution["reason"] = (
                "per-AT commit/rollback/timeout/error instrumentation is absent; "
                "global rollback counters and HyBench histograms are not substituted"
            )
    if workload == "hybench_xp":
        return metrics, None, ap_rates, tp_rates, attribution
    return metrics, ap_rates, None, None, attribution


def _sql_event_metrics(
    output: Path,
    sampling: Mapping[str, Any],
    workload: str,
) -> tuple[dict[str, Any], Sequence[float]]:
    start_ms = int(sampling["started_epoch_ms"]) + WARMUP_SECONDS * 1000
    end_ms = start_ms + MEASUREMENT_SECONDS * 1000
    resource_epochs = [int(row["epoch_ms"]) for row in sampling["resource_samples"]]
    if workload == "job":
        event_path = output / f"job_{WARMUP_SECONDS + MEASUREMENT_SECONDS}_events.jsonl"
        rows = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines() if line]
        successes = [
            row for row in rows
            if row.get("status") == "complete" and start_ms < int(row["epoch_ms"]) <= end_ms
        ]
        failures = [
            row for row in rows
            if row.get("status") != "complete" and start_ms < int(row["epoch_ms"]) <= end_ms
        ]
        right_censored = [
            row for row in rows
            if row.get("status") == "complete"
            and row.get("counted_before_deadline") is False
            and int(row["epoch_ms"]) > end_ms
        ]
        stdout = Path(str(sampling["stdout_path"])).read_text(encoding="utf-8")
        final_markers = re.findall(r"FORMAL_SQL_METRICS_JSON=(\{[^\n]+\})", stdout)
        if len(final_markers) != 1:
            raise FormalPointError("JOB final client metric marker cardinality differs")
        final_client = json.loads(final_markers[0])
        completion_epochs = [int(row["epoch_ms"]) for row in successes]
        latencies = [float(row["latency_ms"]) for row in successes]
        rates = event_interval_rates(
            resource_epochs, completion_epochs, start_ms=start_ms, end_ms=end_ms
        )
        metrics = {
            "qps": statistics.fmean(rates), "xap_qps": None, "xtp_tps": None,
            "query_count": len(successes), "transaction_count": None,
            "latency_p95_ms": _percentile(latencies, 0.95),
            "latency_p99_ms": _percentile(latencies, 0.99),
            "client_failure_count": len(failures),
            "inflight_at_measurement_end": int(final_client["inflight_at_end"]),
            "right_censored_count": len(right_censored),
            "right_censored_observed_latency_ms": [
                float(row["latency_ms"]) for row in right_censored
            ],
            "measurement_start_epoch_ms": start_ms,
            "measurement_end_epoch_ms": end_ms,
        }
        return metrics, rates

    stderr = Path(str(sampling["stderr_path"])).read_text(encoding="utf-8")
    if "FORMAL_TPCH_SEED_AGENT=4508333" not in stderr:
        raise FormalPointError("TPC-H frozen seed adapter activation marker is absent")
    raw_paths = list(output.rglob("*.raw.csv"))
    summary_paths = list(output.rglob("*.summary.json"))
    if len(raw_paths) != 1 or len(summary_paths) != 1:
        raise FormalPointError(
            f"BenchBase result cardinality differs: raw={len(raw_paths)} summary={len(summary_paths)}"
        )
    start_us = start_ms * 1000
    end_us = end_ms * 1000
    completions: list[int] = []
    latencies: list[float] = []
    right_censored_latencies: list[float] = []
    with raw_paths[0].open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            raw_start = float(row["Start Time (microseconds)"])
            query_start_us = raw_start * 1_000_000 if raw_start < 1e13 else raw_start
            latency_us = float(row["Latency (microseconds)"])
            completed_us = int(query_start_us + latency_us)
            if start_us < completed_us <= end_us:
                completions.append(completed_us // 1000)
                latencies.append(latency_us / 1000.0)
            elif start_us < query_start_us <= end_us and completed_us > end_us:
                right_censored_latencies.append(latency_us / 1000.0)
    rates = event_interval_rates(
        resource_epochs, completions, start_ms=start_ms, end_ms=end_ms
    )
    metrics = {
        "qps": statistics.fmean(rates), "xap_qps": None, "xtp_tps": None,
        "query_count": len(completions), "transaction_count": None,
        "latency_p95_ms": _percentile(latencies, 0.95),
        "latency_p99_ms": _percentile(latencies, 0.99),
        "client_failure_count": 0,
        "inflight_at_measurement_end": len(right_censored_latencies),
        "right_censored_count": len(right_censored_latencies),
        "right_censored_observed_latency_ms": right_censored_latencies,
        "measurement_start_epoch_ms": start_ms,
        "measurement_end_epoch_ms": end_ms,
        "benchbase_summary": _json(summary_paths[0]),
    }
    return metrics, rates


def _pressure(
    scenario: str,
    workload: str,
    resources: Sequence[Mapping[str, Any]],
    database: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    adapter: Any,
    adapter_after: Any,
) -> tuple[PressureEvidence, dict[str, Any]]:
    first = resources[0]
    last = resources[-1]
    memory_failcnt = int(last["memory_failcnt"]) - int(first["memory_failcnt"])
    major_faults = int(last["memory_pgmajfault"]) - int(first["memory_pgmajfault"])
    refaults = int(last["memory_workingset_refault"]) - int(first["memory_workingset_refault"])
    temp_spills = _counter_delta(database, "Created_tmp_disk_tables")
    io_bytes = (
        int(last["io_read_raw_bytes"]) - int(first["io_read_raw_bytes"])
        + int(last["io_write_raw_bytes"]) - int(first["io_write_raw_bytes"])
    )
    io_peak = max(float(row["io_utilization_ratio"]) for row in resources)
    capped = adapter.readback["read_cap_bps"] is not None and adapter.readback["write_cap_bps"] is not None
    io_throttle = bool(capped and io_bytes > 0 and io_peak >= 0.85)
    pid_unchanged = int(adapter_after.pid) == int(adapter.pid)
    io_limit_unchanged = all(
        adapter_after.readback[name] == adapter.readback[name]
        for name in ("device", "read_cap_bps", "write_cap_bps")
    )
    pressure = PressureEvidence(
        memory_psi_active=False,
        major_faults_observed=major_faults > 0,
        working_set_refaults_observed=refaults > 0,
        cache_evictions_observed=memory_failcnt > 0,
        temp_or_disk_spill_observed=temp_spills > 0,
        io_throttle_or_wait_observed=io_throttle,
        xap_completed=int(metrics.get("query_count") or 0) if workload == "hybench_xp" else 0,
        at_transactions_completed=int(metrics.get("transaction_count") or 0) if workload == "hybench_xp" else 0,
        container_pid_unchanged=pid_unchanged,
        database_process_unchanged=pid_unchanged,
        io_limit_unchanged=io_limit_unchanged,
    )
    raw = {
        "scenario": scenario,
        "memory_failcnt_delta": memory_failcnt,
        "major_faults_delta": major_faults,
        "working_set_refaults_delta": refaults,
        "created_tmp_disk_tables_delta": temp_spills,
        "io_bytes_delta": io_bytes,
        "io_utilization_peak": io_peak,
        "io_limit_present": capped,
        "container_and_database_pid_unchanged": pid_unchanged,
        "io_limit_unchanged": io_limit_unchanged,
        "io_throttle_pressure_rule": "cap_present_and_bytes_positive_and_peak_utilization_at_least_0.85",
        "decision_input": asdict(pressure),
    }
    return pressure, raw


def _classify_failure(error: BaseException, state: Mapping[str, Any] | None) -> str:
    if state and bool(state.get("OOMKilled")):
        return "oom"
    if isinstance(error, subprocess.TimeoutExpired) or "timeout" in str(error).lower():
        return "timeout"
    return "failed"


def _remove_ephemeral(output: Path) -> None:
    private = output / "private"
    if private.exists():
        shutil.rmtree(private)
    hybench_runtime = output / "hybench_runtime"
    if hybench_runtime.exists() or hybench_runtime.is_symlink():
        if hybench_runtime.is_symlink():
            hybench_runtime.unlink()
        else:
            shutil.rmtree(hybench_runtime)


def run_point(
    *,
    run_attempt_id: str,
    point_id: str,
    point_attempt: int,
    case_id: str,
    params: Mapping[str, float],
    output: Path,
    expected_correctness: Mapping[str, Any] | None,
    method: str = "random_walk",
    lock_marker_environment: str = "FORMAL_MYSQL_RANDOM_WALK_LOCK_HELD",
) -> dict[str, Any]:
    """Run one live point.  The caller must already hold the common host lock."""

    if method not in {"random_walk", "recost_gradient_descent"}:
        raise FormalPointError("unsupported formal point method")
    if lock_marker_environment not in {
        "FORMAL_MYSQL_RANDOM_WALK_LOCK_HELD",
        "FORMAL_MYSQL_RECOST_LOCK_HELD",
    }:
        raise FormalPointError("unsupported formal host-lock marker")
    if os.environ.get(lock_marker_environment) != run_attempt_id:
        raise FormalPointError("complete-Run host-lock ownership marker is absent")
    if not POINT_ID.fullmatch(point_id) or point_attempt <= 0:
        raise FormalPointError("unsafe point identifier or attempt")
    if case_id not in common.CASE_DEFINITIONS:
        raise FormalPointError(f"unknown frozen MySQL case: {case_id}")
    if output.exists() or output.is_symlink():
        raise FormalPointError(f"point output already exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    workload, scenario, template_key = common.CASE_DEFINITIONS[case_id]
    machine, fixed, benchmark, _, measurement, *_ = common._configs()
    if measurement["timing"]["warmup_seconds"] != WARMUP_SECONDS or measurement["timing"]["maximum_measurement_seconds"] != MEASUREMENT_SECONDS:
        raise FormalPointError("frozen point timing differs from the registered 60+600 execution choice")
    requested = _validate_params(params)
    started = time.monotonic()
    atomic_write_json(
        output / "point_start.json",
        {
            "schema_version": 1,
            "freeze_id": common.FREEZE_ID,
            "global_campaign_id": common.GLOBAL_CAMPAIGN_ID,
            "engine": "mysql",
            "method": method,
            "run_attempt_id": run_attempt_id,
            "point_id": point_id,
            "point_attempt": point_attempt,
            "case_id": case_id,
            "workload": workload,
            "scenario": scenario,
            "params_requested": requested,
            "measurement_duration_choice": {
                "seconds": MEASUREMENT_SECONDS,
                "registered_before_results": True,
                "reason": "use_frozen_maximum_to_preserve_one_continuous_measurement_without_abrupt_client_stop",
            },
            "started_at_utc": common.utc_now(),
        },
        replace=False,
    )
    engine = machine["engines"]["mysql"]
    template_root = Path(engine["template_root"]) / template_key
    template_manifest_path = template_root / "database_manifest.json"
    template_manifest = _json(template_manifest_path)
    working_root = Path(engine["working_root"])
    working = working_root / method / run_attempt_id / point_id / f"attempt_{point_attempt:02d}"
    datadir = working / "datadir"
    container = ""
    adapter: Any = None
    container_state: Mapping[str, Any] | None = None
    summary: dict[str, Any]
    try:
        common._copy_template(template_root / "datadir", datadir, working_root)
        container, port, adapter = common._start_container(
            machine, fixed, workload, scenario, datadir
        )
        restored = common.validate_restored_template(port, template_manifest)
        fixed_before = common.validate_fixed_settings(port, fixed, scenario)
        apply = _apply_costs(port, requested, output)
        fixed_after_apply = common.validate_fixed_settings(port, fixed, scenario)
        benchmark_path = common.BENCHMARK_CONFIG_ROOT / workload / "benchmark_config.json"
        benchmark_config = _json(benchmark_path)
        plan = common.capture_plan(
            port,
            str(benchmark_config["database"]),
            Path(str(benchmark_config["target_sql"])),
            output / "plan.json",
            parameter_int=314 if workload == "tpch" else None,
        )
        correctness = common.run_correctness(workload, benchmark_config, output)
        correctness_signature = {
            "row_count": int(correctness["row_count"]),
            "sha256": str(correctness["sha256"]),
        }
        result_match = expected_correctness is None or correctness_signature == dict(expected_correctness)
        total = WARMUP_SECONDS + MEASUREMENT_SECONDS
        if workload.startswith("hybench"):
            command, cwd = _hybench_command(output, benchmark_config, workload, total)
        elif workload == "job":
            command, cwd = common._job_command(output, benchmark_config, total)
        else:
            command, cwd = common._benchbase_command(output, benchmark_config, total)
        prior_at_log = os.environ.get("FORMAL_MYSQL_AT_EVENT_LOG")
        if workload == "hybench_xp":
            os.environ["FORMAL_MYSQL_AT_EVENT_LOG"] = str(output / "at_outcomes.jsonl")
        try:
            sampling = common.run_with_sampling(
                command,
                cwd,
                adapter,
                port,
                output,
                warmup_seconds=WARMUP_SECONDS,
                measurement_seconds=MEASUREMENT_SECONDS,
            )
        finally:
            if prior_at_log is None:
                os.environ.pop("FORMAL_MYSQL_AT_EVENT_LOG", None)
            else:
                os.environ["FORMAL_MYSQL_AT_EVENT_LOG"] = prior_at_log
        resources = list(sampling["resource_samples"])
        database_samples = list(sampling["database_samples"])
        if workload.startswith("hybench"):
            metrics, qps_samples, xap_samples, xtp_samples, at_evidence = _hybench_metrics(
                output, sampling, workload
            )
        else:
            metrics, qps_samples = _sql_event_metrics(output, sampling, workload)
            xap_samples = None
            xtp_samples = None
            at_evidence = {"status": "not_applicable", "at1_to_at6": None}
        rollback_delta = _counter_delta(database_samples, "Com_rollback")
        aborted_delta = _counter_delta(database_samples, "Aborted_clients")
        client_failures = int(metrics.get("client_failure_count") or 0)
        if workload == "hybench_xp" and at_evidence.get("at1_to_at6") is not None:
            attributed_rollbacks = sum(
                int(row["rollbacks"]) for row in at_evidence["at1_to_at6"].values()
            )
            rollback_reconciliation_delta = attributed_rollbacks - rollback_delta
            rollback_tolerance = int(at_evidence.get("reconciliation_tolerance_at_boundary", 16))
            rollback_reconciliation_passed = abs(rollback_reconciliation_delta) <= rollback_tolerance
            at_evidence["global_rollback_delta"] = rollback_delta
            at_evidence["attributed_rollback_total"] = attributed_rollbacks
            at_evidence["rollback_reconciliation_delta"] = rollback_reconciliation_delta
            at_evidence["rollback_reconciliation_tolerance_at_boundary"] = rollback_tolerance
            at_evidence["rollback_reconciliation_passed"] = rollback_reconciliation_passed
            if not rollback_reconciliation_passed:
                at_evidence["status"] = "failed_closed"
        attribution_passed = workload != "hybench_xp" or at_evidence["status"] == "passed"
        if workload == "hybench_xp" and attribution_passed:
            at_counts = at_evidence["at1_to_at6"]
            attributable_errors = sum(
                int(row["timeouts"]) + int(row["errors"]) for row in at_counts.values()
            )
        else:
            attributable_errors = 0
        error_total = max(client_failures, attributable_errors) + aborted_delta
        completed = int(metrics.get("query_count") or 0) + int(metrics.get("transaction_count") or 0)
        error_rate = error_total / max(1, completed + error_total)
        container_state = json.loads(common.run(["docker", "inspect", container]).stdout)[0]["State"]
        oom = bool(container_state.get("OOMKilled"))
        restarted = int(container_state["Pid"]) != adapter.pid or int(container_state.get("RestartCount", 0)) > 0
        metric_summary = summarize_live_point(
            measurement,
            resources,
            qps_samples=qps_samples,
            xap_samples=xap_samples,
            xtp_samples=xtp_samples,
            latency_p95_ms=float(metrics["latency_p95_ms"]),
            latency_p99_ms=float(metrics["latency_p99_ms"]),
            correctness_passed=bool(result_match and attribution_passed),
            error_rate=error_rate,
            oom=oom,
            database_restarted=restarted,
        )
        adapter_after = common.CgroupV1Adapter(
            container,
            adapter.device,
            machine["resource_profiles"][scenario],
        )
        pressure, pressure_raw = _pressure(
            scenario, workload, resources, database_samples, metrics, adapter, adapter_after
        )
        fixed_after = common.validate_fixed_settings(port, fixed, scenario)
        final_cost = common._cost_readback(port)
        atomic_write_json(
            output / "cost_tables_after_measurement.json",
            _cost_table_snapshot(port),
            replace=False,
        )
        params_match = all(
            math.isclose(final_cost[name], requested[name], rel_tol=1e-12, abs_tol=1e-15)
            for name in common.COST_NAMES
        )
        hard_valid = bool(
            metric_summary.live_point.correctness_passed
            and metric_summary.live_point.stable
            and metric_summary.live_point.sample_coverage >= 0.9
            and metric_summary.live_point.error_rate <= 0.01
            and not oom
            and not restarted
            and params_match
        )
        if hard_valid:
            rejection_kind = None
        elif oom:
            rejection_kind = "oom"
        elif not metric_summary.live_point.correctness_passed:
            rejection_kind = "correctness_rejected"
        elif not metric_summary.live_point.stable:
            rejection_kind = "unstable"
        else:
            rejection_kind = "failed"
        summary = {
            "schema_version": 1,
            "freeze_id": common.FREEZE_ID,
            "global_campaign_id": common.GLOBAL_CAMPAIGN_ID,
            "engine": "mysql",
            "method": method,
            "status": "complete" if hard_valid else "rejected",
            "rejection_kind": rejection_kind,
            "run_attempt_id": run_attempt_id,
            "point_id": point_id,
            "point_attempt": point_attempt,
            "case_id": case_id,
            "workload": workload,
            "scenario": scenario,
            "completed_at_utc": common.utc_now(),
            "wall_time_seconds": time.monotonic() - started,
            "template_manifest_path": str(template_manifest_path),
            "template_manifest_sha256": sha256_file(template_manifest_path),
            "template_validation": restored,
            "benchmark_config_path": str(benchmark_path),
            "benchmark_config_sha256": sha256_file(benchmark_path),
            "resource_readback": adapter.readback,
            "resource_readback_after": adapter_after.readback,
            "attribution_adapter": {
                "source_path": str(ATTRIBUTION_SOURCE),
                "source_sha256": sha256_file(ATTRIBUTION_SOURCE),
                "class_path": str(ATTRIBUTION_CLASS),
                "class_sha256": sha256_file(ATTRIBUTION_CLASS),
                "delegate_jdbc_url_matches_frozen_benchmark_config": True,
            },
            "fixed_settings_before": fixed_before,
            "cost_apply": apply,
            "fixed_settings_after_apply": fixed_after_apply,
            "params_applied": final_cost,
            "params_applied_match": params_match,
            "plan_fingerprint_sha256": plan["structural_fingerprint_sha256"],
            "correctness": correctness,
            "correctness_signature": correctness_signature,
            "correctness_matches_run_baseline": result_match,
            "at_outcomes": at_evidence,
            "global_rollback_delta": rollback_delta,
            "global_aborted_clients_delta": aborted_delta,
            "metrics": metrics,
            "live_point": asdict(metric_summary.live_point),
            "stability": metric_summary.stability.as_dict(),
            "pressure_evidence": pressure_raw,
            "pressure_decision_input": asdict(pressure),
            "sampling": {
                key: value
                for key, value in sampling.items()
                if key not in {"resource_samples", "database_samples"}
            },
            "oom": oom,
            "database_restarted": restarted,
            "container_state_after": container_state,
            "fixed_settings_after_measurement": fixed_after,
            "hard_gate_passed": hard_valid,
        }
    except BaseException as error:
        if container:
            try:
                container_state = json.loads(common.run(["docker", "inspect", container]).stdout)[0]["State"]
            except BaseException:
                container_state = None
        summary = {
            "schema_version": 1,
            "freeze_id": common.FREEZE_ID,
            "global_campaign_id": common.GLOBAL_CAMPAIGN_ID,
            "engine": "mysql",
            "method": method,
            "status": _classify_failure(error, container_state),
            "run_attempt_id": run_attempt_id,
            "point_id": point_id,
            "point_attempt": point_attempt,
            "case_id": case_id,
            "workload": workload,
            "scenario": scenario,
            "failed_at_utc": common.utc_now(),
            "wall_time_seconds": time.monotonic() - started,
            "error_type": type(error).__name__,
            "error": str(error),
            "container_state_after": container_state,
        }
    finally:
        if container:
            try:
                common._remove_container(container)
            except BaseException as cleanup_error:
                summary = {
                    **locals().get("summary", {}),
                    "status": "failed",
                    "cleanup_error": f"{type(cleanup_error).__name__}: {cleanup_error}",
                }
        try:
            common._cleanup_working(working, working_root)
        except BaseException as cleanup_error:
            summary = {
                **locals().get("summary", {}),
                "status": "failed",
                "cleanup_error": f"{type(cleanup_error).__name__}: {cleanup_error}",
            }
    summary["cleanup_complete"] = bool(not working.exists() and (not container or not common._owned_container(container)))
    _remove_ephemeral(output)
    if workload == "tpch":
        summary["redacted_generated_benchbase_configs"] = common._redact_benchbase_configs(output)
    summary["artifact_flush"] = fsync_artifact_tree(output)
    summary["checksums_complete"] = True
    atomic_write_json(output / "point_result.json", summary, replace=False)
    fsync_artifact_tree(output)
    checksum_evidence = write_checksum_manifest(output)
    # The checksum manifest is authoritative.  Keep its digest out of the
    # checksummed point_result to avoid a circular self-description.
    summary["checksum_evidence_runtime"] = checksum_evidence
    return summary

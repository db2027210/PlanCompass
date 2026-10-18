#!/usr/bin/env python3
"""Run the frozen JOB SQL mix with transaction-local planner parameters."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import random
import statistics
import threading
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple


PARAMETERS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
PARAMETER_APPLICATION_SEMANTICS = (
    "SET LOCAL per_candidate per_worker immediate_readback "
    "readback_before_workload rollback_after_candidate rollback_after_query"
)


class RunnerError(RuntimeError):
    """The workload cannot produce admissible formal evidence."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    content = (
        json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(temporary), str(path))


def append_jsonl(path: Path, value: Mapping[str, Any], lock: threading.Lock) -> None:
    line = json.dumps(dict(value), ensure_ascii=False, sort_keys=True) + "\n"
    with lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RunnerError(f"JSON root must be an object: {path}")
    return value


def validate_params(
    raw: Mapping[str, Any],
    ranges: Mapping[str, Any],
) -> Dict[str, float]:
    if set(raw) != set(PARAMETERS) or set(ranges) != set(PARAMETERS):
        raise RunnerError("candidate and range contracts must contain exactly seven parameters")
    values: Dict[str, float] = {}
    for name in PARAMETERS:
        value = float(raw[name])
        bounds = ranges[name]
        if not isinstance(bounds, dict):
            raise RunnerError(f"invalid range for {name}")
        lower = float(bounds["min_value"])
        upper = float(bounds["max_value"])
        if not math.isfinite(value) or not lower <= value <= upper:
            raise RunnerError(f"candidate parameter outside frozen range: {name}={value}")
        values[name] = value
    return values


def load_queries(config: Mapping[str, Any]) -> List[Dict[str, Any]]:
    sql_dir = Path(str(config.get("sql_dir") or "")).resolve()
    names = config.get("query_sequence")
    weights = config.get("query_weights")
    hashes = config.get("query_sha256")
    if not isinstance(names, list) or not names:
        raise RunnerError("job_session_runner.query_sequence must be non-empty")
    if not isinstance(weights, list) or len(weights) != len(names):
        raise RunnerError("JOB query weights do not match query sequence")
    if not isinstance(hashes, dict):
        raise RunnerError("JOB query SHA contract is missing")
    parsed: List[Dict[str, Any]] = []
    for raw_name, raw_weight in zip(names, weights):
        name = str(raw_name)
        if Path(name).name != name or not name.endswith(".sql"):
            raise RunnerError(f"unsafe JOB query name: {name}")
        weight = int(raw_weight)
        if weight <= 0:
            raise RunnerError(f"invalid JOB query weight: {name}={weight}")
        path = sql_dir / name
        if not path.is_file():
            raise RunnerError(f"JOB SQL file missing: {path}")
        actual_sha = sha256_file(path)
        if hashes.get(name) != actual_sha:
            raise RunnerError(f"JOB SQL SHA mismatch: {name}")
        parsed.append({
            "query_id": path.stem,
            "name": name,
            "path": path,
            "sql": path.read_text(encoding="utf-8"),
            "sha256": actual_sha,
            "weight": weight,
        })
    if sum(item["weight"] for item in parsed) != 100:
        raise RunnerError("JOB query weights must sum to 100")
    return parsed


def balanced_cycle(queries: Sequence[Mapping[str, Any]], seed: int) -> List[Mapping[str, Any]]:
    cycle: List[Mapping[str, Any]] = list(queries)
    tail: List[Mapping[str, Any]] = []
    for query in queries:
        tail.extend([query] * (int(query["weight"]) - 1))
    random.Random(seed).shuffle(tail)
    cycle.extend(tail)
    return cycle


def read_settings(connection: Any) -> Dict[str, str]:
    values: Dict[str, str] = {}
    with connection.cursor() as cursor:
        for name in PARAMETERS:
            cursor.execute("SELECT current_setting(%s)", (name,))
            row = cursor.fetchone()
            if not row:
                raise RunnerError(f"current_setting returned no row for {name}")
            values[name] = str(row[0])
    return values


def numeric_readback_matches(actual: str, expected: float) -> bool:
    try:
        # PostgreSQL renders real-valued planner GUCs with limited display
        # precision (for example 3162.277660... is read back as 3162.28).
        # Preserve fail-closed readback while accepting only display rounding.
        return abs(float(actual) - float(expected)) <= max(
            1e-12,
            abs(expected) * 1e-6,
        )
    except (TypeError, ValueError):
        return False


def apply_and_readback(cursor: Any, params: Mapping[str, float]) -> Dict[str, str]:
    readback: Dict[str, str] = {}
    for name in PARAMETERS:
        value = params[name]
        cursor.execute(f"SET LOCAL {name} = {format(value, '.17g')}")
        cursor.execute("SELECT current_setting(%s)", (name,))
        row = cursor.fetchone()
        if not row:
            raise RunnerError(f"immediate readback missing for {name}")
        actual = str(row[0])
        if not numeric_readback_matches(actual, value):
            raise RunnerError(
                f"immediate readback mismatch for {name}: actual={actual}, expected={value}"
            )
        readback[name] = actual
    return readback


def consume_result(cursor: Any) -> Tuple[int, str]:
    if cursor.description is None:
        return 0, hashlib.sha256(b"").hexdigest()
    digest = hashlib.sha256()
    count = 0
    while True:
        rows = cursor.fetchmany(1000)
        if not rows:
            break
        for row in rows:
            digest.update(
                json.dumps(row, default=str, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
            digest.update(b"\n")
            count += 1
    return count, digest.hexdigest()


def load_resource_samples(path: Path) -> List[Dict[str, float]]:
    if not path.is_file():
        raise RunnerError(f"formal resource samples missing: {path}")
    samples: List[Dict[str, float]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise RunnerError(f"invalid resource sample line {number}")
        parsed = {}
        for resource in ("cpu", "memory", "io"):
            raw = value.get(f"{resource}_utilization")
            number_value = float(raw)
            if not math.isfinite(number_value) or number_value < 0:
                raise RunnerError(f"invalid {resource} utilization at sample {number}")
            parsed[resource] = number_value
        parsed["end_epoch"] = float(value.get("end_epoch") or number)
        samples.append(parsed)
    if not samples:
        raise RunnerError("formal resource sample set is empty")
    return samples


def assess_resource_stability(
    samples: Sequence[Mapping[str, float]],
    interval_seconds: float,
    window_seconds: int,
    required_windows: int,
) -> Dict[str, Any]:
    expected_per_window = max(1, int(round(window_seconds / interval_seconds)))
    required_samples = expected_per_window * required_windows
    windows = []
    for index in range(required_windows):
        block = samples[index * expected_per_window:(index + 1) * expected_per_window]
        coverage = len(block) / float(expected_per_window)
        windows.append({
            "window_index": index,
            "sample_count": len(block),
            "coverage": coverage,
            "coverage_passed": coverage >= 0.9,
            "means": {
                resource: statistics.fmean(float(row[resource]) for row in block)
                if block else None
                for resource in ("cpu", "memory", "io")
            },
        })
    fields = []
    for resource in ("cpu", "memory", "io"):
        means = [window["means"][resource] for window in windows]
        valid = all(value is not None for value in means)
        mean = statistics.fmean(means) if valid else None
        deviation = statistics.pstdev(means) if valid and len(means) > 1 else 0.0
        coefficient = deviation / mean if mean and mean > 0 else math.inf
        fields.append({
            "field": f"{resource}_utilization",
            "mean": mean,
            "coefficient_of_variation": coefficient,
            "limit": 0.1,
            "stable": valid and coefficient <= 0.1,
        })
    passed = (
        len(samples) >= math.ceil(required_samples * 0.9)
        and all(window["coverage_passed"] for window in windows)
        and all(field["stable"] for field in fields)
    )
    return {
        "source": "job_session_local_runner",
        "passed": passed,
        "stable": passed,
        "aggregate_sample_count": len(samples),
        "expected_sample_count": required_samples,
        "complete_window_count": sum(window["coverage_passed"] for window in windows),
        "consecutive_stable_windows": required_windows if passed else 0,
        "windows": windows,
        "fields": fields,
    }


def default_connect(**kwargs: Any) -> Any:
    try:
        import psycopg2
    except ImportError as exc:
        raise RunnerError("psycopg2 is required by the JOB session runner") from exc
    return psycopg2.connect(**kwargs)


def run(
    full_config: Mapping[str, Any],
    *,
    connect: Callable[..., Any] = default_connect,
) -> Dict[str, Any]:
    runner = full_config.get("job_session_runner")
    if not isinstance(runner, dict):
        raise RunnerError("job_session_runner config is missing")
    contract = full_config.get("parameter_application_contract")
    if not isinstance(contract, dict) or contract.get("method") != "SET_LOCAL":
        raise RunnerError("session-local parameter application contract is missing")
    if (
        contract.get("readback_scope") != "every_parameter_every_candidate_every_worker"
        or contract.get("immediate_readback_required") is not True
        or contract.get("alter_system_prohibited") is not True
    ):
        raise RunnerError("parameter application contract is not formal")

    output_dir = Path(
        os.environ.get("JOB_SESSION_OUTPUT_DIR")
        or str(runner.get("output_dir") or "")
    ).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    query_log = output_dir / "measurement_queries.jsonl"
    if query_log.exists():
        raise RunnerError(f"refusing to append to existing query evidence: {query_log}")
    resource_path = Path(
        os.environ.get("JOB_RESOURCE_SAMPLES_PATH")
        or str(runner.get("resource_samples_path") or "")
    ).resolve()
    candidate_raw: Mapping[str, Any] = full_config.get("initial_params") or {}
    candidate_env = os.environ.get("JOB_CANDIDATE_PARAMS_JSON")
    if candidate_env:
        value = json.loads(candidate_env)
        if not isinstance(value, dict):
            raise RunnerError("JOB_CANDIDATE_PARAMS_JSON must be an object")
        candidate_raw = value
    params = validate_params(candidate_raw, full_config.get("param_ranges") or {})
    queries = load_queries(runner)
    workers = int(runner.get("workers") or 0)
    duration_seconds = float(runner.get("duration_seconds") or 0)
    max_queries = int(runner.get("max_queries_per_worker") or 0)
    if workers <= 0 or duration_seconds <= 0:
        raise RunnerError("workers and duration_seconds must be positive")

    endpoint = runner.get("endpoint")
    if not isinstance(endpoint, dict):
        raise RunnerError("JOB PostgreSQL endpoint is missing")
    password_env = str(endpoint.get("password_env") or "PGPASSWORD")
    password = os.environ.get(password_env)
    if password is None:
        raise RunnerError(f"PostgreSQL password environment variable is missing: {password_env}")
    dsn = {
        "host": str(endpoint.get("host") or "127.0.0.1"),
        "port": int(endpoint["port"]),
        "dbname": str(endpoint["database"]),
        "user": str(endpoint.get("user") or "postgres"),
        "password": password,
    }
    control = connect(**dsn, application_name="ra-job-session-control")
    control.autocommit = True
    try:
        global_before = read_settings(control)
    finally:
        control.close()

    state_lock = threading.Lock()
    log_lock = threading.Lock()
    stop = threading.Event()
    started = time.monotonic()
    sequence = 0
    successes: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    signatures: Dict[str, Tuple[int, str]] = {}
    closed_workers = 0
    cycles = [balanced_cycle(queries, int(runner.get("seed") or 20260727) + index) for index in range(workers)]

    def worker(worker_index: int) -> None:
        nonlocal sequence, closed_workers
        connection = connect(
            **dsn,
            application_name=f"ra-job-session-worker-{worker_index + 1}",
        )
        connection.autocommit = False
        completed = 0
        cycle = cycles[worker_index]
        try:
            while not stop.is_set() and time.monotonic() - started < duration_seconds:
                if max_queries and completed >= max_queries:
                    break
                query = cycle[completed % len(cycle)]
                began = time.monotonic()
                error: Optional[str] = None
                readback: Dict[str, str] = {}
                row_count = 0
                result_sha = ""
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("BEGIN")
                        readback = apply_and_readback(cursor, params)
                        cursor.execute(str(query["sql"]))
                        row_count, result_sha = consume_result(cursor)
                    connection.rollback()
                    post_rollback = read_settings(connection)
                    connection.rollback()
                    if post_rollback != global_before:
                        raise RunnerError(
                            f"worker {worker_index + 1} defaults changed after rollback"
                        )
                    signature = (row_count, result_sha)
                    with state_lock:
                        previous = signatures.setdefault(str(query["query_id"]), signature)
                        if previous != signature:
                            raise RunnerError(
                                f"deterministic result mismatch for {query['query_id']}"
                            )
                        sequence += 1
                        event_sequence = sequence
                        successes.append({
                            "query_id": query["query_id"],
                            "latency_ms": (time.monotonic() - began) * 1000.0,
                            "row_count": row_count,
                            "result_sha256": result_sha,
                        })
                except Exception as exc:
                    connection.rollback()
                    error = f"{type(exc).__name__}: {exc}"
                    with state_lock:
                        sequence += 1
                        event_sequence = sequence
                        failures.append({
                            "worker": worker_index + 1,
                            "query_id": query["query_id"],
                            "error": error,
                        })
                    stop.set()
                append_jsonl(
                    query_log,
                    {
                        "event": "query_complete",
                        "sequence": event_sequence,
                        "terminal": worker_index + 1,
                        "query_id": query["query_id"],
                        "sql_sha256": query["sha256"],
                        "configured_weight": query["weight"],
                        "row_count": row_count,
                        "result_sha256": result_sha,
                        "latency_ms": (time.monotonic() - began) * 1000.0,
                        "success": error is None,
                        "commit_after_query": False,
                        "rollback_after_query": True,
                        "parameter_application": "SET LOCAL",
                        "parameter_readback": readback,
                        "readback_before_workload": True,
                        "error": error,
                    },
                    log_lock,
                )
                completed += 1
        finally:
            try:
                connection.rollback()
            finally:
                connection.close()
                with state_lock:
                    closed_workers += 1

    threads = [
        threading.Thread(target=worker, args=(index,), name=f"job-worker-{index + 1}")
        for index in range(workers)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    wall_seconds = time.monotonic() - started

    final_control = connect(**dsn, application_name="ra-job-session-final-control")
    final_control.autocommit = True
    try:
        global_after = read_settings(final_control)
    finally:
        final_control.close()
    defaults_unchanged = global_after == global_before
    resource_samples = load_resource_samples(resource_path)
    interval = float(runner.get("resource_sample_interval_seconds") or 1.0)
    stability = assess_resource_stability(
        resource_samples,
        interval,
        int(runner.get("stability_window_seconds") or 30),
        int(runner.get("required_stability_windows") or 6),
    )
    resource_means = {
        resource: statistics.fmean(row[resource] for row in resource_samples)
        for resource in ("cpu", "memory", "io")
    }
    expected_query_ids = {str(query["query_id"]) for query in queries}
    observed_query_ids = {row["query_id"] for row in successes}
    correctness = (
        bool(successes)
        and not failures
        and expected_query_ids.issubset(observed_query_ids)
        and all(len(value) == 2 for value in signatures.values())
    )
    cleanup_complete = closed_workers == workers
    measurement = {
        "actual_workload_wall_seconds": wall_seconds,
        "effective_measurement_seconds": wall_seconds,
        "query_count": len(successes),
        "qps": len(successes) / wall_seconds if wall_seconds > 0 else 0.0,
        "db_qps": len(successes) / wall_seconds if wall_seconds > 0 else 0.0,
        "latency_ms": {
            "mean": statistics.fmean(row["latency_ms"] for row in successes)
            if successes else None,
            "max": max((row["latency_ms"] for row in successes), default=None),
        },
        "result_signatures_by_query": {
            query_id: [[signature[0], signature[1]]]
            for query_id, signature in sorted(signatures.items())
        },
        "observed_query_counts": {
            query_id: sum(row["query_id"] == query_id for row in successes)
            for query_id in sorted(observed_query_ids)
        },
        "correctness_passed": correctness,
        "correctness_rule": "deterministic_signature_per_query",
        "error_count": len(failures),
        "error_rate": len(failures) / max(1, len(successes) + len(failures)),
        "resource_mean_utilization": resource_means,
        "resource_stability": stability,
        "raw_resource_samples_path": str(resource_path),
        "raw_resource_samples_sha256": sha256_file(resource_path),
    }
    cleanup = {
        "cleanup_complete": cleanup_complete,
        "worker_count": workers,
        "closed_worker_connections": closed_workers,
    }
    readback_evidence = {
        "parameter_application": "SET LOCAL",
        "scope": "per_candidate_per_worker_per_query_transaction",
        "immediate_readback": True,
        "readback_before_workload": True,
        "rollback_after_candidate": True,
        "global_defaults_before": global_before,
        "global_defaults_after": global_after,
        "global_defaults_unchanged": defaults_unchanged,
    }
    complete = (
        correctness
        and cleanup_complete
        and defaults_unchanged
        and stability.get("passed") is True
    )
    result = {
        "schema_version": 1,
        "status": "complete" if complete else "failed",
        "workload": "job",
        "cost_parameters": params,
        "measurement": measurement,
        "cleanup": cleanup,
        "readback": readback_evidence,
        "failure_reasons": [
            reason
            for reason, passed in (
                ("correctness_failed", correctness),
                ("cleanup_failed", cleanup_complete),
                ("global_defaults_changed", defaults_unchanged),
                ("resource_stability_failed", stability.get("passed") is True),
            )
            if not passed
        ],
    }
    atomic_json(output_dir / "measurement_summary.json", measurement)
    atomic_json(output_dir / "point_result.json", result)
    return result


def main() -> int:
    config_path = Path(os.environ.get("OPTIMIZER_CONFIG", "")).resolve()
    if not config_path.is_file():
        print("FAIL_CLOSED: OPTIMIZER_CONFIG is missing", flush=True)
        return 42
    try:
        result = run(load_json(config_path))
    except (RunnerError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"FAIL_CLOSED: {exc}", flush=True)
        return 42
    print(json.dumps({
        "status": result["status"],
        "point_result": str(
            Path(
                os.environ.get("JOB_SESSION_OUTPUT_DIR")
                or load_json(config_path)["job_session_runner"]["output_dir"]
            ).resolve()
            / "point_result.json"
        ),
    }, sort_keys=True), flush=True)
    return 0 if result["status"] == "complete" else 42


if __name__ == "__main__":
    raise SystemExit(main())

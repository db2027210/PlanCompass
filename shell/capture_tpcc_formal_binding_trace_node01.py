#!/usr/bin/env python3
"""Run one TPCC production capture using the execution-site binding trace."""

from __future__ import annotations

from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import sys
import time

from shell.manual_mysql_sgd_r0_campaign import (
    BENCHBASE_JAR,
    Campaign,
    LANES,
    ROOT,
    atomic_json,
    sha256,
)
from shell.tpcc_subagent_heartbeat import atomic_write


EXPECTED_CANDIDATE_SHA256 = (
    "3b7dec2a6291d16b5cbb4ea3f90eb7972dc9d605c9909b4e33ac8cf21b25244c"
)
TRACE_SCHEMA = "formal_mysql_binding_trace_event_v1"
TPCC_TRANSACTIONS = ("NewOrder", "Payment", "OrderStatus", "Delivery", "StockLevel")
ENTRY_MODULE = "shell.capture_tpcc_formal_binding_trace_node01"
RUN_ID = f"formal_bindcap_tpcc_node01_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
OUTPUT = ROOT / "history/mysql_sgd_algorithm_c/tpcc_cpu/production_formal_binding_capture" / RUN_ID
TRACE_PATH = OUTPUT / "formal_binding_trace.jsonl"
HEARTBEAT = ROOT / "history/mysql_sgd_env_agents_20260723/manual_controllers/tpcc_cpu/subagent_heartbeat_formal_binding_trace.json"


def trace_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def file_bytes(path: Path) -> int:
    return path.stat().st_size if path.is_file() else 0


def check_post_start_gate(
    process: object,
    workload_log: Path,
    resource_samples: Path,
    workload_bytes_before: int,
    resource_bytes_before: int,
) -> dict:
    if process.poll() is not None:
        raise RuntimeError(
            f"BenchBase TPCC exited during 30-second post-start gate: "
            f"rc={process.returncode}"
        )
    workload_bytes_after = file_bytes(workload_log)
    resource_bytes_after = file_bytes(resource_samples)
    if workload_bytes_after <= workload_bytes_before:
        raise RuntimeError(
            "workload log did not grow during 30-second post-start gate: "
            f"before={workload_bytes_before}; after={workload_bytes_after}"
        )
    if resource_bytes_after <= resource_bytes_before:
        raise RuntimeError(
            "resource samples did not grow during 30-second post-start gate: "
            f"before={resource_bytes_before}; after={resource_bytes_after}"
        )
    return {
        "workload_bytes_before": workload_bytes_before,
        "workload_bytes_after": workload_bytes_after,
        "resource_bytes_before": resource_bytes_before,
        "resource_bytes_after": resource_bytes_after,
    }


def check_post_measurement_trace(path: Path, before: int, run_id: str) -> dict:
    after = trace_rows(path)
    if after <= before:
        raise RuntimeError(
            "FormalBindingTrace did not grow after measurement marker: "
            f"before={before}; after={after}"
        )
    return validate_trace_coverage(path, run_id)


def publish_heartbeat(status: str, current: str, evidence: str, next_action: str) -> None:
    atomic_write(HEARTBEAT, {
        "utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "current_task": "production_formal_binding_trace_capture",
        "current_pid_or_command": current,
        "mutex_state": "tpcc_lane_only;single_capture;typed_trace_only",
        "last_evidence": evidence,
        "next_action": next_action,
    })


def validate_candidate() -> str:
    if not BENCHBASE_JAR.is_file():
        raise RuntimeError(f"candidate JAR is absent: {BENCHBASE_JAR}")
    digest = sha256(BENCHBASE_JAR)
    if digest != EXPECTED_CANDIDATE_SHA256:
        raise RuntimeError(f"candidate JAR SHA mismatch: {digest}")
    return digest


def build_launch_spec(jar_sha256: str) -> dict:
    return {
        "entry_module": ENTRY_MODULE,
        "benchmark": "tpcc",
        "transactions": list(TPCC_TRANSACTIONS),
        "jar": str(BENCHBASE_JAR),
        "jar_sha256": jar_sha256,
        "trace_schema": TRACE_SCHEMA,
        "trace_environment": {
            "FORMAL_MYSQL_BINDING_TRACE_PATH": str(TRACE_PATH),
            "FORMAL_MYSQL_BINDING_TRACE_RUN_ID": RUN_ID,
            "FORMAL_MYSQL_BINDING_TRACE_SCHEMA": TRACE_SCHEMA,
            "FORMAL_MYSQL_BINDING_TRACE_BENCHMARK": "tpcc",
            "FORMAL_MYSQL_BINDING_TRACE_SEED_SOURCE": "TPCCWorker.rng.nextInt",
        },
        "binding_authority": "JDBC_execution_site",
        "general_log_binding_authority": False,
    }


def validate_trace_coverage(path: Path, run_id: str) -> dict:
    if not path.is_file():
        raise RuntimeError(f"FormalBindingTrace output is absent: {path}")
    observed = set()
    event_count = 0
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise RuntimeError(
                    f"invalid FormalBindingTrace JSON at line {line_number}: {error}"
                ) from error
            if event.get("run_id") != run_id:
                raise RuntimeError(
                    f"FormalBindingTrace run_id mismatch at line {line_number}: "
                    f"{event.get('run_id')!r} != {run_id!r}"
                )
            if event.get("benchmark") != "tpcc":
                raise RuntimeError(
                    f"FormalBindingTrace benchmark mismatch at line {line_number}: "
                    f"{event.get('benchmark')!r} != 'tpcc'"
                )
            seed_source = event.get("seed_derivation", {}).get("source")
            if seed_source != "TPCCWorker.rng.nextInt":
                raise RuntimeError(
                    f"FormalBindingTrace seed source mismatch at line {line_number}: "
                    f"{seed_source!r} != 'TPCCWorker.rng.nextInt'"
                )
            if "general_log" in json.dumps(event, sort_keys=True).lower():
                raise RuntimeError(f"general-log evidence marker at line {line_number}")
            query_id = event.get("query_id")
            if query_id in TPCC_TRANSACTIONS:
                observed.add(query_id)
            event_count += 1
    missing = [name for name in TPCC_TRANSACTIONS if name not in observed]
    if missing:
        raise RuntimeError(f"missing TPCC transactions: {', '.join(missing)}")
    return {
        "event_count": event_count,
        "expected_transactions": list(TPCC_TRANSACTIONS),
        "observed_transactions": [name for name in TPCC_TRANSACTIONS if name in observed],
    }


def write_capture_result(
    output: Path,
    trace_path: Path,
    run_id: str,
    jar_sha256: str,
    session_final: dict,
) -> dict:
    coverage = validate_trace_coverage(trace_path, run_id)
    result = {
        "schema_version": "tpcc_formal_binding_trace_capture_result_v1",
        "status": "FORMAL_BINDING_TRACE_CAPTURED",
        "benchmark": "tpcc",
        "transactions": list(TPCC_TRANSACTIONS),
        "transaction_coverage": coverage,
        "run_id": run_id,
        "jar": str(BENCHBASE_JAR),
        "jar_sha256": jar_sha256,
        "trace_path": str(trace_path),
        "trace_rows": coverage["event_count"],
        "trace_sha256": sha256(trace_path),
        "session_final": session_final,
        "binding_authority": "JDBC_execution_site",
        "general_log_binding_authority": False,
    }
    atomic_json(output / "capture_result.json", result)
    return result


def process_command(process: object) -> str:
    args = getattr(process, "args", None)
    if isinstance(args, (list, tuple)):
        return " ".join(str(value) for value in args)
    return str(args or "unknown")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    jar_sha256 = validate_candidate()
    if args.validate_only:
        print(json.dumps({
            "status": "PASS_ENTRY_VALIDATION",
            "benchmark": "tpcc",
            "transactions": list(TPCC_TRANSACTIONS),
            "jar": str(BENCHBASE_JAR),
            "jar_sha256": jar_sha256,
            "trace_schema": TRACE_SCHEMA,
            "run_id": RUN_ID,
            "general_log_binding_authority": False,
            "launch_spec": build_launch_spec(jar_sha256),
        }, sort_keys=True))
        return 0

    if OUTPUT.exists():
        raise RuntimeError(f"capture output already exists: {OUTPUT}")
    OUTPUT.mkdir(parents=True)
    campaign = Campaign(
        LANES["tpcc"], OUTPUT / "campaign", RUN_ID,
        measurement_seconds=600, minimum_measurement_seconds=180,
    )
    campaign.output.mkdir(parents=True, exist_ok=True)
    session = None
    process = None
    stopped = False
    try:
        publish_heartbeat(
            "PREPARING", f"capture_entry_pid={os.getpid()} command={sys.argv!r}",
            f"candidate_sha256={jar_sha256};run_id={RUN_ID}",
            "prepare remote lane and inject trace environment",
        )
        campaign.prepare_remote()
        campaign.start_tunnel()
        os.environ.update({
            "FORMAL_MYSQL_BINDING_TRACE_PATH": str(TRACE_PATH),
            "FORMAL_MYSQL_BINDING_TRACE_RUN_ID": RUN_ID,
            "FORMAL_MYSQL_BINDING_TRACE_SCHEMA": TRACE_SCHEMA,
            "FORMAL_MYSQL_BINDING_TRACE_BENCHMARK": "tpcc",
            "FORMAL_MYSQL_BINDING_TRACE_SEED_SOURCE": "TPCCWorker.rng.nextInt",
        })
        atomic_json(OUTPUT / "capture_gate.json", {
            "schema_version": "tpcc_formal_binding_trace_capture_gate_v1",
            "status": "TRACE_ENV_INJECTED",
            "benchmark": "tpcc",
            "transactions": list(TPCC_TRANSACTIONS),
            "jar": str(BENCHBASE_JAR),
            "jar_sha256": jar_sha256,
            "run_id": RUN_ID,
            "trace_path": str(TRACE_PATH),
            "trace_schema": TRACE_SCHEMA,
            "binding_authority": "JDBC_execution_site",
            "general_log_binding_authority": False,
        })
        campaign.start_sampler()
        session = campaign.make_session()
        campaign.session = session
        session.start(warmup_seconds=60, maximum_measurement_seconds=600)
        process = session.process
        if process is None or process.pid <= 0:
            raise RuntimeError("BenchBase TPCC process did not expose a real PID")
        command = process_command(process)
        workload_log = campaign.output / "workload.stdout.log"
        resource_samples = campaign.output / "resource_samples.jsonl"
        workload_bytes_before = file_bytes(workload_log)
        resource_bytes_before = file_bytes(resource_samples)
        publish_heartbeat(
            "CAPTURE_STARTING", f"capture_entry_pid={os.getpid()} workload_pid={process.pid} command={command}",
            f"candidate_sha256={jar_sha256};run_id={RUN_ID};trace_rows={trace_rows(TRACE_PATH)};"
            f"workload_bytes={workload_bytes_before};resource_bytes={resource_bytes_before}",
            "confirm 30-second PID, workload-log, and resource-sample growth",
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(
                    "BenchBase TPCC exited during 30-second post-start gate: "
                    f"rc={process.returncode}"
                )
            time.sleep(0.5)
        runtime_growth = check_post_start_gate(
            process, workload_log, resource_samples,
            workload_bytes_before, resource_bytes_before,
        )
        trace_before_measurement = trace_rows(TRACE_PATH)
        publish_heartbeat(
            "CAPTURE_STARTING", f"capture_entry_pid={os.getpid()} workload_pid={process.pid} command={command}",
            f"candidate_sha256={jar_sha256};run_id={RUN_ID};trace_rows_before_measurement={trace_before_measurement};"
            f"workload_bytes_before={runtime_growth['workload_bytes_before']};"
            f"workload_bytes_after={runtime_growth['workload_bytes_after']};"
            f"resource_bytes_before={runtime_growth['resource_bytes_before']};"
            f"resource_bytes_after={runtime_growth['resource_bytes_after']}",
            "wait for initial measurement marker before requiring trace growth",
        )
        campaign.wait_initial_marker()
        session.wait_until_measurement(180)
        coverage = check_post_measurement_trace(TRACE_PATH, trace_before_measurement, RUN_ID)
        final = session.stop(180)
        stopped = True
        result = write_capture_result(OUTPUT, TRACE_PATH, RUN_ID, jar_sha256, final)
        publish_heartbeat(
            "CAPTURE_COMPLETE", f"capture_entry_pid={os.getpid()} workload_pid={process.pid} command={command}",
            f"run_id={RUN_ID};trace_rows={result['trace_rows']};trace_sha256={result['trace_sha256']}",
            "build strict registry from typed FormalBindingTrace events",
        )
        return 0
    except BaseException as error:
        publish_heartbeat(
            "CAPTURE_FAILED", f"capture_entry_pid={os.getpid()} workload_pid={getattr(process, 'pid', None)} command={process_command(process) if process is not None else sys.argv!r}",
            f"run_id={RUN_ID};error={type(error).__name__}: {error}",
            "inspect exact capture failure before any downstream action",
        )
        raise
    finally:
        if session is not None and getattr(session, "process", None) is not None and not stopped:
            try:
                session.stop(180)
            except BaseException:
                pass
        campaign.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())

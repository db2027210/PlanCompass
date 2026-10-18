#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple


SIGTERM = getattr(signal, "SIGTERM", 15)
SIGKILL = getattr(signal, "SIGKILL", 9)


def read_props(path: Path) -> Dict[str, str]:
    """read props."""
    result: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def pg_endpoint(props: Dict[str, str]) -> Tuple[str, str, str, str, str]:
    """pg endpoint."""
    url = props.get("url") or props.get("url_tp") or props.get("url_ap") or ""
    match = re.search(r"jdbc:postgresql://([^/:?]+):(\d+)/([^?]+)", url)
    if not match:
        raise RuntimeError(f"cannot parse PostgreSQL JDBC URL: {url!r}")
    return (
        match.group(1),
        match.group(2),
        match.group(3),
        props.get("username") or props.get("username_ap") or "postgres",
        props.get("password") or props.get("password_ap") or "",
    )


def set_text(root: ET.Element, xpath: str, value: object) -> None:
    node = root.find(xpath)
    if node is None:
        raise RuntimeError(f"BenchBase template is missing XML node {xpath}")
    node.text = str(value)


def set_or_create_text(root: ET.Element, xpath: str, value: object) -> None:
    node = root.find(xpath)
    if node is None:
        parent_path, separator, tag = xpath.rpartition("/")
        if not separator or not parent_path or not tag:
            raise RuntimeError(f"cannot create XML path {xpath}")
        parent = root.find(parent_path)
        if parent is None:
            raise RuntimeError(f"BenchBase template is missing XML parent {parent_path}")
        node = ET.SubElement(parent, tag)
    node.text = str(value)


def latest_summary(results_dir: Path, started: float) -> Optional[Path]:
    matches = [
        path
        for path in results_dir.glob("tpcc_*.summary.json")
        if path.stat().st_mtime >= started - 2.0
    ]
    return max(matches, key=lambda path: path.stat().st_mtime_ns, default=None)


def parse_pgbench_metrics(text: str) -> Tuple[float, float, int]:
    tps_matches = re.findall(r"^tps = ([0-9.]+)", text, flags=re.MULTILINE)
    latency_matches = re.findall(r"^latency average = ([0-9.]+) ms", text, flags=re.MULTILINE)
    transaction_matches = re.findall(
        r"^number of transactions actually processed: (\d+)",
        text,
        flags=re.MULTILINE,
    )
    tps = float(tps_matches[-1]) if tps_matches else 0.0
    latency_ms = float(latency_matches[-1]) if latency_matches else 0.0
    transactions = int(transaction_matches[-1]) if transaction_matches else 0
    return tps, latency_ms, transactions


def tail_lines(path: Path, limit: int = 160) -> List[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-limit:]


def deadline_policy(duration_s: int) -> Dict[str, float]:
    startup_exit_grace_s = max(
        1.0,
        float(os.environ.get("TPCC_MIX_STARTUP_EXIT_GRACE_SECONDS", "210")),
    )
    term_wait_s = max(0.1, float(os.environ.get("TPCC_MIX_TERM_WAIT_SECONDS", "10")))
    kill_wait_s = max(0.1, float(os.environ.get("TPCC_MIX_KILL_WAIT_SECONDS", "5")))
    deadline_s = float(duration_s) + startup_exit_grace_s

    outer_raw = os.environ.get("TPCC_MIX_OUTER_TIMEOUT_SECONDS")
    outer_timeout_s: Optional[float]
    if outer_raw:
        outer_timeout_s = float(outer_raw)
    elif duration_s < 300:
        # The stable-point warmup has a fixed 300 second subprocess timeout.
        outer_timeout_s = 300.0
    else:
        # Long measurements are managed by the stable-point runner, not that warmup timeout.
        outer_timeout_s = None

    cleanup_deadline_s = deadline_s + term_wait_s + kill_wait_s
    if outer_timeout_s is not None and cleanup_deadline_s >= outer_timeout_s:
        raise ValueError(
            "TPC-C mix deadline must leave cleanup headroom before the outer timeout: "
            f"duration={duration_s}s deadline={deadline_s}s cleanup_deadline={cleanup_deadline_s}s "
            f"outer_timeout={outer_timeout_s}s"
        )
    return {
        "deadline_seconds": deadline_s,
        "startup_exit_grace_seconds": startup_exit_grace_s,
        "term_wait_seconds": term_wait_s,
        "kill_wait_seconds": kill_wait_s,
        "outer_timeout_seconds": outer_timeout_s or 0.0,
    }


def wait_for_processes_until(
    processes: List[Tuple[str, subprocess.Popen[Any]]],
    deadline_mono: float,
    poll_seconds: float = 0.1,
) -> Tuple[bool, Dict[str, Optional[int]]]:
    while True:
        returncodes = {name: process.poll() for name, process in processes}
        if all(returncode is not None for returncode in returncodes.values()):
            return False, returncodes
        remaining = deadline_mono - time.monotonic()
        if remaining <= 0.0:
            return True, returncodes
        time.sleep(min(poll_seconds, remaining))


def wait_for_workload_until(
    processes: List[Tuple[str, subprocess.Popen[Any]]],
    deadline_mono: float,
    completion_probe: Any,
    phase_finished_probe: Optional[Any] = None,
    phase_release: Optional[Any] = None,
    poll_seconds: float = 0.1,
) -> Tuple[bool, Dict[str, Optional[int]], bool]:
    phase_released = False
    while True:
        returncodes = {name: process.poll() for name, process in processes}
        if all(returncode is not None for returncode in returncodes.values()):
            return False, returncodes, False
        if completion_probe():
            return False, returncodes, True
        if (
            not phase_released
            and phase_finished_probe is not None
            and phase_release is not None
            and phase_finished_probe()
        ):
            phase_release()
            phase_released = True
        remaining = deadline_mono - time.monotonic()
        if remaining <= 0.0:
            return True, returncodes, False
        time.sleep(min(poll_seconds, remaining))


def _send_group_signal(process_group_id: int, signal_number: int) -> None:
    if not hasattr(os, "killpg"):
        raise RuntimeError("TPC-C process-group cleanup requires POSIX os.killpg")
    try:
        os.killpg(process_group_id, signal_number)
    except ProcessLookupError:
        pass


def terminate_process_group(
    process: subprocess.Popen[Any],
    term_wait_s: float,
    kill_wait_s: float,
) -> Dict[str, Any]:
    actions: List[str] = []
    _send_group_signal(process.pid, SIGTERM)
    actions.append("term_group")
    try:
        returncode = process.wait(timeout=term_wait_s)
        actions.append("reaped_after_term")
        return {"pid": process.pid, "returncode": returncode, "actions": actions}
    except subprocess.TimeoutExpired:
        _send_group_signal(process.pid, SIGKILL)
        actions.append("kill_group")
    try:
        returncode = process.wait(timeout=kill_wait_s)
        actions.append("reaped_after_kill")
        return {"pid": process.pid, "returncode": returncode, "actions": actions}
    except subprocess.TimeoutExpired:
        actions.append("reap_timeout_after_kill")
        return {
            "pid": process.pid,
            "returncode": process.poll(),
            "actions": actions,
            "reaped": False,
        }


def emit_technical_failure(reason: str, **details: Any) -> None:
    payload: Dict[str, Any] = {
        "event": "technical_failure",
        "reason": reason,
        "counts_as_candidate_replay": False,
        "updates_incumbent": False,
        "increments_accepted_shrink": False,
    }
    payload.update(details)
    print(
        "TPCC_TECHNICAL_FAILURE_JSON=" + json.dumps(payload, sort_keys=True),
        file=sys.stderr,
        flush=True,
    )


def cleanup_finished_completed_workload(
    cleanup_results: Dict[str, Dict[str, Any]],
    completion_probe: Any,
) -> bool:
    return bool(cleanup_results) and all(
        result.get("reaped", True) for result in cleanup_results.values()
    ) and bool(completion_probe())


def native_termination_marker_seen(lines: List[str]) -> bool:
    marker = "waiting for all terminals to finish"
    return any(marker in " ".join(line.split()).casefold() for line in lines)


def native_measurement_marker_seen(lines: List[str]) -> bool:
    return any("PHASE START" in line or "MEASURE" in line for line in lines)


def wait_for_native_measurement_start(
    process: subprocess.Popen[Any],
    native_log: Path,
    deadline_mono: float,
    poll_seconds: float = 0.1,
) -> Tuple[bool, Optional[int]]:
    while True:
        returncode = process.poll()
        if returncode is not None:
            return False, returncode
        if native_measurement_marker_seen(tail_lines(native_log, limit=80)):
            return True, None
        remaining = deadline_mono - time.monotonic()
        if remaining <= 0.0:
            return False, None
        time.sleep(min(poll_seconds, remaining))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-t", "--mode", default="generic")
    parser.add_argument("-c", "--props", required=True)
    parser.add_argument("-f", "--statement-file", default="")
    args = parser.parse_args()

    props = read_props(Path(args.props))
    host, port, dbname, user, password = pg_endpoint(props)
    source_root = Path(os.environ.get("BENCHBASE_SOURCE_ROOT") or "/home/anonymous/benchbase-postgres").resolve()
    runtime_parent = Path(
        os.environ.get("BENCHBASE_RUNTIME_ROOT")
        or "/disk1/resource_adaptive/bruteforce_pg17_runs_20260718/runs/benchbase_runtime"
    ).resolve()
    runtime_root = runtime_parent / f"pg_{port}_tpcc_sensitive"
    runtime_root.mkdir(parents=True, exist_ok=True)
    for name in ("benchbase.jar", "lib", "config"):
        source = source_root / name
        target = runtime_root / name
        if not target.exists():
            target.symlink_to(source, target_is_directory=source.is_dir())
    (runtime_root / "results").mkdir(parents=True, exist_ok=True)

    template = Path(
        os.environ.get("BENCHBASE_XML_TEMPLATE")
        or source_root / "config/postgres/sample_tpcc_config.xml"
    ).resolve()
    tree = ET.parse(template)
    root = tree.getroot()
    duration_s = max(1, int(os.environ.get("GENERIC_RUN_SECONDS", "120")))
    native_terminals = max(1, int(os.environ.get("BENCHBASE_TERMINALS", "32")))
    sensitive_clients = max(0, int(os.environ.get("TPCC_SENSITIVE_CLIENTS", "1")))
    weights = os.environ.get("BENCHBASE_TPCC_WEIGHTS", "45,43,4,4,4")
    jdbc = (
        f"jdbc:postgresql://{host}:{port}/{dbname}?sslmode=disable"
        "&ApplicationName=tpcc-sensitive-native&reWriteBatchedInserts=true"
    )
    set_text(root, "url", jdbc)
    set_text(root, "username", user)
    set_text(root, "password", password)
    set_text(root, "scalefactor", os.environ.get("BENCHBASE_SCALEFACTOR", "1"))
    set_text(root, "terminals", native_terminals)
    set_text(root, "works/work/weights", weights)
    set_or_create_text(root, "works/work/time", duration_s)

    generated_dir = runtime_root / "generated_configs"
    generated_dir.mkdir(parents=True, exist_ok=True)
    run_token = f"{os.getpid()}_{time.time_ns()}"
    generated_xml = generated_dir / f"tpcc_sensitive_{run_token}.xml"
    native_log = generated_dir / f"tpcc_sensitive_{run_token}.native.log"
    sensitive_log = generated_dir / f"tpcc_sensitive_{run_token}.sensitive.log"
    tree.write(generated_xml, encoding="utf-8", xml_declaration=True)

    sensitive_sql = Path(
        os.environ.get("TPCC_SENSITIVE_SQL_FILE")
        or Path(__file__).resolve().parents[1] / "config/workloads/tpcc_sensitive_stocklevel.sql"
    ).resolve()
    if sensitive_clients and not sensitive_sql.is_file():
        raise FileNotFoundError(f"sensitive SQL file not found: {sensitive_sql}")

    native_command = [
        "java",
        os.environ.get("BENCHBASE_JAVA_XMX", "-Xmx4g"),
        "-jar",
        "benchbase.jar",
        "-b",
        "tpcc",
        "-c",
        str(generated_xml),
        "--execute=true",
    ]
    sensitive_command = [
        "pgbench",
        "-n",
        "-h",
        host,
        "-p",
        port,
        "-U",
        user,
        "-d",
        dbname,
        "-c",
        str(sensitive_clients),
        "-j",
        str(max(1, sensitive_clients)),
        "-T",
        str(duration_s),
        "--random-seed=20260721",
        "-P",
        "30",
        "-f",
        str(sensitive_sql),
    ]

    env = os.environ.copy()
    env["PGPASSWORD"] = password
    started = time.time()
    policy = deadline_policy(duration_s)
    deadline_mono = time.monotonic() + policy["deadline_seconds"]
    print(
        "[tpcc-sensitive] "
        f"duration_s={duration_s} native_terminals={native_terminals} "
        f"sensitive_clients={sensitive_clients} weights={weights} "
        f"deadline_s={policy['deadline_seconds']:.3f}",
        flush=True,
    )
    with native_log.open("w", encoding="utf-8") as native_handle:
        native_proc = subprocess.Popen(
            native_command,
            cwd=str(runtime_root),
            env=env,
            stdout=native_handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    sensitive_proc: Optional[subprocess.Popen[str]] = None
    if sensitive_clients:
        native_ready, native_startup_returncode = wait_for_native_measurement_start(
            native_proc,
            native_log,
            deadline_mono,
        )
        if not native_ready:
            cleanup = {}
            if native_proc.poll() is None:
                cleanup["native"] = terminate_process_group(
                    native_proc,
                    policy["term_wait_seconds"],
                    policy["kill_wait_seconds"],
                )
            emit_technical_failure(
                "native_measurement_start_failed",
                native_returncode=native_startup_returncode,
                cleanup=cleanup,
            )
            return native_startup_returncode or 124
        print(
            "TPCC_START_BARRIER_JSON="
            + json.dumps(
                {"native_measurement_ready": True, "sensitive_started": True},
                sort_keys=True,
            ),
            flush=True,
        )
        with sensitive_log.open("w", encoding="utf-8") as sensitive_handle:
            sensitive_proc = subprocess.Popen(
                sensitive_command,
                cwd=str(runtime_root),
                env=env,
                stdout=sensitive_handle,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )

    processes: List[Tuple[str, subprocess.Popen[Any]]] = [("native", native_proc)]
    if sensitive_proc is not None:
        processes.append(("sensitive", sensitive_proc))
    completed_summary: Optional[Path] = None
    lifecycle_release: Dict[str, Any] = {"attempted": False}
    lifecycle_state: Dict[str, bool] = {"native_termination_seen": False}

    def workload_completed() -> bool:
        nonlocal completed_summary
        candidate = latest_summary(runtime_root / "results", started)
        if candidate is None:
            return False
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            float(payload["Throughput (requests/second)"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return False
        completed_summary = candidate
        return True

    def native_phase_finished() -> bool:
        if not lifecycle_state["native_termination_seen"]:
            lifecycle_state["native_termination_seen"] = native_termination_marker_seen(
                tail_lines(native_log, limit=80)
            )
        sensitive_finished = sensitive_proc is None or sensitive_proc.poll() == 0
        return lifecycle_state["native_termination_seen"] and sensitive_finished

    def release_native_sessions() -> None:
        lifecycle_release["attempted"] = True
        sql = (
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE application_name = 'tpcc-sensitive-native' "
            "AND pid <> pg_backend_pid() AND pg_cancel_backend(pid);"
        )
        command = [
            "psql", "-h", host, "-p", port, "-U", user, "-d", dbname,
            "-v", "ON_ERROR_STOP=1", "-At", "-c", sql,
        ]
        try:
            result = subprocess.run(
                command,
                cwd=str(runtime_root),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=15.0,
                check=False,
            )
            lifecycle_release.update(
                returncode=result.returncode,
                cancelled_backends=(result.stdout or "").strip(),
            )
        except subprocess.TimeoutExpired:
            lifecycle_release.update(returncode=124, error="cancel_timeout")
        print(
            "TPCC_LIFECYCLE_RELEASE_JSON="
            + json.dumps(lifecycle_release, sort_keys=True),
            flush=True,
        )

    timed_out, observed_returncodes, summary_completed = wait_for_workload_until(
        processes,
        deadline_mono,
        workload_completed,
        native_phase_finished,
        release_native_sessions,
    )
    cleanup_results: Dict[str, Dict[str, Any]] = {}
    if summary_completed:
        failed_before_completion = {
            name: returncode
            for name, returncode in observed_returncodes.items()
            if returncode not in (None, 0)
        }
        if failed_before_completion:
            emit_technical_failure(
                "child_process_nonzero_before_workload_completion",
                observed_returncodes=observed_returncodes,
            )
            return next(iter(failed_before_completion.values())) or 1
        for name, process in processes:
            if process.poll() is None:
                cleanup_results[name] = terminate_process_group(
                    process,
                    policy["term_wait_seconds"],
                    policy["kill_wait_seconds"],
                )
        unreaped = {
            name: result
            for name, result in cleanup_results.items()
            if not result.get("reaped", True)
        }
        if unreaped:
            emit_technical_failure(
                "completed_workload_child_unreaped",
                observed_returncodes=observed_returncodes,
                cleanup=cleanup_results,
            )
            return 124
    if timed_out:
        for name, process in processes:
            if process.poll() is None:
                cleanup_results[name] = terminate_process_group(
                    process,
                    policy["term_wait_seconds"],
                    policy["kill_wait_seconds"],
                )
        if cleanup_finished_completed_workload(cleanup_results, workload_completed):
            timed_out = False
            summary_completed = True
        else:
            emit_technical_failure(
                "workload_deadline_exceeded",
                duration_seconds=duration_s,
                deadline_seconds=policy["deadline_seconds"],
                outer_timeout_seconds=policy["outer_timeout_seconds"],
                observed_returncodes=observed_returncodes,
                cleanup=cleanup_results,
            )
            return 124

    native_returncode = native_proc.wait(timeout=0)
    sensitive_returncode = sensitive_proc.wait(timeout=0) if sensitive_proc is not None else 0
    for line in tail_lines(native_log):
        print(line, flush=True)
    if sensitive_clients:
        print("[tpcc-sensitive] pgbench tail", flush=True)
        for line in tail_lines(sensitive_log):
            print(line, flush=True)
    lifecycle_cleanup_names = set(cleanup_results) if summary_completed else set()
    native_failed = native_returncode != 0 and "native" not in lifecycle_cleanup_names
    sensitive_failed = sensitive_returncode != 0 and "sensitive" not in lifecycle_cleanup_names
    if native_failed or sensitive_failed:
        emit_technical_failure(
            "child_process_nonzero",
            native_returncode=native_returncode,
            sensitive_returncode=sensitive_returncode,
        )
        return native_returncode or sensitive_returncode

    summary_path = completed_summary or latest_summary(runtime_root / "results", started)
    if summary_path is None:
        raise RuntimeError("BenchBase produced no fresh TPC-C summary")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    native_tps = float(summary.get("Throughput (requests/second)") or 0.0)
    latency = summary.get("Latency Distribution") or {}
    sensitive_text = (
        sensitive_log.read_text(encoding="utf-8", errors="replace")
        if sensitive_clients
        else ""
    )
    sensitive_tps, sensitive_latency_ms, sensitive_transactions = parse_pgbench_metrics(sensitive_text)
    combined_tps = native_tps + sensitive_tps
    measured_fraction = sensitive_tps / combined_tps if combined_tps > 0.0 else 0.0
    client_fraction = sensitive_clients / (native_terminals + sensitive_clients)
    metrics = {
        "native_tps": native_tps,
        "sensitive_tps": sensitive_tps,
        "combined_tps": combined_tps,
        "sensitive_latency_ms": sensitive_latency_ms,
        "sensitive_transactions": sensitive_transactions,
        "sensitive_request_fraction": measured_fraction,
        "sensitive_client_fraction": client_fraction,
        "native_terminals": native_terminals,
        "sensitive_clients": sensitive_clients,
        "duration_s": duration_s,
        "benchbase_summary": str(summary_path),
        "elapsed_s": time.time() - started,
    }
    print("TPCC_SENSITIVE_METRICS_JSON=" + json.dumps(metrics, sort_keys=True), flush=True)
    print(f"XP-QPS is {combined_tps:.12g}", flush=True)
    print(f"XP-TPS is {combined_tps:.12g}", flush=True)

    def latency_ms(name: str) -> float:
        return float(latency.get(name) or 0.0) / 1000.0

    p90_ms = max(latency_ms("90th Percentile Latency (microseconds)"), sensitive_latency_ms)
    p95_ms = max(latency_ms("95th Percentile Latency (microseconds)"), sensitive_latency_ms)
    p99_ms = max(latency_ms("99th Percentile Latency (microseconds)"), sensitive_latency_ms)
    print(
        "Analytical Transaction generic : "
        f"max rt : {max(p90_ms, p95_ms, p99_ms):.6f} | min rt : 0.000000 | "
        f"avg rt : {p90_ms:.6f} | 90% rt : {p90_ms:.6f} | "
        f"95% rt : {p95_ms:.6f} | 99% rt : {p99_ms:.6f}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

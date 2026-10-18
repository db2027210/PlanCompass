#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


CONTAINERS = [
    "binary_plan_tuning_hybenchxp_pg17",
    "binary_plan_tuning_hybenchxp_cpu_mem_w1",
    "binary_plan_tuning_hybenchxp_cpu_mem_w2",
    "binary_plan_tuning_hybenchxp_cpu_mem_w3",
    "binary_plan_tuning_hybenchxp_cpu_mem_w4",
    "binary_plan_tuning_hybenchxp_cpu_mem_w5",
    "binary_plan_tuning_hybenchxp_cpu_mem_w6",
    "binary_plan_tuning_hybenchxp_cpu_mem_w7",
    "binary_plan_tuning_hybenchxp_cpu_mem_w8",
    "binary_plan_tuning_hybenchxp_io_disk1",
    "binary_plan_tuning_hybenchxp_io_disk2",
]


SIZE_RE = re.compile(r"^\s*([0-9.]+)\s*([KMGTPE]?i?B|B)\s*$", re.I)


def run_cmd(args: List[str], timeout: int = 20) -> Tuple[int, str]:
    try:
        proc = subprocess.run(args, check=False, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, ""
    return proc.returncode, proc.stdout.strip()


def parse_percent(text: str) -> Optional[float]:
    try:
        return float(text.strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


def parse_size(text: str) -> Optional[float]:
    match = SIZE_RE.match(text or "")
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).lower()
    multipliers = {
        "b": 1,
        "kb": 1000,
        "mb": 1000 ** 2,
        "gb": 1000 ** 3,
        "tb": 1000 ** 4,
        "kib": 1024,
        "mib": 1024 ** 2,
        "gib": 1024 ** 3,
        "tib": 1024 ** 4,
    }
    return value * multipliers.get(unit, 1)


def parse_mem_usage(text: str) -> Tuple[Optional[float], Optional[float]]:
    if "/" not in text:
        return None, None
    used, limit = text.split("/", 1)
    return parse_size(used.strip()), parse_size(limit.strip())


def docker_stats(containers: List[str]) -> List[Dict[str, Any]]:
    cmd = ["docker", "stats", "--no-stream", "--format", "{{json .}}", *containers]
    code, out = run_cmd(cmd, timeout=45)
    rows: List[Dict[str, Any]] = []
    if code != 0:
        return [{"event": "docker_stats_failed", "code": code}]
    for line in out.splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"event": "docker_stats_parse_failed", "line": line})
    return rows


def docker_inspect(containers: List[str]) -> Tuple[Dict[str, Dict[str, Any]], Optional[Dict[str, Any]]]:
    code, out = run_cmd(["docker", "inspect", *containers], timeout=45)
    result: Dict[str, Dict[str, Any]] = {}
    if code != 0:
        return result, {"type": "docker_inspect_failed", "code": code}
    try:
        docs = json.loads(out)
    except json.JSONDecodeError:
        return result, {"type": "docker_inspect_parse_failed"}
    for doc in docs:
        name = str(doc.get("Name", "")).lstrip("/")
        host = doc.get("HostConfig") or {}
        state = doc.get("State") or {}
        cpuset = str(host.get("CpusetCpus") or "")
        cpuset_count = count_cpuset(cpuset)
        result[name] = {
            "status": state.get("Status"),
            "running": state.get("Running"),
            "pid": state.get("Pid"),
            "cpuset": cpuset,
            "cpuset_count": cpuset_count,
            "nano_cpus": host.get("NanoCpus"),
            "memory_limit": host.get("Memory"),
            "binds": host.get("Binds"),
        }
    return result, None


def count_cpuset(cpuset: str) -> int:
    total = 0
    for part in cpuset.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            total += int(hi) - int(lo) + 1
        else:
            total += 1
    return total


def scan_jsonl_tail(path: Path, offset: int) -> Tuple[int, List[Dict[str, Any]]]:
    if not path.exists():
        return offset, []
    size = path.stat().st_size
    if size < offset:
        offset = 0
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        handle.seek(offset)
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            rows.append(row)
        offset = handle.tell()
    return offset, rows


def iter_numeric_values(obj: Any, prefix: str = "") -> Iterable[Tuple[str, float]]:
    if isinstance(obj, dict):
        for key, value in obj.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            yield from iter_numeric_values(value, child)
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            yield from iter_numeric_values(value, f"{prefix}[{idx}]")
    elif isinstance(obj, (int, float)):
        yield prefix, float(obj)


def allows_negative_metric_key(key: str) -> bool:
    lowered = key.lower()
    return (
        "delta_pct" in lowered
        or lowered.endswith("_delta")
        or ".delta" in lowered
        or "improvement" in lowered
        or "change_pct" in lowered
    )


def validation_anomalies(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    anomalies: List[Dict[str, Any]] = []
    interesting_events = {
        "baseline_validation",
        "baseline_worker_validation",
        "candidate_validation",
        "validation",
    }
    for row in rows:
        event = row.get("event")
        if event not in interesting_events and "validation" not in str(event or ""):
            continue
        context = {
            "event": event,
            "target": row.get("target"),
            "round": row.get("round"),
            "candidate_index": row.get("candidate_index"),
            "label": row.get("label"),
        }
        status = str(row.get("status") or "")
        monitor_error = str(row.get("monitor_error") or "").strip()
        resource_stats = row.get("resource_stats") if isinstance(row.get("resource_stats"), dict) else {}
        worker = row.get("worker") if isinstance(row.get("worker"), dict) else {}

        if status and status != "ok":
            anomalies.append({
                "type": "validation_status_not_ok",
                "severity": "warning",
                "status": status,
                "error": row.get("error") or row.get("process_error") or monitor_error,
                **context,
            })
        if monitor_error:
            anomalies.append({
                "type": "validation_monitor_error",
                "severity": "critical",
                "monitor_error": monitor_error,
                **context,
            })

        if status == "ok":
            if not resource_stats:
                anomalies.append({
                    "type": "validation_resource_stats_missing",
                    "severity": "critical",
                    **context,
                })
            else:
                monitor_ok = resource_stats.get("resource_monitor_ok")
                if monitor_ok is not True:
                    anomalies.append({
                        "type": "validation_resource_monitor_not_ok",
                        "severity": "critical",
                        "resource_monitor_ok": monitor_ok,
                        **context,
                    })
                scope = resource_stats.get("resource_scope")
                if scope != "docker_container":
                    anomalies.append({
                        "type": "validation_resource_scope_invalid",
                        "severity": "critical",
                        "resource_scope": scope,
                        **context,
                    })

                required_samples = int(resource_stats.get("min_stable_samples") or 0)
                stable_samples = resource_stats.get("stable_sample_count")
                if not isinstance(stable_samples, dict) or not stable_samples:
                    stable_samples = row.get("resource_samples")
                stable_samples = stable_samples if isinstance(stable_samples, dict) else {}
                sample_values = [
                    int(value)
                    for value in stable_samples.values()
                    if isinstance(value, (int, float))
                ]
                minimum_samples = min(sample_values) if sample_values else 0
                if required_samples <= 0 or minimum_samples < required_samples:
                    anomalies.append({
                        "type": "validation_stable_samples_insufficient",
                        "severity": "critical",
                        "minimum_samples": minimum_samples,
                        "required_samples": required_samples,
                        "stable_sample_count": stable_samples,
                        **context,
                    })

                container_name = str(resource_stats.get("container_name") or "")
                worker_container = str(worker.get("container") or "")
                if not container_name or (worker_container and container_name != worker_container):
                    anomalies.append({
                        "type": "validation_worker_container_mismatch",
                        "severity": "critical",
                        "resource_container": container_name,
                        "worker_container": worker_container,
                        **context,
                    })

                memory_source = str(resource_stats.get("memory_source") or "")
                io_source = str(resource_stats.get("io_source") or "")
                if not memory_source.startswith("docker_cgroup"):
                    anomalies.append({
                        "type": "validation_memory_source_invalid",
                        "severity": "critical",
                        "memory_source": memory_source,
                        **context,
                    })
                if not io_source.startswith("docker_cgroup"):
                    anomalies.append({
                        "type": "validation_io_source_invalid",
                        "severity": "critical",
                        "io_source": io_source,
                        **context,
                    })

                cpuset_count = count_cpuset(str(worker.get("cpuset_cpus") or ""))
                cpu_stats = resource_stats.get("cpu") if isinstance(resource_stats.get("cpu"), dict) else {}
                cpu_avg = cpu_stats.get("avg")
                cpu_max = cpu_stats.get("max")
                cpu_outlier_count = int(resource_stats.get("cpu_outlier_count") or 0)
                if cpu_outlier_count > 0:
                    anomalies.append({
                        "type": "validation_cpu_outliers_excluded",
                        "severity": "warning",
                        "cpu_outlier_count": cpu_outlier_count,
                        "cpu_outlier_max": resource_stats.get("cpu_outlier_max"),
                        "cpuset_count": cpuset_count,
                        "cpuset": worker.get("cpuset_cpus"),
                        **context,
                    })
                if cpuset_count and any(
                    isinstance(value, (int, float)) and float(value) > cpuset_count * 1.15
                    for value in (cpu_avg, cpu_max)
                ):
                    anomalies.append({
                        "type": "validation_cpu_over_cpuset_limit",
                        "severity": "critical",
                        "cpu_avg_cores": cpu_avg,
                        "cpu_max_cores": cpu_max,
                        "cpuset_count": cpuset_count,
                        "cpuset": worker.get("cpuset_cpus"),
                        **context,
                    })

                resources = row.get("resources") if isinstance(row.get("resources"), dict) else {}
                if not isinstance(resources.get("cpu"), (int, float)) or float(resources.get("cpu") or 0) <= 0:
                    anomalies.append({
                        "type": "validation_cpu_nonpositive",
                        "severity": "critical",
                        "value": resources.get("cpu"),
                        **context,
                    })
                if not isinstance(resources.get("memory"), (int, float)) or float(resources.get("memory") or 0) <= 0:
                    anomalies.append({
                        "type": "validation_memory_nonpositive",
                        "severity": "critical",
                        "value": resources.get("memory"),
                        **context,
                    })
                if not isinstance(resources.get("io"), (int, float)) or float(resources.get("io") or 0) < 0:
                    anomalies.append({
                        "type": "validation_io_invalid",
                        "severity": "critical",
                        "value": resources.get("io"),
                        **context,
                    })
                db_qps = row.get("db_qps")
                if not isinstance(db_qps, (int, float)) or float(db_qps) <= 0:
                    anomalies.append({
                        "type": "validation_db_qps_nonpositive",
                        "severity": "critical",
                        "value": db_qps,
                        **context,
                    })

        for key, value in iter_numeric_values(row):
            if math.isnan(value) or math.isinf(value):
                anomalies.append({
                    "type": "jsonl_non_finite",
                    "severity": "critical",
                    "key": key,
                    "value": value,
                    **context,
                })
            elif (
                value < 0
                and not allows_negative_metric_key(key)
                and any(token in key for token in ("cpu", "memory", "io", "qps", "tps", "latency", "p95", "p99"))
            ):
                anomalies.append({
                    "type": "jsonl_negative_metric",
                    "severity": "critical",
                    "key": key,
                    "value": value,
                    **context,
                })
    return anomalies


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--interval-sec", type=float, default=60.0)
    parser.add_argument("--output", default="")
    parser.add_argument("--max-iterations", type=int, default=0)
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    output = Path(args.output) if args.output else run_dir / "resource_monitor_sanity_hybenchxp.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    offsets: Dict[Path, int] = {}
    cpu_over_count: Dict[str, int] = {}
    jsonl_files = [
        run_dir / "cpu_binary_search_hybenchxp.jsonl",
        run_dir / "memory_binary_search_hybenchxp.jsonl",
        run_dir / "io_binary_search_hybenchxp.jsonl",
        run_dir / "none_binary_search_hybenchxp.jsonl",
    ]

    iteration = 0
    docker_stats_failed_count = 0
    docker_inspect_failed_count = 0
    with output.open("a", encoding="utf-8") as handle:
        while True:
            iteration += 1
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            inspect, inspect_error = docker_inspect(CONTAINERS)
            stats = docker_stats(CONTAINERS)
            docker_stats_failed = any(row.get("event") == "docker_stats_failed" for row in stats)
            if docker_stats_failed:
                docker_stats_failed_count += 1
            else:
                docker_stats_failed_count = 0
            if inspect_error:
                docker_inspect_failed_count += 1
            else:
                docker_inspect_failed_count = 0
            anomalies: List[Dict[str, Any]] = []

            if inspect_error:
                anomalies.append({
                    **inspect_error,
                    "consecutive": docker_inspect_failed_count,
                    "severity": "warning" if docker_inspect_failed_count < 3 else "critical",
                    "note": "Container state is unknown for this watcher sample; do not classify all containers as missing.",
                })
            else:
                for name in CONTAINERS:
                    meta = inspect.get(name)
                    if not meta:
                        anomalies.append({"type": "container_missing", "container": name, "severity": "critical"})
                        continue
                    if not meta.get("running"):
                        anomalies.append({
                            "type": "container_not_running",
                            "container": name,
                            "status": meta.get("status"),
                            "severity": "critical",
                        })

            sample_rows: List[Dict[str, Any]] = []
            for row in stats:
                name = row.get("Name") or row.get("Container")
                if row.get("event"):
                    event_row = dict(row)
                    event_row["type"] = event_row.pop("event")
                    event_row["severity"] = "warning"
                    if event_row["type"] == "docker_stats_failed":
                        event_row["consecutive"] = docker_stats_failed_count
                        event_row["fallback"] = "docker_inspect_container_health"
                        event_row["note"] = (
                            "docker stats timed out; candidate-level JSONL resource_stats use "
                            "cgroup deltas and remain authoritative for validation resources."
                        )
                    anomalies.append(event_row)
                    continue
                if not name:
                    anomalies.append({"type": "docker_stats_missing_container", "severity": "warning", "row": row})
                    continue
                cpu_pct = parse_percent(str(row.get("CPUPerc", "")))
                mem_used, mem_limit = parse_mem_usage(str(row.get("MemUsage", "")))
                meta = inspect.get(str(name), {})
                cpuset_count = int(meta.get("cpuset_count") or 0)
                cpu_limit_pct = cpuset_count * 100.0 if cpuset_count else None
                sample = {
                    "container": name,
                    "cpu_percent": cpu_pct,
                    "cpu_limit_percent": cpu_limit_pct,
                    "memory_metric": "docker_stats_mem_usage_working_set_approx",
                    "memory_metric_source": "docker stats MemUsage",
                    "memory_used_bytes": mem_used,
                    "memory_limit_bytes": mem_limit,
                    "block_io": row.get("BlockIO"),
                    "net_io": row.get("NetIO"),
                    "pids": row.get("PIDs"),
                    "cpuset": meta.get("cpuset"),
                }
                sample_rows.append(sample)
                if cpu_pct is None:
                    anomalies.append({"type": "docker_stats_cpu_unparseable", "container": name, "severity": "warning"})
                elif cpu_limit_pct and cpu_pct > cpu_limit_pct * 1.15:
                    cpu_over_count[str(name)] = cpu_over_count.get(str(name), 0) + 1
                    anomalies.append({
                        "type": "docker_stats_cpu_over_cpuset_limit",
                        "container": name,
                        "cpu_percent": cpu_pct,
                        "cpu_limit_percent": cpu_limit_pct,
                        "consecutive": cpu_over_count[str(name)],
                        "severity": "warning" if cpu_over_count[str(name)] < 3 else "critical",
                        "note": "Verify cgroup quota/cpuset before changing experiment; docker stats can overshoot briefly.",
                    })
                else:
                    cpu_over_count[str(name)] = 0
                if mem_used is not None and mem_limit is not None and mem_limit > 0 and mem_used > mem_limit * 0.95:
                    anomalies.append({
                        "type": "memory_near_limit",
                        "container": name,
                        "memory_used_bytes": mem_used,
                        "memory_limit_bytes": mem_limit,
                        "severity": "critical",
                    })
            if docker_stats_failed:
                for name, meta in sorted(inspect.items()):
                    sample_rows.append({
                        "container": name,
                        "sample_mode": "docker_inspect_fallback",
                        "status": meta.get("status"),
                        "running": meta.get("running"),
                        "pid": meta.get("pid"),
                        "cpuset": meta.get("cpuset"),
                        "cpuset_count": meta.get("cpuset_count"),
                        "memory_limit_bytes": meta.get("memory_limit"),
                    })

            jsonl_anomalies: List[Dict[str, Any]] = []
            for path in jsonl_files:
                offset, rows = scan_jsonl_tail(path, offsets.get(path, 0))
                offsets[path] = offset
                for item in validation_anomalies(rows):
                    item["file"] = str(path)
                    jsonl_anomalies.append(item)
            anomalies.extend(jsonl_anomalies)

            event = {
                "event": "resource_monitor_sanity_sample",
                "timestamp": ts,
                "workload": "hybenchxp",
                "iteration": iteration,
                "run_dir": str(run_dir),
                "samples": sample_rows,
                "anomaly_count": len(anomalies),
                "anomalies": anomalies,
            }
            handle.write(json.dumps(event, sort_keys=True) + "\n")
            handle.flush()
            print(json.dumps({
                "timestamp": ts,
                "iteration": iteration,
                "anomaly_count": len(anomalies),
                "critical_count": sum(1 for a in anomalies if a.get("severity") == "critical"),
            }), flush=True)
            if args.max_iterations and iteration >= args.max_iterations:
                break
            time.sleep(args.interval_sec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

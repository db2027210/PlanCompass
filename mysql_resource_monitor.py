"""Run a benchmark and emit mysqld CPU, RSS memory, and physical IO metrics."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple


Snapshot = Tuple[float, int, int, int]


def _stability_summary(
    samples: List[Dict[str, float]], *, interval_sec: float, window_sec: float = 60.0,
    cpu_capacity: Optional[float] = None,
) -> Dict[str, Any]:
    """Judge whether the last three one-minute resource means form a plateau.

    The benchmark warmup is performed by the caller, so these samples are all
    from the measured phase.  Relative checks are paired with small absolute
    floors so an idle resource (notably IO) is not marked unstable because of
    division by a near-zero mean.
    """
    nominal_per_window = max(1, int(round(window_sec / max(interval_sec, 0.05))))
    # A duration-controlled child commonly exits just before the monitor takes
    # its final tick.  Allow one missed tick per minute while still requiring
    # essentially the full three-minute measured phase.
    required = max(3, 3 * nominal_per_window - 3)
    if len(samples) < required:
        return {
            "stable": False,
            "reason": "insufficient_samples",
            "sample_count": len(samples),
            "required_samples": required,
            "window_sec": window_sec,
        }
    available = min(len(samples), 3 * nominal_per_window)
    samples_per_window = max(1, available // 3)
    recent = samples[-3 * samples_per_window:]
    thresholds = {"cpu": 0.10, "memory": 0.02, "io": 0.25}
    # A fixed 0.05-core tolerance is not scale invariant.  Treat a CPU drift
    # within five percentage points of the enforced cgroup quota as a small
    # absolute change (while retaining 0.05 core as the minimum tolerance).
    cpu_absolute_floor = max(0.05, 0.05 * cpu_capacity) if cpu_capacity else 0.05
    absolute_floors = {"cpu": cpu_absolute_floor, "memory": 0.05, "io": 0.05}
    metrics: Dict[str, Any] = {}
    all_stable = True
    for name in ("cpu", "memory", "io"):
        means = [
            sum(item[name] for item in recent[offset : offset + samples_per_window])
            / samples_per_window
            for offset in range(0, required, samples_per_window)
        ]
        mean = sum(means) / len(means)
        variance = sum((value - mean) ** 2 for value in means) / len(means)
        cv = variance ** 0.5 / max(abs(mean), 1e-9)
        relative_drift = abs(means[-1] - means[0]) / max(abs(mean), 1e-9)
        absolute_span = max(means) - min(means)
        stable = (
            cv <= thresholds[name]
            and relative_drift <= 2.0 * thresholds[name]
        ) or absolute_span <= absolute_floors[name]
        metrics[name] = {
            "window_means": means,
            "cv": cv,
            "relative_drift": relative_drift,
            "absolute_span": absolute_span,
            "absolute_tolerance": absolute_floors[name],
            "stable": stable,
        }
        all_stable = all_stable and stable
    return {
        "stable": all_stable,
        "reason": "plateau" if all_stable else "resource_variation",
        "sample_count": len(samples),
        "required_samples": required,
        "window_sec": samples_per_window * interval_sec,
        "metrics": metrics,
    }


def _working_set_bytes(usage_bytes: int, memory_stat: str) -> int:
    """Match Docker's cgroup-v1 working-set accounting.

    Inactive file cache is reclaimable under memory pressure and otherwise
    makes read-heavy database containers appear permanently full.
    """
    inactive_file = 0
    for line in memory_stat.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "total_inactive_file":
            inactive_file = int(parts[1])
            break
    return max(0, usage_bytes - inactive_file)


def _cgroup_v1_cpu_limit(pid: int) -> Optional[Tuple[str, float]]:
    """Return the unique CPU cgroup path and enforced core quota."""
    try:
        relative = None
        for line in (Path("/proc") / str(pid) / "cgroup").read_text(encoding="utf-8").splitlines():
            _, names, path = line.split(":", 2)
            if "cpu" in names.split(","):
                relative = path
                break
        if not relative:
            return None
        root = Path("/sys/fs/cgroup/cpu,cpuacct") / relative.lstrip("/")
        quota = int((root / "cpu.cfs_quota_us").read_text().strip())
        period = int((root / "cpu.cfs_period_us").read_text().strip())
        if quota <= 0 or period <= 0:
            return None
        return relative, quota / period
    except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError, IndexError):
        return None


def _read_cgroup_v1(pid: int) -> Optional[Snapshot]:
    """Read container totals when /proc/<pid>/io is permission restricted."""
    try:
        controllers: Dict[str, str] = {}
        for line in (Path("/proc") / str(pid) / "cgroup").read_text(encoding="utf-8").splitlines():
            _, names, relative = line.split(":", 2)
            for name in names.split(","):
                controllers[name] = relative
        cpu_rel = controllers.get("cpuacct")
        memory_rel = controllers.get("memory")
        blkio_rel = controllers.get("blkio")
        if not cpu_rel or not memory_rel or not blkio_rel:
            return None
        cpu_ns = int((Path("/sys/fs/cgroup/cpu,cpuacct") / cpu_rel.lstrip("/") / "cpuacct.usage").read_text().strip())
        memory_root = Path("/sys/fs/cgroup/memory") / memory_rel.lstrip("/")
        memory_usage_bytes = int((memory_root / "memory.usage_in_bytes").read_text().strip())
        memory_bytes = _working_set_bytes(
            memory_usage_bytes, (memory_root / "memory.stat").read_text()
        )
        blkio_path = Path("/sys/fs/cgroup/blkio") / blkio_rel.lstrip("/") / "blkio.throttle.io_service_bytes"
        io_bytes = 0
        for line in blkio_path.read_text().splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[1] in {"Read", "Write"}:
                io_bytes += int(parts[2])
        clock_ticks = float(os.sysconf(os.sysconf_names["SC_CLK_TCK"]))
        return cpu_ns / 1e9 * clock_ticks, memory_bytes // 1024, io_bytes, 0
    except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError, IndexError):
        return None


def discover_pids(process_names: Iterable[str]) -> List[int]:
    expected = {name.strip() for name in process_names if name.strip()}
    found: List[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            name = (entry / "comm").read_text(encoding="utf-8").strip()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if name in expected:
            found.append(int(entry.name))
    return sorted(found)


def _read_pid(pid: int) -> Optional[Snapshot]:
    # Prefer container cgroup totals whenever available.  They are the
    # authoritative counters constrained by Docker's CPU/memory/blkio limits.
    # Memory uses the container working set, excluding reclaimable inactive
    # file cache. /proc/<pid> CPU can be misleading around namespace/cgroup
    # scheduling boundaries.
    cgroup = _read_cgroup_v1(pid)
    if cgroup is not None:
        return cgroup
    proc_dir = Path("/proc") / str(pid)
    try:
        stat = (proc_dir / "stat").read_text(encoding="utf-8")
        tail = stat[stat.rfind(")") + 2 :].split()
        cpu_ticks = float(tail[11]) + float(tail[12])
        rss_kb = 0
        for line in (proc_dir / "status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                rss_kb = int(line.split()[1])
                break
        read_bytes = 0
        write_bytes = 0
        try:
            io_lines = (proc_dir / "io").read_text(encoding="utf-8").splitlines()
        except PermissionError:
            io_lines = []
        for line in io_lines:
            if line.startswith("read_bytes:"):
                read_bytes = int(line.split()[1])
            elif line.startswith("write_bytes:"):
                write_bytes = int(line.split()[1])
        return cpu_ticks, rss_kb, read_bytes, write_bytes
    except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError, IndexError):
        return None


def _aggregate(pids: Iterable[int]) -> Snapshot:
    total = [0.0, 0, 0, 0]
    for pid in pids:
        item = _read_pid(pid)
        if item is None:
            continue
        for index, value in enumerate(item):
            total[index] += value
    return float(total[0]), int(total[1]), int(total[2]), int(total[3])


def run_and_monitor(
    command: List[str],
    *,
    pids: Optional[List[int]] = None,
    process_names: Optional[List[str]] = None,
    interval_sec: float = 1.0,
    warmup_samples: int = 1,
    cwd: Optional[str] = None,
) -> Dict[str, Any]:
    if not command:
        raise ValueError("benchmark command is empty")
    selected_pids = list(pids or discover_pids(process_names or ["mysqld"]))
    if not selected_pids:
        raise RuntimeError("no mysqld process found; pass --pid for container/namespace setups")
    cpu_limits = [_cgroup_v1_cpu_limit(pid) for pid in selected_pids]
    cpu_capacity = None
    if all(item is not None for item in cpu_limits):
        cpu_capacity = sum({item[0]: item[1] for item in cpu_limits if item is not None}.values())
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as output_file, tempfile.TemporaryFile(
        mode="w+t", encoding="utf-8"
    ) as error_file:
        proc = subprocess.Popen(command, cwd=cwd, stdout=output_file, stderr=error_file, text=True)
        clock_ticks = float(os.sysconf(os.sysconf_names["SC_CLK_TCK"]))
        previous_time = time.perf_counter()
        previous = _aggregate(selected_pids)
        samples: List[Dict[str, float]] = []

        def collect_sample() -> None:
            nonlocal previous_time, previous
            now = time.perf_counter()
            current = _aggregate(selected_pids)
            elapsed = max(now - previous_time, 1e-9)
            cpu = max(0.0, current[0] - previous[0]) / clock_ticks / elapsed
            if cpu_capacity is not None:
                # CFS quota is a hard upper bound.  Bounding occasional
                # discontinuities in cumulative counters prevents impossible
                # >quota utilization from contaminating an experiment log.
                cpu = min(cpu, cpu_capacity)
            samples.append(
                {
                    "cpu": cpu,
                    "memory": max(0.0, current[1]) / 1024.0 / 1024.0,
                    "io": max(0.0, current[2] + current[3] - previous[2] - previous[3])
                    / 1024.0
                    / 1024.0
                    / elapsed,
                }
            )
            previous_time = now
            previous = current

        while proc.poll() is None:
            time.sleep(max(0.05, interval_sec))
            collect_sample()
        if not samples:
            collect_sample()
        proc.wait()
        output_file.seek(0)
        error_file.seek(0)
        stdout = output_file.read()
        stderr = error_file.read()
    if stdout:
        print(stdout, end="" if stdout.endswith("\n") else "\n", flush=True)
    if proc.returncode != 0:
        raise RuntimeError(f"benchmark failed (exit={proc.returncode}): {stderr[-4000:].strip()}")
    usable = samples[max(0, int(warmup_samples)) :] or samples
    if not usable:
        raise RuntimeError("benchmark ended before a resource sample was collected")
    result = {
        name: sum(sample[name] for sample in usable) / len(usable)
        for name in ("cpu", "memory", "io")
    }
    result["stability"] = _stability_summary(
        usable, interval_sec=interval_sec, window_sec=60.0,
        cpu_capacity=cpu_capacity,
    )
    result["measurement_elapsed_sec"] = len(usable) * interval_sec
    # Preserve workload-level metrics emitted as the last standalone JSON line.
    # This keeps resource sampling and benchmark QPS/latency in one authoritative
    # validation record without asking the workload to know about /proc.
    for line in reversed(stdout.splitlines()):
        try:
            workload = json.loads(line.strip())
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(workload, dict):
            for key in (
                "qps", "latency_p95_ms", "latency_p99_ms", "explain_samples",
                "query_count", "query_time", "failures", "completed_by_query",
            ):
                if key in workload:
                    result[key] = workload[key]
            break
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, action="append", default=[])
    parser.add_argument("--process-name", action="append", default=[])
    parser.add_argument("--interval-sec", type=float, default=1.0)
    parser.add_argument("--warmup-samples", type=int, default=1)
    parser.add_argument("--cwd")
    parser.add_argument("--output-json", type=Path,
                        help="optional persisted copy of the authoritative combined metrics")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command and args.command[0] == "--" else args.command
    resource = run_and_monitor(
        command,
        pids=args.pid or None,
        process_names=args.process_name or ["mysqld"],
        interval_sec=args.interval_sec,
        warmup_samples=args.warmup_samples,
        cwd=args.cwd,
    )
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output_json.with_suffix(args.output_json.suffix + ".tmp")
        temporary.write_text(json.dumps(resource, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, args.output_json)
    print("RESOURCE_METRICS=" + json.dumps(resource, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

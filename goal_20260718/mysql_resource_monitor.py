"""Run a benchmark and emit mysqld CPU, RSS memory, and physical IO metrics."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Dict, Iterable, List, Optional, Tuple


Snapshot = Tuple[float, int, int, int]


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
        memory_bytes = int((Path("/sys/fs/cgroup/memory") / memory_rel.lstrip("/") / "memory.usage_in_bytes").read_text().strip())
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
            cgroup = _read_cgroup_v1(pid)
            if cgroup is not None:
                return cgroup
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
) -> Dict[str, float]:
    if not command:
        raise ValueError("benchmark command is empty")
    selected_pids = list(pids or discover_pids(process_names or ["mysqld"]))
    if not selected_pids:
        raise RuntimeError("no mysqld process found; pass --pid for container/namespace setups")
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
            samples.append(
                {
                    "cpu": max(0.0, current[0] - previous[0]) / clock_ticks / elapsed,
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
    print("RESOURCE_METRICS=" + json.dumps(resource, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

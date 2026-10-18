#!/usr/bin/env python3
"""Persist cgroup-v1 memory pressure evidence for the two Memory workloads."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from time import strftime

ROOT = Path(__file__).resolve().parents[1]
CONTAINERS = {
    "hybench_ap": "ra_mysql_goal_hybench_tp",
    "job": "ra-mysql-grid-job-w2",
}


def scalar(path: Path) -> int:
    return int(path.read_text(encoding="utf-8").strip())


def memory_stat(path: Path) -> dict[str, int]:
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, value = line.split()
        result[key] = int(value)
    return result


def main() -> int:
    rows = []
    for benchmark, container in CONTAINERS.items():
        pid = int(subprocess.check_output(
            ["docker", "inspect", "-f", "{{.State.Pid}}", container], text=True
        ).strip())
        relative = None
        for line in (Path("/proc") / str(pid) / "cgroup").read_text().splitlines():
            _, controllers, value = line.split(":", 2)
            if "memory" in controllers.split(","):
                relative = value
                break
        if relative is None:
            raise RuntimeError(f"no memory cgroup for {container}")
        cgroup = Path("/sys/fs/cgroup/memory") / relative.lstrip("/")
        usage = scalar(cgroup / "memory.usage_in_bytes")
        limit = scalar(cgroup / "memory.limit_in_bytes")
        stats = memory_stat(cgroup / "memory.stat")
        rows.append({
            "benchmark": benchmark,
            "container": container,
            "pid": pid,
            "usage_bytes": usage,
            "limit_bytes": limit,
            "usage_ratio": usage / limit,
            "max_usage_bytes": scalar(cgroup / "memory.max_usage_in_bytes"),
            "failcnt": scalar(cgroup / "memory.failcnt"),
            "rss_bytes": stats.get("rss"),
            "cache_bytes": stats.get("cache"),
            "total_pgfault": stats.get("total_pgfault"),
            "total_pgmajfault": stats.get("total_pgmajfault"),
        })
    payload = {"captured_at": strftime("%Y-%m-%d %H:%M:%S"), "benchmarks": rows}
    output = ROOT / "preflight/wide_20260721/memory_pressure_evidence.json"
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    os.replace(temporary, output)
    print(json.dumps({"output": str(output), "benchmarks": len(rows)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

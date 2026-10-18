#!/usr/bin/env python3
"""Collect fresh one-second cgroup-v1 evidence for one formal JOB point."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Dict, Mapping, Optional, Tuple


class SamplerError(RuntimeError):
    """The formal cgroup profile or sample stream is not admissible."""


def _read_int(path: Path) -> int:
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise SamplerError(f"cannot read integer cgroup counter: {path}") from exc


def _docker_inspect(container: str) -> Dict[str, Any]:
    result = subprocess.run(
        ["docker", "inspect", container],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SamplerError(f"docker inspect failed for {container}: {result.stderr.strip()}")
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SamplerError("docker inspect returned invalid JSON") from exc
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise SamplerError("docker inspect returned an ambiguous container")
    return rows[0]


def _device_rates(rows: Any) -> Dict[str, int]:
    result: Dict[str, int] = {}
    if not isinstance(rows, list):
        return result
    for row in rows:
        if isinstance(row, dict):
            path = str(row.get("Path") or "")
            try:
                result[path] = int(row.get("Rate") or 0)
            except (TypeError, ValueError):
                result[path] = 0
    return result


def verify_profile(profile: Mapping[str, Any]) -> int:
    container = str(profile.get("container_name") or "")
    if not container:
        raise SamplerError("container_name is missing")
    inspected = _docker_inspect(container)
    state = inspected.get("State") or {}
    host = inspected.get("HostConfig") or {}
    if state.get("Running") is not True:
        raise SamplerError("bound PostgreSQL container is not running")
    expected_cpu = int(round(float(profile["cpu"]) * 1_000_000_000))
    checks = (
        (int(host.get("NanoCpus") or 0) == expected_cpu, "NanoCpus"),
        (str(host.get("CpusetCpus") or "") == str(profile["cpuset_cpus"]), "CpusetCpus"),
        (int(host.get("Memory") or 0) == int(profile["memory_limit_bytes"]), "Memory"),
        (int(host.get("MemorySwap") or 0) == int(profile["memory_swap_bytes"]), "MemorySwap"),
    )
    for passed, label in checks:
        if not passed:
            raise SamplerError(f"Docker profile readback mismatch: {label}")
    expected_path = str(profile["io_device_source"])
    expected_read = int(round(float(profile["io_read_cap_mib_s"]) * 1024 * 1024))
    expected_write = int(round(float(profile["io_write_cap_mib_s"]) * 1024 * 1024))
    if _device_rates(host.get("BlkioDeviceReadBps")) != {expected_path: expected_read}:
        raise SamplerError("Docker read BPS cap readback mismatch")
    if _device_rates(host.get("BlkioDeviceWriteBps")) != {expected_path: expected_write}:
        raise SamplerError("Docker write BPS cap readback mismatch")
    pid = int(state.get("Pid") or 0)
    if pid <= 0:
        raise SamplerError("container PID readback is invalid")
    return pid


def _controller_paths(pid: int) -> Dict[str, Path]:
    try:
        lines = Path(f"/proc/{pid}/cgroup").read_text(encoding="ascii").splitlines()
    except OSError as exc:
        raise SamplerError(f"cannot read container cgroup membership for PID {pid}") from exc
    memberships: Dict[str, str] = {}
    for line in lines:
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        if parts[0] == "0" and not parts[1]:
            raise SamplerError("formal sampler requires cgroup v1, found unified cgroup v2")
        for controller in parts[1].split(","):
            memberships[controller] = parts[2].lstrip("/")
    roots = {
        "cpuacct": (Path("/sys/fs/cgroup/cpu,cpuacct"), Path("/sys/fs/cgroup/cpuacct")),
        "memory": (Path("/sys/fs/cgroup/memory"),),
        "blkio": (Path("/sys/fs/cgroup/blkio"),),
    }
    resolved: Dict[str, Path] = {}
    for controller, alternatives in roots.items():
        relative = memberships.get(controller)
        if relative is None:
            raise SamplerError(f"missing cgroup-v1 controller membership: {controller}")
        candidates = [root / relative for root in alternatives]
        match = next((path for path in candidates if path.is_dir()), None)
        if match is None:
            raise SamplerError(f"cannot resolve cgroup-v1 path: {controller}")
        resolved[controller] = match
    return resolved


def _io_totals(path: Path, device: str) -> Tuple[int, int]:
    candidates = (
        path / "blkio.throttle.io_service_bytes_recursive",
        path / "blkio.throttle.io_service_bytes",
    )
    source = next((item for item in candidates if item.is_file()), None)
    if source is None:
        raise SamplerError("cgroup-v1 blkio byte counter is missing")
    read_bytes = 0
    write_bytes = 0
    try:
        lines = source.read_text(encoding="ascii").splitlines()
    except OSError as exc:
        raise SamplerError(f"cannot read cgroup blkio counter: {source}") from exc
    for line in lines:
        fields = line.split()
        if len(fields) != 3 or fields[0] != device:
            continue
        if fields[1].lower() == "read":
            read_bytes += int(fields[2])
        elif fields[1].lower() == "write":
            write_bytes += int(fields[2])
    return read_bytes, write_bytes


class CgroupV1Sampler:
    """A candidate-scoped sampler that owns a fresh JSONL output file."""

    def __init__(self, profile: Mapping[str, Any], interval_seconds: float = 1.0) -> None:
        if abs(float(interval_seconds) - 1.0) > 1e-12:
            raise SamplerError("formal cgroup sample interval must be exactly one second")
        self.profile = dict(profile)
        self.interval_seconds = float(interval_seconds)
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._error: Optional[BaseException] = None

    def start(self, output_path: Path) -> None:
        output_path = output_path.resolve()
        if output_path.exists():
            raise SamplerError(f"refusing to reuse resource evidence: {output_path}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pid = verify_profile(self.profile)
        paths = _controller_paths(pid)
        device = str(self.profile["observed_device"])
        cpu_capacity = float(self.profile["cpu"])
        memory_limit = int(self.profile["memory_limit_bytes"])
        read_cap = float(self.profile["io_read_cap_mib_s"]) * 1024 * 1024
        write_cap = float(self.profile["io_write_cap_mib_s"]) * 1024 * 1024
        output_path.touch(exist_ok=False)

        def collect() -> None:
            try:
                previous_time = time.monotonic()
                previous_cpu = _read_int(paths["cpuacct"] / "cpuacct.usage")
                previous_read, previous_write = _io_totals(paths["blkio"], device)
                self._ready.set()
                while not self._stop.wait(self.interval_seconds):
                    now = time.monotonic()
                    epoch = time.time()
                    cpu = _read_int(paths["cpuacct"] / "cpuacct.usage")
                    read_bytes, write_bytes = _io_totals(paths["blkio"], device)
                    elapsed = now - previous_time
                    usage = _read_int(paths["memory"] / "memory.usage_in_bytes")
                    stat = (paths["memory"] / "memory.stat").read_text(encoding="ascii")
                    values = dict(
                        line.split(None, 1) for line in stat.splitlines() if " " in line
                    )
                    inactive = int(values.get("total_inactive_file", "0"))
                    read_bps = max(0, read_bytes - previous_read) / elapsed
                    write_bps = max(0, write_bytes - previous_write) / elapsed
                    row = {
                        "end_epoch": epoch,
                        "sample_interval_seconds": elapsed,
                        "cpu_utilization": max(0.0, cpu - previous_cpu)
                        / (elapsed * 1_000_000_000 * cpu_capacity),
                        "memory_utilization": max(0, usage - inactive) / memory_limit,
                        "io_utilization": (
                            min(read_bps, read_cap) + min(write_bps, write_cap)
                        ) / (read_cap + write_cap),
                        "read_bps": read_bps,
                        "write_bps": write_bps,
                        "observed_device": device,
                        "memory_limit_bytes": memory_limit,
                    }
                    with output_path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(row, sort_keys=True) + "\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                    previous_time = now
                    previous_cpu = cpu
                    previous_read = read_bytes
                    previous_write = write_bytes
            except BaseException as exc:
                self._error = exc
                self._ready.set()
                self._stop.set()

        self._thread = threading.Thread(target=collect, name="formal-cgroup-v1-sampler")
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            self.stop()
            raise SamplerError("cgroup sampler did not initialize")
        if self._error is not None:
            self.stop()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(5.0, self.interval_seconds * 3))
            if self._thread.is_alive():
                raise SamplerError("cgroup sampler thread did not stop")
        if self._error is not None:
            raise SamplerError(f"cgroup sampler failed: {self._error}") from self._error

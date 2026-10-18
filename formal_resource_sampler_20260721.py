#!/usr/bin/env python3
"""Frozen cgroup-v1 resource adapter for the six-session MySQL campaign."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import time
from typing import Any, Iterable, Mapping


class ResourceError(RuntimeError):
    pass


def combined_limited_io_utilization(
    read_bps: float, write_bps: float, read_cap_bps: int, write_cap_bps: int,
) -> float:
    """Formal IO utilization: combined traffic over combined capability."""
    total_cap = float(read_cap_bps + write_cap_bps)
    if total_cap <= 0.0:
        raise ResourceError("combined IO capability must be positive")
    return (float(read_bps) + float(write_bps)) / total_cap


def _int(path: Path) -> int:
    return int(path.read_text(encoding="utf-8").strip())


def _key_values(path: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        pieces = line.split()
        if len(pieces) == 2:
            try:
                result[pieces[0]] = int(pieces[1])
            except ValueError:
                continue
    return result


def _psi(name: str) -> dict[str, Any]:
    path = Path("/proc/pressure") / name
    if not path.is_file():
        return {"available": False}
    result: dict[str, Any] = {"available": True}
    for line in path.read_text(encoding="utf-8").splitlines():
        pieces = line.split()
        result[pieces[0]] = {
            key: (int(value) if key == "total" else float(value))
            for key, value in (piece.split("=", 1) for piece in pieces[1:])
        }
    return result


def _io_bytes(path: Path, device: str) -> tuple[int, int]:
    read_bytes = 0
    write_bytes = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        pieces = line.split()
        if len(pieces) != 3 or pieces[0] != device:
            continue
        if pieces[1] == "Read":
            read_bytes += int(pieces[2])
        elif pieces[1] == "Write":
            write_bytes += int(pieces[2])
    return read_bytes, write_bytes


def _io_limits(path: Path, device: str) -> int | None:
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        pieces = line.split()
        if len(pieces) == 2 and pieces[0] == device:
            return int(pieces[1])
    return None


def _diskstats(device: str) -> dict[str, int]:
    major, minor = (int(item) for item in device.split(":", 1))
    for line in Path("/proc/diskstats").read_text(encoding="utf-8").splitlines():
        pieces = line.split()
        if len(pieces) >= 14 and int(pieces[0]) == major and int(pieces[1]) == minor:
            return {
                "reads_completed": int(pieces[3]),
                "sectors_read": int(pieces[5]),
                "read_time_ms": int(pieces[6]),
                "writes_completed": int(pieces[7]),
                "sectors_written": int(pieces[9]),
                "write_time_ms": int(pieces[10]),
                "io_in_progress": int(pieces[11]),
                "io_time_ms": int(pieces[12]),
                "weighted_io_time_ms": int(pieces[13]),
            }
    raise ResourceError(f"device {device} is absent from /proc/diskstats")


def _cpu_frequency_khz(cpuset: str) -> float | None:
    cpus: list[int] = []
    for piece in cpuset.split(","):
        if "-" in piece:
            first, last = (int(item) for item in piece.split("-", 1))
            cpus.extend(range(first, last + 1))
        elif piece:
            cpus.append(int(piece))
    values = []
    for cpu in cpus:
        path = Path(f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/scaling_cur_freq")
        if path.is_file():
            try:
                values.append(float(path.read_text(encoding="utf-8").strip()))
            except ValueError:
                pass
    return statistics.fmean(values) if values else None


def _controller_paths(pid: int) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for line in Path(f"/proc/{pid}/cgroup").read_text(encoding="utf-8").splitlines():
        _, controllers, relative = line.split(":", 2)
        if not controllers:
            continue
        controller_names = controllers.split(",")
        # The order in /proc/<pid>/cgroup is not guaranteed to match the
        # combined-controller mount name.  CentOS 7 on node01 reports
        # ``cpuacct,cpu`` while mounting it as ``cpu,cpuacct``.  Resolve the
        # directory that actually contains this container instead of assuming
        # the textual order is identical.
        mount_names = [
            controllers,
            ",".join(reversed(controller_names)),
            *controller_names,
        ]
        roots = [
            Path("/sys/fs/cgroup") / mount_name / relative.lstrip("/")
            for mount_name in dict.fromkeys(mount_names)
        ]
        root = next((candidate for candidate in roots if candidate.is_dir()), roots[0])
        for controller in controller_names:
            result[controller] = root
    required = {"cpu", "cpuacct", "memory", "blkio", "cpuset", "pids"}
    missing = required - set(result)
    if missing:
        raise ResourceError(f"container PID lacks cgroup-v1 controllers: {sorted(missing)}")
    return result


@dataclass(frozen=True)
class RawSnapshot:
    monotonic_ns: int
    epoch_ms: int
    cpu_usage_ns: int
    memory_current_bytes: int
    memory_failcnt: int
    memory_stat: dict[str, int]
    read_bytes: int
    write_bytes: int
    diskstats: dict[str, int]
    memory_psi: dict[str, Any]
    io_psi: dict[str, Any]


class CgroupV1Adapter:
    def __init__(self, container: str, device: str, profile: Mapping[str, Any]):
        inspect = json.loads(subprocess.check_output(["docker", "inspect", container], text=True))[0]
        self.container = container
        self.container_id = inspect["Id"]
        self.pid = int(inspect["State"]["Pid"])
        if inspect["State"]["Status"] != "running" or self.pid <= 0:
            raise ResourceError(f"container is not running: {container}")
        self.host_config = inspect["HostConfig"]
        self.paths = _controller_paths(self.pid)
        self.device = device
        self.profile = dict(profile)
        self.readback = self._readback()

    def _readback(self) -> dict[str, Any]:
        cpu = self.paths["cpu"]
        memory = self.paths["memory"]
        blkio = self.paths["blkio"]
        cpuset = self.paths["cpuset"]
        pids = self.paths["pids"]
        quota = _int(cpu / "cpu.cfs_quota_us")
        period = _int(cpu / "cpu.cfs_period_us")
        cores = quota / period if quota > 0 else None
        values = {
            "cgroup_version": 1,
            "paths": {name: str(path) for name, path in self.paths.items()},
            "cpu_quota_us": quota,
            "cpu_period_us": period,
            "cpu_quota_cores": cores,
            "cpuset_cpus": (cpuset / "cpuset.cpus").read_text(encoding="utf-8").strip(),
            "cpuset_mems": (cpuset / "cpuset.mems").read_text(encoding="utf-8").strip(),
            "memory_limit_bytes": _int(memory / "memory.limit_in_bytes"),
            "memory_swap_limit_bytes": _int(memory / "memory.memsw.limit_in_bytes"),
            "pids_limit": (pids / "pids.max").read_text(encoding="utf-8").strip(),
            "read_cap_bps": _io_limits(blkio / "blkio.throttle.read_bps_device", self.device),
            "write_cap_bps": _io_limits(blkio / "blkio.throttle.write_bps_device", self.device),
            "device": self.device,
            "docker_host_config": {
                "NanoCpus": self.host_config["NanoCpus"],
                "CpusetCpus": self.host_config["CpusetCpus"],
                "CpusetMems": self.host_config["CpusetMems"],
                "Memory": self.host_config["Memory"],
                "MemorySwap": self.host_config["MemorySwap"],
                "PidsLimit": self.host_config["PidsLimit"],
                "NetworkMode": self.host_config["NetworkMode"],
                "BlkioDeviceReadBps": self.host_config["BlkioDeviceReadBps"],
                "BlkioDeviceWriteBps": self.host_config["BlkioDeviceWriteBps"],
            },
        }
        expected = self.profile
        mismatches = {}
        checks = {
            "cpu_quota_cores": (cores, float(expected["cpu_quota_cores"])),
            "cpuset_cpus": (values["cpuset_cpus"], expected["cpuset_cpus"]),
            "cpuset_mems": (values["cpuset_mems"], expected["cpuset_mems"]),
            "memory_limit_bytes": (values["memory_limit_bytes"], expected["memory_bytes"]),
            "memory_swap_limit_bytes": (values["memory_swap_limit_bytes"], expected["memory_swap_bytes"]),
            "pids_limit": (int(values["pids_limit"]), expected["pids_limit"]),
            "read_cap_bps": (values["read_cap_bps"], expected["io_read_bps"]),
            "write_cap_bps": (values["write_cap_bps"], expected["io_write_bps"]),
        }
        for key, (actual, requested) in checks.items():
            if actual != requested:
                mismatches[key] = {"actual": actual, "requested": requested}
        if mismatches:
            raise ResourceError(f"requested/effective cgroup mismatch: {mismatches}")
        values["status"] = "passed"
        return values

    def snapshot(self) -> RawSnapshot:
        memory = self.paths["memory"]
        blkio = self.paths["blkio"]
        read_bytes, write_bytes = _io_bytes(blkio / "blkio.throttle.io_service_bytes", self.device)
        return RawSnapshot(
            monotonic_ns=time.monotonic_ns(),
            epoch_ms=time.time_ns() // 1_000_000,
            cpu_usage_ns=_int(self.paths["cpuacct"] / "cpuacct.usage"),
            memory_current_bytes=_int(memory / "memory.usage_in_bytes"),
            memory_failcnt=_int(memory / "memory.failcnt"),
            memory_stat=_key_values(memory / "memory.stat"),
            read_bytes=read_bytes,
            write_bytes=write_bytes,
            diskstats=_diskstats(self.device),
            memory_psi=_psi("memory"),
            io_psi=_psi("io"),
        )

    def derive(self, previous: RawSnapshot, current: RawSnapshot, *, phase: str, elapsed_s: float) -> dict[str, Any]:
        wall = (current.monotonic_ns - previous.monotonic_ns) / 1e9
        if wall <= 0:
            raise ResourceError("non-positive sample interval")
        cores = float(self.readback["cpu_quota_cores"])
        cpu_used_seconds = (current.cpu_usage_ns - previous.cpu_usage_ns) * 1e-9
        cpu_used_cores = cpu_used_seconds / wall
        cpu_util = cpu_used_cores / cores
        memory_limit = int(self.readback["memory_limit_bytes"])
        inactive = current.memory_stat.get("total_inactive_file", current.memory_stat.get("inactive_file", 0))
        working_set = max(0, current.memory_current_bytes - inactive)
        read_bps = (current.read_bytes - previous.read_bytes) / wall
        write_bps = (current.write_bytes - previous.write_bytes) / wall
        read_cap = self.readback["read_cap_bps"]
        write_cap = self.readback["write_cap_bps"]
        disk_busy = (current.diskstats["io_time_ms"] - previous.diskstats["io_time_ms"]) / (wall * 1000.0)
        if read_cap is None or write_cap is None:
            io_util = disk_busy
            io_mode = "unlimited_host_diskstats"
        else:
            io_util = combined_limited_io_utilization(
                read_bps, write_bps, read_cap, write_cap
            )
            io_mode = "limited_combined_read_write_bytes_over_total_capability"
        return {
            "epoch_ms": current.epoch_ms,
            "elapsed_s": elapsed_s,
            "phase": phase,
            "cgroup_version": 1,
            "cpu_usage_raw_ns": current.cpu_usage_ns,
            "cpu_delta_seconds": cpu_used_seconds,
            "cpu_used_cores": cpu_used_cores,
            "cpu_quota_cores": cores,
            "cpu_utilization_ratio": cpu_util,
            "cpu_frequency_khz": _cpu_frequency_khz(str(self.readback["cpuset_cpus"])),
            "memory_current_bytes": current.memory_current_bytes,
            "memory_limit_bytes": memory_limit,
            "memory_working_set_bytes": working_set,
            "memory_current_utilization_ratio": current.memory_current_bytes / memory_limit,
            "memory_utilization_ratio": working_set / memory_limit,
            "memory_utilization_definition": "(usage_in_bytes-total_inactive_file)/limit_in_bytes",
            "memory_failcnt": current.memory_failcnt,
            "memory_pgmajfault": current.memory_stat.get("total_pgmajfault", current.memory_stat.get("pgmajfault", 0)),
            "memory_workingset_refault": current.memory_stat.get("total_workingset_refault", current.memory_stat.get("workingset_refault", 0)),
            "memory_psi": current.memory_psi,
            "io_read_raw_bytes": current.read_bytes,
            "io_write_raw_bytes": current.write_bytes,
            "io_read_bps": read_bps,
            "io_write_bps": write_bps,
            "io_read_cap_bps": read_cap,
            "io_write_cap_bps": write_cap,
            "io_total_cap_bps": (
                None if read_cap is None or write_cap is None else read_cap + write_cap
            ),
            "io_utilization_ratio": io_util,
            "io_utilization_mode": io_mode,
            "device": self.device,
            "device_io_time_ms": current.diskstats["io_time_ms"],
            "device_busy_ratio": disk_busy,
            "io_psi": current.io_psi,
        }


def append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
        )
        handle.flush()
        os.fsync(handle.fileno())


def coefficient_of_variation(values: Iterable[float]) -> float:
    numbers = list(values)
    mean = statistics.fmean(numbers)
    if len(numbers) < 2 or mean == 0:
        return 0.0
    return statistics.pstdev(numbers) / abs(mean)


def stability_summary(samples: list[Mapping[str, Any]], throughput: list[float] | None = None, *, xp: bool = False) -> dict[str, Any]:
    if len(samples) < 180:
        return {"stable": False, "reason": "fewer_than_180_samples", "sample_count": len(samples)}
    recent = samples[-180:]
    windows = [recent[index:index + 30] for index in range(0, 180, 30)]
    thresholds = {"cpu": 0.05, "memory": 0.03, "io": 0.05}
    fields = {
        "cpu": "cpu_utilization_ratio",
        "memory": "memory_utilization_ratio",
        "io": "io_utilization_ratio",
    }
    details = []
    stable = True
    for window_index, window in enumerate(windows):
        row: dict[str, Any] = {"window": window_index + 1}
        for resource, field in fields.items():
            values = [float(sample[field]) for sample in window]
            mean = statistics.fmean(values)
            if mean < 0.1:
                metric = max(values) - min(values)
                passed = metric <= 0.05
                rule = "absolute_range"
            else:
                metric = coefficient_of_variation(values)
                passed = metric <= thresholds[resource]
                rule = "cv"
            row[resource] = {"mean": mean, "rule": rule, "value": metric, "passed": passed}
            stable = stable and passed
        details.append(row)
    throughput_detail: dict[str, Any] | None = None
    if throughput is not None:
        if len(throughput) < 180:
            throughput_detail = {
                "passed": False, "reason": "insufficient_throughput_samples",
                "decision_role": "audit_only",
            }
        else:
            per_window = []
            limit = 0.1 if xp else 0.05
            for index in range(0, 180, 30):
                values = throughput[-180 + index:-180 + index + 30 if index < 150 else None]
                cv = coefficient_of_variation(values)
                per_window.append({"window": index // 30 + 1, "cv": cv, "passed": cv <= limit})
            throughput_detail = {
                "threshold": limit, "windows": per_window,
                "passed": all(item["passed"] for item in per_window),
                "decision_role": "audit_only",
            }
    return {"stable": stable, "sample_count": len(samples), "resource_windows": details, "throughput": throughput_detail}

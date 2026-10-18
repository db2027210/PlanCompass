#!/usr/bin/env python3
"""Qualify one PostgreSQL/MySQL two-lane execution without touching formal Runs."""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


QUALIFICATION_ID = "two_lane_qualification_20260721_v1"
FORMAL_ROOT = Path(
    "/home/anonymous/program/resource_adaptive_mysql/history/unified_formal/"
    "six_session_20260721_v3"
)
SHARED_ROOT = FORMAL_ROOT / "shared"
GLOBAL_LOCK = FORMAL_ROOT.parent / ".host_measurement.lock"
PG_ROOT = Path("/home/anonymous/program/resource_adaptive_double_loop_sgd_isolated")
MYSQL_ROOT = Path("/home/anonymous/program/resource_adaptive_mysql")
PG_LANE_CPUSET = "36-51,292-307"
MYSQL_LANE_CPUSET = "220-235,476-491"
PG_LANE_MEMS = "0"
MYSQL_LANE_MEMS = "1"
PG_DEVICE = "nvme1n1"
MYSQL_DEVICE = "nvme2n1"


class QualificationError(RuntimeError):
    """Raised when two-lane qualification cannot produce comparable evidence."""


def atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def parse_cpu_set(value: str) -> Set[int]:
    result: Set[int] = set()
    for piece in value.split(","):
        bounds = piece.split("-", 1)
        if len(bounds) == 1:
            result.add(int(bounds[0]))
        else:
            result.update(range(int(bounds[0]), int(bounds[1]) + 1))
    return result


def validate_lane_topology() -> Dict[str, Any]:
    pg = parse_cpu_set(PG_LANE_CPUSET)
    mysql = parse_cpu_set(MYSQL_LANE_CPUSET)
    if pg & mysql:
        raise QualificationError("qualification CPU lanes overlap")
    topology: Dict[int, Tuple[int, int, int]] = {}
    lines = subprocess.check_output(
        ["lscpu", "-e=CPU,NODE,SOCKET,CORE"], text=True
    ).splitlines()[1:]
    for line in lines:
        cpu, node, socket, core = (int(item) for item in line.split())
        topology[cpu] = (node, socket, core)
    for label, cpus, node in (("postgresql", pg, 0), ("mysql", mysql, 1)):
        if len(cpus) != 32:
            raise QualificationError(f"{label} lane must contain 32 logical CPUs")
        observed_nodes = {topology[cpu][0] for cpu in cpus}
        observed_cores = {(topology[cpu][1], topology[cpu][2]) for cpu in cpus}
        if observed_nodes != {node} or len(observed_cores) != 16:
            raise QualificationError(f"{label} lane topology mismatch")
    return {
        "postgresql": {"cpuset_cpus": PG_LANE_CPUSET, "cpuset_mems": PG_LANE_MEMS},
        "mysql": {"cpuset_cpus": MYSQL_LANE_CPUSET, "cpuset_mems": MYSQL_LANE_MEMS},
        "overlap": [],
    }


def formal_scheduler_guard() -> Dict[str, Any]:
    state_path = SHARED_ROOT / "scheduler_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if (
        state.get("active") is not None
        or int(state.get("completed_entry_count", -1)) != 0
        or int(state.get("next_ordinal", -1)) != 1
    ):
        raise QualificationError("formal scheduler is not pristine 0/84")
    return {
        "path": str(state_path),
        "next_ordinal": 1,
        "active": None,
        "completed_entry_count": 0,
    }


def disk_io_time_ms(device: str) -> int:
    for line in Path("/proc/diskstats").read_text(encoding="utf-8").splitlines():
        pieces = line.split()
        if len(pieces) >= 14 and pieces[2] == device:
            return int(pieces[12])
    raise QualificationError(f"device absent from /proc/diskstats: {device}")


def cpu_counters(cpus: Iterable[int]) -> Tuple[int, int]:
    wanted = {f"cpu{cpu}" for cpu in cpus}
    total = 0
    idle = 0
    for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines():
        pieces = line.split()
        if not pieces or pieces[0] not in wanted:
            continue
        values = [int(value) for value in pieces[1:]]
        total += sum(values)
        idle += values[3] + values[4]
    if total == 0:
        raise QualificationError("lane CPU counters are absent")
    return total, idle


def global_lock_is_free() -> bool:
    GLOBAL_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with GLOBAL_LOCK.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return True


def v3_build_or_formal_container_exists() -> bool:
    names = subprocess.check_output(
        ["docker", "ps", "--format", "{{.Names}}"], text=True
    ).splitlines()
    blocked_prefixes = (
        "ra_mysql_v3_build_",
        "ra_formal_pg17_",
        "ra_formal_mysql80_",
    )
    return any(name.startswith(blocked_prefixes) for name in names)


def wait_for_quiet_host(
    output: Path,
    *,
    global_lock_held_by_coordinator: bool,
    timeout_seconds: int = 14400,
) -> Dict[str, Any]:
    """Require two quiet 15-second observations before each qualification phase."""
    deadline = time.monotonic() + timeout_seconds
    lane_cpus = parse_cpu_set(PG_LANE_CPUSET) | parse_cpu_set(MYSQL_LANE_CPUSET)
    observations = []
    consecutive = 0
    previous_time = time.monotonic()
    previous_disks = {name: disk_io_time_ms(name) for name in (PG_DEVICE, MYSQL_DEVICE)}
    previous_cpu = cpu_counters(lane_cpus)
    while time.monotonic() < deadline:
        time.sleep(15)
        current_time = time.monotonic()
        current_disks = {name: disk_io_time_ms(name) for name in (PG_DEVICE, MYSQL_DEVICE)}
        current_cpu = cpu_counters(lane_cpus)
        wall = current_time - previous_time
        total_delta = current_cpu[0] - previous_cpu[0]
        idle_delta = current_cpu[1] - previous_cpu[1]
        cpu_busy = 1.0 - idle_delta / total_delta
        disk_busy = {
            name: (current_disks[name] - previous_disks[name]) / (wall * 1000.0)
            for name in current_disks
        }
        passed = (
            cpu_busy <= 0.20
            and all(value <= 0.20 for value in disk_busy.values())
            and global_lock_held_by_coordinator
            and not v3_build_or_formal_container_exists()
        )
        consecutive = consecutive + 1 if passed else 0
        observations.append(
            {
                "epoch_seconds": time.time(),
                "wall_seconds": wall,
                "lane_cpu_busy_ratio": cpu_busy,
                "disk_busy_ratio": disk_busy,
                "global_lock_held_by_coordinator": global_lock_held_by_coordinator,
                "blocked_container_absent": not v3_build_or_formal_container_exists(),
                "passed": passed,
            }
        )
        atomic_json(output, {"status": "waiting" if consecutive < 2 else "passed", "observations": observations})
        if consecutive >= 2:
            return observations[-1]
        previous_time = current_time
        previous_disks = current_disks
        previous_cpu = current_cpu
    raise QualificationError("host did not become quiet before qualification timeout")


def command_pg(output: Path, barrier: Optional[Path] = None) -> List[str]:
    command = [
        sys.executable,
        str(PG_ROOT / "parallel_qualification_pg_lane.py"),
        "--output-dir",
        str(output),
        "--cpuset-cpus",
        PG_LANE_CPUSET,
        "--cpuset-mems",
        PG_LANE_MEMS,
    ]
    if barrier is not None:
        command.extend(("--barrier-dir", str(barrier)))
    return command


def command_mysql(output: Path, barrier: Optional[Path] = None) -> List[str]:
    command = [
        sys.executable,
        str(MYSQL_ROOT / "parallel_qualification_mysql_lane.py"),
        "--output-root",
        str(output),
        "--cpuset-cpus",
        MYSQL_LANE_CPUSET,
        "--cpuset-mems",
        MYSQL_LANE_MEMS,
    ]
    if barrier is not None:
        command.extend(("--barrier-dir", str(barrier)))
    return command


def run_one(command: Sequence[str], cwd: Path, log_path: Path) -> None:
    with log_path.open("w", encoding="utf-8") as handle:
        result = subprocess.run(command, cwd=cwd, stdout=handle, stderr=subprocess.STDOUT)
    if result.returncode != 0:
        raise QualificationError(f"qualification lane failed rc={result.returncode}: {log_path}")


def run_corun(pg_output: Path, mysql_output: Path, barrier: Path, logs: Path) -> Dict[str, Any]:
    barrier.mkdir(parents=True)
    pg_log = (logs / "corun_postgresql.log").open("w", encoding="utf-8")
    mysql_log = (logs / "corun_mysql.log").open("w", encoding="utf-8")
    pg = subprocess.Popen(command_pg(pg_output, barrier), cwd=PG_ROOT, stdout=pg_log, stderr=subprocess.STDOUT)
    mysql = subprocess.Popen(command_mysql(mysql_output, barrier), cwd=MYSQL_ROOT, stdout=mysql_log, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 1800
        ready_paths = (barrier / "postgresql.ready.json", barrier / "mysql.ready.json")
        while time.monotonic() < deadline:
            if pg.poll() is not None or mysql.poll() is not None:
                raise QualificationError("a co-run lane exited before the synchronized workload barrier")
            if all(path.is_file() for path in ready_paths):
                release = {
                    "qualification_id": QUALIFICATION_ID,
                    "released_epoch_seconds": time.time(),
                    "ready": [json.loads(path.read_text(encoding="utf-8")) for path in ready_paths],
                }
                atomic_json(barrier / "release.json", release)
                break
            time.sleep(0.2)
        else:
            raise QualificationError("co-run lanes did not reach the synchronized barrier")
        pg_rc = pg.wait()
        mysql_rc = mysql.wait()
        if pg_rc != 0 or mysql_rc != 0:
            raise QualificationError(f"co-run failed: pg={pg_rc}, mysql={mysql_rc}")
        return release
    finally:
        for process in (pg, mysql):
            if process.poll() is None:
                process.terminate()
        pg_log.close()
        mysql_log.close()


def relative_delta(solo: float, corun: float) -> float:
    if solo == 0:
        return math.inf if corun != 0 else 0.0
    return abs(corun - solo) / abs(solo)


def pg_result(path: Path) -> Dict[str, Any]:
    value = json.loads((path / "point_result.json").read_text(encoding="utf-8"))
    measurement = value["measurement"]
    return {
        "status": value["status"],
        "measurement_valid": bool(
            value["status"] == "complete"
            and measurement["correctness_passed"]
            and measurement["resource_stability"]["stable"]
            and not value["oom_killed"]
            and int(value["database_restart_count"]) == 0
        ),
        "xap_qps": float(measurement["xap_qps"]),
        "xtp_tps": float(measurement["xtp_tps"]),
        "latency_p95_ms": float(measurement["latency_ms"]["p95"]),
        "resource": {key: float(item) for key, item in measurement["resource_mean_utilization"].items()},
        "plan_fingerprint": value["plan"]["structural_fingerprint"],
    }


def mysql_result(path: Path) -> Dict[str, Any]:
    summaries = sorted(path.glob("mysql_hybench_xp_io/P0/repeat_1/attempt_*/attempt_summary.json"))
    if len(summaries) != 1:
        raise QualificationError(f"expected one MySQL qualification summary under {path}")
    value = json.loads(summaries[0].read_text(encoding="utf-8"))
    metrics = value["metrics"]
    return {
        "status": value["status"],
        "measurement_valid": bool(value["measurement_valid"]),
        "xap_qps": float(metrics["xap_qps"]),
        "xtp_tps": float(metrics["xtp_tps"]),
        "latency_p95_ms": float(metrics["latency_p95_ms"]),
        "resource": {key: float(item) for key, item in value["resource_means"].items()},
        "plan_fingerprint": value["plan_fingerprint_sha256"],
    }


def compare_engine(solo: Mapping[str, Any], corun: Mapping[str, Any]) -> Dict[str, Any]:
    throughput = {
        key: relative_delta(float(solo[key]), float(corun[key]))
        for key in ("xap_qps", "xtp_tps")
    }
    latency = relative_delta(float(solo["latency_p95_ms"]), float(corun["latency_p95_ms"]))
    resource = {
        key: abs(float(corun["resource"][key]) - float(solo["resource"][key]))
        for key in ("cpu", "memory", "io")
    }
    checks = {
        "solo_valid": bool(solo["measurement_valid"]),
        "corun_valid": bool(corun["measurement_valid"]),
        "plan_unchanged": solo["plan_fingerprint"] == corun["plan_fingerprint"],
        "xap_qps_relative_delta_le_0_03": throughput["xap_qps"] <= 0.03,
        "xtp_tps_relative_delta_le_0_03": throughput["xtp_tps"] <= 0.03,
        "latency_p95_relative_delta_le_0_05": latency <= 0.05,
        "target_io_absolute_delta_le_0_015": resource["io"] <= 0.015,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "throughput_relative_delta": throughput,
        "latency_p95_relative_delta": latency,
        "resource_absolute_delta": resource,
        "solo": dict(solo),
        "corun": dict(corun),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()
    root = args.output_root
    if root.exists() or root.is_symlink():
        raise QualificationError(f"qualification output already exists: {root}")
    root.mkdir(parents=True, mode=0o700)
    logs = root / "logs"
    logs.mkdir()
    manifest = {
        "schema_version": 1,
        "qualification_id": QUALIFICATION_ID,
        "status": "running",
        "countable_as_formal_result": False,
        "formal_scheduler": formal_scheduler_guard(),
        "lane_topology": validate_lane_topology(),
        "case": "hybench_xp_io_P0",
        "warmup_seconds": 60,
        "measurement_seconds": 180,
        "started_epoch_seconds": time.time(),
    }
    atomic_json(root / "qualification_manifest.json", manifest)

    GLOBAL_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with GLOBAL_LOCK.open("a+") as lock_handle:
        lock_started = time.monotonic()
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        manifest["qualification_global_lock"] = {
            "path": str(GLOBAL_LOCK),
            "acquired_epoch_seconds": time.time(),
            "wait_seconds": time.monotonic() - lock_started,
            "formal_workers_excluded": True,
        }
        atomic_json(root / "qualification_manifest.json", manifest)
        wait_for_quiet_host(
            root / "admission_pg_solo.json",
            global_lock_held_by_coordinator=True,
        )
        run_one(command_pg(root / "pg_solo"), PG_ROOT, logs / "pg_solo.log")
        wait_for_quiet_host(
            root / "admission_mysql_solo.json",
            global_lock_held_by_coordinator=True,
        )
        run_one(command_mysql(root / "mysql_solo"), MYSQL_ROOT, logs / "mysql_solo.log")
        wait_for_quiet_host(
            root / "admission_corun.json",
            global_lock_held_by_coordinator=True,
        )
        release = run_corun(
            root / "pg_corun",
            root / "mysql_corun",
            root / "corun_barrier",
            logs,
        )
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    pg = compare_engine(pg_result(root / "pg_solo"), pg_result(root / "pg_corun"))
    mysql = compare_engine(mysql_result(root / "mysql_solo"), mysql_result(root / "mysql_corun"))
    result = {
        **manifest,
        "status": "passed" if pg["passed"] and mysql["passed"] else "failed",
        "completed_epoch_seconds": time.time(),
        "barrier_release": release,
        "postgresql": pg,
        "mysql": mysql,
        "passed": bool(pg["passed"] and mysql["passed"]),
        "decision": (
            "eligible_for_two_lane_v4_repeat_confirmation"
            if pg["passed"] and mysql["passed"]
            else "keep_xp_io_serial"
        ),
    }
    atomic_json(root / "qualification_result.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
    return 0 if result["passed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())

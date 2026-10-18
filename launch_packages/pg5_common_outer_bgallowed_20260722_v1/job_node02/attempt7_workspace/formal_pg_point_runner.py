#!/usr/bin/env python3
"""Execute one fresh-template PostgreSQL point under the frozen contracts."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import signal
import statistics
import subprocess
import sys
import threading
import time
import tomllib
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import quote

import psycopg2

from formal_measurement import FrozenMeasurementProtocol, assess_measurement_samples
from formal_pg_engine import (
    ARTIFACTS,
    CAMPAIGN_ID,
    CAMPAIGN_ROOT,
    COST_PARAMETERS,
    FORMAL_JDBC_JAR,
    FREEZE_ID,
    JDBC_JAR,
    PG_PREFLIGHT,
    PG_TEMPLATE_EVIDENCE_DIRNAME,
    RUNTIME_SHM_SIZE_BYTES,
    SHARED_CONFIG,
    TARGET_DATABASE,
    FormalPgError,
    apply_cgroup_io_limits,
    atomic_json,
    container_spec,
    host_measurement_lock,
    machine_config,
    read_json,
    remove_stopped_container,
    run,
    runtime_pg_password,
    sha256_file,
    sha256_file_privileged,
    start_container,
    template_root,
    utc_now,
    validate_frozen_inputs,
)


CACHE_EVICTOR = Path(__file__).resolve().parent / "formal_pg_jdbc" / "build" / "drop_file_cache"
PRESERVE_RESTORE_CACHE = os.environ.get("RA_PG_PRESERVE_RESTORE_CACHE", "0") == "1"
HYBENCH_ROOT = Path(os.environ.get("RA_PG_HYBENCH_ROOT", "/home/anonymous/HyBench-2024-master"))
HYBENCH_JAR = ARTIFACTS["hybench_jar"][0]
HYBENCH_STMTS = ARTIFACTS["hybench_postgresql_statements"][0]
PHASE_HARD_TIMEOUT_SECONDS = int(os.environ.get("RA_PG_PHASE_HARD_TIMEOUT_SECONDS", "600"))
JOB_SQL = ARTIFACTS["job_26a"][0]
TPCH_SQL = ARTIFACTS["tpch_q18"][0]
IO_UTILIZATION_CONTRACT_SHA256 = (
    "35eb6f304627ef915f4cfb463b64e472a760325834df7978ec67cac85c342064"
)


def load_io_utilization_contract() -> Dict[str, Any]:
    """Load the exact public sum-total I/O utilization contract."""
    path = SHARED_CONFIG / "io_utilization_contract.json"
    if sha256_file(path) != IO_UTILIZATION_CONTRACT_SHA256:
        raise FormalPgError("public I/O utilization contract checksum mismatch")
    contract = read_json(path)
    if (
        contract.get("contract_id") != "pg5_io_total_throughput_utilization_v1"
        or contract.get("formula")
        != "(read_bps + write_bps) / (read_cap_bps + write_cap_bps)"
        or contract.get("single_channel_max_formula_allowed") is not False
        or contract.get("read_write_caps_must_match_physical_readback") is not True
    ):
        raise FormalPgError("public I/O utilization contract semantics mismatch")
    return contract


def load_protocols() -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    return (
        read_json(SHARED_CONFIG / "benchmark_matrix.json"),
        read_json(SHARED_CONFIG / "measurement_protocol.json"),
        read_json(SHARED_CONFIG / "preflight_gates.json"),
    )


def default_cost_parameters() -> Dict[str, float]:
    search = read_json(SHARED_CONFIG / "search_spaces.json")["postgresql"]
    return {name: float(search["default"][name]) for name in COST_PARAMETERS}


def joint_path_points() -> Dict[str, Dict[str, float]]:
    search = read_json(SHARED_CONFIG / "search_spaces.json")["postgresql"]
    points: Dict[str, Dict[str, float]] = {"P0": default_cost_parameters()}
    for index in range(11):
        fraction = index / 10.0
        point: Dict[str, float] = {}
        for name in COST_PARAMETERS:
            spec = search["parameters"][name]
            lower = float(spec["min"])
            upper = float(spec["max"])
            if index == 0:
                point[name] = lower
            elif index == 10:
                point[name] = upper
            elif spec["transform"] == "log":
                point[name] = math.exp(math.log(lower) + fraction * (math.log(upper) - math.log(lower)))
            elif spec["transform"] == "linear":
                point[name] = lower + fraction * (upper - lower)
            else:
                raise FormalPgError(f"unsupported frozen transform for {name}")
        points[f"G{index}"] = point
    return points


def repeat_ids(point_id: str) -> List[int]:
    return [1, 2, 3] if point_id in {"P0", "G0", "G10"} else [1]


def case_map() -> Dict[str, Dict[str, Any]]:
    benchmark = read_json(SHARED_CONFIG / "benchmark_matrix.json")
    return {
        case["case_id"]: dict(case)
        for case in benchmark["cases"]
        if case.get("engine") == "postgresql"
    }


def workload_kind(workload: str) -> str:
    if workload in {"hybench_ap", "hybench_xp"}:
        return "hybench"
    if workload in {"job", "tpch"}:
        return workload
    raise FormalPgError(f"unsupported PostgreSQL workload: {workload}")


def working_root() -> Path:
    return Path(machine_config()["engines"]["postgresql"]["working_root"])


def validate_template(kind: str) -> Dict[str, Any]:
    manifest_path = PG_PREFLIGHT / PG_TEMPLATE_EVIDENCE_DIRNAME / kind / "manifest.json"
    manifest = read_json(manifest_path)
    if (
        manifest.get("schema_version") != 1
        or manifest.get("freeze_id") != FREEZE_ID
        or manifest.get("global_campaign_id") != CAMPAIGN_ID
        or manifest.get("status") != "frozen"
        or manifest.get("kind") != kind
        or "physical_template_adoption" in manifest
    ):
        raise FormalPgError(f"invalid frozen template manifest: {manifest_path}")
    pgdata = template_root() / manifest["template_relative_path"] / "pgdata"
    if pgdata.is_symlink() or not pgdata.is_dir():
        raise FormalPgError(f"template PGDATA is invalid: {pgdata}")
    writable = run(
        ["sudo", "find", str(pgdata), "-xdev", "-perm", "/222", "-print", "-quit"]
    ).stdout.strip()
    if writable:
        raise FormalPgError(f"template contains a writable path: {writable}")
    internal = pgdata.parent / "template_manifest.json"
    internal_sha256 = sha256_file(internal)
    expected_sha256 = manifest.get("template_manifest_sha256")
    if not isinstance(expected_sha256, str) or internal_sha256 != expected_sha256:
        raise FormalPgError(f"template manifest checksum mismatch: {kind}")
    internal_manifest = read_json(internal)
    if (
        internal_manifest.get("schema_version") != 1
        or internal_manifest.get("freeze_id") != FREEZE_ID
        or internal_manifest.get("global_campaign_id") != CAMPAIGN_ID
        or internal_manifest.get("status") != "frozen"
        or internal_manifest.get("kind") != kind
        or "physical_template_adoption" in internal_manifest
    ):
        raise FormalPgError(f"invalid embedded frozen template manifest: {internal}")
    return {
        **manifest,
        "resolved_pgdata": str(pgdata),
        "resolved_template_manifest_sha256": internal_sha256,
    }


def safe_runtime_root(path: Path) -> None:
    base = (working_root() / "postgresql").resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise FormalPgError(f"unsafe PostgreSQL runtime path: {path}") from exc
    if resolved == base or len(resolved.parts) <= len(base.parts) + 1:
        raise FormalPgError(f"refusing broad PostgreSQL runtime target: {path}")


def restore_overlay(kind: str, token: str) -> Dict[str, Any]:
    """Restore a disposable physical copy of an immutable frozen template.

    Current-environment smoke rejected OverlayFS metacopy because PostgreSQL
    file opens could return EINTR under the frozen cgroup-v1 I/O throttle.  A
    physical copy is slower but preserves fresh state without changing the
    frozen template or resource contract.
    """
    manifest = validate_template(kind)
    lower = Path(manifest["resolved_pgdata"])
    root = working_root() / "postgresql" / kind / token
    safe_runtime_root(root)
    if root.exists() or root.is_symlink():
        raise FormalPgError(f"fresh runtime root already exists: {root}")
    root.mkdir(parents=True, mode=0o700)
    merged = root / "pgdata"
    merged.mkdir(mode=0o700)
    copy_started = time.monotonic()
    reflink_probe = run(["cp", "--reflink=never", "--version"], check=False)
    copy_mode = "physical_cp_archive"
    copy_command = ["sudo", "cp", "-a"]
    if reflink_probe.returncode == 0:
        copy_mode = "physical_cp_reflink_never"
        copy_command.append("--reflink=never")
    copy_command.extend([str(lower) + "/.", str(merged)])
    run(
        copy_command,
        capture=False,
    )
    copy_wall_seconds = time.monotonic() - copy_started
    expected_bytes = int(manifest["build_runtime"]["pgdata_bytes"])
    copied_bytes = int(run(["sudo", "du", "-sb", str(merged)]).stdout.split()[0])
    # `du -sb` includes directory inode sizes, which legitimately differ after
    # extracting/copying the same PGDATA on another filesystem.  Validate the
    # actual regular-file payload instead; pg_control is verified separately.
    source_sizes = [
        int(value)
        for value in run(
            ["sudo", "find", str(lower), "-type", "f", "-printf", "%s\\n"]
        ).stdout.splitlines()
        if value
    ]
    copied_sizes = [
        int(value)
        for value in run(
            ["sudo", "find", str(merged), "-type", "f", "-printf", "%s\\n"]
        ).stdout.splitlines()
        if value
    ]
    source_payload = {"file_count": len(source_sizes), "file_bytes": sum(source_sizes)}
    copied_payload = {"file_count": len(copied_sizes), "file_bytes": sum(copied_sizes)}
    if copied_payload != source_payload:
        raise FormalPgError(
            "fresh physical copy payload mismatch: "
            f"source={source_payload} copied={copied_payload}"
        )
    control = merged / "global" / "pg_control"
    control_sha256 = sha256_file_privileged(control)
    expected_control_sha256 = str(
        manifest["build_runtime"]["pg_control_sha256_after_clean_shutdown"]
    )
    if control_sha256 != expected_control_sha256:
        raise FormalPgError("fresh physical copy pg_control checksum mismatch")
    # Re-enable writes only in the disposable copy.  The frozen lower tree is
    # revalidated as recursively read-only before every restore.
    run(["sudo", "chmod", "-R", "u+w", str(merged)], capture=False)
    if PRESERVE_RESTORE_CACHE:
        eviction = "skipped_for_warm_cache_memory_scenario"
    else:
        eviction = run(["sudo", str(CACHE_EVICTOR), str(merged)]).stdout.strip()
    mount = run(
        ["findmnt", "-T", str(merged), "-n", "-o", "SOURCE,FSTYPE,OPTIONS"]
    ).stdout.strip()
    return {
        "template_manifest_sha256": manifest["resolved_template_manifest_sha256"],
        "template_pgdata": str(lower),
        "runtime_root": str(root),
        "merged": str(merged),
        "restore_strategy": copy_mode,
        "copied_pgdata_bytes": copied_bytes,
        "manifest_pgdata_du_bytes": expected_bytes,
        "source_regular_file_payload": source_payload,
        "copied_regular_file_payload": copied_payload,
        "copied_pg_control_sha256": control_sha256,
        "copy_wall_seconds": copy_wall_seconds,
        "cache_eviction": eviction,
        "cache_eviction_target": (
            "none_preserve_copy_cache" if PRESERVE_RESTORE_CACHE
            else "disposable_physical_copy_after_copy"
        ),
        "cache_state_inherited": PRESERVE_RESTORE_CACHE,
        "xp_committed_state_inherited": False,
        "mount_readback": mount,
        "runtime_owner_write_restored_in_disposable_copy": True,
        "frozen_template_modified": False,
        "restored_at_utc": utc_now(),
    }


def reset_io_measurement_cache(kind: str, profile: str, pgdata: Path) -> Dict[str, Any]:
    """reset io measurement cache."""
    name, _ = container_spec(kind)
    began = time.monotonic()
    run(["docker", "stop", "--time", "60", name])
    eviction = run(["sudo", str(CACHE_EVICTOR), str(pgdata)]).stdout.strip()
    run(["docker", "start", name])
    restarted = json.loads(run(["docker", "inspect", name]).stdout)[0]
    apply_cgroup_io_limits(str(restarted["Id"]), profile)
    deadline = time.monotonic() + 180.0
    health = "starting"
    while time.monotonic() < deadline:
        inspect = json.loads(run(["docker", "inspect", name]).stdout)[0]
        health = str((inspect.get("State", {}).get("Health") or {}).get("Status") or "none")
        if health == "healthy":
            break
        if not bool(inspect.get("State", {}).get("Running")):
            raise FormalPgError(f"IO cache-reset restart stopped unexpectedly: {name}")
        time.sleep(2)
    if health != "healthy":
        raise FormalPgError(f"IO cache-reset restart did not become healthy: {name}={health}")
    return {
        "status": "complete",
        "strategy": "clean_stop_posix_fadvise_dontneed_start",
        "target": str(pgdata),
        "eviction": eviction,
        "health_after_restart": health,
        "io_limit_reapplied_and_read_back_after_restart": True,
        "wall_seconds": time.monotonic() - began,
        "performed_after_warmup_before_measurement": True,
    }


def cleanup_overlay(kind: str, restore: Mapping[str, Any]) -> Dict[str, Any]:
    name, _ = container_spec(kind)
    remove_stopped_container(name)
    root = Path(str(restore["runtime_root"]))
    merged = Path(str(restore["merged"]))
    safe_runtime_root(root)
    mountpoint = run(["mountpoint", "-q", str(merged)], check=False)
    if mountpoint.returncode == 0:
        run(["sudo", "umount", str(merged)], capture=False)
    runtime_bytes = 0
    runtime_allocated_bytes = 0
    if root.exists():
        runtime_bytes = int(run(["sudo", "du", "-sb", str(root)]).stdout.split()[0])
        runtime_allocated_bytes = int(
            run(["sudo", "du", "-s", "--block-size=1", str(root)]).stdout.split()[0]
        )
        run(["sudo", "find", str(root), "-xdev", "-depth", "-delete"], capture=False)
    if root.exists() or run(["mountpoint", "-q", str(merged)], check=False).returncode == 0:
        raise FormalPgError(f"runtime cleanup is incomplete: {root}")
    return {
        "cleanup_complete": True,
        "cleaned_at_utc": utc_now(),
        "discarded_transient_runtime_bytes": runtime_bytes,
        "discarded_transient_runtime_allocated_bytes": runtime_allocated_bytes,
        "discarded_restore_strategy": restore.get("restore_strategy", "legacy_overlay"),
        "template_preserved": True,
    }


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def cgroup_directory(controller: str, container_id: str) -> Path:
    root = Path("/sys/fs/cgroup") / controller
    candidates = (
        root / "docker-limited" / container_id,
        root / "docker" / container_id,
        root / "system.slice" / f"docker-{container_id}.scope",
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise FormalPgError(
        "Docker cgroup is missing; checked: "
        + ", ".join(str(candidate) for candidate in candidates)
    )


def parse_io_bytes(path: Path, device: str) -> Tuple[int, int]:
    read_bytes = 0
    write_bytes = 0
    for line in read_text(path).splitlines():
        fields = line.split()
        if len(fields) != 3 or fields[0] != device:
            continue
        if fields[1] == "Read":
            read_bytes = int(fields[2])
        elif fields[1] == "Write":
            write_bytes = int(fields[2])
    return read_bytes, write_bytes


def parse_throttle_bps(path: Path) -> Dict[str, int]:
    values: Dict[str, int] = {}
    for line in read_text(path).splitlines():
        fields = line.split()
        if len(fields) == 2:
            values[fields[0]] = int(fields[1])
    return values


def disk_io_ticks(device: str) -> int:
    major, minor = (int(value) for value in device.split(":"))
    for line in Path("/proc/diskstats").read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if int(fields[0]) == major and int(fields[1]) == minor:
            return int(fields[12])
    raise FormalPgError(f"resolved database device is missing from /proc/diskstats: {device}")


def host_cpu_times() -> Tuple[int, int]:
    fields = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()[1:]
    values = [int(value) for value in fields]
    return sum(values), values[3] + values[4]


def wait_for_host_admission() -> Dict[str, Any]:
    policy = machine_config()["global_isolation"]["host_admission"]
    cpu_limit = float(policy["cpu_busy_max_ratio"])
    disk_limit = float(policy["target_disk_busy_max_ratio"])
    required = int(policy["required_consecutive_passes"])
    poll_seconds = int(policy["poll_seconds"])
    timeout_seconds = int(policy["timeout_seconds"])
    device = str(machine_config()["engines"]["postgresql"]["observed_device"])
    began = time.monotonic()
    consecutive = 0
    checks = []
    previous_cpu, previous_idle = host_cpu_times()
    previous_ticks = disk_io_ticks(device)
    previous_time = time.monotonic()
    while time.monotonic() - began <= timeout_seconds:
        time.sleep(poll_seconds)
        current_time = time.monotonic()
        current_cpu, current_idle = host_cpu_times()
        current_ticks = disk_io_ticks(device)
        total_delta = current_cpu - previous_cpu
        idle_delta = current_idle - previous_idle
        seconds = current_time - previous_time
        cpu_busy = (total_delta - idle_delta) / total_delta if total_delta > 0 else 1.0
        disk_busy = (current_ticks - previous_ticks) / (seconds * 1000.0)
        passed = cpu_busy <= cpu_limit and disk_busy <= disk_limit
        consecutive = consecutive + 1 if passed else 0
        checks.append(
            {
                "timestamp_utc": utc_now(),
                "cpu_busy_ratio": cpu_busy,
                "disk_busy_ratio": disk_busy,
                "cpu_busy_max_ratio": cpu_limit,
                "disk_busy_max_ratio": disk_limit,
                "passed": passed,
            }
        )
        if consecutive >= required:
            return {
                "status": "passed",
                "wait_seconds": time.monotonic() - began,
                "required_consecutive_passes": required,
                "checks": checks,
            }
        previous_cpu, previous_idle = current_cpu, current_idle
        previous_ticks = current_ticks
        previous_time = current_time
    raise FormalPgError(
        f"host admission timed out after {timeout_seconds}s; last checks={checks[-required:]}"
    )


class ResourceSampler(threading.Thread):
    def __init__(self, container_id: str, database: str, output: Path, stop: threading.Event):
        super().__init__(name="formal-pg-resource-sampler", daemon=True)
        self.container_id = container_id
        self.database = database
        self.output = output
        self.stop_event = stop
        self.error: Optional[str] = None

    def run(self) -> None:
        try:
            machine = machine_config()
            device = str(machine["engines"]["postgresql"]["observed_device"])
            cpu = cgroup_directory("cpu,cpuacct", self.container_id)
            memory = cgroup_directory("memory", self.container_id)
            blkio = cgroup_directory("blkio", self.container_id)
            connection = psycopg2.connect(
                host="127.0.0.1",
                port=container_spec(workload_kind_for_database(self.database))[1],
                user="postgres",
                password=runtime_pg_password(workload_kind_for_database(self.database)),
                dbname=self.database,
                application_name="formal-resource-sampler-diagnostic",
            )
            connection.autocommit = True
            with self.output.open("x", encoding="utf-8") as handle, connection:
                while not self.stop_event.is_set():
                    monotonic_ns = time.monotonic_ns()
                    wall_utc = dt.datetime.now(dt.timezone.utc).isoformat()
                    io_read, io_write = parse_io_bytes(
                        blkio / "blkio.throttle.io_service_bytes", device
                    )
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "select xact_commit,xact_rollback,blks_read,blks_hit,temp_bytes,deadlocks "
                            "from pg_stat_database where datname=current_database()"
                        )
                        database_counters = list(cursor.fetchone())
                    memory_current = int(read_text(memory / "memory.usage_in_bytes"))
                    memory_stat = {}
                    for stat_line in read_text(memory / "memory.stat").splitlines():
                        pieces = stat_line.split()
                        if len(pieces) == 2:
                            memory_stat[pieces[0]] = int(pieces[1])
                    inactive_file = memory_stat.get(
                        "total_inactive_file", memory_stat.get("inactive_file", 0)
                    )
                    memory_working_set = max(0, memory_current - inactive_file)
                    record = {
                        "schema_version": 1,
                        "timestamp_utc": wall_utc,
                        "monotonic_ns": monotonic_ns,
                        "cpu_usage_ns": int(read_text(cpu / "cpuacct.usage")),
                        "cpu_quota_us": int(read_text(cpu / "cpu.cfs_quota_us")),
                        "cpu_period_us": int(read_text(cpu / "cpu.cfs_period_us")),
                        "memory_current_bytes": memory_current,
                        "memory_limit_bytes": int(read_text(memory / "memory.limit_in_bytes")),
                        "memory_inactive_file_bytes": inactive_file,
                        "memory_working_set_bytes": memory_working_set,
                        "memory_failcnt": int(read_text(memory / "memory.failcnt")),
                        "memory_pgmajfault": memory_stat.get(
                            "total_pgmajfault", memory_stat.get("pgmajfault", 0)
                        ),
                        "memory_workingset_refault": memory_stat.get(
                            "total_workingset_refault", memory_stat.get("workingset_refault", 0)
                        ),
                        "io_device": device,
                        "io_read_bytes": io_read,
                        "io_write_bytes": io_write,
                        "device_io_time_ms": disk_io_ticks(device),
                        "pg_xact_commit": int(database_counters[0]),
                        "pg_xact_rollback": int(database_counters[1]),
                        "pg_blks_read": int(database_counters[2]),
                        "pg_blks_hit": int(database_counters[3]),
                        "pg_temp_bytes": int(database_counters[4]),
                        "pg_deadlocks": int(database_counters[5]),
                        "raw_counter_units": {
                            "cpu_usage_ns": "nanoseconds",
                            "memory": "bytes",
                            "io_bytes": "bytes",
                            "device_io_time": "milliseconds",
                            "postgresql": "counter",
                        },
                    }
                    handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                    handle.flush()
                    began = time.monotonic()
                    remaining = 1.0 - (began - monotonic_ns / 1_000_000_000.0)
                    self.stop_event.wait(max(0.0, remaining))
            connection.close()
        except BaseException as exc:
            self.error = f"{type(exc).__name__}: {exc}"


def workload_kind_for_database(database: str) -> str:
    for kind, value in TARGET_DATABASE.items():
        if value == database:
            return kind
    raise FormalPgError(f"unknown formal database: {database}")


def pg_url(kind: str, *, adapter: bool = False, audit: bool = False) -> str:
    if adapter and audit:
        raise FormalPgError("JDBC URL cannot use both commit and transaction-audit adapters")
    _, port = container_spec(kind)
    database = TARGET_DATABASE[kind]
    if adapter:
        prefix = "jdbc:formalpg:postgresql"
    elif audit:
        prefix = "jdbc:formalpg-audit:postgresql"
    else:
        prefix = "jdbc:postgresql"
    return (
        f"{prefix}://127.0.0.1:{port}/{database}?sslmode=disable"
        "&prepareThreshold=5&defaultRowFetchSize=1000"
        f"&ApplicationName=formal-{kind}-client"
    )


def runtime_hybench_properties(workload: str, duration_seconds: int) -> str:
    benchmark = read_json(SHARED_CONFIG / "benchmark_matrix.json")
    workload_config = benchmark["workloads"][workload]
    query_ids = [int(value) for value in workload_config["ap_query_ids"]]
    query_weights = [int(value) for value in workload_config["ap_query_weights"]]
    if (
        not query_ids
        or len(query_ids) != len(query_weights)
        or len(set(query_ids)) != len(query_ids)
        or any(query_id < 1 or query_id > 13 for query_id in query_ids)
        or any(weight <= 0 for weight in query_weights)
        or sum(query_weights) != 100
    ):
        raise FormalPgError(f"invalid frozen HyBench AP mix for {workload}")
    kind = "hybench"
    password = runtime_pg_password(kind)
    if duration_seconds % 60 != 0:
        raise FormalPgError("formal HyBench duration must be a whole number of minutes")
    duration_minutes = duration_seconds // 60
    ap_clients = 4 if workload == "hybench_ap" else 8
    xtp_clients = 0 if workload == "hybench_ap" else 8
    return "\n".join(
        [
            "sf=10x",
            "db=postgreSQL",
            "classname=formal.pg.FormalPgAuditDriver",
            "classname_ap=formal.pg.FormalPgCommitDriver",
            "username=postgres",
            f"password={password}",
            "username_ap=postgres",
            f"password_ap={password}",
            f"url={pg_url(kind, audit=True)}",
            f"url_tp={pg_url(kind, audit=True)}",
            f"url_ap={pg_url(kind, adapter=True)}",
            "at1_percent=40",
            "at2_percent=30",
            "at3_percent=15",
            "at4_percent=5",
            "at5_percent=7",
            "at6_percent=3",
            "tpclient=0",
            f"apclient={ap_clients}",
            f"xtpclient={xtp_clients}",
            f"xapclient={ap_clients}",
            "fresh_interval=20",
            f"apRunMins={duration_minutes}",
            f"apRunSecs={duration_seconds}",
            f"tpRunMins={duration_minutes}",
            f"tpRunSecs={duration_seconds}",
            f"xpRunMins={duration_minutes}",
            f"xpRunSecs={duration_seconds}",
            "risk_rate=0.1",
            "power_alpha=0.1",
            "apround=1",
            "random_seed=4508333",
            "ap_query_ids=" + ",".join(str(value) for value in query_ids),
            "ap_query_weights=" + ",".join(str(value) for value in query_weights),
            "dist=zipfian",
            "contention_num=10000",
            "progress_windows=10",
            "",
        ]
    )


def benchmark_config(workload: str, duration_seconds: int) -> Dict[str, Any]:
    benchmark, _, _ = load_protocols()
    global_config = benchmark["global"]
    workload_config = benchmark["workloads"][workload]
    artifacts = {
        name: {"path": str(path), "sha256": expected}
        for name, (path, expected) in ARTIFACTS.items()
    }
    if workload == "hybench_xp":
        risk_files = {
            "hybench_related_transfer_bids": HYBENCH_ROOT / "Data_10x" / "Related_transfer_bids",
            "hybench_related_checking_bids": HYBENCH_ROOT / "Data_10x" / "Related_checking_bids",
        }
        for name, path in risk_files.items():
            if not path.is_file():
                raise FormalPgError(f"required HyBench XP risk-control data is missing: {path}")
            artifacts[name] = {"path": str(path), "sha256": sha256_file(path)}
    return {
        "schema_version": 1,
        "freeze_id": FREEZE_ID,
        "global_campaign_id": CAMPAIGN_ID,
        "engine": "postgresql",
        "workload": workload,
        "global": global_config,
        "workload_config": workload_config,
        "client_semantics": benchmark["client_semantics"]["postgresql"],
        "duration_seconds": duration_seconds,
        "jdbc_url_readback": {
            "transactional": pg_url(workload_kind(workload), audit=True),
            "analytical": pg_url(workload_kind(workload), adapter=True),
            "delegate_driver": pg_url(workload_kind(workload)),
        },
        "credentials": {"path": "/home/anonymous/.pgpass", "runtime_user": "postgres", "password": "redacted"},
        "artifacts": artifacts,
        "jdbc": {"path": str(JDBC_JAR), "sha256": sha256_file(JDBC_JAR), "version": "42.7.4"},
        "adapter": {"path": str(FORMAL_JDBC_JAR), "sha256": sha256_file(FORMAL_JDBC_JAR)},
    }


def _event_epoch(row: Mapping[str, Any]) -> float:
    return dt.datetime.fromisoformat(
        str(row["timestamp_utc"]).replace("Z", "+00:00")
    ).timestamp()


def _stability_samples_from_evidence(
    workload: str,
    scenario: str,
    sampler_path: Path,
    query_log_path: Path,
    transaction_log_path: Optional[Path],
    *,
    cutoff_epoch: Optional[float] = None,
) -> List[Dict[str, float]]:
    """Build the exact per-second stability input from currently flushed evidence."""
    sampler_rows = parse_jsonl(sampler_path)
    resource_samples = derive_resource_samples(sampler_rows, scenario)
    if cutoff_epoch is not None:
        resource_samples = [
            sample
            for sample in resource_samples
            if float(sample["end_epoch"]) <= cutoff_epoch
        ]
    query_epochs = [
        _event_epoch(row)
        for row in parse_jsonl(query_log_path)
        if row.get("event") == "query_complete"
        and row.get("success") is True
        and (cutoff_epoch is None or _event_epoch(row) <= cutoff_epoch)
    ]
    transaction_epochs = [
        _event_epoch(row)
        for row in parse_jsonl(transaction_log_path)
        if row.get("event") == "transaction_complete"
        and row.get("at_type") in {f"AT{index}" for index in range(1, 7)}
        and row.get("outcome") in {"commit", "rollback"}
        and int(row.get("error_count") or 0) == 0
        and (cutoff_epoch is None or _event_epoch(row) <= cutoff_epoch)
    ] if transaction_log_path is not None else []
    for sample in resource_samples:
        seconds = float(sample["seconds"])
        query_count = sum(
            1
            for epoch in query_epochs
            if sample["start_epoch"] < epoch <= sample["end_epoch"]
        )
        if workload == "hybench_xp":
            transaction_count = sum(
                1
                for epoch in transaction_epochs
                if sample["start_epoch"] < epoch <= sample["end_epoch"]
            )
            sample["xap_qps"] = query_count / seconds
            sample["xtp_tps"] = transaction_count / seconds
        else:
            sample["qps"] = query_count / seconds
    return resource_samples


def _terminate_process_group(
    process: subprocess.Popen[Any],
    *,
    grace_seconds: float = 10.0,
) -> Dict[str, Any]:
    """Stop and reap one workload process group without leaving descendants."""
    term_sent = False
    kill_sent = False
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            term_sent = True
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
                kill_sent = True
            except ProcessLookupError:
                pass
        process.wait()
    return {
        "term_sent": term_sent,
        "kill_sent": kill_sent,
        "raw_returncode": process.returncode,
        "termination_grace_seconds": grace_seconds,
    }


def _run_workload_command(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    stdout_path: Path,
    duration_seconds: int,
    online_stability: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Run a workload, optionally stopping at the first stable complete window."""
    began_monotonic = time.monotonic()
    began_epoch = time.time()
    began_utc = dt.datetime.fromtimestamp(
        began_epoch, tz=dt.timezone.utc
    ).isoformat()
    with stdout_path.open("x", encoding="utf-8") as stdout:
        if online_stability is None:
            completed = subprocess.run(
                list(command),
                cwd=cwd,
                env=dict(environment),
                stdout=stdout,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=max(
                    duration_seconds,
                    min(duration_seconds + 660, PHASE_HARD_TIMEOUT_SECONDS),
                ),
                check=False,
            )
            raw_returncode = completed.returncode
            stop_record: Dict[str, Any] = {
                "enabled": False,
                "termination_reason": "natural_exit",
                "planned_max_seconds": duration_seconds,
                "raw_returncode": raw_returncode,
            }
        else:
            protocol = FrozenMeasurementProtocol.from_frozen_bundle(SHARED_CONFIG)
            if duration_seconds != protocol.maximum_measurement_seconds:
                raise FormalPgError(
                    "online stability measurement must launch with the frozen 600-second maximum"
                )
            process = subprocess.Popen(
                list(command),
                cwd=cwd,
                env=dict(environment),
                stdout=stdout,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            checks: List[Dict[str, Any]] = []
            last_complete_window_count = -1
            termination_reason: Optional[str] = None
            trigger_assessment: Optional[Dict[str, Any]] = None
            termination: Dict[str, Any] = {
                "term_sent": False,
                "kill_sent": False,
                "raw_returncode": None,
            }
            try:
                while process.poll() is None:
                    elapsed = time.monotonic() - began_monotonic
                    sampler = online_stability.get("sampler")
                    sampler_error = getattr(sampler, "error", None)
                    if sampler_error:
                        termination_reason = "sampler_failure"
                    elif elapsed >= protocol.minimum_measurement_seconds:
                        assessment: Dict[str, Any]
                        try:
                            samples = _stability_samples_from_evidence(
                                str(online_stability["workload"]),
                                str(online_stability["scenario"]),
                                Path(str(online_stability["sampler_path"])),
                                Path(str(online_stability["query_log_path"])),
                                (
                                    Path(str(online_stability["transaction_log_path"]))
                                    if online_stability.get("transaction_log_path")
                                    else None
                                ),
                            )
                            assessment = resource_stability(
                                samples,
                                str(online_stability["workload"]),
                                measurement_start_epoch_seconds=began_epoch,
                                now_epoch_seconds=began_epoch + elapsed,
                            )
                        except Exception as exc:
                            assessment = {
                                "passed": False,
                                "stable": False,
                                "complete_window_count": 0,
                                "online_assessment_error": f"{type(exc).__name__}: {exc}",
                            }
                        complete_windows = int(
                            assessment.get("complete_window_count") or 0
                        )
                        if (
                            complete_windows != last_complete_window_count
                            or elapsed >= protocol.maximum_measurement_seconds
                        ):
                            checks.append(
                                {
                                    "checked_wall_elapsed_seconds": elapsed,
                                    "assessment": assessment,
                                }
                            )
                            last_complete_window_count = complete_windows
                        if assessment.get("stable") is True:
                            termination_reason = "stable_windows"
                            trigger_assessment = assessment
                        elif elapsed >= protocol.maximum_measurement_seconds:
                            termination_reason = "hard_maximum"
                            trigger_assessment = assessment
                    if termination_reason is not None:
                        termination = _terminate_process_group(process)
                        break
                    time.sleep(0.5)
                if process.poll() is None:
                    termination = _terminate_process_group(process)
                else:
                    process.wait()
                    termination["raw_returncode"] = process.returncode
            except BaseException:
                _terminate_process_group(process)
                raise
            raw_returncode = int(process.returncode or 0)
            intentional_stop = termination_reason in {
                "stable_windows",
                "hard_maximum",
            }
            if termination_reason is None:
                termination_reason = "natural_exit"
            earliest_stop = (
                int(trigger_assessment.get("earliest_stable_stop_seconds"))
                if trigger_assessment
                and trigger_assessment.get("earliest_stable_stop_seconds") is not None
                else None
            )
            effective_seconds = (
                earliest_stop
                if termination_reason == "stable_windows" and earliest_stop is not None
                else min(
                    duration_seconds,
                    max(0, int(time.monotonic() - began_monotonic)),
                )
            )
            cutoff_epoch = began_epoch + effective_seconds
            stop_record = {
                "enabled": True,
                "minimum_measurement_seconds": protocol.minimum_measurement_seconds,
                "planned_max_seconds": protocol.maximum_measurement_seconds,
                "stability_window_seconds": protocol.stability_window_seconds,
                "required_consecutive_stability_windows": (
                    protocol.required_consecutive_stability_windows
                ),
                "termination_reason": termination_reason,
                "intentional_stop": intentional_stop,
                "effective_window_seconds": effective_seconds,
                "measurement_started_epoch": began_epoch,
                "measurement_cutoff_epoch": cutoff_epoch,
                "trigger_assessment": trigger_assessment,
                "checks": checks,
                **termination,
            }
            checks_path = stdout_path.with_name("measurement_online_stability_checks.json")
            atomic_json(checks_path, stop_record)
            stop_record["checks_path"] = str(checks_path)
            stop_record["checks_sha256"] = sha256_file(checks_path)
        stdout.flush()
        os.fsync(stdout.fileno())
    intentional = bool(stop_record.get("intentional_stop"))
    effective_exit_code = 0 if intentional else int(raw_returncode)
    return {
        "exit_code": effective_exit_code,
        "raw_returncode": int(raw_returncode),
        "intentional_online_stop": intentional,
        "online_stability_stop": stop_record,
        "measurement_started_at_utc": began_utc,
        "measurement_started_epoch": began_epoch,
        "wall_seconds": time.monotonic() - began_monotonic,
    }


def execute_hybench(
    workload: str,
    duration_seconds: int,
    output_dir: Path,
    phase: str,
    *,
    online_stability: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    props_path = output_dir / f"hybench_{phase}.props"
    props_path.write_text(runtime_hybench_properties(workload, duration_seconds), encoding="utf-8")
    os.chmod(props_path, 0o600)
    result_log = output_dir / f"{phase}_queries.jsonl"
    transaction_log = output_dir / f"{phase}_transactions.jsonl"
    stdout_path = output_dir / f"{phase}_hybench.log"
    command = [
        "java",
        "-cp",
        f"{JDBC_JAR}:{FORMAL_JDBC_JAR}:{HYBENCH_JAR}",
        "com.hybench.HyBench",
        "-t",
        "runap" if workload == "hybench_ap" else "runxp",
        "-c",
        str(props_path),
        "-f",
        str(HYBENCH_STMTS),
    ]
    environment = dict(os.environ)
    environment["FORMAL_PG_RESULT_LOG"] = str(result_log)
    environment["FORMAL_PG_TXN_LOG"] = str(transaction_log)
    process = _run_workload_command(
        command,
        cwd=HYBENCH_ROOT,
        environment=environment,
        stdout_path=stdout_path,
        duration_seconds=duration_seconds,
        online_stability=(
            {
                **dict(online_stability),
                "query_log_path": result_log,
                "transaction_log_path": transaction_log,
            }
            if online_stability is not None
            else None
        ),
    )
    return {
        "command": command,
        **process,
        "properties_path": str(props_path),
        "properties_sha256": sha256_file(props_path),
        "stdout_path": str(stdout_path),
        "stdout_sha256": sha256_file(stdout_path),
        "query_log_path": str(result_log),
        "query_log_sha256": sha256_file(result_log) if result_log.is_file() else None,
        "transaction_log_path": str(transaction_log),
        "transaction_log_sha256": (
            sha256_file(transaction_log) if transaction_log.is_file() else None
        ),
    }


def sql_for_workload(workload: str) -> Path:
    if workload == "job":
        return JOB_SQL
    if workload == "tpch":
        return TPCH_SQL
    raise FormalPgError(f"no exact SQL file for workload {workload}")


def sql_mix_for_workload(workload: str) -> Tuple[List[str], List[Path], List[int]]:
    benchmark = read_json(SHARED_CONFIG / "benchmark_matrix.json")
    workload_config = benchmark["workloads"][workload]
    if workload == "job":
        names = [str(value) for value in workload_config["query_sequence"]]
        ids = [Path(name).stem for name in names]
        paths = [JOB_SQL.parent / name for name in names]
        weights = [int(value) for value in workload_config["query_weights"]]
    elif workload == "tpch":
        query_ids = [int(value) for value in workload_config["query_ids"]]
        ids = [f"Q{query_id}" for query_id in query_ids]
        paths = [
            TPCH_SQL.parent / f"q{query_id:02d}.sql"
            for query_id in query_ids
        ]
        weights = [int(value) for value in workload_config["query_weights"]]
    else:
        raise FormalPgError(f"no SQL mix for workload {workload}")
    if (
        not paths
        or len(paths) != len(ids)
        or len(paths) != len(weights)
        or len(set(ids)) != len(ids)
        or any(weight <= 0 for weight in weights)
        or sum(weights) != 100
    ):
        raise FormalPgError(f"invalid frozen SQL mix for {workload}")
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FormalPgError(f"frozen SQL mix files are missing for {workload}: {missing}")
    expected_hashes = workload_config.get("query_sha256")
    if not isinstance(expected_hashes, dict):
        raise FormalPgError(f"frozen SQL hashes are missing for {workload}")
    hash_keys = names if workload == "job" else ids
    for query_key, path in zip(hash_keys, paths):
        actual = sha256_file(path)
        if expected_hashes.get(query_key) != actual:
            raise FormalPgError(
                f"frozen SQL checksum mismatch for {workload}/{query_key}: {actual}"
            )
    return ids, paths, weights


def execute_sql_loop(
    workload: str,
    duration_seconds: int,
    output_dir: Path,
    phase: str,
    *,
    online_stability: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    kind = workload_kind(workload)
    workload_config = read_json(SHARED_CONFIG / "benchmark_matrix.json")["workloads"][workload]
    default_terminals = int(workload_config.get("terminals", workload_config.get("clients", 1)))
    terminals = int(
        workload_config.get("engine_terminals", {}).get("postgresql", default_terminals)
    )
    if terminals <= 0:
        raise FormalPgError(f"invalid frozen PostgreSQL terminal count for {workload}: {terminals}")
    query_ids, query_paths, query_weights = sql_mix_for_workload(workload)
    result_log = output_dir / f"{phase}_queries.jsonl"
    stdout_path = output_dir / f"{phase}_sql_loop.log"
    command = [
        "java",
        "-cp",
        f"{JDBC_JAR}:{FORMAL_JDBC_JAR}",
        "formal.pg.FormalSqlLoop",
        str(sql_for_workload(workload)),
        str(duration_seconds),
        str(terminals),
        str(result_log),
    ]
    environment = dict(os.environ)
    environment.update(
        {
            "FORMAL_PG_URL": pg_url(kind),
            "FORMAL_PG_USER": "postgres",
            "FORMAL_PG_PASSWORD": runtime_pg_password(kind),
            "FORMAL_PG_SQL_IDS": ",".join(query_ids),
            "FORMAL_PG_SQL_FILES": ",".join(str(path) for path in query_paths),
            "FORMAL_PG_SQL_WEIGHTS": ",".join(str(value) for value in query_weights),
        }
    )
    process = _run_workload_command(
        command,
        cwd=Path(__file__).resolve().parent,
        environment=environment,
        stdout_path=stdout_path,
        duration_seconds=duration_seconds,
        online_stability=(
            {
                **dict(online_stability),
                "query_log_path": result_log,
                "transaction_log_path": None,
            }
            if online_stability is not None
            else None
        ),
    )
    return {
        "command": command,
        **process,
        "stdout_path": str(stdout_path),
        "stdout_sha256": sha256_file(stdout_path),
        "query_log_path": str(result_log),
        "query_log_sha256": sha256_file(result_log) if result_log.is_file() else None,
        "configured_query_ids": query_ids,
        "configured_query_files": [str(path) for path in query_paths],
        "configured_query_file_sha256": {
            query_id: sha256_file(path)
            for query_id, path in zip(query_ids, query_paths)
        },
        "configured_query_weights": query_weights,
        "configured_terminals": terminals,
    }


def execute_workload(
    workload: str,
    duration_seconds: int,
    output_dir: Path,
    phase: str,
    *,
    online_stability: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    if workload in {"hybench_ap", "hybench_xp"}:
        return execute_hybench(
            workload,
            duration_seconds,
            output_dir,
            phase,
            online_stability=online_stability,
        )
    return execute_sql_loop(
        workload,
        duration_seconds,
        output_dir,
        phase,
        online_stability=online_stability,
    )


def ap10_sql() -> str:
    with HYBENCH_STMTS.open("rb") as handle:
        value = tomllib.load(handle)
    sql = value["AP-10"]["sql"]
    if not isinstance(sql, str) or not sql.strip():
        raise FormalPgError("frozen HyBench AP-10 SQL is missing")
    return sql


def hybench_ap_sql_sha256_by_id(query_ids: Sequence[int]) -> Dict[int, str]:
    """Return the exact audit-driver SQL hash for each frozen HyBench AP id."""
    with HYBENCH_STMTS.open("rb") as handle:
        statements = tomllib.load(handle)
    hashes: Dict[int, str] = {}
    for query_id in query_ids:
        entry = statements.get(f"AP-{int(query_id)}")
        sql = entry.get("sql") if isinstance(entry, dict) else None
        if not isinstance(sql, str) or not sql.strip():
            raise FormalPgError(f"frozen HyBench AP-{query_id} SQL is missing")
        hashes[int(query_id)] = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    if len(set(hashes.values())) != len(hashes):
        raise FormalPgError("frozen HyBench AP SQL hashes are not unique")
    return hashes


def explain_sql(workload: str) -> str:
    if workload in {"hybench_ap", "hybench_xp"}:
        return ap10_sql()
    return sql_for_workload(workload).read_text(encoding="utf-8")


def structural_plan(value: Any) -> Any:
    if isinstance(value, list):
        return [structural_plan(item) for item in value]
    if not isinstance(value, dict):
        return value
    keep = (
        "Node Type",
        "Parent Relationship",
        "Parallel Aware",
        "Async Capable",
        "Join Type",
        "Relation Name",
        "Alias",
        "Index Name",
        "Scan Direction",
        "Workers Planned",
        "Strategy",
        "Partial Mode",
        "Subplan Name",
    )
    output = {key: value[key] for key in keep if key in value}
    if "Plans" in value:
        output["Plans"] = structural_plan(value["Plans"])
    return output


def parse_explain_json_output(raw: str) -> Any:
    """Parse psql JSON while tolerating command tags from a preceding SET."""
    start = raw.find("[")
    if start < 0:
        raise FormalPgError("EXPLAIN output does not contain a JSON array")
    return json.loads(raw[start:])


def capture_plan(kind: str, workload: str, output_dir: Path) -> Dict[str, Any]:
    name, _ = container_spec(kind)
    sql = explain_sql(workload).strip().rstrip(";")
    command = [
        "docker",
        "exec",
        "-u",
        "postgres",
        name,
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-A",
        "-t",
        "-d",
        TARGET_DATABASE[kind],
        "-c",
        "SET statement_timeout='600s'; "
        f"EXPLAIN (FORMAT JSON, COSTS TRUE, VERBOSE TRUE, SETTINGS TRUE) {sql}",
    ]
    began = time.monotonic()
    try:
        process = run(command, check=False, timeout=620)
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "timeout",
            "wall_seconds": time.monotonic() - began,
            "timeout_seconds": exc.timeout,
            "completion_within_600_seconds": False,
            "outside_main_measurement": True,
            "non_executing": True,
        }
    if process.returncode != 0:
        return {
            "status": "query_failed_or_statement_timeout",
            "exit_code": process.returncode,
            "wall_seconds": time.monotonic() - began,
            "stdout_tail": (process.stdout or "")[-4000:],
            "stderr_tail": (process.stderr or "")[-4000:],
            "completion_within_600_seconds": False,
            "outside_main_measurement": True,
            "non_executing": True,
        }
    raw = process.stdout.strip()
    plan = parse_explain_json_output(raw)
    structural = structural_plan(plan[0]["Plan"])
    canonical = json.dumps(structural, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    path = output_dir / "plan.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    structural_path = output_dir / "plan_structural.json"
    structural_path.write_text(
        json.dumps(structural, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    workers_planned = collect_key(plan, "Workers Planned")
    return {
        "status": "complete",
        "path": str(path),
        "sha256": sha256_file(path),
        "structural_path": str(structural_path),
        "structural_sha256": sha256_file(structural_path),
        "structural_fingerprint": fingerprint,
        "workers_planned": workers_planned,
        "workers_launched": [],
        "completion_within_600_seconds": time.monotonic() - began <= 600.0,
        "wall_seconds": time.monotonic() - began,
        "outside_main_measurement": True,
        "non_executing": True,
    }


def capture_execution_plan(kind: str, workload: str, output_dir: Path) -> Dict[str, Any]:
    name, _ = container_spec(kind)
    sql = explain_sql(workload).strip().rstrip(";")
    execute_plan = workload != "hybench_xp"
    explain_options = (
        "ANALYZE TRUE, BUFFERS TRUE" if execute_plan
        else "ANALYZE FALSE, BUFFERS FALSE"
    )
    command = [
        "docker",
        "exec",
        "-u",
        "postgres",
        name,
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-A",
        "-t",
        "-d",
        TARGET_DATABASE[kind],
        "-c",
        "SET statement_timeout='600s'; "
        f"EXPLAIN ({explain_options}, FORMAT JSON, SETTINGS TRUE) {sql}",
    ]
    began = time.monotonic()
    path = output_dir / "execution_plan.json"
    try:
        process = run(command, check=False, timeout=620)
        if process.returncode != 0:
            return {
                "status": "query_failed_or_statement_timeout",
                "exit_code": process.returncode,
                "wall_seconds": time.monotonic() - began,
                "stdout_tail": (process.stdout or "")[-4000:],
                "stderr_tail": (process.stderr or "")[-4000:],
                "completion_within_600_seconds": False,
                "outside_main_measurement": True,
            }
        raw = process.stdout.strip()
        plan = parse_explain_json_output(raw)
        path.write_text(
            json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        execution_time_ms = plan[0].get("Execution Time")
        return {
            "status": "complete",
            "path": str(path),
            "sha256": sha256_file(path),
            "wall_seconds": time.monotonic() - began,
            "execution_time_ms": execution_time_ms,
            "workers_planned": collect_key(plan, "Workers Planned"),
            "workers_launched": collect_key(plan, "Workers Launched"),
            "completion_within_600_seconds": (
                float(execution_time_ms) <= 600_000.0
                if execution_time_ms is not None
                else time.monotonic() - began <= 600.0
            ),
            "outside_main_measurement": True,
            "non_executing": not execute_plan,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "timeout",
            "wall_seconds": time.monotonic() - began,
            "timeout_seconds": exc.timeout,
            "completion_within_600_seconds": False,
            "outside_main_measurement": True,
        }


def collect_key(value: Any, key: str) -> List[Any]:
    values: List[Any] = []
    if isinstance(value, dict):
        if key in value:
            values.append(value[key])
        for child in value.values():
            values.extend(collect_key(child, key))
    elif isinstance(value, list):
        for child in value:
            values.extend(collect_key(child, key))
    return values


def parse_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows = []
    if not path.is_file():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def atomic_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    """Persist deterministic derived evidence without altering raw counter logs."""
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def percentile(values: Sequence[float], quantile: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - index) + ordered[upper] * (index - lower)


def cv(values: Sequence[float]) -> float:
    if not values:
        return math.inf
    mean = statistics.fmean(values)
    if mean == 0:
        return 0.0 if max(values) == min(values) else math.inf
    return statistics.pstdev(values) / abs(mean)


def derive_resource_samples(rows: Sequence[Mapping[str, Any]], scenario: str) -> List[Dict[str, float]]:
    profile = machine_config()["resource_profiles"][scenario]
    io_contract = load_io_utilization_contract()
    values = []
    for previous, current in zip(rows, rows[1:]):
        seconds = (int(current["monotonic_ns"]) - int(previous["monotonic_ns"])) / 1e9
        if seconds <= 0:
            continue
        read_bps = (int(current["io_read_bytes"]) - int(previous["io_read_bytes"])) / seconds
        write_bps = (int(current["io_write_bytes"]) - int(previous["io_write_bytes"])) / seconds
        if profile["io_read_bps"] is None:
            io_utilization = (
                int(current["device_io_time_ms"]) - int(previous["device_io_time_ms"])
            ) / (seconds * 1000.0)
            io_utilization_definition = "device_busy_time_fraction"
        else:
            read_cap_bps = float(profile["io_read_bps"])
            write_cap_bps = float(profile["io_write_bps"])
            total_cap_bps = read_cap_bps + write_cap_bps
            if total_cap_bps <= 0.0:
                raise FormalPgError("configured total I/O cap must be positive")
            io_utilization = (
                read_bps + write_bps
            ) / total_cap_bps
            io_utilization_definition = str(io_contract["formula"])
        cpu_raw = (
            (int(current["cpu_usage_ns"]) - int(previous["cpu_usage_ns"])) / 1e9
        ) / seconds / float(profile["cpu_quota_cores"])
        memory_working_set = int(
            current.get("memory_working_set_bytes", current["memory_current_bytes"])
        )
        memory_raw = memory_working_set / float(profile["memory_bytes"])
        memory_current_raw = int(current["memory_current_bytes"]) / float(profile["memory_bytes"])
        raw_utilizations = {
            "cpu": cpu_raw,
            "memory": memory_raw,
            "io": io_utilization,
        }
        invalid_raw = {
            name: value
            for name, value in raw_utilizations.items()
            if not math.isfinite(value) or value < 0.0
        }
        if invalid_raw:
            raise FormalPgError(
                "resource utilization counters must be finite and non-negative: "
                f"{invalid_raw}"
            )
        values.append(
            {
                "start_epoch": dt.datetime.fromisoformat(
                    str(previous["timestamp_utc"]).replace("Z", "+00:00")
                ).timestamp(),
                "end_epoch": dt.datetime.fromisoformat(
                    str(current["timestamp_utc"]).replace("Z", "+00:00")
                ).timestamp(),
                "seconds": seconds,
                "cpu_utilization": cpu_raw,
                "memory_utilization": memory_raw,
                "io_utilization": io_utilization,
                "cpu_utilization_raw": cpu_raw,
                "memory_utilization_raw": memory_raw,
                "memory_current_utilization_raw": memory_current_raw,
                "memory_working_set_bytes": float(memory_working_set),
                "memory_utilization_definition": "(usage_in_bytes-total_inactive_file)/limit_in_bytes",
                "io_utilization_raw": io_utilization,
                "io_utilization_definition": io_utilization_definition,
                "read_cap_bps": (
                    float(profile["io_read_bps"])
                    if profile["io_read_bps"] is not None
                    else 0.0
                ),
                "write_cap_bps": (
                    float(profile["io_write_bps"])
                    if profile["io_write_bps"] is not None
                    else 0.0
                ),
                "io_total_cap_bps": (
                    float(profile["io_read_bps"] + profile["io_write_bps"])
                    if profile["io_read_bps"] is not None
                    else 0.0
                ),
                "read_bps": read_bps,
                "write_bps": write_bps,
                "pg_xact_commit_delta": float(
                    int(current["pg_xact_commit"]) - int(previous["pg_xact_commit"])
                ),
                "pg_xact_rollback_delta": float(
                    int(current["pg_xact_rollback"]) - int(previous["pg_xact_rollback"])
                ),
            }
        )
    return values


def resource_stability(
    samples: Sequence[Mapping[str, float]],
    workload: str,
    *,
    measurement_start_epoch_seconds: Optional[float] = None,
    now_epoch_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    protocol = FrozenMeasurementProtocol.from_frozen_bundle(SHARED_CONFIG)
    if not samples:
        return {
            "passed": False,
            "stable": False,
            "reason": "no_resource_samples",
            "windows": [],
        }
    start_epoch = (
        float(measurement_start_epoch_seconds)
        if measurement_start_epoch_seconds is not None
        else float(samples[0]["start_epoch"])
    )
    end_epoch = (
        float(now_epoch_seconds)
        if now_epoch_seconds is not None
        else float(samples[-1]["end_epoch"])
    )
    aligned = []
    for sample in samples:
        row = dict(sample)
        row["epoch_seconds"] = (
            float(sample["start_epoch"]) + float(sample["end_epoch"])
        ) / 2.0
        aligned.append(row)
    assessment = assess_measurement_samples(
        aligned,
        measurement_start_epoch_seconds=start_epoch,
        now_epoch_seconds=end_epoch,
        xp=workload == "hybench_xp",
        protocol=protocol,
    ).to_dict()
    assessment["passed"] = assessment["stable"]
    assessment["source"] = "formal_measurement.assess_measurement_samples"
    return assessment


def summarize_measurement(
    workload: str,
    scenario: str,
    output_dir: Path,
    execution: Mapping[str, Any],
    sampler_path: Path,
    requested_seconds: int,
) -> Dict[str, Any]:
    online_stop = execution.get("online_stability_stop") or {}
    cutoff_value = online_stop.get("measurement_cutoff_epoch")
    cutoff_epoch = float(cutoff_value) if cutoff_value is not None else None
    measurement_start_value = execution.get("measurement_started_epoch")
    measurement_start_epoch = (
        float(measurement_start_value)
        if measurement_start_value is not None
        else None
    )
    all_query_rows = [
        row
        for row in parse_jsonl(Path(str(execution["query_log_path"])))
        if row.get("event") == "query_complete"
    ]
    query_rows = [
        row
        for row in all_query_rows
        if cutoff_epoch is None or _event_epoch(row) <= cutoff_epoch
    ]
    successes = [row for row in query_rows if row.get("success") is True]
    failures = [row for row in query_rows if row.get("success") is not True]
    latencies = [float(row["latency_ms"]) for row in successes]
    signatures_by_query: Dict[str, set[Tuple[Any, Any]]] = {}
    observed_queries: Dict[str, int] = {}
    for row in successes:
        query_key = str(
            row.get("query_id")
            or row.get("sql_sha256")
            or "single_query_without_explicit_id"
        )
        signatures_by_query.setdefault(query_key, set()).add(
            (row.get("row_count"), row.get("result_sha256"))
        )
        observed_queries[query_key] = observed_queries.get(query_key, 0) + 1
    digests = {
        signature
        for query_signatures in signatures_by_query.values()
        for signature in query_signatures
    }
    transaction_path_value = execution.get("transaction_log_path")
    all_transaction_rows = (
        [
            row
            for row in parse_jsonl(Path(str(transaction_path_value)))
            if row.get("event") == "transaction_complete"
        ]
        if transaction_path_value
        else []
    )
    transaction_rows = [
        row
        for row in all_transaction_rows
        if cutoff_epoch is None or _event_epoch(row) <= cutoff_epoch
    ]
    successful_transactions = [
        row
        for row in transaction_rows
        if row.get("at_type") in {f"AT{index}" for index in range(1, 7)}
        and row.get("outcome") in {"commit", "rollback"}
        and int(row.get("error_count") or 0) == 0
    ]
    resource_samples = _stability_samples_from_evidence(
        workload,
        scenario,
        sampler_path,
        Path(str(execution["query_log_path"])),
        Path(str(transaction_path_value)) if transaction_path_value else None,
        cutoff_epoch=cutoff_epoch,
    )
    derived_resource_path = output_dir / "measurement_derived_resource_samples.jsonl"
    if derived_resource_path.exists() or derived_resource_path.is_symlink():
        raise FormalPgError(
            f"derived resource evidence already exists: {derived_resource_path}"
        )
    atomic_jsonl(derived_resource_path, resource_samples)
    stability = resource_stability(
        resource_samples,
        workload,
        measurement_start_epoch_seconds=measurement_start_epoch,
        now_epoch_seconds=cutoff_epoch,
    )
    coverage = float(stability.get("aggregate_sample_coverage") or 0.0)
    protocol = FrozenMeasurementProtocol.from_frozen_bundle(SHARED_CONFIG)
    effective_seconds = int(online_stop.get("effective_window_seconds") or 0)
    if effective_seconds <= 0:
        effective_seconds = int(stability.get("complete_window_count") or 0) * int(
            protocol.stability_window_seconds
        )
    if effective_seconds <= 0:
        effective_seconds = max(1, int(float(execution["wall_seconds"])))
    means = {
        resource: statistics.fmean(
            float(row[f"{resource}_utilization"]) for row in resource_samples
        )
        if resource_samples
        else None
        for resource in ("cpu", "memory", "io")
    }
    transaction_failures = [
        row
        for row in transaction_rows
        if row.get("outcome") == "error" or int(row.get("error_count") or 0) > 0
    ]
    error_rate = (len(failures) + len(transaction_failures)) / max(
        1, len(query_rows) + len(transaction_rows)
    )
    parameterized_hybench_ap = workload == "hybench_ap"
    expected_hybench_query_count = (
        len(
            read_json(SHARED_CONFIG / "benchmark_matrix.json")["workloads"]
            ["hybench_ap"]["ap_query_ids"]
        )
        if parameterized_hybench_ap
        else None
    )
    correctness_passed = bool(successes) and not failures
    correctness_rule = "deterministic_signature_per_query"
    if parameterized_hybench_ap:
        hybench_stdout = Path(str(execution["stdout_path"])).read_text(
            encoding="utf-8", errors="replace"
        )
        stdout_ap_query_ids = sorted(
            {
                int(value)
                for value in re.findall(r"^AP Query\s+([0-9]+)\s*:", hybench_stdout, re.MULTILINE)
            }
        )
        expected_ap_query_ids = sorted(
            int(value)
            for value in read_json(SHARED_CONFIG / "benchmark_matrix.json")["workloads"]
            ["hybench_ap"]["ap_query_ids"]
        )
        expected_ap_hashes = hybench_ap_sql_sha256_by_id(expected_ap_query_ids)
        ap_id_by_hash = {
            sql_sha256: query_id
            for query_id, sql_sha256 in expected_ap_hashes.items()
        }
        observed_success_hashes = {
            str(row.get("sql_sha256"))
            for row in successes
            if row.get("sql_sha256")
        }
        observed_ap_query_ids = sorted(
            ap_id_by_hash[sql_sha256]
            for sql_sha256 in observed_success_hashes
            if sql_sha256 in ap_id_by_hash
        )
        unknown_ap_sql_sha256 = sorted(
            observed_success_hashes - set(ap_id_by_hash)
        )
        hybench_exception_count = len(
            re.findall(r"(?:PSQLException|\bException\b)", hybench_stdout)
        )
        correctness_passed = bool(
            execution["exit_code"] == 0
            and successes
            and not failures
            and observed_ap_query_ids == expected_ap_query_ids
            and not unknown_ap_sql_sha256
            and hybench_exception_count == 0
        )
        correctness_rule = (
            "hybench_ap_structured_sql_hash_all_query_ids_and_no_client_exceptions"
        )
    else:
        correctness_passed = correctness_passed and all(
            len(values) == 1 for values in signatures_by_query.values()
        )
    summary = {
        "requested_seconds": requested_seconds,
        "planned_max_seconds": int(
            online_stop.get("planned_max_seconds") or requested_seconds
        ),
        "effective_measurement_seconds": effective_seconds,
        "measurement_cutoff_epoch": cutoff_epoch,
        "measurement_termination_reason": online_stop.get("termination_reason"),
        "actual_workload_wall_seconds": execution["wall_seconds"],
        "sample_count": len(resource_samples),
        "sample_coverage": coverage,
        "resource_mean_utilization": means,
        "resource_stability": stability,
        "query_count": len(successes),
        "qps": len(successes) / float(effective_seconds),
        "latency_ms": {
            "mean": statistics.fmean(latencies) if latencies else None,
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
            "max": max(latencies) if latencies else None,
        },
        "result_row_count_and_sha256": [list(value) for value in sorted(digests, key=str)],
        "result_signatures_by_query": {
            query_key: [list(value) for value in sorted(values, key=str)]
            for query_key, values in sorted(signatures_by_query.items())
        },
        "observed_query_counts": dict(sorted(observed_queries.items())),
        "correctness_passed": correctness_passed,
        "correctness_rule": correctness_rule,
        "expected_hybench_query_type_count": expected_hybench_query_count,
        "error_count": len(failures) + len(transaction_failures),
        "error_rate": error_rate,
        "exit_code": execution["exit_code"],
        "raw_returncode": execution.get("raw_returncode"),
        "intentional_online_stop": execution.get("intentional_online_stop") is True,
        "cutoff_tail_events_excluded": {
            "query_complete": len(all_query_rows) - len(query_rows),
            "transaction_complete": len(all_transaction_rows) - len(transaction_rows),
        },
        "raw_resource_samples_path": str(sampler_path),
        "raw_resource_samples_sha256": sha256_file(sampler_path),
        "derived_resource_samples_path": str(derived_resource_path),
        "derived_resource_samples_sha256": sha256_file(derived_resource_path),
        "resource_denominators": machine_config()["resource_profiles"][scenario],
    }
    if parameterized_hybench_ap:
        summary["observed_hybench_ap_query_ids"] = observed_ap_query_ids
        summary["expected_hybench_ap_query_ids"] = expected_ap_query_ids
        summary["stdout_hybench_ap_query_ids"] = stdout_ap_query_ids
        summary["expected_hybench_ap_sql_sha256_by_id"] = {
            str(query_id): sql_sha256
            for query_id, sql_sha256 in expected_ap_hashes.items()
        }
        summary["unknown_hybench_ap_sql_sha256"] = unknown_ap_sql_sha256
        summary["hybench_client_exception_count"] = hybench_exception_count
    if workload == "hybench_xp":
        text = Path(str(execution["stdout_path"])).read_text(encoding="utf-8", errors="replace")
        client_exception_matches = re.findall(
            r"(?:ERROR com\.hybench\.workload\.Client|NullPointerException|"
            r"ExecutionException|PSQLException|\bException\b)",
            text,
        )
        client_exception_count = len(client_exception_matches)
        xap = re.findall(r"XP-QPS is ([0-9.]+)|Fresh-XP-QPS is ([0-9.]+)", text)
        xtp = re.findall(r"XP-TPS is ([0-9.]+)|Fresh-XP-TPS is ([0-9.]+)", text)
        xap_value = float(next(value for pair in xap[-1:] for value in pair if value)) if xap else None
        xtp_value = float(next(value for pair in xtp[-1:] for value in pair if value)) if xtp else None
        metric_matches = re.findall(
            r'HYBENCH_METRICS_JSON=\{"qps":([0-9.eE+\-]+),"query_count":([0-9]+)\}',
            text,
        )
        combined_metric = (
            {
                "qps": float(metric_matches[-1][0]),
                "query_count": int(metric_matches[-1][1]),
            }
            if metric_matches
            else None
        )
        metric_source = "hybench_individual_stream_summary"
        if (
            execution.get("intentional_online_stop") is True
            or xap_value is None
            or xtp_value is None
        ):
            # The exact frozen JAR prints only a combined JSON metric for XP.
            # Preserve it for cross-checking and derive each primary stream from
            # the exact AP result log and pass-through AT transaction audit.
            xap_value = len(successes) / float(effective_seconds)
            xtp_value = len(successful_transactions) / float(effective_seconds)
            metric_source = "cutoff_exact_ap_result_log_and_transaction_audit"
        at_total_matches = re.findall(
            r"Total amount of XP-AT Transaction is ([0-9]+)", text
        )
        reported_at_total = int(at_total_matches[-1]) if at_total_matches else None
        at_counts = {
            f"AT{index}": {"commit": 0, "rollback": 0, "error": 0}
            for index in range(1, 7)
        }
        unknown_counts = {"commit": 0, "rollback": 0, "error": 0}
        for row in transaction_rows:
            target = at_counts.get(str(row.get("at_type")), unknown_counts)
            outcome = str(row.get("outcome"))
            if outcome not in target:
                outcome = "error"
            target[outcome] += 1
            if int(row.get("error_count") or 0) > 0 and outcome != "error":
                target["error"] += int(row["error_count"])
        known_success_total = sum(
            counts["commit"] + counts["rollback"] for counts in at_counts.values()
        )
        transaction_correctness = {
            "required_at_types": [f"AT{index}" for index in range(1, 7)],
            "counts": at_counts,
            "unknown_counts": unknown_counts,
            "reported_hybench_at_total": reported_at_total,
            "audited_known_commit_or_rollback_total": known_success_total,
            "reported_total_matches_audited": reported_at_total == known_success_total,
            "all_at_types_observed": all(
                counts["commit"] + counts["rollback"] > 0
                for counts in at_counts.values()
            ),
            "transaction_error_count": len(transaction_failures),
            "audit_log_path": str(transaction_path_value),
            "audit_log_sha256": (
                sha256_file(Path(str(transaction_path_value)))
                if transaction_path_value and Path(str(transaction_path_value)).is_file()
                else None
            ),
            "hybench_combined_metric": combined_metric,
        }
        summary["xap_qps"] = xap_value
        summary["xtp_tps"] = xtp_value
        summary["xp_metric_source"] = metric_source
        summary["transaction_correctness"] = transaction_correctness
        summary["hybench_client_exception_count"] = client_exception_count
        summary["correctness_rule"] = (
            "hybench_xp_all_at_types_and_no_query_transaction_or_client_exceptions"
        )
        summary["error_count"] = (
            len(failures) + len(transaction_failures) + client_exception_count
        )
        summary["error_rate"] = summary["error_count"] / max(
            1, len(query_rows) + len(transaction_rows) + client_exception_count
        )
        summary["correctness_passed"] = bool(
            execution["exit_code"] == 0
            and xap_value is not None
            and xap_value > 0
            and xtp_value is not None
            and xtp_value > 0
            and transaction_correctness["all_at_types_observed"]
            and not failures
            and not transaction_failures
            and client_exception_count == 0
        )
    path = output_dir / "measurement_summary.json"
    atomic_json(path, summary)
    return summary


def readback(kind: str, profile: str, costs: Mapping[str, float], output_dir: Path) -> Dict[str, Any]:
    name, _ = container_spec(kind)
    inspect = json.loads(run(["docker", "inspect", name]).stdout)[0]
    image_inspect = json.loads(
        run(["docker", "image", "inspect", inspect["Image"]]).stdout
    )[0]
    host = inspect["HostConfig"]
    requested = machine_config()["resource_profiles"][profile]
    database = TARGET_DATABASE[kind]
    settings = read_json(SHARED_CONFIG / "fixed_db_settings.json")["postgresql"]
    expected_settings: Dict[str, Any] = dict(settings["all_profiles"])
    expected_settings.update(settings[profile])
    expected_settings.update(costs)
    expected_settings["statement_timeout"] = "0"
    names = sorted(expected_settings)
    sql = "select name||E'\\t'||current_setting(name)||E'\\t'||coalesce(unit,'') from pg_settings where name in (" + ",".join(
        "'" + name.replace("'", "''") + "'" for name in names
    ) + ") order by name"
    raw = run(
        [
            "docker",
            "exec",
            "-u",
            "postgres",
            name,
            "psql",
            "-A",
            "-t",
            "-d",
            database,
            "-c",
            sql,
        ]
    ).stdout
    setting_rows = {}
    for line in raw.splitlines():
        if not line:
            continue
        fields = line.split("\t")
        if len(fields) != 3:
            raise FormalPgError(f"invalid PostgreSQL settings readback row: {line!r}")
        setting, value, unit = fields
        setting_rows[setting] = {"setting": value, "unit": unit or None}
    host_swap_disabled = not any(
        line.split()[0] != "Filename"
        for line in Path("/proc/swaps").read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    docker_info = run(["docker", "info"], check=False)
    no_swap_limit_support = "No swap limit support" in (
        (docker_info.stdout or "") + (docker_info.stderr or "")
    )
    fixed_checks: Dict[str, Any] = {}
    size_names = {"shared_buffers", "work_mem", "maintenance_work_mem", "effective_cache_size"}
    boolean_names = {
        "parallel_leader_participation",
        "jit",
        "track_io_timing",
        "fsync",
        "synchronous_commit",
        "full_page_writes",
    }
    for setting, expected in expected_settings.items():
        actual = setting_rows.get(setting, {}).get("setting")
        if actual is None:
            passed = False
        elif setting in size_names:
            def parse_size(value: str) -> int:
                match = re.fullmatch(r"([0-9.]+)\s*([A-Za-z]+)", value)
                if not match:
                    raise FormalPgError(f"cannot parse PostgreSQL size setting: {value}")
                factors = {
                    "B": 1,
                    "kB": 1024,
                    "MB": 1024**2,
                    "GB": 1024**3,
                    "TB": 1024**4,
                }
                return round(float(match.group(1)) * factors[match.group(2)])
            passed = parse_size(str(actual)) == parse_size(str(expected))
        elif setting in boolean_names:
            expected_bool = str(expected).lower() in {"on", "true", "1"}
            passed = (str(actual).lower() in {"on", "true", "1"}) == expected_bool
        elif setting in COST_PARAMETERS:
            # PostgreSQL serializes real-valued GUCs with roughly six
            # significant decimal digits (for example, 1.414213562... is
            # read back as 1.41421). Compare at that representation precision.
            passed = math.isclose(
                float(actual),
                float(expected),
                rel_tol=5e-6,
                abs_tol=5e-9,
            )
        elif isinstance(expected, int):
            passed = int(actual) == expected
        else:
            passed = str(actual).lower() == str(expected).lower()
        fixed_checks[setting] = {"expected": expected, "actual": actual, "passed": passed}

    resource_checks = {
        "cpu_quota_cores": {
            "expected": requested["cpu_quota_cores"],
            "actual": host["NanoCpus"] / 1e9,
            "passed": host["NanoCpus"] == int(requested["cpu_quota_cores"] * 1e9),
        },
        "cpuset_cpus": {
            "expected": requested["cpuset_cpus"],
            "actual": host["CpusetCpus"],
            "passed": host["CpusetCpus"] == requested["cpuset_cpus"],
        },
        "cpuset_mems": {
            "expected": requested["cpuset_mems"],
            "actual": host["CpusetMems"],
            "passed": host["CpusetMems"] == requested["cpuset_mems"],
        },
        "memory_bytes": {
            "expected": requested["memory_bytes"],
            "actual": host["Memory"],
            "passed": host["Memory"] == requested["memory_bytes"],
        },
        "memory_swap_semantics": {
            "expected": "container swap disabled",
            "requested_memory_swap_bytes": requested["memory_swap_bytes"],
            "docker_inspect": host["MemorySwap"],
            "host_swap_disabled": host_swap_disabled,
            "docker_no_swap_limit_support": no_swap_limit_support,
            # Docker disables container swap when --memory-swap equals
            # --memory, even when the host itself has a swap device.
            "passed": (
                requested["memory_swap_bytes"] == requested["memory_bytes"]
                and host["MemorySwap"] == host["Memory"]
            ) or (
                host_swap_disabled
                and host["MemorySwap"] == -1
                and no_swap_limit_support
            ),
        },
        "pids_limit": {
            "expected": requested["pids_limit"],
            "actual": host["PidsLimit"],
            "passed": host["PidsLimit"] == requested["pids_limit"],
        },
        "network_mode": {
            "expected": requested["network_mode"],
            "actual": host["NetworkMode"],
            "passed": host["NetworkMode"] == requested["network_mode"],
        },
    }
    mount_source = run(
        [
            "findmnt",
            "-T",
            machine_config()["engines"]["postgresql"]["mount_root"],
            "-n",
            "-o",
            "SOURCE",
        ]
    ).stdout.strip()
    expected_read = requested["io_read_bps"]
    expected_write = requested["io_write_bps"]
    docker_observed_read = {
        row["Path"]: row["Rate"] for row in (host.get("BlkioDeviceReadBps") or [])
    }
    docker_observed_write = {
        row["Path"]: row["Rate"] for row in (host.get("BlkioDeviceWriteBps") or [])
    }
    blkio_cgroup = cgroup_directory("blkio", inspect["Id"])
    observed_read = parse_throttle_bps(blkio_cgroup / "blkio.throttle.read_bps_device")
    observed_write = parse_throttle_bps(blkio_cgroup / "blkio.throttle.write_bps_device")
    device = str(machine_config()["engines"]["postgresql"]["observed_device"])
    resource_checks["io_read_bps"] = {
        "expected": expected_read,
        "actual": observed_read,
        "docker_inspect_request_metadata": docker_observed_read,
        "resolved_device": mount_source,
        "passed": (
            observed_read == {}
            if expected_read is None
            else observed_read == {device: expected_read}
        ),
    }
    resource_checks["io_write_bps"] = {
        "expected": expected_write,
        "actual": observed_write,
        "docker_inspect_request_metadata": docker_observed_write,
        "resolved_device": mount_source,
        "passed": (
            observed_write == {}
            if expected_write is None
            else observed_write == {device: expected_write}
        ),
    }
    resource_checks["runtime_shm_size_bytes"] = {
        "expected_support_mount_size": RUNTIME_SHM_SIZE_BYTES,
        "actual": host["ShmSize"],
        "charged_inside_memory_cgroup": True,
        "passed": host["ShmSize"] == RUNTIME_SHM_SIZE_BYTES,
    }
    resource_checks["image_digest"] = {
        "expected": machine_config()["images"]["postgresql"]["digest"],
        "actual_image_id": image_inspect.get("Id"),
        "actual_repo_digests": image_inspect.get("RepoDigests") or [],
        "passed": any(
            digest.endswith(
                "@" + machine_config()["images"]["postgresql"]["digest"]
            )
            for digest in (image_inspect.get("RepoDigests") or [])
        ),
    }
    output = {
        "docker": {
            "container_id": inspect["Id"],
            "image": inspect["Config"]["Image"],
            "nano_cpus": host["NanoCpus"],
            "cpuset_cpus": host["CpusetCpus"],
            "cpuset_mems": host["CpusetMems"],
            "memory_bytes": host["Memory"],
            "memory_swap_inspect": host["MemorySwap"],
            "pids_limit": host["PidsLimit"],
            "network_mode": host["NetworkMode"],
            "device_read_bps": host.get("BlkioDeviceReadBps") or [],
            "device_write_bps": host.get("BlkioDeviceWriteBps") or [],
            "oom_killed": inspect["State"]["OOMKilled"],
            "restart_count": inspect["RestartCount"],
        },
        "requested_resource_profile": requested,
        "postgresql_settings": setting_rows,
        "expected_fixed_and_cost_settings": expected_settings,
        "host_swap_disabled": host_swap_disabled,
        "docker_no_swap_limit_support": no_swap_limit_support,
        "fixed_setting_checks": fixed_checks,
        "resource_checks": resource_checks,
        "fixed_settings_passed": all(value["passed"] for value in fixed_checks.values()),
        "resource_profile_passed": all(value["passed"] for value in resource_checks.values()),
    }
    path = output_dir / "readback.json"
    atomic_json(path, output)
    return output


def validate_cost_parameters(costs: Mapping[str, float]) -> Dict[str, float]:
    """Validate one live PostgreSQL cost point against the frozen search space."""
    if set(costs) != set(COST_PARAMETERS):
        raise FormalPgError(
            "cost point must contain exactly the seven frozen PostgreSQL cost parameters"
        )
    search = read_json(SHARED_CONFIG / "search_spaces.json")["postgresql"]["parameters"]
    validated: Dict[str, float] = {}
    for name in COST_PARAMETERS:
        raw = costs[name]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise FormalPgError(f"cost parameter is not numeric: {name}")
        value = float(raw)
        if not math.isfinite(value):
            raise FormalPgError(f"cost parameter is not finite: {name}")
        lower = float(search[name]["min"])
        upper = float(search[name]["max"])
        if value < lower or value > upper:
            raise FormalPgError(
                f"cost parameter is outside the frozen range: {name}={value}"
            )
        validated[name] = value
    return validated


def run_cost_point(
    case_id: str,
    point_id: str,
    repeat_id: int,
    output_dir: Path,
    costs: Mapping[str, float],
    *,
    evidence_context: Optional[Mapping[str, Any]] = None,
    host_lock_already_held: bool = False,
    warmup_seconds: Optional[int] = None,
    measurement_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    """Execute one arbitrary in-range cost point from a fresh frozen template."""
    validate_frozen_inputs()
    cases = case_map()
    if case_id not in cases:
        raise FormalPgError(f"unknown PostgreSQL case_id: {case_id}")
    if not isinstance(point_id, str) or not point_id:
        raise FormalPgError("point_id must be a non-empty string")
    if type(repeat_id) is not int or repeat_id <= 0:
        raise FormalPgError("repeat_id must be a positive integer")
    if output_dir.exists() or output_dir.is_symlink():
        raise FormalPgError(f"point output already exists: {output_dir}")
    output_dir.mkdir(parents=True, mode=0o700)
    case = cases[case_id]
    workload = str(case["workload"])
    profile = str(case["fixed_profile"])
    kind = workload_kind(workload)
    measurement = read_json(SHARED_CONFIG / "measurement_protocol.json")["timing"]
    warmup = int(
        measurement["warmup_seconds"]
        if warmup_seconds is None
        else warmup_seconds
    )
    duration = int(
        measurement["maximum_measurement_seconds"]
        if measurement_seconds is None
        else measurement_seconds
    )
    if warmup != int(measurement["warmup_seconds"]):
        raise FormalPgError("formal warmup must equal the frozen 60 seconds")
    if duration != int(measurement["maximum_measurement_seconds"]):
        raise FormalPgError(
            "formal measurement must launch with the frozen 600-second hard maximum; "
            "stable windows stop it online after no less than 180 seconds"
        )
    validated_costs = validate_cost_parameters(costs)
    token = f"{case_id}_{point_id}_r{repeat_id}_{os.getpid()}_{secrets.token_hex(6)}"
    started = utc_now()
    restore: Optional[Dict[str, Any]] = None
    restore_intent: Optional[Dict[str, Any]] = None
    cleanup: Optional[Dict[str, Any]] = None
    result: Dict[str, Any] = {
        "schema_version": 1,
        "freeze_id": FREEZE_ID,
        "global_campaign_id": CAMPAIGN_ID,
        "engine": "postgresql",
        "case_id": case_id,
        "point_id": point_id,
        "repeat_id": repeat_id,
        "status": "running",
        "started_at_utc": started,
        "workload": workload,
        "profile": profile,
        "cost_parameters": validated_costs,
    }
    if evidence_context is not None:
        result["evidence_context"] = dict(evidence_context)
    state_path = output_dir / "point_state.json"
    atomic_json(state_path, result)

    def checkpoint() -> None:
        atomic_json(state_path, result, exclusive=False)

    try:
        lock_context: Any = (
            contextlib.nullcontext({"status": "already_held_by_complete_run"})
            if host_lock_already_held
            else host_measurement_lock()
        )
        with lock_context as lock:
            result["host_lock"] = lock
            checkpoint()
            try:
                result["host_admission"] = wait_for_host_admission()
                checkpoint()
                intended_root = working_root() / "postgresql" / kind / token
                restore_intent = {
                    "restore_strategy": "physical_cp_reflink_never",
                    "runtime_root": str(intended_root),
                    "merged": str(intended_root / "pgdata"),
                    "created_for_point": point_id,
                }
                result["restore_intent"] = restore_intent
                checkpoint()
                restore = restore_overlay(kind, token)
                result["restore"] = restore
                checkpoint()
                container = start_container(
                    kind, Path(restore["merged"]), profile, validated_costs
                )
                result["container"] = container
                checkpoint()
                result["readback"] = readback(
                    kind, profile, validated_costs, output_dir
                )
                checkpoint()
                config = benchmark_config(workload, duration)
                config_path = output_dir / "benchmark_config.json"
                atomic_json(config_path, config)
                result["benchmark_config_path"] = str(config_path)
                result["benchmark_config_sha256"] = sha256_file(config_path)
                result["plan"] = capture_plan(kind, workload, output_dir)
                result["execution_plan"] = capture_execution_plan(kind, workload, output_dir)
                checkpoint()
                warmup_result = execute_workload(workload, warmup, output_dir, "warmup")
                result["warmup"] = warmup_result
                checkpoint()
                if warmup_result["exit_code"] != 0:
                    raise FormalPgError(f"warmup workload failed: exit {warmup_result['exit_code']}")
                if profile == "io":
                    result["measurement_cache_reset"] = reset_io_measurement_cache(
                        kind, profile, Path(str(restore["merged"]))
                    )
                    checkpoint()
                sampler_path = output_dir / "measurement_resource_samples.jsonl"
                stop = threading.Event()
                sampler = ResourceSampler(
                    container["container_id"], TARGET_DATABASE[kind], sampler_path, stop
                )
                sampler.start()
                try:
                    execution = execute_workload(
                        workload,
                        duration,
                        output_dir,
                        "measurement",
                        online_stability={
                            "workload": workload,
                            "scenario": profile,
                            "sampler_path": sampler_path,
                            "sampler": sampler,
                        },
                    )
                finally:
                    stop.set()
                    sampler.join(timeout=30)
                if sampler.is_alive() or sampler.error:
                    raise FormalPgError(
                        f"resource sampler failed: {sampler.error or 'did not stop'}"
                    )
                result["measurement_execution"] = execution
                result["measurement"] = summarize_measurement(
                    workload, profile, output_dir, execution, sampler_path, duration
                )
                checkpoint()
                inspect_after = json.loads(
                    run(["docker", "inspect", container["name"]]).stdout
                )[0]
                result["oom_killed"] = bool(inspect_after["State"]["OOMKilled"])
                result["database_restart_count"] = int(inspect_after["RestartCount"])
            finally:
                if restore is not None and cleanup is None:
                    cleanup = cleanup_overlay(kind, restore)
                    result["cleanup"] = cleanup
                    checkpoint()
                elif restore_intent is not None and cleanup is None:
                    intended_root = Path(str(restore_intent["runtime_root"]))
                    if intended_root.exists() or intended_root.is_symlink():
                        cleanup = cleanup_overlay(kind, restore_intent)
                        result["cleanup"] = cleanup
                        checkpoint()
        result["status"] = "complete"
        result["completed_at_utc"] = utc_now()
    except Exception as exc:
        result["status"] = "failed"
        result["failed_at_utc"] = utc_now()
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)
        atomic_json(state_path, result, exclusive=False)
        atomic_json(output_dir / "point_result.json", result)
        raise
    atomic_json(state_path, result, exclusive=False)
    atomic_json(output_dir / "point_result.json", result)
    return result


def run_point(
    case_id: str,
    point_id: str,
    repeat_id: int,
    output_dir: Path,
    *,
    warmup_seconds: Optional[int] = None,
    measurement_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    """Execute one exact P0/G0..G10 preflight point."""
    points = joint_path_points()
    if point_id not in points:
        raise FormalPgError(f"point is not in frozen P0/G0..G10 set: {point_id}")
    if repeat_id not in repeat_ids(point_id):
        raise FormalPgError(f"repeat is not frozen for {point_id}: {repeat_id}")
    return run_cost_point(
        case_id,
        point_id,
        repeat_id,
        output_dir,
        points[point_id],
        evidence_context={"purpose": "postgresql_engine_gate"},
        warmup_seconds=warmup_seconds,
        measurement_seconds=measurement_seconds,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--point-id", required=True)
    parser.add_argument("--repeat-id", required=True, type=int)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        value = run_point(args.case_id, args.point_id, args.repeat_id, args.output_dir)
        print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2), flush=True)
        return 0
    except (FormalPgError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FORMAL_PG_POINT_ERROR: {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

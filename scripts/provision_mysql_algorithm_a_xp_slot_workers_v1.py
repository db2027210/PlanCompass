#!/usr/bin/env python3
"""Provision new XP workers on positions released by completed memory scenes.

The functions in this module mutate Docker and /disk2 only when called by the
dynamic-slot executor after both memory scenes are terminal.  Importing the
module and building the successor freeze are side-effect free.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_a_dynamic_slots_v1 as slots
from scripts import reset_mysql_xp_data_planes_v5 as reset


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def build_successor_freeze(
    source: Mapping[str, Any], additions: Sequence[Mapping[str, Any]],
    memory_terminal_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    slots.validate_released_xp_workers(source, additions)
    original_xp = [
        copy.deepcopy(dict(row)) for row in source.get("workers") or []
        if row.get("scene") in slots.XP_SCENES
    ]
    if len(original_xp) != 4:
        raise RuntimeError("source freeze does not contain four original XP workers")
    value = copy.deepcopy(dict(source))
    value.update({
        "schema_version": "mysql_algorithm_a_xp_four_worker_resource_successor_v1",
        "release_version": "dynamic_slots_v1",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "formal_launch_authorized": True,
        "algorithm": "A",
        "campaign_scene_set": [
            "job_memory", "tpch_sf4_memory",
            "hybench_xp_cpu", "hybench_xp_io",
        ],
        "active_worker_scenes_after_transition": list(slots.XP_SCENES),
        "completed_memory_scenes": copy.deepcopy(dict(memory_terminal_evidence)),
        "worker_cohort_transition": {
            "contract": slots.TRANSITION_CONTRACT,
            "from_parallelism_per_scene": 2,
            "to_xp_parallelism_per_scene": 4,
            "memory_scene_workers_stopped_before_reassignment": True,
            "native_xp_workload_unchanged": True,
            "new_worker_resource_quota_source": "recipient_xp_prototype",
            "new_worker_cpu_numa_position_source": "released_memory_worker",
        },
        "workers": original_xp + [copy.deepcopy(dict(row)) for row in additions],
    })
    value["templates"] = {
        scene: source["templates"][scene] for scene in slots.XP_SCENES
    }
    return value


def _ensure_fresh_target(
    freeze: Mapping[str, Any], worker: Mapping[str, Any],
) -> tuple[Path, Path, Path, Path]:
    worker_id = str(worker["worker_id"])
    runtime_root = Path(str(freeze["storage"]["runtime_root"])) / worker_id
    datadir = runtime_root / "datadir"
    credentials = runtime_root / "credentials"
    template = Path(str(freeze["templates"][worker["scene"]])).resolve(strict=True)
    if (
        worker_id not in slots.NEW_CONTAINER
        or str(worker["container"]) != slots.NEW_CONTAINER[worker_id]
        or not str(runtime_root).startswith("/disk2/")
        or not str(template).startswith("/disk2/")
        or runtime_root.exists()
        or runtime_root.is_symlink()
    ):
        raise RuntimeError(f"unsafe or non-fresh XP slot target: {worker_id}")
    existing = reset._docker_run(
        ["docker", "ps", "-aq", "--filter", f"name=^{worker['container']}$"],
        timeout=30,
    ).stdout.strip()
    if existing:
        raise RuntimeError(f"new XP container identity already exists: {worker_id}")
    if subprocess.run(
        ["ss", "-ltnH", f"sport = :{int(worker['host_port'])}"],
        capture_output=True, text=True, check=True, timeout=15,
    ).stdout.strip():
        raise RuntimeError(f"new XP host port already listens: {worker_id}")
    runtime_root.mkdir(mode=0o700)
    datadir.mkdir(mode=0o700)
    credentials.mkdir(mode=0o700)
    return runtime_root, datadir, credentials, template


def provision_worker(
    freeze: Mapping[str, Any], worker: Mapping[str, Any], output: Path,
) -> dict[str, Any]:
    """Create one fresh independent XP clone and return full live authority."""
    worker = copy.deepcopy(dict(worker))
    worker_id = str(worker["worker_id"])
    runtime_root, datadir, credentials, template = _ensure_fresh_target(
        freeze, worker,
    )
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    password = secrets.token_hex(32)
    benchmark_password = secrets.token_hex(32)
    defaults = credentials / "selected.cnf"
    benchmark_defaults = credentials / "benchmark.cnf"
    credential_init = credentials / "slot-init.sql"
    reset._write_private(defaults, reset.prep._defaults(password))
    reset._write_private(
        benchmark_defaults, reset._benchmark_defaults(benchmark_password),
    )
    init_sql = (
        "CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED WITH "
        f"caching_sha2_password BY '{password}';\n"
        "ALTER USER 'root'@'%' IDENTIFIED WITH caching_sha2_password "
        f"BY '{password}';\n"
        "CREATE USER IF NOT EXISTS 'root'@'localhost' IDENTIFIED WITH "
        f"caching_sha2_password BY '{password}';\n"
        "ALTER USER 'root'@'localhost' IDENTIFIED WITH caching_sha2_password "
        f"BY '{password}';\n"
        f"CREATE USER IF NOT EXISTS '{reset.BENCHMARK_USER}'@'%' "
        f"IDENTIFIED WITH caching_sha2_password BY '{benchmark_password}';\n"
        f"ALTER USER '{reset.BENCHMARK_USER}'@'%' IDENTIFIED WITH "
        f"caching_sha2_password BY '{benchmark_password}';\n"
        f"GRANT ALL PRIVILEGES ON `hybench_sf10x`.* TO "
        f"'{reset.BENCHMARK_USER}'@'%';\nFLUSH PRIVILEGES;\n"
    )
    reset._write_private(credential_init, init_sql)
    subprocess.run(
        ["sudo", "-n", "chown", "999:999", str(credential_init)],
        check=True, timeout=30,
    )
    subprocess.run(
        ["sudo", "-n", "chmod", "0400", str(credential_init)],
        check=True, timeout=30,
    )
    copied_at = time.monotonic()
    source_bytes = reset._host_tree_bytes(template)
    first_cpu_segment = str(worker["database_cpuset"]).split(",", 1)[0]
    subprocess.run(
        [
            "taskset", "-c", first_cpu_segment, "sudo", "-n", "cp", "-a",
            "--reflink=never", f"{template}/.", str(datadir),
        ],
        check=True, timeout=1800,
    )
    subprocess.run(
        [
            "sudo", "-n", "rm", "-f", str(datadir / "mysql.sock"),
            str(datadir / "mysqld.sock"), str(datadir / "mysqld.pid"),
        ], check=True, timeout=30,
    )
    subprocess.run(
        ["sudo", "-n", "chown", "-R", "999:999", str(datadir)],
        check=True, timeout=600,
    )
    subprocess.run(
        [
            "sudo", "-n", "find", str(datadir), "-type", "d", "-exec",
            "chmod", "0750", "{}", "+",
        ], check=True, timeout=600,
    )
    subprocess.run(
        [
            "sudo", "-n", "find", str(datadir), "-type", "f", "-exec",
            "chmod", "0640", "{}", "+",
        ], check=True, timeout=600,
    )
    copied_bytes = reset._host_tree_bytes(datadir)
    if abs(copied_bytes - source_bytes) > 4 * 1024 * 1024:
        raise RuntimeError(f"{worker_id} full physical copy byte size differs")
    copy_seconds = time.monotonic() - copied_at
    argv = [
        "docker", "run", "-d", "--init", "--name", str(worker["container"]),
        # The shared point-reset implementation fail-closes on this owner
        # label.  Keep its exact authority while recording the dynamic-slot
        # origin in the additional labels below.
        "--label", "ra.owner=mysql_four_scene_v5",
        "--label", f"ra.scenario={worker['scene']}",
        "--label", f"ra.worker_id={worker_id}",
        "--label", f"ra.slot_origin={worker['slot_origin']}",
        "--cpus", str(worker["cpu_quota_cores"]),
        "--cpuset-cpus", str(worker["database_cpuset"]),
        "--cpuset-mems", str(worker["database_cpuset_mems"]),
        "--memory", str(worker["memory_bytes"]),
        "--memory-swap", str(worker["memory_bytes"]),
        "--pids-limit", str(worker["pids_max"]),
        "--device-read-bps",
        f"{freeze['storage']['device']}:{worker['io_read_bps']}",
        "--device-write-bps",
        f"{freeze['storage']['device']}:{worker['io_write_bps']}",
        "-p", f"127.0.0.1:{worker['host_port']}:3306",
        "-v", f"{datadir}:/var/lib/mysql:rw",
        "-v", f"{credentials}:/run/ra-credentials:ro",
        "-v", f"{credential_init}:/run/ra-init/slot.sql:ro",
        reset.IMAGE, "mysqld", "--init-file=/run/ra-init/slot.sql",
        "--persisted-globals-load=OFF", "--bind-address=0.0.0.0",
        f"--innodb-buffer-pool-size={worker['innodb_buffer_pool_bytes']}",
        "--innodb-use-native-aio=OFF", "--innodb-flush-method=fsync",
        "--innodb-flush-log-at-trx-commit=2", "--skip-log-bin",
        "--max-connections=128", "--performance-schema=ON",
    ]
    reset._docker_run(argv, timeout=300)
    reset._wait_mysql_host(worker, defaults, timeout_seconds=300)
    reset._host_mysql(
        worker, defaults,
        "UPDATE mysql.server_cost SET cost_value=NULL; "
        "UPDATE mysql.engine_cost SET cost_value=NULL; FLUSH OPTIMIZER_COSTS;",
        timeout=60,
    )
    query = reset._host_mysql(
        worker, defaults,
        "SELECT @@version,@@global.innodb_buffer_pool_size,"
        "@@global.innodb_use_native_aio,@@global.innodb_flush_method; "
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema='hybench_sf10x';",
        timeout=60,
    ).stdout.splitlines()
    if len(query) != 2 or int(query[1]) != 8:
        raise RuntimeError(f"{worker_id} HyBench schema readback differs")
    version, buffer_pool, native_aio, flush_method = query[0].split("\t")
    if (
        int(buffer_pool) != int(worker["innodb_buffer_pool_bytes"])
        or native_aio != "0"
        or flush_method.lower() != "fsync"
    ):
        raise RuntimeError(f"{worker_id} MySQL fixed-setting readback differs")
    inspect = reset._docker_json("inspect", str(worker["container"]))
    kernel = reset.prep._kernel_resource_readback(inspect, worker)
    blkio = reset.prep._enforce_blkio(inspect, freeze, worker)
    benchmark_identity = reset._host_mysql(
        worker, benchmark_defaults,
        "SELECT CURRENT_USER(),DATABASE();",
        timeout=30,
    ).stdout.strip().split("\t")
    if not benchmark_identity or not benchmark_identity[0].startswith(
        f"{reset.BENCHMARK_USER}@"
    ):
        raise RuntimeError(f"{worker_id} benchmark account readback differs")
    row = {
        **worker,
        "ready": True,
        "container_id": inspect["Id"],
        "container_pid": int((inspect.get("State") or {}).get("Pid") or 0),
        "datadir": str(datadir),
        "credential_defaults": str(defaults),
        "root_defaults_rotated": str(defaults),
        "benchmark_defaults": str(benchmark_defaults),
        "benchmark_defaults_mode": oct(benchmark_defaults.stat().st_mode & 0o777),
        "benchmark_user": reset.BENCHMARK_USER,
        "external_current_user": benchmark_identity[0],
        "external_database": "hybench_sf10x",
        "database": "hybench_sf10x",
        "template": str(template),
        "source_bytes": source_bytes,
        "copied_bytes_before_bootstrap": copied_bytes,
        "copy_mode": "cp_a_reflink_never",
        "copy_seconds": copy_seconds,
        "mysql_version": version,
        "innodb_use_native_aio": False,
        "innodb_flush_method": "fsync",
        "kernel_cgroup_resources": kernel,
        "blkio_authority": blkio,
        "passwords_emitted": False,
        "slot_provisioned_at": now(),
    }
    evidence = output / f"{worker_id}.provisioned.json"
    atomic(evidence, row)
    row["worker_evidence_path"] = str(evidence)
    row["worker_evidence_sha256"] = sha(evidence)
    return row

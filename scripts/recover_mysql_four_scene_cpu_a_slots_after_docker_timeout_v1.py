#!/usr/bin/env python3
"""Resume v864 data-plane preparation after delayed Docker API responses.

The physical copies and credential files already exist.  A timed-out
``docker run -d`` may nevertheless have created or started its bootstrap
container, so this recovery resumes at that durable boundary instead of
copying the databases again.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import prepare_mysql_four_scene_v4 as base
from scripts import prepare_mysql_four_scene_cpu_a_slots_two_worker_v1 as v1


HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_cpu_a_slots_two_worker_20260828_v2"
    / "four_scene_effective_resource_successor.json"
)
SOURCE = HISTORY / "v867_to_c_four_scene_cpu_a_slots_docker_timeout_recovered_20260828"
OUTPUT = HISTORY / "v869_to_c_four_scene_cpu_a_slots_xpio_timeout_recovered_20260828"
FAILED_IDS = {"xp_io_w1", "xp_io_w2"}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def run(command: list[str], timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True, check=True, timeout=timeout
    )


def docker_inspect_or_none(name: str) -> dict[str, Any] | None:
    deadline = time.monotonic() + 300
    while True:
        try:
            probe = subprocess.run(
                ["docker", "inspect", name], cwd=ROOT, text=True,
                capture_output=True, check=False, timeout=60,
            )
            break
        except subprocess.TimeoutExpired:
            if time.monotonic() >= deadline:
                raise
            time.sleep(2)
    if probe.returncode != 0:
        if "No such object" in probe.stderr:
            return None
        raise RuntimeError(f"docker inspect {name} failed: {probe.stderr.strip()}")
    values = json.loads(probe.stdout)
    return dict(values[0])


def password_from_defaults(path: Path) -> str:
    match = re.search(r"^password=(.+)$", path.read_text(encoding="utf-8"), re.MULTILINE)
    if match is None:
        raise RuntimeError(f"password missing from {path}")
    return match.group(1).strip()


def wait_bootstrap(name: str) -> None:
    inspected = docker_inspect_or_none(name)
    if inspected is None:
        raise RuntimeError(f"bootstrap container absent: {name}")
    state = dict(inspected.get("State") or {})
    if state.get("Status") == "created":
        run(["docker", "start", name], timeout=180)
    deadline = time.monotonic() + 360
    while time.monotonic() < deadline:
        inspected = docker_inspect_or_none(name)
        state = dict((inspected or {}).get("State") or {})
        if state.get("Running") is not True:
            raise RuntimeError(f"{name} exited before password normalization: {state}")
        try:
            probe = subprocess.run(
                ["docker", "exec", name, "mysqladmin", "-uroot", "ping", "--silent"],
                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False, timeout=20,
            )
        except subprocess.TimeoutExpired:
            time.sleep(2)
            continue
        if probe.returncode == 0:
            return
        time.sleep(2)
    raise RuntimeError(f"{name} readiness timed out")


def launch_final(freeze: Mapping[str, Any], worker: Mapping[str, Any]) -> None:
    worker_id = str(worker["worker_id"])
    container = str(worker["container"])
    storage = dict((freeze.get("storage_by_worker") or {})[worker_id])
    runtime = Path(str(worker["runtime_root"]))
    datadir = runtime / "datadir"
    credentials = runtime / "credentials"
    is_xp = str(worker["scene"]) in {"hybench_xp_cpu", "hybench_xp_io"}
    command = [
        "docker", "run", "-d", *(["--init"] if is_xp else []),
        "--name", container,
        "--label", "ra.owner=mysql_four_scene_v5",
        "--label", f"ra.scenario={worker['scene']}",
        "--label", f"ra.worker_id={worker_id}",
        "--cpus", str(worker["cpu_quota_cores"]),
        "--cpuset-cpus", str(worker["database_cpuset"]),
        "--cpuset-mems", str(worker["database_cpuset_mems"]),
        "--memory", str(worker["memory_bytes"]),
        "--memory-swap", str(worker["memory_bytes"]),
        "--pids-limit", str(worker["pids_max"]),
        "--device-read-bps", f"{storage['device']}:{worker['io_read_bps']}",
        "--device-write-bps", f"{storage['device']}:{worker['io_write_bps']}",
        "-p", f"127.0.0.1:{worker['host_port']}:3306",
        "-v", f"{datadir}:/var/lib/mysql:rw",
        "-v", f"{credentials}:/run/ra-credentials:ro",
        base.IMAGE, "mysqld", "--persisted-globals-load=OFF",
        "--bind-address=0.0.0.0",
        f"--innodb-buffer-pool-size={worker['innodb_buffer_pool_bytes']}",
        "--innodb-flush-method=fsync" if is_xp else "--innodb-flush-method=O_DIRECT",
        "--innodb-flush-log-at-trx-commit=2", "--skip-log-bin",
        "--max-connections=128", "--performance-schema=ON",
    ]
    if is_xp:
        command.append("--innodb-use-native-aio=OFF")
    try:
        run(command, timeout=300)
    except subprocess.TimeoutExpired:
        if docker_inspect_or_none(container) is None:
            raise
    base._wait_mysql(container, "/run/ra-credentials/selected.cnf", timeout_seconds=360)


def finish_one(freeze: Mapping[str, Any], worker: dict[str, Any]) -> dict[str, Any]:
    worker_id = str(worker["worker_id"])
    container = str(worker["container"])
    bootstrap = container + "_bootstrap"
    runtime = Path(str(worker["runtime_root"]))
    datadir = runtime / "datadir"
    defaults = runtime / "credentials/selected.cnf"
    if not datadir.is_dir() or not defaults.is_file():
        raise RuntimeError(f"{worker_id} durable copy boundary missing")

    final_inspect = docker_inspect_or_none(container)
    if final_inspect is None:
        wait_bootstrap(bootstrap)
        password = password_from_defaults(defaults)
        sql = (
            "FLUSH PRIVILEGES; "
            f"CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED WITH caching_sha2_password BY '{password}'; "
            f"ALTER USER 'root'@'%' IDENTIFIED WITH caching_sha2_password BY '{password}'; "
            f"ALTER USER 'root'@'localhost' IDENTIFIED WITH caching_sha2_password BY '{password}';"
        )
        run(["docker", "exec", bootstrap, "mysql", "-uroot", "-NBe", sql], timeout=180)
        run(["docker", "stop", "--time", "120", bootstrap], timeout=300)
        run(["docker", "rm", bootstrap], timeout=180)
        launch_final(freeze, worker)
    else:
        if (final_inspect.get("State") or {}).get("Running") is not True:
            run(["docker", "start", container], timeout=180)
        base._wait_mysql(container, "/run/ra-credentials/selected.cnf", timeout_seconds=360)

    run([
        "docker", "exec", container, "mysql",
        "--defaults-file=/run/ra-credentials/selected.cnf", "-NBe",
        "UPDATE mysql.server_cost SET cost_value=NULL; "
        "UPDATE mysql.engine_cost SET cost_value=NULL; FLUSH OPTIMIZER_COSTS;",
    ], timeout=180)
    database = v1.DATABASES[str(worker["scene"])]
    query = run([
        "docker", "exec", container, "mysql",
        "--defaults-file=/run/ra-credentials/selected.cnf", "-NBe",
        "SELECT @@version,@@global.innodb_buffer_pool_size; "
        f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='{database}';",
    ], timeout=180).stdout.splitlines()
    if len(query) != 2:
        raise RuntimeError(f"{worker_id} schema readback cardinality differs")
    version, buffer_pool = query[0].split("\t")
    table_count = int(query[1])
    inspect = base._docker_json("inspect", container)
    host = dict(inspect.get("HostConfig") or {})
    expected = {
        "CpusetCpus": worker["database_cpuset"],
        "CpusetMems": worker["database_cpuset_mems"],
        "NanoCpus": int(round(float(worker["cpu_quota_cores"]) * 1_000_000_000)),
        "Memory": int(worker["memory_bytes"]),
        "PidsLimit": int(worker["pids_max"]),
    }
    differences = {key: {"actual": host.get(key), "expected": value}
                   for key, value in expected.items() if host.get(key) != value}
    if differences or int(buffer_pool) != int(worker["innodb_buffer_pool_bytes"]) or table_count <= 0:
        raise RuntimeError(f"{worker_id} effective resource/schema differs: {differences}")
    storage = dict((freeze.get("storage_by_worker") or {})[worker_id])
    mini = dict(freeze)
    mini["storage"] = storage
    kernel = base._kernel_resource_readback(inspect, worker)
    blkio = base._enforce_blkio(inspect, mini, worker)
    template = Path(str((freeze.get("templates") or {})[worker["scene"]]))
    row = {
        **dict(worker), "container_id": inspect["Id"],
        "container_pid": (inspect.get("State") or {}).get("Pid"),
        "datadir": str(datadir), "credential_defaults": str(defaults),
        "template": str(template), "source_bytes": base._mounted_tree_bytes(template),
        "copied_bytes_before_bootstrap": base._mounted_tree_bytes(datadir),
        "copy_mode": "cp_a_reflink_never", "copy_seconds": None,
        "mysql_version": version, "database": database, "table_count": table_count,
        "effective_resources": {
            **expected, "DockerMemorySwap": int(host.get("MemorySwap") or 0),
            "HostSwapDevices": base._host_swap_devices(), "EffectiveSwapBytes": 0,
        },
        "kernel_cgroup_resources": kernel, "blkio_authority": blkio,
        "innodb_buffer_pool_bytes": int(buffer_pool), "ready": True,
        "recovered_after_docker_run_timeout": True,
    }
    return v1.provision(row)


def main() -> int:
    freeze = load(FREEZE)
    source = load(SOURCE / "prepared_binding.json")
    if source.get("status") != "PREPARATION_FAILED_CLOSED":
        raise RuntimeError("v864 failed preparation boundary differs")
    OUTPUT.mkdir(parents=True, exist_ok=False, mode=0o700)
    workers = {str(row["worker_id"]): dict(row) for row in freeze.get("workers") or []}
    existing = {str(row["worker_id"]): dict(row) for row in source.get("workers") or []}
    results = dict(existing)
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="docker-timeout-recovery") as pool:
        futures = {pool.submit(finish_one, freeze, workers[key]): key for key in sorted(FAILED_IDS)}
        for future in as_completed(futures):
            worker_id = futures[future]
            try:
                results[worker_id] = future.result()
                print(json.dumps({"stage": "RECOVERED", "worker_id": worker_id}), flush=True)
            except BaseException as error:
                failures.append({"worker_id": worker_id, "error": f"{type(error).__name__}: {error}"})
                print(json.dumps({"stage": "FAILED_CLOSED", **failures[-1]}), flush=True)
    expected = set(workers)
    status = "PREPARED" if not failures and set(results) == expected else "PREPARATION_FAILED_CLOSED"
    common = {
        "created_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE), "freeze_sha256": sha(FREEZE),
        "accepted_rounds": {scene: 0 for scene in v1.DATABASES},
        "workload_started": False, "fresh_r0_complete": False,
        "formal_outer_started": False, "algorithm_a_measurement_credit_reused": False,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "recovered_from": str(SOURCE),
    }
    prepared = OUTPUT / "prepared_binding.json"
    atomic(prepared, {
        **common, "schema_version": "mysql_cpu_a_slots_prepared_data_planes_v1",
        "status": status, "workers": [results[key] for key in sorted(results)],
        "failures": failures, "physical_prepare_parallelism": 2,
    })
    accounts = OUTPUT / "benchmark_accounts_binding.json"
    atomic(accounts, {
        **common, "schema_version": "mysql_cpu_a_slots_benchmark_accounts_v1",
        "status": "PASS" if status == "PREPARED" else "FAILED_CLOSED",
        "prepared_binding_path": str(prepared), "prepared_binding_sha256": sha(prepared),
        "workers": [results[key] for key in sorted(results)],
        "failures": failures, "passwords_emitted": False,
    })
    print(json.dumps({"status": status, "prepared": len(results), "failures": failures}), flush=True)
    return 0 if status == "PREPARED" else 4


if __name__ == "__main__":
    raise SystemExit(main())

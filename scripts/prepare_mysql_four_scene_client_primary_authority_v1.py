#!/usr/bin/env python3
"""Repair and seal the eight-worker authority for client-primary B/C.

Only the four data planes whose current identity/mount no longer matches the
two-worker freeze are rebuilt.  The remaining four are resource-read back and
adopted.  No PostgreSQL or non-MySQL process is stopped or changed.
"""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v24 as coordination
from scripts import prepare_mysql_four_scene_v4 as prepare
from scripts import provision_mysql_four_scene_benchmark_users_v5 as accounts


HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = ROOT / (
    "config/formal/frozen_mysql_four_scene_two_worker_20260903_v20/"
    "four_scene_effective_resource_successor.json"
)
FREEZE_SCHEMA = "mysql_four_scene_two_worker_client_primary_successor_v20"
REPAIR_WORKERS = {
    "job_w1", "job_w2", "tpch_w1", "tpch_w2", "xp_cpu_w1", "xp_cpu_w2",
}
SWAP_REBUILD_WORKERS = {"tpch_w2", "xp_cpu_w2"}
DATABASES = {
    "job_memory": "imdb_job",
    "tpch_sf4_memory": "tpch",
    "hybench_xp_cpu": "hybench_sf10x",
    "hybench_xp_io": "hybench_sf10x",
}


def now() -> str:
    return datetime.now().astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def run(argv: list[str], *, timeout: float = 600.0, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, cwd=ROOT, capture_output=True, text=True, check=check,
        timeout=timeout,
    )


def inspect(container: str, *, absent_ok: bool = False) -> dict[str, Any] | None:
    completed = run(["docker", "inspect", container], timeout=300, check=False)
    if completed.returncode != 0:
        if absent_ok and "No such" in completed.stderr:
            return None
        raise RuntimeError(f"Docker inspect failed for {container}: {completed.stderr[-500:]}")
    rows = json.loads(completed.stdout)
    if not isinstance(rows, list) or len(rows) != 1:
        raise RuntimeError(f"Docker inspect cardinality differs for {container}")
    return dict(rows[0])


def cpus(value: str) -> set[int]:
    result: set[int] = set()
    for token in value.split(","):
        left, separator, right = token.strip().partition("-")
        if left:
            result.update(range(int(left), int(right) + 1) if separator else [int(left)])
    return result


def host_swap_inventory() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for line in Path("/proc/swaps").read_text(encoding="utf-8").splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 5:
            result.append({
                "filename": fields[0], "type": fields[1],
                "size_kib": int(fields[2]), "used_kib": int(fields[3]),
                "priority": int(fields[4]),
            })
    return result


def install_prepare_successor() -> None:
    original = prepare._run

    def zero_swappiness_run(
        argv: list[str], *, timeout: int = 600, capture: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        effective = list(argv)
        if effective[:2] == ["docker", "run"] and "--memory-swap" in effective:
            if "--memory-swappiness" not in effective:
                index = effective.index("--memory-swap")
                effective[index + 2:index + 2] = ["--memory-swappiness", "0"]
        try:
            return original(effective, timeout=timeout, capture=capture)
        except subprocess.TimeoutExpired:
            if effective[:3] != ["docker", "run", "-d"] or "--name" not in effective:
                raise
            name = effective[effective.index("--name") + 1]
            deadline = time.monotonic() + 300.0
            while time.monotonic() < deadline:
                current = inspect(name, absent_ok=True)
                if current is not None and (current.get("State") or {}).get("Running") is True:
                    return subprocess.CompletedProcess(effective, 0, f"{current['Id']}\n", "")
                time.sleep(2.0)
            raise

    prepare._run = zero_swappiness_run
    prepare._host_swap_devices = lambda: []


def quarantine_and_rebuild(
    freeze: dict[str, Any], worker: dict[str, Any], output: Path,
) -> dict[str, Any]:
    worker_id = str(worker["worker_id"])
    container = str(worker["container"])
    bootstrap = container + "_bootstrap"
    bootstrap_current = inspect(bootstrap, absent_ok=True)
    if bootstrap_current is not None:
        if (bootstrap_current.get("State") or {}).get("Running") is True:
            run(["docker", "stop", "--time", "120", bootstrap], timeout=300)
        run(["docker", "rm", bootstrap], timeout=300)
    current = inspect(container, absent_ok=True)
    if current is not None:
        labels = dict((current.get("Config") or {}).get("Labels") or {})
        if labels.get("ra.owner") != "mysql_four_scene_v5":
            raise RuntimeError(f"refusing to replace non-project container {container}")
        if (current.get("State") or {}).get("Running") is True:
            run(["docker", "stop", "--time", "120", container], timeout=300)
        run(["docker", "rm", container], timeout=300)
    runtime = Path(str(freeze["storage"]["runtime_root"])) / worker_id
    if runtime.exists():
        quarantine = (
            Path(str(freeze["storage"]["runtime_root"]))
            / f"quarantine_client_primary_{datetime.now().strftime('%Y%m%dT%H%M%S')}_{os.getpid()}"
            / worker_id
        )
        quarantine.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        runtime.rename(quarantine)
    row = prepare._prepare_worker(freeze, worker, output)
    row.update({
        "fresh_rebuild_for_client_primary_campaign": True,
        "memory_swappiness": 0,
        "effective_swap_policy": "anonymous_swap_disabled_swappiness_0",
    })
    return row


def recover_or_rebuild(
    freeze: dict[str, Any], worker: dict[str, Any], output: Path,
) -> dict[str, Any]:
    current = inspect(str(worker["container"]), absent_ok=True)
    if current is not None:
        if (
            str(worker["worker_id"]) in SWAP_REBUILD_WORKERS
            and (current.get("HostConfig") or {}).get("MemorySwappiness") != 0
        ):
            return quarantine_and_rebuild(freeze, worker, output)
        row = adopt_existing(freeze, worker)
        row["fresh_rebuild_for_client_primary_campaign"] = True
        row["recovered_after_docker_control_timeout"] = True
        return row
    return quarantine_and_rebuild(freeze, worker, output)


def adopt_existing(freeze: dict[str, Any], worker: dict[str, Any]) -> dict[str, Any]:
    worker_id = str(worker["worker_id"])
    container = str(worker["container"])
    current = inspect(container)
    assert current is not None
    if (current.get("HostConfig") or {}).get("MemorySwappiness") != 0:
        raise RuntimeError(
            f"{worker_id} cannot be adopted: Docker MemorySwappiness is not zero"
        )
    if (current.get("State") or {}).get("Running") is not True:
        try:
            run(["docker", "start", container], timeout=600)
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
            current = inspect(container)
            assert current is not None
            if (current.get("State") or {}).get("Running") is not True:
                raise
        current = inspect(container)
        assert current is not None
    host = dict(current.get("HostConfig") or {})
    state = dict(current.get("State") or {})
    labels = dict((current.get("Config") or {}).get("Labels") or {})
    runtime = Path(str(freeze["storage"]["runtime_root"])) / worker_id
    mounts = {
        str(row.get("Destination") or ""): str(row.get("Source") or "")
        for row in current.get("Mounts") or []
    }
    expected = {
        "CpusetCpus": str(worker["database_cpuset"]),
        "CpusetMems": str(worker["database_cpuset_mems"]),
        "NanoCpus": int(round(float(worker["cpu_quota_cores"]) * 1_000_000_000)),
        "Memory": int(worker["memory_bytes"]),
        "MemorySwappiness": 0,
        "PidsLimit": int(worker["pids_max"]),
    }
    differences = {key: {"actual": host.get(key), "expected": value} for key, value in expected.items() if host.get(key) != value}
    if (
        not state.get("Running") or state.get("Restarting")
        or labels.get("ra.owner") != "mysql_four_scene_v5"
        or labels.get("ra.worker_id") != worker_id
        or mounts.get("/var/lib/mysql") != str(runtime / "datadir")
        or mounts.get("/run/ra-credentials") != str(runtime / "credentials")
        or differences
    ):
        raise RuntimeError(f"{worker_id} preserved plane differs: {differences}")
    mysql_completed: subprocess.CompletedProcess[str] | None = None
    for _attempt in range(120):
        mysql_completed = run([
            "docker", "exec", container, "mysql",
            "--defaults-file=/run/ra-credentials/selected.cnf", "-Nse",
            "SELECT @@version,@@global.innodb_buffer_pool_size; "
            f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='{DATABASES[str(worker['scene'])]}';",
        ], timeout=30, check=False)
        if mysql_completed.returncode == 0:
            break
        time.sleep(2)
    assert mysql_completed is not None
    mysql_lines = mysql_completed.stdout.strip().splitlines()
    mysql = mysql_lines[0].split("\t") if mysql_lines else []
    if (
        mysql_completed.returncode != 0 or len(mysql_lines) != 2
        or len(mysql) != 2 or int(mysql[1]) != int(worker["innodb_buffer_pool_bytes"])
        or int(mysql_lines[1]) <= 0
    ):
        raise RuntimeError(f"{worker_id} MySQL readback differs")
    blkio = prepare._enforce_blkio(current, freeze, worker)
    kernel = prepare._kernel_resource_readback(current, worker)
    return {
        **copy.deepcopy(worker),
        "ready": True,
        "container_id": str(current["Id"]),
        "container_pid": int(state.get("Pid") or 0),
        "datadir": str(runtime / "datadir"),
        "credential_defaults": str(runtime / "credentials/selected.cnf"),
        "root_defaults_rotated": str(runtime / "credentials/selected.cnf"),
        "database": DATABASES[str(worker["scene"])],
        "mysql_version": mysql[0],
        "container_init": host.get("Init"),
        "kernel_cgroup_resources": kernel,
        "blkio_authority": blkio,
        "memory_swappiness": 0,
        "docker_memory_swap_readback": int(host.get("MemorySwap") or 0),
        "effective_swap_policy": "anonymous_swap_disabled_swappiness_0",
        "fresh_rebuild_for_client_primary_campaign": False,
    }


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--resume-partial", action="store_true")
    args = parser.parse_args()
    output = HISTORY / args.run_id / "authority"
    if output.is_symlink() or (
        output.exists() and not args.resume_partial
    ):
        raise RuntimeError(f"fresh authority output required: {output}")
    if output.exists() and (output / "authority_result.json").exists():
        raise RuntimeError("completed authority cannot be resumed")
    output.mkdir(parents=True, mode=0o700, exist_ok=args.resume_partial)
    freeze = load(FREEZE)
    workers = {str(row["worker_id"]): copy.deepcopy(row) for row in freeze["workers"]}
    if freeze.get("schema_version") != FREEZE_SCHEMA or len(workers) != 8:
        raise RuntimeError("client-primary resource freeze differs")
    install_prepare_successor()

    prepared_rows: dict[str, dict[str, Any]] = {}
    repair_output = output / "fresh_repairs"
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="client-primary-repair") as pool:
        futures = {
            pool.submit(
                recover_or_rebuild if args.resume_partial else quarantine_and_rebuild,
                freeze, workers[worker_id], repair_output,
            ): worker_id
            for worker_id in sorted(REPAIR_WORKERS)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            prepared_rows[worker_id] = future.result()
            print(json.dumps({"worker_id": worker_id, "fresh_rebuilt": True}), flush=True)
    for worker_id in sorted(set(workers) - REPAIR_WORKERS):
        prepared_rows[worker_id] = adopt_existing(freeze, workers[worker_id])
        print(json.dumps({"worker_id": worker_id, "adopted": True}), flush=True)

    target_cpus = set().union(*(
        cpus(str(row["database_cpuset"])) | cpus(str(row["client_cpuset"]))
        for row in workers.values()
    ))
    gate = coordination.rolling_quiet_gate(
        run_id=f"{args.run_id}-authority",
        required_seconds=60.0,
        maximum_wait_seconds=600.0,
        maximum_load1=512.0,
        target_cpus=target_cpus,
    )
    gate.update({
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "host_swap_inventory": host_swap_inventory(),
        "container_effective_swap_policy": "anonymous_swap_disabled_swappiness_0",
    })
    preflight_path = output / "transition_preflight.json"
    preflight = {
        **gate,
        "schema_version": "mysql_client_primary_eight_worker_preflight_v1",
        "freeze_path": str(FREEZE), "freeze_sha256": sha(FREEZE),
        "formal_launch_authorized": gate.get("status") == "PASS",
        "accepted_rounds": {scene: 0 for scene in freeze["active_scenes"]},
    }
    atomic(preflight_path, preflight)
    if preflight["formal_launch_authorized"] is not True:
        raise RuntimeError("eight-worker dynamic non-interference gate failed")

    prepared_path = output / "prepared_binding.json"
    prepared = {
        "schema_version": "mysql_client_primary_eight_worker_prepared_v1",
        "status": "PREPARED", "created_at": now(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE), "freeze_sha256": sha(FREEZE),
        "preflight_path": str(preflight_path), "preflight_sha256": sha(preflight_path),
        "workers": [prepared_rows[key] for key in sorted(prepared_rows)],
        "workload_started": False, "fresh_r0_complete": False,
        "formal_outer_started": False,
        "accepted_rounds": {scene: 0 for scene in freeze["active_scenes"]},
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
    }
    atomic(prepared_path, prepared)

    accounts.PREPARED = prepared_path
    accounts.FREEZE = FREEZE
    accounts.FREEZE_SCHEMA = FREEZE_SCHEMA
    accounts.OUTPUT_SCHEMA = "mysql_client_primary_eight_worker_accounts_v1"
    accounts.AUTHORITY_VERSION = "client_primary_eight_worker_v1"
    account_run_id = f"{args.run_id}_accounts"
    old_argv = sys.argv
    sys.argv = [str(Path(accounts.__file__)), "--run-id", account_run_id, "--rotate-all"]
    try:
        code = accounts.main()
    finally:
        sys.argv = old_argv
    if code != 0:
        raise RuntimeError("benchmark account provisioning failed")
    source_accounts = HISTORY / account_run_id / "benchmark_accounts_binding.json"
    accounts_path = output / "benchmark_accounts_binding.json"
    shutil.copy2(source_accounts, accounts_path)
    accounts_path.with_name(accounts_path.name + ".sha256").write_text(
        f"{sha(accounts_path)}  {accounts_path.name}\n", encoding="ascii",
    )
    result = {
        "schema_version": "mysql_client_primary_eight_worker_authority_v1",
        "status": "PASS", "created_at": now(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "freeze": {"path": str(FREEZE), "sha256": sha(FREEZE)},
        "preflight": {"path": str(preflight_path), "sha256": sha(preflight_path)},
        "prepared": {"path": str(prepared_path), "sha256": sha(prepared_path)},
        "accounts": {"path": str(accounts_path), "sha256": sha(accounts_path)},
        "worker_ids": sorted(workers),
        "fresh_rebuilt_worker_ids": sorted(REPAIR_WORKERS),
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
    }
    atomic(output / "authority_result.json", result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

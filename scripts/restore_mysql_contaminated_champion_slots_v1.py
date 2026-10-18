#!/usr/bin/env python3
"""Restore three relational slots and seal six live champion-replay workers."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v17 as coordination
from scripts import prepare_mysql_four_scene_v4 as prepare
from scripts import build_mysql_mixed_disk_four_worker_scene_authority_v1 as authority


OUTPUT = ROOT / "history/mysql_four_scene_20260809/v1064_contaminated_champion_live_authority_20260829"
FOUR_FREEZE_PATH = ROOT / "config/formal/frozen_mysql_four_scene_cpu_a_slots_four_worker_20260828_v3/four_scene_effective_resource_successor.json"
IO_FREEZE_PATH = ROOT / "config/formal/frozen_mysql_xp_eight_worker_20260827_v1/xp_effective_resource_successor.json"
RESTORES = {
    "job_w1": {
        "current_label": "xp_cpu_w5",
        "backup": "/disk2/resource_adaptive_mysql_algorithm_c_cpu_a_slots_v2/quarantine/v986_xp_cpu_eight_worker_cpu_a_slots_authority_20260828/discard_quarantine/xp_cpu_w5",
        "schema": "imdb_job",
    },
    "job_w2": {
        "current_label": "xp_cpu_w6",
        "backup": "/disk1/resource_adaptive/mysql_algorithm_c_cpu_a_slots_v2/quarantine/v1003_xp8_cpu_a_slots_additions_preprovision_20260828/discard_quarantine/xp_cpu_w6",
        "schema": "imdb_job",
    },
    "tpch_w1": {
        "current_label": "xp_io_w5",
        "backup": "/disk2/resource_adaptive_mysql_algorithm_c_cpu_a_slots_v2/quarantine/v987_xp_io_eight_worker_cpu_a_slots_authority_20260828/discard_quarantine/xp_io_w5",
        "schema": "tpch",
    },
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
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(f"{sha(path)}  {path.name}\n")


def mini_freeze(full: Mapping[str, Any], worker_id: str) -> dict[str, Any]:
    value = copy.deepcopy(dict(full))
    value["storage"] = copy.deepcopy(dict(full["storage_by_worker"][worker_id]))
    value["container_owner_label"] = "mysql_four_scene_v5"
    return value


def docker_run_worker(freeze: Mapping[str, Any], worker: Mapping[str, Any]) -> None:
    runtime = Path(str(worker["runtime_root"]))
    datadir = runtime / "datadir"
    credentials = runtime / "credentials"
    container = str(worker["container"])
    command = [
        "docker", "run", "-d", "--name", container,
        "--label", "ra.owner=mysql_four_scene_v5",
        "--label", f"ra.scenario={worker['scene']}",
        "--label", f"ra.worker_id={worker['worker_id']}",
        "--cpus", str(worker["cpu_quota_cores"]),
        "--cpuset-cpus", str(worker["database_cpuset"]),
        "--cpuset-mems", str(worker["database_cpuset_mems"]),
        "--memory", str(worker["memory_bytes"]),
        "--memory-swap", str(worker["memory_bytes"]),
        "--pids-limit", str(worker["pids_max"]),
        "--device-read-bps", f"{freeze['storage']['device']}:{worker['io_read_bps']}",
        "--device-write-bps", f"{freeze['storage']['device']}:{worker['io_write_bps']}",
        "-p", f"127.0.0.1:{worker['host_port']}:3306",
        "-v", f"{datadir}:/var/lib/mysql:rw",
        "-v", f"{credentials}:/run/ra-credentials:ro",
        prepare.IMAGE, "mysqld", "--persisted-globals-load=OFF", "--bind-address=0.0.0.0",
        f"--innodb-buffer-pool-size={worker['innodb_buffer_pool_bytes']}",
        "--innodb-flush-method=O_DIRECT", "--innodb-flush-log-at-trx-commit=2",
        "--skip-log-bin", "--max-connections=128", "--performance-schema=ON",
    ]
    prepare._run(command, timeout=60)
    prepare._wait_mysql(container, "/run/ra-credentials/selected.cnf")
    prepare._run([
        "docker", "exec", container, "mysql",
        "--defaults-file=/run/ra-credentials/selected.cnf", "-NBe",
        "UPDATE mysql.server_cost SET cost_value=NULL; UPDATE mysql.engine_cost SET cost_value=NULL; FLUSH OPTIMIZER_COSTS;",
    ], timeout=60)


def restore_one(full_freeze: Mapping[str, Any], worker: dict[str, Any]) -> dict[str, Any]:
    worker_id = str(worker["worker_id"])
    spec = RESTORES[worker_id]
    runtime = Path(str(worker["runtime_root"]))
    backup = Path(str(spec["backup"]))
    current = authority.inspect(str(worker["container"]))
    labels = (current.get("Config") or {}).get("Labels") or {}
    mounts = {str(row.get("Destination")): str(row.get("Source")) for row in current.get("Mounts") or []}
    if (
        (current.get("State") or {}).get("Running") is not True
        or labels.get("ra.worker_id") != spec["current_label"]
        or mounts.get("/var/lib/mysql") != str(runtime / "datadir")
        or not backup.is_dir() or not (backup / "credentials/selected.cnf").is_file()
    ):
        raise RuntimeError(f"{worker_id} recoverable predecessor identity differs")
    subprocess.run([
        "mysql", f"--defaults-file={runtime / 'credentials/selected.cnf'}",
        "--host=127.0.0.1", f"--port={worker['host_port']}", "--connect-timeout=5",
        "-NBe", "SELECT SCHEMA_NAME FROM information_schema.schemata WHERE SCHEMA_NAME='hybench_sf10x'",
    ], cwd=ROOT, text=True, capture_output=True, check=True, timeout=30)
    prepare._run(["docker", "stop", "--time", "120", str(worker["container"])], timeout=150)
    prepare._run(["docker", "rm", str(worker["container"])], timeout=30)
    discarded = runtime.parent / "quarantine" / OUTPUT.name / worker_id
    if discarded.exists() or discarded.is_symlink():
        raise RuntimeError(f"fresh discarded-runtime quarantine required: {discarded}")
    discarded.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.rename(runtime, discarded)
    os.rename(backup, runtime)
    scoped = mini_freeze(full_freeze, worker_id)
    docker_run_worker(scoped, worker)
    row = authority.canonical_row(scoped, worker)
    row = authority.verify_live(scoped, row)
    row.update({
        "restored_from_recoverable_quarantine": str(backup),
        "discarded_interrupted_xp_runtime": str(discarded),
        "full_template_recopy_performed": False,
        "restored_at": now(),
    })
    return row


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh restoration authority required: {OUTPUT}")
    four = load(FOUR_FREEZE_PATH)
    io_freeze = load(IO_FREEZE_PATH)
    four_workers = {str(row["worker_id"]): dict(row) for row in four["workers"]}
    io_workers = {str(row["worker_id"]): dict(row) for row in io_freeze["workers"]}
    OUTPUT.mkdir(parents=True, mode=0o700)
    rows: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="restore-relational") as pool:
        futures = {pool.submit(restore_one, four, four_workers[worker_id]): worker_id for worker_id in RESTORES}
        for future in as_completed(futures):
            worker_id = futures[future]
            try:
                rows[worker_id] = future.result()
                print(json.dumps({"stage": "RESTORED", "worker_id": worker_id}), flush=True)
            except BaseException as error:
                failures.append({"worker_id": worker_id, "error": f"{type(error).__name__}: {error}"})
                print(json.dumps({"stage": "FAILED_CLOSED", **failures[-1]}), flush=True)
    if failures:
        atomic(OUTPUT / "restore_failures.json", {"status": "FAILED_CLOSED", "failures": failures})
        return 4
    for worker_id in ("xp_cpu_w1",):
        scoped = mini_freeze(four, worker_id)
        rows[worker_id] = authority.verify_live(scoped, authority.canonical_row(scoped, four_workers[worker_id]))
    for worker_id in ("xp_io_w1", "xp_io_w2"):
        scoped = mini_freeze(io_freeze, worker_id)
        rows[worker_id] = authority.verify_live(scoped, authority.canonical_row(scoped, io_workers[worker_id]))
    target_cpus = set().union(*(
        authority.cpus(str(row["database_cpuset"])) | authority.cpus(str(row["client_cpuset"]))
        for row in rows.values()
    ))
    gate = coordination.rolling_quiet_gate(
        run_id=OUTPUT.name, required_seconds=60.0, maximum_wait_seconds=600.0,
        maximum_load1=512.0, target_cpus=target_cpus,
    )
    if gate.get("status") != "PASS":
        raise RuntimeError("restored champion replay authority gate failed")
    common = {
        "created_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True, "status": "PASS",
        "formal_launch_authorized": True, "observed_seconds": gate.get("observed_seconds"),
        "rolling_dynamic_noninterference_gate": gate,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "accepted_state_credit_increment": 0,
        "resource_freeze_by_scene": {
            "job_memory": {"path": str(FOUR_FREEZE_PATH), "sha256": sha(FOUR_FREEZE_PATH)},
            "tpch_sf4_memory": {"path": str(FOUR_FREEZE_PATH), "sha256": sha(FOUR_FREEZE_PATH)},
            "hybench_xp_cpu": {"path": str(FOUR_FREEZE_PATH), "sha256": sha(FOUR_FREEZE_PATH)},
            "hybench_xp_io": {"path": str(IO_FREEZE_PATH), "sha256": sha(IO_FREEZE_PATH)},
        },
    }
    preflight = OUTPUT / "transition_preflight.json"
    atomic(preflight, {**common, "schema_version": "mysql_contaminated_champion_preflight_v1"})
    prepared = OUTPUT / "prepared_binding.json"
    atomic(prepared, {
        **common, "schema_version": "mysql_contaminated_champion_prepared_v1",
        "status": "PREPARED", "preflight_path": str(preflight), "preflight_sha256": sha(preflight),
        "workers": [rows[key] for key in sorted(rows)], "workload_started": False,
    })
    accounts = OUTPUT / "benchmark_accounts_binding.json"
    atomic(accounts, {
        **common, "schema_version": "mysql_contaminated_champion_accounts_v1",
        "prepared_binding_path": str(prepared), "prepared_binding_sha256": sha(prepared),
        "workers": [rows[key] for key in sorted(rows)], "passwords_emitted": False,
    })
    manifest = OUTPUT / "live_authority.json"
    atomic(manifest, {
        **common, "schema_version": "mysql_contaminated_champion_live_authority_v1",
        "artifacts": {"prepared": str(prepared), "preflight": str(preflight), "accounts": str(accounts)},
        "artifacts_sha256": {"prepared": sha(prepared), "preflight": sha(preflight), "accounts": sha(accounts)},
        "workers": sorted(rows), "restored_relational_workers": sorted(RESTORES),
    })
    print(json.dumps({"status": "PASS", "authority": str(manifest)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

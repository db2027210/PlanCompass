#!/usr/bin/env python3
"""Refresh one live two-worker scene on the mixed CPU-A-slot successor."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_cpu_a_slots_v12 as dynamic_gate
from scripts import prepare_mysql_four_scene_v4 as prepare


HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_cpu_a_slots_two_worker_20260828_v2"
    / "four_scene_effective_resource_successor.json"
)
SCENES = {
    "job_memory": "imdb_job",
    "tpch_sf4_memory": "tpch",
    "hybench_xp_cpu": "hybench_sf10x",
    "hybench_xp_io": "hybench_sf10x",
}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular JSON required: {path}")
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
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def run(argv: list[str], *, timeout: float = 120.0, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, cwd=ROOT, text=True, capture_output=True, check=check,
        timeout=timeout,
    )


def inspect(name: str) -> dict[str, Any]:
    value = json.loads(run(["docker", "inspect", name], timeout=60).stdout)
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise RuntimeError(f"Docker inspect cardinality differs: {name}")
    return value[0]


def cpus(value: str) -> set[int]:
    result: set[int] = set()
    for segment in value.split(","):
        start, separator, end = segment.strip().partition("-")
        result.update(range(int(start), int(end) + 1) if separator else [int(start)])
    return result


def read_live_worker(freeze: Mapping[str, Any], worker: Mapping[str, Any]) -> dict[str, Any]:
    row = copy.deepcopy(dict(worker))
    worker_id = str(row["worker_id"])
    container = str(row["container"])
    inspected = inspect(container)
    state = inspected.get("State") or {}
    host = inspected.get("HostConfig") or {}
    expected = {
        "CpusetCpus": str(row["database_cpuset"]),
        "CpusetMems": str(row["database_cpuset_mems"]),
        "NanoCpus": int(round(float(row["cpu_quota_cores"]) * 1_000_000_000)),
        "Memory": int(row["memory_bytes"]),
        "PidsLimit": int(row["pids_max"]),
    }
    differences = {
        key: {"actual": host.get(key), "expected": value}
        for key, value in expected.items() if host.get(key) != value
    }
    if state.get("Running") is not True or state.get("Restarting") is True or differences:
        raise RuntimeError(f"{worker_id} live Docker resource differs: {differences}")
    storage = dict(
        (freeze.get("storage_by_worker") or {}).get(worker_id)
        or freeze["storage"]
    )
    runtime_root = Path(
        str(row.get("runtime_root") or Path(str(storage["runtime_root"])) / worker_id)
    )
    datadir = runtime_root / "datadir"
    mounts = {
        str(item.get("Destination") or ""): str(item.get("Source") or "")
        for item in inspected.get("Mounts") or []
    }
    if mounts.get("/var/lib/mysql") != str(datadir):
        raise RuntimeError(f"{worker_id} datadir mount differs")
    mysql = run([
        "docker", "exec", container, "mysql",
        "--defaults-extra-file=/run/ra-credentials/selected.cnf", "-Nse",
        "SELECT @@GLOBAL.innodb_buffer_pool_size,"
        "@@GLOBAL.innodb_use_native_aio,@@GLOBAL.innodb_flush_method;",
    ], timeout=60).stdout.strip().split("\t")
    if len(mysql) != 3 or int(mysql[0]) != int(row["innodb_buffer_pool_bytes"]):
        raise RuntimeError(f"{worker_id} InnoDB readback differs")
    database = SCENES[str(row["scene"])]
    identity = run([
        "docker", "exec", container, "mysql",
        "--defaults-extra-file=/run/ra-credentials/benchmark.cnf",
        "-D", database, "-Nse",
        "SELECT CURRENT_USER(),DATABASE();",
    ], timeout=60).stdout.strip().split("\t")
    if len(identity) != 2 or not identity[0].startswith("ra_benchmark@") or identity[1] != database:
        raise RuntimeError(f"{worker_id} benchmark identity differs")
    sessions = run([
        "docker", "exec", container, "mysql",
        "--defaults-extra-file=/run/ra-credentials/selected.cnf", "-Nse",
        "SELECT COUNT(*) FROM information_schema.processlist "
        "WHERE ID<>CONNECTION_ID() AND COMMAND<>'Daemon' AND USER<>'event_scheduler';",
    ], timeout=60).stdout.strip()
    if sessions != "0":
        raise RuntimeError(f"{worker_id} still has active sessions: {sessions}")
    row.update({
        "ready": True, "container_id": str(inspected["Id"]),
        "container_pid": int(state.get("Pid") or 0), "datadir": str(datadir),
        "credential_defaults": str(runtime_root / "credentials/selected.cnf"),
        "root_defaults_rotated": str(runtime_root / "credentials/selected.cnf"),
        "benchmark_defaults": str(runtime_root / "credentials/benchmark.cnf"),
        "benchmark_user": "ra_benchmark", "database": database,
        "external_current_user": identity[0], "external_database": database,
        "innodb_use_native_aio": mysql[1] == "1",
        "innodb_flush_method": mysql[2], "container_init": host.get("Init"),
        "kernel_cgroup_resources": prepare._kernel_resource_readback(inspected, row),
        "blkio_authority": prepare._enforce_blkio(inspected, dict(freeze), row),
        "passwords_emitted": False,
    })
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=sorted(SCENES), required=True)
    parser.add_argument("--source-authority", type=Path, required=True)
    parser.add_argument("--sequence-state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.source_authority.expanduser().resolve()
    sequence_state = args.sequence_state.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if HISTORY.resolve() not in output.parents or output.exists() or output.is_symlink():
        raise RuntimeError("fresh authority output under formal history required")
    freeze = load(FREEZE)
    source_preflight_path = source / "transition_preflight.json"
    source_prepared_path = source / "prepared_binding.json"
    source_accounts_path = source / "benchmark_accounts_binding.json"
    source_preflight = load(source_preflight_path)
    source_prepared = load(source_prepared_path)
    source_accounts = load(source_accounts_path)
    sequence = load(sequence_state)
    if (
        freeze.get("schema_version")
        != "mysql_four_scene_cpu_a_slots_two_worker_successor_v2"
        or source_preflight.get("status") != "PASS"
        or source_preflight.get("freeze_sha256") != sha(FREEZE)
        or source_prepared.get("status") != "PREPARED"
        or source_prepared.get("freeze_sha256") != sha(FREEZE)
        or source_accounts.get("status") != "PASS"
        or source_accounts.get("freeze_sha256") != sha(FREEZE)
        or sequence.get("async_scene") != args.scene
        or sequence.get("status") != "COMPLETE"
        or sequence.get("stage") != "FINISHED"
        or not bool((sequence.get("converged") or {}).get(args.scene))
    ):
        raise RuntimeError("scene handoff authority differs")
    frozen = {str(row["worker_id"]): dict(row) for row in freeze["workers"]}
    target_ids = {
        worker_id for worker_id, row in frozen.items() if row["scene"] == args.scene
    }
    if len(target_ids) != 2:
        raise RuntimeError("two-worker scene cardinality differs")
    refreshed = {worker_id: read_live_worker(freeze, frozen[worker_id]) for worker_id in sorted(target_ids)}
    prepared_rows = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in source_prepared["workers"]
    }
    account_rows = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in source_accounts["workers"]
    }
    if set(prepared_rows) != set(frozen) or set(account_rows) != set(frozen):
        raise RuntimeError("source authority worker set differs")
    prepared_rows.update(copy.deepcopy(refreshed))
    account_rows.update(copy.deepcopy(refreshed))
    target_cpus = {
        cpu for row in refreshed.values()
        for field in ("database_cpuset", "client_cpuset")
        for cpu in cpus(str(row[field]))
    }
    gate = dynamic_gate.rolling_quiet_gate(
        run_id=f"mysql-scene-handoff-{args.scene}-{os.getpid()}",
        required_seconds=60.0, maximum_wait_seconds=180.0,
        maximum_load1=16.0, target_cpus=target_cpus,
    )
    if gate.get("status") != "PASS":
        raise RuntimeError(f"scene handoff dynamic gate failed: {gate.get('status')}")
    output.mkdir(parents=True, mode=0o700)
    accepted = {scene: 0 for scene in SCENES}
    common = {
        "created_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene_scope": args.scene, "freeze_path": str(FREEZE),
        "freeze_sha256": sha(FREEZE), "accepted_rounds": accepted,
        "observed_seconds": float(gate.get("observed_seconds") or 60.0),
        "formal_launch_authorized": True,
        "rolling_dynamic_noninterference_gate": gate,
        "sequence_state_path": str(sequence_state),
        "sequence_state_sha256": sha(sequence_state),
        "source_authority_path": str(source),
        "source_preflight_sha256": sha(source_preflight_path),
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "accepted_state_credit_increment": 0,
    }
    preflight = output / "transition_preflight.json"
    atomic(preflight, {**common, "schema_version": "mysql_two_worker_scene_handoff_preflight_v1", "status": "PASS"})
    prepared = output / "prepared_binding.json"
    atomic(prepared, {
        **common, "schema_version": "mysql_two_worker_scene_handoff_prepared_v1",
        "status": "PREPARED", "preflight_path": str(preflight),
        "preflight_sha256": sha(preflight),
        "workers": [prepared_rows[key] for key in sorted(prepared_rows)],
        "workload_started": False, "fresh_r0_complete": False,
        "formal_outer_started": False,
    })
    accounts = output / "benchmark_accounts_binding.json"
    atomic(accounts, {
        **common, "schema_version": "mysql_two_worker_scene_handoff_accounts_v1",
        "status": "PASS", "prepared_binding_path": str(prepared),
        "prepared_binding_sha256": sha(prepared),
        "workers": [account_rows[key] for key in sorted(account_rows)],
        "passwords_emitted": False, "workload_started": False,
    })
    result = {
        "schema_version": "mysql_cpu_a_slots_two_worker_scene_live_authority_v2",
        "status": "PASS", "created_at": now(), "scene": args.scene,
        "freeze_path": str(FREEZE), "freeze_sha256": sha(FREEZE),
        "preflight": str(preflight), "preflight_sha256": sha(preflight),
        "prepared": str(prepared), "prepared_sha256": sha(prepared),
        "accounts": str(accounts), "accounts_sha256": sha(accounts),
        "target_worker_ids": sorted(target_ids),
        "sequence_state_path": str(sequence_state),
        "sequence_state_sha256": sha(sequence_state),
    }
    atomic(output / "live_authority.json", result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

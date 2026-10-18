#!/usr/bin/env python3
"""Seal one scene's live w1-w4 authority after its boundary adoption."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v14 as gate_v14
from scripts import prepare_mysql_four_scene_v4 as prepare


HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_mixed_disk_four_worker_20260827_v3"
    / "four_scene_effective_resource_successor.json"
)
FREEZE_SCHEMA = "mysql_four_scene_mixed_disk_four_worker_successor_v3"
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
    if path.is_symlink() or not path.is_file():
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


def cpus(value: str) -> set[int]:
    result: set[int] = set()
    for field in value.split(","):
        start, separator, end = field.strip().partition("-")
        result.update(range(int(start), int(end) + 1) if separator else [int(start)])
    return result


def inspect(name: str) -> dict[str, Any]:
    completed = subprocess.run(
        ["docker", "inspect", name], cwd=ROOT, capture_output=True,
        text=True, check=True, timeout=60,
    )
    value = json.loads(completed.stdout)
    if not isinstance(value, list) or len(value) != 1:
        raise RuntimeError(f"Docker inspect cardinality differs: {name}")
    return dict(value[0])


def canonical_row(
    freeze: Mapping[str, Any], worker: Mapping[str, Any],
    live: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    row = copy.deepcopy(dict(live or {}))
    row.update(copy.deepcopy(dict(worker)))
    storage = dict(
        (freeze.get("storage_by_worker") or {}).get(worker["worker_id"])
        or freeze["storage"]
    )
    runtime = Path(
        str(worker.get("runtime_root") or Path(str(storage["runtime_root"])) / str(worker["worker_id"]))
    )
    row.update({
        "runtime_root": str(runtime),
        "datadir": str(worker.get("datadir") or runtime / "datadir"),
        "credential_defaults": str(
            worker.get("credential_defaults") or runtime / "credentials/selected.cnf"
        ),
        "root_defaults_rotated": str(
            worker.get("credential_defaults") or runtime / "credentials/selected.cnf"
        ),
        "benchmark_defaults": str(
            worker.get("benchmark_defaults") or runtime / "credentials/benchmark.cnf"
        ),
        "database": SCENES[str(worker["scene"])],
        "benchmark_user": "ra_benchmark",
        "passwords_emitted": False,
    })
    return row


def verify_live(freeze: Mapping[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    current = inspect(str(row["container"]))
    state = current.get("State") or {}
    host = current.get("HostConfig") or {}
    labels = (current.get("Config") or {}).get("Labels") or {}
    mounts = {
        str(item.get("Destination") or ""): str(item.get("Source") or "")
        for item in current.get("Mounts") or []
    }
    expected_nano = int(round(float(row["cpu_quota_cores"]) * 1_000_000_000))
    if (
        state.get("Running") is not True
        or state.get("Restarting") is True
        or labels.get("ra.owner") != "mysql_four_scene_v5"
        or labels.get("ra.worker_id") != row["worker_id"]
        or host.get("CpusetCpus") != row["database_cpuset"]
        or host.get("CpusetMems") != row["database_cpuset_mems"]
        or int(host.get("NanoCpus") or 0) != expected_nano
        or int(host.get("Memory") or 0) != int(row["memory_bytes"])
        or int(host.get("PidsLimit") or 0) != int(row["pids_max"])
        or mounts.get("/var/lib/mysql") != row["datadir"]
    ):
        raise RuntimeError(f"{row['worker_id']} mixed four-worker live identity differs")
    identity = subprocess.run(
        [
            "mysql", f"--defaults-file={row['benchmark_defaults']}",
            "--host=127.0.0.1", f"--port={row['host_port']}",
            "--connect-timeout=5", "-NBe", "SELECT CURRENT_USER(),DATABASE()",
            str(row["database"]),
        ],
        cwd=ROOT, capture_output=True, text=True, check=True, timeout=30,
    ).stdout.strip().split("\t")
    if len(identity) != 2 or not identity[0].startswith("ra_benchmark@") or identity[1] != row["database"]:
        raise RuntimeError(f"{row['worker_id']} benchmark identity differs")
    row.update({
        "ready": True,
        "container_id": str(current["Id"]),
        "container_pid": int(state.get("Pid") or 0),
        "external_current_user": identity[0],
        "external_database": identity[1],
        "kernel_cgroup_resources": prepare._kernel_resource_readback(current, row),
        "blkio_authority": prepare._enforce_blkio(current, dict(freeze), row),
    })
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=sorted(SCENES), required=True)
    parser.add_argument("--two-worker-authority", type=Path, required=True)
    parser.add_argument("--w34-authority", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if HISTORY.resolve() not in output.parents or output.exists() or output.is_symlink():
        raise RuntimeError("fresh mixed four-worker authority output under history required")
    freeze = load(FREEZE)
    if freeze.get("schema_version") != FREEZE_SCHEMA:
        raise RuntimeError("mixed four-worker freeze schema differs")
    frozen = {str(row["worker_id"]): dict(row) for row in freeze["workers"]}
    target_ids = {
        worker_id for worker_id, row in frozen.items()
        if row["scene"] == args.scene
    }
    if len(frozen) != 16 or len(target_ids) != 4:
        raise RuntimeError("mixed four-worker frozen cardinality differs")

    two_root = args.two_worker_authority.expanduser().resolve()
    two_prepared_path = two_root / "prepared_binding.json"
    two_accounts_path = two_root / "benchmark_accounts_binding.json"
    two_prepared = load(two_prepared_path)
    two_accounts = load(two_accounts_path)
    w34_path = args.w34_authority.expanduser().resolve()
    w34 = load(w34_path)
    if (
        two_prepared.get("status") != "PREPARED"
        or two_accounts.get("status") != "PASS"
        or float(two_prepared.get("observed_seconds") or 0) < 60.0
        or w34.get("status") != "PASS"
        or w34.get("scene") != args.scene
        or w34.get("resource_freeze_sha256") != sha(FREEZE)
    ):
        raise RuntimeError("mixed four-worker source authority differs")
    live_rows = {
        str(row["worker_id"]): dict(row)
        for row in (two_prepared.get("workers") or [])
        if str(row.get("worker_id")) in target_ids
    }
    live_rows.update({
        str(row["worker_id"]): dict(row) for row in (w34.get("workers") or [])
    })
    if set(live_rows) != target_ids:
        raise RuntimeError("mixed four-worker target authority set differs")

    rows: dict[str, dict[str, Any]] = {}
    for worker_id, worker in frozen.items():
        row = canonical_row(freeze, worker, live_rows.get(worker_id))
        if worker_id in target_ids:
            row = verify_live(freeze, row)
        rows[worker_id] = row
    target_cpus = {
        cpu for worker_id in target_ids
        for field in (rows[worker_id]["database_cpuset"], rows[worker_id]["client_cpuset"])
        for cpu in cpus(str(field))
    }
    gate = gate_v14.rolling_quiet_gate(
        run_id=f"mysql-mixed-four-worker-{args.scene}-{os.getpid()}",
        required_seconds=60.0, maximum_wait_seconds=600.0,
        # Full-parallel mode intentionally permits all sixteen disjoint MySQL
        # lanes to be busy.  Global load1 is not an interference signal for a
        # scene whose effective CPU/NUMA/storage placement is disjoint; the
        # rolling gate below still audits the actual co-resident processes.
        maximum_load1=512.0, target_cpus=target_cpus,
    )
    if gate.get("status") != "PASS":
        raise RuntimeError("mixed four-worker dynamic non-interference gate failed")

    output.mkdir(parents=True, mode=0o700)
    accepted = {scene: 0 for scene in SCENES}
    common = {
        "created_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene_scope": args.scene, "freeze_path": str(FREEZE),
        "freeze_sha256": sha(FREEZE), "accepted_rounds": accepted,
        "observed_seconds": float(gate.get("observed_seconds") or 0.0),
        "formal_launch_authorized": True,
        "rolling_dynamic_noninterference_gate": gate,
        "source_two_worker_authority": str(two_root),
        "source_two_worker_prepared_sha256": sha(two_prepared_path),
        "source_w34_authority": str(w34_path),
        "source_w34_authority_sha256": sha(w34_path),
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "accepted_state_credit_increment": 0,
    }
    preflight_path = output / "transition_preflight.json"
    atomic(preflight_path, {
        **common,
        "schema_version": "mysql_mixed_four_worker_scene_preflight_v1",
        "status": "PASS",
    })
    prepared_path = output / "prepared_binding.json"
    atomic(prepared_path, {
        **common,
        "schema_version": "mysql_mixed_four_worker_scene_prepared_v1",
        "status": "PREPARED", "preflight_path": str(preflight_path),
        "preflight_sha256": sha(preflight_path),
        "workers": [rows[key] for key in sorted(rows)],
        "workload_started": False, "fresh_r0_complete": False,
        "formal_outer_started": False,
    })
    accounts_path = output / "benchmark_accounts_binding.json"
    atomic(accounts_path, {
        **common,
        "schema_version": "mysql_mixed_four_worker_scene_accounts_v1",
        "status": "PASS", "prepared_binding_path": str(prepared_path),
        "prepared_binding_sha256": sha(prepared_path),
        "workers": [rows[key] for key in sorted(rows)],
        "passwords_emitted": False, "workload_started": False,
    })
    result = {
        "schema_version": "mysql_mixed_four_worker_scene_live_authority_v1",
        "status": "PASS", "created_at": now(), "scene": args.scene,
        "freeze_path": str(FREEZE), "freeze_sha256": sha(FREEZE),
        "preflight": str(preflight_path), "preflight_sha256": sha(preflight_path),
        "prepared": str(prepared_path), "prepared_sha256": sha(prepared_path),
        "accounts": str(accounts_path), "accounts_sha256": sha(accounts_path),
        "target_worker_ids": sorted(target_ids),
    }
    atomic(output / "live_authority.json", result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Adopt one scene's completed disk1 baseline planes as formal w3/w4.

Only the live data planes are adopted.  No external optimizer result, baseline
score, calibration point, candidate measurement, or accepted-round credit is
reused.  The operation is scene-local and is intended to run only at that
scene's completed outer-round boundary.
"""

from __future__ import annotations

import argparse
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

from scripts import prepare_mysql_four_scene_v4 as prep


FREEZE = (
    ROOT / "config/formal/"
    "frozen_mysql_four_scene_mixed_disk_four_worker_20260827_v3/"
    "four_scene_effective_resource_successor.json"
)
EXPECTED_FREEZE_SHA256 = (
    "94c5ff345a0b2f096e07e1d5fb19d13636a0a93c019835a1628fe814aad41063"
)
HISTORY = ROOT / "history/mysql_four_scene_20260809"
SCENES = {
    "job_memory": ("job_w3", "job_w4"),
    "tpch_sf4_memory": ("tpch_w3", "tpch_w4"),
    "hybench_xp_cpu": ("xp_cpu_w3", "xp_cpu_w4"),
    "hybench_xp_io": ("xp_io_w3", "xp_io_w4"),
}
DATABASES = {
    "job_memory": "imdb_job",
    "tpch_sf4_memory": "tpch",
    "hybench_xp_cpu": "hybench_sf10x",
    "hybench_xp_io": "hybench_sf10x",
}
# The audited XP reset authority intentionally accepts one formal owner label
# across both SSDs.  Storage provenance remains frozen independently in
# ``storage_by_worker`` and in every live read-back row; using the established
# owner value here lets the adopted disk1 lanes enter the same reset path as
# disk2 without weakening the exact container/worker/storage checks.
FORMAL_OWNER = "mysql_four_scene_v5"
EXTERNAL_OWNER = "mysql_external_baseline_disk1_v1"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


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


def inspect(name: str) -> dict[str, Any]:
    completed = subprocess.run(
        ["docker", "inspect", name], capture_output=True, text=True,
        check=True, timeout=60,
    )
    value = json.loads(completed.stdout)
    if not isinstance(value, list) or len(value) != 1:
        raise RuntimeError(f"container inspect cardinality differs: {name}")
    return dict(value[0])


def mysql(worker: Mapping[str, Any], sql: str, *, timeout: int = 30) -> str:
    completed = subprocess.run(
        [
            "mysql", f"--defaults-file={worker['credential_defaults']}",
            "--host=127.0.0.1", f"--port={worker['host_port']}",
            "--connect-timeout=5", "-NBe", sql,
        ],
        capture_output=True, text=True, check=True, timeout=timeout,
    )
    return completed.stdout.strip()


def wait_mysql(worker: Mapping[str, Any], *, seconds: int = 300) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        current = inspect(str(worker["container"]))
        if (current.get("State") or {}).get("Running") is not True:
            raise RuntimeError(f"{worker['worker_id']} exited during adoption")
        try:
            if mysql(worker, "SELECT 1", timeout=15) == "1":
                return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            time.sleep(1)
    raise RuntimeError(f"{worker['worker_id']} MySQL readiness timed out")


def validate_external(worker: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    labels = (current.get("Config") or {}).get("Labels") or {}
    state = current.get("State") or {}
    host = current.get("HostConfig") or {}
    mounts = {
        str(row.get("Destination")): str(row.get("Source"))
        for row in current.get("Mounts") or []
    }
    original_id = str(worker.get("adopted_container_id_at_freeze") or "")
    original_worker = str(worker.get("adopted_physical_worker_id") or "")
    if (
        current.get("Id") != original_id
        or state.get("Running") is not True
        or labels.get("ra.owner") != EXTERNAL_OWNER
        or labels.get("ra.worker_id") != original_worker
        or labels.get("ra.scenario") != worker.get("scene")
        or host.get("CpusetCpus") != worker.get("database_cpuset")
        or host.get("CpusetMems") != worker.get("database_cpuset_mems")
        or int(host.get("Memory") or 0) != int(worker["memory_bytes"])
        or int(host.get("PidsLimit") or 0) != int(worker["pids_max"])
        or mounts.get("/var/lib/mysql") != worker.get("datadir")
        or mounts.get("/run/ra-credentials")
        != str(Path(str(worker["credential_defaults"])).parent)
    ):
        raise RuntimeError(f"{worker['worker_id']} external adoption identity differs")
    active = mysql(
        worker,
        "SELECT COUNT(*) FROM information_schema.processlist "
        "WHERE ID<>CONNECTION_ID() AND COMMAND<>'Daemon' "
        "AND USER<>'event_scheduler'",
    )
    if active != "0":
        raise RuntimeError(f"{worker['worker_id']} still has active SQL sessions")


def recreate(worker: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    container = str(worker["container"])
    original_id = str(current["Id"])
    command = [str(value) for value in (current.get("Config") or {}).get("Cmd") or []]
    image = str((current.get("Config") or {}).get("Image") or "")
    mounts = {
        str(row.get("Destination")): str(row.get("Source"))
        for row in current.get("Mounts") or []
    }
    credential_root = Path(str(worker["credential_defaults"])).parent
    reset_init = credential_root / "reset-init.sql"
    storage = dict((load(FREEZE).get("storage_by_worker") or {})[worker["worker_id"]])
    required_flags = {
        "--persisted-globals-load=OFF", "--bind-address=0.0.0.0",
        f"--innodb-buffer-pool-size={worker['innodb_buffer_pool_bytes']}",
        "--innodb-flush-log-at-trx-commit=2", "--skip-log-bin",
        "--max-connections=128", "--performance-schema=ON",
    }
    if (
        image != "mysql:8.0"
        or not command or command[0] != "mysqld"
        or not required_flags.issubset(command)
        or mounts.get("/run/ra-init/reset.sql") != str(reset_init)
        or not reset_init.is_file() or reset_init.is_symlink()
        or storage.get("device") != "/dev/nvme1n1"
        or storage.get("device_major_minor") != "259:5"
    ):
        raise RuntimeError(f"{worker['worker_id']} recreation source differs")

    subprocess.run(
        ["docker", "stop", "--time", "120", container],
        capture_output=True, text=True, check=True, timeout=180,
    )
    stopped = inspect(container)
    if stopped.get("Id") != original_id or (stopped.get("State") or {}).get("Running") is not False:
        raise RuntimeError(f"{worker['worker_id']} did not stop cleanly")
    subprocess.run(
        ["docker", "rm", container], capture_output=True, text=True,
        check=True, timeout=120,
    )
    argv = [
        "docker", "run", "-d", "--init", "--name", container,
        "--label", f"ra.owner={FORMAL_OWNER}",
        "--label", f"ra.scenario={worker['scene']}",
        "--label", f"ra.worker_id={worker['worker_id']}",
        "--label", "ra.data_plane_origin=completed_disk1_plane_adopted_no_result_credit",
        "--cpus", str(worker["cpu_quota_cores"]),
        "--cpuset-cpus", str(worker["database_cpuset"]),
        "--cpuset-mems", str(worker["database_cpuset_mems"]),
        "--memory", str(worker["memory_bytes"]),
        "--memory-swap", str(worker["memory_bytes"]),
        "--pids-limit", str(worker["pids_max"]),
        "--device-read-bps", f"{storage['device']}:{worker['io_read_bps']}",
        "--device-write-bps", f"{storage['device']}:{worker['io_write_bps']}",
        "-p", f"127.0.0.1:{worker['host_port']}:3306",
        "-v", f"{worker['datadir']}:/var/lib/mysql:rw",
        "-v", f"{credential_root}:/run/ra-credentials:ro",
        "-v", f"{reset_init}:/run/ra-init/reset.sql:ro",
        image, *command,
    ]
    subprocess.run(argv, capture_output=True, text=True, check=True, timeout=300)
    wait_mysql(worker)
    adopted = inspect(container)
    labels = (adopted.get("Config") or {}).get("Labels") or {}
    if (
        adopted.get("Id") == original_id
        or (adopted.get("State") or {}).get("Running") is not True
        or labels.get("ra.owner") != FORMAL_OWNER
        or labels.get("ra.worker_id") != worker["worker_id"]
    ):
        raise RuntimeError(f"{worker['worker_id']} formal adoption differs")
    return adopted


def readback(worker: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    host = current.get("HostConfig") or {}
    mounts = {
        str(row.get("Destination")): str(row.get("Source"))
        for row in current.get("Mounts") or []
    }
    expected_nano = int(round(float(worker["cpu_quota_cores"]) * 1_000_000_000))
    if (
        host.get("CpusetCpus") != worker["database_cpuset"]
        or host.get("CpusetMems") != worker["database_cpuset_mems"]
        or int(host.get("NanoCpus") or 0) != expected_nano
        or int(host.get("Memory") or 0) != int(worker["memory_bytes"])
        or int(host.get("PidsLimit") or 0) != int(worker["pids_max"])
        or mounts.get("/var/lib/mysql") != worker["datadir"]
    ):
        raise RuntimeError(f"{worker['worker_id']} Docker readback differs")
    kernel = prep._kernel_resource_readback(current, worker)
    blkio = prep._enforce_blkio(current, load(FREEZE), worker)
    database = DATABASES[str(worker["scene"])]
    query = mysql(
        worker,
        "SELECT @@version,@@global.innodb_buffer_pool_size; "
        f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='{database}';",
    ).splitlines()
    if len(query) != 2:
        raise RuntimeError(f"{worker['worker_id']} schema readback cardinality differs")
    version, buffer_pool = query[0].split("\t")
    table_count = int(query[1])
    if int(buffer_pool) != int(worker["innodb_buffer_pool_bytes"]) or table_count <= 0:
        raise RuntimeError(f"{worker['worker_id']} schema or buffer pool differs")
    return {
        **worker,
        "container_id": current["Id"],
        "container_pid": int((current.get("State") or {}).get("Pid") or 0),
        "ready": True,
        "mysql_version": version,
        "database": database,
        "table_count": table_count,
        "kernel_cgroup_resources": kernel,
        "blkio_authority": blkio,
        "external_method_result_credit_reused": False,
        "prior_optimizer_cost_state_credit_reused": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=sorted(SCENES), required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run-id")
    freeze = load(FREEZE)
    if (
        sha(FREEZE) != EXPECTED_FREEZE_SHA256
        or freeze.get("schema_version")
        != "mysql_four_scene_mixed_disk_four_worker_successor_v3"
        or freeze.get("status") != "FROZEN_RUNTIME_READBACK_PENDING"
        or freeze.get("formal_scope") != "mysql_only"
        or freeze.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("mixed-disk v3 freeze differs")
    all_workers = {
        str(row["worker_id"]): dict(row) for row in freeze.get("workers") or []
    }
    selected = {worker_id: all_workers[worker_id] for worker_id in SCENES[args.scene]}
    output = HISTORY / args.run_id
    authority_path = output / "w34_live_authority.json"
    if authority_path.is_file():
        prior = load(authority_path)
        if prior.get("status") == "PASS" and prior.get("resource_freeze_sha256") == sha(FREEZE):
            print(json.dumps(prior, sort_keys=True))
            return 0
        raise RuntimeError("existing adoption authority differs")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)

    rows: list[dict[str, Any]] = []
    for worker_id in SCENES[args.scene]:
        worker = selected[worker_id]
        current = inspect(str(worker["container"]))
        labels = (current.get("Config") or {}).get("Labels") or {}
        if labels.get("ra.owner") == EXTERNAL_OWNER:
            validate_external(worker, current)
            old_id = str(current["Id"])
            current = recreate(worker, current)
            adoption = {
                "external_container_id": old_id,
                "formal_container_id": current["Id"],
                "container_recreated_with_same_datadir": True,
            }
        elif (
            labels.get("ra.owner") == FORMAL_OWNER
            and labels.get("ra.worker_id") == worker_id
            and (current.get("State") or {}).get("Running") is True
        ):
            adoption = {
                "external_container_id": worker["adopted_container_id_at_freeze"],
                "formal_container_id": current["Id"],
                "container_recreated_with_same_datadir": True,
                "resumed_after_partial_adoption": True,
            }
        else:
            raise RuntimeError(f"{worker_id} adoption owner differs")
        row = readback(worker, current)
        row["adoption"] = adoption
        rows.append(row)
        atomic(output / f"{worker_id}.json", row)

    document = {
        "schema_version": "mysql_mixed_disk_scene_w34_live_authority_v1",
        "status": "PASS",
        "created_at": now(),
        "scene": args.scene,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "resource_freeze_path": str(FREEZE),
        "resource_freeze_sha256": sha(FREEZE),
        "worker_ids": list(SCENES[args.scene]),
        "workers": rows,
        "external_mysql_data_plane_adoption_performed": True,
        "external_postgresql_control_or_mutation_performed": False,
        "external_method_results_reused": False,
        "prior_optimizer_state_credit_reused": False,
        "recalibration_performed": False,
        "candidate_measurements_performed": False,
        "accepted_round_credit_increment": 0,
        "completed_candidate_replay_increment": 0,
        "scene_boundary_transition_required": True,
    }
    atomic(authority_path, document)
    print(json.dumps(document, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

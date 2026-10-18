#!/usr/bin/env python3
"""Recover live XP planes after an acknowledged Docker-run response timeout."""

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
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import provision_mysql_four_scene_benchmark_users_v5 as users
from scripts import reset_mysql_xp_data_planes_v5 as reset


FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260811_v15"
    / "four_scene_effective_resource_successor.json"
)
XP_IDS = ("xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular non-symlink JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def docker_inspect(container: str) -> dict[str, Any]:
    value = json.loads(subprocess.run(
        ["docker", "inspect", container], capture_output=True, text=True,
        check=True, timeout=180,
    ).stdout)
    if not isinstance(value, list) or len(value) != 1:
        raise RuntimeError(f"Docker inspect cardinality differs: {container}")
    return dict(value[0])


def recover_worker(
    *, freeze: Mapping[str, Any], frozen: Mapping[str, Any],
    prior: Mapping[str, Any], partial_stage: Path, output: Path,
) -> dict[str, Any]:
    worker_id = str(frozen["worker_id"])
    container = str(frozen["container"])
    runtime_root = Path(str(freeze["storage"]["runtime_root"])) / worker_id
    defaults = runtime_root / "credentials/selected.cnf"
    if not defaults.is_file() or defaults.is_symlink():
        raise RuntimeError(f"{worker_id} selected root defaults absent")
    inspect = docker_inspect(container)
    host = inspect.get("HostConfig") or {}
    labels = (inspect.get("Config") or {}).get("Labels") or {}
    mounts = [
        row for row in (inspect.get("Mounts") or [])
        if row.get("Destination") == "/var/lib/mysql"
    ]
    expected_host = {
        "CpusetCpus": frozen["database_cpuset"],
        "CpusetMems": frozen["database_cpuset_mems"],
        "NanoCpus": int(round(float(frozen["cpu_quota_cores"]) * 1_000_000_000)),
        "Memory": int(frozen["memory_bytes"]),
        "PidsLimit": int(frozen["pids_max"]),
        "Init": True,
    }
    if (
        (inspect.get("State") or {}).get("Running") is not True
        or labels.get("ra.owner") != "mysql_four_scene_v5"
        or labels.get("ra.worker_id") != worker_id
        or any(host.get(key) != value for key, value in expected_host.items())
        or len(mounts) != 1
        or mounts[0].get("Source") != str(runtime_root / "datadir")
    ):
        raise RuntimeError(f"{worker_id} recovered Docker resource identity differs")
    query = subprocess.run(
        [
            "mysql", f"--defaults-file={defaults}", "--host=127.0.0.1",
            f"--port={frozen['host_port']}", "--connect-timeout=10", "-Nse",
            "SELECT @@version,@@global.innodb_buffer_pool_size,"
            "@@global.innodb_use_native_aio,@@global.innodb_flush_method," 
            "(SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='hybench_sf10x'),"
            "(SELECT COUNT(*) FROM information_schema.processlist "
            "WHERE ID<>CONNECTION_ID() AND COMMAND<>'Daemon' "
            "AND USER<>'event_scheduler')",
        ], capture_output=True, text=True, check=True, timeout=60,
    ).stdout.strip().split("\t")
    if (
        len(query) != 6
        or int(query[1]) != int(frozen["innodb_buffer_pool_bytes"])
        or query[2] != "0"
        or query[3].lower() != "fsync"
        or int(query[4]) != 8
        or int(query[5]) != 0
    ):
        raise RuntimeError(f"{worker_id} recovered MySQL readback differs: {query}")
    kernel = reset.prep._kernel_resource_readback(inspect, frozen)
    blkio = reset.prep._enforce_blkio(inspect, freeze, frozen)
    if reset.prep._host_swap_devices():
        raise RuntimeError("host swap became active during XP recovery")
    partial = partial_stage / worker_id / "reset_worker.json"
    if partial.is_file() and not partial.is_symlink():
        row = load(partial)
        if row.get("container_id") != inspect.get("Id"):
            raise RuntimeError(f"{worker_id} partial reset container identity differs")
        partial_binding = {"path": str(partial), "sha256": sha(partial)}
    else:
        row = {**dict(prior), **dict(frozen)}
        partial_binding = None
    row.update({
        "worker_id": worker_id,
        "scene": frozen["scene"],
        "container": container,
        "container_id": inspect["Id"],
        "container_pid": (inspect.get("State") or {}).get("Pid"),
        "datadir": str(runtime_root / "datadir"),
        "credential_defaults": str(defaults),
        "database": "hybench_sf10x",
        "table_count": 8,
        "mysql_version": query[0],
        "innodb_buffer_pool_bytes": int(query[1]),
        "innodb_use_native_aio": False,
        "innodb_flush_method": query[3],
        "container_init": True,
        "docker_host_resources": {**expected_host, "EffectiveSwapBytes": 0},
        "kernel_cgroup_resources": kernel,
        "blkio_authority": blkio,
        "recovery_after_docker_run_response_timeout": True,
        "partial_reset_worker_binding": partial_binding,
        "ready": True,
    })
    worker_output = output / worker_id
    worker_output.mkdir(parents=True, exist_ok=False)
    evidence = worker_output / "recovered_worker.json"
    atomic(evidence, row)
    row["worker_evidence_path"] = str(evidence)
    row["worker_evidence_sha256"] = sha(evidence)
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--failed-state", type=Path, required=True)
    parser.add_argument("--predecessor-xp", type=Path, required=True)
    parser.add_argument("--partial-stage", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run-id")
    failed_state = args.failed_state.resolve()
    predecessor_path = args.predecessor_xp.resolve()
    partial_stage = args.partial_stage.resolve()
    freeze = load(FREEZE)
    failed = load(failed_state)
    predecessor = load(predecessor_path)
    error = str(failed.get("error") or "")
    acknowledged_docker_timeout = (
        ("docker', 'run'" in error and "timed out after 180 seconds" in error)
        or (
            "docker', 'inspect'" in error
            and "timed out after 30 seconds" in error
        )
    )
    if (
        freeze.get("schema_version")
        != "mysql_disk2_four_scene_two_worker_effective_resource_successor_v15"
        or failed.get("status") != "FAILED_CLOSED"
        or failed.get("stage") != "FAILED"
        or failed.get("outer_attempt") != 2
        or not acknowledged_docker_timeout
        or failed.get("freeze_sha256") != sha(FREEZE)
        or predecessor.get("status") not in {"PASS", "PREPARED"}
        or predecessor.get("freeze_sha256") != sha(FREEZE)
        or {row.get("worker_id") for row in (predecessor.get("workers") or [])}
        != set(XP_IDS)
        or not partial_stage.is_dir()
    ):
        raise RuntimeError("Docker-run timeout recovery authority differs")
    controller_pid = int(failed.get("pid") or 0)
    if controller_pid > 0 and Path(f"/proc/{controller_pid}").exists():
        raise RuntimeError("failed controller remains alive")
    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh recovery output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    frozen = {row["worker_id"]: row for row in freeze["workers"]}
    prior = {row["worker_id"]: row for row in predecessor["workers"]}
    recovered: dict[str, dict[str, Any]] = {}
    for worker_id in XP_IDS:
        recovered[worker_id] = recover_worker(
            freeze=freeze, frozen=frozen[worker_id], prior=prior[worker_id],
            partial_stage=partial_stage, output=output,
        )
    account_root = output / "accounts"
    account_root.mkdir()
    provisioned = []
    for worker_id in XP_IDS:
        account = users._run_worker(
            recovered[worker_id], account_root,
            verify_existing=False, rotate_all=True,
        )
        provisioned.append({**recovered[worker_id], **account})
    provisioned.sort(key=lambda row: str(row["worker_id"]))
    document = {
        "schema_version": "mysql_algorithm_c_boundary_docker_run_timeout_recovery_v1",
        "status": "PREPARED",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE),
        "freeze_sha256": sha(FREEZE),
        "failed_state_path": str(failed_state),
        "failed_state_sha256": sha(failed_state),
        "predecessor_xp_binding_path": str(predecessor_path),
        "predecessor_xp_binding_sha256": sha(predecessor_path),
        "partial_stage_path": str(partial_stage),
        "partial_stage_worker_evidence": {
            worker_id: recovered[worker_id]["partial_reset_worker_binding"]
            for worker_id in XP_IDS
        },
        "recovery_source_path": str(Path(__file__).resolve()),
        "recovery_source_sha256": sha(Path(__file__).resolve()),
        "reset_source_path": str(Path(reset.__file__).resolve()),
        "reset_source_sha256": sha(Path(reset.__file__).resolve()),
        "workers": provisioned,
        "failures": [],
        "workload_started": False,
        "fresh_r0_complete": False,
        "formal_outer_started": False,
        "accepted_rounds": {scene: 0 for scene in freeze["active_scenes"]},
    }
    binding = output / "prepared_binding.json"
    atomic(binding, document)
    print(json.dumps({
        "status": "PREPARED", "binding": str(binding),
        "workers": len(provisioned),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

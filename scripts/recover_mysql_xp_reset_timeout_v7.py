#!/usr/bin/env python3
"""Verify Docker-run timeout side effects and seal a complete v7 XP reset."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import prepare_mysql_four_scene_v4 as prep


FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v7"
    / "four_scene_effective_resource_successor.json"
)
FAILED_RESET = (
    ROOT / "history/mysql_four_scene_20260809"
    / "xp_restore_after_failed_formal_v7_20260809T193000p0800/prepared_binding.json"
)
CPU_IDS = ("xp_cpu_w1", "xp_cpu_w2")
IO_IDS = ("xp_io_w1", "xp_io_w2")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def verify_cpu(freeze: dict, worker: dict, failed: dict, output: Path) -> dict:
    worker_id = worker["worker_id"]
    matching = [row for row in failed["failures"] if row.get("worker_id") == worker_id]
    if len(matching) != 1 or not str(matching[0].get("error", "")).startswith("TimeoutExpired:"):
        raise RuntimeError(f"{worker_id} timeout evidence differs")
    container = worker["container"]
    inspect = prep._docker_json("inspect", container)
    state = inspect.get("State") or {}
    labels = (inspect.get("Config") or {}).get("Labels") or {}
    host = inspect.get("HostConfig") or {}
    expected = {
        "CpusetCpus": worker["database_cpuset"],
        "CpusetMems": worker["database_cpuset_mems"],
        "NanoCpus": int(round(float(worker["cpu_quota_cores"]) * 1_000_000_000)),
        "Memory": int(worker["memory_bytes"]),
        "PidsLimit": int(worker["pids_max"]),
    }
    mounts = inspect.get("Mounts") or []
    datadir = Path(str(freeze["storage"]["runtime_root"])) / worker_id / "datadir"
    mysql_mounts = [row for row in mounts if row.get("Destination") == "/var/lib/mysql"]
    if (
        state.get("Running") is not True
        or labels.get("ra.owner") != "mysql_four_scene_v5"
        or labels.get("ra.worker_id") != worker_id
        or labels.get("ra.data_plane_origin") != "xp_template_reset_after_formal_point"
        or any(host.get(key) != value for key, value in expected.items())
        or len(mysql_mounts) != 1
        or mysql_mounts[0].get("Source") != str(datadir)
    ):
        raise RuntimeError(f"{worker_id} Docker timeout recovery readback differs")
    active = prep._run([
        "docker", "exec", container, "mysql",
        "--defaults-file=/run/ra-credentials/selected.cnf", "-NBe",
        "SELECT COUNT(*) FROM information_schema.processlist "
        "WHERE ID<>CONNECTION_ID() AND COMMAND<>'Daemon' AND USER<>'event_scheduler'",
    ], timeout=60).stdout.strip()
    if active != "0":
        raise RuntimeError(f"{worker_id} still has active sessions")
    prep._run([
        "docker", "exec", container, "mysql",
        "--defaults-file=/run/ra-credentials/selected.cnf", "-NBe",
        "UPDATE mysql.server_cost SET cost_value=NULL; "
        "UPDATE mysql.engine_cost SET cost_value=NULL; FLUSH OPTIMIZER_COSTS;",
    ], timeout=90)
    values = prep._run([
        "docker", "exec", container, "mysql",
        "--defaults-file=/run/ra-credentials/selected.cnf", "-NBe",
        "SELECT @@version,@@global.innodb_buffer_pool_size; "
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='hybench_sf10x'; "
        "SELECT COUNT(*) FROM mysql.server_cost WHERE cost_value IS NOT NULL; "
        "SELECT COUNT(*) FROM mysql.engine_cost WHERE cost_value IS NOT NULL;",
    ], timeout=90).stdout.splitlines()
    if len(values) != 4 or int(values[1]) != 8 or values[2:] != ["0", "0"]:
        raise RuntimeError(f"{worker_id} schema/cost reset readback differs")
    version, buffer_pool = values[0].split("\t")
    if int(buffer_pool) != int(worker["innodb_buffer_pool_bytes"]):
        raise RuntimeError(f"{worker_id} buffer pool readback differs")
    kernel = prep._kernel_resource_readback(inspect, worker)
    blkio = prep._enforce_blkio(inspect, freeze, worker)
    quarantine = (
        Path(str(freeze["storage"]["runtime_root"])) / "quarantine"
        / FAILED_RESET.parent.name / worker_id
    )
    if not quarantine.is_dir() or not datadir.is_dir():
        raise RuntimeError(f"{worker_id} reset/quarantine directories differ")
    row = {
        "worker_id": worker_id,
        "scene": worker["scene"],
        "container": container,
        "container_id": inspect["Id"],
        "container_pid": state.get("Pid"),
        "host_port": worker["host_port"],
        "datadir": str(datadir),
        "credential_defaults": str(datadir.parent / "credentials/selected.cnf"),
        "template": freeze["templates"][worker["scene"]],
        "database": "hybench_sf10x",
        "table_count": 8,
        "mysql_version": version,
        "innodb_buffer_pool_bytes": int(buffer_pool),
        "docker_host_resources": {**expected, "EffectiveSwapBytes": 0},
        "kernel_cgroup_resources": kernel,
        "blkio_authority": blkio,
        "quarantined_predecessor_runtime_root": str(quarantine),
        "reset_context": "formal_point",
        "recovered_after_docker_run_response_timeout": True,
        "docker_run_side_effect_verified": True,
        "cost_tables_reset_to_defaults": True,
        "ready": True,
    }
    worker_dir = output / worker_id
    worker_dir.mkdir(parents=True, exist_ok=True)
    evidence = worker_dir / "reset_timeout_recovery_worker.json"
    evidence.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    row["worker_evidence_path"] = str(evidence)
    row["worker_evidence_sha256"] = sha(evidence)
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", args.run_id) is None:
        parser.error("unsafe run-id")
    freeze = load(FREEZE)
    failed = load(FAILED_RESET)
    if (
        freeze.get("schema_version")
        != "mysql_disk2_four_scene_two_worker_effective_resource_successor_v7"
        or failed.get("status") != "FAILED_CLOSED"
        or {row.get("worker_id") for row in failed.get("workers") or []} != set(IO_IDS)
        or {row.get("worker_id") for row in failed.get("failures") or []} != set(CPU_IDS)
    ):
        raise RuntimeError("failed XP reset binding differs")
    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    frozen = {row["worker_id"]: row for row in freeze["workers"]}
    rows = [row for row in failed["workers"] if row["worker_id"] in IO_IDS]
    rows.extend(verify_cpu(freeze, frozen[worker_id], failed, output) for worker_id in CPU_IDS)
    rows.sort(key=lambda row: row["worker_id"])
    document = {
        "schema_version": "mysql_four_scene_xp_reset_timeout_recovery_v7",
        "status": "PREPARED",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE),
        "freeze_sha256": sha(FREEZE),
        "failed_reset_path": str(FAILED_RESET),
        "failed_reset_sha256": sha(FAILED_RESET),
        "workers": rows,
        "failures": [],
        "workload_started": False,
        "fresh_r0_complete": False,
        "formal_outer_started": False,
        "accepted_rounds": {scene: 0 for scene in freeze["active_scenes"]},
    }
    binding = output / "prepared_binding.json"
    binding.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    binding.with_name(binding.name + ".sha256").write_text(
        f"{sha(binding)}  {binding.name}\n", encoding="ascii"
    )
    print(json.dumps({"status": "PREPARED", "binding": str(binding)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

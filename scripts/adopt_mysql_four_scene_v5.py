#!/usr/bin/env python3
"""Adopt the verified physical copies into the v7 effective envelope."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any


_PREP_PATH = Path(__file__).resolve().with_name("prepare_mysql_four_scene_v4.py")
_PREP_SPEC = importlib.util.spec_from_file_location("prepare_mysql_four_scene_v4", _PREP_PATH)
if _PREP_SPEC is None or _PREP_SPEC.loader is None:
    raise RuntimeError("cannot load pinned v4 preparation helpers")
prep = importlib.util.module_from_spec(_PREP_SPEC)
_PREP_SPEC.loader.exec_module(prep)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FREEZE = (
    ROOT
    / "config/formal/frozen_mysql_disk2_two_worker_20260809_v7"
    / "four_scene_effective_resource_successor.json"
)
DEFAULT_PREFLIGHT = ROOT / "preflight/mysql_four_scene_20260809/runtime_preflight_v7.json"
XP_RESET_EVIDENCE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "xp_reset_timeout_recovery_v7_20260809T193600p0800/prepared_binding.json"
)
IMAGE = prep.IMAGE
IMAGE_ID = prep.IMAGE_ID


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular non-symlink JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def _validate(
    freeze_path: Path, preflight_path: Path
) -> tuple[dict[str, Any], dict[str, Any], Path, dict[str, Any]]:
    freeze_path = freeze_path.resolve(strict=True)
    preflight_path = preflight_path.resolve(strict=True)
    freeze = _load(freeze_path)
    preflight = _load(preflight_path)
    if (
        freeze.get("schema_version")
        != "mysql_disk2_four_scene_two_worker_effective_resource_successor_v7"
        or freeze.get("formal_scope") != "mysql_only"
        or freeze.get("cross_engine_comparison_prohibited") is not True
        or freeze.get("formal_launch_authorized") is not False
        or len(freeze.get("workers") or []) != 8
    ):
        raise RuntimeError("v7 freeze identity differs")
    if (
        preflight.get("status") != "PASS"
        or preflight.get("formal_launch_authorized") is not True
        or preflight.get("freeze_path") != str(freeze_path)
        or preflight.get("freeze_sha256") != _sha(freeze_path)
        or preflight.get("blockers") != []
        or float(preflight.get("observed_seconds") or 0) < 60.0
    ):
        raise RuntimeError("v7 preflight authority differs or is not PASS")
    xp_reset = _load(XP_RESET_EVIDENCE)
    if (
        xp_reset.get("status") != "PREPARED"
        or xp_reset.get("freeze_sha256") != _sha(freeze_path)
        or len(xp_reset.get("workers") or []) != 4
        or any((xp_reset.get("accepted_rounds") or {}).values())
    ):
        raise RuntimeError("v7 fresh XP reset authority differs")
    failure_contract = freeze["predecessor_data_plane_failure"]
    failure_path = (ROOT / failure_contract["path"]).resolve(strict=True)
    failure = _load(failure_path)
    if (
        _sha(failure_path) != failure_contract["sha256"]
        or failure.get("status") != "PREPARATION_FAILED_CLOSED"
        or failure.get("workload_started") is not False
        or failure.get("fresh_r0_complete") is not False
        or failure.get("formal_outer_started") is not False
    ):
        raise RuntimeError("v4 fail-closed data-plane evidence differs")
    image = prep._docker_json("image", "inspect", IMAGE)
    if image.get("Id") != IMAGE_ID:
        raise RuntimeError("local MySQL image identity differs")
    return freeze, preflight, failure_path, failure


def _prior_evidence(
    worker_id: str, failure_path: Path, failure: dict[str, Any]
) -> dict[str, Any]:
    passed = [row for row in failure.get("workers") or [] if row.get("worker_id") == worker_id]
    failed = [row for row in failure.get("failures") or [] if row.get("worker_id") == worker_id]
    if len(passed) + len(failed) != 1:
        raise RuntimeError(f"{worker_id} predecessor evidence cardinality differs")
    if passed:
        worker_evidence = failure_path.parent / worker_id / "prepared_worker.json"
        if not worker_evidence.is_file():
            raise RuntimeError(f"{worker_id} predecessor worker evidence is absent")
        return {
            "predecessor_status": "V4_READY_BEFORE_GLOBAL_FAIL_CLOSED",
            "binding_path": str(failure_path),
            "binding_sha256": _sha(failure_path),
            "worker_evidence_path": str(worker_evidence),
            "worker_evidence_sha256": _sha(worker_evidence),
            "copy_mode": passed[0].get("copy_mode"),
            "source_bytes": passed[0].get("source_bytes"),
            "copied_bytes_before_bootstrap": passed[0].get("copied_bytes_before_bootstrap"),
        }
    error = str(failed[0].get("error") or "")
    allowed = {
        "tpch_w1": "TimeoutExpired:",
        "xp_io_w1": "RuntimeError: xp_io_w1 kernel cgroup resource differs:",
        "xp_io_w2": "RuntimeError: xp_io_w2 kernel cgroup resource differs:",
    }
    if worker_id not in allowed or not error.startswith(allowed[worker_id]):
        raise RuntimeError(f"{worker_id} predecessor failure stage is not adoptable")
    return {
        "predecessor_status": "V4_FAILED_AFTER_PHYSICAL_COPY_AND_FINAL_CONTAINER_START",
        "binding_path": str(failure_path),
        "binding_sha256": _sha(failure_path),
        "failure": error,
        "workload_started": False,
    }


def _adopt_worker(
    freeze: dict[str, Any], worker: dict[str, Any], failure_path: Path,
    failure: dict[str, Any], output_root: Path,
) -> dict[str, Any]:
    worker_id = str(worker["worker_id"])
    if re.fullmatch(r"[a-z0-9_]+", worker_id) is None:
        raise RuntimeError(f"unsafe worker id: {worker_id!r}")
    container = str(worker["container"])
    runtime_root = Path(freeze["storage"]["runtime_root"]) / worker_id
    datadir = runtime_root / "datadir"
    credentials = runtime_root / "credentials"
    defaults = credentials / "selected.cnf"
    for path in (runtime_root, datadir, credentials, defaults):
        if not path.exists() or path.is_symlink():
            raise RuntimeError(f"{worker_id} inherited data-plane path differs: {path}")
    if defaults.stat().st_mode & 0o077:
        raise RuntimeError(f"{worker_id} credential permissions are not private")
    prior = _prior_evidence(worker_id, failure_path, failure)
    existing = prep._run(
        ["docker", "ps", "-aq", "--filter", f"name=^{container}$"], timeout=30
    ).stdout.strip()
    if existing:
        inspected = prep._docker_json("inspect", container)
        labels = (inspected.get("Config") or {}).get("Labels") or {}
        mounts = inspected.get("Mounts") or []
        mysql_mounts = [row for row in mounts if row.get("Destination") == "/var/lib/mysql"]
        if (
            labels.get("ra.owner") != "mysql_four_scene_v5"
            or labels.get("ra.worker_id") != worker_id
            or len(mysql_mounts) != 1
            or mysql_mounts[0].get("Source") != str(datadir)
        ):
            raise RuntimeError(f"{worker_id} existing container authority differs")
        if (inspected.get("State") or {}).get("Running") is True:
            active = prep._run(
                [
                    "docker", "exec", container, "mysql",
                    "--defaults-file=/run/ra-credentials/selected.cnf", "-NBe",
                    "SELECT COUNT(*) FROM information_schema.processlist "
                    "WHERE ID<>CONNECTION_ID() AND COMMAND<>'Daemon' "
                    "AND USER<>'event_scheduler'",
                ], timeout=30,
            ).stdout.strip()
            if active != "0":
                raise RuntimeError(f"{worker_id} still has active non-system sessions")
            prep._run(["docker", "stop", "--time", "120", container], timeout=150)
        prep._run(["docker", "rm", container], timeout=30)

    prep._run(
        [
            "docker", "run", "-d", "--name", container,
            "--label", "ra.owner=mysql_four_scene_v5",
            "--label", f"ra.scenario={worker['scene']}",
            "--label", f"ra.worker_id={worker_id}",
            "--label", "ra.data_plane_origin=mysql_four_scene_v4_no_workload",
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
            IMAGE, "mysqld", "--persisted-globals-load=OFF", "--bind-address=0.0.0.0",
            f"--innodb-buffer-pool-size={worker['innodb_buffer_pool_bytes']}",
            "--innodb-flush-method=O_DIRECT", "--innodb-flush-log-at-trx-commit=2",
            "--skip-log-bin", "--max-connections=128", "--performance-schema=ON",
        ],
        timeout=60,
    )
    prep._wait_mysql(container, "/run/ra-credentials/selected.cnf", timeout_seconds=300)
    prep._run(
        [
            "docker", "exec", container, "mysql",
            "--defaults-file=/run/ra-credentials/selected.cnf", "-NBe",
            "UPDATE mysql.server_cost SET cost_value=NULL; "
            "UPDATE mysql.engine_cost SET cost_value=NULL; FLUSH OPTIMIZER_COSTS;",
        ],
        timeout=90,
    )
    database = {
        "job_memory": "imdb_job",
        "tpch_sf4_memory": "tpch",
        "hybench_xp_cpu": "hybench_sf10x",
        "hybench_xp_io": "hybench_sf10x",
    }[worker["scene"]]
    query = prep._run(
        [
            "docker", "exec", container, "mysql",
            "--defaults-file=/run/ra-credentials/selected.cnf", "-NBe",
            "SELECT @@version,@@global.innodb_buffer_pool_size; "
            f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='{database}';",
        ],
        timeout=90,
    ).stdout.splitlines()
    if len(query) != 2:
        raise RuntimeError(f"{worker_id} MySQL/database readback cardinality differs")
    version, buffer_pool = query[0].split("\t")
    table_count = int(query[1])
    if int(buffer_pool) != int(worker["innodb_buffer_pool_bytes"]) or table_count <= 0:
        raise RuntimeError(f"{worker_id} buffer pool or schema readback differs")
    if worker["scene"] == "tpch_sf4_memory" and table_count != 8:
        raise RuntimeError(f"{worker_id} TPC-H table count is not exactly 8")

    inspect = prep._docker_json("inspect", container)
    host = inspect.get("HostConfig") or {}
    expected_host = {
        "CpusetCpus": worker["database_cpuset"],
        "CpusetMems": worker["database_cpuset_mems"],
        "NanoCpus": int(round(float(worker["cpu_quota_cores"]) * 1_000_000_000)),
        "Memory": int(worker["memory_bytes"]),
        "PidsLimit": int(worker["pids_max"]),
    }
    differences = {
        key: {"actual": host.get(key), "expected": value}
        for key, value in expected_host.items() if host.get(key) != value
    }
    if differences:
        raise RuntimeError(f"{worker_id} Docker v5 resource differs: {differences}")
    if int(worker["swap_bytes"]) != 0 or prep._host_swap_devices():
        raise RuntimeError(f"{worker_id} effective zero-swap condition differs")
    docker_memory_swap = int(host.get("MemorySwap") or 0)
    if docker_memory_swap not in (-1, int(worker["memory_bytes"])):
        raise RuntimeError(f"{worker_id} Docker MemorySwap representation differs")
    kernel = prep._kernel_resource_readback(inspect, worker)
    blkio = prep._enforce_blkio(inspect, freeze, worker)
    mounts = inspect.get("Mounts") or []
    mysql_mounts = [row for row in mounts if row.get("Destination") == "/var/lib/mysql"]
    if len(mysql_mounts) != 1 or mysql_mounts[0].get("Source") != str(datadir):
        raise RuntimeError(f"{worker_id} datadir bind readback differs")

    row = {
        "worker_id": worker_id,
        "scene": worker["scene"],
        "container": container,
        "container_id": inspect["Id"],
        "container_pid": (inspect.get("State") or {}).get("Pid"),
        "host_port": worker["host_port"],
        "datadir": str(datadir),
        "credential_defaults": str(defaults),
        "mysql_version": version,
        "database": database,
        "table_count": table_count,
        "innodb_buffer_pool_bytes": int(buffer_pool),
        "docker_host_resources": {
            **expected_host,
            "DockerMemorySwap": docker_memory_swap,
            "EffectiveSwapBytes": 0,
        },
        "kernel_cgroup_resources": kernel,
        "blkio_authority": blkio,
        "prior_physical_copy_evidence": prior,
        "ready": True,
    }
    worker_output = output_root / worker_id
    worker_output.mkdir(parents=True, exist_ok=True)
    evidence = worker_output / "adopted_worker.json"
    evidence.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    row["worker_evidence_path"] = str(evidence)
    row["worker_evidence_sha256"] = _sha(evidence)
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", type=Path, default=DEFAULT_FREEZE)
    parser.add_argument("--preflight", type=Path, default=DEFAULT_PREFLIGHT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", args.run_id) is None:
        parser.error("unsafe run-id")
    if not 1 <= args.workers <= 2:
        parser.error("workers must be 1 or 2")
    freeze, preflight, failure_path, failure = _validate(args.freeze, args.preflight)
    output_root = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output_root.exists():
        raise RuntimeError(f"fresh v5 adoption output already exists: {output_root}")
    output_root.mkdir(parents=True, mode=0o700)
    lock = Path("/tmp/mysql_four_scene_v5_adopt.lock").open("w")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise RuntimeError("another v5 adoption owns the lock") from error

    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="mysql-v5-adopt") as pool:
        futures = {
            pool.submit(
                _adopt_worker, freeze, worker, failure_path, failure, output_root
            ): worker
            for worker in freeze["workers"]
        }
        for future in as_completed(futures):
            worker = futures[future]
            try:
                row = future.result()
                results.append(row)
                print(json.dumps({"worker_id": row["worker_id"], "ready": True}), flush=True)
            except BaseException as error:
                failures.append({
                    "worker_id": worker["worker_id"],
                    "error": f"{type(error).__name__}: {error}",
                })
                print(json.dumps(failures[-1]), flush=True)
    results.sort(key=lambda row: row["worker_id"])
    document = {
        "schema_version": "mysql_four_scene_adopted_data_planes_v7",
        "status": "PREPARED" if not failures and len(results) == 8 else "ADOPTION_FAILED_CLOSED",
        "created_at": _now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(args.freeze.resolve()),
        "freeze_sha256": _sha(args.freeze.resolve()),
        "preflight_path": str(args.preflight.resolve()),
        "preflight_sha256": _sha(args.preflight.resolve()),
        "preflight_formal_launch_authorized": preflight["formal_launch_authorized"],
        "xp_reset_evidence_path": str(XP_RESET_EVIDENCE),
        "xp_reset_evidence_sha256": _sha(XP_RESET_EVIDENCE),
        "adoption_source_path": str(Path(__file__).resolve()),
        "adoption_source_sha256": _sha(Path(__file__).resolve()),
        "predecessor_failure_path": str(failure_path),
        "predecessor_failure_sha256": _sha(failure_path),
        "workers": results,
        "failures": failures,
        "data_planes_started": len(results),
        "workload_started": False,
        "fresh_r0_complete": False,
        "formal_outer_started": False,
        "accepted_rounds": {scene: 0 for scene in freeze["active_scenes"]},
    }
    binding = output_root / "prepared_binding.json"
    binding.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    binding.with_name(binding.name + ".sha256").write_text(
        f"{_sha(binding)}  {binding.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "status": document["status"], "prepared": len(results),
        "failed": len(failures), "binding": str(binding),
    }), flush=True)
    return 0 if document["status"] == "PREPARED" else 4


if __name__ == "__main__":
    raise SystemExit(main())

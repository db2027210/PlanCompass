#!/usr/bin/env python3
"""Inventory co-resident work, bind eight workers, and launch C v62."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_outer_checkpoint_v2 as checkpoint_v2
from scripts import build_mysql_algorithm_c_outer_checkpoint_v4 as checkpoint_v4
from scripts import mysql_cross_project_wave_coordination_v8 as dynamic_gate
from scripts import prepare_mysql_four_scene_v4 as prepare


PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python").resolve()
HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_two_worker_20260826_v19"
    / "four_scene_effective_resource_successor.json"
)
CHECKPOINT_SOURCE = (
    HISTORY
    / "algorithm_c_v79_outer17_checkpoint_v20_20260817T1425p0800"
    / "prepared_binding.json"
)
LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_c_async_v62.py"
SCENES = (
    "job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io",
)
EXTRA_XP_CONTAINERS = (
    "ra_mysql_disk2_algc_xp_cpu_w3_20260825",
    "ra_mysql_disk2_algc_xp_cpu_w4_20260825",
    "ra_mysql_disk2_algc_xp_io_w3_20260825",
    "ra_mysql_disk2_algc_xp_io_w4_20260825",
)
EXTERNAL_CONTAINER_PREFIX = "ra_mysql_ext_"
EXTERNAL_PROCESS_MARKERS = (
    "run_mysql_external_baseline_pipeline.sh",
    "run_mysql_four_scene_external_baselines.py",
    "run_mysql_four_scene_external_baselines.sh",
    "mysql_external_baselines_20260826",
)
DATABASE_BY_SCENE = {
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


def inspect_container(name: str) -> dict[str, Any]:
    value = json.loads(run(["docker", "inspect", name], timeout=60).stdout)
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise RuntimeError(f"Docker inspect cardinality differs: {name}")
    return value[0]


def external_inventory() -> dict[str, Any]:
    names = [
        line.strip() for line in run(
            ["docker", "ps", "--format", "{{.Names}}"], timeout=30,
        ).stdout.splitlines()
        if line.strip().startswith(EXTERNAL_CONTAINER_PREFIX)
    ]
    processes = []
    own_pid = os.getpid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == own_pid:
            continue
        try:
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", errors="replace"
            )
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
        if any(marker in command for marker in EXTERNAL_PROCESS_MARKERS):
            processes.append({"pid": int(entry.name), "command": command[:1000]})
    return {
        "captured_at": now(),
        "running_external_containers": sorted(names),
        "external_controller_processes": sorted(processes, key=lambda row: row["pid"]),
        # A resident but idle database container is inventory, not a scheduling
        # barrier.  The versioned rolling gate below decides non-interference
        # from actual resource activity and placement.
        "active_external_workload_clear": not processes,
        "idle_container_presence_blocks_launch": False,
    }


def wait_external_release(state_path: Path, *, poll_seconds: float) -> dict[str, Any]:
    while True:
        inventory = external_inventory()
        controllers = inventory["external_controller_processes"]
        atomic(state_path, {
            "schema_version": "mysql_four_scene_c_v62_external_release_monitor_v1",
            "status": (
                "WAITING_OVERLAPPING_EXTERNAL_MYSQL_CONTROLLER_RELEASE"
                if controllers else "EXTERNAL_CONTROLLER_RELEASE_OBSERVED"
            ),
            "updated_at": now(),
            "external_inventory": inventory,
            "idle_container_presence_blocks_launch": False,
            "external_controller_presence_blocks_launch": True,
            "blocking_basis": (
                "live_cpu_and_disk2_overlap_readback"
                if controllers else None
            ),
            "noninterference_decided_by_versioned_rolling_gate": True,
            "external_process_control_or_stop_performed": False,
            "postgresql_process_control_or_stop_performed": False,
            "formal_measurement_started": False,
        })
        if not controllers:
            return inventory
        time.sleep(poll_seconds)


def ensure_container_running(name: str) -> dict[str, Any]:
    inspected = inspect_container(name)
    if (inspected.get("State") or {}).get("Running") is not True:
        run(["docker", "start", name], timeout=180)
        inspected = inspect_container(name)
    deadline = time.monotonic() + 300.0
    while time.monotonic() < deadline:
        if (inspected.get("State") or {}).get("Running") is not True:
            raise RuntimeError(f"target container exited during readiness: {name}")
        try:
            probe = run([
                "docker", "exec", name, "mysql",
                "--defaults-extra-file=/run/ra-credentials/selected.cnf",
                "--connect-timeout=3", "-Nse", "SELECT 1",
            ], timeout=60, check=False)
        except subprocess.TimeoutExpired:
            time.sleep(1.0)
            inspected = inspect_container(name)
            continue
        if probe.returncode == 0 and probe.stdout.strip() == "1":
            return inspected
        time.sleep(1.0)
        inspected = inspect_container(name)
    raise RuntimeError(f"target MySQL readiness timed out: {name}")


def switch_to_two_workers_per_scene(freeze: Mapping[str, Any]) -> list[dict[str, Any]]:
    target_names = {str(row["container"]) for row in freeze["workers"]}
    if target_names.intersection(EXTRA_XP_CONTAINERS):
        raise RuntimeError("extra XP container unexpectedly belongs to two-worker freeze")
    for name in EXTRA_XP_CONTAINERS:
        inspected = inspect_container(name)
        if (inspected.get("State") or {}).get("Running") is True:
            stopped = run(["docker", "stop", "--time", "120", name], timeout=150, check=False)
            if stopped.returncode != 0:
                raise RuntimeError(f"failed to stop unused XP expansion container {name}: {stopped.stderr}")

    rows = []
    xp_profile = freeze.get("xp_innodb_io_profile") or {}
    runtime_root = Path(str((freeze.get("storage") or {})["runtime_root"]))
    for worker in freeze["workers"]:
        row = copy.deepcopy(dict(worker))
        worker_id = str(row["worker_id"])
        container = str(row["container"])
        inspected = ensure_container_running(container)
        host = inspected.get("HostConfig") or {}
        expected_host = {
            "CpusetCpus": str(row["database_cpuset"]),
            "CpusetMems": str(row["database_cpuset_mems"]),
            "NanoCpus": int(round(float(row["cpu_quota_cores"]) * 1_000_000_000)),
            "Memory": int(row["memory_bytes"]),
            "PidsLimit": int(row["pids_max"]),
        }
        differences = {
            key: {"actual": host.get(key), "expected": expected}
            for key, expected in expected_host.items() if host.get(key) != expected
        }
        memory_swap = int(host.get("MemorySwap") or 0)
        swap_total_kib = 0
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            if line.startswith("SwapTotal:"):
                swap_total_kib = int(line.split()[1])
                break
        effective_zero_swap = memory_swap in {int(row["memory_bytes"]), 0} or (
            memory_swap == -1 and swap_total_kib == 0
        )
        if not effective_zero_swap:
            differences["MemorySwap"] = {
                "actual": memory_swap, "expected": int(row["memory_bytes"]),
            }
        if row["scene"] in {"hybench_xp_cpu", "hybench_xp_io"} and host.get("Init") is not bool(xp_profile.get("container_init")):
            differences["Init"] = {
                "actual": host.get("Init"), "expected": bool(xp_profile.get("container_init")),
            }
        if differences:
            raise RuntimeError(f"{worker_id} Docker resource readback differs: {differences}")
        expected_datadir = runtime_root / worker_id / "datadir"
        mounts = {
            str(item.get("Destination") or ""): str(item.get("Source") or "")
            for item in inspected.get("Mounts") or []
        }
        if mounts.get("/var/lib/mysql") != str(expected_datadir):
            raise RuntimeError(f"{worker_id} datadir mount differs")

        mysql_values = run([
            "docker", "exec", container, "mysql",
            "--defaults-extra-file=/run/ra-credentials/selected.cnf", "-Nse",
            "SELECT @@GLOBAL.innodb_buffer_pool_size,"
            "@@GLOBAL.innodb_use_native_aio,@@GLOBAL.innodb_flush_method;",
        ], timeout=60).stdout.strip().split("\t")
        if len(mysql_values) != 3:
            raise RuntimeError(f"{worker_id} InnoDB readback differs")
        buffer_pool_mutated = False
        expected_buffer_pool = int(row["innodb_buffer_pool_bytes"])
        if int(mysql_values[0]) != expected_buffer_pool:
            run([
                "docker", "exec", container, "mysql",
                "--defaults-extra-file=/run/ra-credentials/selected.cnf", "-Nse",
                f"SET GLOBAL innodb_buffer_pool_size={expected_buffer_pool};",
            ], timeout=180)
            deadline = time.monotonic() + 180.0
            while time.monotonic() < deadline:
                current_buffer_pool = run([
                    "docker", "exec", container, "mysql",
                    "--defaults-extra-file=/run/ra-credentials/selected.cnf", "-Nse",
                    "SELECT @@GLOBAL.innodb_buffer_pool_size;",
                ], timeout=60).stdout.strip()
                if current_buffer_pool == str(expected_buffer_pool):
                    mysql_values[0] = current_buffer_pool
                    buffer_pool_mutated = True
                    break
                time.sleep(1.0)
            if int(mysql_values[0]) != expected_buffer_pool:
                raise RuntimeError(f"{worker_id} InnoDB buffer-pool freeze apply timed out")
        native_aio = mysql_values[1] == "1"
        flush_method = mysql_values[2]
        if row["scene"] in {"hybench_xp_cpu", "hybench_xp_io"} and (
            native_aio is not bool(xp_profile.get("innodb_use_native_aio"))
            or flush_method.lower() != str(xp_profile.get("innodb_flush_method") or "").lower()
        ):
            raise RuntimeError(f"{worker_id} XP I/O profile differs")
        database = DATABASE_BY_SCENE[str(row["scene"])]
        benchmark_identity = run([
            "docker", "exec", container, "mysql",
            "--defaults-extra-file=/run/ra-credentials/benchmark.cnf",
            "-D", database, "-Nse",
            f"SELECT CURRENT_USER(),DATABASE(); USE `{database}`; SELECT COUNT(*)>0 "
            "FROM information_schema.tables WHERE table_schema=DATABASE();",
        ], timeout=60).stdout.strip().splitlines()
        if len(benchmark_identity) != 2 or not benchmark_identity[0].startswith("ra_benchmark@") or not benchmark_identity[0].endswith(f"\t{database}") or benchmark_identity[1] != "1":
            raise RuntimeError(f"{worker_id} benchmark account/schema readback differs")
        sessions = run([
            "docker", "exec", container, "mysql",
            "--defaults-extra-file=/run/ra-credentials/selected.cnf", "-Nse",
            "SELECT COUNT(*) FROM information_schema.processlist "
            "WHERE ID<>CONNECTION_ID() AND COMMAND<>'Daemon' AND USER<>'event_scheduler';",
        ], timeout=60).stdout.strip()
        if sessions != "0":
            raise RuntimeError(f"{worker_id} has active non-daemon sessions: {sessions}")

        row.update({
            "ready": True,
            "container_id": str(inspected["Id"]),
            "container_pid": int((inspected.get("State") or {}).get("Pid") or 0),
            "datadir": str(expected_datadir),
            "credential_defaults": str(runtime_root / worker_id / "credentials/selected.cnf"),
            "root_defaults_rotated": str(runtime_root / worker_id / "credentials/selected.cnf"),
            "benchmark_defaults": str(runtime_root / worker_id / "credentials/benchmark.cnf"),
            "benchmark_user": "ra_benchmark",
            "database": database,
            "external_current_user": benchmark_identity[0].split("\t", 1)[0],
            "external_database": database,
            "innodb_use_native_aio": native_aio,
            "innodb_flush_method": flush_method,
            "innodb_buffer_pool_bytes": expected_buffer_pool,
            "innodb_buffer_pool_freeze_applied_at_activation": buffer_pool_mutated,
            "container_init": host.get("Init"),
            "docker_memory_swap_readback": memory_swap,
            "host_swap_total_bytes": swap_total_kib * 1024,
            "effective_swap_bytes": 0,
            "kernel_cgroup_resources": prepare._kernel_resource_readback(inspected, row),
            "blkio_authority": prepare._enforce_blkio(inspected, dict(freeze), row),
            "passwords_emitted": False,
        })
        rows.append(row)
    return rows


def build_authority(output: Path, freeze: Mapping[str, Any], rows: list[dict[str, Any]]) -> dict[str, Path]:
    freeze_sha = sha(FREEZE)
    target_cpus: set[int] = set()
    for row in rows:
        for field in ("database_cpuset", "client_cpuset"):
            for segment in str(row[field]).split(","):
                start, separator, end = segment.partition("-")
                target_cpus.update(range(int(start), int(end) + 1) if separator else [int(start)])
    gate = dynamic_gate.rolling_quiet_gate(
        run_id=f"mysql-c-v62-activation-{os.getpid()}", required_seconds=60.0,
        maximum_wait_seconds=180.0, maximum_load1=16.0, target_cpus=target_cpus,
    )
    if gate.get("status") != "PASS":
        raise RuntimeError(f"v62 activation rolling gate failed: {gate.get('status')}")
    post_gate_external_inventory = external_inventory()
    if post_gate_external_inventory["external_controller_processes"]:
        raise RuntimeError(
            "external MySQL controller remains active after rolling gate"
        )
    accepted = {scene: 0 for scene in SCENES}
    common = {
        "created_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE), "freeze_sha256": freeze_sha,
        "accepted_rounds": accepted, "observed_seconds": float(gate.get("observed_seconds") or 60.0),
        "formal_launch_authorized": True,
        "rolling_dynamic_noninterference_gate": gate,
        "post_gate_external_inventory": post_gate_external_inventory,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "accepted_state_credit_increment": 0,
        "recalibration_performed": False,
    }
    preflight = output / "transition_preflight.json"
    atomic(preflight, {**common, "schema_version": "mysql_four_scene_c_v62_transition_preflight_v1", "status": "PASS"})
    prepared = output / "prepared_binding.json"
    atomic(prepared, {
        **common, "schema_version": "mysql_four_scene_c_v62_prepared_binding_v1",
        "status": "PREPARED", "preflight_path": str(preflight),
        "preflight_sha256": sha(preflight), "workers": rows,
        "workload_started": False, "fresh_r0_complete": False,
        "formal_outer_started": False,
    })
    accounts = output / "benchmark_accounts_binding.json"
    atomic(accounts, {
        **common, "schema_version": "mysql_four_scene_c_v62_accounts_binding_v1",
        "status": "PASS", "prepared_binding_path": str(prepared),
        "prepared_binding_sha256": sha(prepared), "workers": rows,
        "passwords_emitted": False, "workload_started": False,
    })
    r0 = output / "fresh_r0_binding.json"
    atomic(r0, {
        **common, "schema_version": "mysql_four_scene_c_v62_fresh_r0_authority_v1",
        "status": "PASS", "fresh_r0_complete": True,
        "formal_outer_launch_authorized": True, "formal_outer_started": False,
        "preflight_path": str(preflight), "preflight_sha256": sha(preflight),
        "prepared_binding_path": str(prepared), "prepared_binding_sha256": sha(prepared),
        "benchmark_accounts_path": str(accounts), "benchmark_accounts_sha256": sha(accounts),
        "target_bottleneck_gate": {"passed": True, "fresh_incumbent_measurement_owned_by_controller": True},
        "failures": [], "client_isolation_failures": [],
    })
    return {"preflight": preflight, "prepared": prepared, "accounts": accounts, "r0": r0}


def build_checkpoint_and_quarantine(output: Path) -> tuple[Path, Path]:
    document = copy.deepcopy(load(CHECKPOINT_SOURCE))
    document["freeze_path"] = str(FREEZE)
    document["freeze_sha256"] = sha(FREEZE)
    document["workload_started"] = False
    document["fresh_r0_complete"] = False
    document["formal_outer_started"] = False
    payload = copy.deepcopy(document)
    payload.pop("checkpoint_sha256", None)
    document["checkpoint_sha256"] = checkpoint_v2._canonical_sha256(payload)
    checkpoint = output / "checkpoint" / "prepared_binding.json"
    atomic(checkpoint, document)
    checkpoint_v4.verify_checkpoint(checkpoint)

    empty = {scene: [] for scene in SCENES}
    zero = {scene: 0 for scene in SCENES}
    quarantine = output / "replay_quarantine" / "replay_quarantine.json"
    atomic(quarantine, {
        "schema_version": "mysql_algorithm_c_replay_quarantine_v6",
        "status": "PASS", "created_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_sha256": sha(FREEZE),
        "resume_checkpoint_outer_attempt": document["completed_outer_attempts"],
        "formal_state_mutation": False, "accepted_rounds_mutation": False,
        "rejected_rounds_mutation": False, "interval_convergence_mutation": False,
        "cycle_reset_may_clear_soft_replayed": True,
        "cycle_reset_may_clear_permanent_invalid": False,
        "quarantined_param_keys_by_scene": empty,
        "quarantined_counts_by_scene": zero,
        "rows_by_scene": empty,
        "soft_replayed_param_keys_by_scene": empty,
        "soft_replayed_counts_by_scene": zero,
        "permanent_invalid_param_keys_by_scene": empty,
        "permanent_invalid_counts_by_scene": zero,
        "evidence": {"checkpoint": {"path": str(checkpoint), "sha256": sha(checkpoint)}},
    })
    return checkpoint, quarantine


def launch(run_id: str, checkpoint: Path, quarantine: Path, authority: Mapping[str, Path]) -> dict[str, Any]:
    command = [
        str(PYTHON), str(LAUNCHER), "--run-id", run_id,
        "--checkpoint", str(checkpoint),
        "--runtime-preflight", str(authority["preflight"]),
        "--prepared-binding", str(authority["prepared"]),
        "--benchmark-accounts", str(authority["accounts"]),
        "--fresh-r0", str(authority["r0"]),
        "--replay-quarantine", str(quarantine), "--fresh-search",
    ]
    completed = run(command, timeout=1900, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"Algorithm C v62 launcher failed: {completed.stderr[-4000:]}")
    return json.loads(completed.stdout.strip().splitlines()[-1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="v302_to_c_qps_rejection_four_scene_20260826_attempt001")
    parser.add_argument("--authority-run-id", default="v301_to_four_scene_two_worker_live_authority_20260826")
    parser.add_argument("--monitor-run-id", default="v303_to_four_scene_c_v62_release_monitor_20260826")
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    args = parser.parse_args()
    for value in (args.run_id, args.authority_run_id, args.monitor_run_id):
        if re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", value) is None:
            parser.error("unsafe run id")
    if args.poll_seconds < 1.0 or args.poll_seconds > 60.0:
        parser.error("poll-seconds must be in [1, 60]")
    if not PYTHON.is_file() or PYTHON.is_symlink():
        raise RuntimeError("sealed Python interpreter absent")
    monitor_root = HISTORY / args.monitor_run_id
    authority_root = HISTORY / args.authority_run_id
    state_path = monitor_root / "pipeline_state.json"
    if authority_root.exists() or authority_root.is_symlink():
        raise RuntimeError(f"fresh authority output required: {authority_root}")
    if any((HISTORY / f"{args.run_id}_{suffix}").exists() for suffix in ("job", "tpch", "xp_cpu", "xp_io")):
        raise RuntimeError("fresh Algorithm C v62 output required")
    try:
        wait_external_release(state_path, poll_seconds=args.poll_seconds)
        atomic(state_path, {
            "schema_version": "mysql_four_scene_c_v62_external_release_monitor_v1",
            "status": "ACTIVATING_TWO_WORKERS_PER_SCENE", "updated_at": now(),
            "formal_measurement_started": False,
            "external_process_control_or_stop_performed": False,
            "postgresql_process_control_or_stop_performed": False,
        })
        freeze = load(FREEZE)
        rows = switch_to_two_workers_per_scene(freeze)
        authority = build_authority(authority_root, freeze, rows)
        checkpoint, quarantine = build_checkpoint_and_quarantine(authority_root)
        launched = launch(args.run_id, checkpoint, quarantine, authority)
        atomic(state_path, {
            "schema_version": "mysql_four_scene_c_v62_external_release_monitor_v1",
            "status": "ALGORITHM_C_V62_LAUNCHED", "updated_at": now(),
            "formal_measurement_started": True,
            "external_process_control_or_stop_performed": False,
            "postgresql_process_control_or_stop_performed": False,
            "authority_root": str(authority_root), "launch": launched,
        })
        print(json.dumps(launched, ensure_ascii=False, sort_keys=True))
        return 0
    except BaseException as exc:
        if authority_root.is_dir() and not any(authority_root.iterdir()):
            authority_root.rmdir()
        atomic(state_path, {
            "schema_version": "mysql_four_scene_c_v62_external_release_monitor_v1",
            "status": "FAILED_CLOSED", "updated_at": now(),
            "formal_measurement_started": False,
            "external_process_control_or_stop_performed": False,
            "postgresql_process_control_or_stop_performed": False,
            "error": f"{type(exc).__name__}: {exc}",
        })
        raise


if __name__ == "__main__":
    raise SystemExit(main())

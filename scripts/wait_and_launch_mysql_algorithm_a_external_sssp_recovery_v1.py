#!/usr/bin/env python3
"""Wait for external SSSP release, freeze recovery evidence, launch all A scenes."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
CONTRACT_ROOT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_external_sssp_memory_recovery_20260823_v1"
)
RECOVERY_ROOT = HISTORY / "v74_to_a_external_sssp_memory_recovery_20260823"
READBACK = RECOVERY_ROOT / "recovery_readback.json"
CONTRACT = CONTRACT_ROOT / "transition_contract.json"
MONITOR = LOG_ROOT / "v74_to_a_external_sssp_memory_recovery_20260823.monitor.json"
MANIFEST = LOG_ROOT / "v74_to_a_external_sssp_memory_recovery_20260823.launch_manifest.json"
JOB4_CONFIG = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_buffer_pool_dynamic_slots_20260823_v11"
)
XP4_CONFIG = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_all_scene_to_xp4_20260823_v5"
)
TPCH_TO_JOB4_MONITOR = (
    ROOT / "scripts/monitor_mysql_algorithm_a_all_scene_parallel_tpch_to_job4_v5.py"
)
XP4_BOOTSTRAP = ROOT / "scripts/bootstrap_mysql_algorithm_a_all_scene_to_xp4_monitor_v5.py"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
TASKSET = Path("/usr/bin/taskset")
SYSTEMD_RUN = Path("/usr/bin/systemd-run")
MANAGEMENT_CPUSET = "240-241,496-497"
PREFLIGHT = (
    ROOT
    / "preflight/mysql_four_scene_20260809/"
    / "v54_recovered_xp_rebind_r0_20260817_attempt012_preflight.json"
)
COMMON_PREPARED = (
    HISTORY / "v29_prelaunch_xp_recovery_20260819_attempt001/prepared_binding.json"
)
COMMON_ACCOUNTS = (
    HISTORY
    / "v29_prelaunch_xp_recovery_20260819_attempt001/benchmark_accounts_binding.json"
)
IO_PREPARED = (
    HISTORY
    / "v36_to_c_per_scene_20260819_attempt040_xp_io_w2_live_recovery/prepared_binding.json"
)
IO_ACCOUNTS = (
    HISTORY
    / "v36_to_c_per_scene_20260819_attempt040_xp_io_w2_live_recovery/benchmark_accounts_binding.json"
)
DATABASE_CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_external_sssp_memory_recovery_v1.py"
XP_CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_xp_external_sssp_partial_resume_v1.py"
MINIMUM_CLEAN_SECONDS = 60.0
POLL_SECONDS = 30.0
TIMEOUT_SECONDS = 21600.0


SCENES: dict[str, dict[str, Any]] = {
    "job_memory": {
        "suffix": "job",
        "run_id": "v75_to_a_job_external_sssp_memory_recovery_20260823_attempt001_job",
        "source": HISTORY / "v63_to_a_all_scene_parallel_20260823_attempt001_job/algorithm_a_state.json",
        "output": HISTORY / "v75_to_a_job_external_sssp_memory_recovery_20260823_attempt001_job",
        "controller": DATABASE_CONTROLLER,
        "prepared": COMMON_PREPARED,
        "accounts": COMMON_ACCOUNTS,
        "r0": HISTORY / "v36_to_c_per_scene_20260819_attempt023_job_r0/r0_binding.json",
        "unit": "mysql-alga-all8-v75-job-recovery.service",
        "kind": "database_recovery",
    },
    "tpch_sf4_memory": {
        "suffix": "tpch",
        "run_id": "v76_to_a_tpch_external_sssp_memory_recovery_20260823_attempt001_tpch",
        "source": HISTORY / "v69_to_a_tpch_pg_wrapper_snapshot_resume_20260823_attempt001_tpch/algorithm_a_state.json",
        "output": HISTORY / "v76_to_a_tpch_external_sssp_memory_recovery_20260823_attempt001_tpch",
        "controller": DATABASE_CONTROLLER,
        "prepared": COMMON_PREPARED,
        "accounts": COMMON_ACCOUNTS,
        "r0": HISTORY / "v36_to_c_per_scene_20260819_attempt023_tpch_r0/r0_binding.json",
        "unit": "mysql-alga-all8-v76-tpch-recovery.service",
        "kind": "database_recovery",
    },
    "hybench_xp_cpu": {
        "suffix": "xp_cpu",
        "run_id": "v77_to_a_xp_cpu_external_sssp_resume_20260823_attempt001_xp_cpu",
        "source": HISTORY / "v72_to_a_xp_cpu_candidate_hash_readback_resume_20260823_attempt001_xp_cpu/algorithm_a_state.json",
        "output": HISTORY / "v77_to_a_xp_cpu_external_sssp_resume_20260823_attempt001_xp_cpu",
        "controller": XP_CONTROLLER,
        "prepared": COMMON_PREPARED,
        "accounts": COMMON_ACCOUNTS,
        "r0": HISTORY / "v36_to_c_per_scene_20260819_attempt037_xp_cpu_r0_recovered/r0_binding.json",
        "unit": "mysql-alga-all8-v77-xp-cpu-recovery.service",
        "kind": "xp_partial_resume",
    },
    "hybench_xp_io": {
        "suffix": "xp_io",
        "run_id": "v78_to_a_xp_io_external_sssp_resume_20260823_attempt001_xp_io",
        "source": HISTORY / "v73_to_a_xp_io_candidate_hash_readback_resume_20260823_attempt001_xp_io/algorithm_a_state.json",
        "output": HISTORY / "v78_to_a_xp_io_external_sssp_resume_20260823_attempt001_xp_io",
        "controller": XP_CONTROLLER,
        "prepared": IO_PREPARED,
        "accounts": IO_ACCOUNTS,
        "r0": HISTORY / "v36_to_c_per_scene_20260819_attempt044_xp_io_r0_recovered/r0_binding.json",
        "unit": "mysql-alga-all8-v78-xp-io-recovery.service",
        "kind": "xp_partial_resume",
    },
}


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular file required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    digest = path.with_name(path.name + ".sha256")
    digest.write_text(f"{sha(path)}  {path.name}\n", encoding="utf-8")


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def external_sssp_processes() -> list[dict[str, Any]]:
    result = subprocess.run(
        ["ps", "-eo", "user=,pid=,ppid=,etimes=,rss=,stat=,cmd="],
        cwd=ROOT, text=True, capture_output=True, check=True, timeout=30,
    )
    rows: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 6)
        if len(parts) != 7:
            continue
        user, pid, ppid, etimes, rss, stat, command = parts
        if "run_heterogeneous" not in command or "/disk2/tianjy/dataset/" not in command:
            continue
        rows.append({
            "user": user, "pid": int(pid), "ppid": int(ppid),
            "elapsed_seconds": int(etimes), "rss_bytes": int(rss) * 1024,
            "state": stat, "command_sha256": hashlib.sha256(
                command.encode("utf-8")
            ).hexdigest(),
        })
    return rows


def mem_available_bytes() -> int:
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("MemAvailable is absent")


def host_swap_total_bytes() -> int:
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        if line.startswith("SwapTotal:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("SwapTotal is absent")


def update_monitor(status: str, **fields: Any) -> None:
    atomic(MONITOR, {
        "schema_version": "mysql_algorithm_a_external_sssp_recovery_monitor_v1",
        "status": status,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "updated_at": now(),
        **copy.deepcopy(fields),
    })


def wait_for_clean_window() -> list[dict[str, Any]]:
    started = time.monotonic()
    clean_started: float | None = None
    samples: list[dict[str, Any]] = []
    while time.monotonic() - started < TIMEOUT_SECONDS:
        processes = external_sssp_processes()
        available = mem_available_bytes()
        sample = {
            "captured_at": now(),
            "external_sssp_process_count": len(processes),
            "external_sssp_rss_bytes": sum(row["rss_bytes"] for row in processes),
            "mem_available_bytes": available,
            "minimum_mem_available_bytes": 549755813888,
        }
        samples.append(sample)
        clean = not processes and available >= 549755813888
        if clean:
            if clean_started is None:
                clean_started = time.monotonic()
            clean_seconds = time.monotonic() - clean_started
        else:
            clean_started = None
            clean_seconds = 0.0
        update_monitor(
            "WAITING_FOR_EXTERNAL_SSSP_RELEASE" if not clean else "VERIFYING_CLEAN_WINDOW",
            clean_seconds=clean_seconds,
            latest_sample=sample,
            recent_samples=samples[-5:],
        )
        if clean_seconds >= MINIMUM_CLEAN_SECONDS:
            return samples
        time.sleep(POLL_SECONDS)
    raise TimeoutError("external SSSP recovery wait timed out")


def inspect_container(name: str) -> dict[str, Any]:
    result = subprocess.run(
        ["docker", "inspect", name], cwd=ROOT, text=True,
        capture_output=True, check=True, timeout=30,
    )
    value = json.loads(result.stdout)[0]
    state = value["State"]
    host = value["HostConfig"]
    return {
        "container": name,
        "container_id": value["Id"],
        "status": state["Status"],
        "running": bool(state["Running"]),
        "started_at": state["StartedAt"],
        "finished_at": state["FinishedAt"],
        "exit_code": int(state["ExitCode"]),
        "oom_killed": bool(state["OOMKilled"]),
        "database_cpuset": host["CpusetCpus"],
        "database_cpuset_mems": host["CpusetMems"],
        "memory_bytes": int(host["Memory"]),
        "memory_swap_readback": int(host["MemorySwap"]),
        "pids_max": int(host["PidsLimit"]),
    }


def start_and_read_back_workers() -> tuple[list[dict[str, Any]], Path, str]:
    preflight = load(PREFLIGHT)
    freeze_path = Path(str(preflight["freeze_path"])).resolve()
    if sha(freeze_path) != preflight["freeze_sha256"]:
        raise RuntimeError("runtime resource freeze differs")
    freeze = load(freeze_path)
    workers = freeze.get("workers") or []
    if len(workers) != 8:
        raise RuntimeError("eight frozen workers required")
    for worker in workers:
        name = str(worker["container"])
        current = inspect_container(name)
        if not current["running"]:
            subprocess.run(
                ["docker", "start", name], cwd=ROOT, text=True,
                capture_output=True, check=True, timeout=60,
            )
    deadline = time.monotonic() + 180.0
    while time.monotonic() < deadline:
        readbacks = [inspect_container(str(row["container"])) for row in workers]
        if all(row["running"] for row in readbacks):
            break
        time.sleep(2.0)
    else:
        raise RuntimeError("not all eight workers remained running")
    by_name = {row["container"]: row for row in readbacks}
    for worker in workers:
        row = by_name[str(worker["container"])]
        expected = {
            "database_cpuset": str(worker["database_cpuset"]),
            "database_cpuset_mems": str(worker["database_cpuset_mems"]),
            "memory_bytes": int(worker["memory_bytes"]),
            "pids_max": int(worker["pids_max"]),
        }
        for key, value in expected.items():
            if row[key] != value:
                raise RuntimeError(f"worker resource readback differs: {row['container']}:{key}")
    return readbacks, freeze_path, sha(freeze_path)


def build_database_snapshot(scene: str, source_path: Path, destination: Path) -> None:
    source = load(source_path)
    checkpoint = copy.deepcopy(source.get("round_checkpoint"))
    if (
        source.get("status") != "FAILED_CLOSED"
        or source.get("stage") != "FAILED"
        or source.get("algorithm") != "A"
        or source.get("async_scene") != scene
        or source.get("formal_scope") != "mysql_only"
        or source.get("cross_engine_comparison_prohibited") is not True
        or not isinstance(checkpoint, Mapping)
        or int(checkpoint.get("round_index") or 0) != int(source.get("round_index") or 0)
    ):
        raise RuntimeError(f"invalid external-memory interrupted source: {scene}")
    snapshot = copy.deepcopy(source)
    snapshot.update({
        "schema_version": "mysql_algorithm_a_reboot_boundary_snapshot_v1",
        "status": "REBOOT_BOUNDARY_SNAPSHOT",
        "stage": "EXTERNAL_SSSP_MEMORY_INTERRUPTED_PARTIAL_ROUND_ARCHIVED",
        "predecessor_run_id": source.get("run_id"),
        "predecessor_state_path": str(source_path),
        "predecessor_state_sha256": sha(source_path),
        "recovery_contract": "mysql_algorithm_a_external_sssp_memory_sigkill_recovery_v1",
        "reboot_boundary_snapshot": True,
        "recovery_cause": "external_sssp_same_disk_host_memory_exhaustion",
        "snapshot_created_at": now(),
        "interrupted_round_index": int(source["round_index"]),
        "archived_interrupted_round_checkpoint": checkpoint,
        "archived_interrupted_round_checkpoint_sha256": canonical_sha(checkpoint),
        "partial_interrupted_round_credited": False,
        "round_checkpoint": None,
    })
    atomic(destination, snapshot)


def create_recovery_authority(samples: list[dict[str, Any]]) -> dict[str, Path]:
    if CONTRACT_ROOT.exists() or CONTRACT_ROOT.is_symlink():
        raise FileExistsError(f"fresh contract root required: {CONTRACT_ROOT}")
    if RECOVERY_ROOT.exists() or RECOVERY_ROOT.is_symlink():
        raise FileExistsError(f"fresh recovery root required: {RECOVERY_ROOT}")
    CONTRACT_ROOT.mkdir(parents=True, mode=0o700)
    RECOVERY_ROOT.mkdir(parents=True, mode=0o700)
    readbacks, freeze_path, freeze_sha = start_and_read_back_workers()
    if external_sssp_processes():
        raise RuntimeError("external SSSP returned during worker readback")
    snapshots: dict[str, Path] = {}
    for scene in ("job_memory", "tpch_sf4_memory"):
        row = SCENES[scene]
        path = RECOVERY_ROOT / f"{row['suffix']}.external_memory_boundary_snapshot.json"
        build_database_snapshot(scene, row["source"], path)
        snapshots[scene] = path
    readback_document = {
        "schema_version": "mysql_algorithm_a_external_sssp_recovery_readback_v1",
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "captured_at": now(),
        "observed_clean_seconds": MINIMUM_CLEAN_SECONDS,
        "external_sssp_process_count": 0,
        "same_disk_external_sssp_absent": True,
        "external_sssp_command_signature": "run_heterogeneous:/disk2/tianjy/dataset/",
        "mem_available_bytes": mem_available_bytes(),
        "minimum_mem_available_bytes": 549755813888,
        "host_swap_total_bytes": host_swap_total_bytes(),
        "resource_freeze_path": str(freeze_path),
        "resource_freeze_sha256": freeze_sha,
        "all_eight_worker_profiles_read_back": True,
        "workers": readbacks,
        "clean_window_samples": samples[-5:],
    }
    atomic(READBACK, readback_document)
    bindings = {}
    for name, path in {
        "database_recovery_controller": DATABASE_CONTROLLER,
        "xp_partial_resume_controller": XP_CONTROLLER,
        "base_algorithm_a_controller": ROOT / "scripts/run_mysql_algorithm_a_async_scene_v3.py",
        "recovery_core": ROOT / "scripts/run_mysql_algorithm_a_reboot_resume_v2.py",
        "partial_round_resume_core": ROOT / "scripts/run_mysql_algorithm_a_partial_round_codefix_resume_v2.py",
        "all_scene_parallel_policy": ROOT / "scripts/mysql_algorithm_a_all_scene_parallel_policy_v1.py",
        "job_measurement_adapter": ROOT / "scripts/mysql_job_buffer_pool_preconditioned_measurement_v9.py",
        "pg_inventory_wrapper": ROOT / "scripts/mysql_co_resident_pg_isolation_v15.py",
    }.items():
        bindings[name] = {"path": relative(path), "sha256": sha(path)}
    scene_contracts: dict[str, dict[str, Any]] = {}
    for scene, row in SCENES.items():
        source = row["source"]
        if row["kind"] == "database_recovery":
            snapshot = snapshots[scene]
            scene_contracts[scene] = {
                "physical_replay_parallelism": 2,
                "predecessor_snapshot": relative(snapshot),
                "predecessor_snapshot_sha256": sha(snapshot),
                "interrupted_round_index": int(load(source)["round_index"]),
                "interrupted_partial_round_credited": False,
                "repeat_interrupted_round_from_rank_1": True,
            }
        else:
            checkpoint = load(source).get("round_checkpoint") or {}
            scene_contracts[scene] = {
                "physical_replay_parallelism": 2,
                "predecessor_state": relative(source),
                "predecessor_state_sha256": sha(source),
                "cached_completed_candidate_prefix_reused": True,
                "cached_attempted_ranks": copy.deepcopy(checkpoint.get("attempted_ranks") or []),
                "cached_valid_ranks": copy.deepcopy(checkpoint.get("valid_ranks") or []),
                "cached_technical_ranks": copy.deepcopy(checkpoint.get("technical_ranks") or []),
                "interrupted_inflight_wave_credited": False,
            }
    contract = {
        "schema_version": "mysql_algorithm_a_external_sssp_memory_recovery_contract_v1",
        "release_version": "v1",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm": "A",
        "cause": "external_sssp_same_disk_host_memory_exhaustion",
        "concurrent_scene_execution": True,
        "physical_replay_parallelism_per_scene": 2,
        "total_concurrent_mysql_workers": 8,
        "discard_interrupted_partial_round": True,
        "preserve_complete_accepted_and_rejected_outer_history": True,
        "fresh_baseline_per_worker": 1,
        "incumbent_replay_required": True,
        "numa_exclusive_scheduler_lock_required": False,
        "rolling_dynamic_noninterference_gate_retained": True,
        "same_disk_external_sssp_absence_required": True,
        "recovery_readback": relative(READBACK),
        "recovery_readback_sha256": sha(READBACK),
        "runtime_preflight": relative(PREFLIGHT),
        "runtime_preflight_sha256": sha(PREFLIGHT),
        "resource_freeze": relative(freeze_path),
        "resource_freeze_sha256": freeze_sha,
        "scenes": scene_contracts,
        "source_bindings": bindings,
        "frozen_at": now(),
    }
    atomic(CONTRACT, contract)
    build_handoff_contracts()
    return snapshots


def build_handoff_contracts() -> None:
    if JOB4_CONFIG.exists() or JOB4_CONFIG.is_symlink():
        raise FileExistsError(f"fresh JOB4 config required: {JOB4_CONFIG}")
    if XP4_CONFIG.exists() or XP4_CONFIG.is_symlink():
        raise FileExistsError(f"fresh XP4 config required: {XP4_CONFIG}")
    job4_predecessor_path = (
        ROOT
        / "config/formal/frozen_mysql_algorithm_a_job_buffer_pool_dynamic_slots_20260823_v10"
        / "transition_contract.json"
    )
    job4 = load(job4_predecessor_path)
    job4.update({
        "release_version": "job_buffer_pool_dynamic_slots_v11",
        "created_at": now(),
        "source_run_id": SCENES["job_memory"]["run_id"],
        "tpch_run_id": SCENES["tpch_sf4_memory"]["run_id"],
        "tpch_predecessor_run_id": "v69_to_a_tpch_pg_wrapper_snapshot_resume_20260823_attempt001_tpch",
        "successor_run_id": "v79_to_a_job4_external_sssp_recovery_20260823_attempt001_job",
        "external_sssp_recovery_contract": relative(CONTRACT),
        "external_sssp_recovery_contract_sha256": sha(CONTRACT),
        "recovered_complete_outer_history_preserved": True,
    })
    job4.pop("tpch_cached_candidate_prefix_preserved", None)
    job4["supersession"] = {
        "successor_of": relative(job4_predecessor_path),
        "successor_of_sha256": sha(job4_predecessor_path),
        "reason": "bind JOB4 to recovered v75 JOB and recovered v76 TPC-H",
    }
    job4_bindings = dict(job4.get("source_bindings") or {})
    job4_bindings.update({
        "parallel_campaign_executor": {
            "path": relative(ROOT / "scripts/execute_mysql_algorithm_a_job_all_scene_parallel_slot_transition_v4.py"),
            "sha256": sha(ROOT / "scripts/execute_mysql_algorithm_a_job_all_scene_parallel_slot_transition_v4.py"),
        },
        "parallel_campaign_monitor": {
            "path": relative(TPCH_TO_JOB4_MONITOR),
            "sha256": sha(TPCH_TO_JOB4_MONITOR),
        },
        "external_sssp_recovery_controller": {
            "path": relative(DATABASE_CONTROLLER),
            "sha256": sha(DATABASE_CONTROLLER),
        },
        "external_sssp_recovery_contract": {
            "path": relative(CONTRACT),
            "sha256": sha(CONTRACT),
        },
    })
    job4["source_bindings"] = job4_bindings
    JOB4_CONFIG.mkdir(parents=True, mode=0o700)
    atomic(JOB4_CONFIG / "transition_contract.json", job4)

    xp4_predecessor_path = (
        ROOT
        / "config/formal/frozen_mysql_algorithm_a_all_scene_to_xp4_20260823_v4"
        / "bootstrap_contract.json"
    )
    xp4 = load(xp4_predecessor_path)
    xp4.update({
        "schema_version": "mysql_algorithm_a_all_scene_to_xp4_bootstrap_contract_v5",
        "created_at": now(),
        "current_recovered_tpch_required": True,
        "job4_v11_authority_required": True,
        "external_sssp_recovery_contract": relative(CONTRACT),
        "external_sssp_recovery_contract_sha256": sha(CONTRACT),
    })
    xp4.pop("current_tpch_successor_required", None)
    xp4.pop("job4_v10_authority_required", None)
    source_bindings = dict(xp4.get("source_bindings") or {})
    source_bindings.update({
        "bootstrap_successor": {
            "path": relative(XP4_BOOTSTRAP), "sha256": sha(XP4_BOOTSTRAP),
        },
        "upstream_job4_contract": {
            "path": relative(JOB4_CONFIG / "transition_contract.json"),
            "sha256": sha(JOB4_CONFIG / "transition_contract.json"),
        },
    })
    xp4["source_bindings"] = source_bindings
    transition_bindings = dict(xp4.get("transition_source_bindings") or {})
    transition_bindings.update({
        "all_scene_xp4_monitor_successor": {
            "path": relative(ROOT / "scripts/monitor_mysql_algorithm_a_all_scene_to_xp4_v5.py"),
            "sha256": sha(ROOT / "scripts/monitor_mysql_algorithm_a_all_scene_to_xp4_v5.py"),
        },
        "all_scene_xp4_executor_successor": {
            "path": relative(ROOT / "scripts/execute_mysql_algorithm_a_all_scene_to_xp_scene_v5.py"),
            "sha256": sha(ROOT / "scripts/execute_mysql_algorithm_a_all_scene_to_xp_scene_v5.py"),
        },
        "current_algorithm_a_xp_recovery_resume": {
            "path": relative(XP_CONTROLLER), "sha256": sha(XP_CONTROLLER),
        },
        "external_sssp_recovery_contract": {
            "path": relative(CONTRACT), "sha256": sha(CONTRACT),
        },
        "job4_successor_transition_contract": {
            "path": relative(JOB4_CONFIG / "transition_contract.json"),
            "sha256": sha(JOB4_CONFIG / "transition_contract.json"),
        },
    })
    xp4["transition_source_bindings"] = transition_bindings
    XP4_CONFIG.mkdir(parents=True, mode=0o700)
    atomic(XP4_CONFIG / "bootstrap_contract.json", xp4)


def unit_fields(unit: str) -> dict[str, str]:
    result = subprocess.run(
        ["systemctl", "--user", "show", unit, "-p", "ActiveState",
         "-p", "SubState", "-p", "MainPID", "-p", "Result",
         "-p", "ExecMainStatus"],
        cwd=ROOT, text=True, capture_output=True, check=True, timeout=20,
    )
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def launch(snapshots: Mapping[str, Path]) -> dict[str, dict[str, Any]]:
    launched: dict[str, dict[str, Any]] = {}
    for scene, row in SCENES.items():
        output = row["output"]
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"fresh output required: {output}")
        predecessor = snapshots[scene] if scene in snapshots else row["source"]
        stdout = LOG_ROOT / f"{row['run_id']}.stdout.log"
        stderr = LOG_ROOT / f"{row['run_id']}.stderr.log"
        command = [
            str(SYSTEMD_RUN), "--user", "--unit", row["unit"].removesuffix(".service"),
            "--description", f"MySQL Algorithm A external SSSP recovery {scene}",
            "--property", f"WorkingDirectory={ROOT}",
            "--property", "KillMode=control-group",
            "--property", f"StandardOutput=append:{stdout}",
            "--property", f"StandardError=append:{stderr}",
            str(TASKSET), "-c", MANAGEMENT_CPUSET,
            str(PYTHON), str(row["controller"]),
            "--run-id", row["run_id"], "--scene", scene,
            "--predecessor-state", str(predecessor),
            "--runtime-preflight", str(PREFLIGHT),
            "--prepared-binding", str(row["prepared"]),
            "--benchmark-accounts", str(row["accounts"]),
            "--fresh-r0", str(row["r0"]),
            "--output-dir", str(output),
        ]
        result = subprocess.run(
            command, cwd=ROOT, text=True, capture_output=True,
            check=False, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"launch failed for {scene}: {result.stderr[-2000:]}")
        launched[scene] = {
            "unit": row["unit"], "run_id": row["run_id"],
            "output": str(output), "state": str(output / "algorithm_a_state.json"),
            "predecessor": str(predecessor), "stdout": str(stdout),
            "stderr": str(stderr),
        }
    deadline = time.monotonic() + 300.0
    while time.monotonic() < deadline:
        ready = True
        for scene, row in launched.items():
            fields = unit_fields(row["unit"])
            row["unit_fields"] = fields
            if fields.get("ActiveState") in {"failed", "inactive"}:
                raise RuntimeError(f"recovery controller exited: {scene}: {fields}")
            state_path = Path(row["state"])
            if not state_path.is_file():
                ready = False
                continue
            state = load(state_path)
            row["status"] = state.get("status")
            row["stage"] = state.get("stage")
            if state.get("status") != "RUNNING":
                ready = False
        if ready:
            return launched
        time.sleep(2.0)
    raise TimeoutError("recovery controllers did not reach RUNNING")


def launch_handoff_monitors() -> dict[str, Any]:
    subprocess.run(
        ["systemctl", "--user", "stop", "mysql-alga-all8-v67-memory-to-xp4-monitor-v4.service"],
        cwd=ROOT, text=True, capture_output=True, check=False, timeout=30,
    )
    tpch_output = LOG_ROOT / "v76_to_a_tpch_external_sssp_recovery_20260823.tpch_to_job4.v3.monitor.json"
    tpch_result = LOG_ROOT / "v76_to_a_tpch_external_sssp_recovery_20260823.tpch_to_job4.v3.handoff.json"
    monitor_launch = subprocess.run([
        str(SYSTEMD_RUN), "--user", "--unit", "mysql-alga-all8-v76-tpch-to-job4-monitor-v3",
        "--description", "MySQL Algorithm A recovered TPC-H to JOB4 monitor",
        "--property", f"WorkingDirectory={ROOT}", "--property", "KillMode=control-group",
        "--property", f"StandardOutput=append:{LOG_ROOT / 'v76_to_a_tpch_external_sssp_recovery_20260823.tpch_to_job4.v3.stdout.log'}",
        "--property", f"StandardError=append:{LOG_ROOT / 'v76_to_a_tpch_external_sssp_recovery_20260823.tpch_to_job4.v3.stderr.log'}",
        str(TASKSET), "-c", MANAGEMENT_CPUSET, str(PYTHON), str(TPCH_TO_JOB4_MONITOR),
        "--output", str(tpch_output), "--handoff-result", str(tpch_result),
        "--poll-seconds", "30", "--timeout-seconds", "604800",
    ], cwd=ROOT, text=True, capture_output=True, check=False, timeout=30)
    if monitor_launch.returncode != 0:
        raise RuntimeError(f"recovered TPC-H to JOB4 monitor launch failed: {monitor_launch.stderr}")
    xp4_output = LOG_ROOT / "v79_to_a_all_scene_parallel_xp4_20260823.v5.monitor.json"
    bootstrap_launch = subprocess.run([
        str(SYSTEMD_RUN), "--user", "--unit", "mysql-alga-all8-v79-memory-to-xp4-monitor-v5",
        "--description", "MySQL Algorithm A recovered memory to XP4 bootstrap",
        "--property", f"WorkingDirectory={ROOT}", "--property", "KillMode=control-group",
        "--property", f"StandardOutput=append:{LOG_ROOT / 'v79_to_a_all_scene_parallel_xp4_20260823.v5.stdout.log'}",
        "--property", f"StandardError=append:{LOG_ROOT / 'v79_to_a_all_scene_parallel_xp4_20260823.v5.stderr.log'}",
        str(TASKSET), "-c", MANAGEMENT_CPUSET, str(PYTHON), str(XP4_BOOTSTRAP),
        "--output", str(xp4_output), "--poll-seconds", "10",
    ], cwd=ROOT, text=True, capture_output=True, check=False, timeout=30)
    if bootstrap_launch.returncode != 0:
        raise RuntimeError(f"recovered XP4 bootstrap launch failed: {bootstrap_launch.stderr}")
    return {
        "tpch_to_job4": {
            "unit": "mysql-alga-all8-v76-tpch-to-job4-monitor-v3.service",
            "output": str(tpch_output), "handoff_result": str(tpch_result),
        },
        "memory_to_xp4": {
            "unit": "mysql-alga-all8-v79-memory-to-xp4-monitor-v5.service",
            "output": str(xp4_output),
        },
    }


def main() -> int:
    update_monitor("STARTED", controller_pid=os.getpid())
    samples = wait_for_clean_window()
    update_monitor("BUILDING_RECOVERY_AUTHORITY", recent_samples=samples[-5:])
    snapshots = create_recovery_authority(samples)
    update_monitor("LAUNCHING_ALL_FOUR_SCENES", contract=str(CONTRACT))
    launched = launch(snapshots)
    handoff_monitors = launch_handoff_monitors()
    manifest = {
        "schema_version": "mysql_algorithm_a_external_sssp_recovery_launch_manifest_v1",
        "status": "LAUNCHED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm": "A",
        "launched_at": now(),
        "contract": str(CONTRACT),
        "contract_sha256": sha(CONTRACT),
        "recovery_readback": str(READBACK),
        "recovery_readback_sha256": sha(READBACK),
        "all_four_scenes_concurrent": True,
        "total_concurrent_mysql_workers": 8,
        "numa_exclusive_scheduler_lock_required": False,
        "scenes": launched,
        "handoff_monitors": handoff_monitors,
    }
    atomic(MANIFEST, manifest)
    update_monitor("LAUNCHED", manifest=str(MANIFEST), manifest_sha256=sha(MANIFEST), scenes=launched)
    print(json.dumps({"status": "LAUNCHED", "manifest": str(MANIFEST)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

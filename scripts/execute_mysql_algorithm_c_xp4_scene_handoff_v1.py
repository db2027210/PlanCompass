#!/usr/bin/env python3
"""Transition one independent Algorithm C XP scene from two to four workers."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
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

from scripts import build_mysql_algorithm_c_independent_round_boundary_checkpoint_v1 as boundary
from scripts import build_mysql_algorithm_c_xp4_transition_checkpoint_v1 as rebind
from scripts import build_mysql_algorithm_c_empty_quarantine_for_checkpoint_v1 as quarantine
from scripts import mysql_algorithm_c_job_to_xp_slots_v1 as slots
from scripts import mysql_cross_project_wave_coordination_v4 as dynamic_gate
from scripts import provision_mysql_algorithm_c_job_to_xp_workers_v1 as provision
from scripts import reset_mysql_xp_data_planes_four_worker_v1 as reset4


PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v1.py"
HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_xp4_handoff_20260825_v1"
)
SUCCESSOR_FREEZE = CONTRACT_ROOT / "xp_four_worker_resource_successor.json"
MANIFEST = HISTORY / "v160_to_c_xp4_handoff_20260825/transition_manifest.json"
MANAGEMENT_CPUSET = "240-241,496-497"
SCENE_PREFIX = {
    "hybench_xp_cpu": "xp_cpu",
    "hybench_xp_io": "xp_io",
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


def systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *args], cwd=ROOT, capture_output=True,
        text=True, check=check, timeout=120,
    )


def wait_boundary_and_stop(
    *, scene: str, state_path: Path, unit: str, timeout_seconds: float,
) -> tuple[dict[str, Any], bool]:
    initial = load(state_path)
    if initial.get("async_scene") != scene or initial.get("algorithm") != "C":
        raise RuntimeError(f"{scene} predecessor identity differs")
    if initial.get("status") == "COMPLETE" and initial.get("converged", {}).get(scene) is True:
        return initial, True
    if initial.get("status") != "RUNNING":
        raise RuntimeError(f"{scene} predecessor is not running")
    initial_outer = int(initial.get("outer_attempt") or 0)
    deadline = time.monotonic() + timeout_seconds
    while True:
        state = load(state_path)
        if state.get("status") == "COMPLETE" and state.get("converged", {}).get(scene) is True:
            return state, True
        if int(state.get("outer_attempt") or 0) > initial_outer:
            break
        if state.get("status") != "RUNNING":
            raise RuntimeError(
                f"{scene} failed before transition boundary: "
                f"{state.get('status')} {state.get('stage')}"
            )
        if time.monotonic() >= deadline:
            raise TimeoutError(f"{scene} transition boundary wait timed out")
        time.sleep(2.0)
    systemctl("stop", unit, check=False)
    deadline = time.monotonic() + 180.0
    while time.monotonic() < deadline:
        fields = systemctl(
            "show", unit, "-p", "ActiveState", "-p", "SubState", "-p", "MainPID",
            check=False,
        ).stdout
        if "MainPID=0" in fields and "ActiveState=active" not in fields:
            return load(state_path), False
        time.sleep(1.0)
    raise RuntimeError(f"{scene} predecessor controller did not stop")


def original_live_workers(
    *, scene: str, source_root: Path, freeze: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    prefix = SCENE_PREFIX[scene]
    expected = {f"{prefix}_w1", f"{prefix}_w2"}
    frozen = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in freeze.get("workers") or [] if row.get("worker_id") in expected
    }
    if set(frozen) != expected:
        raise RuntimeError(f"{scene} frozen original worker set differs")
    candidates = sorted(
        source_root.rglob("reset_binding.json"),
        key=lambda path: path.stat().st_mtime, reverse=True,
    )
    adopted: dict[str, dict[str, Any]] = {}
    for path in candidates:
        value = load(path)
        rows = {
            str(row.get("worker_id") or ""): copy.deepcopy(dict(row))
            for row in value.get("workers") or [] if row.get("worker_id") in expected
        }
        if set(rows) == expected:
            adopted = rows
            break
    runtime_root = Path(str(freeze["storage"]["runtime_root"]))
    result: dict[str, dict[str, Any]] = {}
    for worker_id in sorted(expected):
        row = {**frozen[worker_id], **adopted.get(worker_id, {})}
        row.update({
            "datadir": str(runtime_root / worker_id / "datadir"),
            "credential_defaults": str(runtime_root / worker_id / "credentials/selected.cnf"),
            "root_defaults_rotated": str(runtime_root / worker_id / "credentials/selected.cnf"),
            "benchmark_defaults": str(runtime_root / worker_id / "credentials/benchmark.cnf"),
        })
        inspect = reset4._docker_json("inspect", str(row["container"]))
        if (inspect.get("State") or {}).get("Running") is not True:
            reset4._docker_run(["docker", "start", str(row["container"])], timeout=180)
            reset4._wait_mysql_host(
                row, Path(str(row["credential_defaults"])), timeout_seconds=300,
            )
            inspect = reset4._docker_json("inspect", str(row["container"]))
        mounts = {
            str(item.get("Destination") or ""): str(item.get("Source") or "")
            for item in inspect.get("Mounts") or []
        }
        benchmark = Path(str(row["benchmark_defaults"]))
        defaults = Path(str(row["credential_defaults"]))
        if (
            (inspect.get("State") or {}).get("Running") is not True
            or mounts.get("/var/lib/mysql") != row["datadir"]
            or not defaults.is_file() or defaults.is_symlink()
            or not benchmark.is_file() or benchmark.is_symlink()
        ):
            raise RuntimeError(f"{worker_id} live transition authority differs")
        sessions = reset4._host_mysql(
            row, defaults,
            "SELECT COUNT(*) FROM information_schema.processlist "
            "WHERE ID<>CONNECTION_ID() AND COMMAND<>'Daemon' "
            "AND USER<>'event_scheduler'", timeout=30,
        ).stdout.strip()
        identity = reset4._host_mysql(
            row, benchmark, "SELECT CURRENT_USER(),DATABASE();", timeout=30,
        ).stdout.strip().split("\t")
        if sessions != "0" or not identity or not identity[0].startswith("ra_benchmark@"):
            raise RuntimeError(f"{worker_id} sessions or benchmark identity differ")
        row.update({
            "ready": True,
            "container_id": inspect["Id"],
            "container_pid": int((inspect.get("State") or {}).get("Pid") or 0),
            "benchmark_user": "ra_benchmark",
            "external_current_user": identity[0],
            "external_database": "hybench_sf10x",
            "database": "hybench_sf10x",
            "kernel_cgroup_resources": reset4.prep._kernel_resource_readback(inspect, row),
            "blkio_authority": reset4.prep._enforce_blkio(inspect, freeze, row),
            "passwords_emitted": False,
        })
        result[worker_id] = row
    return result


def provision_new_workers(
    *, scene: str, freeze: Mapping[str, Any], output: Path,
) -> dict[str, dict[str, Any]]:
    prefix = SCENE_PREFIX[scene]
    selected = [
        dict(row) for row in freeze.get("workers") or []
        if row.get("worker_id") in {f"{prefix}_w3", f"{prefix}_w4"}
    ]
    if len(selected) != 2:
        raise RuntimeError(f"{scene} planned new worker set differs")
    rows: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix=f"provision-{prefix}") as pool:
        futures = {
            pool.submit(provision.provision_worker, freeze, row, output): row["worker_id"]
            for row in selected
        }
        for future in as_completed(futures):
            worker_id = str(futures[future])
            rows[worker_id] = future.result()
    return rows


def authority_artifacts(
    *, scene: str, freeze_path: Path, freeze: Mapping[str, Any],
    live_rows: Mapping[str, Mapping[str, Any]], output: Path,
) -> dict[str, Path]:
    freeze_sha = sha(freeze_path)
    target_rows = {str(key): copy.deepcopy(dict(value)) for key, value in live_rows.items()}
    all_rows = []
    for frozen in freeze.get("workers") or []:
        worker_id = str(frozen["worker_id"])
        if worker_id in target_rows:
            all_rows.append(target_rows[worker_id])
        else:
            all_rows.append({**copy.deepcopy(dict(frozen)), "ready": False,
                             "authority_placeholder_for_other_scene": True})
    target_cpus = {
        cpu for row in target_rows.values()
        for cpu in slots.parse_cpuset(row["database_cpuset"])
        | slots.parse_cpuset(row["client_cpuset"])
    }
    rolling = dynamic_gate.rolling_quiet_gate(
        run_id=f"mysql-c-xp4-transition-{scene}-{os.getpid()}",
        required_seconds=60.0, maximum_wait_seconds=120.0,
        maximum_load1=16.0, target_cpus=target_cpus,
    )
    if rolling.get("status") != "PASS":
        raise RuntimeError(f"{scene} transition rolling gate failed closed")
    common = {
        "status": "PASS", "created_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "authority_scene_scope": scene,
        "freeze_path": str(freeze_path), "freeze_sha256": freeze_sha,
        "accepted_rounds": {name: 0 for name in (
            "job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io"
        )},
        "observed_seconds": float(rolling.get("observed_seconds") or 0.0),
        "formal_launch_authorized": True,
        "rolling_dynamic_noninterference_gate": rolling,
        "recalibration_performed": False,
        "accepted_state_credit_increment": 0,
        "external_process_control_or_stop_performed": False,
    }
    preflight_path = output / "transition_preflight.json"
    atomic(preflight_path, {
        **common,
        "schema_version": "mysql_algorithm_c_xp4_transition_preflight_v1",
    })
    prepared_path = output / "prepared_binding.json"
    atomic(prepared_path, {
        **common, "status": "PREPARED",
        "schema_version": "mysql_algorithm_c_xp4_transition_prepared_v1",
        "preflight_path": str(preflight_path), "preflight_sha256": sha(preflight_path),
        "workers": all_rows, "workload_started": False,
        "fresh_r0_complete": False, "formal_outer_started": False,
    })
    account_rows = [
        {**copy.deepcopy(row), "passwords_emitted": False}
        for row in all_rows
    ]
    accounts_path = output / "benchmark_accounts_binding.json"
    atomic(accounts_path, {
        **common,
        "schema_version": "mysql_algorithm_c_xp4_transition_accounts_v1",
        "prepared_binding_path": str(prepared_path),
        "prepared_binding_sha256": sha(prepared_path),
        "workers": account_rows, "passwords_emitted": False,
        "workload_started": False,
    })
    r0_path = output / "r0_transition_waiver.json"
    atomic(r0_path, {
        **common,
        "schema_version": "mysql_algorithm_c_xp4_transition_r0_waiver_v1",
        "formal_outer_launch_authorized": True,
        "preflight_path": str(preflight_path), "preflight_sha256": sha(preflight_path),
        "prepared_binding_path": str(prepared_path),
        "prepared_binding_sha256": sha(prepared_path),
        "benchmark_accounts_path": str(accounts_path),
        "benchmark_accounts_sha256": sha(accounts_path),
        "fresh_r0_complete": False, "formal_outer_started": False,
        "incumbent_remeasured_after_transition": False,
        "completed_candidate_replay_increment": 0,
        "failures": [],
    })
    return {
        "preflight": preflight_path, "prepared": prepared_path,
        "accounts": accounts_path, "r0": r0_path,
    }


def launch_successor(
    *, scene: str, checkpoint: Path, quarantine_path: Path,
    artifacts: Mapping[str, Path], transition_root: Path,
) -> dict[str, Any]:
    suffix = "cpu" if scene == "hybench_xp_cpu" else "io"
    run_id = f"v160_to_c_xp4_20260825_attempt001_xp_{suffix}"
    unit = f"mysql-algc-xp4-{suffix}-v160.service"
    stdout = LOG_ROOT / f"{run_id}.stdout.log"
    stderr = LOG_ROOT / f"{run_id}.stderr.log"
    barrier = Path(f"/tmp/mysql_c_xp4_start/{run_id}")
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        f"--description=MySQL Algorithm C four-worker {scene} boundary resume",
        f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,
        str(PYTHON), str(CONTROLLER), "--run-id", run_id,
        "--checkpoint", str(checkpoint),
        "--runtime-preflight", str(artifacts["preflight"]),
        "--prepared-binding", str(artifacts["prepared"]),
        "--benchmark-accounts", str(artifacts["accounts"]),
        "--fresh-r0", str(artifacts["r0"]),
        "--replay-quarantine", str(quarantine_path),
        "--scene", scene, "--scene-start-barrier", str(barrier),
    ]
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{scene} successor launch failed: {completed.stderr}")
    state_path = HISTORY / run_id / "run_state.json"
    deadline = time.monotonic() + 300.0
    while time.monotonic() < deadline:
        fields = systemctl("show", unit, "-p", "ActiveState", "-p", "MainPID", check=False).stdout
        if "ActiveState=failed" in fields or "ActiveState=inactive" in fields:
            raise RuntimeError(f"{scene} successor exited during startup: {fields}")
        if state_path.is_file():
            state = load(state_path)
            if state.get("status") == "RUNNING" and state.get("physical_replay_parallelism") == 4:
                return {
                    "status": "LAUNCHED", "scene": scene, "run_id": run_id,
                    "unit": unit, "state": str(state_path), "output": str(state_path.parent),
                    "stdout": str(stdout), "stderr": str(stderr),
                }
        time.sleep(2.0)
    raise TimeoutError(f"{scene} successor startup evidence timed out")


def run(args: argparse.Namespace) -> int:
    scene = args.scene
    manifest = load(args.manifest.resolve())
    if (
        manifest.get("status") not in {"READY", "LAUNCHED"}
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("memory_scenes_complete") is not True
        or manifest.get("memory_worker_containers_stopped") is not True
        or manifest.get("successor_freeze_sha256") != sha(SUCCESSOR_FREEZE)
    ):
        raise RuntimeError("XP four-worker handoff manifest differs")
    binding = (manifest.get("xp_predecessors") or {}).get(scene) or {}
    state_path = Path(str(binding.get("state") or "")).resolve()
    source_root = state_path.parent
    unit = str(binding.get("unit") or "")
    transition_root = HISTORY / f"v160_to_c_xp4_handoff_20260825/{SCENE_PREFIX[scene]}"
    result_path = transition_root / "handoff_result.json"
    if result_path.is_file():
        result = load(result_path)
        if result.get("status") in {"LAUNCHED", "ALREADY_COMPLETE"}:
            print(json.dumps(result, sort_keys=True))
            return 0
    transition_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    stopped_state, terminal = wait_boundary_and_stop(
        scene=scene, state_path=state_path, unit=unit,
        timeout_seconds=float(args.timeout_seconds),
    )
    if terminal:
        result = {
            "status": "ALREADY_COMPLETE", "scene": scene,
            "accepted_rounds": (stopped_state.get("accepted_rounds") or {}).get(scene),
            "state": str(state_path), "completed_at": now(),
            "unused_released_capacity_reason": "scene_converged_before_handoff",
        }
        atomic(result_path, result)
        print(json.dumps(result, sort_keys=True))
        return 0
    boundary_root = transition_root / "source_boundary_checkpoint"
    source_boundary = boundary.build(source_root)
    boundary_path = boundary_root / "prepared_binding.json"
    boundary_root.mkdir(parents=True, mode=0o700)
    boundary.base.alg.atomic(boundary_path, source_boundary)
    boundary.verifier.verify_checkpoint(boundary_path)
    freeze = load(SUCCESSOR_FREEZE)
    original = original_live_workers(scene=scene, source_root=source_root, freeze=freeze)
    additions = provision_new_workers(
        scene=scene, freeze=freeze, output=transition_root / "provisioned_workers",
    )
    live = {**original, **additions}
    artifacts = authority_artifacts(
        scene=scene, freeze_path=SUCCESSOR_FREEZE, freeze=freeze,
        live_rows=live, output=transition_root / "authority",
    )
    authority_path = transition_root / "transition_authority.json"
    source_freeze = Path(str(source_boundary["freeze_path"])).resolve()
    transition_authority = {
        "schema_version": "mysql_algorithm_c_xp4_transition_authority_v1",
        "status": "PASS", "created_at": now(), "scene": scene,
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "round_boundary_preserved": True,
        "accepted_state_credit_increment": 0,
        "completed_candidate_replay_increment": 0,
        "recalibration_performed": False,
        "source_freeze_path": str(source_freeze),
        "source_freeze_sha256": sha(source_freeze),
        "successor_freeze_path": str(SUCCESSOR_FREEZE),
        "successor_freeze_sha256": sha(SUCCESSOR_FREEZE),
        "source_boundary_checkpoint_path": str(boundary_path),
        "source_boundary_checkpoint_sha256": sha(boundary_path),
        "worker_ids": sorted(live),
        "candidate_batches_before": [[1, 2], [3, 4], [5]],
        "candidate_batches_after": [[1, 2, 3, 4], [5]],
        "semantic_top_k": 5,
        "native_xp_workload_unchanged": True,
        "lease_mode": "none",
    }
    atomic(authority_path, transition_authority)
    rebound_root = transition_root / "successor_checkpoint"
    rebound = rebind.build(boundary_path, SUCCESSOR_FREEZE, authority_path)
    rebound_root.mkdir(parents=True, mode=0o700)
    checkpoint_path = rebound_root / "prepared_binding.json"
    rebind.base.alg.atomic(checkpoint_path, rebound)
    rebind.verifier.verify_checkpoint(checkpoint_path)
    quarantine_root = transition_root / "replay_quarantine"
    quarantine_root.mkdir(parents=True, mode=0o700)
    empty = {name: [] for name in quarantine.SCENES}
    zeros = {name: 0 for name in quarantine.SCENES}
    quarantine_document = {
        "schema_version": "mysql_algorithm_c_replay_quarantine_v6",
        "status": "PASS", "created_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_sha256": rebound["freeze_sha256"],
        "resume_checkpoint_outer_attempt": rebound["completed_outer_attempts"],
        "formal_state_mutation": False, "accepted_rounds_mutation": False,
        "rejected_rounds_mutation": False, "interval_convergence_mutation": False,
        "quarantined_counts_by_scene": zeros,
        "soft_replayed_counts_by_scene": dict(zeros),
        "permanent_invalid_counts_by_scene": dict(zeros),
        "quarantined_param_keys_by_scene": empty,
        "soft_replayed_param_keys_by_scene": {name: [] for name in quarantine.SCENES},
        "permanent_invalid_param_keys_by_scene": {name: [] for name in quarantine.SCENES},
        "rows_by_scene": {name: [] for name in quarantine.SCENES},
        "cycle_reset_may_clear_soft_replayed": True,
        "cycle_reset_may_clear_permanent_invalid": False,
        "initialization": "empty_xp4_transition_boundary_resume",
        "evidence": {
            "checkpoint": quarantine.bound(checkpoint_path),
            "resource_freeze": quarantine.bound(SUCCESSOR_FREEZE),
            "pruning_contract": quarantine.bound(quarantine.PRUNING),
        },
    }
    quarantine_path = quarantine_root / "replay_quarantine.json"
    atomic(quarantine_path, quarantine_document)
    launch = launch_successor(
        scene=scene, checkpoint=checkpoint_path, quarantine_path=quarantine_path,
        artifacts=artifacts, transition_root=transition_root,
    )
    result = {
        **launch, "completed_at": now(),
        "source_boundary_checkpoint": str(boundary_path),
        "successor_checkpoint": str(checkpoint_path),
        "transition_authority": str(authority_path),
        "accepted_rounds_preserved": rebound["reconstructed_state"]["accepted_rounds"][scene],
        "completed_candidate_replay_increment": 0,
        "recalibration_performed": False,
    }
    atomic(result_path, result)
    print(json.dumps(result, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=tuple(SCENE_PREFIX), required=True)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--timeout-seconds", type=float, default=172800.0)
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("positive timeout required")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""At a scene-local C boundary, expand XP from two to four physical workers."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HISTORY = ROOT / "history/mysql_four_scene_20260809"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python").resolve()
FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_cpu_a_slots_four_worker_20260828_v3"
    / "four_scene_effective_resource_successor.json"
)
SOURCE_FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_cpu_a_slots_two_worker_20260828_v2"
    / "four_scene_effective_resource_successor.json"
)
SOURCE_AUTHORITY = HISTORY / "v893_to_cpu_a_slots_two_worker_handoff_source_20260828"
BOUNDARY_BUILDER = ROOT / "scripts/build_mysql_algorithm_c_independent_round_boundary_checkpoint_v4.py"
QUARANTINE_REBINDER = ROOT / "scripts/build_mysql_algorithm_c_transition_replay_quarantine_v1.py"
LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_c_async_v76.py"
CBA_MONITOR = ROOT / "scripts/monitor_mysql_xp_cba_cpu_a_slots_v1.py"
AUTHORITY_DIRNAME = "live_authority_v2"
SCENES = {
    "hybench_xp_cpu": {"prefix": "xp_cpu", "management_cpu": "240"},
    "hybench_xp_io": {"prefix": "xp_io", "management_cpu": "496"},
}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


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
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def run(argv: list[str], timeout: float = 300.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, cwd=ROOT, text=True, capture_output=True, check=False,
        timeout=timeout,
    )


def stop_at_next_boundary(
    scene: str, state_path: Path, unit: str, audit_path: Path,
) -> dict[str, Any]:
    initial = load(state_path)
    if initial.get("algorithm") != "C" or initial.get("async_scene") != scene:
        raise RuntimeError("C predecessor identity differs")
    initial_outer = int(initial.get("outer_attempt") or 0)
    while True:
        state = load(state_path)
        if state.get("status") == "COMPLETE" and bool((state.get("converged") or {}).get(scene)):
            return {"already_complete": True, "state": state}
        if state.get("status") != "RUNNING":
            raise RuntimeError(f"C predecessor terminated: {state.get('status')} {state.get('error')}")
        if int(state.get("outer_attempt") or 0) > initial_outer:
            break
        time.sleep(2.0)
    stopped = run(["systemctl", "--user", "stop", unit], 180)
    if stopped.returncode != 0:
        raise RuntimeError(f"failed to stop predecessor C: {stopped.stderr[-2000:]}")
    deadline = time.monotonic() + 180.0
    while time.monotonic() < deadline:
        shown = run(["systemctl", "--user", "show", unit, "-p", "ActiveState", "-p", "MainPID"], 30).stdout
        if "MainPID=0" in shown and "ActiveState=active" not in shown:
            document = {
                "schema_version": "mysql_algorithm_c_scene_boundary_stop_v1",
                "status": "PASS", "created_at": now(), "scene": scene,
                "initial_outer_attempt": initial_outer,
                "stopped_state": load(state_path), "unit": unit,
                "accepted_state_credit_increment": 0,
                "completed_candidate_replay_due_to_transition": 0,
            }
            atomic(audit_path, document)
            return document
        time.sleep(1.0)
    raise RuntimeError("predecessor C did not stop at bounded boundary")


def build_authority(
    scene: str, transition_root: Path,
) -> tuple[dict[str, Path], dict[str, dict[str, Any]]]:
    from scripts import mysql_cross_project_wave_coordination_cpu_a_slots_v13 as gate
    from scripts import provision_mysql_algorithm_c_cpu_a_slots_idle_xp_workers_v1 as provision
    from scripts import refresh_mysql_cpu_a_slots_two_worker_scene_authority_v2 as refresh

    freeze = load(FREEZE)
    frozen = {str(row["worker_id"]): dict(row) for row in freeze["workers"]}
    prefix = str(SCENES[scene]["prefix"])
    target_ids = {f"{prefix}_w{index}" for index in range(1, 5)}
    added_ids = {f"{prefix}_w3", f"{prefix}_w4"}
    provision_root = transition_root / "provision"
    live: dict[str, dict[str, Any]] = {}
    pending_added: set[str] = set()
    for worker_id in sorted(added_ids):
        evidence_path = provision_root / f"{worker_id}.provisioned.json"
        if not evidence_path.is_file():
            pending_added.add(worker_id)
            continue
        evidence = load(evidence_path)
        frozen_row = frozen[worker_id]
        if (
            evidence.get("ready") is not True
            or evidence.get("worker_id") != worker_id
            or evidence.get("scene") != scene
            or evidence.get("container") != frozen_row.get("container")
            or evidence.get("runtime_root") != frozen_row.get("runtime_root")
            or evidence.get("database_cpuset") != frozen_row.get("database_cpuset")
            or evidence.get("client_cpuset") != frozen_row.get("client_cpuset")
        ):
            raise RuntimeError(f"existing provision evidence differs: {worker_id}")
        live[worker_id] = refresh.read_live_worker(freeze, frozen_row)
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix=f"provision-{prefix}") as pool:
        futures = {
            pool.submit(provision.provision_worker, freeze, frozen[worker_id], provision_root): worker_id
            for worker_id in sorted(pending_added)
        }
        for future in as_completed(futures):
            live[futures[future]] = future.result()
    for worker_id in sorted(target_ids - added_ids):
        live[worker_id] = refresh.read_live_worker(freeze, frozen[worker_id])
    target_cpus = {
        cpu for row in live.values()
        for field in ("database_cpuset", "client_cpuset")
        for cpu in refresh.cpus(str(row[field]))
    }
    quiet = gate.rolling_quiet_gate(
        run_id=f"mysql-c-four-worker-{scene}-{os.getpid()}",
        required_seconds=60.0, maximum_wait_seconds=300.0,
        maximum_load1=16.0, target_cpus=target_cpus,
    )
    if quiet.get("status") != "PASS":
        raise RuntimeError(f"four-worker transition dynamic gate failed: {quiet.get('status')}")
    source_prepared = load(SOURCE_AUTHORITY / "prepared_binding.json")
    source_accounts = load(SOURCE_AUTHORITY / "benchmark_accounts_binding.json")
    prepared_rows = {str(row["worker_id"]): copy.deepcopy(dict(row)) for row in source_prepared["workers"]}
    account_rows = {str(row["worker_id"]): copy.deepcopy(dict(row)) for row in source_accounts["workers"]}
    for worker_id, row in frozen.items():
        prepared_rows.setdefault(worker_id, {**copy.deepcopy(row), "ready": False})
        account_rows.setdefault(worker_id, {**copy.deepcopy(row), "ready": False})
    prepared_rows.update(copy.deepcopy(live))
    account_rows.update(copy.deepcopy(live))
    accepted = {name: 0 for name in ("job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io")}
    common = {
        "created_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene_scope": scene, "freeze_path": str(FREEZE),
        "freeze_sha256": sha(FREEZE), "accepted_rounds": accepted,
        "observed_seconds": float(quiet.get("observed_seconds") or 0),
        "formal_launch_authorized": True,
        "rolling_dynamic_noninterference_gate": quiet,
        "accepted_state_credit_increment": 0,
        "recalibration_performed": False,
        "completed_candidate_replay_due_to_transition": 0,
    }
    authority_root = transition_root / AUTHORITY_DIRNAME
    preflight = authority_root / "transition_preflight.json"
    atomic(preflight, {**common, "schema_version": "mysql_algorithm_c_four_worker_transition_preflight_v1", "status": "PASS"})
    prepared = authority_root / "prepared_binding.json"
    atomic(prepared, {
        **common, "schema_version": "mysql_algorithm_c_four_worker_transition_prepared_v1",
        "status": "PREPARED", "preflight_path": str(preflight),
        "preflight_sha256": sha(preflight),
        "workers": [prepared_rows[key] for key in sorted(frozen)],
        "workload_started": False, "fresh_r0_complete": False,
        "formal_outer_started": False,
    })
    accounts = authority_root / "benchmark_accounts_binding.json"
    atomic(accounts, {
        **common, "schema_version": "mysql_algorithm_c_four_worker_transition_accounts_v1",
        "status": "PASS", "prepared_binding_path": str(prepared),
        "prepared_binding_sha256": sha(prepared),
        "workers": [account_rows[key] for key in sorted(frozen)],
        "passwords_emitted": False, "workload_started": False,
    })
    r0 = authority_root / "fresh_r0_binding.json"
    atomic(r0, {
        **common, "schema_version": "mysql_algorithm_c_four_worker_transition_r0_v1",
        "status": "PASS", "fresh_r0_complete": True,
        "formal_outer_launch_authorized": True, "formal_outer_started": False,
        "preflight_path": str(preflight), "preflight_sha256": sha(preflight),
        "prepared_binding_path": str(prepared), "prepared_binding_sha256": sha(prepared),
        "benchmark_accounts_path": str(accounts), "benchmark_accounts_sha256": sha(accounts),
        "target_bottleneck_gate": {"passed": True, "preserved_from_predecessor_checkpoint": True},
        "failures": [], "client_isolation_failures": [],
    })
    return {"preflight": preflight, "prepared": prepared, "accounts": accounts, "r0": r0}, live


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=sorted(SCENES), required=True)
    parser.add_argument("--source-run-root", type=Path, required=True)
    parser.add_argument("--source-unit", required=True)
    parser.add_argument("--transition-root", type=Path, required=True)
    parser.add_argument("--successor-run-id", required=True)
    parser.add_argument("--old-cba-unit", required=True)
    parser.add_argument("--new-cba-unit", required=True)
    parser.add_argument("--cba-pipeline-root", type=Path, required=True)
    parser.add_argument("--b-run-id", required=True)
    parser.add_argument("--b-unit", required=True)
    parser.add_argument("--a-run-id", required=True)
    parser.add_argument("--a-unit", required=True)
    parser.add_argument(
        "--resume-stopped-boundary", action="store_true",
        help="Resume a fail-closed transition whose predecessor boundary was already sealed",
    )
    args = parser.parse_args()
    scene = args.scene
    source_root = args.source_run_root.resolve()
    transition_root = args.transition_root.resolve()
    if HISTORY.resolve() not in transition_root.parents:
        raise RuntimeError("transition root beneath formal history required")
    boundary_stop_path = transition_root / "boundary_stop.json"
    if transition_root.exists():
        if not args.resume_stopped_boundary or not boundary_stop_path.is_file():
            raise RuntimeError("fresh transition root or sealed-boundary resume required")
        stopped = load(boundary_stop_path)
        shown = run([
            "systemctl", "--user", "show", args.source_unit,
            "-p", "ActiveState", "-p", "MainPID",
        ], 30).stdout
        if (
            stopped.get("status") != "PASS"
            or stopped.get("scene") != scene
            or stopped.get("stopped_state") != load(source_root / "run_state.json")
            or "MainPID=0" not in shown
            or "ActiveState=active" in shown
        ):
            raise RuntimeError("sealed predecessor boundary resume authority differs")
    else:
        transition_root.mkdir(parents=True, mode=0o700)
        stopped = stop_at_next_boundary(
            scene, source_root / "run_state.json", args.source_unit,
            boundary_stop_path,
        )
    if stopped.get("already_complete") is True:
        return 0
    boundary_dir = transition_root / "boundary_checkpoint"
    boundary_binding = boundary_dir / "prepared_binding.json"
    if args.resume_stopped_boundary and boundary_binding.is_file():
        checkpoint = load(boundary_binding)
        if (
            checkpoint.get("status") != "PREPARED"
            or checkpoint.get("source_run_root") != str(source_root)
            or int(checkpoint.get("completed_outer_attempts") or -1)
            != int(stopped["stopped_state"].get("outer_attempt") or -2)
        ):
            raise RuntimeError("existing boundary checkpoint resume authority differs")
    else:
        built = run([
            str(PYTHON), str(BOUNDARY_BUILDER), "--run-root", str(source_root),
            "--output-dir", str(boundary_dir),
        ], 600)
        if built.returncode != 0:
            raise RuntimeError(f"boundary checkpoint failed: {built.stderr[-5000:]}")
    authority_root = transition_root / AUTHORITY_DIRNAME
    authority = {
        "preflight": authority_root / "transition_preflight.json",
        "prepared": authority_root / "prepared_binding.json",
        "accounts": authority_root / "benchmark_accounts_binding.json",
        "r0": authority_root / "fresh_r0_binding.json",
    }
    existing_authority = {name: path.is_file() for name, path in authority.items()}
    if args.resume_stopped_boundary and all(existing_authority.values()):
        expected_status = {
            "preflight": "PASS", "prepared": "PREPARED",
            "accounts": "PASS", "r0": "PASS",
        }
        for name, path in authority.items():
            document = load(path)
            if (
                document.get("status") != expected_status[name]
                or document.get("scene_scope") != scene
                or document.get("freeze_sha256") != sha(FREEZE)
                or document.get("formal_launch_authorized") is not True
            ):
                raise RuntimeError(f"existing live authority differs: {name}")
    elif any(existing_authority.values()):
        raise RuntimeError("partial live authority resume prohibited")
    else:
        authority, _live = build_authority(scene, transition_root)
    transition_authority = transition_root / "transition_authority.json"
    if args.resume_stopped_boundary and transition_authority.is_file():
        transition_document = load(transition_authority)
        if (
            transition_document.get("status") != "PASS"
            or transition_document.get("scene") != scene
            or transition_document.get("successor_freeze_sha256") != sha(FREEZE)
            or transition_document.get("round_boundary_preserved") is not True
        ):
            raise RuntimeError("existing transition authority differs")
    else:
        atomic(transition_authority, {
            "schema_version": "mysql_algorithm_c_cpu_a_slots_2_to_4_transition_authority_v1",
            "status": "PASS", "created_at": now(), "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True, "scene": scene,
            "round_boundary_preserved": True, "accepted_state_credit_increment": 0,
            "source_freeze_path": str(SOURCE_FREEZE), "source_freeze_sha256": sha(SOURCE_FREEZE),
            "successor_freeze_path": str(FREEZE), "successor_freeze_sha256": sha(FREEZE),
            "completed_candidate_replay_due_to_transition": 0,
            "recalibration_performed": False,
        })
    from scripts import build_mysql_algorithm_c_cpu_a_slots_transition_checkpoint_v2 as rebind
    rebound_dir = transition_root / "successor_checkpoint"
    rebound_path = rebound_dir / "prepared_binding.json"
    if args.resume_stopped_boundary and rebound_path.is_file():
        rebound = load(rebound_path)
        if (
            rebound.get("status") != "PREPARED"
            or rebound.get("freeze_sha256") != sha(FREEZE)
            or int(rebound.get("completed_outer_attempts") or -1)
            != int(checkpoint.get("completed_outer_attempts") or -2)
        ):
            raise RuntimeError("existing successor checkpoint differs")
    else:
        rebound_dir.mkdir(mode=0o700)
        rebound = rebind.build(
            boundary_binding, FREEZE, transition_authority,
        )
        rebind.base.alg.atomic(rebound_path, rebound)
    quarantine = transition_root / "replay_quarantine.json"
    if not quarantine.is_file():
        rebound_q = run([
            str(PYTHON), str(QUARANTINE_REBINDER),
            "--checkpoint", str(rebound_path), "--output", str(quarantine),
        ], 300)
        if rebound_q.returncode != 0:
            raise RuntimeError(f"quarantine rebind failed: {rebound_q.stderr[-5000:]}")
    launched = run([
        str(PYTHON), str(LAUNCHER), "--run-id", args.successor_run_id,
        "--checkpoint", str(rebound_path),
        "--runtime-preflight", str(authority["preflight"]),
        "--prepared-binding", str(authority["prepared"]),
        "--benchmark-accounts", str(authority["accounts"]),
        "--fresh-r0", str(authority["r0"]),
        "--replay-quarantine", str(quarantine),
        "--scene", scene, "--resume-search", "--startup-timeout-seconds", "900",
    ], 1200)
    if launched.returncode != 0:
        raise RuntimeError(f"four-worker C launch failed: {launched.stderr[-6000:]}")
    successor_state = HISTORY / f"{args.successor_run_id}_{SCENES[scene]['prefix']}" / "run_state.json"
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        if successor_state.is_file() and load(successor_state).get("status") == "RUNNING":
            break
        time.sleep(2)
    else:
        raise RuntimeError("four-worker C successor did not enter RUNNING")
    run(["systemctl", "--user", "stop", args.old_cba_unit], 120)
    monitor = run([
        "systemd-run", "--user", f"--unit={args.new_cba_unit.removesuffix('.service')}",
        f"--description=MySQL {scene} four-worker C-to-B-to-A handoff",
        f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
        "/usr/bin/taskset", "-c", str(SCENES[scene]["management_cpu"]),
        str(PYTHON), str(CBA_MONITOR), "--scene", scene,
        "--c-state", str(successor_state), "--source-authority", str(SOURCE_AUTHORITY),
        "--pipeline-root", str(args.cba_pipeline_root),
        "--b-run-id", args.b_run_id, "--b-unit", args.b_unit,
        "--a-run-id", args.a_run_id, "--a-unit", args.a_unit,
        "--poll-seconds", "15", "--maximum-wait-seconds", "259200",
    ], 60)
    if monitor.returncode != 0:
        raise RuntimeError(f"successor CBA monitor launch failed: {monitor.stderr[-3000:]}")
    atomic(transition_root / "transition_complete.json", {
        "schema_version": "mysql_algorithm_c_cpu_a_slots_four_worker_transition_complete_v1",
        "status": "PASS", "completed_at": now(), "scene": scene,
        "successor_run_id": args.successor_run_id,
        "successor_state": str(successor_state),
        "accepted_history_preserved": True,
        "completed_candidate_replay_due_to_transition": 0,
        "recalibration_performed": False,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

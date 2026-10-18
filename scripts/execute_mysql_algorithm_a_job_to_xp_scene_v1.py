#!/usr/bin/env python3
"""Transition one XP Algorithm A scene to four workers independently."""

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
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import execute_mysql_algorithm_a_xp_slot_transition_v1 as legacy
from scripts import mysql_algorithm_a_job_to_xp_slots_v1 as slots
from scripts import mysql_cross_project_wave_coordination_v2 as coordination
from scripts import provision_mysql_algorithm_a_job_to_xp_workers_v1 as provision
from scripts import reset_mysql_xp_data_planes_v5 as reset


PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_job_to_xp_resume_v1.py"
HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
TRANSITION_ROOT = HISTORY / "v39_to_a_policy10_20260821_job_to_xp_slots_v1"
CONTRACT_PATH = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_to_xp_slots_20260821_v1"
    / "transition_contract.json"
)
MANAGEMENT_CPUSET = "240-241,496-497"


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
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def checkpoint(path: Path, document: dict[str, Any], stage: str, **fields: Any) -> None:
    document.update({"status": "TRANSITIONING", "stage": stage, "updated_at": now()})
    document.update(copy.deepcopy(fields))
    atomic(path, document)


def systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *args], cwd=ROOT, capture_output=True,
        text=True, check=check, timeout=120,
    )


def validate_contract() -> dict[str, Any]:
    contract = load(CONTRACT_PATH)
    if (
        contract.get("schema_version") != "mysql_algorithm_a_job_to_xp_slot_contract_v1"
        or contract.get("status") != "FROZEN"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("contract") != slots.TRANSITION_CONTRACT
        or contract.get("trigger_requires_job_complete_10") is not True
        or contract.get("xp_parallelism") != {"before": 2, "after": 4}
        or contract.get("per_scene_independent_transition") is not True
        or contract.get("round_boundary_required") is not True
        or contract.get("mixed_worker_cohort_round_prohibited") is not True
        or contract.get("fresh_baseline_repetitions_per_worker") != 2
        or contract.get("incumbent_replay_required") is not True
        or contract.get("native_xp_workload_unchanged") is not True
    ):
        raise RuntimeError("frozen JOB-to-XP transition contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != binding.get("sha256"):
            raise RuntimeError(f"JOB-to-XP source binding differs: {path}")
    return contract


def terminal(state: Mapping[str, Any], scene: str) -> bool:
    return bool(
        state.get("algorithm") == "A"
        and state.get("async_scene") == scene
        and state.get("formal_scope") == "mysql_only"
        and state.get("status") in {"COMPLETE", "NONCONVERGENCE_CLEAN"}
        and state.get("round_checkpoint") is None
    )


def wait_and_stop(
    *, state_path: Path, unit: str, scene: str, output: Path,
    timeout_seconds: float,
) -> Path | None:
    initial = load(state_path)
    if terminal(initial, scene):
        return None
    if initial.get("status") != "RUNNING" or initial.get("async_scene") != scene:
        raise RuntimeError(f"{scene} predecessor is not running")
    initial_round = int(initial.get("round_index") or 0)
    initial_accepted = int(initial.get("accepted") or 0)
    initial_rejected = int(initial.get("rejected_rounds") or 0)
    already_boundary = initial.get("round_checkpoint") is None
    deadline = time.monotonic() + timeout_seconds
    while not already_boundary and time.monotonic() < deadline:
        state = load(state_path)
        if terminal(state, scene):
            return None
        if (
            int(state.get("accepted") or 0) > initial_accepted
            or int(state.get("rejected_rounds") or 0) > initial_rejected
            or int(state.get("round_index") or 0) > initial_round
        ):
            break
        if state.get("status") != "RUNNING":
            raise RuntimeError(f"{scene} failed before transition boundary")
        time.sleep(5)
    else:
        if not already_boundary:
            raise TimeoutError(f"{scene} round-boundary wait timed out")
    systemctl("stop", unit, check=False)
    fields = systemctl(
        "show", unit, "-p", "ActiveState", "-p", "MainPID", check=False,
    ).stdout
    if "MainPID=0" not in fields or "ActiveState=active" in fields:
        raise RuntimeError(f"{scene} predecessor controller did not stop: {fields}")
    stopped = load(state_path)
    partial = copy.deepcopy(stopped.get("round_checkpoint"))
    snapshot = copy.deepcopy(stopped)
    snapshot.update({
        "status": "TRANSITION_BOUNDARY_SNAPSHOT",
        "stage": "ROUND_BOUNDARY_SEALED_FOR_XP_2_TO_4",
        "transition_boundary_snapshot": True,
        "transition_boundary_snapshot_at": now(),
        "transition_boundary_completed_round": initial_round,
        "predecessor_state_path": str(state_path),
        "predecessor_state_sha256_after_stop": sha(state_path),
        "partial_next_round_archived": partial is not None,
        "partial_next_round_credited": False,
        "archived_partial_round_checkpoint": partial,
        "round_index": initial_round,
        "round_checkpoint": None,
    })
    snapshot_path = output / f"{scene}.predecessor_boundary_snapshot.json"
    atomic(snapshot_path, snapshot)
    return snapshot_path


def stop_donors(
    source: Mapping[str, Any], scene: str, job_evidence: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if job_evidence.get("complete") is not True:
        raise RuntimeError("JOB donor slots cannot stop before JOB COMPLETE")
    donor_ids = {
        donor for donor, (recipient_scene, _worker, _port)
        in slots.DONOR_TO_RECIPIENT.items() if recipient_scene == scene
    }
    rows = [
        row for row in source.get("workers") or []
        if row.get("worker_id") in donor_ids
    ]
    if len(rows) != 2:
        raise RuntimeError(f"{scene} JOB donor cardinality differs")
    results = []
    for row in rows:
        container = str(row["container"])
        before = reset._docker_json("inspect", container)
        if (before.get("State") or {}).get("Running") is True:
            reset._docker_run(["docker", "stop", "--time", "60", container], timeout=120)
        after = reset._docker_json("inspect", container)
        if after.get("Id") != before.get("Id") or (after.get("State") or {}).get("Running") is True:
            raise RuntimeError(f"JOB donor did not stop: {container}")
        results.append({
            "worker_id": row["worker_id"], "container": container,
            "container_id": before["Id"], "stopped": True,
            "datadir_retained": True, "recoverable": True,
        })
    return results


def launch_resume(
    *, scene: str, snapshot: Path, authority: Path,
) -> dict[str, Any]:
    suffix = "xp_cpu" if scene == "hybench_xp_cpu" else "xp_io"
    run_id = f"v39_to_a_policy10_job_to_xp_slots_20260821_attempt001_{suffix}"
    unit = f"mysql-alga-job-to-xp-{suffix}.service"
    output = HISTORY / run_id
    stdout = LOG_ROOT / f"{run_id}.stdout.log"
    stderr = LOG_ROOT / f"{run_id}.stderr.log"
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh XP successor output required: {output}")
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        f"--description=MySQL Algorithm A four-worker resume {scene}",
        f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,
        str(PYTHON), str(CONTROLLER), "--run-id", run_id,
        "--scene", scene, "--predecessor-state", str(snapshot),
        "--transition-authority", str(authority), "--output-dir", str(output),
    ]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
    if completed.returncode != 0:
        raise RuntimeError(f"{scene} successor launch failed: {completed.stderr}")
    state_path = output / "algorithm_a_state.json"
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        fields = systemctl("show", unit, "-p", "ActiveState", "-p", "MainPID", check=False).stdout
        if "ActiveState=failed" in fields or "ActiveState=inactive" in fields:
            raise RuntimeError(f"{scene} successor exited during startup: {fields}")
        if state_path.is_file():
            state = load(state_path)
            if state.get("status") == "RUNNING" and int(state.get("physical_replay_parallelism") or 0) == 4:
                return {
                    "scene": scene, "run_id": run_id, "unit": unit,
                    "output": str(output), "state": str(state_path),
                    "stdout": str(stdout), "stderr": str(stderr),
                    "stage": state.get("stage"),
                }
        time.sleep(1)
    raise TimeoutError(f"{scene} successor startup timed out")


def run(args: argparse.Namespace) -> int:
    scene = str(args.scene)
    result_path = args.result.resolve()
    if result_path.is_file():
        prior = load(result_path)
        if prior.get("status") in {"LAUNCHED", "NOT_REQUIRED_XP_TERMINAL"}:
            print(json.dumps(prior, ensure_ascii=False, sort_keys=True))
            return 0
    validate_contract()
    trigger = load(args.trigger.resolve())
    freeze_path = Path(str(trigger.get("successor_freeze_path") or "")).resolve()
    source_path = Path(str(trigger.get("source_freeze_path") or "")).resolve()
    job_evidence = trigger.get("job_terminal_evidence") or {}
    scene_binding = (trigger.get("xp_scenes") or {}).get(scene) or {}
    if (
        trigger.get("schema_version") != "mysql_algorithm_a_job_to_xp_trigger_v1"
        or trigger.get("status") != "READY"
        or trigger.get("contract") != slots.TRANSITION_CONTRACT
        or trigger.get("contract_path") != str(CONTRACT_PATH)
        or trigger.get("contract_sha256") != sha(CONTRACT_PATH)
        or job_evidence.get("complete") is not True
        or int(job_evidence.get("accepted") or 0) != 10
        or not freeze_path.is_file() or sha(freeze_path) != trigger.get("successor_freeze_sha256")
        or not source_path.is_file() or sha(source_path) != trigger.get("source_freeze_sha256")
        or scene_binding.get("scene") != scene
    ):
        raise RuntimeError("JOB-to-XP trigger authority differs")
    source = load(source_path)
    successor = load(freeze_path)
    document: dict[str, Any] = {
        "schema_version": "mysql_algorithm_a_job_to_xp_scene_handoff_v1",
        "status": "WAITING_ROUND_BOUNDARY", "stage": "TRIGGER_VALIDATED",
        "created_at": now(), "updated_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "contract": slots.TRANSITION_CONTRACT, "scene": scene,
        "trigger_path": str(args.trigger.resolve()),
        "trigger_sha256": sha(args.trigger.resolve()),
        "job_terminal_evidence": copy.deepcopy(job_evidence),
    }
    atomic(result_path, document)
    state_path = Path(str(scene_binding["state"]))
    predecessor_output = Path(str(scene_binding["output"]))
    snapshot = wait_and_stop(
        state_path=state_path, unit=str(scene_binding["unit"]), scene=scene,
        output=TRANSITION_ROOT, timeout_seconds=args.timeout_seconds,
    )
    if snapshot is None:
        document.update({
            "status": "NOT_REQUIRED_XP_TERMINAL",
            "stage": "XP_REACHED_TERMINAL_BEFORE_SLOT_TRANSITION",
            "updated_at": now(), "terminal_state_path": str(state_path),
            "terminal_state_sha256": sha(state_path),
        })
        atomic(result_path, document)
        return 0
    checkpoint(result_path, document, "XP_PREDECESSOR_STOPPED_AT_BOUNDARY", snapshot=str(snapshot))
    released = stop_donors(source, scene, job_evidence)
    checkpoint(result_path, document, "JOB_DONOR_POSITIONS_RELEASED", released_job_workers=released)
    additions = [
        copy.deepcopy(dict(row)) for row in successor.get("workers") or []
        if row.get("scene") == scene and row.get("worker_id") in slots.NEW_CONTAINER
    ]
    if len(additions) != 2:
        raise RuntimeError(f"{scene} successor additions differ")
    provision_root = TRANSITION_ROOT / "provisioned_workers"
    new_live: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="alga-xp-slot") as pool:
        futures = {
            pool.submit(provision.provision_worker, successor, row, provision_root): str(row["worker_id"])
            for row in additions
        }
        for future in as_completed(futures):
            new_live[futures[future]] = future.result()
    original_live = legacy.latest_live_worker_rows(predecessor_output, scene, successor)
    all_live = {**original_live, **new_live}
    checkpoint(result_path, document, "FOUR_XP_WORKERS_PROVISIONED", worker_ids=sorted(all_live))
    target_cpus = {
        cpu for row in all_live.values()
        for cpu in slots.parse_cpuset(row["database_cpuset"]) | slots.parse_cpuset(row["client_cpuset"])
    }
    gate = coordination.rolling_quiet_gate(
        run_id=f"v39_to_a_policy10_20260821_job_to_xp_{scene}",
        required_seconds=60.0, maximum_wait_seconds=300.0, target_cpus=target_cpus,
    )
    gate["target_worker_ids"] = sorted(all_live)
    gate_path = TRANSITION_ROOT / f"{scene}.dynamic_noninterference_gate.json"
    atomic(gate_path, gate)
    if gate.get("status") != "PASS":
        raise RuntimeError(f"{scene} four-worker non-interference gate failed")
    predecessor = load(snapshot)
    authority = {
        "schema_version": "mysql_algorithm_a_job_to_xp_transition_authority_v1",
        "status": "PASS", "created_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "contract": slots.TRANSITION_CONTRACT,
        "transition_contract_path": str(CONTRACT_PATH),
        "transition_contract_sha256": sha(CONTRACT_PATH),
        "scene": scene, "predecessor_snapshot_path": str(snapshot),
        "predecessor_snapshot_sha256": sha(snapshot),
        "preserved_search_state_sha256": slots.canonical_sha256(slots.search_state_payload(predecessor)),
        "successor_freeze_path": str(freeze_path),
        "successor_freeze_sha256": sha(freeze_path),
        "memory_terminal_evidence": {"job_memory": copy.deepcopy(job_evidence)},
        "dynamic_noninterference_gate": gate,
        "workers": [all_live[key] for key in sorted(all_live)],
        "native_xp_workload_unchanged": True,
        "mixed_worker_cohort_round_prohibited": True,
        "fresh_baseline_repetitions_per_worker": 2,
        "incumbent_replay_required": True,
        "canonical_baseline_preserved": True,
        "accepted_ranges_incumbent_history_preserved": True,
        "per_scene_independent_transition": True,
    }
    authority_path = TRANSITION_ROOT / f"{scene}.transition_authority.json"
    atomic(authority_path, authority)
    launched = launch_resume(scene=scene, snapshot=snapshot, authority=authority_path)
    document.update({
        "status": "LAUNCHED", "stage": "FOUR_WORKER_XP_RESUME_LAUNCHED",
        "updated_at": now(), "launch": launched,
        "authority_path": str(authority_path), "authority_sha256": sha(authority_path),
        "dynamic_noninterference_gate_path": str(gate_path),
        "dynamic_noninterference_gate_sha256": sha(gate_path),
        "xp_parallelism_before": 2, "xp_parallelism_after": 4,
    })
    atomic(result_path, document)
    print(json.dumps(document, ensure_ascii=False, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=slots.XP_SCENES, required=True)
    parser.add_argument("--trigger", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=172800.0)
    args = parser.parse_args()
    try:
        return run(args)
    except BaseException as error:
        result = args.result.resolve()
        prior = load(result) if result.is_file() else {}
        prior.update({
            "status": "FAILED_CLOSED", "stage": "FAILED_CLOSED",
            "updated_at": now(),
            "error": f"{type(error).__name__}: {error}",
        })
        atomic(result, prior)
        raise


if __name__ == "__main__":
    raise SystemExit(main())

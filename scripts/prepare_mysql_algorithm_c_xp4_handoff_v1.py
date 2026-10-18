#!/usr/bin/env python3
"""Release completed Algorithm A memory slots and launch independent C XP handoffs."""

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
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_c_job_to_xp_slots_v1 as slots
from scripts import reset_mysql_xp_data_planes_four_worker_v1 as reset4


HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_xp4_handoff_20260826_v2"
)
SOURCE_FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18"
    / "four_scene_effective_resource_successor.json"
)
SUCCESSOR_FREEZE = CONTRACT_ROOT / "xp_four_worker_resource_successor.json"
TRANSITION_CONTRACT = CONTRACT_ROOT / "transition_contract.json"
OPTIMIZATION_CONTRACT = CONTRACT_ROOT / "optimization_contract.json"
COORDINATION_CONTRACT = CONTRACT_ROOT / "coordination_contract.json"
ASYNC_CONTRACT = CONTRACT_ROOT / "algorithm_c_xp4_async_contract.json"
JOB_STATE = (
    HISTORY / "v153_to_a_job4_round11_20260825_attempt001_job"
    / "algorithm_a_state.json"
)
TPCH_STATE = (
    HISTORY / "v100_to_a_tpch_external_diagnostic_resume_20260823_attempt001_tpch"
    / "algorithm_a_state.json"
)
CPU_STATE = (
    HISTORY / "v59_to_c_xp_top4_20260825_attempt033_cpu_zero_safe_xp_cpu"
    / "run_state.json"
)
IO_STATE = (
    HISTORY / "v59_to_c_xp_top4_20260825_attempt034_io_zero_safe_xp_io"
    / "run_state.json"
)
CPU_UNIT = "mysql-algc-async-v59-attempt033-cpu-fullparallel.service"
IO_UNIT = "mysql-algc-async-v59-attempt034-io-fullparallel.service"
MANIFEST_ROOT = HISTORY / "v164_to_xp4_capacity_handoff_20260826"
MANIFEST = MANIFEST_ROOT / "transition_manifest.json"
EXECUTOR = ROOT / "scripts/execute_mysql_algorithm_c_xp4_scene_handoff_v1.py"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
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


def relative_binding(path: Path) -> dict[str, str]:
    path = path.resolve()
    return {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}


def terminal_a(path: Path, scene: str) -> tuple[dict[str, Any], dict[str, Any]]:
    state = load(path)
    accepted = int(state.get("accepted") or 0)
    threshold = float(state.get("normalized_width_threshold") or 0.01)
    width = float(state.get("normalized_width") or 1.0)
    if not (
        state.get("algorithm") == "A" and state.get("async_scene") == scene
        and state.get("formal_scope") == "mysql_only"
        and state.get("status") == "COMPLETE" and state.get("stage") == "COMPLETE"
        and state.get("converged") is True and state.get("round_checkpoint") is None
        and accepted >= int(state.get("accepted_target") or accepted)
        and width <= threshold
    ):
        raise RuntimeError(f"Algorithm A memory donor is not complete: {scene}")
    return state, {
        "scene": scene, "complete": True, "accepted": accepted,
        "normalized_width": width, "normalized_width_threshold": threshold,
        "run_id": state.get("run_id"), "state_path": str(path),
        "state_sha256": sha(path),
    }


def stop_job_workers(job: Mapping[str, Any], source: Mapping[str, Any]) -> list[dict[str, Any]]:
    actual = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in (
            ((job.get("authority") or {}).get("worker_cohort_transition") or {})
            .get("workers") or []
        )
    }
    expected = {"job_w1", "job_w2", "job_w3", "job_w4"}
    frozen = {str(row["worker_id"]): row for row in source.get("workers") or []}
    position_source = {
        "job_w1": "job_w1", "job_w2": "job_w2",
        "job_w3": "tpch_w1", "job_w4": "tpch_w2",
    }
    if set(actual) != expected:
        raise RuntimeError("completed four-worker JOB authority differs")
    results = []
    for worker_id in sorted(expected):
        row = actual[worker_id]
        donor = frozen[position_source[worker_id]]
        if any(row.get(field) != donor.get(field) for field in slots.POSITION_FIELDS):
            raise RuntimeError(f"{worker_id} released physical position differs")
        container = str(row["container"])
        before = reset4._docker_json("inspect", container)
        if (before.get("State") or {}).get("Running") is True:
            reset4._docker_run(["docker", "stop", "--time", "60", container], timeout=120)
        after = reset4._docker_json("inspect", container)
        if after.get("Id") != before.get("Id") or (after.get("State") or {}).get("Running") is True:
            raise RuntimeError(f"completed JOB worker did not stop: {worker_id}")
        results.append({
            "worker_id": worker_id, "container": container,
            "container_id": before["Id"], "position_source": position_source[worker_id],
            "stopped": True, "datadir_retained": True, "recoverable": True,
        })
    return results


def write_contracts(successor_sha: str) -> None:
    runtime = ROOT / "scripts/run_mysql_four_scene_algorithm_c_xp4_runtime_v1.py"
    controller = ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v1.py"
    authority = ROOT / "scripts/run_mysql_xp_four_worker_authority_v1.py"
    reset = ROOT / "scripts/reset_mysql_xp_data_planes_four_worker_v1.py"
    algorithm = ROOT / "scripts/run_mysql_four_scene_algorithm_c_v2.py"
    gate = ROOT / "scripts/mysql_cross_project_wave_coordination_v4.py"
    coordination = {
        "schema_version": "mysql_algorithm_c_xp4_no_lease_coordination_v1",
        "status": "FROZEN_RUNTIME_VALIDATED", "created_at": now(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "candidate_batches": [[1, 2, 3, 4], [5]],
        "native_xp_workload_unchanged": True,
        "lease_mode": "none", "shared_nvme_mutual_exclusion": False,
        "numa_exclusive_scheduler_lock": False, "whole_round_resource_lease": False,
        "four_scene_barrier": False, "sibling_scene_wait": False,
        "external_process_control_or_stop_performed": False,
        "per_wave_quiet_seconds": 15, "maximum_quiet_wait_seconds": 60,
        "maximum_gate_attempts_per_wave": 1, "maximum_idle_load1": 16,
        "source_bindings": {
            "coordination_source_path": str(gate.relative_to(ROOT)),
            "coordination_source_sha256": sha(gate),
            "boundary_resume_controller_path": str(runtime.relative_to(ROOT)),
            "boundary_resume_controller_sha256": sha(runtime),
        },
    }
    atomic(COORDINATION_CONTRACT, coordination)
    optimization = {
        "schema_version": "mysql_algorithm_c_xp4_optimization_contract_v1",
        "status": "FROZEN_RUNTIME_VALIDATED", "created_at": now(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "pg_search_outer": {
            "real_top_k_limit": 5, "candidate_batches": [[1, 2, 3, 4], [5]],
            "required_acceptance_tier": "fallback", "accepted_target": 10,
            "post_search_stability_confirmation": False,
        },
        "optimized_reset": {"reset_concurrency": 4},
        "bindings": {
            "reset_source_path": str(reset.relative_to(ROOT)),
            "reset_source_sha256": sha(reset),
            "algorithm_source_path": str(algorithm.relative_to(ROOT)),
            "algorithm_source_sha256": sha(algorithm),
            "resource_freeze_path": str(SUCCESSOR_FREEZE.relative_to(ROOT)),
            "resource_freeze_sha256": successor_sha,
        },
    }
    atomic(OPTIMIZATION_CONTRACT, optimization)
    sources = {
        "controller": controller,
        "runtime": runtime,
        "authority": authority,
        "reset": reset,
        "reset_implementation": ROOT / "scripts/reset_mysql_xp_data_planes_v6.py",
        "slot_mapping": ROOT / "scripts/mysql_algorithm_c_job_to_xp_slots_v1.py",
        "provisioner": ROOT / "scripts/provision_mysql_algorithm_c_job_to_xp_workers_v1.py",
        "boundary_builder": ROOT / "scripts/build_mysql_algorithm_c_independent_round_boundary_checkpoint_v1.py",
        "checkpoint_rebinder": ROOT / "scripts/build_mysql_algorithm_c_xp4_transition_checkpoint_v1.py",
        "scene_handoff_executor": EXECUTOR,
        "dynamic_gate": gate,
        "optimization_contract": OPTIMIZATION_CONTRACT,
        "coordination_contract": COORDINATION_CONTRACT,
        "resource_successor": SUCCESSOR_FREEZE,
    }
    async_contract = {
        "schema_version": "mysql_algorithm_c_xp4_async_contract_v1",
        "status": "FROZEN_RUNTIME_VALIDATED", "created_at": now(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "candidate_batches": [[1, 2, 3, 4], [5]],
        "physical_replay_parallelism": 4, "semantic_top_k": 5,
        "algorithm_c_candidate_or_outer_flow_changed": False,
        "native_xp_workload_unchanged": True,
        "accepted_history_preserved": True,
        "completed_candidate_replay_due_to_transition": 0,
        "recalibration_performed": False,
        "lease_mode": "none", "four_scene_barrier": False,
        "sibling_scene_wait": False,
        "source_bindings": {name: relative_binding(path) for name, path in sources.items()},
    }
    atomic(ASYNC_CONTRACT, async_contract)
    transition = {
        "schema_version": "mysql_algorithm_c_xp4_handoff_contract_v1",
        "status": "FROZEN", "created_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "memory_scenes_must_complete_before_release": True,
        "round_boundary_required_per_xp_scene": True,
        "per_scene_transition_independent": True,
        "mixed_worker_cohort_round_prohibited": True,
        "candidate_batches_before": [[1, 2], [3, 4], [5]],
        "candidate_batches_after": [[1, 2, 3, 4], [5]],
        "semantic_top_k": 5, "accepted_history_preserved": True,
        "completed_candidate_replay_due_to_transition": 0,
        "recalibration_performed": False, "native_xp_workload_unchanged": True,
        "lease_mode": "none", "four_scene_barrier": False,
        "sibling_scene_wait": False,
        "source_bindings": {name: relative_binding(path) for name, path in sources.items()},
    }
    atomic(TRANSITION_CONTRACT, transition)


def launch_scene_executor(scene: str) -> dict[str, Any]:
    suffix = "cpu" if scene == "hybench_xp_cpu" else "io"
    unit = f"mysql-algc-xp4-handoff-{suffix}-v160.service"
    stdout = LOG_ROOT / f"v160_to_c_xp4_handoff_{suffix}.stdout.log"
    stderr = LOG_ROOT / f"v160_to_c_xp4_handoff_{suffix}.stderr.log"
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        f"--description=MySQL Algorithm C independent XP four-worker handoff {scene}",
        f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,
        str(PYTHON), str(EXECUTOR), "--scene", scene, "--manifest", str(MANIFEST),
    ]
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{scene} handoff executor launch failed: {completed.stderr}")
    return {"scene": scene, "unit": unit, "stdout": str(stdout), "stderr": str(stderr)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prepare-capacity-only", action="store_true",
        help="freeze and release completed memory-scene slots without launching C",
    )
    args = parser.parse_args()
    if MANIFEST.is_file():
        manifest = load(MANIFEST)
        if manifest.get("status") in {"READY", "LAUNCHED"}:
            print(json.dumps(manifest, sort_keys=True))
            return 0
    # The continuously running monitor owns MANIFEST_ROOT/monitor_state.json
    # before the transition is ready.  Only the immutable contract root and
    # the transition manifest itself must be fresh here.
    if CONTRACT_ROOT.exists() or MANIFEST.is_file():
        raise RuntimeError("fresh XP4 handoff contract and manifest required")
    job, job_evidence = terminal_a(JOB_STATE, "job_memory")
    _tpch, tpch_evidence = terminal_a(TPCH_STATE, "tpch_sf4_memory")
    source = load(SOURCE_FREEZE)
    additions = slots.build_xp_workers(source)
    successor = slots.build_successor_freeze(
        source, additions, created_at=now(),
        job_evidence={"job_memory": job_evidence, "tpch_sf4_memory": tpch_evidence},
    )
    CONTRACT_ROOT.mkdir(parents=True, mode=0o700)
    atomic(SUCCESSOR_FREEZE, successor)
    write_contracts(sha(SUCCESSOR_FREEZE))
    stopped = stop_job_workers(job, source)
    manifest = {
        "schema_version": "mysql_algorithm_c_xp4_handoff_manifest_v1",
        "status": "READY", "created_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "memory_scenes_complete": True,
        "memory_terminal_evidence": {
            "job_memory": job_evidence, "tpch_sf4_memory": tpch_evidence,
        },
        "memory_worker_containers_stopped": True,
        "released_workers": stopped,
        "successor_freeze_path": str(SUCCESSOR_FREEZE),
        "successor_freeze_sha256": sha(SUCCESSOR_FREEZE),
        "transition_contract_path": str(TRANSITION_CONTRACT),
        "transition_contract_sha256": sha(TRANSITION_CONTRACT),
        "xp_predecessors": {
            "hybench_xp_cpu": {"state": str(CPU_STATE), "unit": CPU_UNIT},
            "hybench_xp_io": {"state": str(IO_STATE), "unit": IO_UNIT},
        },
        "per_scene_transition_independent": True,
        "four_scene_barrier": False, "sibling_scene_wait": False,
        "lease_mode": "none", "recalibration_performed": False,
        "completed_candidate_replay_due_to_transition": 0,
    }
    atomic(MANIFEST, manifest)
    if args.prepare_capacity_only:
        manifest.update({
            "status": "CAPACITY_READY",
            "prepared_at": now(),
            "automatic_algorithm_c_handoff_launched": False,
            "capacity_consumers_must_bind_at_their_own_algorithm_boundary": True,
        })
        atomic(MANIFEST, manifest)
        print(json.dumps(manifest, sort_keys=True))
        return 0
    launches = [
        launch_scene_executor(scene) for scene in slots.XP_SCENES
    ]
    manifest.update({"status": "LAUNCHED", "launched_at": now(), "executors": launches})
    atomic(MANIFEST, manifest)
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

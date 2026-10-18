#!/usr/bin/env python3
"""Launch v95 C with one-row tail support and per-scene XP release.

This supervisor owns only the MySQL C processes it starts.  It never controls
co-resident PostgreSQL or other external processes.
"""

from __future__ import annotations

import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
CAMPAIGN_ID = "v1194_to_c_client_primary_four_scene_20260903"
CAMPAIGN = HISTORY / CAMPAIGN_ID
MANIFEST = CAMPAIGN / "launch_manifest.json"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v6.py"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260904_v95/"
    "global_monotonic_incumbent_contract.json"
)
SCENE_REFRESH = ROOT / "scripts/refresh_mysql_client_primary_scene_authority_v1.py"
FRESH_R0_BUILDER = ROOT / (
    "scripts/build_mysql_algorithm_c_client_primary_live_authority_v1.py"
)
SOURCE_AUTHORITY = HISTORY / (
    "v1192_to_c_client_primary_live_authority_20260903/authority"
)
INITIAL_R0 = HISTORY / (
    "v1193_to_c_client_primary_fresh_r0_20260903/fresh_r0_binding.json"
)
CHECKPOINT = HISTORY / (
    "v1185_to_c_client_primary_bootstrap_20260903/checkpoint/prepared_binding.json"
)
QUARANTINE = HISTORY / (
    "v1185_to_c_client_primary_bootstrap_20260903/"
    "replay_quarantine/replay_quarantine.json"
)
BARRIER = Path("/tmp/mysql_async_outer_start") / CAMPAIGN_ID / "algorithm_c"
SCENES = {
    "job_memory": "job",
    "tpch_sf4_memory": "tpch",
    "hybench_xp_cpu": "xp_cpu",
    "hybench_xp_io": "xp_io",
}
MEMORY_SCENES = ("job_memory", "tpch_sf4_memory")
B_STATES = {
    "job_memory": HISTORY / (
        "v1178_to_b_client_primary_four_scene_20260903_job/run_state.json"
    ),
    "tpch_sf4_memory": HISTORY / (
        "v1191_to_b_tpch_client_primary_predecision_continue_20260903_tpch/"
        "run_state.json"
    ),
    "hybench_xp_cpu": HISTORY / (
        "v1184_to_b_client_primary_xp_cleanup_recovery_20260903_xp_cpu/"
        "run_state.json"
    ),
    "hybench_xp_io": HISTORY / (
        "v1184_to_b_client_primary_xp_cleanup_recovery_20260903_xp_io/"
        "run_state.json"
    ),
}


def now() -> str:
    return datetime.now().astimezone().isoformat()


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


def verified_state(path: Path) -> dict[str, Any]:
    sidecar = path.with_name(path.name + ".sha256")
    if not path.is_file() or not sidecar.is_file():
        raise RuntimeError(f"state artifact absent: {path}")
    if sidecar.read_text(encoding="ascii").split()[0] != sha(path):
        raise RuntimeError(f"state sidecar differs: {path}")
    return load(path)


def b_complete(scene: str) -> bool:
    state = verified_state(B_STATES[scene])
    return (
        state.get("status") == "COMPLETE"
        and not state.get("error")
        and (state.get("converged") or {}).get(scene) is True
    )


def prepare_xp_scene(scene: str) -> tuple[Path, Path]:
    suffix = SCENES[scene]
    authority_id = f"v1194_to_c_{suffix}_scene_authority_v95_20260904"
    r0_id = f"v1194_to_c_{suffix}_scene_fresh_r0_v95_20260904"
    authority = HISTORY / authority_id / "authority"
    fresh_r0 = HISTORY / r0_id / "fresh_r0_binding.json"
    if not authority.exists():
        subprocess.run([
            sys.executable, str(SCENE_REFRESH), "--run-id", authority_id,
            "--scene", scene, "--source-authority-root", str(SOURCE_AUTHORITY),
        ], cwd=ROOT, check=True)
    if not fresh_r0.exists():
        subprocess.run([
            sys.executable, str(FRESH_R0_BUILDER), "--run-id", r0_id,
            "--authority-root", str(authority),
        ], cwd=ROOT, check=True)
    return authority, fresh_r0


def main() -> int:
    for path in (
        CONTRACT, CONTROLLER, SCENE_REFRESH, FRESH_R0_BUILDER, INITIAL_R0,
        CHECKPOINT, QUARANTINE,
        SOURCE_AUTHORITY / "transition_preflight.json",
        SOURCE_AUTHORITY / "prepared_binding.json",
        SOURCE_AUTHORITY / "benchmark_accounts_binding.json",
    ):
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"regular launch artifact required: {path}")
    if CAMPAIGN.exists() or CAMPAIGN.is_symlink():
        raise RuntimeError(f"fresh recovery campaign required: {CAMPAIGN}")
    if BARRIER.exists() or BARRIER.is_symlink():
        raise RuntimeError(f"fresh C barrier required: {BARRIER}")
    outputs = {
        scene: HISTORY / f"{CAMPAIGN_ID}_{suffix}"
        for scene, suffix in SCENES.items()
    }
    memory_output = HISTORY / f"{CAMPAIGN_ID}_memory_pair_job"
    if memory_output.exists() or any(path.exists() for path in outputs.values()):
        raise RuntimeError("fresh C output roots required")
    if not all(b_complete(scene) for scene in MEMORY_SCENES):
        raise RuntimeError("memory-scene Algorithm B completion differs")

    contract = load(CONTRACT)
    release = dict(contract.get("full_parallel_launch") or {})
    if (
        contract.get("release_version") != "v95-memory-pair-tail-client-primary"
        or contract.get("mixed_memory_scene_batch_supported") is not True
        or contract.get("single_candidate_tail_batch_supported") is not True
        or release.get("per_scene_b_completion_release") is not True
        or release.get("four_scene_wave_barrier") is not False
        or release.get("sibling_scene_wait") is not False
    ):
        raise RuntimeError("v95 staged contract differs")

    CAMPAIGN.mkdir(parents=True, mode=0o700)
    BARRIER.mkdir(parents=True, mode=0o700)
    memory_quarantine_path = CAMPAIGN / "memory_pair_replay_quarantine.json"
    memory_quarantine = load(QUARANTINE)
    for key in tuple(memory_quarantine):
        if key.endswith("_by_scene") and isinstance(memory_quarantine[key], Mapping):
            memory_quarantine[key] = {
                scene: memory_quarantine[key][scene] for scene in MEMORY_SCENES
            }
    memory_quarantine.update({
        "schema_version": "mysql_algorithm_c_replay_quarantine_v6",
        "memory_pair_projection": True,
        "source_quarantine_path": str(QUARANTINE),
        "source_quarantine_sha256": sha(QUARANTINE),
        "active_scenes": list(MEMORY_SCENES),
        "formal_state_mutation": False,
    })
    atomic(memory_quarantine_path, memory_quarantine)

    launches: dict[str, dict[str, Any]] = {}
    processes: dict[str, subprocess.Popen[str]] = {}
    process_scenes: dict[str, tuple[str, ...]] = {}
    logs: dict[str, Any] = {}
    manifest: dict[str, Any] = {
        "schema_version": "mysql_algorithm_c_client_primary_staged_launch_v95",
        "status": "PREPARING",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "contract": str(CONTRACT),
        "contract_sha256": sha(CONTRACT),
        "supervisor_source": str(Path(__file__).resolve()),
        "supervisor_source_sha256": sha(Path(__file__).resolve()),
        "declared_scenes": list(SCENES),
        "all_four_scenes_declared_in_same_formal_campaign": True,
        "all_four_scenes_started_together": False,
        "per_scene_b_completion_release": True,
        "workers_per_scene": 2,
        "maximum_concurrent_mysql_workers": 8,
        "four_scene_wave_barrier": False,
        "sibling_scene_wait": False,
        "lease_mode": "none",
        "mixed_memory_scene_batch_supported": True,
        "launch_successor_correction": (
            "memory_pair_quarantine_schema_v6_and_single_candidate_tail"
        ),
        "single_candidate_tail_batch_supported": True,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "b_states": {scene: str(path) for scene, path in B_STATES.items()},
        "launches": launches,
    }

    def launch(
        key: str, scenes: tuple[str, ...], authority: Path, fresh_r0: Path,
        quarantine: Path,
    ) -> None:
        memory_pair = scenes == MEMORY_SCENES
        suffix = "memory_pair_job" if memory_pair else SCENES[scenes[0]]
        run_id = f"{CAMPAIGN_ID}_{suffix}"
        log_path = CAMPAIGN / f"{suffix}.controller.log"
        log = log_path.open("w", encoding="utf-8")
        environment = os.environ.copy()
        environment["MYSQL_TWO_WORKER_AUTHORITY_ROOT"] = str(authority)
        if memory_pair:
            environment["MYSQL_C_SCENE_GROUP"] = "memory_pair"
        command = [
            sys.executable, str(CONTROLLER), "--run-id", run_id,
            "--checkpoint", str(CHECKPOINT),
            "--runtime-preflight", str(authority / "transition_preflight.json"),
            "--prepared-binding", str(authority / "prepared_binding.json"),
            "--benchmark-accounts", str(authority / "benchmark_accounts_binding.json"),
            "--fresh-r0", str(fresh_r0),
            "--replay-quarantine", str(quarantine), "--fresh-search",
        ]
        if not memory_pair:
            command.extend([
                "--scene", scenes[0], "--scene-start-barrier", str(BARRIER),
            ])
        process = subprocess.Popen(
            command, cwd=ROOT, env=environment, stdout=log,
            stderr=subprocess.STDOUT, text=True, start_new_session=True,
        )
        processes[key] = process
        process_scenes[key] = scenes
        logs[key] = log
        for scene in scenes:
            launches[scene] = {
                "scene": scene, "scene_group": key, "run_id": run_id,
                "pid": process.pid, "workers": 2, "started_at": now(),
                "log": str(log_path), "authority_root": str(authority),
                "fresh_r0": str(fresh_r0),
                "release_reason": "same_scene_algorithm_b_complete",
            }

    def project_memory() -> None:
        source = memory_output / "run_state.json"
        state = verified_state(source)
        for scene in MEMORY_SCENES:
            destination = outputs[scene] / "run_state.json"
            if destination.exists():
                continue
            projected = copy.deepcopy(state)
            projected.update({
                "memory_pair_source_state": str(source),
                "memory_pair_source_state_sha256": sha(source),
                "scene_projection": scene,
                "physical_measurement_reused": False,
            })
            atomic(destination, projected)

    atomic(MANIFEST, manifest)
    try:
        launch(
            "memory_pair", MEMORY_SCENES, SOURCE_AUTHORITY, INITIAL_R0,
            memory_quarantine_path,
        )
        while True:
            for key, process in tuple(processes.items()):
                if process.poll() is None:
                    continue
                code = int(process.returncode)
                if key == "memory_pair" and code == 0:
                    project_memory()
                for scene in process_scenes[key]:
                    launches[scene].update({
                        "exit_code": code, "completed_at": now(),
                    })
                if not logs[key].closed:
                    logs[key].close()
                processes.pop(key)

            for scene in ("hybench_xp_cpu", "hybench_xp_io"):
                if scene in launches:
                    continue
                b_state = verified_state(B_STATES[scene])
                if b_state.get("status") in {"FAILED", "FAILED_CLOSED"} or b_state.get("error"):
                    raise RuntimeError(f"B failed before C release: {scene}")
                if not b_complete(scene):
                    continue
                authority, fresh_r0 = prepare_xp_scene(scene)
                launch(scene, (scene,), authority, fresh_r0, QUARANTINE)

            running = sorted(
                scene for scene, row in launches.items() if "exit_code" not in row
            )
            pending = sorted(set(SCENES) - set(launches))
            manifest.update({
                "status": "RUNNING_STAGED",
                "updated_at": now(),
                "launches": launches,
                "running_scenes": running,
                "pending_b_scenes": pending,
            })
            completed = len(launches) == len(SCENES) and not running
            if completed:
                passed = all(row.get("exit_code") == 0 for row in launches.values())
                manifest.update({
                    "status": "COMPLETE" if passed else "FAILED_CLOSED",
                    "completed_at": now(),
                })
                atomic(MANIFEST, manifest)
                return 0 if passed else 4
            atomic(MANIFEST, manifest)
            time.sleep(30)
    except Exception as error:
        manifest.update({
            "status": "FAILED_CLOSED", "updated_at": now(),
            "error": f"{type(error).__name__}: {error}", "launches": launches,
        })
        atomic(MANIFEST, manifest)
        raise
    finally:
        for log in logs.values():
            if not log.closed:
                log.close()


if __name__ == "__main__":
    raise SystemExit(main())

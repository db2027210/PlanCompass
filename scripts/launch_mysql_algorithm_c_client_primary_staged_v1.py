#!/usr/bin/env python3
"""Launch C per scene as its matching B run completes, in one campaign."""

from __future__ import annotations

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
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v4.py"
CONTRACT = ROOT / (
    "config/formal/"
    "frozen_mysql_four_scene_algorithm_c_client_primary_20260903_v93/"
    "global_monotonic_incumbent_contract.json"
)
CHECKPOINT = HISTORY / (
    "v1185_to_c_client_primary_bootstrap_20260903/"
    "checkpoint/prepared_binding.json"
)
QUARANTINE = HISTORY / (
    "v1185_to_c_client_primary_bootstrap_20260903/"
    "replay_quarantine/replay_quarantine.json"
)
SOURCE_AUTHORITY = HISTORY / (
    "v1183_to_b_xp_blkio_identity_recovery_20260903/authority"
)
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
SCENES = {
    "job_memory": "job",
    "tpch_sf4_memory": "tpch",
    "hybench_xp_cpu": "xp_cpu",
    "hybench_xp_io": "xp_io",
}
INITIAL_SCENES = ("job_memory", "tpch_sf4_memory")


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


def verified_b_state(scene: str) -> dict[str, Any]:
    path = B_STATES[scene]
    sidecar = path.with_name(path.name + ".sha256")
    if not path.is_file() or not sidecar.is_file():
        raise RuntimeError(f"B state artifact absent: {path}")
    if sidecar.read_text(encoding="ascii").split()[0] != sha(path):
        raise RuntimeError(f"B state sidecar differs: {path}")
    return load(path)


def b_complete(scene: str) -> bool:
    state = verified_b_state(scene)
    return (
        state.get("status") == "COMPLETE"
        and state.get("error") in (None, "")
        and (state.get("converged") or {}).get(scene) is True
    )


def prepare_authority(scene: str, ordinal: int) -> tuple[Path, Path]:
    suffix = SCENES[scene]
    authority_id = (
        "v1192_to_c_client_primary_live_authority_20260903"
        if ordinal == 0 else
        f"v1194_to_c_{suffix}_release_authority_20260903"
    )
    r0_id = (
        "v1193_to_c_client_primary_fresh_r0_20260903"
        if ordinal == 0 else
        f"v1194_to_c_{suffix}_release_fresh_r0_20260903"
    )
    authority = HISTORY / authority_id / "authority"
    fresh_r0 = HISTORY / r0_id / "fresh_r0_binding.json"
    if ordinal == 0 and all((authority / name).is_file() for name in (
        "authority_result.json", "transition_preflight.json",
        "prepared_binding.json", "benchmark_accounts_binding.json",
    )) and fresh_r0.is_file():
        # JOB/TPC-H containers are fixed after B completion.  Reuse the
        # already-passed snapshot so their release is not coupled to a
        # transient XP reset while XP Algorithm B is still running.
        return authority, fresh_r0
    subprocess.run([
        sys.executable,
        "scripts/refresh_mysql_client_primary_authority_after_xp_reset_v2.py",
        "--run-id", authority_id,
        "--source-authority-root", str(SOURCE_AUTHORITY),
    ], cwd=ROOT, check=True)
    subprocess.run([
        sys.executable,
        "scripts/build_mysql_algorithm_c_client_primary_live_authority_v1.py",
        "--run-id", r0_id,
        "--authority-root", str(authority),
    ], cwd=ROOT, check=True)
    return authority, fresh_r0


def main() -> int:
    if CAMPAIGN.exists() or CAMPAIGN.is_symlink():
        raise RuntimeError(f"fresh staged C campaign required: {CAMPAIGN}")
    for path in (CONTRACT, CHECKPOINT, QUARANTINE):
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"regular launch artifact required: {path}")
    contract = load(CONTRACT)
    release = dict(contract.get("full_parallel_launch") or {})
    if (
        contract.get("release_version") != "v93-staged-client-primary"
        or release.get("all_four_scenes_started_together") is not False
        or release.get("per_scene_b_completion_release") is not True
    ):
        raise RuntimeError("v93 staged launch contract differs")
    for scene in INITIAL_SCENES:
        if not b_complete(scene):
            raise RuntimeError(f"initial C scene B is not complete: {scene}")
    outputs = {
        scene: HISTORY / f"{CAMPAIGN_ID}_{suffix}"
        for scene, suffix in SCENES.items()
    }
    memory_pair_output = HISTORY / f"{CAMPAIGN_ID}_memory_pair_job"
    if (
        any(path.exists() or path.is_symlink() for path in outputs.values())
        or memory_pair_output.exists() or memory_pair_output.is_symlink()
    ):
        raise RuntimeError("fresh staged C scene outputs required")
    barrier = Path("/tmp/mysql_async_outer_start") / CAMPAIGN_ID / "algorithm_c"
    if barrier.exists() or barrier.is_symlink():
        raise RuntimeError(f"fresh staged C barrier required: {barrier}")
    barrier.mkdir(parents=True, mode=0o700)
    CAMPAIGN.mkdir(parents=True, mode=0o700)
    memory_pair_quarantine = CAMPAIGN / "memory_pair_replay_quarantine.json"
    memory_quarantine = load(QUARANTINE)
    for key in tuple(memory_quarantine):
        if key.endswith("_by_scene") and isinstance(memory_quarantine[key], Mapping):
            memory_quarantine[key] = {
                scene: memory_quarantine[key][scene] for scene in INITIAL_SCENES
            }
    memory_quarantine.update({
        "schema_version": "mysql_algorithm_c_replay_quarantine_v6",
        "memory_pair_projection": True,
        "source_quarantine_path": str(QUARANTINE),
        "source_quarantine_sha256": sha(QUARANTINE),
        "active_scenes": list(INITIAL_SCENES),
        "formal_state_mutation": False,
    })
    atomic(memory_pair_quarantine, memory_quarantine)

    launches: dict[str, dict[str, Any]] = {}
    processes: dict[str, subprocess.Popen[str]] = {}
    process_scenes: dict[str, tuple[str, ...]] = {}
    logs: dict[str, Any] = {}
    manifest: dict[str, Any] = {
        "schema_version": "mysql_algorithm_c_client_primary_staged_launch_v1",
        "status": "PREPARING_INITIAL_SCENES",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "contract": str(CONTRACT),
        "contract_sha256": sha(CONTRACT),
        "declared_scenes": list(SCENES),
        "initial_release_scenes": list(INITIAL_SCENES),
        "all_four_scenes_declared_in_same_formal_campaign": True,
        "all_four_scenes_started_together": False,
        "per_scene_b_completion_release": True,
        "workers_per_scene": 2,
        "maximum_concurrent_mysql_workers": 8,
        "four_scene_wave_barrier": False,
        "sibling_scene_wait": False,
        "lease_mode": "none",
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "b_states": {scene: str(path) for scene, path in B_STATES.items()},
        "memory_pair_quarantine": str(memory_pair_quarantine),
        "memory_pair_quarantine_sha256": sha(memory_pair_quarantine),
        "launches": launches,
    }
    atomic(MANIFEST, manifest)

    def launch(scene_group: tuple[str, ...], authority: Path, fresh_r0: Path) -> None:
        memory_pair = scene_group == INITIAL_SCENES
        process_key = "memory_pair" if memory_pair else scene_group[0]
        suffix = "memory_pair_job" if memory_pair else SCENES[scene_group[0]]
        run_id = f"{CAMPAIGN_ID}_{suffix}"
        log_path = CAMPAIGN / f"{suffix}.controller.log"
        log = log_path.open("w", encoding="utf-8")
        environment = os.environ.copy()
        environment["MYSQL_TWO_WORKER_AUTHORITY_ROOT"] = str(authority)
        replay_quarantine = memory_pair_quarantine if memory_pair else QUARANTINE
        command = [
            sys.executable, str(CONTROLLER), "--run-id", run_id,
            "--checkpoint", str(CHECKPOINT),
            "--runtime-preflight", str(authority / "transition_preflight.json"),
            "--prepared-binding", str(authority / "prepared_binding.json"),
            "--benchmark-accounts", str(authority / "benchmark_accounts_binding.json"),
            "--fresh-r0", str(fresh_r0),
            "--replay-quarantine", str(replay_quarantine),
            "--fresh-search",
        ]
        if memory_pair:
            environment["MYSQL_C_SCENE_GROUP"] = "memory_pair"
        else:
            command.extend([
                "--scene", scene_group[0], "--scene-start-barrier", str(barrier),
            ])
        process = subprocess.Popen(command, cwd=ROOT, env=environment,
           stdout=log, stderr=subprocess.STDOUT,
           text=True, start_new_session=True)
        processes[process_key] = process
        process_scenes[process_key] = scene_group
        logs[process_key] = log
        for scene in scene_group:
            launches[scene] = {
                "scene": scene,
                "scene_group": process_key,
                "run_id": run_id,
                "pid": process.pid,
                "workers": 2,
                "started_at": now(),
                "log": str(log_path),
                "authority_root": str(authority),
                "fresh_r0": str(fresh_r0),
                "release_reason": "same_scene_algorithm_b_complete",
            }

    def project_memory_pair_state() -> None:
        source = memory_pair_output / "run_state.json"
        source_state = load(source)
        for scene in INITIAL_SCENES:
            destination = outputs[scene] / "run_state.json"
            projected = dict(source_state)
            projected.update({
                "memory_pair_source_state": str(source),
                "memory_pair_source_state_sha256": sha(source),
                "scene_projection": scene,
                "physical_measurement_reused": False,
            })
            atomic(destination, projected)

    try:
        initial_authority, initial_r0 = prepare_authority(INITIAL_SCENES[0], 0)
        launch(INITIAL_SCENES, initial_authority, initial_r0)
        manifest.update({
            "status": "RUNNING_STAGED",
            "initial_scenes_started_at": now(),
            "launches": launches,
            "running_scenes": sorted(INITIAL_SCENES),
            "pending_b_scenes": sorted(set(SCENES) - set(INITIAL_SCENES)),
        })
        atomic(MANIFEST, manifest)

        while True:
            for process_key, process in tuple(processes.items()):
                scenes = process_scenes[process_key]
                if any("exit_code" not in launches[scene] for scene in scenes):
                    code = process.poll()
                    if code is not None:
                        if process_key == "memory_pair" and code == 0:
                            project_memory_pair_state()
                        for scene in scenes:
                            launches[scene].update({
                                "exit_code": code,
                                "completed_at": now(),
                            })
                        logs[process_key].close()
            for scene in SCENES:
                if scene in launches:
                    continue
                state = verified_b_state(scene)
                if state.get("status") in {"FAILED", "FAILED_CLOSED"} or state.get("error"):
                    raise RuntimeError(f"B failed before C release: {scene}")
                if b_complete(scene):
                    authority, fresh_r0 = prepare_authority(scene, len(launches))
                    launch((scene,), authority, fresh_r0)
            running = sorted(
                scene for scene, row in launches.items()
                if "exit_code" not in row
            )
            pending = sorted(set(SCENES) - set(launches))
            manifest.update({
                "updated_at": now(),
                "launches": launches,
                "running_scenes": running,
                "pending_b_scenes": pending,
            })
            if len(launches) == len(SCENES) and not running:
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
            "status": "FAILED_CLOSED",
            "updated_at": now(),
            "error": f"{type(error).__name__}: {error}",
            "launches": launches,
        })
        atomic(MANIFEST, manifest)
        raise
    finally:
        for scene, log in logs.items():
            if not log.closed:
                log.close()


if __name__ == "__main__":
    raise SystemExit(main())

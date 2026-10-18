#!/usr/bin/env python3
"""Adopt the live memory-pair C and release each XP C after its own B."""

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
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260903_v93/"
    "global_monotonic_incumbent_contract.json"
)
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v4.py"
SCENE_REFRESH = ROOT / "scripts/refresh_mysql_client_primary_scene_authority_v1.py"
SOURCE_AUTHORITY = HISTORY / (
    "v1192_to_c_client_primary_live_authority_20260903/authority"
)
CHECKPOINT = HISTORY / (
    "v1185_to_c_client_primary_bootstrap_20260903/checkpoint/prepared_binding.json"
)
QUARANTINE = HISTORY / (
    "v1185_to_c_client_primary_bootstrap_20260903/"
    "replay_quarantine/replay_quarantine.json"
)
BARRIER = Path("/tmp/mysql_async_outer_start") / CAMPAIGN_ID / "algorithm_c"
MEMORY_RUN_ID = f"{CAMPAIGN_ID}_memory_pair_job"
MEMORY_STATE = HISTORY / MEMORY_RUN_ID / "run_state.json"
SCENES = {
    "job_memory": "job",
    "tpch_sf4_memory": "tpch",
    "hybench_xp_cpu": "xp_cpu",
    "hybench_xp_io": "xp_io",
}
B_STATES = {
    "hybench_xp_cpu": HISTORY / (
        "v1184_to_b_client_primary_xp_cleanup_recovery_20260903_xp_cpu/run_state.json"
    ),
    "hybench_xp_io": HISTORY / (
        "v1184_to_b_client_primary_xp_cleanup_recovery_20260903_xp_io/run_state.json"
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


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def project_memory_state(state: Mapping[str, Any]) -> None:
    for scene in ("job_memory", "tpch_sf4_memory"):
        suffix = SCENES[scene]
        output = HISTORY / f"{CAMPAIGN_ID}_{suffix}"
        destination = output / "run_state.json"
        if destination.is_file():
            continue
        output.mkdir(parents=True, mode=0o700)
        projected = copy.deepcopy(dict(state))
        projected.update({
            "memory_pair_source_state": str(MEMORY_STATE),
            "memory_pair_source_state_sha256": sha(MEMORY_STATE),
            "scene_projection": scene,
            "physical_measurement_reused": False,
        })
        atomic(destination, projected)


def prepare_scene(scene: str) -> tuple[Path, Path]:
    suffix = SCENES[scene]
    authority_id = f"v1194_to_c_{suffix}_scene_authority_20260904"
    r0_id = f"v1194_to_c_{suffix}_scene_fresh_r0_20260904"
    subprocess.run([
        sys.executable, str(SCENE_REFRESH), "--run-id", authority_id,
        "--scene", scene, "--source-authority-root", str(SOURCE_AUTHORITY),
    ], cwd=ROOT, check=True)
    authority = HISTORY / authority_id / "authority"
    subprocess.run([
        sys.executable,
        "scripts/build_mysql_algorithm_c_client_primary_live_authority_v1.py",
        "--run-id", r0_id, "--authority-root", str(authority),
    ], cwd=ROOT, check=True)
    return authority, HISTORY / r0_id / "fresh_r0_binding.json"


def main() -> int:
    manifest = load(MANIFEST)
    if (
        manifest.get("status") != "RUNNING_STAGED"
        or manifest.get("contract_sha256") != sha(CONTRACT)
        or not MEMORY_STATE.is_file()
    ):
        raise RuntimeError("live staged C adoption authority differs")
    memory_pid = int(manifest["launches"]["job_memory"]["pid"])
    if not process_alive(memory_pid):
        raise RuntimeError("live memory-pair C controller is absent")
    successor = {
        "schema_version": "mysql_algorithm_c_staged_supervisor_successor_v1",
        "status": "ACTIVE",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "predecessor_manifest_sha256": sha(MANIFEST),
        "algorithm_contract": str(CONTRACT),
        "algorithm_contract_sha256": sha(CONTRACT),
        "supervisor_source": str(Path(__file__).resolve()),
        "supervisor_source_sha256": sha(Path(__file__).resolve()),
        "scene_refresh_source": str(SCENE_REFRESH),
        "scene_refresh_source_sha256": sha(SCENE_REFRESH),
        "memory_controller_adopted": True,
        "memory_controller_pid": memory_pid,
        "per_scene_b_completion_release": True,
        "sibling_scene_identity_refresh_required": False,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
    }
    successor_path = CAMPAIGN / "supervisor_successor.json"
    atomic(successor_path, successor)
    manifest.update({
        "schema_version": "mysql_algorithm_c_client_primary_staged_launch_v2",
        "supervisor_successor": str(successor_path),
        "supervisor_successor_sha256": sha(successor_path),
        "updated_at": now(),
    })
    atomic(MANIFEST, manifest)

    processes: dict[str, subprocess.Popen[str]] = {}
    logs: dict[str, Any] = {}
    try:
        while True:
            memory = verified_state(MEMORY_STATE)
            if memory.get("status") in {"FAILED", "FAILED_CLOSED"} or memory.get("error"):
                raise RuntimeError(f"memory-pair C failed: {memory.get('error')}")
            if memory.get("status") == "COMPLETE":
                project_memory_state(memory)
            elif not process_alive(memory_pid):
                raise RuntimeError("memory-pair C controller disappeared before completion")

            launches = dict(manifest.get("launches") or {})
            for scene, state_path in B_STATES.items():
                if scene in launches:
                    continue
                b = verified_state(state_path)
                if b.get("status") in {"FAILED", "FAILED_CLOSED"} or b.get("error"):
                    raise RuntimeError(f"B failed before C release: {scene}")
                if not (
                    b.get("status") == "COMPLETE"
                    and (b.get("converged") or {}).get(scene) is True
                ):
                    continue
                authority, fresh_r0 = prepare_scene(scene)
                suffix = SCENES[scene]
                run_id = f"{CAMPAIGN_ID}_{suffix}"
                log_path = CAMPAIGN / f"{suffix}.controller.log"
                log = log_path.open("w", encoding="utf-8")
                environment = os.environ.copy()
                environment["MYSQL_TWO_WORKER_AUTHORITY_ROOT"] = str(authority)
                process = subprocess.Popen([
                    sys.executable, str(CONTROLLER), "--run-id", run_id,
                    "--checkpoint", str(CHECKPOINT),
                    "--runtime-preflight", str(authority / "transition_preflight.json"),
                    "--prepared-binding", str(authority / "prepared_binding.json"),
                    "--benchmark-accounts", str(authority / "benchmark_accounts_binding.json"),
                    "--fresh-r0", str(fresh_r0),
                    "--replay-quarantine", str(QUARANTINE),
                    "--fresh-search", "--scene", scene,
                    "--scene-start-barrier", str(BARRIER),
                ], cwd=ROOT, env=environment, stdout=log,
                   stderr=subprocess.STDOUT, text=True, start_new_session=True)
                processes[scene] = process
                logs[scene] = log
                launches[scene] = {
                    "scene": scene,
                    "run_id": run_id,
                    "pid": process.pid,
                    "workers": 2,
                    "started_at": now(),
                    "log": str(log_path),
                    "authority_root": str(authority),
                    "fresh_r0": str(fresh_r0),
                    "release_reason": "same_scene_algorithm_b_complete",
                    "scene_scoped_authority_refresh": True,
                }
                manifest["launches"] = launches
                atomic(MANIFEST, manifest)

            for scene, process in tuple(processes.items()):
                code = process.poll()
                if code is None:
                    continue
                launches = dict(manifest.get("launches") or {})
                launches[scene].update({"exit_code": code, "completed_at": now()})
                manifest["launches"] = launches
                logs[scene].close()
                processes.pop(scene)

            launches = dict(manifest.get("launches") or {})
            running = sorted(
                scene for scene, row in launches.items()
                if "exit_code" not in row and (
                    scene not in {"job_memory", "tpch_sf4_memory"}
                    or memory.get("status") != "COMPLETE"
                )
            )
            pending = sorted(set(SCENES) - set(launches))
            manifest.update({
                "status": "RUNNING_STAGED",
                "updated_at": now(),
                "running_scenes": running,
                "pending_b_scenes": pending,
                "launches": launches,
            })
            if not pending and not running:
                passed = (
                    memory.get("status") == "COMPLETE"
                    and all(row.get("exit_code") == 0 for scene, row in launches.items()
                            if scene.startswith("hybench_xp"))
                )
                manifest.update({
                    "status": "COMPLETE" if passed else "FAILED_CLOSED",
                    "completed_at": now(),
                })
                successor.update({
                    "status": "COMPLETE" if passed else "FAILED_CLOSED",
                    "completed_at": now(),
                })
                atomic(successor_path, successor)
                manifest["supervisor_successor_sha256"] = sha(successor_path)
                atomic(MANIFEST, manifest)
                return 0 if passed else 4
            atomic(MANIFEST, manifest)
            time.sleep(30)
    except Exception as error:
        manifest.update({
            "status": "FAILED_CLOSED",
            "updated_at": now(),
            "error": f"{type(error).__name__}: {error}",
        })
        atomic(MANIFEST, manifest)
        raise
    finally:
        for log in logs.values():
            if not log.closed:
                log.close()


if __name__ == "__main__":
    raise SystemExit(main())

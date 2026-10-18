#!/usr/bin/env python3
"""Wait for disk2 release, then independently advance XP Top4 C -> B -> A."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
STATE = HISTORY / "v300_to_xp_top4_qps_abc_chain_20260826/pipeline_state.json"
C_LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_c_xp4_qps_rejection_v1.py"
B_LAUNCHER = ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_scene_v22.py"
A_LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_a_xp_top4_scene_v9.py"
LIVE_AUTHORITY_ROOT = HISTORY / "v290_to_xp4_live_authority_20260826"
SCENES = {"hybench_xp_cpu": "xp_cpu", "hybench_xp_io": "xp_io"}
EXTERNAL_CLEAR_SECONDS = 60.0
POLL_SECONDS = 15.0


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def maybe_load(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    candidate = Path(path)
    try:
        return load(candidate) if candidate.is_file() else {}
    except (OSError, ValueError, RuntimeError):
        return {}


def atomic(value: Mapping[str, Any]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = STATE.with_name(f".{STATE.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, STATE)
    STATE.with_name(STATE.name + ".sha256").write_text(
        f"{sha(STATE)}  {STATE.name}\n", encoding="ascii"
    )


def command(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, cwd=ROOT, capture_output=True, text=True,
        check=False, timeout=timeout,
    )


def external_blockers() -> dict[str, Any]:
    processes = command(["ps", "-eo", "pid=,args="], 15)
    process_rows = []
    for line in processes.stdout.splitlines():
        if (
            "run_mysql_four_scene_external_baselines.py" in line
            or "run_mysql_external_baseline_pipeline.sh" in line
        ) and "/home/anonymous/program/resource_adaptive" in line:
            process_rows.append(line.strip())
    containers = command(
        ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"], 30,
    )
    container_rows = [
        line.strip() for line in containers.stdout.splitlines()
        if line.strip().startswith("ra_mysql_ext_")
    ]
    return {
        "observed_at": now(),
        "external_mysql_controller_processes": process_rows,
        "external_mysql_running_containers": container_rows,
        "docker_inventory_returncode": containers.returncode,
        "clear": (
            processes.returncode == 0 and containers.returncode == 0
            and not process_rows and not container_rows
        ),
    }


def terminal(state: Mapping[str, Any], algorithm: str, scene: str) -> bool:
    return bool(
        state.get("algorithm") == algorithm
        and state.get("async_scene") == scene
        and state.get("status") == "COMPLETE"
        and state.get("stage") == "FINISHED"
        and not state.get("error")
        and (state.get("converged") or {}).get(scene) is True
    )


def compact(state: Mapping[str, Any], scene: str) -> dict[str, Any]:
    return {
        "status": state.get("status"), "stage": state.get("stage"),
        "outer_attempt": state.get("outer_attempt"),
        "accepted_rounds": (state.get("accepted_rounds") or {}).get(scene),
        "rejected_outer_attempts": (
            state.get("rejected_outer_attempts") or {}
        ).get(scene),
        "converged": (state.get("converged") or {}).get(scene),
        "error": state.get("error"),
    }


def invoke_c(scene: str, row: Mapping[str, Any]) -> dict[str, Any]:
    tag = f"v{int(row['next_c_tag']):03d}"
    result = command([
        str(PYTHON), str(C_LAUNCHER), "--scene", scene,
        "--campaign-tag", tag,
        "--attempt-number", str(int(row["next_c_attempt"])),
        "--live-authority-root", str(LIVE_AUTHORITY_ROOT),
    ], 600)
    return {
        "returncode": result.returncode,
        "stdout": result.stdout[-8000:], "stderr": result.stderr[-8000:],
        "campaign_tag": tag, "completed_at": now(),
    }


def invoke_b(scene: str, row: Mapping[str, Any]) -> dict[str, Any]:
    suffix = SCENES[scene]
    number = int(str(row["c_campaign_tag"])[1:]) + 1
    run_id = f"v{number:03d}_to_b_top4_qps_rejection_20260826_attempt001_{suffix}"
    pipeline = HISTORY / f"v{number:03d}_to_b_top4_qps_pipeline_20260826"
    authority = HISTORY / f"v{number:03d}_to_b_top4_qps_live_authority_20260826"
    unit = f"mysql-algb-top4-v{number:03d}-{suffix}.service"
    result = command([
        str(PYTHON), str(B_LAUNCHER), "--scene", scene,
        "--c-state", str(row["c_state"]), "--run-id", run_id,
        "--pipeline-root", str(pipeline), "--authority-root", str(authority),
        "--unit", unit,
    ], 1200)
    return {
        "returncode": result.returncode,
        "stdout": result.stdout[-8000:], "stderr": result.stderr[-8000:],
        "run_id": run_id, "state": str(HISTORY / run_id / "run_state.json"),
        "pipeline_root": str(pipeline), "unit": unit, "completed_at": now(),
    }


def invoke_a(scene: str, row: Mapping[str, Any]) -> dict[str, Any]:
    suffix = SCENES[scene]
    number = int(str(row["c_campaign_tag"])[1:]) + 2
    run_id = f"v{number:03d}_to_a_top4_fresh_20260826_attempt001_{suffix}"
    pipeline = HISTORY / f"v{number:03d}_to_a_top4_pipeline_20260826"
    authority = HISTORY / f"v{number:03d}_to_a_top4_live_authority_20260826"
    unit = f"mysql-alga-top4-v{number:03d}-{suffix}.service"
    result = command([
        str(PYTHON), str(A_LAUNCHER), "--scene", scene,
        "--b-state", str(row["b_state"]), "--run-id", run_id,
        "--pipeline-root", str(pipeline), "--authority-root", str(authority),
        "--unit", unit,
    ], 1200)
    return {
        "returncode": result.returncode,
        "stdout": result.stdout[-8000:], "stderr": result.stderr[-8000:],
        "run_id": run_id,
        "state": str(HISTORY / run_id / "algorithm_a_state.json"),
        "pipeline_root": str(pipeline), "unit": unit, "completed_at": now(),
    }


def parse_last_json(text: str) -> dict[str, Any]:
    for line in reversed(text.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, Mapping):
            return dict(value)
    return {}


def initial() -> dict[str, Any]:
    return {
        "schema_version": "mysql_xp_top4_qps_abc_chain_v1",
        "status": "WAITING_EXTERNAL_MYSQL_DISK2_RELEASE",
        "started_at": now(), "updated_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "four_scene_barrier": False, "sibling_scene_wait": False,
        "lease_mode": "none", "physical_workers_per_xp_scene": 4,
        "maximum_concurrent_xp_mysql_workers": 8,
        "external_clear_since_epoch": None,
        "scenes": {
            scene: {
                "phase": "WAITING_EXTERNAL_MYSQL_DISK2_RELEASE",
                "next_c_tag": 297, "next_c_attempt": 10,
                "c_launch_attempts": [], "b_launch_attempts": [],
                "a_launch_attempts": [],
            }
            for scene in SCENES
        },
    }


def main() -> int:
    document = load(STATE) if STATE.is_file() else initial()
    while True:
        blockers = external_blockers()
        document["external_blockers"] = blockers
        if blockers["clear"]:
            if document.get("external_clear_since_epoch") is None:
                document["external_clear_since_epoch"] = time.time()
        else:
            document["external_clear_since_epoch"] = None
        clear_for = (
            0.0 if document.get("external_clear_since_epoch") is None
            else max(0.0, time.time() - float(document["external_clear_since_epoch"]))
        )
        document["external_clear_seconds"] = clear_for

        c_launchable = []
        b_launchable = []
        a_launchable = []
        for scene, row in document["scenes"].items():
            phase = row["phase"]
            c_state = maybe_load(row.get("c_state"))
            b_state = maybe_load(row.get("b_state"))
            a_state = maybe_load(row.get("a_state"))
            if c_state:
                row["c_progress"] = compact(c_state, scene)
            if b_state:
                row["b_progress"] = compact(b_state, scene)
            if a_state:
                row["a_progress"] = compact(a_state, scene)
            if phase == "C_RUNNING":
                if terminal(c_state, "C", scene):
                    row["phase"] = "READY_TO_LAUNCH_B"
                    b_launchable.append(scene)
                elif c_state.get("status") in {"FAILED", "FAILED_CLOSED"}:
                    accepted = int((c_state.get("accepted_rounds") or {}).get(scene) or 0)
                    outer = int(c_state.get("outer_attempt") or 0)
                    if accepted == 0 and outer == 0:
                        row["phase"] = "WAITING_EXTERNAL_MYSQL_DISK2_RELEASE"
                        row["next_c_tag"] = int(row["next_c_tag"]) + 2
                        row["next_c_attempt"] = int(row["next_c_attempt"]) + 1
                    else:
                        row["phase"] = "C_FAILURE_REQUIRES_BOUNDARY_RECOVERY"
            elif phase == "B_RUNNING":
                if terminal(b_state, "B", scene):
                    row["phase"] = "READY_TO_LAUNCH_A"
                    a_launchable.append(scene)
                elif b_state.get("status") in {"FAILED", "FAILED_CLOSED"}:
                    row["phase"] = "B_FAILURE_REQUIRES_BOUNDARY_RECOVERY"
            elif phase == "A_RUNNING":
                if terminal(a_state, "A", scene):
                    row["phase"] = "COMPLETE"
                    row["completed_at"] = now()
                elif a_state.get("status") in {"FAILED", "FAILED_CLOSED"}:
                    row["phase"] = "A_FAILURE_REQUIRES_BOUNDARY_RECOVERY"
            if (
                row["phase"] == "WAITING_EXTERNAL_MYSQL_DISK2_RELEASE"
                and clear_for >= EXTERNAL_CLEAR_SECONDS
            ):
                c_launchable.append(scene)
            elif row["phase"] == "READY_TO_LAUNCH_B":
                b_launchable.append(scene)
            elif row["phase"] == "READY_TO_LAUNCH_A":
                a_launchable.append(scene)

        for scenes, phase, launcher in (
            (sorted(set(c_launchable)), "C", invoke_c),
            (sorted(set(b_launchable)), "B", invoke_b),
            (sorted(set(a_launchable)), "A", invoke_a),
        ):
            if not scenes:
                continue
            for scene in scenes:
                document["scenes"][scene]["phase"] = f"LAUNCHING_{phase}"
            document["updated_at"] = now()
            atomic(document)
            with ThreadPoolExecutor(max_workers=len(scenes)) as pool:
                futures = {
                    pool.submit(launcher, scene, document["scenes"][scene]): scene
                    for scene in scenes
                }
                for future in as_completed(futures):
                    scene = futures[future]
                    row = document["scenes"][scene]
                    try:
                        result = future.result()
                    except Exception as exc:  # preserve monitor liveness
                        result = {
                            "returncode": 99, "stdout": "",
                            "stderr": f"{type(exc).__name__}: {exc}",
                            "completed_at": now(),
                        }
                    row[f"{phase.lower()}_launch_attempts"].append(result)
                    if result["returncode"] == 0:
                        if phase == "C":
                            parsed = parse_last_json(result["stdout"])
                            row["c_campaign_tag"] = result["campaign_tag"]
                            row["c_state"] = parsed.get("state") or str(
                                HISTORY
                                / f"{result['campaign_tag']}_to_c_top4_qps_rejection_20260826_attempt{int(row['next_c_attempt']):03d}_{SCENES[scene]}"
                                / "run_state.json"
                            )
                        else:
                            row[f"{phase.lower()}_state"] = result["state"]
                        row["phase"] = f"{phase}_RUNNING"
                    else:
                        if phase == "C":
                            row["phase"] = "WAITING_EXTERNAL_MYSQL_DISK2_RELEASE"
                            row["next_c_tag"] = int(row["next_c_tag"]) + 2
                            row["next_c_attempt"] = int(row["next_c_attempt"]) + 1
                        else:
                            row["phase"] = f"READY_TO_LAUNCH_{phase}"

        phases = {row["phase"] for row in document["scenes"].values()}
        document["status"] = (
            "COMPLETE" if phases == {"COMPLETE"}
            else "REQUIRES_BOUNDARY_RECOVERY" if any(
                "REQUIRES_BOUNDARY_RECOVERY" in phase for phase in phases
            )
            else "RUNNING" if any(
                phase.endswith("_RUNNING") or phase.startswith("LAUNCHING_")
                for phase in phases
            )
            else "WAITING_EXTERNAL_MYSQL_DISK2_RELEASE"
        )
        document["updated_at"] = now()
        atomic(document)
        if document["status"] == "COMPLETE":
            document["completed_at"] = now()
            atomic(document)
            return 0
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())

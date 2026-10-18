#!/usr/bin/env python3
"""Launch four independent MySQL Algorithm B scene controllers."""

from __future__ import annotations

import argparse
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
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python").resolve()
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_async_scene_v36.py"
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_async_outer_20260819_v36"
    / "algorithm_b_async_contract.json"
)
RESUME_SOURCE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v35_formal_pg_outer_adaptive_20260819_attempt034"
)
HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
MANAGEMENT_CPUSET = "240-241,496-497"
SCENES = {
    "job_memory": "job",
    "tpch_sf4_memory": "tpch",
    "hybench_xp_cpu": "xp_cpu",
    "hybench_xp_io": "xp_io",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
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


def unit_fields(unit: str) -> dict[str, str]:
    shown = subprocess.run(
        [
            "systemctl", "--user", "show", unit,
            "-p", "ActiveState", "-p", "SubState", "-p", "MainPID",
            "-p", "Result", "-p", "ExecMainStatus",
        ],
        cwd=ROOT, text=True, capture_output=True, check=True, timeout=15,
    ).stdout
    return dict(line.split("=", 1) for line in shown.splitlines() if "=" in line)


def stop_units(units: list[str]) -> None:
    for unit in units:
        subprocess.run(
            ["systemctl", "--user", "stop", unit], cwd=ROOT,
            text=True, capture_output=True, check=False, timeout=30,
        )


def validate_contract() -> dict[str, Any]:
    contract = load(CONTRACT)
    if (
        contract.get("release_version") != "v36"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("four_scene_wave_barrier") is not False
        or contract.get("per_scene_outer_state_machine") is not True
        or contract.get("recalibration_required") is not False
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
    ):
        raise RuntimeError("Algorithm B asynchronous launch contract differs")
    return contract


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--startup-timeout-seconds", type=float, default=1800.0)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,110}", args.campaign_id) is None:
        parser.error("unsafe campaign id")
    if args.startup_timeout_seconds < 120:
        parser.error("startup timeout is too small for four authority readbacks")
    if not PYTHON.is_file() or PYTHON.is_symlink():
        raise RuntimeError("sealed Python interpreter absent")
    contract = validate_contract()
    run_ids = {
        scene: f"{args.campaign_id}_{suffix}" for scene, suffix in SCENES.items()
    }
    outputs = {scene: HISTORY / run_id for scene, run_id in run_ids.items()}
    if any(path.exists() or path.is_symlink() for path in outputs.values()):
        raise RuntimeError("fresh per-scene Algorithm B outputs are required")
    barrier_root = Path("/tmp/mysql_async_outer_start") / args.campaign_id / "algorithm_b"
    if barrier_root.exists() or barrier_root.is_symlink():
        raise RuntimeError(f"fresh startup barrier required: {barrier_root}")
    barrier_root.mkdir(parents=True, mode=0o700)
    LOG_ROOT.mkdir(parents=True, exist_ok=True)

    units: dict[str, str] = {}
    logs: dict[str, dict[str, str]] = {}
    started_units: list[str] = []
    try:
        for scene, run_id in run_ids.items():
            unit = f"mysql-algb-async-{run_id}.service"
            stdout = LOG_ROOT / f"{run_id}.stdout.log"
            stderr = LOG_ROOT / f"{run_id}.stderr.log"
            command = [
                "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
                f"--description=MySQL Algorithm B independent scene {scene}",
                f"--property=WorkingDirectory={ROOT}",
                "--property=KillMode=control-group",
                f"--property=StandardOutput=append:{stdout}",
                f"--property=StandardError=append:{stderr}",
                "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET, str(PYTHON),
                str(CONTROLLER), "--run-id", run_id,
                "--resume-from", str(RESUME_SOURCE),
                "--scene", scene,
                "--scene-start-barrier", str(barrier_root),
            ]
            started = subprocess.run(
                command, cwd=ROOT, text=True, capture_output=True,
                check=False, timeout=30,
            )
            if started.returncode != 0:
                raise RuntimeError(
                    f"{scene} Algorithm B launch failed: {started.stderr[-1000:]}"
                )
            units[scene] = unit
            logs[scene] = {"stdout": str(stdout), "stderr": str(stderr)}
            started_units.append(unit)

        deadline = time.monotonic() + args.startup_timeout_seconds
        states: dict[str, dict[str, Any]] = {}
        fields: dict[str, dict[str, str]] = {}
        while time.monotonic() < deadline:
            ready = True
            for scene, unit in units.items():
                fields[scene] = unit_fields(unit)
                if fields[scene].get("ActiveState") in {"inactive", "failed"}:
                    raise RuntimeError(
                        f"{scene} Algorithm B exited during startup: {fields[scene]}; "
                        f"stderr={logs[scene]['stderr']}"
                    )
                state_path = outputs[scene] / "run_state.json"
                if not state_path.is_file():
                    ready = False
                    continue
                state = load(state_path)
                states[scene] = state
                if not (
                    state.get("status") == "RUNNING"
                    and state.get("algorithm") == "B"
                    and state.get("async_scene") == scene
                    and state.get("four_scene_wave_barrier") is False
                    and state.get("other_scene_completion_required") is False
                ):
                    ready = False
            if ready and len(states) == len(SCENES):
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("asynchronous Algorithm B startup timed out")
    except BaseException:
        stop_units(started_units)
        raise

    manifest = LOG_ROOT / f"{args.campaign_id}.async_b.launch_manifest.json"
    atomic(manifest, {
        "schema_version": "mysql_algorithm_b_async_scene_launch_manifest_v1",
        "status": "LAUNCHED",
        "launched_at": datetime.now(timezone.utc).isoformat(),
        "campaign_id": args.campaign_id,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "four_scene_wave_barrier": False,
        "per_scene_outer_state_machine": True,
        "scene_may_advance_immediately": True,
        "recalibration_performed": False,
        "external_observer_or_watchdog_started": False,
        "startup_barrier": str(barrier_root),
        "startup_barrier_is_per_round": False,
        "candidate_batches": [[1, 2], [3, 4], [5]],
        "controller_path": str(CONTROLLER),
        "controller_sha256": sha(CONTROLLER),
        "contract_path": str(CONTRACT),
        "contract_sha256": sha(CONTRACT),
        "contract_release": contract.get("release_version"),
        "scenes": {
            scene: {
                "run_id": run_ids[scene],
                "unit": units[scene],
                "main_pid": int(fields[scene].get("MainPID") or 0),
                "output": str(outputs[scene]),
                "run_state": str(outputs[scene] / "run_state.json"),
                "stage": states[scene].get("stage"),
                **logs[scene],
            }
            for scene in SCENES
        },
    })
    print(json.dumps({
        "status": "LAUNCHED",
        "campaign_id": args.campaign_id,
        "manifest": str(manifest),
        "units": units,
        "stages": {scene: states[scene].get("stage") for scene in SCENES},
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

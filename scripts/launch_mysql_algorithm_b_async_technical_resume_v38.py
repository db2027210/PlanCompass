#!/usr/bin/env python3
"""Launch one asynchronous Algorithm B scene from a completed outer boundary."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import launch_mysql_algorithm_b_async_v37 as launcher
from scripts import run_mysql_algorithm_b_async_scene_v36 as controller


ROOT = launcher.ROOT
HISTORY = launcher.HISTORY
LOG_ROOT = launcher.LOG_ROOT
PYTHON = launcher.PYTHON
CONTROLLER = launcher.CONTROLLER
MANAGEMENT_CPUSET = launcher.MANAGEMENT_CPUSET


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--resume-from", type=Path, required=True)
    parser.add_argument("--scene", choices=tuple(launcher.SCENES))
    parser.add_argument("--scene-start-barrier", type=Path, required=True)
    parser.add_argument("--startup-timeout-seconds", type=float, default=1800.0)
    parser.add_argument("--prepared-binding-override", type=Path)
    parser.add_argument("--benchmark-accounts-override", type=Path)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run id")
    if args.startup_timeout_seconds < 120:
        parser.error("startup timeout is too small")
    source = args.resume_from.expanduser().resolve()
    evidence = controller.validate_completed_scene_technical_resume(source)
    source_scene = list(evidence["state"]["active_scenes"])
    if source_scene != [args.scene]:
        raise RuntimeError("technical-resume source scene differs")
    contract = controller.validate_contract()
    barrier = args.scene_start_barrier.expanduser().resolve()
    expected_ready = {
        f"{scene}.ready.json" for scene in launcher.SCENES
    }
    if (
        not barrier.is_dir()
        or {path.name for path in barrier.glob("*.ready.json")}
        != expected_ready
    ):
        raise RuntimeError("technical-resume startup barrier differs")
    output = HISTORY / args.run_id
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh technical-resume output required: {output}")
    unit = f"mysql-algb-async-{args.run_id}.service"
    stdout = LOG_ROOT / f"{args.run_id}.stdout.log"
    stderr = LOG_ROOT / f"{args.run_id}.stderr.log"
    environment_options: list[str] = []
    if args.prepared_binding_override or args.benchmark_accounts_override:
        if not (args.prepared_binding_override and args.benchmark_accounts_override):
            raise RuntimeError("technical live authority overrides must be paired")
        prepared_override = args.prepared_binding_override.expanduser().resolve()
        accounts_override = args.benchmark_accounts_override.expanduser().resolve()
        if not prepared_override.is_file() or not accounts_override.is_file():
            raise RuntimeError("technical live authority override absent")
        environment_options = [
            f"--setenv=MYSQL_B_TECHNICAL_PREPARED={prepared_override}",
            f"--setenv=MYSQL_B_TECHNICAL_ACCOUNTS={accounts_override}",
        ]
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        f"--description=MySQL Algorithm B technical resume {args.scene}",
        f"--property=WorkingDirectory={ROOT}",
        "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        *environment_options,
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET, str(PYTHON),
        str(CONTROLLER), "--run-id", args.run_id,
        "--resume-from", str(source), "--scene", args.scene,
        "--scene-start-barrier", str(barrier),
    ]
    started = subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True,
        check=False, timeout=30,
    )
    if started.returncode != 0:
        raise RuntimeError(f"technical resume launch failed: {started.stderr[-1000:]}")

    state_path = output / "run_state.json"
    deadline = time.monotonic() + args.startup_timeout_seconds
    state = None
    fields = None
    while time.monotonic() < deadline:
        fields = launcher.unit_fields(unit)
        if fields.get("ActiveState") in {"inactive", "failed"}:
            raise RuntimeError(
                f"technical resume exited during startup: {fields}; stderr={stderr}"
            )
        if state_path.is_file():
            state = launcher.load(state_path)
            restored = state.get("completed_outer_dynamic_support_restore") or {}
            if (
                state.get("status") == "RUNNING"
                and state.get("async_scene") == args.scene
                and int(restored.get("completed_outer_attempt") or 0)
                == int(evidence["completed_outer_attempt"])
                and int((state.get("accepted_rounds") or {}).get(args.scene, -1))
                == int((evidence["state"]["accepted_rounds"])[args.scene])
                and state.get("four_scene_wave_barrier") is False
            ):
                break
        time.sleep(0.5)
    else:
        subprocess.run(
            ["systemctl", "--user", "stop", unit], cwd=ROOT,
            text=True, capture_output=True, check=False, timeout=30,
        )
        raise RuntimeError("technical resume startup timed out")
    assert state is not None and fields is not None

    manifest = LOG_ROOT / f"{args.run_id}.algorithm_b_technical_resume.launch_manifest.json"
    launcher.atomic(manifest, {
        "schema_version": "mysql_algorithm_b_async_technical_resume_launch_v1",
        "status": "LAUNCHED",
        "launched_at": datetime.now(timezone.utc).isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": args.scene,
        "run_id": args.run_id,
        "unit": unit,
        "main_pid": int(fields.get("MainPID") or 0),
        "output": str(output),
        "run_state": str(state_path),
        "stage": state.get("stage"),
        "accepted_rounds": state.get("accepted_rounds"),
        "source_run_root": str(source),
        "source_state_sha256": evidence["state_sha256"],
        "completed_outer_attempt_inherited": evidence["completed_outer_attempt"],
        "interrupted_outer_attempt_discarded": evidence["interrupted_outer_attempt"],
        "interrupted_outer_measurement_credit": False,
        "technical_failure": evidence["technical_failure"],
        "scene_start_barrier": str(barrier),
        "four_scene_wave_barrier": False,
        "other_scene_completion_required": False,
        "recalibration_performed": False,
        "controller_path": str(CONTROLLER),
        "controller_sha256": launcher.sha(CONTROLLER),
        "contract_path": str(controller.CONTRACT),
        "contract_sha256": launcher.sha(controller.CONTRACT),
        "contract_release": contract.get("release_version"),
        "stdout": str(stdout),
        "stderr": str(stderr),
    })
    print(json.dumps({
        "status": "LAUNCHED", "run_id": args.run_id,
        "unit": unit, "manifest": str(manifest),
        "stage": state.get("stage"),
        "accepted_rounds": state.get("accepted_rounds"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

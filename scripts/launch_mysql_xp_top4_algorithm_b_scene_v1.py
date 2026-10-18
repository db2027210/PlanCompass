#!/usr/bin/env python3
"""Launch fresh Top-4 Algorithm B for one completed Algorithm C XP scene."""

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
HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v1.py"
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v10"
    / "algorithm_b_top4_contract.json"
)
MANAGEMENT_CPUSET = "240-241,496-497"
SCENES = {
    "hybench_xp_cpu": {
        "suffix": "xp_cpu",
        "c_state": HISTORY / "v59_to_c_xp_top4_20260825_attempt033_cpu_zero_safe_xp_cpu/run_state.json",
        "bootstrap": HISTORY / "v161_to_b_top4_fresh_bootstrap_20260826_v10_cpu",
    },
    "hybench_xp_io": {
        "suffix": "xp_io",
        "c_state": HISTORY / "v59_to_c_xp_top4_20260825_attempt034_io_zero_safe_xp_io/run_state.json",
        "bootstrap": HISTORY / "v161_to_b_top4_fresh_bootstrap_20260826_v10_io",
    },
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=sorted(SCENES), required=True)
    args = parser.parse_args()
    scene = args.scene
    row = SCENES[scene]
    suffix = str(row["suffix"])
    c_path = Path(row["c_state"])
    c_state = load(c_path)
    if not (
        c_state.get("algorithm") == "C"
        and c_state.get("async_scene") == scene
        and c_state.get("status") in {"COMPLETE", "NON_CONVERGED"}
        and c_state.get("stage") == "FINISHED"
        and not c_state.get("error")
        and c_state.get("active_scenes") == [scene]
        and (c_state.get("converged") or {}).get(scene) is True
        and int((c_state.get("accepted_rounds") or {}).get(scene) or 0) >= 10
    ):
        raise RuntimeError(f"{scene} Algorithm C is not complete")
    run_id = f"v161_to_b_top4_fresh_20260826_attempt008_{suffix}"
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", run_id) is None:
        raise RuntimeError("unsafe B run id")
    output = HISTORY / run_id
    result_path = HISTORY / "v161_to_b_top4_pipeline_20260826" / f"{suffix}.launch.json"
    if result_path.is_file():
        result = load(result_path)
        if result.get("status") == "LAUNCHED":
            prior_state = load(Path(str(result.get("state") or "")))
            if prior_state.get("status") in {"RUNNING", "COMPLETE"}:
                print(json.dumps(result, sort_keys=True))
                return 0
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh B output required: {output}")
    bootstrap = Path(row["bootstrap"])
    if not (bootstrap / "bootstrap_manifest.json").is_file():
        raise RuntimeError(f"{scene} frozen B bootstrap absent")
    unit = f"mysql-algb-top4-v161-{suffix}.service"
    stdout = LOG_ROOT / f"{run_id}.stdout.log"
    stderr = LOG_ROOT / f"{run_id}.stderr.log"
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        f"--description=MySQL fresh Top-4 Algorithm B {scene}",
        f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,
        str(PYTHON), str(CONTROLLER), "--run-id", run_id,
        "--resume-from", str(bootstrap), "--scene", scene,
    ]
    launched = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=30,
    )
    if launched.returncode != 0:
        raise RuntimeError(f"{scene} B launch failed: {launched.stderr[-2000:]}")
    state_path = output / "run_state.json"
    deadline = time.monotonic() + 300.0
    while time.monotonic() < deadline:
        shown = subprocess.run(
            ["systemctl", "--user", "show", unit, "-p", "ActiveState", "-p", "MainPID"],
            cwd=ROOT, capture_output=True, text=True, check=False, timeout=15,
        ).stdout
        if "ActiveState=failed" in shown or "ActiveState=inactive" in shown:
            raise RuntimeError(f"{scene} B exited during startup: {shown}; stderr={stderr}")
        if state_path.is_file():
            state = load(state_path)
            if (
                state.get("algorithm") == "B" and state.get("async_scene") == scene
                and state.get("status") == "RUNNING"
                and state.get("four_scene_wave_barrier") is False
                and state.get("other_scene_completion_required") is False
            ):
                result = {
                    "schema_version": "mysql_algorithm_b_xp_top4_scene_launch_v1",
                    "status": "LAUNCHED", "launched_at": now(), "scene": scene,
                    "formal_scope": "mysql_only",
                    "cross_engine_comparison_prohibited": True,
                    "algorithm_c_sequence_gate_path": str(c_path),
                    "algorithm_c_sequence_gate_sha256": sha(c_path),
                    "algorithm_c_state_or_result_credit": False,
                    "run_id": run_id, "unit": unit, "output": str(output),
                    "state": str(state_path), "stdout": str(stdout), "stderr": str(stderr),
                    "contract_path": str(CONTRACT), "contract_sha256": sha(CONTRACT),
                }
                atomic(result_path, result)
                print(json.dumps(result, sort_keys=True))
                return 0
        time.sleep(1.0)
    raise TimeoutError(f"{scene} B startup evidence timed out")


if __name__ == "__main__":
    raise SystemExit(main())

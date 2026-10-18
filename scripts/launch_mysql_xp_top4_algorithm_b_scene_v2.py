#!/usr/bin/env python3
"""Launch fresh Top4 B with a just-refreshed per-scene XP4 authority."""

from __future__ import annotations

import argparse
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
HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v2.py"
REFRESH = ROOT / "scripts/refresh_mysql_xp4_live_authority_from_reset_v1.py"
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v11"
    / "algorithm_b_top4_contract.json"
)
MANAGEMENT_CPUSET = "240-241,496-497"
SCENES = {
    "hybench_xp_cpu": {
        "suffix": "xp_cpu",
        "c_state": HISTORY / "v59_to_c_xp_top4_20260825_attempt033_cpu_zero_safe_xp_cpu/run_state.json",
        "bootstrap": HISTORY / "v161_to_b_top4_fresh_bootstrap_20260826_v11_cpu",
        "reset_binding": HISTORY / "v161_to_b_top4_fresh_20260826_attempt008_xp_cpu/xp_resets/algorithm_b_independent_fresh_baseline/reset_binding.json",
    },
    "hybench_xp_io": {
        "suffix": "xp_io",
        "c_state": HISTORY / "v178_to_c_top4_io_xp4_20260826_attempt011_xp_io/run_state.json",
        "bootstrap": HISTORY / "v161_to_b_top4_fresh_bootstrap_20260826_v11_io",
        "reset_binding": None,
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


def latest_reset_binding(scene: str, c_root: Path) -> Path:
    prefix = "xp_cpu" if scene == "hybench_xp_cpu" else "xp_io"
    expected = {f"{prefix}_w{index}" for index in range(1, 5)}
    candidates = sorted(
        c_root.rglob("reset_binding.json"),
        key=lambda path: path.stat().st_mtime, reverse=True,
    )
    for path in candidates:
        value = load(path)
        worker_ids = {
            str(row.get("worker_id")) for row in value.get("workers") or []
            if row.get("worker_id") in expected
        }
        if value.get("status") == "PASS" and worker_ids == expected:
            return path
    raise RuntimeError(f"{scene} has no completed four-worker reset binding")


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
    run_id = f"v179_to_b_top4_fresh_20260826_attempt009_{suffix}"
    output = HISTORY / run_id
    result_path = HISTORY / "v179_to_b_top4_pipeline_20260826" / f"{suffix}.launch.json"
    if result_path.is_file():
        result = load(result_path)
        state_path = Path(str(result.get("state") or ""))
        if result.get("status") == "LAUNCHED" and state_path.is_file():
            if load(state_path).get("status") in {"RUNNING", "COMPLETE"}:
                print(json.dumps(result, sort_keys=True))
                return 0
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh B output required: {output}")
    bootstrap = Path(row["bootstrap"])
    if not (bootstrap / "bootstrap_manifest.json").is_file():
        raise RuntimeError(f"{scene} frozen B bootstrap absent")
    reset_binding = (
        Path(row["reset_binding"])
        if row["reset_binding"] is not None
        else latest_reset_binding(scene, c_path.parent)
    )
    authority_output = (
        HISTORY / "v179_to_b_top4_live_authority_20260826" / scene
    )
    refreshed = subprocess.run(
        [str(PYTHON), str(REFRESH), "--scene", scene,
         "--reset-binding", str(reset_binding), "--output", str(authority_output)],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=180,
    )
    if refreshed.returncode != 0:
        raise RuntimeError(f"{scene} current authority refresh failed: {refreshed.stderr[-3000:]}")
    live = load(authority_output / "live_authority.json")
    if live.get("status") != "PASS" or live.get("scene") != scene:
        raise RuntimeError(f"{scene} current authority differs")
    authority_root = authority_output / "authority"
    unit = f"mysql-algb-top4-v179-{suffix}.service"
    stdout = LOG_ROOT / f"{run_id}.stdout.log"
    stderr = LOG_ROOT / f"{run_id}.stderr.log"
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        f"--description=MySQL fresh Top-4 Algorithm B successor {scene}",
        f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,
        "/usr/bin/env", f"MYSQL_XP4_AUTHORITY_ROOT={authority_root}",
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
                    "schema_version": "mysql_algorithm_b_xp_top4_scene_launch_v2",
                    "status": "LAUNCHED", "launched_at": now(), "scene": scene,
                    "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
                    "algorithm_c_sequence_gate_path": str(c_path),
                    "algorithm_c_sequence_gate_sha256": sha(c_path),
                    "algorithm_c_state_or_result_credit": False,
                    "authority_refresh_path": str(authority_output / "live_authority.json"),
                    "authority_refresh_sha256": sha(authority_output / "live_authority.json"),
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

#!/usr/bin/env python3
"""Launch one memory-scene Algorithm B after its v60 Algorithm C ends."""

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
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_async_scene_v37.py"
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_async_outer_20260826_v38"
    / "algorithm_b_async_contract.json"
)
RESUME_SOURCE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v35_formal_pg_outer_adaptive_20260819_attempt034"
)
HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
MANAGEMENT_CPUSET = "240-241,496-497"
SCENES = {"job_memory": "job", "tpch_sf4_memory": "tpch"}


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
    parser.add_argument("--c-state", type=Path, required=True)
    parser.add_argument("--campaign-id", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,110}", args.campaign_id) is None:
        parser.error("unsafe campaign id")
    scene = args.scene
    suffix = SCENES[scene]
    c_path = args.c_state.expanduser().resolve()
    c_state = load(c_path)
    if not (
        c_state.get("algorithm") == "C"
        and c_state.get("async_scene") == scene
        and c_state.get("stage") == "FINISHED"
        and c_state.get("status") in {"COMPLETE", "NON_CONVERGED"}
        and (c_state.get("converged") or {}).get(scene) is True
        and int((c_state.get("accepted_rounds") or {}).get(scene) or 0) >= 10
        and not c_state.get("error")
    ):
        raise RuntimeError(f"{scene} v60 Algorithm C is not complete")
    run_id = f"{args.campaign_id}_{suffix}"
    output = HISTORY / run_id
    receipt = HISTORY / args.campaign_id / f"{suffix}.launch.json"
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh Algorithm B output required: {output}")
    barrier = Path("/tmp/mysql_async_outer_start") / run_id / "algorithm_b"
    barrier.mkdir(parents=True, mode=0o700)
    unit = f"mysql-algb-qps-{run_id}.service"
    stdout = LOG_ROOT / f"{run_id}.stdout.log"
    stderr = LOG_ROOT / f"{run_id}.stderr.log"
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        f"--description=MySQL QPS-only Algorithm B {scene}",
        f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,
        str(PYTHON), str(CONTROLLER), "--run-id", run_id,
        "--resume-from", str(RESUME_SOURCE), "--scene", scene,
        "--scene-start-barrier", str(barrier),
    ]
    launched = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=30,
    )
    if launched.returncode != 0:
        raise RuntimeError(f"{scene} Algorithm B launch failed: {launched.stderr[-2000:]}")
    state_path = output / "run_state.json"
    deadline = time.monotonic() + 900.0
    while time.monotonic() < deadline:
        if state_path.is_file():
            state = load(state_path)
            if (
                state.get("status") == "RUNNING"
                and state.get("algorithm") == "B"
                and state.get("async_scene") == scene
                and state.get("four_scene_wave_barrier") is False
            ):
                document = {
                    "schema_version": "mysql_algorithm_b_qps_only_scene_launch_v1",
                    "status": "LAUNCHED",
                    "launched_at": datetime.now(timezone.utc).astimezone().isoformat(),
                    "scene": scene,
                    "formal_scope": "mysql_only",
                    "cross_engine_comparison_prohibited": True,
                    "algorithm_c_sequence_gate_path": str(c_path),
                    "algorithm_c_sequence_gate_sha256": sha(c_path),
                    "algorithm_c_state_credit_reused": False,
                    "run_id": run_id,
                    "unit": unit,
                    "state": str(state_path),
                    "output": str(output),
                    "contract_path": str(CONTRACT),
                    "contract_sha256": sha(CONTRACT),
                    "stdout": str(stdout),
                    "stderr": str(stderr),
                }
                atomic(receipt, document)
                print(json.dumps(document, ensure_ascii=False, sort_keys=True))
                return 0
        time.sleep(1.0)
    raise TimeoutError(f"{scene} Algorithm B startup evidence timed out")


if __name__ == "__main__":
    raise SystemExit(main())

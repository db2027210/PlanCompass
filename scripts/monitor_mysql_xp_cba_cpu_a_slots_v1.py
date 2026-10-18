#!/usr/bin/env python3
"""Independently advance one XP scene through terminal C -> fresh B -> fresh A."""

from __future__ import annotations

import argparse
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
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python").resolve()
B_LAUNCHER = ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_cpu_a_slots_scene_v2.py"
A_LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_a_xp_top4_cpu_a_slots_scene_v2.py"
SCENE_SUFFIX = {"hybench_xp_cpu": "xp_cpu", "hybench_xp_io": "xp_io"}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


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


def history_path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if HISTORY.resolve() not in path.parents:
        raise argparse.ArgumentTypeError("path must be beneath formal history")
    return path


def terminal(state: Mapping[str, Any], algorithm: str, scene: str) -> bool:
    converged = state.get("converged")
    if isinstance(converged, Mapping):
        converged = converged.get(scene)
    return bool(
        state.get("algorithm") == algorithm
        and state.get("async_scene") == scene
        and state.get("status") == "COMPLETE"
        and state.get("stage") == "FINISHED"
        and not state.get("error")
        and converged is True
    )


def compact(state: Mapping[str, Any], scene: str) -> dict[str, Any]:
    accepted = state.get("accepted")
    if accepted is None:
        accepted = (state.get("accepted_rounds") or {}).get(scene)
    return {
        "status": state.get("status"), "stage": state.get("stage"),
        "accepted": accepted,
        "round_or_outer": state.get("round_index", state.get("outer_attempt")),
        "converged": (
            (state.get("converged") or {}).get(scene)
            if isinstance(state.get("converged"), Mapping)
            else state.get("converged")
        ),
        "error": state.get("error"),
    }


def invoke(argv: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        argv, cwd=ROOT, text=True, capture_output=True, check=False, timeout=1200,
    )
    return {
        "attempted_at": now(), "argv": argv,
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-6000:],
        "stderr_tail": completed.stderr[-6000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=sorted(SCENE_SUFFIX), required=True)
    parser.add_argument("--c-state", type=history_path, required=True)
    parser.add_argument("--source-authority", type=history_path, required=True)
    parser.add_argument("--pipeline-root", type=history_path, required=True)
    parser.add_argument("--b-run-id", required=True)
    parser.add_argument("--b-unit", required=True)
    parser.add_argument("--a-run-id", required=True)
    parser.add_argument("--a-unit", required=True)
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    parser.add_argument("--maximum-wait-seconds", type=float, default=259200.0)
    args = parser.parse_args()
    scene = args.scene
    suffix = SCENE_SUFFIX[scene]
    if not args.b_run_id.endswith(f"_{suffix}") or not args.a_run_id.endswith(f"_{suffix}"):
        parser.error("B/A run ids must use the exact scene suffix")
    if any("/" in value or not value.endswith(".service") for value in (args.b_unit, args.a_unit)):
        parser.error("B/A units must be .service basenames")
    if args.poll_seconds <= 0 or args.maximum_wait_seconds <= 0:
        parser.error("positive wait bounds required")
    pipeline = args.pipeline_root
    state_path = pipeline / "pipeline_state.json"
    b_output = HISTORY / args.b_run_id
    a_output = HISTORY / args.a_run_id
    b_state_path = b_output / "run_state.json"
    a_state_path = a_output / "algorithm_a_state.json"
    b_authority_root = pipeline / "b_live_authority"
    a_authority_root = pipeline / "a_live_authority"
    pipeline.mkdir(parents=True, exist_ok=True, mode=0o700)
    deadline = time.monotonic() + args.maximum_wait_seconds
    attempts: dict[str, list[dict[str, Any]]] = {"B": [], "A": []}
    next_attempt = {"B": 0.0, "A": 0.0}
    while time.monotonic() < deadline:
        c_state = load(args.c_state)
        b_state = load(b_state_path)
        a_state = load(a_state_path)
        c_done = terminal(c_state, "C", scene)
        b_done = terminal(b_state, "B", scene)
        a_done = terminal(a_state, "A", scene)
        phase = "COMPLETE" if a_done else "A" if b_done else "B" if c_done else "C"
        document = {
            "schema_version": "mysql_xp_cba_cpu_a_slots_independent_pipeline_v1",
            "status": "COMPLETE" if a_done else "MONITORING",
            "updated_at": now(), "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "scene": scene, "current_phase": phase,
            "four_scene_barrier": False, "sibling_scene_wait": False,
            "source_authority": str(args.source_authority),
            "c": {"state": str(args.c_state), **compact(c_state, scene), "complete": c_done},
            "b": {"run_id": args.b_run_id, "state": str(b_state_path),
                  **compact(b_state, scene), "complete": b_done},
            "a": {"run_id": args.a_run_id, "state": str(a_state_path),
                  **compact(a_state, scene), "complete": a_done},
            "launch_attempts": attempts,
        }
        atomic(state_path, document)
        if a_done:
            document["completed_at"] = now()
            atomic(state_path, document)
            return 0
        current = time.monotonic()
        if c_done and not b_state and current >= next_attempt["B"]:
            result = invoke([
                str(PYTHON), str(B_LAUNCHER), "--scene", scene,
                "--c-state", str(args.c_state),
                "--source-authority", str(args.source_authority),
                "--run-id", args.b_run_id, "--pipeline-root", str(pipeline / "b_launch"),
                "--authority-root", str(b_authority_root), "--unit", args.b_unit,
            ])
            attempts["B"].append(result)
            next_attempt["B"] = time.monotonic() + 60.0
        if b_done and not a_state and current >= next_attempt["A"]:
            result = invoke([
                str(PYTHON), str(A_LAUNCHER), "--scene", scene,
                "--b-state", str(b_state_path),
                "--source-authority", str(args.source_authority),
                "--run-id", args.a_run_id, "--pipeline-root", str(pipeline / "a_launch"),
                "--authority-root", str(a_authority_root), "--unit", args.a_unit,
            ])
            attempts["A"].append(result)
            next_attempt["A"] = time.monotonic() + 60.0
        time.sleep(args.poll_seconds)
    document = load(state_path)
    document.update({"status": "WAIT_TIMEOUT", "timed_out_at": now()})
    atomic(state_path, document)
    return 4


if __name__ == "__main__":
    raise SystemExit(main())

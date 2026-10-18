#!/usr/bin/env python3
"""Monitor current independent XP C -> B -> A Top4 pipelines."""

from __future__ import annotations

import copy
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import monitor_mysql_four_scene_abc_pipeline_v1 as base


STATE = (
    base.HISTORY / "v181_to_abc_top4_pipeline_20260826/pipeline_state.json"
)
B_LAUNCHER = ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_scene_v2.py"
A_LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_a_xp_top4_scene_v1.py"
XP = {
    "hybench_xp_cpu": {
        "suffix": "xp_cpu",
        "c": base.HISTORY / "v59_to_c_xp_top4_20260825_attempt033_cpu_zero_safe_xp_cpu/run_state.json",
        "b": base.HISTORY / "v179_to_b_top4_fresh_20260826_attempt009_xp_cpu/run_state.json",
        "b_launch": base.HISTORY / "v179_to_b_top4_pipeline_20260826/xp_cpu.launch.json",
        "a": base.HISTORY / "v180_to_a_top4_fresh_20260826_attempt001_xp_cpu/algorithm_a_state.json",
        "a_launch": base.HISTORY / "v180_to_a_top4_pipeline_20260826/xp_cpu.launch.json",
    },
    "hybench_xp_io": {
        "suffix": "xp_io",
        "c": base.HISTORY / "v178_to_c_top4_io_xp4_20260826_attempt011_xp_io/run_state.json",
        "b": base.HISTORY / "v179_to_b_top4_fresh_20260826_attempt009_xp_io/run_state.json",
        "b_launch": base.HISTORY / "v179_to_b_top4_pipeline_20260826/xp_io.launch.json",
        "a": base.HISTORY / "v180_to_a_top4_fresh_20260826_attempt001_xp_io/algorithm_a_state.json",
        "a_launch": base.HISTORY / "v180_to_a_top4_pipeline_20260826/xp_io.launch.json",
    },
}


def invoke(launcher: Path, scene: str) -> dict[str, Any]:
    completed = subprocess.run(
        [str(base.PYTHON), str(launcher), "--scene", scene], cwd=ROOT,
        capture_output=True, text=True, check=False, timeout=900,
    )
    return {
        "launcher": str(launcher), "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
        "completed_at": base.now(),
    }


def failed(state: dict[str, Any]) -> bool:
    return state.get("status") in {
        "FAILED", "FAILED_CLOSED", "TECHNICAL_FAILURE_STOPPED_CLEAN",
    }


def main() -> int:
    attempted: set[tuple[str, str]] = set()
    launch_failures: dict[str, dict[str, Any]] = {}
    while True:
        rows: dict[str, Any] = {}
        for scene, paths in XP.items():
            c_state = base.load(Path(paths["c"]))
            b_state = base.load(Path(paths["b"]))
            a_state = base.load(Path(paths["a"]))
            c_done = base.terminal(c_state, "C", scene)
            b_done = base.terminal(b_state, "B", scene)
            a_done = base.terminal(a_state, "A", scene)
            if (
                c_done and not b_state and (scene, "B") not in attempted
                and scene not in launch_failures
            ):
                attempted.add((scene, "B"))
                result = invoke(B_LAUNCHER, scene)
                if result["returncode"] != 0:
                    launch_failures[scene] = {"scene": scene, "phase": "B", **result}
                b_state = base.load(Path(paths["b"]))
                b_done = base.terminal(b_state, "B", scene)
            if (
                b_done and not a_state and (scene, "A") not in attempted
                and scene not in launch_failures
            ):
                attempted.add((scene, "A"))
                result = invoke(A_LAUNCHER, scene)
                if result["returncode"] != 0:
                    launch_failures[scene] = {"scene": scene, "phase": "A", **result}
                a_state = base.load(Path(paths["a"]))
                a_done = base.terminal(a_state, "A", scene)
            runtime_failure = None
            if failed(a_state):
                runtime_failure = {"scene": scene, "phase": "A", "state": base.compact(a_state, scene)}
            elif failed(b_state):
                runtime_failure = {"scene": scene, "phase": "B", "state": base.compact(b_state, scene)}
            elif failed(c_state):
                runtime_failure = {"scene": scene, "phase": "C", "state": base.compact(c_state, scene)}
            if runtime_failure is not None:
                launch_failures.setdefault(scene, runtime_failure)
            current = a_state or b_state or c_state
            rows[scene] = {
                "current": base.compact(current, scene),
                "c": {**base.compact(c_state, scene), "complete": c_done,
                      "state_path": str(paths["c"])},
                "b": {**base.compact(b_state, scene), "complete": b_done,
                      "state_path": str(paths["b"]),
                      "launch": base.load(Path(paths["b_launch"])) or None},
                "a": {**base.compact(a_state, scene), "complete": a_done,
                      "state_path": str(paths["a"]),
                      "launch": base.load(Path(paths["a_launch"])) or None},
                "next_phase": (
                    "COMPLETE" if a_done else "A" if b_done else "B" if c_done else "C"
                ),
                "failure": copy.deepcopy(launch_failures.get(scene)),
            }
        job = base.load(base.JOB_A)
        tpch = base.load(base.TPCH_A)
        memory = {
            "job_memory": {**base.compact(job, "job_memory"),
                           "complete": base.terminal(job, "A", "job_memory")},
            "tpch_sf4_memory": {**base.compact(tpch, "tpch_sf4_memory"),
                                "complete": base.terminal(tpch, "A", "tpch_sf4_memory")},
        }
        all_done = all(row["a"]["complete"] for row in rows.values())
        memory_done = all(row["complete"] for row in memory.values())
        document = {
            "schema_version": "mysql_four_scene_abc_top4_pipeline_monitor_v3",
            "status": (
                "COMPLETE" if all_done and memory_done else
                "MONITORING_WITH_FAILURE" if launch_failures else "MONITORING"
            ),
            "updated_at": base.now(), "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "memory_algorithm_a": memory,
            "xp_independent_pipelines": rows,
            "four_scene_barrier": False, "sibling_scene_wait": False,
            "lease_mode": "none", "failures": copy.deepcopy(launch_failures),
            "b_launcher": str(B_LAUNCHER), "a_launcher": str(A_LAUNCHER),
        }
        if document["status"] == "COMPLETE":
            document["completed_at"] = base.now()
        base.atomic(STATE, document)
        if document["status"] == "COMPLETE":
            return 0
        time.sleep(15.0)


if __name__ == "__main__":
    raise SystemExit(main())

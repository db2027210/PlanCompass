#!/usr/bin/env python3
"""Continuously advance the current four-scene Top4 ABC pipeline."""

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


HISTORY = base.HISTORY
STATE = HISTORY / "v260_to_abc_top4_pipeline_20260826/pipeline_state.json"
B_LAUNCHER = ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_scene_v14.py"
A_LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_a_xp_top4_scene_v6.py"
RETRY_SECONDS = 60.0
XP = {
    "hybench_xp_cpu": {
        "c": HISTORY / "v59_to_c_xp_top4_20260825_attempt033_cpu_zero_safe_xp_cpu/run_state.json",
        "b": HISTORY / "v255_to_b_top4_fresh_20260826_attempt028_xp_cpu/run_state.json",
        "b_launch": HISTORY / "v255_to_b_top4_pipeline_20260826/xp_cpu.launch.json",
        "a": HISTORY / "v258_to_a_top4_fresh_20260826_attempt006_xp_cpu/algorithm_a_state.json",
        "a_launch": HISTORY / "v258_to_a_top4_pipeline_20260826/xp_cpu.launch.json",
    },
    "hybench_xp_io": {
        "c": HISTORY / "v256_to_c_top4_io_outer6_partial_20260826_attempt023_xp_io/run_state.json",
        "b": HISTORY / "v257_to_b_top4_fresh_20260826_attempt020_xp_io/run_state.json",
        "b_launch": HISTORY / "v257_to_b_top4_pipeline_20260826/xp_io.launch.json",
        "a": HISTORY / "v258_to_a_top4_fresh_20260826_attempt006_xp_io/algorithm_a_state.json",
        "a_launch": HISTORY / "v258_to_a_top4_pipeline_20260826/xp_io.launch.json",
    },
}
MEMORY = {
    "job_memory": {
        "c": HISTORY / "v39_to_c_per_scene_20260820_attempt073_job_cached_preemption_resume_job/run_state.json",
        "b": HISTORY / "v36_formal_async_outer_20260819_attempt044_retry003_job/run_state.json",
        "a": base.JOB_A,
    },
    "tpch_sf4_memory": {
        "c": HISTORY / "v39_to_c_per_scene_20260820_attempt080_tpch_cached_preemption_resume_tpch/run_state.json",
        "b": HISTORY / "v36_formal_async_outer_20260819_attempt044_retry003_tpch/run_state.json",
        "a": base.TPCH_A,
    },
}


def failed(state: dict[str, Any]) -> bool:
    return state.get("status") in {
        "FAILED", "FAILED_CLOSED", "TECHNICAL_FAILURE_STOPPED_CLEAN",
    }


def invoke(launcher: Path, scene: str) -> dict[str, Any]:
    completed = subprocess.run(
        [str(base.PYTHON), str(launcher), "--scene", scene],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=900,
    )
    return {
        "launcher": str(launcher), "scene": scene,
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
        "completed_at": base.now(),
    }


def main() -> int:
    next_retry: dict[tuple[str, str], float] = {}
    launch_attempts: list[dict[str, Any]] = []
    while True:
        rows: dict[str, Any] = {}
        failures: dict[str, Any] = {}
        for scene, paths in XP.items():
            c_state = base.load(paths["c"])
            b_state = base.load(paths["b"])
            a_state = base.load(paths["a"])
            c_done = base.terminal(c_state, "C", scene)
            b_done = base.terminal(b_state, "B", scene)
            a_done = base.terminal(a_state, "A", scene)
            phase = "B" if c_done and not b_state else "A" if b_done and not a_state else None
            if phase is not None:
                key = (scene, phase)
                now_mono = time.monotonic()
                if now_mono >= next_retry.get(key, 0.0):
                    launcher = B_LAUNCHER if phase == "B" else A_LAUNCHER
                    result = invoke(launcher, scene)
                    launch_attempts.append(result)
                    launch_attempts = launch_attempts[-40:]
                    next_retry[key] = now_mono + RETRY_SECONDS
                    b_state = base.load(paths["b"])
                    a_state = base.load(paths["a"])
                    b_done = base.terminal(b_state, "B", scene)
                    a_done = base.terminal(a_state, "A", scene)
                    if result["returncode"] != 0:
                        failures[scene] = {"phase": phase, **result}
            runtime_failure = None
            for name, state in (("A", a_state), ("B", b_state), ("C", c_state)):
                if failed(state):
                    runtime_failure = {
                        "phase": name, "state": base.compact(state, scene),
                        "error": state.get("error"),
                    }
                    break
            if runtime_failure is not None:
                failures[scene] = runtime_failure
            current = a_state or b_state or c_state
            rows[scene] = {
                "current": base.compact(current, scene),
                "c": {**base.compact(c_state, scene), "complete": c_done,
                      "state_path": str(paths["c"])},
                "b": {**base.compact(b_state, scene), "complete": b_done,
                      "state_path": str(paths["b"]),
                      "launch": base.load(paths["b_launch"]) or None},
                "a": {**base.compact(a_state, scene), "complete": a_done,
                      "state_path": str(paths["a"]),
                      "launch": base.load(paths["a_launch"]) or None},
                "next_phase": "COMPLETE" if a_done else "A" if b_done else "B" if c_done else "C",
            }
        memory: dict[str, Any] = {}
        for scene, paths in MEMORY.items():
            memory[scene] = {}
            for algorithm in ("C", "B", "A"):
                state = base.load(paths[algorithm.lower()])
                memory[scene][algorithm.lower()] = {
                    **base.compact(state, scene),
                    "complete": base.terminal(state, algorithm, scene),
                    "state_path": str(paths[algorithm.lower()]),
                }
        all_xp_done = all(row["a"]["complete"] for row in rows.values())
        all_memory_done = all(
            phase["complete"] for scene in memory.values() for phase in scene.values()
        )
        document = {
            "schema_version": "mysql_four_scene_abc_top4_pipeline_monitor_v8",
            "status": "COMPLETE" if all_xp_done and all_memory_done else (
                "MONITORING_WITH_FAILURE" if failures else "MONITORING"
            ),
            "updated_at": base.now(), "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "memory_abc": memory, "xp_independent_pipelines": rows,
            "failures": copy.deepcopy(failures),
            "launch_attempts": copy.deepcopy(launch_attempts),
            "b_launcher": str(B_LAUNCHER), "a_launcher": str(A_LAUNCHER),
            "four_scene_barrier": False, "sibling_scene_wait": False,
            "lease_mode": "none", "physical_workers_per_xp_scene": 4,
            "maximum_concurrent_xp_mysql_workers": 8,
            "candidate_sampling": False,
            "parallel_expansion_policy": "use_all_frozen_workers_at_each_physical_wave_boundary",
        }
        if document["status"] == "COMPLETE":
            document["completed_at"] = base.now()
        base.atomic(STATE, document)
        if document["status"] == "COMPLETE":
            return 0
        time.sleep(15.0)


if __name__ == "__main__":
    raise SystemExit(main())

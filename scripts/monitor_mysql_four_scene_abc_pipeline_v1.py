#!/usr/bin/env python3
"""Monitor JOB/A and independently advance each XP scene through C -> B -> A."""

from __future__ import annotations

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
PIPELINE = HISTORY / "v161_to_abc_top4_pipeline_20260826"
STATE = PIPELINE / "pipeline_state.json"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
B_LAUNCHER = ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_scene_v1.py"
A_LAUNCHER = ROOT / "scripts/launch_mysql_xp_top4_algorithm_a_scene_v1.py"
JOB_A = HISTORY / "v153_to_a_job4_round11_20260825_attempt001_job/algorithm_a_state.json"
TPCH_A = HISTORY / "v100_to_a_tpch_external_diagnostic_resume_20260823_attempt001_tpch/algorithm_a_state.json"
XP = {
    "hybench_xp_cpu": {
        "suffix": "xp_cpu",
        "c": HISTORY / "v59_to_c_xp_top4_20260825_attempt033_cpu_zero_safe_xp_cpu/run_state.json",
    },
    "hybench_xp_io": {
        "suffix": "xp_io",
        "c": HISTORY / "v59_to_c_xp_top4_20260825_attempt034_io_zero_safe_xp_io/run_state.json",
    },
}


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


def terminal(state: Mapping[str, Any], algorithm: str, scene: str) -> bool:
    converged = state.get("converged")
    if isinstance(converged, Mapping):
        converged = converged.get(scene)
    accepted = state.get("accepted")
    if accepted is None:
        accepted = (state.get("accepted_rounds") or {}).get(scene)
    target = state.get("accepted_target")
    if target is None:
        target = 10
    active_scenes = state.get("active_scenes")
    scene_scope_matches = (
        active_scenes is None
        or active_scenes == [scene]
        or state.get("async_scene") == scene
    )
    # Older independent C controllers computed their process exit label from
    # the dormant four-scene convergence map.  A single scene can therefore
    # be fully converged while the aggregate label says NON_CONVERGED.  The
    # formal sequence gate is the completed scene-local state, not dormant
    # sibling entries.
    return bool(
        state.get("algorithm") == algorithm
        and state.get("status") in {"COMPLETE", "NON_CONVERGED"}
        and state.get("stage") in {"FINISHED", "COMPLETE"}
        and not state.get("error")
        and scene_scope_matches
        and converged is True and int(accepted or 0) >= int(target)
    )


def compact(state: Mapping[str, Any], scene: str) -> dict[str, Any]:
    accepted = state.get("accepted")
    if accepted is None:
        accepted = (state.get("accepted_rounds") or {}).get(scene)
    rejected = state.get("rejected_rounds")
    if rejected is None:
        rejected = (state.get("rejected_outer_attempts") or {}).get(scene)
    return {
        "algorithm": state.get("algorithm"), "status": state.get("status"),
        "stage": state.get("stage"), "accepted": accepted, "rejected": rejected,
        "round_or_outer": state.get("round_index", state.get("outer_attempt")),
    }


def invoke(launcher: Path, scene: str) -> dict[str, Any]:
    completed = subprocess.run(
        [str(PYTHON), str(launcher), "--scene", scene], cwd=ROOT,
        capture_output=True, text=True, check=False, timeout=600,
    )
    return {
        "launcher": str(launcher), "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-4000:], "stderr_tail": completed.stderr[-4000:],
        "completed_at": now(),
    }


def main() -> int:
    launch_attempted: set[tuple[str, str]] = set()
    while True:
        rows: dict[str, Any] = {}
        failure: dict[str, Any] | None = None
        for scene, config in XP.items():
            suffix = str(config["suffix"])
            c_state = load(Path(config["c"]))
            b_launch_path = (
                HISTORY / "v161_to_b_top4_pipeline_20260826" / f"{suffix}.launch.json"
            )
            b_launch = load(b_launch_path)
            b_state = load(Path(str(b_launch.get("state") or ""))) if b_launch else {}
            a_launch_path = (
                HISTORY / "v162_to_a_top4_pipeline_20260826" / f"{suffix}.launch.json"
            )
            a_launch = load(a_launch_path)
            a_state = load(Path(str(a_launch.get("state") or ""))) if a_launch else {}
            c_done = terminal(c_state, "C", scene)
            b_done = terminal(b_state, "B", scene)
            a_done = terminal(a_state, "A", scene)
            if c_done and not b_launch and (scene, "B") not in launch_attempted:
                launch_attempted.add((scene, "B"))
                result = invoke(B_LAUNCHER, scene)
                if result["returncode"] != 0:
                    failure = {"scene": scene, "phase": "B", **result}
                else:
                    b_launch = load(b_launch_path)
                    b_state = load(Path(str(b_launch.get("state") or "")))
            if b_done and not a_launch and (scene, "A") not in launch_attempted:
                if A_LAUNCHER.is_file() and not A_LAUNCHER.is_symlink():
                    launch_attempted.add((scene, "A"))
                    result = invoke(A_LAUNCHER, scene)
                    if result["returncode"] != 0:
                        failure = {"scene": scene, "phase": "A", **result}
                    else:
                        a_launch = load(a_launch_path)
                        a_state = load(Path(str(a_launch.get("state") or "")))
            current = a_state or b_state or c_state
            rows[scene] = {
                "current": compact(current, scene),
                "c": {**compact(c_state, scene), "complete": c_done},
                "b": {**compact(b_state, scene), "complete": b_done,
                      "launch": b_launch or None},
                "a": {**compact(a_state, scene), "complete": a_done,
                      "launch": a_launch or None},
                "next_phase": (
                    "COMPLETE" if a_done else "A" if b_done else "B" if c_done else "C"
                ),
            }
        job = load(JOB_A)
        tpch = load(TPCH_A)
        document = {
            "schema_version": "mysql_four_scene_abc_top4_pipeline_monitor_v1",
            "status": "FAILED_CLOSED" if failure else "MONITORING",
            "updated_at": now(), "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "memory_algorithm_a": {
                "job_memory": {**compact(job, "job_memory"),
                               "complete": terminal(job, "A", "job_memory")},
                "tpch_sf4_memory": {**compact(tpch, "tpch_sf4_memory"),
                                    "complete": terminal(tpch, "A", "tpch_sf4_memory")},
            },
            "xp_independent_pipelines": rows,
            "four_scene_barrier": False, "sibling_scene_wait": False,
            "failure": failure,
        }
        all_done = all(row["a"]["complete"] for row in rows.values())
        memory_done = all(row["complete"] for row in document["memory_algorithm_a"].values())
        if all_done and memory_done:
            document["status"] = "COMPLETE"
            document["completed_at"] = now()
        atomic(STATE, document)
        if failure:
            return 1
        if document["status"] == "COMPLETE":
            return 0
        time.sleep(15.0)


if __name__ == "__main__":
    raise SystemExit(main())

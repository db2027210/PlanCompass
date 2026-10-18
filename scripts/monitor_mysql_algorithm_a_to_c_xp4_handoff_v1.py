#!/usr/bin/env python3
"""Monitor Algorithm A completion and trigger the audited C XP four-slot handoff."""

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
JOB = HISTORY / "v153_to_a_job4_round11_20260825_attempt001_job/algorithm_a_state.json"
TPCH = HISTORY / "v100_to_a_tpch_external_diagnostic_resume_20260823_attempt001_tpch/algorithm_a_state.json"
CPU = HISTORY / "v59_to_c_xp_top4_20260825_attempt033_cpu_zero_safe_xp_cpu/run_state.json"
IO = HISTORY / "v59_to_c_xp_top4_20260825_attempt034_io_zero_safe_xp_io/run_state.json"
HANDOFF_ROOT = HISTORY / "v160_to_c_xp4_handoff_20260825"
MANIFEST = HANDOFF_ROOT / "transition_manifest.json"
MONITOR = HANDOFF_ROOT / "monitor_state.json"
PREPARE = ROOT / "scripts/prepare_mysql_algorithm_c_xp4_handoff_v1.py"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
RESULTS = {
    "hybench_xp_cpu": HANDOFF_ROOT / "xp_cpu/handoff_result.json",
    "hybench_xp_io": HANDOFF_ROOT / "xp_io/handoff_result.json",
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


def a_complete(state: Mapping[str, Any], scene: str) -> bool:
    return bool(
        state.get("algorithm") == "A" and state.get("async_scene") == scene
        and state.get("status") == "COMPLETE" and state.get("stage") == "COMPLETE"
        and state.get("converged") is True and state.get("round_checkpoint") is None
        and float(state.get("normalized_width") or 1.0)
        <= float(state.get("normalized_width_threshold") or 0.01)
    )


def compact(state: Mapping[str, Any], scene: str) -> dict[str, Any]:
    accepted = state.get("accepted")
    if accepted is None:
        accepted = (state.get("accepted_rounds") or {}).get(scene)
    rejected = state.get("rejected_rounds")
    if rejected is None:
        rejected = (state.get("rejected_outer_attempts") or {}).get(scene)
    converged = state.get("converged")
    if isinstance(converged, Mapping):
        converged = converged.get(scene)
    return {
        "status": state.get("status"), "stage": state.get("stage"),
        "accepted": accepted, "rejected": rejected, "converged": converged,
        "round_or_outer": state.get("round_index", state.get("outer_attempt")),
        "physical_replay_parallelism": state.get("physical_replay_parallelism"),
    }


def main() -> int:
    prepare_attempted = MANIFEST.is_file()
    while True:
        job = load(JOB)
        tpch = load(TPCH)
        manifest = load(MANIFEST)
        xp_rows: dict[str, Any] = {}
        for scene, original in (
            ("hybench_xp_cpu", CPU), ("hybench_xp_io", IO),
        ):
            result = load(RESULTS[scene])
            state_path = Path(str(result.get("state") or original))
            state = load(state_path)
            xp_rows[scene] = {
                **compact(state, scene),
                "state_path": str(state_path),
                "handoff": result or None,
            }
        job_done = a_complete(job, "job_memory")
        tpch_done = a_complete(tpch, "tpch_sf4_memory")
        document = {
            "schema_version": "mysql_algorithm_a_to_c_xp4_monitor_v1",
            "status": "MONITORING", "updated_at": now(),
            "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
            "algorithm_a": {
                "job_memory": {**compact(job, "job_memory"), "complete": job_done},
                "tpch_sf4_memory": {**compact(tpch, "tpch_sf4_memory"), "complete": tpch_done},
            },
            "algorithm_c_xp": xp_rows,
            "memory_scenes_complete": job_done and tpch_done,
            "handoff_manifest": manifest or None,
            "handoff_policy": {
                "xp_scene_round_boundaries_independent": True,
                "candidate_batches_after": [[1, 2, 3, 4], [5]],
                "lease_mode": "none", "four_scene_barrier": False,
                "sibling_scene_wait": False, "recalibration_performed": False,
                "completed_candidate_replay_due_to_transition": 0,
            },
        }
        atomic(MONITOR, document)
        if job_done and tpch_done and not manifest and not prepare_attempted:
            prepare_attempted = True
            completed = subprocess.run(
                [str(PYTHON), str(PREPARE)], cwd=ROOT,
                capture_output=True, text=True, check=False, timeout=900,
            )
            document["last_prepare"] = {
                "returncode": completed.returncode,
                "stdout_tail": completed.stdout[-4000:],
                "stderr_tail": completed.stderr[-4000:],
                "completed_at": now(),
            }
            if completed.returncode != 0:
                document["status"] = "FAILED_CLOSED"
                atomic(MONITOR, document)
                return completed.returncode
            atomic(MONITOR, document)
        successor_terminal = all(
            row.get("status") in {"COMPLETE", "NONCONVERGENCE_CLEAN"}
            for row in xp_rows.values()
        )
        if job_done and tpch_done and manifest.get("status") == "LAUNCHED" and successor_terminal:
            document["status"] = "COMPLETE"
            document["completed_at"] = now()
            atomic(MONITOR, document)
            return 0
        time.sleep(15.0)


if __name__ == "__main__":
    raise SystemExit(main())

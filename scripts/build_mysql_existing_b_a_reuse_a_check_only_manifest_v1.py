#!/usr/bin/env python3
"""Seal existing terminal B/A reuse and the independent-A-check boundary."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
OUTPUT = HISTORY / "v1028_existing_b_a_reuse_no_rerun_a_check_only_20260828"
B_STATES = {
    "job_memory": HISTORY / "v523_to_b_job_mixed_four_worker_boundary_resume_v73_20260827_job/run_state.json",
    "tpch_sf4_memory": HISTORY / "v517_to_b_tpch_mixed_four_worker_boundary_resume_v73_20260827_tpch/run_state.json",
    "hybench_xp_cpu": HISTORY / "v551_to_b_cpu_mixed_eight_worker_v3_20260827_xp_cpu/run_state.json",
    "hybench_xp_io": HISTORY / "v809_to_b_io_outer15_slow_docker_resume_20260827_xp_io/run_state.json",
}
A_STATES = {
    "job_memory": HISTORY / "v153_to_a_job4_round11_20260825_attempt001_job/algorithm_a_state.json",
    "tpch_sf4_memory": HISTORY / "v100_to_a_tpch_external_diagnostic_resume_20260823_attempt001_tpch/algorithm_a_state.json",
    "hybench_xp_cpu": HISTORY / "v843_to_a_cpu_round5_control2_resume_20260827_xp_cpu/algorithm_a_state.json",
    "hybench_xp_io": HISTORY / "v859_to_a_io_round2_wave2_physical_adapter_fixed_resume_20260828_xp_io/algorithm_a_state.json",
}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bound(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": sha(path)}


def atomic(path: Path, value: Mapping[str, Any]) -> None:
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
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh reuse output required: {OUTPUT}")
    b_rows: dict[str, Any] = {}
    for scene, path in B_STATES.items():
        state = load(path)
        active = list((state.get("active_by_scene") or {}).get(scene) or [])
        top4 = state.get("xp_top4_parameter_release") or {}
        if (
            state.get("algorithm") != "B"
            or state.get("async_scene") != scene
            or state.get("status") != "COMPLETE"
            or state.get("stage") != "FINISHED"
            or state.get("formal_scope") != "mysql_only"
            or state.get("cross_engine_comparison_prohibited") is not True
            or state.get("formal_evidence_eligible") is not True
            or state.get("error") not in (None, "")
            or (state.get("converged") or {}).get(scene) is not True
            or len(active) != 4 or len(set(active)) != 4
            or list(top4.get("active_parameters") or []) != active
            or int(top4.get("candidate_subset_count") or 0) != 15
            or top4.get("predecessor_seven_parameter_evidence_reused") is not False
        ):
            raise RuntimeError(f"{scene} reusable B terminal state differs")
        b_rows[scene] = {
            **bound(path), "status": "COMPLETE", "active_parameters": active,
            "accepted_rounds": int((state.get("accepted_rounds") or {}).get(scene) or 0),
            "outer_attempt": int(state.get("outer_attempt") or 0),
            "physical_replay_parallelism": int(state.get("physical_replay_parallelism") or 0),
            "formal_evidence_eligible": True,
        }

    a_rows: dict[str, Any] = {}
    for scene, path in A_STATES.items():
        state = load(path)
        active = list(state.get("active_parameters") or [])
        common = bool(
            state.get("algorithm") == "A"
            and state.get("async_scene") == scene
            and state.get("formal_scope") == "mysql_only"
            and state.get("cross_engine_comparison_prohibited") is True
            and len(active) == 4 and len(set(active)) == 4
            and int(state.get("candidate_count_per_round") or 0) == 16
        )
        if scene != "hybench_xp_io":
            valid = bool(
                common and state.get("status") == "COMPLETE"
                and state.get("stage") == "COMPLETE"
                and state.get("converged") is True
                and state.get("error") in (None, "")
            )
            disposition = "REUSE_TERMINAL_NO_RERUN"
        else:
            checkpoint = state.get("round_checkpoint") or {}
            valid = bool(
                common and state.get("status") == "FAILED_CLOSED"
                and state.get("stage") == "FAILED"
                and state.get("converged") is False
                and int(state.get("round_index") or 0) == 4
                and int(state.get("accepted") or 0) == 3
                and int(checkpoint.get("round_index") or 0) == 4
                and int(checkpoint.get("candidate_count") or 0) == 16
                and list(checkpoint.get("attempted_ranks") or []) == []
                and "selected.cnf" in str(state.get("error") or "")
            )
            disposition = "RESUME_FROM_VALID_ROUND4_AFTER_C_TERMINAL"
        if not valid:
            raise RuntimeError(f"{scene} reusable A state differs")
        a_rows[scene] = {
            **bound(path), "status": state.get("status"),
            "active_parameters": active,
            "round_index": int(state.get("round_index") or 0),
            "accepted": int(state.get("accepted") or 0),
            "converged": bool(state.get("converged")),
            "disposition": disposition,
        }

    OUTPUT.mkdir(parents=True, mode=0o700)
    document = {
        "schema_version": "mysql_existing_b_a_reuse_a_check_only_manifest_v1",
        "status": "PASS", "created_at": now(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "algorithm_b": {
            "all_four_scenes_complete": True,
            "rerun_required": False,
            "historical_terminal_result_reuse": True,
            "scenes": b_rows,
        },
        "algorithm_a": {
            "role": "independent_posthoc_check_only",
            "feedback_to_b_or_c": False,
            "candidate_qps_range_or_ranking_prior_used_by_b_or_c": False,
            "candidate_vectors_used_by_b_or_c": False,
            "qps_used_by_b_or_c": False,
            "parameter_ranges_used_by_b_or_c": False,
            "search_direction_used_by_b_or_c": False,
            "three_terminal_scenes_reused_without_rerun": True,
            "xp_io_full_restart_required": False,
            "xp_io_valid_round4_resume_required_after_c_terminal": True,
            "scenes": a_rows,
        },
        "current_c_policy": {
            "only_xp_cpu_and_xp_io_top4_c_running": True,
            "c_completion_does_not_invalidate_independent_b_or_a_results": True,
            "b_relaunch_after_c_prohibited": True,
            "a_terminal_scene_relaunch_after_c_prohibited": True,
            "a_io_may_resume_only_after_its_c_scene_is_terminal": True,
        },
        "builder": bound(Path(__file__).resolve()),
    }
    output = OUTPUT / "reuse_manifest.json"
    atomic(output, document)
    print(json.dumps({"status": "PASS", **bound(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

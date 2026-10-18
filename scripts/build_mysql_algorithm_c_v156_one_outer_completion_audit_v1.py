#!/usr/bin/env python3
"""Audit one complete v156 outer round for all four formal MySQL scenes."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
CAMPAIGN = HISTORY / "c_v156_four_scene_candidate_search_20260923"
RUNS = {
    "job_memory": HISTORY / "c_v156_four_scene_candidate_search_20260923_job",
    "tpch_sf4_memory": HISTORY / "c_v156_four_scene_candidate_search_20260923_tpch",
    "hybench_xp_cpu": HISTORY / "c_v156_four_scene_candidate_search_20260923_xp_cpu",
    "hybench_xp_io": HISTORY / "c_v156_four_scene_candidate_search_20260923_xp_io",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular JSON required: {path}")
    value = json.loads(path.read_text())
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n"
    )


def main() -> int:
    output = CAMPAIGN / "one_outer_completion_audit.json"
    if output.exists() or output.is_symlink():
        raise RuntimeError("fresh one-outer completion audit required")
    launch_path = CAMPAIGN / "launch_manifest.json"
    launch = load(launch_path)
    if (
        launch.get("schema_version") != "mysql_algorithm_c_v156_four_scene_launch_v1"
        or launch.get("all_four_scenes_started_together") is not True
        or launch.get("four_scene_round_barrier") is not False
        or launch.get("sibling_scene_wait") is not False
        or set(launch.get("candidate_scenes") or []) != set(RUNS)
        or launch.get("formal_scope") != "mysql_only"
        or launch.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("v156 four-scene launch authority differs")

    rows: dict[str, Any] = {}
    for scene, run in RUNS.items():
        state_path = run / "run_state.json"
        gate_path = run / "paper_baseline_resource_gate.json"
        decision_path = run / f"outer_001/{scene}.decision.json"
        cessation_path = run / "completed_outer_001_cessation.json"
        state = load(state_path)
        gate = load(gate_path)
        decision = load(decision_path)
        cessation = load(cessation_path)
        resources = (
            dict(gate.get("resources") or {})
            if scene == "hybench_xp_io"
            else dict((gate.get("scenes") or {}).get(scene) or {})
        )
        if (
            gate.get("status") != "PASS"
            or gate.get("candidate_search_authorized") is not True
            or float(gate.get("tolerance_percentage_points") or 0) != 1.0
            or set(resources) != {"cpu", "memory", "io"}
            or any(
                row.get("passed") is not True
                or abs(float(row.get("delta_percentage_points"))) > 1.0
                for row in resources.values()
            )
        ):
            raise RuntimeError(f"{scene} paper-baseline resource gate differs")
        batch_rows = []
        for batch_index, expected_count in ((1, 2), (2, 2), (3, 1)):
            batch_path = run / f"outer_001/top5_batch_{batch_index}/batch_result.json"
            batch = load(batch_path)
            measurements = dict(batch.get("measurements") or {})
            if (
                batch.get("status") != "PASS"
                or batch.get("scene") != scene
                or batch.get("failures") not in ([], None)
                or len(measurements) != expected_count
                or any(
                    row.get("hard_gate_passed") is not True
                    or row.get("coverage_complete") is not True
                    for row in measurements.values()
                )
            ):
                raise RuntimeError(f"{scene} outer 1 batch {batch_index} differs")
            batch_rows.append({
                "batch_index": batch_index,
                "path": str(batch_path), "sha256": sha(batch_path),
                "measurement_count": len(measurements),
                "status": "PASS", "coverage_complete": True,
            })
        accepted = decision.get("accepted") is True
        accepted_rounds = int(decision.get("accepted_rounds") or 0)
        rejected_rounds = int(decision.get("rejected_outer_attempts") or 0)
        if (
            int(decision.get("outer_attempt") or 0) != 1
            or decision.get("formal_evidence_eligible") is not True
            or accepted_rounds + rejected_rounds != 1
            or accepted != (accepted_rounds == 1)
            or int(state.get("outer_attempt") or 0) != 1
            or int((state.get("accepted_rounds") or {}).get(scene) or 0)
            != accepted_rounds
            or int((state.get("rejected_outer_attempts") or {}).get(scene) or 0)
            != rejected_rounds
            or cessation.get("status") != "PASS"
            or int(cessation.get("completed_outer_attempt") or 0) != 1
            or cessation.get("controller_process_group_terminated") is not True
            or cessation.get("outer_002_batch_1_result_present") is not False
            or any(int(value) != 0 for value in
                   (cessation.get("post_cleanup_active_benchmark_sessions") or {}).values())
            or cessation.get("completed_decision_sha256") != sha(decision_path)
        ):
            raise RuntimeError(f"{scene} completed outer decision differs")
        rows[scene] = {
            "status": "COMPLETE",
            "outer_attempt": 1,
            "accepted": accepted,
            "accepted_rounds": accepted_rounds,
            "rejected_outer_attempts": rejected_rounds,
            "paper_baseline_gate": {
                "path": str(gate_path), "sha256": sha(gate_path),
                "status": "PASS", "tolerance_percentage_points": 1.0,
                "resources": resources,
            },
            "batches": batch_rows,
            "decision": {"path": str(decision_path), "sha256": sha(decision_path)},
            "cessation": {"path": str(cessation_path), "sha256": sha(cessation_path)},
            "incomplete_outer_002_formal_evidence_eligible": False,
        }

    authority_result = Path(str(launch["authority_root"])) / "authority_result.json"
    fresh_r0 = Path(str(launch["fresh_r0"]))
    authority = load(authority_result)
    fresh = load(fresh_r0)
    if authority.get("status") != "PASS" or fresh.get("status") != "PASS":
        raise RuntimeError("v156 authority or fresh R0 differs")
    result = {
        "schema_version": "mysql_algorithm_c_v156_one_outer_completion_audit_v1",
        "status": "PASS",
        "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "resource_tolerance_percentage_points": 1.0,
        "all_four_scenes_started_together": True,
        "all_four_scenes_completed_outer_001": True,
        "completed_round_acceptance_summary": {
            scene: row["accepted"] for scene, row in rows.items()
        },
        "launch_manifest": {"path": str(launch_path), "sha256": sha(launch_path)},
        "authority_result": {
            "path": str(authority_result), "sha256": sha(authority_result),
        },
        "fresh_r0": {"path": str(fresh_r0), "sha256": sha(fresh_r0)},
        "scenes": rows,
        "incomplete_outer_002_artifacts_excluded": True,
        "postgresql_process_control_or_stop_performed": False,
    }
    atomic(output, result)
    print(json.dumps({"status": "PASS", "path": str(output), "sha256": sha(output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

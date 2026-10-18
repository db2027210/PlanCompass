#!/usr/bin/env python3
"""Seal a completed four-scene MySQL Algorithm C campaign.

The writer is intentionally fail closed.  It creates no receipt until all
four scene controllers report a formal, converged terminal state and their
last outer decisions prove the configured one-percent interval precision.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
SCENES = {
    "job": "job_memory",
    "tpch": "tpch_sf4_memory",
    "xp_cpu": "hybench_xp_cpu",
    "xp_io": "hybench_xp_io",
}
TERMINAL_WIDTH = 0.01


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def coverage_receipt(scene: str, measurement: dict[str, Any]) -> dict[str, Any]:
    require(measurement.get("coverage_complete") is True, f"{scene}: incomplete coverage")
    require(measurement.get("hard_gate_passed") is True, f"{scene}: hard gate failed")
    workload = measurement.get("workload_result")
    require(isinstance(workload, dict), f"{scene}: workload result missing")
    if scene == "job_memory":
        coverage = workload.get("query_coverage") or {}
        require(workload.get("query_count") == 113, "JOB query count is not 113")
        require(coverage.get("coverage_complete") is True, "JOB coverage failed")
        require(len(coverage.get("completed_queries") or []) == 113, "JOB completed count differs")
        require(not coverage.get("missing_queries"), "JOB has missing SQL")
        return {"suite": "full_job", "completed": 113, "required": 113}
    if scene == "tpch_sf4_memory":
        coverage = workload.get("query_coverage") or {}
        require(coverage.get("coverage_complete") is True, "TPC-H coverage failed")
        require(len(coverage.get("completed_queries") or []) == 22, "TPC-H completed count differs")
        require(not coverage.get("missing_queries"), "TPC-H has missing SQL")
        return {"suite": "tpch_sf4_q1_q22", "completed": 22, "required": 22}
    coverage = workload.get("workload_coverage") or {}
    require(coverage.get("coverage_complete") is True, f"{scene}: native XP coverage failed")
    require(
        set(coverage.get("completed_iq") or []) == {f"IQ{i}" for i in range(1, 8)},
        f"{scene}: IQ coverage differs",
    )
    require(
        set(coverage.get("completed_at") or []) == {f"AT{i}" for i in range(1, 7)},
        f"{scene}: AT coverage differs",
    )
    return {
        "suite": "native_hybench_xp",
        "completed_iq": sorted(coverage["completed_iq"]),
        "completed_at": sorted(coverage["completed_at"]),
    }


def baseline_receipt(path: Path, scene: str) -> dict[str, Any]:
    document = load(path)
    require(document.get("status") == "PASS", f"{scene}: baseline gate failed")
    resources = document.get("resources") or (
        (document.get("scenes") or {}).get(scene) or {}
    )
    deltas = {
        name: float(row["delta_percentage_points"])
        for name, row in resources.items()
    }
    require(set(deltas) == {"cpu", "memory", "io"}, f"{scene}: baseline resources differ")
    require(all(abs(value) <= 1.0 for value in deltas.values()), f"{scene}: baseline delta exceeds 1 pp")
    return {
        "path": str(path),
        "sha256": sha(path),
        "status": "PASS",
        "delta_percentage_points": deltas,
        "tolerance_percentage_points": 1.0,
    }


def scene_receipt(prefix: str, suffix: str, scene: str) -> dict[str, Any]:
    run = HISTORY / f"{prefix}_{suffix}"
    state_path = run / "run_state.json"
    state = load(state_path)
    require(state.get("formal_scope") == "mysql_only", f"{scene}: formal scope differs")
    require(state.get("cross_engine_comparison_prohibited") is True, f"{scene}: cross-engine flag differs")
    require(state.get("formal_evidence_eligible") is True, f"{scene}: formal evidence is ineligible")
    require(state.get("status") == "COMPLETE", f"{scene}: state is not COMPLETE")
    require(state.get("stage") == "FINISHED", f"{scene}: terminal stage is not FINISHED")
    require(state.get("completion_reason") == "all_active_log_domain_widths_reached_1pct", f"{scene}: completion reason differs")
    require((state.get("converged") or {}).get(scene) is True, f"{scene}: convergence flag is false")
    require(state.get("post_search_stability_confirmation") is False, f"{scene}: unexpected post-search confirmation")
    require(not state.get("error"), f"{scene}: terminal error is present")

    outer_attempt = int(state.get("outer_attempt") or 0)
    require(outer_attempt >= 1, f"{scene}: no completed outer decision")
    decisions: list[dict[str, Any]] = []
    semantic_measurement_count = 0
    for index in range(1, outer_attempt + 1):
        decision_path = run / f"outer_{index:03d}" / f"{scene}.decision.json"
        decision = load(decision_path)
        require(decision.get("outer_attempt") == index, f"{scene}: outer {index} index differs")
        require(decision.get("formal_evidence_eligible") is True, f"{scene}: outer {index} is ineligible")
        evaluations = decision.get("evaluations") or []
        coverage = []
        for row in evaluations:
            if row.get("semantic_state_eligible", True) is not True:
                continue
            coverage.append(coverage_receipt(scene, row))
        semantic_measurement_count += len(coverage)
        convergence = decision.get("interval_convergence") or {}
        decisions.append({
            "outer_attempt": index,
            "accepted": decision.get("accepted"),
            "semantic_measurement_count": len(coverage),
            "normalized_width": convergence.get("normalized_width"),
            "converged": convergence.get("converged"),
            "path": str(decision_path),
            "sha256": sha(decision_path),
        })

    final = decisions[-1]
    require(final["converged"] is True, f"{scene}: final decision is not converged")
    require(final["normalized_width"] is not None, f"{scene}: final width missing")
    require(float(final["normalized_width"]) <= TERMINAL_WIDTH, f"{scene}: final width exceeds 0.01")
    require(semantic_measurement_count > 0, f"{scene}: no complete semantic measurement")
    return {
        "state": {
            "path": str(state_path),
            "sha256": sha(state_path),
            "status": state["status"],
            "stage": state["stage"],
            "completion_reason": state["completion_reason"],
            "outer_attempt": outer_attempt,
            "accepted_rounds": (state.get("accepted_rounds") or {}).get(scene),
            "rejected_outer_attempts": (state.get("rejected_outer_attempts") or {}).get(scene),
        },
        "decision_count": len(decisions),
        "semantic_measurement_count": semantic_measurement_count,
        "final_normalized_width": float(final["normalized_width"]),
        "terminal_normalized_width": TERMINAL_WIDTH,
        "decisions": decisions,
    }


def main() -> int:
    if len(sys.argv) not in {2, 3}:
        raise RuntimeError("usage: writer RUN_PREFIX [OUTPUT_BASENAME]")
    prefix = sys.argv[1]
    output_name = sys.argv[2] if len(sys.argv) == 3 else "final_completion_audit.json"
    require(Path(output_name).name == output_name and output_name.endswith(".json"), "output must be a JSON basename")
    campaign = HISTORY / prefix
    output = campaign / output_name
    require(not output.exists() and not output.is_symlink(), f"fresh audit required: {output}")

    manifest_path = campaign / "launch_manifest.json"
    manifest = load(manifest_path)
    require(manifest.get("formal_scope") == "mysql_only", "launch scope differs")
    require(manifest.get("cross_engine_comparison_prohibited") is True, "launch comparison flag differs")
    require(manifest.get("all_four_scenes_started_together") is True, "four scenes did not start together")
    require(manifest.get("four_scene_round_barrier") is False, "four-scene barrier is enabled")
    require(manifest.get("sibling_scene_wait") is False, "sibling-scene wait is enabled")
    require(set(manifest.get("candidate_scenes") or []) == set(SCENES.values()), "scene set differs")

    baselines: dict[str, Any] = {}
    scenes: dict[str, Any] = {}
    for suffix, scene in SCENES.items():
        run = HISTORY / f"{prefix}_{suffix}"
        baselines[scene] = baseline_receipt(run / "paper_baseline_resource_gate.json", scene)
        scenes[scene] = scene_receipt(prefix, suffix, scene)

    monitor_path = campaign / "completion_monitor_summary.json"
    monitor = load(monitor_path)
    require(monitor.get("all_terminal") is True, "monitor has not observed all terminal scenes")
    require(monitor.get("all_complete") is True, "monitor has not observed all scenes complete")
    require(all(row.get("process_alive") is False for row in (monitor.get("scenes") or {}).values()), "controller still alive in terminal monitor snapshot")

    document = {
        "schema_version": "mysql_algorithm_c_final_completion_audit_v1",
        "created_at": datetime.now().astimezone().isoformat(),
        "status": "PASS_COMPLETE",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "run_prefix": prefix,
        "launch_manifest": {"path": str(manifest_path), "sha256": sha(manifest_path)},
        "paper_baseline_resource_gates": baselines,
        "scenes": scenes,
        "all_four_scenes_complete": True,
        "all_final_normalized_widths_at_most_0_01": True,
        "full_workload_coverage_verified_for_semantic_measurements": True,
        "monitor_terminal_snapshot": {"path": str(monitor_path), "sha256": sha(monitor_path)},
        "postgresql_process_control_or_stop_performed": False,
    }
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    checksum = output.with_suffix(output.suffix + ".sha256")
    checksum.write_text(f"{sha(output)}  {output.name}\n", encoding="utf-8")
    print(json.dumps({"path": str(output), "sha256": sha(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

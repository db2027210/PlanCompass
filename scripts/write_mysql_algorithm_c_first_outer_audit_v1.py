#!/usr/bin/env python3
"""Seal evidence that a fresh four-scene Algorithm C run completed an outer round."""

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
EXPECTED_WAVES = (2, 2, 1)


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
    require(set(coverage.get("completed_iq") or []) == {f"IQ{i}" for i in range(1, 8)}, f"{scene}: IQ coverage differs")
    require(set(coverage.get("completed_at") or []) == {f"AT{i}" for i in range(1, 7)}, f"{scene}: AT coverage differs")
    return {
        "suite": "native_hybench_xp",
        "completed_iq": sorted(coverage["completed_iq"]),
        "completed_at": sorted(coverage["completed_at"]),
    }


def main() -> int:
    if len(sys.argv) not in {2, 3}:
        raise RuntimeError("usage: writer RUN_PREFIX [OUTPUT_BASENAME]")
    prefix = sys.argv[1]
    campaign = HISTORY / prefix
    output_name = (
        sys.argv[2]
        if len(sys.argv) == 3
        else "first_complete_outer_round_audit.json"
    )
    require(
        Path(output_name).name == output_name and output_name.endswith(".json"),
        "output must be a JSON basename",
    )
    output = campaign / output_name
    require(not output.exists() and not output.is_symlink(), f"fresh audit required: {output}")

    baseline_receipts: dict[str, Any] = {}
    completed_receipts: dict[str, Any] = {}
    for suffix, scene in SCENES.items():
        run = HISTORY / f"{prefix}_{suffix}"
        baseline_path = run / "paper_baseline_resource_gate.json"
        baseline = load(baseline_path)
        require(baseline.get("status") == "PASS", f"{scene}: baseline gate failed")
        resources = baseline.get("resources") or (
            (baseline.get("scenes") or {}).get(scene) or {}
        )
        deltas = {
            name: float(row["delta_percentage_points"])
            for name, row in resources.items()
        }
        require(set(deltas) == {"cpu", "memory", "io"}, f"{scene}: baseline resources differ")
        require(all(abs(value) <= 1.0 for value in deltas.values()), f"{scene}: baseline delta exceeds 1 pp")
        baseline_receipts[scene] = {
            "path": str(baseline_path),
            "sha256": sha(baseline_path),
            "delta_percentage_points": deltas,
            "tolerance_percentage_points": 1.0,
        }

        state_path = run / "run_state.json"
        state = load(state_path)
        scene_outer = int(state.get("outer_attempt") or 0)
        if scene_outer < 1:
            continue
        decision_path = run / "outer_001" / f"{scene}.decision.json"
        decision = load(decision_path)
        require(decision.get("outer_attempt") == 1, f"{scene}: first decision index differs")
        require(decision.get("formal_evidence_eligible") is True, f"{scene}: decision is ineligible")
        evaluations = decision.get("evaluations") or []
        require(
            0 < len(evaluations) <= 5,
            f"{scene}: invalid semantic evaluation count",
        )
        batch_receipts = []
        coverage_rows = []
        for batch_index, expected_count in enumerate(EXPECTED_WAVES, 1):
            batch_path = run / "outer_001" / f"top5_batch_{batch_index}" / "batch_result.json"
            batch = load(batch_path)
            require(batch.get("status") == "PASS", f"{scene}: batch {batch_index} failed")
            require(not batch.get("failures"), f"{scene}: batch {batch_index} has failures")
            raw_measurements = batch.get("measurements") or {}
            measurements = (
                list(raw_measurements.values())
                if isinstance(raw_measurements, dict)
                else list(raw_measurements)
            )
            require(len(measurements) == expected_count, f"{scene}: batch {batch_index} wave size differs")
            coverage_rows.extend(coverage_receipt(scene, row) for row in measurements)
            batch_receipts.append({
                "batch_index": batch_index,
                "measurement_count": len(measurements),
                "path": str(batch_path),
                "sha256": sha(batch_path),
                "status": "PASS",
            })
        continued_stage = str(state.get("stage") or "")
        require(
            state.get("status") in {"RUNNING", "COMPLETE"}
            and not continued_stage.startswith("OUTER_001"),
            f"{scene}: controller did not continue beyond outer 1",
        )
        convergence = decision.get("interval_convergence") or {}
        completed_receipts[scene] = {
            "decision": {
                "accepted": decision.get("accepted"),
                "accepted_rounds": decision.get("accepted_rounds"),
                "formal_evidence_eligible": True,
                "normalized_width": convergence.get("normalized_width"),
                "terminal_normalized_width": convergence.get("terminal_normalized_width"),
                "converged": convergence.get("converged"),
                "semantic_evaluation_count": len(evaluations),
                "physical_top5_measurement_count": sum(
                    row["measurement_count"] for row in batch_receipts
                ),
                "no_move_accepted_candidates_excluded": decision.get(
                    "no_move_accepted_candidates_excluded"
                ),
                "mysql_effective_parameter_movement_required": decision.get(
                    "mysql_effective_parameter_movement_required"
                ),
                "path": str(decision_path),
                "sha256": sha(decision_path),
            },
            "physical_waves": batch_receipts,
            "coverage": coverage_rows,
            "continued_stage": continued_stage,
            "state_path": str(state_path),
            "state_sha256_at_audit": sha(state_path),
        }

    require(completed_receipts, "no complete outer round found")
    prior_audit = campaign / "pg_correspondence_audit.json"
    monitor = campaign / "completion_monitor_summary.json"
    document = {
        "schema_version": "mysql_algorithm_c_first_complete_outer_round_audit_v1",
        "created_at": datetime.now().astimezone().isoformat(),
        "status": "PASS_RUNNING_FIRST_COMPLETE_OUTER_OBSERVED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "run_prefix": prefix,
        "paper_baseline_resource_gates": baseline_receipts,
        "completed_outer_001_scenes": completed_receipts,
        "completed_scene_count": len(completed_receipts),
        "all_four_scenes_complete_outer_001": len(completed_receipts) == len(SCENES),
        "experiment_complete": False,
        "experiment_continues_independently": True,
        "prior_correspondence_audit": {"path": str(prior_audit), "sha256": sha(prior_audit)},
        "monitor_snapshot": {"path": str(monitor), "sha256": sha(monitor)},
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

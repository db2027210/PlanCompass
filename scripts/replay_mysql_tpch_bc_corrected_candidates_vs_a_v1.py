#!/usr/bin/env python3
"""Replay the valid B champion and corrected C semantic-batch champion.

Candidate selection reads B/C evidence only.  The completed same-cohort A
artifact is opened strictly after both points are frozen and is used only as
the post-search comparison threshold.  Each B/C point is measured twice on
each of the two sealed TPC-H workers (four full Q1-Q22 measurements per point).
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
from statistics import fmean
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_b_pg_outer_policy_v31 as policy
from scripts import mysql_cross_project_wave_coordination_v24 as gate_v24
from scripts import run_mysql_tpch_algorithm_b_client_qps_fresh_v1 as b_runtime


SCENE = "tpch_sf4_memory"
HISTORY = ROOT / "history/mysql_four_scene_20260809"
B_STATE = (
    HISTORY
    / "v1132_to_b_tpch_client_qps_frozen_outer2_resume_20260830_tpch"
    / "run_state.json"
)
C_STATE = (
    HISTORY
    / "v1153_tpch_c_active_scene_complete_20260830"
    / "run_state.json"
)
C_DECISION = (
    HISTORY
    / "v1151_to_c_tpch_cached_no_move_resume_20260830_tpch"
    / "outer_013/tpch_sf4_memory.decision.json"
)
A_COMPARISON = (
    HISTORY
    / "v1156_tpch_abc_client_qps_counterbalanced_final_20260830"
    / "comparison_result.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular JSON evidence required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object evidence required: {path}")
    return dict(value)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def _select_bc_points() -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    """Freeze points without opening any Algorithm A artifact."""
    b_state = load(B_STATE)
    c_state = load(C_STATE)
    c_decision = load(C_DECISION)
    if (
        b_state.get("status") != "COMPLETE"
        or (b_state.get("converged") or {}).get(SCENE) is not True
        or c_state.get("status") != "COMPLETE"
        or (c_state.get("converged") or {}).get(SCENE) is not True
        or b_state.get("algorithm_a_parameter_or_measurement_read") is not False
        or c_state.get("algorithm_a_parameter_or_measurement_read") is not False
    ):
        raise RuntimeError("completed A-independent B/C search evidence differs")

    rows = [
        _mapping(row, "C evaluation")
        for row in (c_decision.get("evaluations") or [])
    ]
    if len(rows) < 5:
        raise RuntimeError("C outer 13 lacks the complete base Top-5")
    base_rows = rows[:5]
    if [int(row.get("rank") or 0) for row in base_rows] != [1, 2, 3, 4, 5]:
        raise RuntimeError("C outer 13 base Top-5 order differs")

    accepted: list[dict[str, Any]] = []
    for row in base_rows:
        decision = _mapping(row.get("outer_decision"), "C outer decision")
        if decision.get("accepted") is not True:
            continue
        ranking = _mapping(row.get("candidate_ranking"), "C candidate ranking")
        policy.moved_shrink_subset(
            incumbent_params={
                "disk_temptable_create_cost": 20,
                "disk_temptable_row_cost": 0.244889409684,
                "io_block_read_cost": 0.49831045597,
                "key_compare_cost": 0.05,
                "memory_block_read_cost": 0.481881228297,
                "memory_temptable_create_cost": 1,
                "memory_temptable_row_cost": 0.1,
                "row_evaluate_cost": 0.0749894209332,
            },
            candidate_ranking=ranking,
            active_params=[
                "io_block_read_cost",
                "memory_block_read_cost",
                "row_evaluate_cost",
                "disk_temptable_row_cost",
            ],
        )
        accepted.append({"row": row, "decision": decision})
    accepted.sort(key=policy.accepted_selection_key, reverse=True)
    if not accepted or int(accepted[0]["row"].get("rank") or 0) != 3:
        raise RuntimeError("corrected first accepting C semantic batch differs")

    c_row = accepted[0]["row"]
    if not math.isclose(
        float(c_row.get("db_qps") or math.nan),
        0.1990311194180822,
        rel_tol=1e-12,
        abs_tol=1e-15,
    ):
        raise RuntimeError("corrected C candidate QPS evidence differs")
    points = {
        "b": copy.deepcopy(_mapping(b_state["params_by_scene"][SCENE], "B params")),
        "c": copy.deepcopy(_mapping(c_row["params"], "corrected C params")),
    }
    selection = {
        "algorithm_a_artifact_opened_during_selection": False,
        "algorithm_a_used_as_search_prior": False,
        "b": {
            "state_path": str(B_STATE),
            "state_sha256": sha(B_STATE),
            "search_incumbent_client_qps": float(
                b_state["incumbents"][SCENE]["db_qps"]
            ),
        },
        "c": {
            "decision_path": str(C_DECISION),
            "decision_sha256": sha(C_DECISION),
            "semantic_batch_kind": "base_top5",
            "selected_rank": 3,
            "search_lane_normalized_client_qps": float(c_row["db_qps"]),
            "pg_first_accepting_semantic_batch_preserved": True,
        },
    }
    return points, selection


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh output required: {output}")
    output.mkdir(parents=True, mode=0o700)

    # Freeze B/C points before any A read.
    points, selection = _select_bc_points()
    a_result = load(A_COMPARISON)
    a_final = _mapping(
        _mapping(a_result.get("summaries"), "A comparison summaries").get(
            "a_final"
        ),
        "A final summary",
    )
    a_threshold_qps = float(a_final.get("mean_client_qps") or math.nan)
    if (
        a_result.get("comparison_metric")
        != "fixed_five_lane_q1_q22_client_qps"
        or not math.isfinite(a_threshold_qps)
        or a_threshold_qps <= 0.0
    ):
        raise RuntimeError("post-search A threshold evidence differs")

    original_install = b_runtime.install_runtime

    def install_runtime():
        core = original_install()
        core._impl["SCENES"] = (SCENE,)
        return core

    b_runtime.install_runtime = install_runtime
    try:
        core = b_runtime.install_runtime()
    finally:
        b_runtime.install_runtime = original_install
    impl = core._impl
    alg = impl["alg"]
    state_path = output / "run_state.json"
    state: dict[str, Any] = {
        "schema_version": "mysql_tpch_bc_corrected_candidate_replay_state_v1",
        "status": "STARTING",
        "stage": "VALIDATING_RUNTIME",
        "run_id": args.run_id,
        "scene": SCENE,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm_search_reexecuted": False,
        "accepted_state_credit_increment": 0,
        "candidate_selection": selection,
        "algorithm_a_read_after_bc_point_freeze": True,
    }
    alg.atomic(state_path, state)
    try:
        impl["configure_runtime"]()
        freeze, workers, point_gate = alg.r0mod._validate_authority()
        if set(workers) != {"tpch_w1", "tpch_w2"}:
            raise RuntimeError("sealed TPC-H worker pair differs")
        target_cpus = {
            cpu
            for worker in workers.values()
            for field in ("database_cpuset", "client_cpuset")
            for cpu in alg.r0mod._cpus(str(worker[field]))
        }
        orders = [
            {"tpch_w1": "b", "tpch_w2": "c"},
            {"tpch_w1": "c", "tpch_w2": "b"},
            {"tpch_w1": "b", "tpch_w2": "c"},
            {"tpch_w1": "c", "tpch_w2": "b"},
        ]
        state.update({
            "status": "RUNNING",
            "stage": "BC_REPLAY",
            "runtime_point_gate": point_gate,
            "counterbalanced_order": orders,
            "post_search_a_threshold": {
                "path": str(A_COMPARISON),
                "sha256": sha(A_COMPARISON),
                "mean_client_qps": a_threshold_qps,
            },
            "controller_source_path": str(Path(__file__).resolve()),
            "controller_source_sha256": sha(Path(__file__).resolve()),
            "resource_freeze_path": str(b_runtime.FREEZE),
            "resource_freeze_sha256": sha(b_runtime.FREEZE),
            "points": points,
        })
        manifest = output / "formal_launch_manifest.json"
        alg.atomic(manifest, {
            "schema_version": "mysql_tpch_bc_corrected_candidate_replay_manifest_v1",
            "status": "FROZEN_RUNTIME_VALIDATED",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "scene": SCENE,
            "workload": {
                "scale_factor": 4,
                "required_queries": "Q1-Q22 exactly once per measurement",
                "lanes": 5,
                "hard_timeout_seconds": 900,
            },
            "comparison_metric": "fixed_five_lane_q1_q22_client_qps",
            "points": points,
            "candidate_selection": selection,
            "counterbalanced_order": orders,
            "each_point_measurements": 4,
            "each_point_measurements_per_worker": 2,
            "algorithm_a_used_as_post_search_check_only": True,
            "algorithm_a_used_for_b_or_c_search": False,
            "accepted_state_credit_increment": 0,
            "external_process_control_or_stop_performed": False,
            "lease_mode": "none",
            "resource_freeze_path": str(b_runtime.FREEZE),
            "resource_freeze_sha256": sha(b_runtime.FREEZE),
            "source_bindings": {
                "controller": {
                    "path": str(Path(__file__).resolve()),
                    "sha256": sha(Path(__file__).resolve()),
                },
                "b_state": selection["b"],
                "c_decision": selection["c"],
                "a_post_search_threshold": {
                    "path": str(A_COMPARISON),
                    "sha256": sha(A_COMPARISON),
                },
            },
        })
        state["formal_launch_manifest_path"] = str(manifest)
        state["formal_launch_manifest_sha256"] = sha(manifest)
        alg.atomic(state_path, state)

        rows_by_point: dict[str, list[dict[str, Any]]] = {"b": [], "c": []}
        measurement = impl["single_measurement"]
        for wave_index, labels in enumerate(orders, 1):
            state["stage"] = f"WAVE_{wave_index:02d}_DYNAMIC_GATE"
            alg.atomic(state_path, state)
            gate = gate_v24.rolling_quiet_gate(
                run_id=f"{args.run_id}-wave-{wave_index:02d}",
                required_seconds=15.0,
                maximum_wait_seconds=90.0,
                maximum_load1=512.0,
                target_cpus=target_cpus,
            )
            gate_path = output / f"wave_{wave_index:02d}_dynamic_gate.json"
            alg.atomic(gate_path, gate)
            if gate.get("status") != "PASS":
                raise RuntimeError(f"wave {wave_index} dynamic gate failed")
            assignments = {
                worker_id: {
                    "rank": position,
                    "params": copy.deepcopy(points[label]),
                    "comparison_point": label,
                }
                for position, (worker_id, label) in enumerate(
                    labels.items(), start=1
                )
            }
            wave_root = output / f"wave_{wave_index:02d}"
            measured = measurement.measure_batch(
                freeze,
                workers,
                assignments,
                wave_root,
                f"{args.run_id}_wave_{wave_index:02d}",
                state_path=state_path,
                stage_prefix=f"BC_REPLAY_WAVE_{wave_index:02d}",
            )
            if set(measured) != set(assignments):
                raise RuntimeError(f"wave {wave_index} worker coverage differs")
            for worker_id, row in measured.items():
                label = labels[worker_id]
                client_qps = float(row.get("db_qps") or math.nan)
                if (
                    row.get("hard_gate_passed") is not True
                    or row.get("coverage_complete") is not True
                    or row.get("qps_metric")
                    != "fixed_five_lane_q1_q22_client_qps"
                    or not math.isfinite(client_qps)
                    or client_qps <= 0.0
                ):
                    raise RuntimeError(f"invalid replay row: {label}/{worker_id}")
                rows_by_point[label].append({
                    "wave": wave_index,
                    "worker_id": worker_id,
                    "client_qps": client_qps,
                    "server_db_qps_audit": float(row["server_db_qps_audit"]),
                    "p95_ms": float(row["p95_ms"]),
                    "resource_means": copy.deepcopy(row["resource_means"]),
                    "batch_result_path": str(wave_root / "batch_result.json"),
                    "batch_result_sha256": sha(wave_root / "batch_result.json"),
                })
            state["completed_waves"] = wave_index
            alg.atomic(state_path, state)

        summaries: dict[str, Any] = {}
        for label, rows in rows_by_point.items():
            if len(rows) != 4 or any(
                sum(row["worker_id"] == worker for row in rows) != 2
                for worker in ("tpch_w1", "tpch_w2")
            ):
                raise RuntimeError(f"{label} repeated worker coverage differs")
            mean_qps = fmean(row["client_qps"] for row in rows)
            summaries[label] = {
                "mean_client_qps": mean_qps,
                "gain_vs_a_final": mean_qps / a_threshold_qps - 1.0,
                "rows": rows,
                "params": points[label],
            }
        b_beats_a = summaries["b"]["mean_client_qps"] > a_threshold_qps
        c_beats_a = summaries["c"]["mean_client_qps"] > a_threshold_qps
        result = {
            "schema_version": "mysql_tpch_bc_corrected_candidate_replay_v1",
            "status": "PASS" if b_beats_a and c_beats_a else "GOAL_NOT_MET",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "comparison_metric": "fixed_five_lane_q1_q22_client_qps",
            "a_final_post_search_threshold_qps": a_threshold_qps,
            "b_beats_a_final": b_beats_a,
            "c_beats_a_final": c_beats_a,
            "summaries": summaries,
            "candidate_selection": selection,
            "algorithm_a_used_as_post_search_check_only": True,
            "algorithm_a_used_for_b_or_c_search": False,
            "accepted_state_credit_increment": 0,
        }
        result_path = output / "comparison_result.json"
        alg.atomic(result_path, result)
        state.update({
            "status": result["status"],
            "stage": "COMPLETE",
            "result_path": str(result_path),
            "result_sha256": sha(result_path),
        })
        alg.atomic(state_path, state)
        print(json.dumps({
            "status": result["status"],
            "result": str(result_path),
            "b_beats_a_final": b_beats_a,
            "c_beats_a_final": c_beats_a,
        }, sort_keys=True))
        return 0 if result["status"] == "PASS" else 2
    except BaseException as error:
        state.update({
            "status": "FAILED_CLOSED",
            "stage": "FAILED",
            "error": f"{type(error).__name__}: {error}",
        })
        alg.atomic(state_path, state)
        raise


if __name__ == "__main__":
    raise SystemExit(main())

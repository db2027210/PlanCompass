#!/usr/bin/env python3
"""Counterbalanced same-cohort TPC-H client-QPS check after B and C finish.

Algorithm A is read only after both fresh B/C searches have converged.  It is
therefore a post-search check, never a search prior.  Baseline and A/B/C are
each replayed once on each sealed disk2 worker in counterbalanced order.
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

from scripts import mysql_cross_project_wave_coordination_v24 as gate_v24
from scripts import run_mysql_tpch_algorithm_b_client_qps_fresh_v1 as b_runtime


SCENE = "tpch_sf4_memory"
B_STATE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v1125_to_b_tpch_client_qps_disk2_normalized_20260830_tpch/run_state.json"
)
C_STATE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v1126_to_c_tpch_client_qps_disk2_normalized_20260830_tpch/run_state.json"
)
A_STATE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v100_to_a_tpch_external_diagnostic_resume_20260823_attempt001_tpch"
    / "algorithm_a_state.json"
)
A_FINAL_DECISION = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v100_to_a_tpch_external_diagnostic_resume_20260823_attempt001_tpch"
    / "round_014/outer_decision.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def converged(state: Mapping[str, Any]) -> bool:
    value = state.get("converged")
    if isinstance(value, Mapping):
        return value.get(SCENE) is True
    return value is True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh comparison output required: {output}")
    output.mkdir(parents=True, mode=0o700)

    core = b_runtime.install_runtime()
    impl = core._impl
    alg = impl["alg"]
    state_path = output / "run_state.json"
    state: dict[str, Any] = {
        "schema_version": "mysql_tpch_abc_counterbalanced_client_qps_state_v1",
        "status": "STARTING",
        "stage": "VALIDATING_COMPLETED_B_AND_C",
        "run_id": args.run_id,
        "scene": SCENE,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm_search_reexecuted": False,
        "accepted_state_credit_increment": 0,
        "algorithm_a_read_before_bc_completion": False,
    }
    alg.atomic(state_path, state)
    try:
        # Validate B and C first.  A must not be read until this succeeds.
        b_state = load(B_STATE)
        c_state = load(C_STATE)
        if (
            b_state.get("status") != "COMPLETE"
            or c_state.get("status") != "COMPLETE"
            or not converged(b_state)
            or not converged(c_state)
            or (b_state.get("qps_metric_contract") or {}).get(
                "outer_progress_metric"
            ) != "fixed_five_lane_q1_q22_client_qps"
            or (c_state.get("qps_metric_contract") or {}).get(
                "outer_progress_metric"
            ) != "fixed_five_lane_q1_q22_client_qps"
            or b_state.get(
                "outer_acceptance_uses_same_worker_normalized_client_qps"
            ) is not True
            or c_state.get(
                "outer_acceptance_uses_same_worker_normalized_client_qps"
            ) is not True
            or (b_state.get("same_worker_qps_normalization") or {}).get(
                "schema_version"
            ) != "mysql_tpch_same_worker_client_qps_normalization_v1"
            or (c_state.get("same_worker_qps_normalization") or {}).get(
                "schema_version"
            ) != "mysql_tpch_same_worker_client_qps_normalization_v1"
        ):
            raise RuntimeError("fresh B/C client-QPS searches are not complete")

        # This is the first A read in this process and happens post convergence.
        a_state = load(A_STATE)
        a_decision = load(A_FINAL_DECISION)
        if (
            a_state.get("status") != "COMPLETE"
            or a_state.get("converged") is not True
            or int(a_state.get("accepted") or 0) != 10
            or (a_decision.get("accepted") or {}).get("decision", {}).get(
                "accepted"
            ) is not True
        ):
            raise RuntimeError("Algorithm A final comparison point differs")

        points = {
            "baseline": copy.deepcopy(alg.DEFAULTS),
            "a": copy.deepcopy(dict(a_state["incumbent_params"])),
            "b": copy.deepcopy(dict(b_state["params_by_scene"][SCENE])),
            "c": copy.deepcopy(dict(c_state["params_by_scene"][SCENE])),
        }
        impl["configure_runtime"]()
        freeze, workers, point_gate = alg.r0mod._validate_authority()
        if set(workers) != {"tpch_w1", "tpch_w2"}:
            raise RuntimeError("counterbalanced comparison worker pair differs")
        target_cpus = {
            cpu
            for worker in workers.values()
            for field in ("database_cpuset", "client_cpuset")
            for cpu in alg.r0mod._cpus(str(worker[field]))
        }
        state.update({
            "status": "RUNNING",
            "stage": "COUNTERBALANCED_REPLAY",
            "algorithm_a_read_after_bc_completion": True,
            "bc_completion_bindings": {
                "b": {"path": str(B_STATE), "sha256": sha(B_STATE)},
                "c": {"path": str(C_STATE), "sha256": sha(C_STATE)},
            },
            "algorithm_a_check_binding": {
                "state_path": str(A_STATE),
                "state_sha256": sha(A_STATE),
                "decision_path": str(A_FINAL_DECISION),
                "decision_sha256": sha(A_FINAL_DECISION),
            },
            "runtime_point_gate": point_gate,
            "controller_source_path": str(Path(__file__).resolve()),
            "controller_source_sha256": sha(Path(__file__).resolve()),
            "resource_freeze_path": str(b_runtime.FREEZE),
            "resource_freeze_sha256": sha(b_runtime.FREEZE),
            "authority_bindings": {
                name: {
                    "path": str(b_runtime.AUTHORITY_ROOT / filename),
                    "sha256": sha(b_runtime.AUTHORITY_ROOT / filename),
                }
                for name, filename in {
                    "preflight": "transition_preflight.json",
                    "prepared": "prepared_binding.json",
                    "accounts": "benchmark_accounts_binding.json",
                }.items()
            },
            "effective_worker_resource_readback": {
                worker_id: {
                    key: copy.deepcopy(worker.get(key))
                    for key in (
                        "container",
                        "container_id",
                        "runtime_root",
                        "host_port",
                        "database_cpuset",
                        "database_cpuset_mems",
                        "client_cpuset",
                        "client_cpuset_mems",
                        "cpu_quota_cores",
                        "memory_bytes",
                        "swap_bytes",
                        "io_read_bps",
                        "io_write_bps",
                        "pids_max",
                        "external_database",
                        "external_current_user",
                    )
                }
                for worker_id, worker in workers.items()
            },
            "counterbalanced_order": [
                {"tpch_w1": "baseline", "tpch_w2": "c"},
                {"tpch_w1": "a", "tpch_w2": "b"},
                {"tpch_w1": "b", "tpch_w2": "a"},
                {"tpch_w1": "c", "tpch_w2": "baseline"},
            ],
        })
        manifest_path = output / "formal_launch_manifest.json"
        alg.atomic(manifest_path, {
            "schema_version": "mysql_tpch_abc_counterbalanced_launch_manifest_v1",
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
            "algorithm_a_used_as_post_search_check_only": True,
            "algorithm_a_used_for_b_or_c_search": False,
            "algorithm_search_reexecuted": False,
            "points": copy.deepcopy(points),
            "counterbalanced_order": copy.deepcopy(
                state["counterbalanced_order"]
            ),
            "resource_freeze_path": str(b_runtime.FREEZE),
            "resource_freeze_sha256": sha(b_runtime.FREEZE),
            "authority_bindings": copy.deepcopy(state["authority_bindings"]),
            "effective_worker_resource_readback": copy.deepcopy(
                state["effective_worker_resource_readback"]
            ),
            "source_bindings": {
                "controller": {
                    "path": str(Path(__file__).resolve()),
                    "sha256": sha(Path(__file__).resolve()),
                },
                "metric_adapter": {
                    "path": str(Path(b_runtime.client_qps.__file__).resolve()),
                    "sha256": sha(Path(b_runtime.client_qps.__file__).resolve()),
                },
                "dynamic_gate": {
                    "path": str(Path(gate_v24.__file__).resolve()),
                    "sha256": sha(Path(gate_v24.__file__).resolve()),
                },
                "b_state": {"path": str(B_STATE), "sha256": sha(B_STATE)},
                "c_state": {"path": str(C_STATE), "sha256": sha(C_STATE)},
                "a_state": {"path": str(A_STATE), "sha256": sha(A_STATE)},
                "a_final_decision": {
                    "path": str(A_FINAL_DECISION),
                    "sha256": sha(A_FINAL_DECISION),
                },
            },
            "accepted_state_credit_increment": 0,
            "external_process_control_or_stop_performed": False,
            "lease_mode": "none",
        })
        state["formal_launch_manifest_path"] = str(manifest_path)
        state["formal_launch_manifest_sha256"] = sha(manifest_path)
        alg.atomic(state_path, state)

        rows_by_point: dict[str, list[dict[str, Any]]] = {
            label: [] for label in points
        }
        measurement = impl["single_measurement"]
        orders = state["counterbalanced_order"]
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
                stage_prefix=f"ABC_COMPARE_WAVE_{wave_index:02d}",
            )
            if set(measured) != set(assignments):
                raise RuntimeError(f"wave {wave_index} worker set differs")
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
                    or not math.isclose(
                        client_qps,
                        float(row.get("client_qps_audit") or math.nan),
                        rel_tol=1e-12,
                        abs_tol=1e-15,
                    )
                ):
                    raise RuntimeError(
                        f"invalid client-QPS comparison row: {label}/{worker_id}"
                    )
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
            if len(rows) != 2 or {row["worker_id"] for row in rows} != {
                "tpch_w1", "tpch_w2"
            }:
                raise RuntimeError(f"{label} counterbalanced coverage differs")
            summaries[label] = {
                "mean_client_qps": fmean(row["client_qps"] for row in rows),
                "mean_server_db_qps_audit": fmean(
                    row["server_db_qps_audit"] for row in rows
                ),
                "rows": rows,
                "params": points[label],
            }
        baseline_qps = float(summaries["baseline"]["mean_client_qps"])
        a_qps = float(summaries["a"]["mean_client_qps"])
        for label in ("a", "b", "c"):
            summaries[label]["client_qps_gain_vs_baseline"] = (
                float(summaries[label]["mean_client_qps"]) / baseline_qps - 1.0
            )
        b_beats_a = float(summaries["b"]["mean_client_qps"]) > a_qps
        c_beats_a = float(summaries["c"]["mean_client_qps"]) > a_qps
        a_server_baseline = float(a_state["canonical_baseline"]["db_qps"])
        a_server_final = float(
            a_decision["accepted"]["row"]["db_qps"]
        )
        result = {
            "schema_version": "mysql_tpch_abc_counterbalanced_client_qps_v1",
            "status": "PASS" if b_beats_a and c_beats_a else "GOAL_NOT_MET",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "comparison_metric": "fixed_five_lane_q1_q22_client_qps",
            "same_current_resource_freeze": True,
            "same_two_disk2_workers": True,
            "each_point_replayed_once_per_worker": True,
            "counterbalanced_order": orders,
            "algorithm_a_used_as_post_search_check_only": True,
            "algorithm_a_used_for_b_or_c_search": False,
            "summaries": summaries,
            "b_beats_a": b_beats_a,
            "c_beats_a": c_beats_a,
            "reported_algorithm_a_server_qps": {
                "baseline": a_server_baseline,
                "final": a_server_final,
                "gain": a_server_final / a_server_baseline - 1.0,
                "metric_role": "server_statement_counter_audit_not_full_suite_client_throughput",
            },
            "algorithm_search_reexecuted": False,
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
            "b_beats_a": b_beats_a,
            "c_beats_a": c_beats_a,
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

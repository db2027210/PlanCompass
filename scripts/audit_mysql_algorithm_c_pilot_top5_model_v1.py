#!/usr/bin/env python3
"""Audit predicted Top-5 ranking against the non-formal real pilot replay."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_algorithm_c_boundary_resume_outer4_pilot_nonformal_v1_20260812T2207p0800"
)
CHECKPOINT = (
    ROOT / "history/mysql_four_scene_20260809"
    / "algorithm_c_outer4_checkpoint_v2_optimized_20260812/prepared_binding.json"
)
PREDICTIONS = RUN_ROOT / "outer_005/predicted_rankings.json"
INTERFERENCE = (
    ROOT / "preflight/mysql_four_scene_20260809"
    / "runtime_preflight_pilot_override_v2_20260812T2206p0800.json"
)
OUTPUT_ROOT = (
    ROOT / "history/mysql_four_scene_20260809"
    / "algorithm_c_pilot_top5_model_diagnostic_v1_20260812"
)
OUTPUT = OUTPUT_ROOT / "model_diagnostic.json"
SCENES = ["job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io"]
TARGET = {
    "job_memory": "memory",
    "tpch_sf4_memory": "memory",
    "hybench_xp_cpu": "cpu",
    "hybench_xp_io": "io",
}
RESOURCES = ["cpu", "memory", "io"]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def binding(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": sha(path)}


def average_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: (values[index], index))
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and values[order[end]] == values[order[cursor]]:
            end += 1
        average = (cursor + 1 + end) / 2.0
        for position in range(cursor, end):
            ranks[order[position]] = average
        cursor = end
    return ranks


def pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum(
        (x - left_mean) * (y - right_mean) for x, y in zip(left, right)
    )
    left_scale = math.sqrt(sum((x - left_mean) ** 2 for x in left))
    right_scale = math.sqrt(sum((y - right_mean) ** 2 for y in right))
    if left_scale == 0.0 or right_scale == 0.0:
        return None
    return numerator / (left_scale * right_scale)


def spearman(left: list[float], right: list[float]) -> float | None:
    return pearson(average_ranks(left), average_ranks(right))


def main() -> int:
    inputs = [CHECKPOINT, PREDICTIONS, INTERFERENCE, RUN_ROOT / "run_state.json"]
    inputs.extend(RUN_ROOT / "outer_005" / f"{scene}.decision.json" for scene in SCENES)
    inputs.extend(
        RUN_ROOT / "outer_005" / f"top5_batch_{index}" / "batch_result.json"
        for index in range(1, 4)
    )
    for path in inputs:
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"regular pilot evidence required: {path}")
    if OUTPUT_ROOT.exists():
        raise RuntimeError(f"refusing to replace diagnostic output: {OUTPUT_ROOT}")

    checkpoint = load(CHECKPOINT)
    predictions = load(PREDICTIONS)
    run_state = load(RUN_ROOT / "run_state.json")
    interference = load(INTERFERENCE)
    incumbents = checkpoint["reconstructed_state"]["incumbents"]
    if (
        run_state.get("status") != "PILOT_COMPLETE"
        or run_state.get("execution_class") != "INTERFERENCE_OVERRIDE_PILOT"
        or run_state.get("formal_evidence_eligible") is not False
        or run_state.get("accepted_state_credit_prohibited") is not True
        or interference.get("status") != "BLOCKED_FAIL_CLOSED"
        or interference.get("formal_launch_authorized") is not False
        or predictions.get("outer_attempt") != 5
        or set(predictions.get("selected_top_k") or {}) != set(SCENES)
    ):
        raise RuntimeError("pilot diagnostic authority differs")

    batch_bindings = []
    for index in range(1, 4):
        path = RUN_ROOT / "outer_005" / f"top5_batch_{index}" / "batch_result.json"
        document = load(path)
        expected_count = 8 if index < 3 else 4
        if (
            document.get("status") != "PASS"
            or document.get("failures") not in ([], None)
            or len(document.get("measurements") or {}) != expected_count
            or any(
                row.get("hard_gate_passed") is not True
                for row in (document.get("measurements") or {}).values()
            )
        ):
            raise RuntimeError(f"pilot batch {index} is not complete hard-gate evidence")
        batch_bindings.append(binding(path))

    scene_audits: dict[str, Any] = {}
    for scene in SCENES:
        target = TARGET[scene]
        incumbent = incumbents[scene]
        decision_path = RUN_ROOT / "outer_005" / f"{scene}.decision.json"
        decision = load(decision_path)
        selected = predictions["selected_top_k"][scene]
        evaluations = decision.get("evaluations") or []
        # selected_top_k preserves each row's original full-pool rank after
        # replay-quarantine filtering, while the measurement layer assigns
        # deterministic replay slots 1..5.  Align by the sealed list order and
        # retain the original rank as provenance.
        predicted_by_rank = {
            replay_slot: row for replay_slot, row in enumerate(selected, 1)
        }
        observed_by_rank = {int(row["rank"]): row for row in evaluations}
        if set(predicted_by_rank) != set(range(1, 6)) or set(observed_by_rank) != set(range(1, 6)):
            raise RuntimeError(f"Top-5 rank coverage differs for {scene}")

        rows: list[dict[str, Any]] = []
        for rank in range(1, 6):
            predicted = predicted_by_rank[rank]
            observed = observed_by_rank[rank]
            predicted_params = predicted.get("optimized_params") or {}
            observed_params = observed.get("params") or {}
            if (
                set(predicted_params) != set(observed_params)
                or any(
                    not math.isclose(
                        float(predicted_params[name]),
                        float(observed_params[name]),
                        rel_tol=1e-12,
                        abs_tol=1e-12,
                    )
                    for name in predicted_params
                )
            ):
                raise RuntimeError(
                    f"prediction/measurement parameter alignment differs for "
                    f"{scene} replay slot {rank}"
                )
            predicted_resources = predicted["predicted_resources"]
            observed_resources = observed["resource_means"]
            qps_change = (
                float(observed["db_qps"]) / float(incumbent["db_qps"]) - 1.0
            )
            target_relief = (
                float(incumbent["resource_means"][target])
                - float(observed_resources[target])
            )
            non_targets = [name for name in RESOURCES if name != target]
            non_target_increase = sum(
                max(
                    0.0,
                    float(observed_resources[name])
                    - float(incumbent["resource_means"][name]),
                )
                for name in non_targets
            )
            # Diagnostic only. Formal acceptance remains the exact tiered gate.
            actual_utility = qps_change + target_relief - 0.25 * non_target_increase
            errors = {
                name: abs(float(predicted_resources[name]) - float(observed_resources[name]))
                for name in RESOURCES
            }
            outer_decision = observed.get("outer_decision") or {}
            rows.append({
                "rank": rank,
                "original_prediction_pool_rank": int(predicted["rank"]),
                "worker_id": observed["worker_id"],
                "predicted_score": float(predicted["candidate_score"]),
                "predicted_score_before_confidence": float(
                    predicted["candidate_score_before_confidence"]
                ),
                "prediction_confidence": float(
                    predicted.get("resource_model", {}).get("confidence", 0.0)
                ),
                "predicted_resources": predicted_resources,
                "observed_resources": observed_resources,
                "absolute_resource_error": errors,
                "observed_qps_relative_change": qps_change,
                "observed_target_relief": target_relief,
                "observed_non_target_increase": non_target_increase,
                "diagnostic_actual_utility": actual_utility,
                "hard_gate_passed": observed.get("hard_gate_passed") is True,
                "tiered_acceptance": outer_decision.get("accepted") is True,
                "tiered_acceptance_reason": outer_decision.get("reason"),
            })

        predicted_scores = [row["predicted_score"] for row in rows]
        actual_utilities = [row["diagnostic_actual_utility"] for row in rows]
        predicted_targets = [row["predicted_resources"][target] for row in rows]
        observed_targets = [row["observed_resources"][target] for row in rows]
        qps_changes = [row["observed_qps_relative_change"] for row in rows]
        mae_by_resource = {
            name: sum(row["absolute_resource_error"][name] for row in rows) / len(rows)
            for name in RESOURCES
        }
        overall_mae = sum(mae_by_resource.values()) / len(mae_by_resource)
        ranking_rho = spearman(predicted_scores, actual_utilities)
        target_rho = spearman(predicted_targets, observed_targets)
        qps_rho = spearman(predicted_scores, qps_changes)
        actual_best_rank = max(rows, key=lambda row: row["diagnostic_actual_utility"])["rank"]
        rank_signal = max(0.0, float(ranking_rho or 0.0))
        error_signal = max(0.25, 1.0 - min(0.75, overall_mae))
        suggested_multiplier = max(0.25, 0.5 * rank_signal + 0.5 * error_signal)
        scene_audits[scene] = {
            "target_resource": target,
            "incumbent": incumbent,
            "rows": rows,
            "diagnostics": {
                "spearman_predicted_score_vs_actual_utility": ranking_rho,
                "spearman_predicted_target_vs_observed_target": target_rho,
                "spearman_predicted_score_vs_qps_change": qps_rho,
                "mae_by_resource": mae_by_resource,
                "overall_resource_mae": overall_mae,
                "predicted_top1_is_diagnostic_actual_best": actual_best_rank == 1,
                "diagnostic_actual_best_predicted_rank": actual_best_rank,
            },
            "soft_ranking_recommendation": {
                "action": "downweight_until_clean_formal_top5"
                if suggested_multiplier < 0.75 else "retain_then_recheck_clean_formal_top5",
                "suggested_confidence_multiplier": suggested_multiplier,
                "formal_model_mutation_authorized": False,
            },
            "decision_evidence": binding(decision_path),
        }

    document = {
        "schema_version": "mysql_algorithm_c_pilot_top5_model_diagnostic_v1",
        "status": "PASS_DIAGNOSTIC_ONLY",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "execution_class": "INTERFERENCE_OVERRIDE_PILOT",
        "formal_evidence_eligible": False,
        "formal_model_mutation_authorized": False,
        "reason": "real_Top5_replay_completed_but_dynamic_interference_gate_failed",
        "method": {
            "ranking_metric": "Spearman_with_average_tie_ranks",
            "resource_error_metric": "mean_absolute_error_on_cpu_memory_io_fractions",
            "diagnostic_actual_utility": (
                "qps_relative_change + target_relief - 0.25*positive_non_target_increase"
            ),
            "formal_acceptance_gate_unchanged": True,
        },
        "scene_audits": scene_audits,
        "next_action": {
            "use_this_artifact_for_diagnostic_downweighting_only": True,
            "collect_clean_formal_Top5_before_sealing_model_successor": True,
            "do_not_credit_pilot_acceptance_or_convergence": True,
        },
        "evidence": {
            "checkpoint": binding(CHECKPOINT),
            "predictions": binding(PREDICTIONS),
            "run_state": binding(RUN_ROOT / "run_state.json"),
            "interference": binding(INTERFERENCE),
            "batches": batch_bindings,
        },
    }
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=False)
    OUTPUT.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

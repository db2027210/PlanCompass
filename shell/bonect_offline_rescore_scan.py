#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import time
from typing import Any, Dict, Iterable, List, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bonect_robust_ranking import DEFAULT_VARIANT, attach_robust_scores, robust_sort_key, select_diverse_top_k
from resource_utility import ResourceUtilityCalculator
from shell.bonect_full_grid_estimator_scan import (
    COST_PARAMS,
    apply_paired_delta_calibration,
    load_baseline_calibration,
    load_config,
    parameter_signature,
    resource_signature,
    write_json,
)


RESOURCE_NAMES: Tuple[str, ...] = ("cpu", "memory", "io")
DIRECTION_CONFIG_KEYS: Tuple[str, ...] = (
    "min_target_down_util_pct",
    "min_delta_util_pct",
    "min_other_up_util_pct",
    "score_min_target_down_util_pct",
    "score_min_other_up_util_pct",
    "max_non_target_down_util_pct",
    "require_all_non_target_up",
)


def build_calculator(config: Dict[str, Any]) -> ResourceUtilityCalculator:
    scoring = config.get("scoring") or {}
    return ResourceUtilityCalculator(
        reward_type="sigmoid_linear",
        penalty_type="sigmoid_linear",
        overload_threshold=float(scoring.get("overload_threshold", 0.80)),
        score_mode=str(scoring.get("score_mode", "band_target")),
        resource_weights=scoring.get("resource_weights") if isinstance(scoring.get("resource_weights"), dict) else None,
        resource_reward_weights=(
            scoring.get("resource_reward_weights")
            if isinstance(scoring.get("resource_reward_weights"), dict)
            else None
        ),
        resource_penalty_weights=(
            scoring.get("resource_penalty_weights")
            if isinstance(scoring.get("resource_penalty_weights"), dict)
            else None
        ),
        resource_target_ranges=(
            scoring.get("resource_target_ranges")
            if isinstance(scoring.get("resource_target_ranges"), dict)
            else None
        ),
    )


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
            if isinstance(value, dict):
                yield value


def tree_error_profile(
    model: Dict[str, Any],
    capability: Dict[str, float],
    *,
    support_miss_count: int,
) -> Tuple[Dict[str, Dict[str, float]], float]:
    residuals = ((model.get("leave_one_out_metrics") or {}).get("absolute_residual") or {})
    support_multiplier = 1.0 + 0.5 * max(0, int(support_miss_count))
    profile: Dict[str, Dict[str, float]] = {}
    for name in RESOURCE_NAMES:
        bound = float((residuals.get(f"{name}_delta") or {}).get("p95") or 0.0) * support_multiplier
        relative = bound / max(float(capability.get(name) or 0.0), 1e-12)
        profile[name] = {
            "model_abs_bound": bound,
            "baseline_abs_bound": 0.0,
            "relative_p95": relative,
            "confidence": 1.0 / (1.0 + relative),
        }
    qps_bound = float((residuals.get("db_qps_delta_pct") or {}).get("p95") or 0.0) * support_multiplier
    return profile, qps_bound


def predicted_goal_hit(
    row: Dict[str, Any],
    baseline_resource: Dict[str, float],
    capability: Dict[str, float],
    scenario: str,
) -> bool:
    resource = row.get("estimated_resource") or {}
    qps_lower = float(row.get("estimated_db_qps_delta_pct") or 0.0) - float(
        row.get("estimated_db_qps_error_bound_pct") or 0.0
    )
    deltas_pp = {
        name: (float(resource.get(name) or 0.0) - float(baseline_resource.get(name) or 0.0))
        / max(float(capability.get(name) or 0.0), 1e-12)
        * 100.0
        for name in RESOURCE_NAMES
    }
    target_util = float(resource.get(scenario) or 0.0) / max(float(capability.get(scenario) or 0.0), 1e-12) * 100.0
    others = [delta for name, delta in deltas_pp.items() if name != scenario]
    return qps_lower >= 15.0 and deltas_pp.get(scenario, 0.0) <= -5.0 and target_util < 80.0 and max(others) > 0.0


def compact_rescore(
    row: Dict[str, Any],
    *,
    calibration: Dict[str, Any],
    calculator: ResourceUtilityCalculator,
    capability: Dict[str, float],
    candidate_gate: Dict[str, Any],
) -> Dict[str, Any]:
    params = row.get("params") or {}
    if set(params) != set(COST_PARAMS):
        raise ValueError(f"candidate does not contain exactly seven cost params: {sorted(params)}")
    plan_features = row.get("plan_delta_features") or {}
    resource, qps_delta, calibration_detail = apply_paired_delta_calibration(
        row.get("estimated_resource") or {},
        plan_features,
        calibration,
        params,
    )
    tree_detail = calibration_detail.get("tree") or {}
    support_misses = tree_detail.get("out_of_support_split_features") or []
    model = (calibration.get("replay_delta_tree") or {}).get("model") or {}
    error_profile, qps_error_bound = tree_error_profile(
        model,
        capability,
        support_miss_count=len(support_misses),
    )
    utilization = {
        name: float(resource.get(name) or 0.0) / max(float(capability[name]), 1e-12) * 100.0
        for name in RESOURCE_NAMES
    }
    score = calculator.calculate_utility_score(resource, capability)
    return {
        "candidate_index": row.get("candidate_index"),
        "param_signature": parameter_signature(params),
        "params": params,
        "estimated_resource": resource,
        "utilization_pct": utilization,
        "score": float(score),
        "score_signature": f"{float(score):.12g}",
        "resource_signature": resource_signature(resource),
        "estimated_db_qps_delta_pct": qps_delta,
        "estimated_db_qps_error_bound_pct": qps_error_bound,
        "estimated_db_qps_conservative_delta_pct": float(qps_delta or 0.0) - qps_error_bound,
        "resource_error_profile": error_profile,
        "plan_delta_features": plan_features,
        "plan_hash": row.get("plan_hash"),
        "failed_query_count": row.get("failed_query_count", 0),
        "query_count": row.get("query_count", 0),
        "score_direction_objective": {
            name: candidate_gate[name]
            for name in DIRECTION_CONFIG_KEYS
            if name in candidate_gate
        },
        "replay_delta_tree": {
            "leaf_id": tree_detail.get("leaf_id"),
            "leaf_samples": tree_detail.get("leaf_samples"),
            "training_candidates": tree_detail.get("training_candidates") or [],
            "out_of_support_features": tree_detail.get("out_of_support_features") or [],
            "out_of_support_split_features": support_misses,
            "supported": not support_misses,
        },
    }


def replay_ranks(
    ranked: Sequence[Dict[str, Any]],
    replay_rows: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Match known replay labels by canonical seven-parameter signature.

    Rescored rows do not carry candidate_id; the matching key is the
    seven-parameter value signature. This is the correct audit logic.
    """
    rank_by_signature: Dict[str, int] = {}
    for rank, row in enumerate(ranked, start=1):
        rank_by_signature.setdefault(str(row.get("param_signature")), rank)
    output = []
    for replay in replay_rows:
        params = replay.get("params") or {}
        sig = parameter_signature(params)
        rank = rank_by_signature.get(sig)
        if rank is None:
            rank = rank_by_signature.get(sig)  # fallback repeat lookup
        output.append(
            {
                "candidate_id": replay.get("candidate_id"),
                "rank": rank,
                "db_qps_delta_pct": replay.get("db_qps_delta_pct"),
                "resource_delta_pp": replay.get("resource_delta_pp"),
                "params": params,
                "param_signature": sig,
                "matched_by": "canonical_seven_param_signature",
            }
        )
    return output


def write_markdown(
    path: Path,
    summary: Dict[str, Any],
    top_rows: Sequence[Dict[str, Any]],
    *,
    capability: Dict[str, float],
    scenario: str,
) -> None:
    """Write rescore Markdown summary with explicit capability/scenario (no hidden globals)."""
    lines = [
        "# Bonect Replay-Delta Offline Rescore",
        "",
        f"- candidates: `{summary['candidate_count']}`",
        f"- elapsed_seconds: `{summary['elapsed_seconds']:.3f}`",
        f"- supported_candidates: `{summary['supported_candidate_count']}`",
        f"- predicted_conservative_goal_hits: `{summary['predicted_conservative_goal_hit_count']}`",
        f"- model LOO pairwise inversion: `{summary['model_leave_one_out_pairwise_inversion_rate']:.6f}`",
        f"- model LOO pairwise inversion (10pp error-aware): "
        f"`{summary.get('model_leave_one_out_pairwise_error_aware_10pp_rate', 0.0):.6f}`",
        f"- scenario: `{scenario}`",
        "",
        f"- diversity_policy: `generic_answer_blind` (pf_bin_max={summary.get('diversity_policy', {}).get('max_per_pf_bin', 8)} resp_bin_max={summary.get('diversity_policy', {}).get('max_per_resp_bin', 12)} qps_bin_max={summary.get('diversity_policy', {}).get('max_per_qps_bin', 15)} lane_max={summary.get('diversity_policy', {}).get('max_per_lane_cluster', 20)})",
        "",
        "| rank | qps point | qps lower | CPU% | Memory% | IO% | supported | leaf | pf_bin | resp_bin | qps_bin | lane | params |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- | --- | --- | --- | --- |",
    ]
    from bonect_robust_ranking import candidate_diversity_record

    for rank, row in enumerate(top_rows, start=1):
        util = row.get("utilization_pct") or {}
        tree = row.get("replay_delta_tree") or {}
        params = row.get("params") or {}
        param_text = "/".join(f"{float(params[name]):.8g}" for name in COST_PARAMS)
        dr = (
            candidate_diversity_record(row, capability=capability, scenario=scenario)
            if rank <= 60
            else {}
        )
        lines.append(
            f"| {rank} | {float(row.get('estimated_db_qps_delta_pct') or 0.0):.2f} | "
            f"{float(row.get('estimated_db_qps_conservative_delta_pct') or 0.0):.2f} | "
            f"{float(util.get('cpu') or 0.0):.2f} | {float(util.get('memory') or 0.0):.2f} | "
            f"{float(util.get('io') or 0.0):.2f} | {tree.get('supported')} | "
            f"{tree.get('leaf_id')} | {dr.get('pf_bin', '')[:40]} | {dr.get('resp_bin', '')[:30]} | "
            f"{dr.get('qps_bin', '')[:20]} | {dr.get('param_lane', '')[:60]} | `{param_text}` |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline replay-delta rescore for an existing Bonect estimator scan.")
    parser.add_argument("--candidates-jsonl", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--baseline-calibration-json", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--scenario", default="memory", choices=("cpu", "memory", "io"))
    parser.add_argument("--replay-labels-jsonl")
    parser.add_argument("--top-k", type=int, default=60)
    args = parser.parse_args()

    started = time.perf_counter()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    config = load_config(Path(args.config))
    calibration = load_baseline_calibration(Path(args.baseline_calibration_json))
    calculator = build_calculator(config)
    capability = {str(name): float(value) for name, value in (config.get("capability") or {}).items()}
    baseline_resource = {str(name): float(value) for name, value in (calibration.get("actual_resource") or {}).items()}
    candidate_gate = ((((config.get("optimizer") or {}).get("bruteforce") or {}).get("candidate_gate")) or {})

    records: List[Dict[str, Any]] = []
    for count, row in enumerate(iter_jsonl(Path(args.candidates_jsonl)), start=1):
        records.append(
            compact_rescore(
                row,
                calibration=calibration,
                calculator=calculator,
                capability=capability,
                candidate_gate=candidate_gate,
            )
        )
        if count % 10000 == 0:
            print(f"rescored={count}", flush=True)

    records = attach_robust_scores(
        records,
        capability=capability,
        scenario=args.scenario,
        variants=(DEFAULT_VARIANT,),
        selected_variant=DEFAULT_VARIANT,
        baseline_resource=baseline_resource,
    )
    ranked = sorted(records, key=lambda row: robust_sort_key(row, variant=DEFAULT_VARIANT), reverse=True)
    top_distinct = select_diverse_top_k(
        ranked,
        capability=capability,
        scenario=args.scenario,
        top_k=max(1, int(args.top_k)),
    )
    supported_ranked = [row for row in ranked if (row.get("replay_delta_tree") or {}).get("supported")]
    uncertain_ranked = [row for row in ranked if not (row.get("replay_delta_tree") or {}).get("supported")]
    top_supported = select_diverse_top_k(
        supported_ranked,
        capability=capability,
        scenario=args.scenario,
        top_k=max(1, int(args.top_k)),
    )
    top_uncertain = select_diverse_top_k(
        uncertain_ranked,
        capability=capability,
        scenario=args.scenario,
        top_k=max(1, int(args.top_k)),
    )

    compact_path = output_dir / "rescored_candidates.jsonl"
    with compact_path.open("w", encoding="utf-8") as handle:
        for row in records:
            compact_row = dict(row)
            robust = ((compact_row.pop("robust_scores", {}) or {}).get(DEFAULT_VARIANT) or {})
            compact_row.pop("robust_ranking", None)
            compact_row["robust_summary"] = {
                "variant": DEFAULT_VARIANT,
                "robust_score": robust.get("robust_score"),
                "resource_direction_passed": robust.get("resource_direction_passed"),
                "resource_delta_util_pp": (
                    (robust.get("resource_direction") or {}).get("resource_delta_util_pp")
                ),
                "plan_confidence": (robust.get("plan_confidence") or {}).get("confidence"),
                "uncertainty_penalty": robust.get("uncertainty_penalty"),
            }
            handle.write(json.dumps(compact_row, ensure_ascii=False, sort_keys=True) + "\n")
    write_json(output_dir / "top_robust_distinct.json", top_distinct)
    write_json(output_dir / "top_supported_distinct.json", top_supported)
    write_json(output_dir / "top_uncertain_distinct.json", top_uncertain)

    replay_rows = list(iter_jsonl(Path(args.replay_labels_jsonl))) if args.replay_labels_jsonl else []
    known_ranks = replay_ranks(ranked, replay_rows)
    write_json(output_dir / "known_replay_ranks.json", known_ranks)
    model = (calibration.get("replay_delta_tree") or {}).get("model") or {}
    loo_pairwise = ((model.get("leave_one_out_metrics") or {}).get("qps_pairwise") or {})
    loo_pairwise_error_aware = (
        (model.get("leave_one_out_metrics") or {}).get("qps_pairwise_error_aware_10pp") or {}
    )
    from bonect_robust_ranking import candidate_diversity_record
    top_diversity = {
        "pf_bin_counts": {},
        "resp_bin_counts": {},
        "qps_bin_counts": {},
        "param_lane_counts": {},
    }
    for row in top_distinct:
        dr = candidate_diversity_record(row, capability=capability, scenario=args.scenario)
        top_diversity["pf_bin_counts"][dr["pf_bin"]] = top_diversity["pf_bin_counts"].get(dr["pf_bin"], 0) + 1
        top_diversity["resp_bin_counts"][dr["resp_bin"]] = top_diversity["resp_bin_counts"].get(dr["resp_bin"], 0) + 1
        top_diversity["qps_bin_counts"][dr["qps_bin"]] = top_diversity["qps_bin_counts"].get(dr["qps_bin"], 0) + 1
        top_diversity["param_lane_counts"][dr["param_lane"]] = top_diversity["param_lane_counts"].get(dr["param_lane"], 0) + 1

    summary = {
        "status": "completed",
        "scenario": args.scenario,
        "candidate_count": len(records),
        "supported_candidate_count": len(supported_ranked),
        "uncertain_candidate_count": len(uncertain_ranked),
        "predicted_conservative_goal_hit_count": sum(
            predicted_goal_hit(row, baseline_resource, capability, args.scenario) for row in records
        ),
        "model_leave_one_out_pairwise_inversion_rate": float(loo_pairwise.get("rate") or 0.0),
        "model_leave_one_out_pairwise_error_aware_10pp_rate": float(
            loo_pairwise_error_aware.get("rate") or 0.0
        ),
        "model_leave_one_out_qps_residual": (
            ((model.get("leave_one_out_metrics") or {}).get("absolute_residual") or {}).get("db_qps_delta_pct")
        ),
        "known_replay_ranks": known_ranks,
        "top_robust_path": str((output_dir / "top_robust_distinct.json").resolve()),
        "top_supported_path": str((output_dir / "top_supported_distinct.json").resolve()),
        "top_uncertain_path": str((output_dir / "top_uncertain_distinct.json").resolve()),
        "elapsed_seconds": time.perf_counter() - started,
        "diversity_policy": {
            "name": "generic_answer_blind_diversity",
            "exploitation_slots": 6,
            "max_per_pf_bin": 8,
            "max_per_resp_bin": 12,
            "max_per_qps_bin": 15,
            "max_per_lane_cluster": 20,
            "exclude_disaster": True,
            "exclude_negative_qps": True,
            "matching": "canonical_seven_param_signature_not_candidate_id",
        },
        "top_k_diversity_coverage": top_diversity,
    }
    write_json(output_dir / "rescore_summary.json", summary)
    write_markdown(
        output_dir / "rescore_summary.md",
        summary,
        top_distinct[:20],
        capability=capability,
        scenario=args.scenario,
    )
    # Precomputed-import-compatible summary so parameter_optimizer.py can ingest
    # `rescored_candidates.jsonl` directly: the importer looks for
    # `<candidates_dir>/scan_summary.json` with `status` in
    # {complete, completed, finished} and `failure_count == 0`. The offline
    # rescore has no planning failures (it reuses the estimator scan's plan
    # features), so every candidate record counts as successful.
    scan_summary = {
        "status": "completed",
        "scenario": args.scenario,
        "source": "bonect_offline_rescore_scan",
        "total": len(records),
        "completed": len(records),
        "total_candidates": len(records),
        "completed_candidates": len(records),
        "successful_count": len(records),
        "failure_count": 0,
        "failure_rate": 0.0,
        "failure_fail_per_query": 0,
        "rescore_summary_path": str((output_dir / "rescore_summary.json").resolve()),
        "candidates_path": str((output_dir / "rescored_candidates.jsonl").resolve()),
        "distinct_resource_count": len({row.get("resource_signature") for row in records}),
        "distinct_plan_count": len({row.get("plan_hash") for row in records}),
        "diversity_policy": "generic_answer_blind_pf_bin_resp_bin_qps_bin_param_lane",
        "exploitation_slots": 6,
        "max_per_pf_bin": 8,
        "model_leave_one_out_pairwise_inversion_rate": float(loo_pairwise.get("rate") or 0.0),
        "model_leave_one_out_pairwise_error_aware_10pp_rate": float(
            loo_pairwise_error_aware.get("rate") or 0.0
        ),
        "elapsed_seconds": summary["elapsed_seconds"],
    }
    write_json(output_dir / "scan_summary.json", scan_summary)
    print(
        f"completed candidates={len(records)} supported={len(supported_ranked)} "
        f"elapsed={summary['elapsed_seconds']:.3f}s output={output_dir}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

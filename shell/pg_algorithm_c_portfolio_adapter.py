"""Connect the resource-only plan portfolio to C's existing prediction audits."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import threading
from typing import Any, Dict

from algorithm_c_plan_portfolio import CONTRACT, configured_policy, identity, plan_id, search
from gradient_descent_optimizer import GradientDescentParameterOptimizer


def generate(
    controller: Any,
    round_index: int,
    *,
    plan_space_saturation_required: bool = False,
) -> list[Dict[str, Any]]:
    # Local import avoids a cycle while keeping the existing fingerprint,
    # support-domain and validity implementation authoritative.
    from shell import run_pg_algorithm_c_scene as base

    policy = configured_policy(controller.algorithm_cfg)
    if policy is None:
        raise ValueError("portfolio generator called without opt-in configuration")
    incumbent = dict(controller.state["incumbent_params"])
    reference = controller.state.get("incumbent_validation") or controller.state["canonical_baseline"]
    measured = {name: float(reference["resources"][name]) for name in ("cpu", "memory", "io")}
    resources = controller.inner_scoring_reference_resources(measured)
    queries, baseline_probe, predictor_config = controller.queries_and_probe(incumbent)
    scoring = copy.deepcopy(base.formal_inner_scoring_options(controller.target))
    scoring.pop("directional_scoring_contract_required", None)
    gradient = GradientDescentParameterOptimizer(
        resource_estimation_func=lambda *_: resources, capability=controller.capacity,
        verbose=False, **scoring,
    )
    utility = gradient.utility_calculator
    initial_score = utility.calculate_utility_score(resources, controller.capacity)
    directions, direction_audit = {}, {}
    for name in base.ACTIVE_PARAMS:
        audit: Dict[str, Any] = {}
        directions[name], _ = gradient._calculate_direction(
            name, controller.experience_table, resources, controller.capacity,
            audit_sink=audit, original_gradient_only=True,
        )
        direction_audit[name] = audit
    bounds = {name: (float(controller.ranges[name].min_value), float(controller.ranges[name].max_value))
              for name in base.ACTIVE_PARAMS}
    log_params = [name for name in base.ACTIVE_PARAMS if base.resolve_parameter_transform(name) == "log"]
    # No cross-round prediction cache: every call takes a fresh full workload
    # baseline probe. Each thread owns a DB probe/estimator connection context.
    thread_state = threading.local()
    support_excluded = []

    def evaluate(params):
        if not hasattr(thread_state, "estimator"):
            thread_state.estimator = base.RatioScaleEstimator(
                predictor_config, queries, baseline_probe, resources, incumbent,
                controller.experience_artifact["resource_prediction_calibration"],
            )
        predicted, _stats, metadata = thread_state.estimator(
            {"contract": "read_only_full_workload_plan_set"}, copy.deepcopy(params),
        )
        score = float(utility.calculate_utility_score(predicted, controller.capacity))
        active = [name for name in base.ACTIVE_PARAMS if float(params[name]) != float(incumbent[name])]
        deltas, move = {}, {}
        for name in base.ACTIVE_PARAMS:
            before, after = float(incumbent[name]), float(params[name])
            change = after - before
            move[name] = (math.log(after / before) if name in log_params else
                          change / max(bounds[name][1] - bounds[name][0], 1e-12))
            deltas[name] = {"before": before, "proposed_before_clip": after, "after_clip": after,
                            "absolute": change, "relative": change / before if before else None,
                            "clipped": False}
        parameter_id = hashlib.sha256(repr(identity(params)).encode()).hexdigest()[:16]
        row = base.attach_three_level_fingerprint({
            "subset_id": f"portfolio_{parameter_id}", "active_subset": active,
            "optimized_params": copy.deepcopy(params), "initial_params": copy.deepcopy(incumbent),
            "initial_resource": copy.deepcopy(resources), "final_resource": copy.deepcopy(predicted),
            "initial_score": initial_score, "final_score": score, "score_improvement": score - initial_score,
            "iterations": 0, "final_estimation_metadata": copy.deepcopy(metadata),
            "log_path": str(controller.log_path),
        }, controller.capacity)
        row.update({
            "candidate_origin": CONTRACT, "probe_kind": "resource_plan_portfolio",
            "used_for_gradient": False, "gradient_state_unchanged": True,
            "global_plan_coverage_rescue": True,
            "global_plan_coverage_contract": base.GLOBAL_PLAN_COVERAGE_CONTRACT,
            "dScore/dU": {"available": False, "reason": "portfolio_mixes_gradient_and_coverage_proposals"},
            "T": {"available": False, "reason": "experience_direction_audited_at_generation"},
            "confidence": {"value": min((metadata.get("plan_model_confidence") or {}).values() or [0.0]),
                           "classification": "portfolio_resource_prediction_confidence"},
            "g_j": move, "g_j_semantics": "proposal_displacement_not_claimed_gradient",
            "eta": 1.0, "eta_semantics": "normalized_box_proposal", "eta_applied_to_candidate": False,
            "delta_param": deltas, "candidate_score": score, "qps_used_for_inner_scoring": False,
            "external_algorithm_evidence_used": False,
            # Same-plan numeric alternatives remain distinct candidates. They
            # bypass plan-level reject pruning only in the explicit recovery
            # path after distinct-plan coverage has been exhausted.
            "numeric_refinement": True,
            "plan_space_saturation_required": plan_space_saturation_required,
            "numeric_refinement_reason": (
                "distinct_plan_coverage_exhausted_unseen_exact_parameter_plan_identity"
                if plan_space_saturation_required
                else "portfolio_retains_same_plan_numeric_representatives"
            ),
        })
        support = base.candidate_target_support_assessment(row, controller.target)
        if base.target_support_abstention_enabled(controller.experience_artifact, controller.target) and (
            not isinstance(support, dict) or support.get("supported") is not True
            or support.get("support_abstained") is True
        ):
            if isinstance(support, dict) and controller.allow_target_support_expansion_probe(row, support):
                row.update({"target_support_expansion_probe": True,
                            "target_support_prediction_used_for_candidate_score": False,
                            "target_support_expansion_real_replay_required": True,
                            "target_support_expansion_qps_used": False,
                            "target_support_expansion_distance_ratio": support.get("distance_to_threshold_ratio")})
            else:
                support_excluded.append({"id": parameter_id, "params": params, "support": support})
                return None
        return row

    memory = controller.state.get("plan_portfolio") or {}
    admission = controller.experience_artifact.get("formal_admission") or {}
    target_abstained = admission.get("target_prediction_abstained") is True
    first_miss = int(controller.state.get("consecutive_missed_outer_rounds") or 0) > 0
    context = {
        "predictor_config": predictor_config, "queries": queries,
        "baseline_probe": baseline_probe, "baseline_resources": resources,
        "incumbent": incumbent, "bounds": bounds,
        "model": controller.experience_artifact["resource_prediction_calibration"],
    }
    context_id = hashlib.sha256(json.dumps(context, sort_keys=True, default=str).encode()).hexdigest()
    refinement_audits = []

    def refinement_direction(row):
        result, resource_audit = {}, {}
        for name in base.ACTIVE_PARAMS:
            audit = {}
            result[name], _ = gradient._calculate_direction(
                name, controller.experience_table, row["final_resource"], controller.capacity,
                audit_sink=audit, original_gradient_only=True,
            )
            resource_audit[name] = audit
        refinement_audits.append({"params": row["optimized_params"], "directions": resource_audit})
        return result

    rankings, audit = search(
        bounds=bounds, log_params=log_params, incumbent=incumbent, directions=directions,
        evaluate=evaluate, policy=policy, first_miss=first_miss,
        cursor=int(memory.get("cursor") or 1), seen_plans=memory.get("seen_plans") or [],
        prior_params=memory.get("prior_params") or [],
        refinement_direction=refinement_direction,
        target_abstained=target_abstained,
    )
    audit.update({"context_sha256": context_id, "round_index": round_index,
                  "experience_direction_audit": direction_audit,
                  "refinement_direction_audits": refinement_audits,
                  "target_support_excluded": sorted(support_excluded, key=lambda item: item["id"]),
                  "within_current_outer_interval_only": True})
    controller.log("algorithm_c_plan_portfolio_generation", **audit,
                   candidate_rankings=rankings)
    if audit["errors"] and len(rankings) < base.BASE_TOP_K:
        raise RuntimeError("portfolio offline probe failures cannot count as an effective miss")
    if rankings:
        controller.validate_top5_audit_records(rankings[:base.BASE_TOP_K])
    controller.state["plan_portfolio"] = {
        "contract": CONTRACT, "cursor": audit["next_cursor"], "last_context_sha256": context_id,
        "target_prediction_abstained": target_abstained,
        "selection_seen_plans": list(memory.get("seen_plans") or []),
        "seen_plans": sorted(set(memory.get("seen_plans") or []) | {plan_id(row) for row in rankings}),
        "prior_params": (memory.get("prior_params") or []) + [row["optimized_params"] for row in rankings],
        "last_audit": audit,
    }
    controller.save_state()
    return rankings

"""Module."""


from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

from adaptive_replay_controller import (
    AdaptiveReplayController,
    AdaptiveReplayDecision,
    BatchInputs,
    BatchDecision,
    LaneQuotaConfig,
    ReplayLane,
    assign_lanes,
    record_fallback_best,
    select_batch_candidates,
)
from common_outer_loop import cap_real_replay_top_k


@dataclass
class PerBatchReplayResult:
    """PerBatchReplayResult."""

    final_decision: str
    """`AdaptiveReplayDecision.value`"""

    stop_reason: str
    """Reason"""

    batches_run: int
    """>0  replay"""

    expansions_used: int
    """ EXPAND_ONE_BATCH """

    replayed_indices: List[int] = field(default_factory=list)
    """ `candidates`  index"""

    replayed_evaluations: List[Dict[str, Any]] = field(default_factory=list)
    """ evaluation record"""

    lane_counts: Dict[str, int] = field(default_factory=dict)
    """ lane cumulative this round"""

    decisions: List[Dict[str, Any]] = field(default_factory=list)
    """ `BatchDecision.to_jsonl_record()`"""

    calibration_summaries: List[Dict[str, Any]] = field(default_factory=list)
    """ delta """

    fallback_best_record: Optional[Dict[str, Any]] = None
    """cand19  DB-QPS fallback best permanent +  explainable"""

    global_best_record: Optional[Dict[str, Any]] = None
    """DB-QPS  explainable """

    remaining_upper_bound_beats_best: bool = False
    """ `decide_batch`  `I5`"""

    safety_model_gate_passed: bool = True
    """ run  `I1` safety/model hard gate """


def run_adaptive_replay_batches(
    *,
    candidates: List[Dict[str, Any]],
    replay_callback: Callable[[Dict[str, Any]], Dict[str, Any]],
    replay_batch_callback: Optional[
        Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]
    ] = None,
    summary_callback: Callable[[List[Dict[str, Any]]], Dict[str, Any]],
    target_resource: str,
    baseline_resource: Dict[str, float],
    capability: Dict[str, float],
    mandatory_anchor_indices: Sequence[int] = (),
    forced_anchor_indices: Sequence[int] = (),
    diagnostic_candidates: Sequence[Dict[str, Any]] = (),
    reserve_candidates: Sequence[Dict[str, Any]] = (),
    safety_model_gate_passed: bool = True,
    io_source_valid: bool = True,
    io_stable_sample_count_positive: bool = True,
    initial_fallback_best_record: Optional[Dict[str, Any]] = None,
    quota: Optional[LaneQuotaConfig] = None,
    max_budget: Optional[int] = None,
    batch_size: Optional[int] = None,
    min_evidence: Optional[int] = None,
    max_expansions: Optional[int] = None,
    decide_batch_spy: Optional[Callable[[BatchInputs], BatchDecision]] = None,
    decision_writer: Optional[Callable[[Dict[str, Any]], None]] = None,
    remaining_upper_bound_evaluator: Optional[
        Callable[[List[Dict[str, Any]], Optional[Dict[str, Any]]], bool]
    ] = None,
    post_batch_callback: Optional[
        Callable[
            [List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]],
            Dict[str, Any],
        ]
    ] = None,
) -> PerBatchReplayResult:
    """run adaptive replay batches."""


    quota = copy.deepcopy(quota or LaneQuotaConfig())
    max_budget = cap_real_replay_top_k(
        max_budget if max_budget is not None else quota.max_budget
    )
    bs = min(
        max_budget,
        max(1, int(batch_size if batch_size is not None else quota.batch_size)),
    )
    min_evidence = (
        min_evidence if min_evidence is not None else quota.min_evidence
    )
    min_evidence = min(max_budget, max(0, int(min_evidence)))
    # Candidate-pool expansion may continue in read-only estimation, but no
    # adaptive decision may expand real replay beyond the global five-point cap.
    max_expansions = 0
    # The controller and lane selector must observe the effective runtime
    # schedule. Leaving the dataclass at its defaults makes a caller-provided
    # 6/12-candidate budget look like 24 to decide_batch().
    quota.max_budget = int(max_budget)
    quota.min_evidence = int(min_evidence)
    quota.batch_size = int(bs)
    quota.max_expansions = int(max_expansions)


    working_candidates: List[Dict[str, Any]] = list(candidates)
    working_diagnostic_pool: List[Dict[str, Any]] = list(diagnostic_candidates)
    working_reserve_pool: List[Dict[str, Any]] = list(reserve_candidates)

    replayed_indices: List[int] = []
    replayed_set: Set[int] = set()
    replayed_evaluations: List[Dict[str, Any]] = []
    decisions: List[Dict[str, Any]] = []
    calibration_summaries: List[Dict[str, Any]] = []
    expansions_used = 0
    fallback_best_record: Optional[Dict[str, Any]] = (
        copy.deepcopy(initial_fallback_best_record)
        if initial_fallback_best_record is not None
        else None
    )
    global_best_record: Optional[Dict[str, Any]] = None

    controller = AdaptiveReplayController(
        quota=quota, target_resource=target_resource
    )
    decide_fn = (
        decide_batch_spy if decide_batch_spy is not None else controller.decide_batch
    )

    final_decision = ""
    stop_reason = ""
    final_lane_counts: Dict[str, int] = {}
    final_i5: bool = False
    batch_index = 0
    batches_run = 0

    def _assign_lanes() -> Dict[str, List[int]]:
        return assign_lanes(
            working_candidates,
            target_resource=target_resource,
            baseline_resource=baseline_resource,
            capability=capability,
            mandatory_anchor_indices=mandatory_anchor_indices,
            forced_anchor_indices=forced_anchor_indices,
        )

    def _best_eval() -> Optional[Dict[str, Any]]:
        if not replayed_evaluations:
            return None
        best = None
        best_delta = float("-inf")
        for ev in replayed_evaluations:
            status = ev.get("candidate_primary_target_status") or {}
            try:
                v = float(status.get("db_qps_delta_pct") or 0.0)
            except (TypeError, ValueError):
                v = 0.0
            if v > best_delta:
                best_delta = v
                best = ev
        return best

    def _remaining_lane_pool() -> Dict[str, List[int]]:
        lanes_all = _assign_lanes()
        remaining = {
            lane: [i for i in idxs if i not in replayed_set]
            for lane, idxs in lanes_all.items()
        }
        for lane, indices in remaining.items():
            if lane == "mandatory_anchor":
                continue
            indices.sort(
                key=lambda index: float(
                    working_candidates[index].get(
                        "_adaptive_replay_rank_score",
                        -float(index),
                    )
                    or 0.0
                ),
                reverse=True,
            )
        return remaining

    def _update_fallback_and_global(evaluation: Dict[str, Any]) -> None:
        """ update fallback and global."""


        nonlocal fallback_best_record, global_best_record
        status = evaluation.get("candidate_primary_target_status") or {}
        db_delta_raw = status.get("db_qps_delta_pct")
        try:
            db_delta = float(db_delta_raw) if db_delta_raw is not None else None
        except (TypeError, ValueError):
            db_delta = None
        target_delta_raw = status.get("bottleneck_resource_delta_pp")
        try:
            target_delta = (
                float(target_delta_raw)
                if target_delta_raw is not None
                else None
            )
        except (TypeError, ValueError):
            target_delta = None
        explainable_hit = bool(
            evaluation.get("phase2_long_term_goal_met")
            or evaluation.get("target_direction_ok")
        )
        params = evaluation.get("params") or {}

        if db_delta is not None and db_delta > 0 and not explainable_hit:
            cur = fallback_best_record
            cur_delta = (
                float(cur.get("db_qps_delta_pct") or float("-inf"))
                if cur
                else float("-inf")
            )
            if db_delta > cur_delta:
                fallback_best_record = record_fallback_best(
                    candidate_index=int(evaluation.get("_candidate_index", -1)),
                    db_qps_delta_pct=db_delta,
                    params=dict(params),
                    plan_change_count=int(evaluation.get("plan_change_count") or 0),
                    target_resource=target_resource,
                    target_resource_delta_pp=target_delta,
                )

        if db_delta is not None:
            cur = global_best_record
            cur_delta = (
                float(cur.get("db_qps_delta_pct") or float("-inf"))
                if cur
                else float("-inf")
            )
            if db_delta > cur_delta:
                global_best_record = {
                    "candidate_index": int(
                        evaluation.get("_candidate_index", -1)
                    ),
                    "db_qps_delta_pct": db_delta,
                    "target_resource_delta_pp": target_delta,
                    "explainable_hit": explainable_hit,
                    "params": dict(params),
                }

    while True:

        available_pool = _remaining_lane_pool()
        selected = select_batch_candidates(
            available_pool,
            quota,
            batch_size=bs,
            reserve_enabled=expansions_used > 0,
        )
        batch_pairs: List[tuple] = []
        for lane_name, idxs in selected.items():
            for idx in idxs:
                if idx in replayed_set:
                    continue
                batch_pairs.append((lane_name, idx))
        batch_pairs = batch_pairs[:bs]

        if (
            not batch_pairs
            and len(replayed_evaluations) < min_evidence
            and working_diagnostic_pool
        ):
            needed = max(1, min_evidence - len(replayed_evaluations))
            take = min(bs, needed, len(working_diagnostic_pool))
            for raw_cand in working_diagnostic_pool[:take]:
                cand = copy.deepcopy(raw_cand)
                cand["_adaptive_replay_forced_lane"] = "diagnostic"
                if cand not in working_candidates:
                    working_candidates.append(cand)
            working_diagnostic_pool = working_diagnostic_pool[take:]
            continue

        if (
            not batch_pairs
            and len(replayed_evaluations) < min_evidence
            and working_reserve_pool
            and expansions_used < max_expansions
        ):
            needed = max(1, min_evidence - len(replayed_evaluations))
            take = min(bs, needed, len(working_reserve_pool))
            for raw_cand in working_reserve_pool[:take]:
                cand = copy.deepcopy(raw_cand)
                cand["_adaptive_replay_forced_lane"] = "reserve"
                if cand not in working_candidates:
                    working_candidates.append(cand)
            working_reserve_pool = working_reserve_pool[take:]
            expansions_used += 1
            continue

        if not batch_pairs:
            final_decision = AdaptiveReplayDecision.STOP_EARLY_KEEP_BEST.value
            stop_reason = "no_replayable_candidates_remaining"
            break

        if len(replayed_evaluations) + len(batch_pairs) > max_budget:
            allowed = max(
                0, max_budget - len(replayed_evaluations)
            )
            if allowed == 0:
                final_decision = (
                    AdaptiveReplayDecision.STOP_EARLY_KEEP_BEST.value
                )
                stop_reason = "max_budget_reached"
                break
            batch_pairs = batch_pairs[:allowed]


        batch_evaluations: Dict[int, Dict[str, Any]] = {}
        if replay_batch_callback is not None:
            batch_records: List[Dict[str, Any]] = []
            for lane_name, idx in batch_pairs:
                record = copy.deepcopy(working_candidates[idx])
                record["_candidate_index"] = idx
                record["_lane"] = lane_name
                batch_records.append(record)
            returned = replay_batch_callback(batch_records)
            if not isinstance(returned, list):
                raise TypeError("replay_batch_callback must return a list")
            for position, evaluation in enumerate(returned):
                if not isinstance(evaluation, dict):
                    continue
                candidate_index = evaluation.get("_candidate_index")
                if not isinstance(candidate_index, int):
                    if position >= len(batch_records):
                        continue
                    candidate_index = int(
                        batch_records[position]["_candidate_index"]
                    )
                if candidate_index in batch_evaluations:
                    raise ValueError(
                        "replay_batch_callback returned duplicate "
                        f"_candidate_index={candidate_index}"
                    )
                batch_evaluations[candidate_index] = evaluation

        for lane_name, idx in batch_pairs:
            cand_record = working_candidates[idx]
            evaluation = (
                batch_evaluations.get(idx)
                if replay_batch_callback is not None
                else replay_callback(cand_record)
            )
            if not isinstance(evaluation, dict):
                continue
            evaluation = dict(evaluation)
            evaluation["_candidate_index"] = idx
            evaluation["_lane"] = lane_name
            replayed_indices.append(idx)
            replayed_set.add(idx)
            replayed_evaluations.append(evaluation)
            _update_fallback_and_global(evaluation)

        batches_run += 1


        summary = summary_callback(list(replayed_evaluations)) or {}
        if post_batch_callback is not None:
            try:
                calibration_summary = post_batch_callback(
                    working_candidates,
                    list(replayed_evaluations),
                    dict(summary),
                ) or {}
            except Exception as exc:
                calibration_summary = {
                    "available": False,
                    "training_gate_passed": False,
                    "delta_calibration_requires_expansion": True,
                    "reason": "online_delta_calibration_failed",
                    "error": repr(exc),
                }
            if calibration_summary:
                calibration_summaries.append(copy.deepcopy(calibration_summary))
                recalibrated_summary = summary_callback(
                    list(replayed_evaluations)
                ) or {}
                summary = {**recalibrated_summary, **calibration_summary}


        lane_counts: Dict[str, int] = {
            lane: 0 for lane in available_pool
        }
        for ev in replayed_evaluations:
            ln = ev.get("_lane")
            if ln and ln in lane_counts:
                lane_counts[ln] += 1


        i3: Optional[float] = None
        for ev in replayed_evaluations:
            status = ev.get("candidate_primary_target_status") or {}
            v_raw = status.get("db_qps_delta_pct")
            try:
                if v_raw is None:
                    continue
                v = float(v_raw)
            except (TypeError, ValueError):
                continue
            if i3 is None or v > i3:
                i3 = v

        # I7: cumulative target-down evidence count
        i7 = 0
        for ev in replayed_evaluations:
            status = ev.get("candidate_primary_target_status") or {}
            bdpp = status.get("bottleneck_resource_delta_pp")
            try:
                if bdpp is None:
                    continue
                if float(bdpp) < 0.0:
                    i7 += 1
            except (TypeError, ValueError):
                continue

        # I5: remaining upper bound beats best
        calibration_requires_expansion = bool(
            summary.get("delta_calibration_requires_expansion", False)
        )
        if remaining_upper_bound_evaluator is not None:
            remaining_cands = [
                working_candidates[i]
                for lane_idx_list in _remaining_lane_pool().values()
                for i in lane_idx_list
                if i not in replayed_set
            ]
            i5 = bool(
                remaining_upper_bound_evaluator(remaining_cands, _best_eval())
            )
        else:
            remaining_indices = [
                index
                for indices in _remaining_lane_pool().values()
                for index in indices
                if index not in replayed_set
            ]
            calibrated_upper_bound_beats_best = False
            if summary.get("available") and remaining_indices:
                uncertainty = (
                    summary.get("loo_abs_error_p90") or {}
                ).get("db_qps_delta_pct", 0.0)
                try:
                    uncertainty_value = max(0.0, float(uncertainty or 0.0))
                except (TypeError, ValueError):
                    uncertainty_value = 0.0
                predicted_upper_bounds: List[float] = []
                for index in remaining_indices:
                    candidate = working_candidates[index]
                    predicted = candidate.get("predicted_db_qps_delta_pct")
                    try:
                        if predicted is not None:
                            predicted_upper_bounds.append(
                                float(predicted) + uncertainty_value
                            )
                    except (TypeError, ValueError):
                        continue
                if predicted_upper_bounds:
                    calibrated_upper_bound_beats_best = bool(
                        i3 is None or max(predicted_upper_bounds) > i3 + 0.5
                    )
            i5 = bool(
                (
                    len(replayed_evaluations) < min_evidence
                    or (
                        calibration_requires_expansion
                        and len(replayed_evaluations) < max_budget
                    )
                    or (
                        calibrated_upper_bound_beats_best
                        and len(replayed_evaluations) < max_budget
                    )
                )
                and bool(remaining_indices)
            )


        inputs = BatchInputs(
            safety_model_gate_passed=safety_model_gate_passed,
            io_source_valid=io_source_valid,
            io_stable_sample_count_positive=io_stable_sample_count_positive,
            explainable_conservative_pass_count=int(
                summary.get("explainable_lane_hit_count", 0)
            ),
            fallback_best_db_qps_delta_pct=i3,
            pairwise_inversion_rate=float(
                summary.get("pairwise_inversion_rate") or 0.0
            ),
            remaining_upper_bound_beats_best=i5,
            ranking_stable=bool(
                summary.get("first_stage_ranking_validation_pass", False)
            ),
            cumulative_target_down_evidence_count=i7,
            batch_index=batch_index,
            batches_so_far=batches_run,
            replayed_count=len(replayed_evaluations),
            expansions_used=expansions_used,
            lane_counts=dict(lane_counts),
        )
        # Always invoke the controller once per completed real-replay batch so
        # its inputs remain auditable and the next decision observes every
        # batch. Minimum-evidence and calibration guards may conservatively
        # override this proposed decision below, but must not bypass the call.
        controller_decision = decide_fn(inputs)
        remaining_replayable_count = sum(
            len(indices) for indices in _remaining_lane_pool().values()
        )
        if (
            len(replayed_evaluations) < min_evidence
            and safety_model_gate_passed
            and io_source_valid
            and io_stable_sample_count_positive
        ):
            if remaining_replayable_count == 0 and working_diagnostic_pool:
                needed = max(1, min_evidence - len(replayed_evaluations))
                expansion_delta = min(bs, needed, len(working_diagnostic_pool))
                decision = BatchDecision(
                    decision=AdaptiveReplayDecision.EXPAND_DIAGNOSTIC,
                    reason=(
                        " lane  delta "
                        f"{len(replayed_evaluations)}/{min_evidence} "
                        f"{expansion_delta}  diagnostic "
                    ),
                    inputs_snapshot=AdaptiveReplayController._snapshot_inputs(inputs),
                    remaining_upper_bound_beats_best=True,
                    ranking_stable=False,
                    pairwise_inversion_rate=inputs.pairwise_inversion_rate,
                    lane_counts=dict(inputs.lane_counts),
                    expansion_delta=expansion_delta,
                )
            elif (
                remaining_replayable_count == 0
                and working_reserve_pool
                and expansions_used < max_expansions
            ):
                needed = max(1, min_evidence - len(replayed_evaluations))
                expansion_delta = min(bs, needed, len(working_reserve_pool))
                decision = BatchDecision(
                    decision=AdaptiveReplayDecision.EXPAND_ONE_BATCH,
                    reason=(
                        " diagnostic lane "
                        f"{len(replayed_evaluations)}/{min_evidence} reserve "
                        f" {expansion_delta} "
                    ),
                    inputs_snapshot=AdaptiveReplayController._snapshot_inputs(inputs),
                    remaining_upper_bound_beats_best=True,
                    ranking_stable=False,
                    pairwise_inversion_rate=inputs.pairwise_inversion_rate,
                    lane_counts=dict(inputs.lane_counts),
                    expansion_delta=expansion_delta,
                )
            elif remaining_replayable_count == 0:
                decision = BatchDecision(
                    decision=AdaptiveReplayDecision.STOP_FIX_RANKING,
                    reason=(
                        ""
                        f"{len(replayed_evaluations)}/{min_evidence}"
                        " diagnostic/reserve "
                    ),
                    inputs_snapshot=AdaptiveReplayController._snapshot_inputs(inputs),
                    remaining_upper_bound_beats_best=False,
                    ranking_stable=False,
                    pairwise_inversion_rate=inputs.pairwise_inversion_rate,
                    lane_counts=dict(inputs.lane_counts),
                )
            else:
                decision = BatchDecision(
                    decision=AdaptiveReplayDecision.CONTINUE_NEXT_BATCH,
                    reason=(
                        " delta "
                        f"{len(replayed_evaluations)}/{min_evidence}"
                    ),
                    inputs_snapshot=AdaptiveReplayController._snapshot_inputs(inputs),
                    remaining_upper_bound_beats_best=True,
                    ranking_stable=False,
                    pairwise_inversion_rate=inputs.pairwise_inversion_rate,
                    lane_counts=dict(inputs.lane_counts),
                )
        elif calibration_requires_expansion and len(replayed_evaluations) < max_budget:
            decision = BatchDecision(
                decision=AdaptiveReplayDecision.CONTINUE_NEXT_BATCH,
                reason=(
                    " delta  12->18->24 "
                    f"{len(replayed_evaluations)}/{max_budget}"
                ),
                inputs_snapshot=AdaptiveReplayController._snapshot_inputs(inputs),
                remaining_upper_bound_beats_best=True,
                ranking_stable=False,
                pairwise_inversion_rate=inputs.pairwise_inversion_rate,
                lane_counts=dict(inputs.lane_counts),
            )
        elif calibration_requires_expansion and len(replayed_evaluations) >= max_budget:
            decision = BatchDecision(
                decision=AdaptiveReplayDecision.STOP_FIX_RANKING,
                reason=(
                    "24  delta "
                    ""
                ),
                inputs_snapshot=AdaptiveReplayController._snapshot_inputs(inputs),
                remaining_upper_bound_beats_best=False,
                ranking_stable=False,
                pairwise_inversion_rate=inputs.pairwise_inversion_rate,
                lane_counts=dict(inputs.lane_counts),
            )
        else:
            decision = controller_decision
        if decision is None or not hasattr(decision, "to_jsonl_record"):

            final_decision = "compute_error"
            stop_reason = (
                f"decide_fn returned {type(decision).__name__}; "
                "expected BatchDecision"
            )
            break

        decision_record = decision.to_jsonl_record()
        decision_record["batch_index"] = batch_index
        decision_record["batches_run"] = batches_run
        decision_record["replayed_count"] = len(replayed_evaluations)
        decision_record["expansions_used"] = expansions_used
        if calibration_summaries:
            decision_record["online_delta_calibration"] = copy.deepcopy(
                calibration_summaries[-1]
            )
        decisions.append(decision_record)
        if decision_writer is not None:
            try:
                decision_writer(decision_record)
            except Exception:
                pass

        final_lane_counts = dict(lane_counts)
        final_i5 = bool(i5)

        d = decision.decision
        final_decision = d.value


        if d == AdaptiveReplayDecision.STOP_FIX:
            stop_reason = "safety_model_or_io_source_gate_failed"
            break
        if d == AdaptiveReplayDecision.STOP_FIX_RANKING:
            stop_reason = (
                "explainable_lane_empty_with_pairwise_inversion_above_threshold"
            )
            break
        if d == AdaptiveReplayDecision.STOP_EARLY_KEEP_BEST:
            stop_reason = "remaining_upper_bound_cannot_beat_current_best"
            break
        if d == AdaptiveReplayDecision.PROCEED_TO_OUTER_LOOP:
            stop_reason = "evidence_sufficient_proceed_to_outer_loop"
            break
        if d == AdaptiveReplayDecision.EXPAND_DIAGNOSTIC:
            # Append the controller-requested diagnostic evidence. The default
            # controller asks for three; the minimum-evidence guard can ask for
            # enough points to complete the 12-point first stage.
            requested = max(1, int(decision.expansion_delta or 3))
            take = min(requested, len(working_diagnostic_pool))
            for k in range(take):
                cand = copy.deepcopy(working_diagnostic_pool[k])
                cand["_adaptive_replay_forced_lane"] = "diagnostic"
                if cand not in working_candidates:
                    working_candidates.append(cand)
            working_diagnostic_pool = working_diagnostic_pool[take:]
            batch_index += 1
            continue
        if d == AdaptiveReplayDecision.EXPAND_ONE_BATCH:
            if expansions_used >= max_expansions:
                stop_reason = "expansions_exhausted"
                break
            expansions_used += 1
            requested = max(1, int(decision.expansion_delta or bs))
            take = min(requested, len(working_reserve_pool))
            for k in range(take):
                cand = copy.deepcopy(working_reserve_pool[k])
                cand["_adaptive_replay_forced_lane"] = "reserve"
                if cand not in working_candidates:
                    working_candidates.append(cand)
            working_reserve_pool = working_reserve_pool[take:]
            batch_index += 1
            continue
        if d == AdaptiveReplayDecision.CONTINUE_NEXT_BATCH:
            batch_index += 1
            continue

        stop_reason = "unknown_decision"
        break

    return PerBatchReplayResult(
        final_decision=final_decision,
        stop_reason=stop_reason,
        batches_run=batches_run,
        expansions_used=expansions_used,
        replayed_indices=replayed_indices,
        replayed_evaluations=replayed_evaluations,
        lane_counts=final_lane_counts,
        decisions=decisions,
        calibration_summaries=calibration_summaries,
        fallback_best_record=fallback_best_record,
        global_best_record=global_best_record,
        remaining_upper_bound_beats_best=final_i5,
        safety_model_gate_passed=safety_model_gate_passed,
    )


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------


def _self_test() -> int:
    """ self test."""
    print("=" * 70, flush=True)
    print(
        "adaptive_replay_per_batch_runner self-test (iter104 P0-A smoke)",
        flush=True,
    )
    print("=" * 70, flush=True)
    pass_count = 0
    fail_count = 0

    def _check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal pass_count, fail_count
        if cond:
            print(f"[PASS] {name}", flush=True)
            pass_count += 1
        else:
            print(f"[FAIL] {name} — {detail}", flush=True)
            fail_count += 1

    baseline = {"cpu": 50.0, "memory": 90.0, "io": 20.0}
    capability = {"cpu": 100.0, "memory": 100.0, "io": 100.0}
    cands = [
        {  # explainable: Memory -7pp conservative (bound 2), IO +0.6pp
            "predicted_resource": {"cpu": 40.0, "memory": 83.0, "io": 20.6},
            "params": {"seq_page_cost": 1.0},
            "_eval_recipe": {
                "db_qps_delta_pct": 5.5,
                "bottleneck_resource_delta_pp": -1.2,
                "target_direction_ok": True,
                "phase2_long_term_goal_met": True,
                "performance_guard_ok": True,
            },
            "plan_change_count": 3,
        },
        {  # fallback: Memory up, db_qps proxy +30%
            "predicted_resource": {"cpu": 50.0, "memory": 99.0, "io": 22.0},
            "predicted_db_qps_delta_pct": 30.64,
            "params": {"seq_page_cost": 2.0},
            "_eval_recipe": {
                "db_qps_delta_pct": 30.64,
                "bottleneck_resource_delta_pp": 5.5,
                "target_direction_ok": False,
                "phase2_long_term_goal_met": False,
                "performance_guard_ok": True,
            },
            "plan_change_count": 6,
        },
        {  # diagnostic plateau (Memory = baseline 90.0)
            "predicted_resource": {"cpu": 50.0, "memory": 90.0, "io": 20.0},
            "params": {"seq_page_cost": 1.5},
            "_eval_recipe": {
                "db_qps_delta_pct": 0.0,
                "bottleneck_resource_delta_pp": 0.0,
                "target_direction_ok": False,
                "phase2_long_term_goal_met": False,
                "performance_guard_ok": True,
            },
            "plan_change_count": 0,
        },
    ]
    diagnostic_pool = [
        {  # diagnostic plateau
            "predicted_resource": {"cpu": 50.0, "memory": 90.0, "io": 20.0},
            "params": {"seq_page_cost": 1.4},
            "_eval_recipe": {
                "db_qps_delta_pct": 0.0,
                "bottleneck_resource_delta_pp": -0.4,
                "target_direction_ok": False,
                "phase2_long_term_goal_met": False,
                "performance_guard_ok": True,
            },
            "plan_change_count": 1,
        }
    ]

    def _replay_cb(cand: Dict[str, Any]) -> Dict[str, Any]:
        recipe = cand.get("_eval_recipe", {})
        return {
            "params": cand.get("params", {}),
            "plan_change_count": cand.get("plan_change_count", 0),
            "candidate_primary_target_status": {
                "db_qps_delta_pct": recipe.get("db_qps_delta_pct", 0.0),
                "bottleneck_resource_delta_pp": recipe.get(
                    "bottleneck_resource_delta_pp", 0.0
                ),
            },
            "target_direction_ok": recipe.get("target_direction_ok", False),
            "phase2_long_term_goal_met": recipe.get(
                "phase2_long_term_goal_met", False
            ),
            "performance_guard_ok": recipe.get("performance_guard_ok", True),
        }

    def _summary_cb(evals: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "pairwise_inversion_rate": 0.05,
            "first_stage_ranking_validation_pass": True,
            "explainable_lane_hit_count": sum(
                1 for e in evals if e.get("phase2_long_term_goal_met")
            ),
            "lane_counts": {},
        }

    # Use default remaining_upper_bound_evaluator=None heuristic
    result = run_adaptive_replay_batches(
        candidates=cands,
        replay_callback=_replay_cb,
        summary_callback=_summary_cb,
        target_resource="memory",
        baseline_resource=baseline,
        capability=capability,
        mandatory_anchor_indices=[],
        forced_anchor_indices=[],
        diagnostic_candidates=diagnostic_pool,
        batch_size=2,
        max_budget=6,
        min_evidence=3,
    )

    # Should have replayed candidates
    _check(
        "self_test_replayed_evaluations_nonempty",
        len(result.replayed_evaluations) > 0,
        f"evaluations={result.replayed_evaluations}",
    )
    _check(
        "self_test_decisions_recorded",
        len(result.decisions) > 0,
        f"decisions={result.decisions}",
    )
    _check(
        "self_test_batches_run_positive",
        result.batches_run > 0,
        f"batches_run={result.batches_run}",
    )
    # Fallback best record persists (cand[1]: db_qps +30.64% non-explainable)
    _check(
        "self_test_fallback_best_record_permanent",
        result.fallback_best_record is not None
        and bool(result.fallback_best_record.get("permanent"))
        and not bool(result.fallback_best_record.get("explainable")),
        f"fallback_best={result.fallback_best_record}",
    )
    _check(
        "self_test_fallback_best_db_qps_30_64",
        result.fallback_best_record is not None
        and abs(
            float(result.fallback_best_record.get("db_qps_delta_pct") or 0.0)
            - 30.64
        )
        < 0.001,
        f"db_qps={result.fallback_best_record.get('db_qps_delta_pct') if result.fallback_best_record else None}",
    )
    # Global best record also tracked for same candidate
    _check(
        "self_test_global_best_record_tracked",
        result.global_best_record is not None
        and abs(
            float(result.global_best_record.get("db_qps_delta_pct") or 0.0)
            - 30.64
        )
        < 0.001,
        f"global_best={result.global_best_record}",
    )

    print("=" * 70, flush=True)
    print(
        f"adaptive_replay_per_batch_runner self-test: {pass_count} passed, "
        f"{fail_count} failed",
        flush=True,
    )
    print("=" * 70, flush=True)
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    import sys

    sys.exit(_self_test())

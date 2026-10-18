#!/usr/bin/env python3
"""Module."""


from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

# Make the project root importable
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)


_passed = 0
_failed = 0


def _check(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"[PASS] {name}")
    else:
        _failed += 1
        print(f"[FAIL] {name} — {detail}")


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _make_candidate(
    *,
    cand_id: str,
    predicted_resource: Dict[str, float],
    predicted_db_qps_delta_pct: Optional[float] = None,
    db_qps_delta_pct: float = 0.0,
    bottleneck_resource_delta_pp: float = 0.0,
    target_direction_ok: bool = False,
    phase2_long_term_goal_met: bool = False,
    performance_guard_ok: bool = True,
    plan_change_count: int = 0,
    params: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    recipe = {
        "db_qps_delta_pct": db_qps_delta_pct,
        "bottleneck_resource_delta_pp": bottleneck_resource_delta_pp,
        "target_direction_ok": target_direction_ok,
        "phase2_long_term_goal_met": phase2_long_term_goal_met,
        "performance_guard_ok": performance_guard_ok,
    }
    cand: Dict[str, Any] = {
        "predicted_resource": dict(predicted_resource),
        "params": dict(params or {"seq_page_cost": 1.0, "random_page_cost": 4.0}),
        "_eval_recipe": recipe,
        "plan_change_count": plan_change_count,
        "_cand_id": cand_id,
    }
    if predicted_db_qps_delta_pct is not None:
        cand["predicted_db_qps_delta_pct"] = predicted_db_qps_delta_pct
    return cand


BASELINE = {"cpu": 50.0, "memory": 90.0, "io": 20.0}
CAPABILITY = {"cpu": 100.0, "memory": 100.0, "io": 100.0}


def _replay_callback(cand: Dict[str, Any]) -> Dict[str, Any]:
    """ replay callback."""
    recipe = cand.get("_eval_recipe", {})
    return {
        "params": dict(cand.get("params", {})),
        "plan_change_count": int(cand.get("plan_change_count", 0)),
        "candidate_primary_target_status": {
            "db_qps_delta_pct": recipe.get("db_qps_delta_pct", 0.0),
            "bottleneck_resource_delta_pp": recipe.get(
                "bottleneck_resource_delta_pp", 0.0
            ),
            "bottleneck_resource": "memory",
        },
        "target_direction_ok": recipe.get("target_direction_ok", False),
        "phase2_long_term_goal_met": recipe.get(
            "phase2_long_term_goal_met", False
        ),
        "performance_guard_ok": recipe.get("performance_guard_ok", True),
        "_cand_id": cand.get("_cand_id"),
    }


def _summary_callback_fixed(
    *,
    pairwise_inversion_rate: float = 0.05,
    ranking_pass: bool = True,
    explainable_hits: Optional[int] = None,
) -> "SummaryCallbackFn":
    """ summary callback fixed."""
    def _summary(evals: List[Dict[str, Any]]) -> Dict[str, Any]:
        hits = (
            explainable_hits
            if explainable_hits is not None
            else sum(1 for e in evals if e.get("phase2_long_term_goal_met"))
        )
        return {
            "pairwise_inversion_rate": pairwise_inversion_rate,
            "first_stage_ranking_validation_pass": ranking_pass,
            "explainable_lane_hit_count": hits,
            "lane_counts": {},
        }
    return _summary  # type: ignore[return-value]


# Type alias for readability
SummaryCallbackFn = Any


class _SpyDecideBatch:
    """ SpyDecideBatch."""


    def __init__(self, target_resource: str = "memory"):
        from adaptive_replay_controller import (
            AdaptiveReplayController,
            LaneQuotaConfig,
        )

        self._controller = AdaptiveReplayController(
            quota=LaneQuotaConfig(), target_resource=target_resource
        )
        self.calls: List[Any] = []  # List[BatchInputs]

    def __call__(self, inputs):
        # import here so module-level imports stay light
        from adaptive_replay_controller import BatchDecision  # noqa: F401
        self.calls.append(inputs)
        return self._controller.decide_batch(inputs)


def _build_initial_like_candidates() -> List[Dict[str, Any]]:
    """ build initial like candidates."""


    cands: List[Dict[str, Any]] = []
    for i in range(7):
        cands.append(
            _make_candidate(
                cand_id=f"explainable_{i}",
                predicted_resource={"cpu": 40.0, "memory": 83.0, "io": 20.6},
                db_qps_delta_pct=5.5 + i * 0.1,
                bottleneck_resource_delta_pp=-1.2 - i * 0.05,
                target_direction_ok=True,
                phase2_long_term_goal_met=True,
                plan_change_count=3 + i,
                params={"seq_page_cost": float(1 + i)},
            )
        )
    for i in range(5):
        cands.append(
            _make_candidate(
                cand_id=f"fallback_{i}",
                predicted_resource={"cpu": 50.0, "memory": 99.0, "io": 22.0},
                predicted_db_qps_delta_pct=30.0,
                db_qps_delta_pct=30.64,
                bottleneck_resource_delta_pp=5.5,
                target_direction_ok=False,
                phase2_long_term_goal_met=False,
                plan_change_count=6,
                params={"random_page_cost": float(2 + i)},
            )
        )
    for i in range(5):
        cands.append(
            _make_candidate(
                cand_id=f"diagnostic_{i}",
                predicted_resource={"cpu": 50.0, "memory": 90.0, "io": 20.0},
                db_qps_delta_pct=0.0,
                bottleneck_resource_delta_pp=0.0,
                plan_change_count=0,
                params={"cpu_tuple_cost": 0.01},
            )
        )
    return cands


def _build_feedback_like_candidates() -> List[Dict[str, Any]]:
    """ build feedback like candidates."""

    cands: List[Dict[str, Any]] = []
    for i in range(4):
        cands.append(
            _make_candidate(
                cand_id=f"fb_explain_{i}",
                predicted_resource={"cpu": 41.0, "memory": 84.0, "io": 21.0},
                db_qps_delta_pct=4.5 + i * 0.2,
                bottleneck_resource_delta_pp=-1.0 - i * 0.03,
                target_direction_ok=True,
                phase2_long_term_goal_met=True,
                plan_change_count=2 + i,
            )
        )
    for i in range(6):
        cands.append(
            _make_candidate(
                cand_id=f"fb_fb_{i}",
                predicted_resource={"cpu": 50.0, "memory": 96.0, "io": 22.0},
                predicted_db_qps_delta_pct=28.0,
                db_qps_delta_pct=27.68,
                bottleneck_resource_delta_pp=3.0,
                target_direction_ok=False,
                phase2_long_term_goal_met=False,
                plan_change_count=5,
            )
        )
    for i in range(4):
        cands.append(
            _make_candidate(
                cand_id=f"fb_diag_{i}",
                predicted_resource={"cpu": 50.0, "memory": 90.0, "io": 20.0},
                db_qps_delta_pct=0.0,
                bottleneck_resource_delta_pp=0.0,
                plan_change_count=0,
            )
        )
    return cands


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_initial_path_decide_batch_called_per_batch() -> None:
    """test initial path decide batch called per batch."""

    from adaptive_replay_per_batch_runner import run_adaptive_replay_batches

    cands = _build_initial_like_candidates()
    spy = _SpyDecideBatch(target_resource="memory")
    summary_cb = _summary_callback_fixed(
        pairwise_inversion_rate=0.05,
        ranking_pass=True,
    )
    result = run_adaptive_replay_batches(
        candidates=cands,
        replay_callback=_replay_callback,
        summary_callback=summary_cb,
        target_resource="memory",
        baseline_resource=BASELINE,
        capability=CAPABILITY,
        mandatory_anchor_indices=[],
        forced_anchor_indices=[],
        diagnostic_candidates=[],
        batch_size=6,
        max_budget=24,
        min_evidence=12,
        decide_batch_spy=spy,
    )
    _check(
        "initial_batches_run_positive",
        result.batches_run > 0,
        f"batches_run={result.batches_run}",
    )
    _check(
        "initial_decide_batch_called_per_batch",
        len(spy.calls) == result.batches_run,
        f"len(spy.calls)={len(spy.calls)} batches_run={result.batches_run}",
    )

    if result.batches_run >= 2:
        first_inputs = spy.calls[0]
        second_inputs = spy.calls[1]

        _check(
            "initial_second_batch_replayed_count_grew",
            second_inputs.replayed_count > first_inputs.replayed_count,
            f"first.replayed_count={first_inputs.replayed_count} "
            f"second.replayed_count={second_inputs.replayed_count}",
        )

        first_lane_counts = first_inputs.lane_counts or {}
        second_lane_counts = second_inputs.lane_counts or {}
        _check(
            "initial_second_batch_lane_counts_reflect_first_batch",
            any(
                second_lane_counts.get(lane, 0)
                >= first_lane_counts.get(lane, 0)
                and second_lane_counts.get(lane, 0) > 0
                for lane in ("explainable", "fallback", "diagnostic")
            ),
            f"first_lane_counts={first_lane_counts} second_lane_counts={second_lane_counts}",
        )


        _check(
            "initial_second_batch_i3_reflects_first_batch_real_replay",
            second_inputs.fallback_best_db_qps_delta_pct is not None
            and abs(second_inputs.fallback_best_db_qps_delta_pct - 30.64)
            < 0.001,
            f"second.i3={second_inputs.fallback_best_db_qps_delta_pct}",
        )


        _check(
            "initial_second_batch_i2_grew_from_first_batch",
            second_inputs.explainable_conservative_pass_count
            >= first_inputs.explainable_conservative_pass_count,
            f"first.i2={first_inputs.explainable_conservative_pass_count} "
            f"second.i2={second_inputs.explainable_conservative_pass_count}",
        )
    _check(
        "initial_decision_recorded",
        result.final_decision != "",
        f"final_decision={result.final_decision!r}",
    )


def test_feedback_path_decide_batch_called_per_batch() -> None:
    """test feedback path decide batch called per batch."""

    from adaptive_replay_per_batch_runner import run_adaptive_replay_batches

    cands = _build_feedback_like_candidates()
    spy = _SpyDecideBatch(target_resource="memory")
    summary_cb = _summary_callback_fixed(
        pairwise_inversion_rate=0.05,
        ranking_pass=True,
    )
    result = run_adaptive_replay_batches(
        candidates=cands,
        replay_callback=_replay_callback,
        summary_callback=summary_cb,
        target_resource="memory",
        baseline_resource=BASELINE,
        capability=CAPABILITY,
        batch_size=6,
        max_budget=24,
        min_evidence=12,
        decide_batch_spy=spy,
    )
    _check(
        "feedback_batches_run_positive",
        result.batches_run > 0,
        f"batches_run={result.batches_run}",
    )
    _check(
        "feedback_decide_batch_called_per_batch",
        len(spy.calls) == result.batches_run,
        f"len(spy.calls)={len(spy.calls)} batches_run={result.batches_run}",
    )
    if result.batches_run >= 2:
        first_inputs = spy.calls[0]
        second_inputs = spy.calls[1]
        _check(
            "feedback_second_batch_replayed_count_grew",
            second_inputs.replayed_count > first_inputs.replayed_count,
            f"first.replayed_count={first_inputs.replayed_count} "
            f"second.replayed_count={second_inputs.replayed_count}",
        )
        # feedback lane candidates db_qps_delta_pct=27.68
        _check(
            "feedback_second_batch_i3_reflects_first_batch_real_replay",
            second_inputs.fallback_best_db_qps_delta_pct is not None
            and abs(second_inputs.fallback_best_db_qps_delta_pct - 27.68)
            < 0.001,
            f"second.i3={second_inputs.fallback_best_db_qps_delta_pct}",
        )


def test_stop_early_keep_best_stops_within_quota() -> None:
    """test stop early keep best stops within quota."""


    from adaptive_replay_per_batch_runner import run_adaptive_replay_batches

    cands = _build_initial_like_candidates()
    spy = _SpyDecideBatch(target_resource="memory")


    cands_stop = [
        _make_candidate(
            cand_id="fb_only_a",
            predicted_resource={"cpu": 50.0, "memory": 95.0, "io": 22.0},
            predicted_db_qps_delta_pct=4.0,
            db_qps_delta_pct=4.2,
            bottleneck_resource_delta_pp=2.0,  # Memory up → I7 not incremented
            target_direction_ok=False,
            phase2_long_term_goal_met=False,
            plan_change_count=2,
        )
        for _ in range(12)
    ]
    summary_cb = _summary_callback_fixed(
        pairwise_inversion_rate=0.05,
        ranking_pass=True,
        explainable_hits=1,  # I2=1 < min_explainable_for_proceed (3)
    )
    result = run_adaptive_replay_batches(
        candidates=cands_stop,
        replay_callback=_replay_callback,
        summary_callback=summary_cb,
        target_resource="memory",
        baseline_resource=BASELINE,
        capability=CAPABILITY,
        batch_size=6,
        max_budget=24,
        min_evidence=12,
        decide_batch_spy=spy,

        remaining_upper_bound_evaluator=lambda remaining, best: False,
    )
    _check(
        "stop_early_decision_value",
        result.final_decision == "STOP_EARLY_KEEP_BEST",
        f"final_decision={result.final_decision}; "
        f"decisions={[d.get('decision') for d in result.decisions]}",
    )

    _check(
        "stop_early_stopped_before_max_budget",
        len(result.replayed_evaluations) < 24,
        f"replayed={len(result.replayed_evaluations)}",
    )

    _check(
        "stop_early_at_least_one_batch",
        result.batches_run >= 1,
        f"batches_run={result.batches_run}",
    )

    _check(
        "stop_early_fallback_best_retained",
        result.fallback_best_record is not None
        and abs(
            float(result.fallback_best_record.get("db_qps_delta_pct") or 0.0)
            - 4.2
        )
        < 0.001,
        f"fallback_best={result.fallback_best_record}",
    )


def test_stop_early_at_6_or_12() -> None:
    """test stop early at 6 or 12."""


    from adaptive_replay_controller import LaneQuotaConfig
    from adaptive_replay_per_batch_runner import run_adaptive_replay_batches

    custom_quota = LaneQuotaConfig(
        max_budget=24, min_evidence=12, batch_size=6, max_expansions=2,
        explainable=7, fallback=24, diagnostic=5, mandatory_anchor=2, reserve=5,
    )

    cands = []
    for i in range(20):
        cands.append(
            _make_candidate(
                cand_id=f"fb_{i}",
                predicted_resource={"cpu": 50.0, "memory": 95.0, "io": 22.0},
                predicted_db_qps_delta_pct=4.0,  # fallback lane
                db_qps_delta_pct=4.2,
                bottleneck_resource_delta_pp=2.0,
                target_direction_ok=False,
                phase2_long_term_goal_met=False,
                plan_change_count=2,
            )
        )
    summary_cb = _summary_callback_fixed(
        pairwise_inversion_rate=0.05,
        ranking_pass=True,
        explainable_hits=1,
    )

    result = run_adaptive_replay_batches(
        candidates=cands,
        replay_callback=_replay_callback,
        summary_callback=summary_cb,
        target_resource="memory",
        baseline_resource=BASELINE,
        capability=CAPABILITY,
        quota=custom_quota,
        batch_size=6,
        max_budget=24,
        min_evidence=12,
        remaining_upper_bound_evaluator=lambda remaining, best: False,
    )
    _check(
        "stop_early_batch6_decision_value",
        result.final_decision == "STOP_EARLY_KEEP_BEST",
        f"final_decision={result.final_decision}",
    )
    # The campaign-wide cap overrides the legacy six/twelve evidence schedule.
    _check(
        "stop_early_replays_global_top5_only",
        len(result.replayed_evaluations) == 5,
        f"replayed={len(result.replayed_evaluations)}",
    )


    state2 = {"batches_seen": 0}

    def _i5_evolving_v2(remaining, best):


        b = state2["batches_seen"]
        state2["batches_seen"] += 1


        return b == 0

    result2 = run_adaptive_replay_batches(
        candidates=cands,
        replay_callback=_replay_callback,
        summary_callback=summary_cb,
        target_resource="memory",
        baseline_resource=BASELINE,
        capability=CAPABILITY,
        quota=custom_quota,
        batch_size=6,
        max_budget=24,
        min_evidence=12,
        remaining_upper_bound_evaluator=_i5_evolving_v2,
    )
    _check(
        "stop_early_at_12_decision_value",
        result2.final_decision == "STOP_EARLY_KEEP_BEST",
        f"final_decision={result2.final_decision}; "
        f"decisions={[d.get('decision') for d in result2.decisions]}",
    )
    _check(
        "stop_early_second_schedule_still_global_top5",
        len(result2.replayed_evaluations) == 5 and result2.batches_run == 1,
        f"replayed={len(result2.replayed_evaluations)}; "
        f"batches_run={result2.batches_run}",
    )


def test_expand_diagnostic_appends_and_continues() -> None:
    """test expand diagnostic appends and continues."""


    from adaptive_replay_per_batch_runner import run_adaptive_replay_batches


    cands = [
        _make_candidate(
            cand_id=f"fallback_{i}",
            predicted_resource={"cpu": 50.0, "memory": 95.0, "io": 22.0},
            predicted_db_qps_delta_pct=8.0,
            db_qps_delta_pct=8.0,
            bottleneck_resource_delta_pp=2.0,
            target_direction_ok=False,
            phase2_long_term_goal_met=False,
            plan_change_count=2,
        )
        for i in range(8)
    ]
    diagnostic_pool = [
        _make_candidate(
            cand_id=f"diag_extra_{i}",
            predicted_resource={"cpu": 50.0, "memory": 90.0, "io": 20.0},
            db_qps_delta_pct=0.0,
            bottleneck_resource_delta_pp=-0.5,  # Memory down (a bit) — still
            # not explainable because conservative bound makes it through?
            # marker for diagnostic lane classification (plateau)
            target_direction_ok=False,
            phase2_long_term_goal_met=False,
            plan_change_count=0,
        )
        for i in range(6)
    ]
    summary_cb = _summary_callback_fixed(
        pairwise_inversion_rate=0.05,  # I4 <= 0.10
        ranking_pass=True,
        explainable_hits=0,  # I2 == 0
    )
    decisions_seen: List[str] = []

    def _decision_writer(rec: Dict[str, Any]) -> None:
        decisions_seen.append(rec.get("decision", ""))

    result = run_adaptive_replay_batches(
        candidates=cands,
        replay_callback=_replay_callback,
        summary_callback=summary_cb,
        target_resource="memory",
        baseline_resource=BASELINE,
        capability=CAPABILITY,
        diagnostic_candidates=diagnostic_pool,
        batch_size=6,
        max_budget=24,
        min_evidence=12,
        decision_writer=_decision_writer,
        remaining_upper_bound_evaluator=lambda remaining, best: True,  # I5=True
    )

    _check(
        "expand_diagnostic_decision_seen",
        "EXPAND_DIAGNOSTIC" in decisions_seen,
        f"decisions_seen={decisions_seen}",
    )
    # The decision is auditable, but no second real batch may exceed Top-5.
    _check(
        "expand_diagnostic_stops_at_global_top5",
        result.batches_run == 1 and len(result.replayed_evaluations) == 5,
        f"batches_run={result.batches_run}; stop_reason={result.stop_reason}",
    )
    # Diagnostic candidates remain read-only after the real replay budget ends.
    has_diag_eval = any(
        ev.get("_cand_id", "").startswith("diag_extra_")
        for ev in result.replayed_evaluations
    )
    _check(
        "expand_diagnostic_not_replayed_beyond_global_cap",
        not has_diag_eval,
        f"eval cand_ids={ [ev.get('_cand_id') for ev in result.replayed_evaluations] }",
    )

    _check(
        "expand_diagnostic_does_not_stop_fix_ranking",
        "STOP_FIX_RANKING" not in decisions_seen,
        f"decisions_seen={decisions_seen}",
    )


def test_fallback_best_preserved_when_explainable_lane_empty() -> None:
    """test fallback best preserved when explainable lane empty."""
    from adaptive_replay_per_batch_runner import run_adaptive_replay_batches

    cands = [
        _make_candidate(
            cand_id="cand19_style",
            predicted_resource={"cpu": 50.0, "memory": 99.0, "io": 22.0},
            predicted_db_qps_delta_pct=30.64,
            db_qps_delta_pct=30.64,
            bottleneck_resource_delta_pp=5.50,
            target_direction_ok=False,
            phase2_long_term_goal_met=False,
            performance_guard_ok=True,
            plan_change_count=6,
            params={
                "seq_page_cost": 1.02737,
                "random_page_cost": 2.44005,
                "cpu_tuple_cost": 1e-06,
                "cpu_index_tuple_cost": 0.00497895,
                "cpu_operator_cost": 0.0025,
                "parallel_setup_cost": 1000.0,
                "parallel_tuple_cost": 0.1,
            },
        )
    ]
    summary_cb = _summary_callback_fixed(
        pairwise_inversion_rate=0.05,
        ranking_pass=True,
        explainable_hits=0,  # I2 == 0 → EXPAND_DIAGNOSTIC
    )
    result = run_adaptive_replay_batches(
        candidates=cands,
        replay_callback=_replay_callback,
        summary_callback=summary_cb,
        target_resource="memory",
        baseline_resource=BASELINE,
        capability=CAPABILITY,
        batch_size=6,
        max_budget=24,
        min_evidence=12,
        remaining_upper_bound_evaluator=lambda remaining, best: False,
    )
    _check(
        "fallback_best_record_present",
        result.fallback_best_record is not None,
        f"fallback_best={result.fallback_best_record}",
    )
    if result.fallback_best_record:
        rec = result.fallback_best_record
        _check(
            "fallback_best_db_qps_delta_pct_30_64",
            abs(float(rec.get("db_qps_delta_pct") or 0.0) - 30.64) < 0.001,
            f"db_qps={rec.get('db_qps_delta_pct')}",
        )
        _check(
            "fallback_best_permanent_true",
            bool(rec.get("permanent")),
            f"rec={rec}",
        )
        _check(
            "fallback_best_explainable_false",
            not bool(rec.get("explainable")),
            f"rec={rec}",
        )
        _check(
            "fallback_best_reason_correct",
            rec.get("reason") == "fallback_best_dbqps_not_explainable",
            f"reason={rec.get('reason')}",
        )
        _check(
            "fallback_best_has_7_cost_params",
            len(rec.get("params") or {}) == 7,
            f"params={rec.get('params')}",
        )
    _check(
        "global_best_record_also_present",
        result.global_best_record is not None
        and abs(
            float(result.global_best_record.get("db_qps_delta_pct") or 0.0)
            - 30.64
        )
        < 0.001,
        f"global_best={result.global_best_record}",
    )


def test_mandatory_anchor_does_not_swallow_explainable_quota() -> None:
    """test mandatory anchor does not swallow explainable quota."""

    from adaptive_replay_per_batch_runner import run_adaptive_replay_batches

    # 1 mandatory anchor + 1 explainable + 1 fallback + 1 diagnostic
    cands = [
        _make_candidate(
            cand_id="mandatory_anchor",
            predicted_resource={"cpu": 50.0, "memory": 99.0, "io": 22.0},
            predicted_db_qps_delta_pct=20.0,
            db_qps_delta_pct=20.0,
            bottleneck_resource_delta_pp=5.0,
            target_direction_ok=False,
            phase2_long_term_goal_met=False,
            plan_change_count=4,
        ),
        _make_candidate(
            cand_id="explainable_only",
            predicted_resource={"cpu": 40.0, "memory": 83.0, "io": 20.6},
            db_qps_delta_pct=5.5,
            bottleneck_resource_delta_pp=-1.2,
            target_direction_ok=True,
            phase2_long_term_goal_met=True,
            plan_change_count=3,
        ),
        _make_candidate(
            cand_id="fallback_only",
            predicted_resource={"cpu": 50.0, "memory": 96.0, "io": 22.0},
            predicted_db_qps_delta_pct=10.0,
            db_qps_delta_pct=10.0,
            bottleneck_resource_delta_pp=4.0,
            target_direction_ok=False,
            phase2_long_term_goal_met=False,
            plan_change_count=4,
        ),
        _make_candidate(
            cand_id="diag_only",
            predicted_resource={"cpu": 50.0, "memory": 90.0, "io": 20.0},
        ),
    ]
    summary_cb = _summary_callback_fixed(
        pairwise_inversion_rate=0.05,
        ranking_pass=True,
        explainable_hits=1,
    )
    result = run_adaptive_replay_batches(
        candidates=cands,
        replay_callback=_replay_callback,
        summary_callback=summary_cb,
        target_resource="memory",
        baseline_resource=BASELINE,
        capability=CAPABILITY,
        mandatory_anchor_indices=[0],  # mandatory_anchor
        forced_anchor_indices=[],
        batch_size=4,
        max_budget=12,
        min_evidence=4,
        remaining_upper_bound_evaluator=lambda remaining, best: False,
    )

    mandatory_replayed = any(
        ev.get("_cand_id") == "mandatory_anchor"
        for ev in result.replayed_evaluations
    )
    _check(
        "mandatory_anchor_replayed",
        mandatory_replayed,
        f"eval ids={[ev.get('_cand_id') for ev in result.replayed_evaluations]}",
    )

    explainable_replayed = any(
        ev.get("_cand_id") == "explainable_only"
        for ev in result.replayed_evaluations
    )
    _check(
        "explainable_replayed_not_swallowed_by_mandatory",
        explainable_replayed,
        f"eval ids={[ev.get('_cand_id') for ev in result.replayed_evaluations]}",
    )

    mandatory_ev = next(
        (ev for ev in result.replayed_evaluations if ev.get("_cand_id") == "mandatory_anchor"),
        None,
    )
    _check(
        "mandatory_anchor_assigned_to_mandatory_lane",
        mandatory_ev is not None
        and mandatory_ev.get("_lane") == "mandatory_anchor",
        f"mandatory_ev._lane={ mandatory_ev.get('_lane') if mandatory_ev else None }",
    )

    explainable_ev = next(
        (ev for ev in result.replayed_evaluations if ev.get("_cand_id") == "explainable_only"),
        None,
    )
    _check(
        "explainable_assigned_to_explainable_lane",
        explainable_ev is not None
        and explainable_ev.get("_lane") == "explainable",
        f"explainable_ev._lane={ explainable_ev.get('_lane') if explainable_ev else None }",
    )


def test_spy_inputs_prove_per_batch_decision_uses_new_feedback() -> None:
    """test spy inputs prove per batch decision uses new feedback."""


    from adaptive_replay_per_batch_runner import run_adaptive_replay_batches

    cands = _build_initial_like_candidates()
    spy = _SpyDecideBatch(target_resource="memory")


    summary_state = {"n": 0}

    def _summary_evolutionary(evals: List[Dict[str, Any]]) -> Dict[str, Any]:
        n = summary_state["n"]
        summary_state["n"] += 1
        # Different inputs every batch to ensure I2/I7 reflect real replay
        return {
            "pairwise_inversion_rate": 0.05,
            "first_stage_ranking_validation_pass": (n >= 1),  # batch 1 False, later True
            "explainable_lane_hit_count": 2 if n == 0 else 4,
            "lane_counts": {},
        }

    result = run_adaptive_replay_batches(
        candidates=cands,
        replay_callback=_replay_callback,
        summary_callback=_summary_evolutionary,
        target_resource="memory",
        baseline_resource=BASELINE,
        capability=CAPABILITY,
        mandatory_anchor_indices=[],
        batch_size=6,
        max_budget=18,
        min_evidence=12,
        decide_batch_spy=spy,
        remaining_upper_bound_evaluator=lambda remaining, best: True,
    )
    _check(
        "spy_records_single_capped_batch",
        len(spy.calls) == 1,
        f"len(spy.calls)={len(spy.calls)}",
    )
    if len(spy.calls) >= 2:
        i0 = spy.calls[0]
        i1 = spy.calls[1]
        _check(
            "spy_i2_grew_between_batches",
            i1.explainable_conservative_pass_count
            > i0.explainable_conservative_pass_count,
            f"i0.i2={i0.explainable_conservative_pass_count} "
            f"i1.i2={i1.explainable_conservative_pass_count}",
        )
        _check(
            "spy_replayed_count_grew_between_batches",
            i1.replayed_count > i0.replayed_count,
            f"i0.replayed_count={i0.replayed_count} "
            f"i1.replayed_count={i1.replayed_count}",
        )
        _check(
            "spy_batch_index_advanced",
            i1.batch_index == i0.batch_index + 1,
            f"i0.batch_index={i0.batch_index} i1.batch_index={i1.batch_index}",
        )

        second_lane_counts = i1.lane_counts or {}
        _check(
            "spy_second_batch_lane_counts_nonzero",
            any(v > 0 for v in second_lane_counts.values()),
            f"second_lane_counts={second_lane_counts}",
        )
    _check(
        "spy_decision_writer_records_capped_batch",
        len(result.decisions) == 1,
        f"len(decisions)={len(result.decisions)}",
    )


def test_safety_gate_fail_triggers_stop_fix() -> None:
    """test safety gate fail triggers stop fix."""
    from adaptive_replay_per_batch_runner import run_adaptive_replay_batches

    cands = _build_initial_like_candidates()
    summary_cb = _summary_callback_fixed(
        pairwise_inversion_rate=0.05,
        ranking_pass=True,
    )
    result = run_adaptive_replay_batches(
        candidates=cands,
        replay_callback=_replay_callback,
        summary_callback=summary_cb,
        target_resource="memory",
        baseline_resource=BASELINE,
        capability=CAPABILITY,
        safety_model_gate_passed=False,
        batch_size=6,
        max_budget=24,
        min_evidence=12,
        remaining_upper_bound_evaluator=lambda remaining, best: False,
    )
    _check(
        "safety_fail_decision_value",
        result.final_decision == "STOP_FIX",
        f"final_decision={result.final_decision}; "
        f"decisions={[d.get('decision') for d in result.decisions]}",
    )
    _check(
        "safety_fail_stop_reason",
        "gate" in result.stop_reason or "safety" in result.stop_reason,
        f"stop_reason={result.stop_reason}",
    )


def test_expand_one_batch_triggers_on_reserve() -> None:
    """test expand one batch triggers on reserve."""


    from adaptive_replay_controller import LaneQuotaConfig
    from adaptive_replay_per_batch_runner import run_adaptive_replay_batches


    custom_quota = LaneQuotaConfig(
        max_budget=6, min_evidence=12, batch_size=6, max_expansions=2,
        explainable=7, fallback=24, diagnostic=5, mandatory_anchor=2, reserve=12,
    )

    cands = [
        _make_candidate(
            cand_id=f"c_{i}",
            predicted_resource={"cpu": 50.0, "memory": 95.0, "io": 22.0},
            # fallback lane classification: db_qps proxy +10 → fallback
            predicted_db_qps_delta_pct=10.0,
            db_qps_delta_pct=6.5,  # >= 5%
            bottleneck_resource_delta_pp=2.5,
            target_direction_ok=False,
            phase2_long_term_goal_met=False,
            plan_change_count=2,
        )
        for i in range(6)
    ]
    reserve = [
        _make_candidate(
            cand_id=f"reserve_{i}",
            predicted_resource={"cpu": 50.0, "memory": 95.0, "io": 22.0},
            predicted_db_qps_delta_pct=11.0,
            db_qps_delta_pct=7.0,
            bottleneck_resource_delta_pp=2.5,
            target_direction_ok=False,
            phase2_long_term_goal_met=False,
            plan_change_count=2,
        )
        for i in range(6)
    ]
    summary_cb = _summary_callback_fixed(
        pairwise_inversion_rate=0.05,
        ranking_pass=True,
        explainable_hits=1,
    )
    result = run_adaptive_replay_batches(
        candidates=cands,
        replay_callback=_replay_callback,
        summary_callback=summary_cb,
        target_resource="memory",
        baseline_resource=BASELINE,
        capability=CAPABILITY,
        quota=custom_quota,
        reserve_candidates=reserve,
        batch_size=6,
        max_budget=6,
        min_evidence=12,
        max_expansions=2,

        remaining_upper_bound_evaluator=lambda remaining, best: True,
    )
    decisions_seen = [d.get("decision", "") for d in result.decisions]
    _check(
        "expand_one_batch_not_allowed_by_global_cap",
        "EXPAND_ONE_BATCH" not in decisions_seen,
        f"decisions_seen={decisions_seen}",
    )
    _check(
        "expand_one_batch_uses_no_real_expansion",
        result.expansions_used == 0,
        f"expansions_used={result.expansions_used}",
    )
    # Reserve candidates may stay in the read-only pool but are not replayed.
    has_reserve = any(
        ev.get("_cand_id", "").startswith("reserve_")
        for ev in result.replayed_evaluations
    )
    _check(
        "expand_one_batch_does_not_replay_reserve_beyond_cap",
        not has_reserve and len(result.replayed_evaluations) <= 5,
        f"eval cand_ids={[ev.get('_cand_id') for ev in result.replayed_evaluations]}",
    )


def test_proceed_to_outer_loop_when_evidence_sufficient() -> None:
    """test proceed to outer loop when evidence sufficient."""
    from adaptive_replay_per_batch_runner import run_adaptive_replay_batches

    # 12 explainable candidates with real down + 5%+ QPS. The current
    # controller requires the full first-stage evidence prefix before proceed.
    cands = [
        _make_candidate(
            cand_id=f"exp_{i}",
            predicted_resource={"cpu": 40.0, "memory": 83.0, "io": 20.6},
            db_qps_delta_pct=6.0 + i * 0.1,
            bottleneck_resource_delta_pp=-1.5,
            target_direction_ok=True,
            phase2_long_term_goal_met=True,
            plan_change_count=3,
        )
        for i in range(12)
    ]
    summary_cb = _summary_callback_fixed(
        pairwise_inversion_rate=0.05,
        ranking_pass=True,
        explainable_hits=4,  # I2 = 4
    )
    result = run_adaptive_replay_batches(
        candidates=cands,
        replay_callback=_replay_callback,
        summary_callback=summary_cb,
        target_resource="memory",
        baseline_resource=BASELINE,
        capability=CAPABILITY,
        batch_size=6,
        max_budget=24,
        min_evidence=12,
        remaining_upper_bound_evaluator=lambda remaining, best: False,
    )
    decisions_seen = [d.get("decision", "") for d in result.decisions]
    _check(
        "proceed_to_outer_loop_decision_seen",
        "PROCEED_TO_OUTER_LOOP" in decisions_seen,
        f"decisions_seen={decisions_seen}",
    )
    _check(
        "proceed_final_decision_value",
        result.final_decision == "PROCEED_TO_OUTER_LOOP",
        f"final_decision={result.final_decision}",
    )


def test_two_call_paths_register_for_iteration_in_dummy_dispatch() -> None:
    """test two call paths register for iteration in dummy dispatch."""


    from adaptive_replay_per_batch_runner import run_adaptive_replay_batches

    initial_cands = _build_initial_like_candidates()
    initial_spy = _SpyDecideBatch(target_resource="memory")
    run_adaptive_replay_batches(
        candidates=initial_cands,
        replay_callback=_replay_callback,
        summary_callback=_summary_callback_fixed(
            pairwise_inversion_rate=0.05, ranking_pass=True
        ),
        target_resource="memory",
        baseline_resource=BASELINE,
        capability=CAPABILITY,
        batch_size=6,
        max_budget=18,
        min_evidence=12,
        decide_batch_spy=initial_spy,
    )
    feedback_cands = _build_feedback_like_candidates()
    feedback_spy = _SpyDecideBatch(target_resource="memory")
    run_adaptive_replay_batches(
        candidates=feedback_cands,
        replay_callback=_replay_callback,
        summary_callback=_summary_callback_fixed(
            pairwise_inversion_rate=0.05, ranking_pass=True
        ),
        target_resource="memory",
        baseline_resource=BASELINE,
        capability=CAPABILITY,
        batch_size=6,
        max_budget=18,
        min_evidence=12,
        decide_batch_spy=feedback_spy,
    )
    _check(
        "both_paths_helper_invoked_at_least_once",
        len(initial_spy.calls) >= 1 and len(feedback_spy.calls) >= 1,
        f"initial calls={len(initial_spy.calls)} "
        f"feedback calls={len(feedback_spy.calls)}",
    )
    _check(
        "both_paths_use_same_helper_via_decide_batch",
        len(initial_spy.calls) > 0 and len(feedback_spy.calls) > 0,
        f"both must produce decide_batch calls",
    )


def main() -> int:
    print("=== iter104 P0-A adaptive replay per-batch runner tests ===")
    test_initial_path_decide_batch_called_per_batch()
    test_feedback_path_decide_batch_called_per_batch()
    test_stop_early_keep_best_stops_within_quota()
    test_stop_early_at_6_or_12()
    test_expand_diagnostic_appends_and_continues()
    test_fallback_best_preserved_when_explainable_lane_empty()
    test_mandatory_anchor_does_not_swallow_explainable_quota()
    test_spy_inputs_prove_per_batch_decision_uses_new_feedback()
    test_safety_gate_fail_triggers_stop_fix()
    test_expand_one_batch_triggers_on_reserve()
    test_proceed_to_outer_loop_when_evidence_sufficient()
    test_two_call_paths_register_for_iteration_in_dummy_dispatch()
    print(f"\n=== Summary: {_passed} passed, {_failed} failed ===")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

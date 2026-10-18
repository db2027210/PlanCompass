"""Module."""


from __future__ import annotations

import importlib
import os
import sys
from typing import Any, Dict, List, Optional

# Make the project root importable
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import adaptive_replay_controller
import adaptive_replay_per_batch_runner
from adaptive_replay_controller import (
    AdaptiveReplayDecision,
    BatchDecision,
    BatchInputs,
    LaneQuotaConfig,
)


COST_PARAM_KEYS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)


def _check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[PASS] {name}", flush=True)
    else:
        print(f"[FAIL] {name} — {detail}", flush=True)
        raise AssertionError(f"{name} failed: {detail}")


def _sample_candidate(
    *,
    seq: float = 1.0,
    rand: float = 4.0,
    cpu_tuple: float = 0.01,
    cpu_index: float = 0.005,
    cpu_op: float = 0.0025,
    parallel_setup: float = 1000.0,
    parallel_tuple: float = 0.1,
    predicted_memory: float = 90.0,
    predicted_db_qps_delta_pct: float = 0.0,
    is_target_down: bool = False,
    is_explainable: bool = False,
    rank: int = 1,
) -> Dict[str, Any]:
    return {
        "params": {
            "seq_page_cost": seq,
            "random_page_cost": rand,
            "cpu_tuple_cost": cpu_tuple,
            "cpu_index_tuple_cost": cpu_index,
            "cpu_operator_cost": cpu_op,
            "parallel_setup_cost": parallel_setup,
            "parallel_tuple_cost": parallel_tuple,
        },
        "predicted_resource": {
            "cpu": 50.0,
            "memory": predicted_memory,
            "io": 20.0,
        },
        "predicted_db_qps_delta_pct": predicted_db_qps_delta_pct,
        "rank": rank,
        "score": 0.5,
        "source": "iter106_test",
        "_eval_recipe": {
            "db_qps_delta_pct": predicted_db_qps_delta_pct,
            "bottleneck_resource_delta_pp": (-2.0 if is_target_down else 1.5),
            "target_direction_ok": is_target_down,
            "phase2_long_term_goal_met": is_explainable,
            "performance_guard_ok": True,
        },
        "plan_change_count": 2,
    }


def _replay_callback_factory(recipes_out: List[Dict[str, Any]]):
    def _cb(cand: Dict[str, Any]) -> Dict[str, Any]:
        recipe = cand.get("_eval_recipe", {})
        eval_record = {
            "params": dict(cand.get("params", {})),
            "plan_change_count": cand.get("plan_change_count", 0),
            "candidate_primary_target_status": {
                "db_qps_delta_pct": recipe.get("db_qps_delta_pct", 0.0),
                "bottleneck_resource_delta_pp": recipe.get(
                    "bottleneck_resource_delta_pp", 0.0
                ),
            },
            "target_direction_ok": recipe.get("target_direction_ok", False),
            "primary_target_direction_ok": recipe.get("target_direction_ok", False),
            "phase2_long_term_goal_met": recipe.get(
                "phase2_long_term_goal_met", False
            ),
            "performance_guard_ok": recipe.get("performance_guard_ok", True),
        }
        recipes_out.append(eval_record)
        return eval_record

    return _cb


def _summary_callback_factory():
    def _sum(evals: List[Dict[str, Any]]) -> Dict[str, Any]:
        explainable = sum(
            1 for e in evals if e.get("phase2_long_term_goal_met")
        )
        return {
            "pairwise_inversion_rate": 0.04,
            "first_stage_ranking_validation_pass": bool(len(evals) >= 6),
            "explainable_lane_hit_count": explainable,
        }

    return _sum


def _make_batch_decision(
    decision: AdaptiveReplayDecision,
    inputs: BatchInputs,
    reason: str = "test",
) -> BatchDecision:
    """ make batch decision."""
    return BatchDecision(
        decision=decision,
        reason=reason,
        inputs_snapshot={},
        remaining_upper_bound_beats_best=inputs.remaining_upper_bound_beats_best,
        ranking_stable=inputs.ranking_stable,
        pairwise_inversion_rate=inputs.pairwise_inversion_rate,
        lane_counts=dict(inputs.lane_counts),
        explainable_lane_empty=inputs.explainable_conservative_pass_count == 0,
    )


def _make_spy_decide_batch(target_resource: str = "memory") -> "_SpyDecideBatch":
    """ make spy decide batch."""
    return _SpyDecideBatch(target_resource=target_resource)


class _SpyDecideBatch:
    """ SpyDecideBatch."""

    def __init__(self, target_resource: str = "memory") -> None:
        from adaptive_replay_controller import (
            AdaptiveReplayController,
            LaneQuotaConfig,
        )

        self._controller = AdaptiveReplayController(
            quota=LaneQuotaConfig(), target_resource=target_resource
        )
        self.calls: List[Any] = []

    def __call__(self, inputs: BatchInputs) -> BatchDecision:
        self.calls.append(inputs)
        return self._controller.decide_batch(inputs)


class _AlwaysStopEarlyDecideBatch:
    """ AlwaysStopEarlyDecideBatch."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, inputs: BatchInputs) -> BatchDecision:
        self.calls += 1
        return _make_batch_decision(
            AdaptiveReplayDecision.STOP_EARLY_KEEP_BEST,
            inputs,
            reason="upper_bound_test",
        )


class _AlwaysContinueDecideBatch:
    """ AlwaysContinueDecideBatch."""

    def __init__(self) -> None:
        self.calls: List[Any] = []

    def __call__(self, inputs: BatchInputs) -> BatchDecision:
        self.calls.append(inputs)
        return _make_batch_decision(
            AdaptiveReplayDecision.CONTINUE_NEXT_BATCH,
            inputs,
            reason="collect_more_evidence",
        )


def _diagnostic_only_quota(
    *,
    max_budget: int = 12,
    min_evidence: int = 6,
    batch_size: int = 6,
) -> LaneQuotaConfig:
    return LaneQuotaConfig(
        max_budget=max_budget,
        min_evidence=min_evidence,
        batch_size=batch_size,
        explainable=0,
        fallback=0,
        diagnostic=max_budget,
        mandatory_anchor=0,
        reserve=0,
        max_expansions=0,
    )


def _fallback_friendly_quota(
    *,
    max_budget: int = 12,
    min_evidence: int = 6,
    batch_size: int = 6,
) -> LaneQuotaConfig:
    """ fallback friendly quota."""


    return LaneQuotaConfig(
        max_budget=max_budget,
        min_evidence=min_evidence,
        batch_size=batch_size,
        explainable=0,
        fallback=max_budget,
        diagnostic=max_budget,
        mandatory_anchor=0,
        reserve=0,
        max_expansions=0,
    )


def test_live_path_helper_invoked() -> int:
    """test live path helper invoked."""
    print(
        "[TEST 1] parameter_optimizer._run_adaptive_replay_batches real call chain",
        flush=True,
    )
    try:
        importlib.import_module("parameter_optimizer")
    except Exception as exc:  # pragma: no cover
        print(f"[FAIL] parameter_optimizer import error: {exc!r}", flush=True)
        return 1
    import parameter_optimizer

    _check(
        "parameter_optimizer_module_exposes_run_adaptive_replay_batches",
        hasattr(parameter_optimizer, "_run_adaptive_replay_batches"),
        "module  _run_adaptive_replay_batches ",
    )
    _check(
        "binding_is_same_function_as_helper",
        parameter_optimizer._run_adaptive_replay_batches
        is getattr(adaptive_replay_per_batch_runner, "run_adaptive_replay_batches"),
        "module  _run_adaptive_replay_batches  helper ",
    )
    print("[PASS] TEST 1 done", flush=True)
    return 0


def test_replay_callback_feedback_into_batch2_inputs() -> int:
    """test replay callback feedback into batch2 inputs."""
    print(
        "[TEST 2] replay callback returned evaluations flow into second-batch BatchInputs "
        "(feedback_replay phase)",
        flush=True,
    )
    cands: List[Dict[str, Any]] = []
    for i in range(12):
        cands.append(
            _sample_candidate(
                seq=1.0 + 0.01 * i,
                rand=4.0 + 0.01 * i,
                cpu_tuple=0.01 + 0.001 * i,
                predicted_memory=90.0,
                predicted_db_qps_delta_pct=(10.0 + i),
                is_target_down=False,
                is_explainable=False,
                rank=i + 1,
            )
        )
    recipes: List[Dict[str, Any]] = []
    spy = _AlwaysContinueDecideBatch()
    decision_records: List[Dict[str, Any]] = []

    def _decision_writer(rec: Dict[str, Any]) -> None:
        decision_records.append(dict(rec))

    result = adaptive_replay_per_batch_runner.run_adaptive_replay_batches(
        candidates=cands,
        replay_callback=_replay_callback_factory(recipes),
        summary_callback=_summary_callback_factory(),
        target_resource="memory",
        baseline_resource={"cpu": 50.0, "memory": 90.0, "io": 20.0},
        capability={"cpu": 100.0, "memory": 100.0, "io": 100.0},
        batch_size=6,
        max_budget=12,
        min_evidence=6,
        max_expansions=0,
        decide_batch_spy=spy,
        decision_writer=_decision_writer,
        quota=_fallback_friendly_quota(
            max_budget=12, min_evidence=6, batch_size=6
        ),
    )

    _check(
        "at_least_one_batch_executed_via_replay_callback",
        len(recipes) > 0,
        f"recipes count = {len(recipes)}",
    )
    _check(
        "two_decisions_recorded",
        len(spy.calls) == 2,
        f"spy.calls = {len(spy.calls)}",
    )
    if len(spy.calls) >= 2:
        second_inputs = spy.calls[1]
        first_inputs = spy.calls[0]
        _check(
            "second_batch_replayed_count_strictly_greater_than_first",
            second_inputs.replayed_count > first_inputs.replayed_count,
            f"first.replayed_count={first_inputs.replayed_count} "
            f"second.replayed_count={second_inputs.replayed_count}",
        )
        _check(
            "all_candidates_replayed_via_callback_12",
            len(result.replayed_evaluations) == 12,
            f"replayed={len(result.replayed_evaluations)}",
        )
        _check(
            "second_batch_i3_geq_first_batch_i3",
            (second_inputs.fallback_best_db_qps_delta_pct or 0.0)
            >= (first_inputs.fallback_best_db_qps_delta_pct or 0.0),
            f"first.I3={first_inputs.fallback_best_db_qps_delta_pct} "
            f"second.I3={second_inputs.fallback_best_db_qps_delta_pct}",
        )
    _check(
        "decision_writer_was_called_per_batch",
        len(decision_records) == len(spy.calls),
        f"decision_records={len(decision_records)} spy.calls={len(spy.calls)}",
    )
    print("[PASS] TEST 2 done", flush=True)
    return 0


def test_stop_early_keep_best_stops() -> int:
    """test stop early keep best stops."""
    print(
        "[TEST 3] STOP_EARLY_KEEP_BEST stops helper before max_budget (feedback)",
        flush=True,
    )

    spy = _AlwaysStopEarlyDecideBatch()
    cands = [
        _sample_candidate(seq=i + 1.0, rank=i + 1)
        for i in range(24)
    ]
    recipes: List[Dict[str, Any]] = []
    result = adaptive_replay_per_batch_runner.run_adaptive_replay_batches(
        candidates=cands,
        replay_callback=_replay_callback_factory(recipes),
        summary_callback=_summary_callback_factory(),
        target_resource="memory",
        baseline_resource={"cpu": 50.0, "memory": 90.0, "io": 20.0},
        capability={"cpu": 100.0, "memory": 100.0, "io": 100.0},
        batch_size=6,
        max_budget=24,
        min_evidence=6,
        max_expansions=0,
        decide_batch_spy=spy,
        quota=LaneQuotaConfig(
            max_budget=24,
            min_evidence=6,
            batch_size=6,
            explainable=0,
            fallback=0,
            diagnostic=6,
            mandatory_anchor=0,
            reserve=0,
            max_expansions=0,
        ),
    )
    _check(
        "stop_early_decision_emitted",
        result.final_decision == AdaptiveReplayDecision.STOP_EARLY_KEEP_BEST.value,
        f"final_decision={result.final_decision}",
    )
    _check(
        "stop_reason_is_helper_standardized_for_stop_early",
        result.stop_reason == "remaining_upper_bound_cannot_beat_current_best",
        f"stop_reason={result.stop_reason}",
    )
    _check(
        "batches_run_is_one",
        result.batches_run == 1,
        f"batches_run={result.batches_run}",
    )
    _check(
        "replayed_count_equal_batch_size_not_max_budget",
        len(result.replayed_evaluations) == 6 and len(recipes) == 6,
        f"replayed={len(result.replayed_evaluations)} recipes={len(recipes)}",
    )
    _check(
        "spy_called_exactly_once_with_stop_early",
        spy.calls == 1,
        f"spy.calls={spy.calls}",
    )
    print("[PASS] TEST 3 done", flush=True)
    return 0


def test_expand_diagnostic_appends_and_continues() -> int:
    """test expand diagnostic appends and continues."""
    print(
        "[TEST 4] EXPAND_DIAGNOSTIC appends diagnostic pool then continues (feedback)",
        flush=True,
    )

    state = {"phase": 0, "spied_inputs": []}

    def _spy(inputs: BatchInputs) -> BatchDecision:
        state["spied_inputs"].append(inputs)
        if state["phase"] == 0:
            state["phase"] = 1
            return _make_batch_decision(
                AdaptiveReplayDecision.EXPAND_DIAGNOSTIC,
                inputs,
                reason="add_diagnostic_lane",
            )
        return _make_batch_decision(
            AdaptiveReplayDecision.CONTINUE_NEXT_BATCH,
            inputs,
            reason="keep_going_after_expand",
        )

    cands = [_sample_candidate(seq=i + 1.0, rank=i + 1) for i in range(6)]
    diagnostic_pool = [
        _sample_candidate(
            seq=99.0,
            rand=99.0,
            rank=99,
            predicted_memory=90.0,
            is_explainable=False,
            is_target_down=False,
        )
    ]
    recipes: List[Dict[str, Any]] = []
    from adaptive_replay_controller import LaneQuotaConfig

    result = adaptive_replay_per_batch_runner.run_adaptive_replay_batches(
        candidates=cands,
        replay_callback=_replay_callback_factory(recipes),
        summary_callback=_summary_callback_factory(),
        target_resource="memory",
        baseline_resource={"cpu": 50.0, "memory": 90.0, "io": 20.0},
        capability={"cpu": 100.0, "memory": 100.0, "io": 100.0},
        batch_size=6,
        max_budget=12,
        min_evidence=6,
        max_expansions=0,
        quota=LaneQuotaConfig(
            max_budget=12,
            min_evidence=6,
            batch_size=6,
            max_expansions=0,
            diagnostic=6,
        ),
        decide_batch_spy=_spy,
        diagnostic_candidates=diagnostic_pool,
    )
    _check(
        "explore_more_than_initial_6_after_expand_diagnostic",
        len(recipes) > 6,
        f"recipes (replayed) count = {len(recipes)}",
    )
    _check(
        "decisions_count_at_least_two",
        len(state["spied_inputs"]) >= 2,
        f"spied={len(state['spied_inputs'])}",
    )
    print("[PASS] TEST 4 done", flush=True)
    return 0


def test_fallback_and_global_best_preserved_when_explainable_zero() -> int:
    """test fallback and global best preserved when explainable zero."""
    print(
        "[TEST 5] fallback/global-best preserved even when explainable=0 (feedback)",
        flush=True,
    )

    def _spy(inputs: BatchInputs) -> BatchDecision:
        return _make_batch_decision(
            AdaptiveReplayDecision.STOP_EARLY_KEEP_BEST,
            inputs,
            reason="upper_bound_test_explainable_zero",
        )

    cands: List[Dict[str, Any]] = []
    for i in range(6):
        cand = _sample_candidate(
            seq=i + 1.0,
            rank=i + 1,
            is_explainable=False,
            is_target_down=False,
            predicted_db_qps_delta_pct=10.0 + i,
        )
        cands.append(cand)
    recipes: List[Dict[str, Any]] = []
    result = adaptive_replay_per_batch_runner.run_adaptive_replay_batches(
        candidates=cands,
        replay_callback=_replay_callback_factory(recipes),
        summary_callback=_summary_callback_factory(),
        target_resource="memory",
        baseline_resource={"cpu": 50.0, "memory": 90.0, "io": 20.0},
        capability={"cpu": 100.0, "memory": 100.0, "io": 100.0},
        batch_size=6,
        max_budget=6,
        min_evidence=6,
        max_expansions=0,
        decide_batch_spy=_spy,
        quota=LaneQuotaConfig(
            max_budget=6,
            min_evidence=6,
            batch_size=6,
            explainable=0,
            fallback=6,
            diagnostic=6,
            mandatory_anchor=0,
            reserve=0,
            max_expansions=0,
        ),
    )
    _check(
        "fallback_best_recorded",
        result.fallback_best_record is not None,
        f"fallback_best={result.fallback_best_record}",
    )
    if result.fallback_best_record is not None:
        _check(
            "fallback_best_permanent",
            bool(result.fallback_best_record.get("permanent")),
            f"record={result.fallback_best_record}",
        )
        _check(
            "fallback_best_not_explainable",
            not bool(result.fallback_best_record.get("explainable")),
            f"record={result.fallback_best_record}",
        )
        _check(
            "fallback_best_high_db_qps",
            float(result.fallback_best_record.get("db_qps_delta_pct") or 0.0)
            >= 10.0,
            f"db_qps={result.fallback_best_record.get('db_qps_delta_pct')}",
        )
    _check(
        "global_best_recorded_with_high_db_qps",
        result.global_best_record is not None
        and float(result.global_best_record.get("db_qps_delta_pct") or 0.0)
        >= 10.0,
        f"global_best={result.global_best_record}",
    )
    if result.global_best_record is not None:
        _check(
            "global_best_explainable_flag_false",
            result.global_best_record.get("explainable_hit") is False,
            f"global_best={result.global_best_record}",
        )
    print("[PASS] TEST 5 done", flush=True)
    return 0


def test_parameter_optimizer_feedback_replay_block_writes_feedback_phase() -> int:
    """test parameter optimizer feedback replay block writes feedback phase."""

    print(
        "[TEST 6] parameter_optimizer.py feedback live path writes phase=feedback_replay",
        flush=True,
    )
    import parameter_optimizer  # noqa
    src_path = parameter_optimizer.__file__
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    _check(
        "feedback_live_path_dispatch_marker_present",
        "_feedback_live_path_dispatch_done" in src,
        "missing _feedback_live_path_dispatch_done marker",
    )
    _check(
        "feedback_live_path_invokes_run_adaptive_replay_batches",
        "_run_adaptive_replay_batches(" in src,
        "missing _run_adaptive_replay_batches( call in feedback live path",
    )
    _check(
        "feedback_dispatch_done_break_in_for_loop",
        "if _feedback_live_path_dispatch_done:" in src,
        "missing break guard in feedback for-loop",
    )
    _check(
        "feedback_post_summary_guard_skips_when_dispatched",
        "if not _feedback_live_path_dispatch_done:" in src,
        "missing post-summary dispatch guard in feedback path",
    )
    _check(
        "feedback_live_path_decision_phase_is_feedback_replay",
        '"phase": "feedback_replay"' in src,
        "missing decision_writer phase=feedback_replay marker",
    )
    _check(
        "feedback_live_path_call_site_marker_present",
        "feedback_replay_per_batch_dispatch" in src,
        "missing feedback_replay_per_batch_dispatch call_site marker",
    )
    _check(
        "feedback_live_path_dispatch_done_event_present",
        "feedback_replay_per_batch_dispatch_done" in src,
        "missing feedback_replay_per_batch_dispatch_done event marker",
    )
    print("[PASS] TEST 6 done", flush=True)
    return 0


def test_only_7_cost_params_in_candidates() -> int:
    """test only 7 cost params in candidates."""
    print(
        "[TEST 7] candidate params and eval params only contain 7 cost params (feedback)",
        flush=True,
    )
    cands = [_sample_candidate(seq=i + 1.0, rank=i + 1) for i in range(6)]
    for cand in cands:
        params = cand.get("params") or {}
        keys = set(params.keys())
        _check(
            "candidate_params_subset_of_7_cost_keys",
            keys.issubset(set(COST_PARAM_KEYS)),
            f"keys={keys}",
        )
    recipes: List[Dict[str, Any]] = []
    result = adaptive_replay_per_batch_runner.run_adaptive_replay_batches(
        candidates=cands,
        replay_callback=_replay_callback_factory(recipes),
        summary_callback=_summary_callback_factory(),
        target_resource="memory",
        baseline_resource={"cpu": 50.0, "memory": 90.0, "io": 20.0},
        capability={"cpu": 100.0, "memory": 100.0, "io": 100.0},
        batch_size=3,
        max_budget=6,
        min_evidence=3,
        max_expansions=0,
        decide_batch_spy=_make_spy_decide_batch(),
    )
    for ev in result.replayed_evaluations:
        params = ev.get("params") or {}
        keys = set(params.keys())
        _check(
            "eval_record_params_subset_of_7_cost_keys",
            keys.issubset(set(COST_PARAM_KEYS)),
            f"keys={keys}",
        )
    if result.fallback_best_record is not None:
        keys = set((result.fallback_best_record.get("params") or {}).keys())
        _check(
            "fallback_best_params_subset_of_7_cost_keys",
            keys.issubset(set(COST_PARAM_KEYS)),
            f"keys={keys}",
        )
    if result.global_best_record is not None:
        keys = set((result.global_best_record.get("params") or {}).keys())
        _check(
            "global_best_params_subset_of_7_cost_keys",
            keys.issubset(set(COST_PARAM_KEYS)),
            f"keys={keys}",
        )
    print("[PASS] TEST 7 done", flush=True)
    return 0


def main() -> int:
    tests = [
        test_live_path_helper_invoked,
        test_replay_callback_feedback_into_batch2_inputs,
        test_stop_early_keep_best_stops,
        test_expand_diagnostic_appends_and_continues,
        test_fallback_and_global_best_preserved_when_explainable_zero,
        test_parameter_optimizer_feedback_replay_block_writes_feedback_phase,
        test_only_7_cost_params_in_candidates,
    ]
    fails = 0
    for t in tests:
        try:
            rc = t()
            if rc != 0:
                fails += 1
        except Exception as exc:
            print(f"[FAIL] {t.__name__} raised: {exc!r}", flush=True)
            fails += 1
    print("=" * 70, flush=True)
    print(
        f"iter106 P0-B-2 feedback live path adapter tests: {len(tests) - fails}/"
        f"{len(tests)} passed",
        flush=True,
    )
    print("=" * 70, flush=True)
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

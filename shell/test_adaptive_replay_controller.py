"""Module."""


from __future__ import annotations

import os
import sys

# Make the project root importable
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import adaptive_replay_controller as arc
from adaptive_replay_controller import (
    AdaptiveReplayController,
    AdaptiveReplayDecision,
    BatchInputs,
    LaneQuotaConfig,
    ReplayLane,
    assign_lanes,
    enforce_lane_quotas,
    record_fallback_best,
    select_batch_candidates,
)


def _make_inputs(**overrides) -> BatchInputs:
    """ make inputs."""


    defaults = dict(
        safety_model_gate_passed=True,
        io_source_valid=True,
        io_stable_sample_count_positive=True,
        explainable_conservative_pass_count=0,
        fallback_best_db_qps_delta_pct=None,
        pairwise_inversion_rate=0.0,
        remaining_upper_bound_beats_best=False,
        ranking_stable=True,
        cumulative_target_down_evidence_count=0,
        batch_index=0,
        batches_so_far=1,
        replayed_count=6,
        expansions_used=0,
        lane_counts={
            "explainable": 0,
            "fallback": 0,
            "diagnostic": 0,
            "mandatory_anchor": 0,
            "reserve": 0,
        },
    )
    defaults.update(overrides)
    return BatchInputs(**defaults)


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------


def test_adaptive_replay_stop_early() -> None:
    """test adaptive replay stop early."""


    controller = AdaptiveReplayController(target_resource="memory")
    inp = _make_inputs(
        explainable_conservative_pass_count=2,
        fallback_best_db_qps_delta_pct=4.0,
        pairwise_inversion_rate=0.05,
        remaining_upper_bound_beats_best=False,
        ranking_stable=True,  # I6=stable
        cumulative_target_down_evidence_count=2,
        batch_index=0,
        batches_so_far=1,
        replayed_count=6,
        lane_counts={
            "explainable": 2,
            "fallback": 2,
            "diagnostic": 1,
            "mandatory_anchor": 1,
            "reserve": 0,
        },
    )
    dec = controller.decide_batch(inp)
    assert dec.decision == AdaptiveReplayDecision.STOP_EARLY_KEEP_BEST, (
        f"low-uncertainty + best stable should STOP_EARLY_KEEP_BEST, got {dec.decision}"
    )
    assert dec.expansion_delta == 0, (
        f"STOP_EARLY_KEEP_BEST should not expand, got expansion_delta={dec.expansion_delta}"
    )
    assert not dec.explainable_lane_empty, (
        "explainable lane has 2 candidates, should not be flagged empty"
    )

    assert dec.fallback_best_record is None or dec.fallback_best_record.get("permanent"), (
        "fallback_best_record slot should be available or permanent"
    )


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------


def test_adaptive_replay_expand_on_upper_bound() -> None:
    """test adaptive replay expand on upper bound."""


    controller = AdaptiveReplayController(target_resource="memory")
    inp = _make_inputs(
        explainable_conservative_pass_count=1,
        fallback_best_db_qps_delta_pct=6.0,  # >=5% to skip #7
        pairwise_inversion_rate=0.05,
        remaining_upper_bound_beats_best=True,  # I5=true
        ranking_stable=True,  # I6=stable (skip #6)
        cumulative_target_down_evidence_count=1,  # <3, skip PROCEED
        batch_index=3,
        batches_so_far=4,
        replayed_count=24,  # budget exhausted
        expansions_used=0,  # < max_expansions=2
    )
    dec = controller.decide_batch(inp)
    assert dec.decision == AdaptiveReplayDecision.EXPAND_ONE_BATCH, (
        f"budget exhausted + I5=true + expansions<2 should EXPAND_ONE_BATCH, got {dec.decision}"
    )
    assert dec.expansion_delta == 6, (
        f"EXPAND_ONE_BATCH should add +6, got {dec.expansion_delta}"
    )


def test_adaptive_replay_stops_after_expansions_exhausted() -> None:
    """test adaptive replay stops after expansions exhausted."""
    controller = AdaptiveReplayController(target_resource="memory")
    inp = _make_inputs(
        explainable_conservative_pass_count=1,
        fallback_best_db_qps_delta_pct=6.0,
        pairwise_inversion_rate=0.05,
        remaining_upper_bound_beats_best=True,
        ranking_stable=False,
        cumulative_target_down_evidence_count=1,
        replayed_count=24,
        expansions_used=2,
    )
    dec = controller.decide_batch(inp)
    assert dec.decision == AdaptiveReplayDecision.STOP_EARLY_KEEP_BEST, (
        "exhausted budget must stop and preserve best, got "
        f"{dec.decision}"
    )


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------


def test_adaptive_replay_no_count_fill_in_explainable_lane() -> None:
    """test adaptive replay no count fill in explainable lane."""


    controller = AdaptiveReplayController(target_resource="memory")


    inp_a = _make_inputs(
        explainable_conservative_pass_count=0,  # I2=0
        fallback_best_db_qps_delta_pct=0.0,
        pairwise_inversion_rate=0.7218,
        remaining_upper_bound_beats_best=False,
        ranking_stable=False,
        cumulative_target_down_evidence_count=0,
        lane_counts={
            "explainable": 0,
            "fallback": 5,
            "diagnostic": 1,
            "mandatory_anchor": 0,
            "reserve": 0,
        },
    )
    dec_a = controller.decide_batch(inp_a)
    assert dec_a.decision == AdaptiveReplayDecision.STOP_FIX_RANKING, (
        f"I2=0 + I4>0.10 should STOP_FIX_RANKING, got {dec_a.decision}"
    )
    assert dec_a.explainable_lane_empty, (
        "STOP_FIX_RANKING must emit explainable_lane_empty=True (v6  count-fill)"
    )


    inp_b = _make_inputs(
        explainable_conservative_pass_count=0,  # I2=0
        fallback_best_db_qps_delta_pct=0.0,
        pairwise_inversion_rate=0.05,  # I4 <= 0.10
        remaining_upper_bound_beats_best=False,
        ranking_stable=True,
        cumulative_target_down_evidence_count=0,
        lane_counts={
            "explainable": 0,
            "fallback": 5,
            "diagnostic": 1,
            "mandatory_anchor": 0,
            "reserve": 0,
        },
    )
    dec_b = controller.decide_batch(inp_b)
    assert dec_b.decision == AdaptiveReplayDecision.EXPAND_DIAGNOSTIC, (
        f"I2=0 + I4<=0.10 should EXPAND_DIAGNOSTIC, got {dec_b.decision}"
    )
    assert dec_b.explainable_lane_empty, (
        "EXPAND_DIAGNOSTIC must emit explainable_lane_empty=True"
    )
    assert dec_b.expansion_delta == 3, (
        f"EXPAND_DIAGNOSTIC should add +3, got {dec_b.expansion_delta}"
    )


    baseline = {"cpu": 50.0, "memory": 90.0, "io": 20.0}
    capability = {"cpu": 100.0, "memory": 100.0, "io": 100.0}
    cands = [

        {
            "predicted_resource": {"cpu": 50.0, "memory": 95.0, "io": 22.0},
            "predicted_db_qps_delta_pct": 10.0,
        }
        for _ in range(5)
    ]
    lanes = assign_lanes(
        cands,
        target_resource="memory",
        baseline_resource=baseline,
        capability=capability,
        mandatory_anchor_indices=[],
        forced_anchor_indices=[],
        conservative_error_bound_pp=2.0,
    )
    assert len(lanes["explainable"]) == 0, (
        "5 fallback candidates should not produce any explainable lane candidate"
    )
    assert len(lanes["fallback"]) == 5, (
        "5 fallback candidates should all go to fallback lane"
    )

    selected = select_batch_candidates(
        lanes, LaneQuotaConfig(), batch_size=6, reserve_enabled=False
    )
    assert len(selected["explainable"]) == 0, (
        "no cross-lane count-fill: explainable stays 0 even if fallback has 5"
    )
    assert len(selected["fallback"]) <= 5, (
        "fallback selected within quota"
    )

    check = enforce_lane_quotas(lanes, LaneQuotaConfig())
    assert check["explainable_lane_empty"], (
        "enforce_lane_quotas must flag explainable_lane_empty"
    )
    assert check["no_count_fill_applied"], (
        "enforce_lane_quotas must affirm no count-fill applied"
    )


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------


def test_adaptive_replay_mandatory_anchor_no_lane_swallow() -> None:
    """test adaptive replay mandatory anchor no lane swallow."""


    baseline = {"cpu": 50.0, "memory": 90.0, "io": 20.0}
    capability = {"cpu": 100.0, "memory": 100.0, "io": 100.0}
    cands = [

        {
            "predicted_resource": {"cpu": 40.0, "memory": 99.0, "io": 22.0},
            "predicted_db_qps_delta_pct": 30.0,
        },

        {
            "predicted_resource": {"cpu": 37.0, "memory": 96.0, "io": 22.0},
            "predicted_db_qps_delta_pct": 27.0,
        },
        # cand2-4: explainable (Memory -7pp + IO +0.6pp, conservative bound=2pp)
        {
            "predicted_resource": {"cpu": 40.0, "memory": 83.0, "io": 20.6},
        },
        {
            "predicted_resource": {"cpu": 41.0, "memory": 82.5, "io": 21.0},
        },
        {
            "predicted_resource": {"cpu": 42.0, "memory": 82.0, "io": 21.5},
        },
    ]
    lanes = assign_lanes(
        cands,
        target_resource="memory",
        baseline_resource=baseline,
        capability=capability,
        mandatory_anchor_indices=[0],  # cand19
        forced_anchor_indices=[1],  # cand2 forced anchor
        conservative_error_bound_pp=2.0,
    )
    assert 0 in lanes["mandatory_anchor"], (
        "cand0 should be in mandatory_anchor lane (is_mandatory_anchor=True)"
    )
    assert 1 in lanes["mandatory_anchor"], (
        "cand1 should be in mandatory_anchor lane (is_forced_anchor=True)"
    )
    assert len(lanes["explainable"]) == 3, (
        f"3 explainable candidates expected, got {lanes['explainable']}"
    )

    check = enforce_lane_quotas(lanes, LaneQuotaConfig())
    assert not check["lane_quota_exceeded"].get("mandatory_anchor"), (
        "mandatory_anchor lane within quota=2"
    )

    selected = select_batch_candidates(
        lanes, LaneQuotaConfig(), batch_size=6, reserve_enabled=False
    )
    assert len(selected["mandatory_anchor"]) == 2, (
        f"2 mandatory anchors selected, got {selected['mandatory_anchor']}"
    )
    assert len(selected["explainable"]) == 3, (
        f"3 explainable selected (not swallowed by anchor), got {selected['explainable']}"
    )

    assert check["lane_counts"]["explainable"] == 3, (
        "explainable lane_counts=3, independent of mandatory_anchor"
    )


# ---------------------------------------------------------------------------
# Test 5: PROCEED_TO_OUTER_LOOP
# ---------------------------------------------------------------------------


def test_adaptive_replay_proceed_to_outer_loop() -> None:
    """test adaptive replay proceed to outer loop."""
    controller = AdaptiveReplayController(target_resource="memory")
    inp = _make_inputs(
        explainable_conservative_pass_count=3,  # I2>=3
        fallback_best_db_qps_delta_pct=7.0,  # I3>=5%
        pairwise_inversion_rate=0.05,  # I4<=0.10
        remaining_upper_bound_beats_best=False,
        ranking_stable=True,
        cumulative_target_down_evidence_count=3,  # I7>=3
        batch_index=3,
        batches_so_far=4,
        replayed_count=24,
    )
    dec = controller.decide_batch(inp)
    assert dec.decision == AdaptiveReplayDecision.PROCEED_TO_OUTER_LOOP, (
        f"full evidence should PROCEED_TO_OUTER_LOOP, got {dec.decision}"
    )


# ---------------------------------------------------------------------------
# Test 6: STOP_FIX on safety gate fail (I1=false)
# ---------------------------------------------------------------------------


def test_adaptive_replay_stop_fix_on_safety_gate() -> None:
    """test adaptive replay stop fix on safety gate."""


    controller = AdaptiveReplayController(target_resource="memory")

    # A
    inp = _make_inputs(safety_model_gate_passed=False)
    dec = controller.decide_batch(inp)
    assert dec.decision == AdaptiveReplayDecision.STOP_FIX, (
        f"safety gate fail should STOP_FIX, got {dec.decision}"
    )

    # B
    inp = _make_inputs(io_source_valid=False)
    dec = controller.decide_batch(inp)
    assert dec.decision == AdaptiveReplayDecision.STOP_FIX, (
        f"io_source invalid should STOP_FIX, got {dec.decision}"
    )

    # C
    inp = _make_inputs(io_stable_sample_count_positive=False)
    dec = controller.decide_batch(inp)
    assert dec.decision == AdaptiveReplayDecision.STOP_FIX, (
        f"io stable_sample_count==0 should STOP_FIX, got {dec.decision}"
    )


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------


def test_adaptive_replay_stop_fix_ranking_on_i2_zero_i4_high() -> None:
    """test adaptive replay stop fix ranking on i2 zero i4 high."""


    controller = AdaptiveReplayController(target_resource="memory")
    inp = _make_inputs(
        explainable_conservative_pass_count=0,
        pairwise_inversion_rate=0.7218,
        fallback_best_db_qps_delta_pct=0.0,
        ranking_stable=False,
        lane_counts={
            "explainable": 0,
            "fallback": 5,
            "diagnostic": 1,
            "mandatory_anchor": 0,
            "reserve": 0,
        },
    )
    dec = controller.decide_batch(inp)
    assert dec.decision == AdaptiveReplayDecision.STOP_FIX_RANKING, (
        f"v6-style I2=0 + I4=0.7218 should STOP_FIX_RANKING, got {dec.decision}"
    )
    assert dec.explainable_lane_empty, (
        "STOP_FIX_RANKING must emit explainable_lane_empty=True ( count-fill)"
    )


# ---------------------------------------------------------------------------
# Test 8: EXPAND_DIAGNOSTIC on plateau (I2=0 + I4<=0.10)
# ---------------------------------------------------------------------------


def test_adaptive_replay_expand_diagnostic_on_plateau() -> None:
    """test adaptive replay expand diagnostic on plateau."""


    controller = AdaptiveReplayController(target_resource="memory")
    inp = _make_inputs(
        explainable_conservative_pass_count=0,
        pairwise_inversion_rate=0.05,
        fallback_best_db_qps_delta_pct=0.0,
        ranking_stable=True,
        cumulative_target_down_evidence_count=0,
        lane_counts={
            "explainable": 0,
            "fallback": 2,
            "diagnostic": 3,
            "mandatory_anchor": 1,
            "reserve": 0,
        },
    )
    dec = controller.decide_batch(inp)
    assert dec.decision == AdaptiveReplayDecision.EXPAND_DIAGNOSTIC, (
        f"I2=0 + I4<=0.10 should EXPAND_DIAGNOSTIC, got {dec.decision}"
    )
    assert dec.expansion_delta == 3, (
        f"EXPAND_DIAGNOSTIC should add +3, got {dec.expansion_delta}"
    )
    assert dec.explainable_lane_empty, (
        "EXPAND_DIAGNOSTIC must emit explainable_lane_empty=True"
    )


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------


def test_fallback_best_record_permanent() -> None:
    """test fallback best record permanent."""


    rec = record_fallback_best(
        candidate_index=19,
        db_qps_delta_pct=30.64,
        params={
            "seq_page_cost": 1.0273684210526317,
            "random_page_cost": 2.440047216005639,
            "cpu_tuple_cost": 1e-06,
            "cpu_index_tuple_cost": 0.004978947368421052,
            "cpu_operator_cost": 0.0025,
            "parallel_setup_cost": 1000.0,
            "parallel_tuple_cost": 0.1,
        },
        plan_change_count=6,
        capture_file="memory/hybench_ap_plan_captures/feedback_final_0_cand19_20260706_062734.json",
        target_resource="memory",
        target_resource_delta_pp=5.50,
    )
    assert rec["permanent"], "fallback best record must be permanent"
    assert not rec["explainable"], (
        "fallback best must be marked not explainable (Memory +5.50pp wrong dir)"
    )
    assert rec["reason"] == "fallback_best_dbqps_not_explainable", (
        f"reason mismatch: {rec['reason']}"
    )
    assert len(rec["params"]) == 7, (
        f"must record all 7 cost params, got {len(rec['params'])}"
    )

    whitelist = {
        "seq_page_cost",
        "random_page_cost",
        "cpu_tuple_cost",
        "cpu_index_tuple_cost",
        "cpu_operator_cost",
        "parallel_setup_cost",
        "parallel_tuple_cost",
    }
    assert set(rec["params"].keys()) == whitelist, (
        "params must be exactly the 7 cost param whitelist, no 8th param"
    )
    assert rec["candidate_index"] == 19
    assert rec["db_qps_delta_pct"] == 30.64
    assert rec["target_resource"] == "memory"
    assert rec["target_resource_delta_pp"] == 5.50


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------


def test_to_jsonl_record_serialization() -> None:
    """test to jsonl record serialization."""
    controller = AdaptiveReplayController(target_resource="memory")
    inp = _make_inputs(
        explainable_conservative_pass_count=3,
        fallback_best_db_qps_delta_pct=7.0,
        pairwise_inversion_rate=0.05,
        cumulative_target_down_evidence_count=3,
    )
    dec = controller.decide_batch(inp)
    rec = dec.to_jsonl_record()
    assert rec["event"] == "adaptive_replay_decision", (
        f"event mismatch: {rec['event']}"
    )
    assert rec["decision"] == AdaptiveReplayDecision.PROCEED_TO_OUTER_LOOP.value, (
        f"decision mismatch: {rec['decision']}"
    )
    assert "inputs_snapshot" in rec, "inputs_snapshot must be present for audit"
    assert "I2_explainable_conservative_pass_count" in rec["inputs_snapshot"]
    assert "I4_pairwise_inversion_rate" in rec["inputs_snapshot"]
    assert "lane_counts" in rec, "lane_counts must be present"
    assert "remaining_upper_bound_beats_best" in rec
    assert "ranking_stable" in rec

    import json

    json.dumps(rec)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        ("test_adaptive_replay_stop_early", test_adaptive_replay_stop_early),
        ("test_adaptive_replay_expand_on_upper_bound",
         test_adaptive_replay_expand_on_upper_bound),
        ("test_adaptive_replay_stops_after_expansions_exhausted",
         test_adaptive_replay_stops_after_expansions_exhausted),
        ("test_adaptive_replay_no_count_fill_in_explainable_lane",
         test_adaptive_replay_no_count_fill_in_explainable_lane),
        ("test_adaptive_replay_mandatory_anchor_no_lane_swallow",
         test_adaptive_replay_mandatory_anchor_no_lane_swallow),
        ("test_adaptive_replay_proceed_to_outer_loop",
         test_adaptive_replay_proceed_to_outer_loop),
        ("test_adaptive_replay_stop_fix_on_safety_gate",
         test_adaptive_replay_stop_fix_on_safety_gate),
        ("test_adaptive_replay_stop_fix_ranking_on_i2_zero_i4_high",
         test_adaptive_replay_stop_fix_ranking_on_i2_zero_i4_high),
        ("test_adaptive_replay_expand_diagnostic_on_plateau",
         test_adaptive_replay_expand_diagnostic_on_plateau),
        ("test_fallback_best_record_permanent",
         test_fallback_best_record_permanent),
        ("test_to_jsonl_record_serialization",
         test_to_jsonl_record_serialization),
    ]
    pass_count = 0
    for name, fn in tests:
        try:
            fn()
            print(f"[PASS] {name}")
            pass_count += 1
        except AssertionError as exc:
            print(f"[FAIL] {name}: {exc}")
        except Exception as exc:
            print(f"[ERROR] {name}: {type(exc).__name__}: {exc}")
    print("=" * 70)
    print(f"adaptive replay controller tests: {pass_count}/{len(tests)} passed")
    print("=" * 70)
    if pass_count != len(tests):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

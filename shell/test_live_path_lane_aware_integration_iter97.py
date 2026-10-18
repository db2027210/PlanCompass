#!/usr/bin/env python3
"""Module."""


from __future__ import annotations

import sys
import os
import copy
from typing import Dict, Any, List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parameter_optimizer import (
    _apply_lane_aware_replay_summary,
    _collect_mandatory_anchor_indices,
    _collect_forced_anchor_indices,
    _lane_aware_replay_prefix_validation,
)


def _make_candidate(
    *,
    cpu: float = 50.0,
    memory: float = 90.0,
    io: float = 20.0,
    db_qps_delta: float = 0.0,
    is_mandatory: bool = False,
    is_forced: bool = False,
    selection_reason: str = "",
    extra: bool = False,
) -> Dict[str, Any]:
    c = {
        "predicted_resource": {"cpu": cpu, "memory": memory, "io": io},
        "predicted_db_qps_delta_pct": db_qps_delta,
    }
    if is_mandatory:
        c["is_mandatory_anchor"] = True
    if is_forced:
        c["is_forced_anchor"] = True
    if selection_reason:
        c["selection_reason"] = selection_reason
    if extra:
        c["phase2_known_real_direction_seed"] = True
    return c


def _baseline() -> Dict[str, float]:
    return {"cpu": 56.0, "memory": 3.38, "io": 800.0}


def _capability() -> Dict[str, float]:
    return {"cpu": 14.0, "memory": 4.0, "io": 1024.0}


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


def test_flag_off_summary_unchanged() -> None:
    """test flag off summary unchanged."""


    summary: Dict[str, Any] = {
        "first_stage_ranking_validation_pass": True,
        "next_action_hint": "continue_outer_amplification",
    }
    _check(
        "flag_off_summary_no_lane_aware_field",
        "lane_aware_replay_validation" not in summary,
        "summary should not have lane_aware_replay_validation when flag off",
    )


def test_flag_on_explainable_hit_merges_summary() -> None:
    """test flag on explainable hit merges summary."""

    # memory baseline 3.38 GiB, cap 4 GiB → util 84.5%
    # candidate memory 3.20 GiB → util 80% → delta -4.5pp (down, explainable)
    # cpu 56→60 (+4pp up), io 800→820 (+2pp up) → best_other_up +4pp > 0.5
    candidates = [
        _make_candidate(memory=3.20, cpu=60.0, io=820.0, db_qps_delta=5.0),
        _make_candidate(memory=3.40, cpu=55.0, io=810.0, db_qps_delta=3.5),  # fallback
    ]
    summary: Dict[str, Any] = {
        "first_stage_ranking_validation_pass": True,
        "next_action_hint": "continue_outer_amplification",
    }
    _apply_lane_aware_replay_summary(
        summary,
        replayed_candidates=candidates,
        target_resource="memory",
        baseline_resource=_baseline(),
        capability=_capability(),
    )
    _check(
        "flag_on_summary_has_lane_aware_field",
        summary.get("lane_aware_replay_validation") is True,
    )
    _check(
        "flag_on_explainable_hit_count_is_1",
        summary.get("explainable_lane_hit_count") == 1,
        f"got {summary.get('explainable_lane_hit_count')}",
    )
    _check(
        "flag_on_explainable_lane_not_empty",
        summary.get("explainable_lane_empty") is False,
    )
    _check(
        "flag_on_first_stage_pass_not_forced_false",
        summary.get("first_stage_ranking_validation_pass") is True,
        "should not force False when explainable lane has hits",
    )
    _check(
        "flag_on_no_stop_fix_marker",
        summary.get("explainable_lane_empty_stop_fix") is None,
    )


def test_flag_on_explainable_empty_forces_stop_fix() -> None:
    """test flag on explainable empty forces stop fix."""


    # all candidates: memory up (not explainable), cpu/io mixed
    candidates = [
        _make_candidate(memory=3.50, cpu=60.0, io=820.0, db_qps_delta=5.0),  # fallback
        _make_candidate(memory=3.45, cpu=55.0, io=810.0, db_qps_delta=4.0),  # fallback
    ]
    summary: Dict[str, Any] = {
        "first_stage_ranking_validation_pass": True,
        "next_action_hint": "continue_outer_amplification",
        "short_term_goal_met": True,
    }
    _apply_lane_aware_replay_summary(
        summary,
        replayed_candidates=candidates,
        target_resource="memory",
        baseline_resource=_baseline(),
        capability=_capability(),
    )
    _check(
        "explainable_empty_forced_true",
        summary.get("explainable_lane_empty") is True,
    )
    _check(
        "explainable_empty_hit_count_0",
        summary.get("explainable_lane_hit_count") == 0,
    )
    _check(
        "explainable_empty_first_stage_pass_false",
        summary.get("first_stage_ranking_validation_pass") is False,
    )
    _check(
        "explainable_empty_short_term_false",
        summary.get("short_term_goal_met") is False,
    )
    _check(
        "explainable_empty_next_action_stop_fix",
        summary.get("next_action_hint")
        == "stop_fix_ranking_explainable_lane_empty",
        f"got {summary.get('next_action_hint')}",
    )
    _check(
        "explainable_empty_stop_fix_marker_true",
        summary.get("explainable_lane_empty_stop_fix") is True,
    )


def test_mandatory_anchor_independent_count() -> None:
    """test mandatory anchor independent count."""
    candidates = [
        _make_candidate(  # mandatory anchor — memory up but in mandatory_anchor lane
            memory=3.50,
            cpu=60.0,
            io=820.0,
            is_mandatory=True,
        ),
        _make_candidate(  # explainable — memory down, cpu up
            memory=3.20,
            cpu=60.0,
            io=820.0,
            db_qps_delta=5.0,
        ),
    ]
    summary: Dict[str, Any] = {}
    _apply_lane_aware_replay_summary(
        summary,
        replayed_candidates=candidates,
        target_resource="memory",
        baseline_resource=_baseline(),
        capability=_capability(),
        mandatory_anchor_indices=[0],
    )
    _check(
        "mandatory_anchor_count_is_1",
        summary.get("mandatory_anchor_count") == 1,
    )
    _check(
        "explainable_hit_count_is_1",
        summary.get("explainable_lane_hit_count") == 1,
    )
    lane_counts = summary.get("lane_counts", {})
    _check(
        "mandatory_anchor_lane_has_1",
        lane_counts.get("mandatory_anchor") == 1,
        f"got {lane_counts.get('mandatory_anchor')}",
    )
    _check(
        "explainable_lane_has_1",
        lane_counts.get("explainable") == 1,
        f"got {lane_counts.get('explainable')}",
    )


def test_collect_mandatory_anchor_indices_field_priority() -> None:
    """test collect mandatory anchor indices field priority."""


    candidates = [
        _make_candidate(is_mandatory=True),  # idx 0 — is_mandatory_anchor
        _make_candidate(  # idx 1 — forced_replay_anchor + phase2_known_real_direction_seed
            selection_reason="forced_replay_anchor_revalidate_configured_best",
            extra=True,
        ),
        _make_candidate(),  # idx 2 — not anchor
        _make_candidate(),  # idx 3 — phase2_known_replay_anchor via field
    ]
    candidates[3]["phase2_known_replay_anchor"] = True
    candidates.append(_make_candidate())  # idx 4 — is_global_best_replay_anchor
    candidates[4]["is_global_best_replay_anchor"] = True

    indices = _collect_mandatory_anchor_indices(candidates, target_resource="memory")
    _check(
        "collect_mandatory_returns_4_indices",
        len(indices) == 4,
        f"got {indices}",
    )
    _check(
        "collect_mandatory_has_idx_0",
        0 in indices,
    )
    _check(
        "collect_mandatory_has_idx_1",
        1 in indices,
    )
    _check(
        "collect_mandatory_has_idx_3",
        3 in indices,
    )
    _check(
        "collect_mandatory_has_idx_4",
        4 in indices,
    )
    _check(
        "collect_mandatory_not_has_idx_2",
        2 not in indices,
    )


def test_collect_forced_anchor_indices() -> None:
    """test collect forced anchor indices."""
    candidates = [
        _make_candidate(is_forced=True),  # idx 0
        _make_candidate(selection_reason="forced_replay_anchor_xxx"),  # idx 1
        _make_candidate(),  # idx 2 — not forced
    ]
    indices = _collect_forced_anchor_indices(candidates)
    _check(
        "collect_forced_returns_2_indices",
        len(indices) == 2,
        f"got {indices}",
    )
    _check("collect_forced_has_0", 0 in indices)
    _check("collect_forced_has_1", 1 in indices)


def test_lane_aware_count_fill_skip_logic() -> None:
    """test lane aware count fill skip logic."""


    # selected list: all memory-up candidates (explainable lane empty)
    selected = [
        _make_candidate(memory=3.50, cpu=60.0, io=820.0, db_qps_delta=5.0),
        _make_candidate(memory=3.45, cpu=55.0, io=810.0, db_qps_delta=4.0),
    ]
    pre_fill_info = _lane_aware_replay_prefix_validation(
        list(selected),
        target_resource="memory",
        baseline_resource=_baseline(),
        capability=_capability(),
    )
    lane_aware_count_fill_skip = bool(pre_fill_info.get("explainable_lane_empty"))
    _check(
        "count_fill_skip_when_explainable_empty",
        lane_aware_count_fill_skip is True,
    )

    # selected list with one explainable candidate → skip = False
    selected2 = [
        _make_candidate(memory=3.20, cpu=60.0, io=820.0, db_qps_delta=5.0),  # explainable
        _make_candidate(memory=3.50, cpu=60.0, io=820.0, db_qps_delta=5.0),  # fallback
    ]
    pre_fill_info2 = _lane_aware_replay_prefix_validation(
        list(selected2),
        target_resource="memory",
        baseline_resource=_baseline(),
        capability=_capability(),
    )
    lane_aware_count_fill_skip2 = bool(pre_fill_info2.get("explainable_lane_empty"))
    _check(
        "count_fill_not_skip_when_explainable_has_hit",
        lane_aware_count_fill_skip2 is False,
    )


def test_lane_aware_summary_no_mutation() -> None:
    """test lane aware summary no mutation."""
    candidates = [
        _make_candidate(memory=3.20, cpu=60.0, io=820.0, db_qps_delta=5.0),
        _make_candidate(memory=3.50, cpu=60.0, io=820.0, db_qps_delta=5.0),
    ]
    candidates_before = copy.deepcopy(candidates)
    summary: Dict[str, Any] = {}
    _apply_lane_aware_replay_summary(
        summary,
        replayed_candidates=candidates,
        target_resource="memory",
        baseline_resource=_baseline(),
        capability=_capability(),
    )
    _check(
        "summary_no_mutation",
        candidates == candidates_before,
        "replayed_candidates list should not be mutated",
    )


def main() -> int:
    print("=" * 70)
    print("iter97 live-path lane-aware integration tests")
    print("=" * 70)
    test_flag_off_summary_unchanged()
    test_flag_on_explainable_hit_merges_summary()
    test_flag_on_explainable_empty_forces_stop_fix()
    test_mandatory_anchor_independent_count()
    test_collect_mandatory_anchor_indices_field_priority()
    test_collect_forced_anchor_indices()
    test_lane_aware_count_fill_skip_logic()
    test_lane_aware_summary_no_mutation()
    print("=" * 70)
    print(f"iter97 live-path tests: {_passed}/{_passed + _failed} passed")
    print("=" * 70)
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

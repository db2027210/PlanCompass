#!/usr/bin/env python3
"""Regression coverage for the user-specified interval loop, without a database."""

from __future__ import annotations

import copy
import json
import math
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from algorithm_c_budget_policy import box_identity, complement_boxes
from shell import run_pg_algorithm_c_scene as base
from shell.run_pg_algorithm_c_budgeted_scene import BudgetedLaneController
from shell.test_algorithm_c_recovery_loop import controller


def strict_cfg():
    return {
        "algorithm_c": {"qps_noise_gate": {"enabled": False, "static_floor_pct": 0.0}},
        "binary_plan_tuning": {
            "acceptance_policy": {"resource_capacity": {
                "cpu_cores": 100.0, "memory_gib": 100.0, "io_mib_s": 100.0,
            }},
            "binary_search": {"nested_loop": {"outer_acceptance": {
                "qps_reference_mode": "last_accepted", "qps_threshold_mode": "static_floor",
                "min_db_qps_gain_pct": 0.0, "non_target_bottleneck_threshold_pct": 80.0,
            }}},
        },
    }


def main() -> int:
    # A tighter configured precision must not be rejected by the legacy
    # accepted_target estimate; actual interval widths control this mode.
    convergence = base.resolve_convergence_contract({
        "accepted_target": 1, "normalized_width_threshold": 0.001,
        "budgeted_trial": {"adaptive_search": {}},
    })
    assert convergence["normalized_width_threshold"] == 0.001
    # A subset accept halves only its moved dimensions; the full box is not 1%.
    c, _ = controller()
    c.max_rounds = 100
    c.cfg = strict_cfg()
    c.trial["visited_regions"] = []
    c.state["canonical_baseline"]["resources"].update(memory=20.0, io=20.0)
    params = copy.deepcopy(c.state["incumbent_params"])
    params["seq_page_cost"] *= 2.0
    evaluation = {
        "raw": {"candidate_ranking": {"optimized_params": params, "active_subset": ["seq_page_cost"]}},
        "normalized": {"db_qps": 101.0, "status": "ok", "resources": {"cpu": 3.0, "memory": 20.0, "io": 20.0}},
        "decision": {"accepted": True, "qps_up": True, "acceptance_tier": "secondary"},
    }
    BudgetedLaneController.accept(c, evaluation, 1)
    assert c.state["accepted"] == 1 and c.state["incumbent_params"] == params
    assert c.state["normalized_width"] == 1.0
    assert math.isclose(c.trial["actual_parameter_widths"]["seq_page_cost"], 0.5)
    assert c.interval_progress_width(100) == 1.0  # Counts cannot fake interval precision.

    # Reopening an unexplored region increases the actual width; a rejected
    # exploration returns to its parent before the due incumbent bisection.
    r, _ = controller()
    initial = r.box()
    r.ranges, _ = base.binary_shrink_parameter_ranges(r.ranges, r.state["incumbent_params"], base.ACTIVE_PARAMS)
    parent = r.box()
    region = complement_boxes(initial, parent)[0]
    r.trial["region_queue"] = [{"id": box_identity(region), "box": region, "optimistic_resource_gain": 0.5}]
    r.trial["consecutive_rejected_batches"] = 1
    r.begin_first_miss_expansion(2)
    assert r.state["normalized_width"] == 1.0
    assert r.trial["consecutive_rejected_batches"] == 1
    assert base.parameter_vector_range_audit(r.state["incumbent_params"], r.ranges)["passed"]
    r.restore_rejected_expansion()
    assert r.box() == parent
    incumbent = copy.deepcopy(r.state["incumbent_params"])
    r.trial["consecutive_rejected_batches"] = 2
    r.shrink_after_two_misses()
    assert math.isclose(r.state["normalized_width"], 0.25)
    assert r.state["incumbent_params"] == incumbent and r.state["accepted"] == 0
    assert r.trial["consecutive_rejected_batches"] == 0

    # Detect a broken/no-op contraction immediately, without a replay/time cap.
    broken, _ = controller()
    broken.trial["consecutive_rejected_batches"] = 2
    with mock.patch.object(base.LaneController, "refine_ranges_around_current_incumbent", return_value={}):
        try:
            broken.shrink_after_two_misses()
        except RuntimeError as exc:
            assert "no_progress" in str(exc)
        else:
            raise AssertionError("a no-op shrink was silently accepted")
    assert broken.trial["consecutive_rejected_batches"] == 2

    # Newly seen plans affect exploration, but two valid misses still accumulate.
    novel, _ = controller()
    for plan in ("new-plan-a", "new-plan-b"):
        candidates = [{"three_level_fingerprint": {"plan_level": plan}}]
        novel.record_adaptive_batch(candidates, [{"status": "ok", "candidate_ranking": candidates[0]}], None)
    assert novel.trial["consecutive_rejected_batches"] == 2
    assert novel.next_adaptive_decision().action == "shrink_incumbent"

    # Expansion prioritizes unseen plans without discarding same-plan identities.
    diversity, _ = controller()
    diversity.trial["active_region_mode"] = "first_miss_expansion"
    diversity.trial["seen_search_plan_fingerprints"] = ["old-plan"]
    pool = [{"optimized_params": {"p": index}, "three_level_fingerprint": {"plan_level": plan}}
            for index, plan in enumerate(("old-plan", "new-a", "new-a", "new-b"))]
    with mock.patch.object(base.LaneController, "select_outer_admitted_candidates",
                           return_value=(pool, {"selected": []})):
        selected, audit = BudgetedLaneController.select_outer_admitted_candidates(
            diversity, pool, round_index=2, batch_kind="search", limit=4,
        )
    assert [row["optimized_params"]["p"] for row in selected] == [1, 3, 0, 2]
    assert audit["qps_used_for_selection"] is False

    # Disabled noise cannot swallow a real, arbitrarily small positive QPS gain.
    cfg = strict_cfg()
    assert base.configured_static_qps_floor_pct(cfg) == 0.0
    assert base.formal_outer_qps_progress_contract(cfg)["passed"]
    reference = {"db_qps": 100.0, "resources": {"cpu": 40.0, "memory": 20.0, "io": 20.0}}
    for qps, accepted in ((100.000001, True), (100.0, False), (99.999999, False)):
        candidate = {"db_qps": qps, "status": "ok", "resources": {"cpu": 41.0, "memory": 20.0, "io": 20.0},
                     "qps_noise_model": {"ready": True, "effective_min_gain_pct": 50.0}}
        decision = base.outer_semantic_acceptance_details(candidate, reference, reference, target="cpu", cfg=cfg)
        assert decision["accepted"] is accepted, decision
        if accepted:
            assert decision["acceptance_tier"] == "fallback"
    higher_reference = {**reference, "db_qps": 110.0}
    candidate["db_qps"] = 105.0
    assert not base.outer_semantic_acceptance_details(candidate, higher_reference, reference, target="cpu", cfg=cfg)["accepted"]
    for resources, tier in (
        ({"cpu": 39.0, "memory": 21.0, "io": 20.0}, "strict"),
        ({"cpu": 39.0, "memory": 20.0, "io": 20.0}, "secondary"),
        ({"cpu": 41.0, "memory": 20.0, "io": 20.0}, "fallback"),
    ):
        candidate["resources"] = resources
        decision = base.outer_semantic_acceptance_details(candidate, reference, reference, target="cpu", cfg=cfg)
        assert decision["accepted"] and decision["accepted_shrink"], decision
        assert decision["acceptance_tier"] == tier
    candidate["resources"] = {"cpu": 39.0, "memory": 81.0, "io": 20.0}
    assert not base.outer_semantic_acceptance_details(candidate, reference, reference, target="cpu", cfg=cfg)["accepted"]

    # A no-improvement search can finish at precision, retaining the baseline
    # honestly and invoking no final-validation replay.
    baseline, _ = controller()
    baseline.trial["feasible_archive"] = {}
    baseline.algorithm_cfg = {"scene": "xp_cpu"}
    with tempfile.TemporaryDirectory(prefix="c-interval-result-") as temporary:
        baseline.output_dir = Path(temporary)
        baseline.finish_search_only("adaptive_convergence")
        result = json.loads((baseline.output_dir / baseline.execution_role.result_filename).read_text())
        assert result["status"] == "search_completed"
        assert result["trial"]["selection"]["selection_source"] == "baseline"
        assert result["trial"]["selection"]["observed_qps"] == 100.0
        assert result["trial"]["validation"]["physical_replays"] == 0
    print("PASS: actual widths, region expansion, no-op detection, strict QPS and baseline retention", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

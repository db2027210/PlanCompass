#!/usr/bin/env python3
"""Resume Outer 7 after repairing subset shrink, diversity, and plan risk."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import (
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v20
    as predecessor,
)
from scripts import (
    run_mysql_four_scene_algorithm_b_independent_lane_failure_isolation_core_v21
    as base,
)
from scripts import mysql_algorithm_b_tpch_full_suite_plan_ranker_v3 as plan_ranker


alg = base.alg
runtime = base.runtime
SOURCE = predecessor.SOURCE
INITIAL_SOURCE = (
    base.HISTORY / "v8_formal_independent_outer_rank3_identity_resume_v63_20260818_attempt001"
)
INTERRUPTED_V20 = (
    base.HISTORY
    / "v20_formal_batchedplan_allworker_drain_outer7_20260818_attempt018"
)
INTERRUPTED_V20_DRAIN = (
    INTERRUPTED_V20 / "interrupted_outer7_rank3_bidirectional_plan_risk_drain.json"
)
SCHEDULER_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_b_parallel_221_20260818_v21"
    / "algorithm_b_parallel_221_contract.json"
)


def _regular(path: Path) -> dict[str, Any]:
    return dict(predecessor.v12._regular_json(path))


def reconstruct_subset_only_ranges() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    initial_state = _regular(INITIAL_SOURCE / "run_state.json")
    ranges = copy.deepcopy(dict(initial_state["ranges_by_scene"]))
    events: list[dict[str, Any]] = []
    for outer in range(1, 7):
        rankings_path = SOURCE / f"outer_{outer:03d}/predicted_rankings.json"
        rankings = _regular(rankings_path).get("selected_top_k") or {}
        for decision_path in sorted((SOURCE / f"outer_{outer:03d}").glob("*.decision.json")):
            decision = _regular(decision_path)
            if decision.get("accepted") is not True:
                continue
            scene = str(decision["scene"])
            chosen_rank = decision.get("selected_outer_candidate_rank")
            if chosen_rank is None:
                best = [
                    row for row in (decision.get("evaluations") or [])
                    if row.get("unique_small_loop_best") is True
                ]
                if len(best) != 1:
                    raise RuntimeError(
                        f"Outer {outer} {scene} historical selected candidate differs"
                    )
                chosen_rank = int(best[0]["rank"])
            chosen_rank = int(chosen_rank)
            candidate_rows = [
                row for row in (rankings.get(scene) or [])
                if int(row["rank"]) == chosen_rank
            ]
            if len(candidate_rows) != 1:
                raise RuntimeError(
                    f"Outer {outer} {scene} ranking identity differs"
                )
            candidate = candidate_rows[0]
            subset = [str(name) for name in candidate["active_subset"]]
            if not subset or not set(subset).issubset(ranges[scene]):
                raise RuntimeError(
                    f"Outer {outer} {scene} accepted active subset differs"
                )
            before = copy.deepcopy(ranges[scene])
            ranges[scene] = alg.shrink_ranges(
                ranges[scene], candidate["optimized_params"], subset,
            )
            events.append({
                "outer_attempt": outer,
                "scene": scene,
                "selected_rank": chosen_rank,
                "accepted_candidate_active_subset": subset,
                "accepted_params": copy.deepcopy(candidate["optimized_params"]),
                "ranges_before": before,
                "ranges_after": copy.deepcopy(ranges[scene]),
                "decision_path": str(decision_path),
                "decision_sha256": alg.sha(decision_path),
                "rankings_path": str(rankings_path),
                "rankings_sha256": alg.sha(rankings_path),
            })
    if [(row["outer_attempt"], row["scene"], row["selected_rank"]) for row in events] != [
        (4, "hybench_xp_cpu", 3),
        (4, "job_memory", 4),
        (6, "hybench_xp_io", 3),
    ]:
        raise RuntimeError("historical accepted subset reconstruction differs")
    return ranges, events


def repair_outer7_ranges(**kwargs: Any) -> None:
    if int(kwargs["outer_attempt"]) != 7:
        return
    state = kwargs["state"]
    ranges_by_scene = kwargs["ranges_by_scene"]
    output = Path(kwargs["output"])
    source_state = _regular(SOURCE / "run_state.json")
    if (
        int(state.get("outer_attempt") or 0) != 6
        or state.get("accepted_rounds") != source_state.get("accepted_rounds")
        or ranges_by_scene != source_state.get("ranges_by_scene")
    ):
        raise RuntimeError("Outer 7 pre-repair state differs from clean source")
    corrected, events = reconstruct_subset_only_ranges()
    if corrected == ranges_by_scene:
        raise RuntimeError("subset-only range repair unexpectedly has no effect")
    before = copy.deepcopy(ranges_by_scene)
    ranges_by_scene.clear()
    ranges_by_scene.update(copy.deepcopy(corrected))
    recovery_path = output / "outer_007/subset_only_range_recovery.json"
    recovery_path.parent.mkdir(parents=True, exist_ok=True)
    alg.atomic(recovery_path, {
        "schema_version": "mysql_algorithm_b_subset_only_range_recovery_v1",
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "effective_from_outer": 7,
        "historical_measurements_or_decisions_rewritten": False,
        "accepted_round_credit_changed": False,
        "recovery_reason": "untested_active_parameters_were_shrunk_by_subset_candidates",
        "old_policy": "all_active_parameters_shrunk_per_accepted_candidate",
        "new_policy": "accepted_candidate_active_subset_only",
        "ranges_before": before,
        "ranges_after": corrected,
        "accepted_events_replayed": events,
        "source_run_root": str(SOURCE),
        "source_state_sha256": alg.sha(SOURCE / "run_state.json"),
    })
    state["ranges_by_scene"] = copy.deepcopy(corrected)
    state["subset_only_range_recovery"] = {
        "status": "PASS",
        "effective_from_outer": 7,
        "path": str(recovery_path),
        "sha256": alg.sha(recovery_path),
    }


def state_document_v21(run_id: str) -> dict[str, Any]:
    document = predecessor.state_document_v20(run_id)
    document["technical_continuation"].update({
        "kind": "outer7_subset_shrink_diversity_bidirectional_plan_risk_resume_v21",
        "outer7_v20_partial_measurements_reused": False,
        "interrupted_v20_root": str(INTERRUPTED_V20),
        "interrupted_v20_state_sha256": alg.sha(INTERRUPTED_V20 / "run_state.json"),
        "interrupted_v20_drain_path": str(INTERRUPTED_V20_DRAIN),
        "interrupted_v20_drain_sha256": alg.sha(INTERRUPTED_V20_DRAIN),
        "historical_measurements_or_decisions_rewritten": False,
        "accepted_round_credit_changed": False,
    })
    document["candidate_diversity_release"] = {
        "effective_from_outer": 7,
        "selector_path": str(ROOT / "scripts/mysql_algorithm_b_candidate_diversity_v1.py"),
        "selector_sha256": alg.sha(
            ROOT / "scripts/mysql_algorithm_b_candidate_diversity_v1.py"
        ),
        "qps_used": False,
        "inner_candidate_score_modified": False,
        "top_k_parameter_space_diversity": True,
    }
    document["range_shrink_release"] = {
        "effective_from_outer": 7,
        "policy": "accepted_candidate_active_subset_only_v1",
        "untested_active_parameter_ranges_shrunk": False,
        "historical_range_state_reconstructed_before_outer7": True,
    }
    document["tpch_full_suite_plan_release"].update({
        "ranker_path": str(Path(plan_ranker.__file__).resolve()),
        "ranker_sha256": alg.sha(Path(plan_ranker.__file__).resolve()),
        "extreme_plan_handling": (
            "symmetric_aggregate_and_per_query_uncapped_soft_discount"
        ),
    })
    document["controller_source_path"] = str(Path(__file__).resolve())
    document["controller_source_sha256"] = alg.sha(Path(__file__).resolve())
    return document


def validate_contract() -> dict[str, Any]:
    if not SCHEDULER_CONTRACT.is_file() or SCHEDULER_CONTRACT.is_symlink():
        raise RuntimeError(f"sealed v21 scheduler contract absent: {SCHEDULER_CONTRACT}")
    contract = alg.load(SCHEDULER_CONTRACT)
    if (
        contract.get("release_version") != "v21"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or int(contract.get("candidate_parallelism") or 0) != 2
        or (contract.get("range_shrink") or {}).get("policy")
        != "accepted_candidate_active_subset_only_v1"
        or (contract.get("candidate_diversity") or {}).get("qps_used") is not False
        or (contract.get("tpch_plan_ranking") or {}).get("symmetric_risk") is not True
    ):
        raise RuntimeError("parallel v21 continuation contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding["path"])
        if (
            not path.is_file() or path.is_symlink()
            or alg.sha(path) != str(binding["sha256"])
        ):
            raise RuntimeError(f"v21 source binding differs: {path}")
    return contract


def install() -> None:
    predecessor.install()
    base.PREPARED = predecessor.PREPARED
    base.ACCOUNTS = predecessor.ACCOUNTS
    base.FreshBaselineObjective.score = predecessor.core._score_with_resource_payload
    runtime.alg.measure_batch = predecessor.independent_measurement.measure_batch
    runtime._build_single_candidate_assignments = predecessor.core._build_assignments_221
    runtime._measure_batch_with_fair_coordination = predecessor.measure_resume
    plan_ranker.install()
    base.rank_scene = plan_ranker.rank_scene
    base.state_document = state_document_v21
    base.OUTER_BOUNDARY_STATE_TRANSFORM = repair_outer7_ranges
    alg.atomic = predecessor.atomic_with_historical_identity


def main() -> int:
    validate_contract()
    predecessor.validate_source()
    predecessor.predecessor.validate_recovery_and_plan_capture()
    predecessor.validate_batched_plan_capture()
    reconstruct_subset_only_ranges()
    install()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Resume after Outer 10 with real Top-5 online resource-model correction."""

from __future__ import annotations

import copy
from pathlib import Path
import re
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import (
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v21
    as predecessor,
)
from scripts import (
    run_mysql_four_scene_algorithm_b_independent_lane_failure_isolation_core_v21
    as old_base,
)
from scripts import (
    run_mysql_four_scene_algorithm_b_independent_lane_failure_isolation_core_v22
    as base,
)
from scripts import mysql_algorithm_b_tpch_full_suite_plan_ranker_v3 as plan_ranker


alg = base.alg
runtime = base.runtime
v20 = predecessor.predecessor
v12 = v20.v12
scheduler_core = v20.core
SOURCE = (
    base.HISTORY
    / "v21_formal_subset_shrink_diverse_symmetric_risk_outer7_20260818_attempt019"
)
COMPLETED_OUTERS = 10
EFFECTIVE_OUTER = COMPLETED_OUTERS + 1
INITIAL_SOURCE = (
    base.HISTORY / "v8_formal_independent_outer_rank3_identity_resume_v63_20260818_attempt001"
)
SCHEDULER_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_b_parallel_221_20260818_v22"
    / "algorithm_b_parallel_221_contract.json"
)
REUSED_BATCHES = {
    (outer, rank): SOURCE / f"outer_{outer:03d}/top5_batch_{rank}/batch_result.json"
    for outer in range(1, COMPLETED_OUTERS + 1)
    for rank in range(1, base.FIXED_TOP_K + 1)
}
CLEAN_BOUNDARY_DRAIN = SOURCE / "stopped_after_outer10_online_model_v22_drain.json"
_ORIGINAL_ATOMIC = v20._ORIGINAL_ATOMIC


def _regular(path: Path) -> dict[str, Any]:
    return dict(v12._regular_json(path))


def validate_source() -> dict[str, Any]:
    state = _regular(SOURCE / "run_state.json")
    if (
        state.get("algorithm") != "B"
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or int(state.get("outer_attempt") or 0) < COMPLETED_OUTERS
        or any(bool(value) for value in (state.get("converged") or {}).values())
    ):
        raise RuntimeError("v22 continuation source state differs")
    for outer in range(1, COMPLETED_OUTERS + 1):
        rankings_path = SOURCE / f"outer_{outer:03d}/predicted_rankings.json"
        rankings = _regular(rankings_path)
        if set(rankings.get("selected_top_k") or {}) != set(base.SCENES):
            raise RuntimeError(f"v22 source outer {outer} ranking scenes differ")
        decisions = sorted((SOURCE / f"outer_{outer:03d}").glob("*.decision.json"))
        if len(decisions) != len(base.SCENES):
            raise RuntimeError(f"v22 source outer {outer} decision cardinality differs")
        for path in decisions:
            _regular(path)
        for rank in range(1, base.FIXED_TOP_K + 1):
            batch = _regular(REUSED_BATCHES[(outer, rank)])
            if not (batch.get("measurements") or {}):
                raise RuntimeError(
                    f"v22 source batch empty: outer={outer}, rank={rank}"
                )
    return state


def rank_scene_resume(**kwargs: Any) -> dict[str, Any]:
    outer = int(kwargs["round_index"])
    scene = str(kwargs["scene"])
    if outer <= COMPLETED_OUTERS:
        rankings = _regular(
            SOURCE / f"outer_{outer:03d}/predicted_rankings.json"
        )
        source_audit = dict((rankings.get("scene_audit") or {}).get(scene) or {})
        source_top5 = copy.deepcopy(
            list((rankings.get("selected_top_k") or {}).get(scene) or [])
        )
        if len(source_top5) != base.FIXED_TOP_K:
            raise RuntimeError(
                f"v22 source outer {outer} {scene} Top-5 cardinality differs"
            )
        if source_audit.get("selected_top5") != source_top5:
            raise RuntimeError(
                f"v22 source outer {outer} {scene} scene audit differs"
            )
        source_audit["historical_ranking_restored_verified"] = True
        source_audit["historical_ranking_source_path"] = str(
            SOURCE / f"outer_{outer:03d}/predicted_rankings.json"
        )
        source_audit["historical_ranking_source_sha256"] = alg.sha(
            SOURCE / f"outer_{outer:03d}/predicted_rankings.json"
        )
        return source_audit
    return plan_ranker.rank_scene(**kwargs)


def measure_resume(**kwargs: Any):
    output = Path(kwargs["output"])
    match = re.search(r"outer_(\d+)/top5_batch_(\d+)$", str(output))
    if match is not None:
        key = (int(match.group(1)), int(match.group(2)))
        source = REUSED_BATCHES.get(key)
        if source is not None:
            destination = output / "batch_result.json"
            old_base._mirror_batch(source, destination)
            v12._mirror_supporting_wave_evidence(source, destination)
            document = alg.load(destination)
            state = kwargs["state"]
            state["stage"] = f"{kwargs['stage_prefix']}_RESTORED_VERIFIED"
            state["scene_independent_continuation_source"] = str(SOURCE)
            state["scene_independent_continuation_reused_batches"] = [
                [outer, rank]
                for outer, rank in sorted(REUSED_BATCHES)
                if (outer, rank) <= key
            ]
            alg.atomic(Path(kwargs["state_path"]), state)
            return copy.deepcopy(dict(document["measurements"])), state
    return scheduler_core._measure_221(**kwargs)


def atomic_with_historical_identity(path: Path, value: Mapping[str, Any]) -> None:
    historical = {f"outer_{outer:03d}" for outer in range(1, COMPLETED_OUTERS + 1)}
    if path.name.endswith(".decision.json") and path.parent.name in historical:
        source = _regular(SOURCE / path.parent.name / path.name)
        if v12._decision_semantics(value) != v12._decision_semantics(source):
            raise RuntimeError(
                f"{path.parent.name} reconstructed decision differs: {path.name}"
            )
    if path.name == "predicted_rankings.json" and path.parent.name in historical:
        source = _regular(SOURCE / path.parent.name / path.name)
        if (
            base.ranking_identity(value.get("selected_top_k") or {})
            != old_base.ranking_identity(source.get("selected_top_k") or {})
        ):
            raise RuntimeError(
                f"{path.parent.name} recomputed Top-5 differs from source"
            )
    _ORIGINAL_ATOMIC(path, value)


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
    source_state = _regular(predecessor.SOURCE / "run_state.json")
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


def bootstrap_historical_online_model(**kwargs: Any) -> None:
    if int(kwargs["outer_attempt"]) != EFFECTIVE_OUTER:
        return
    state = kwargs["state"]
    models = kwargs["models"]
    output = Path(kwargs["output"])
    source_state = _regular(SOURCE / "run_state.json")
    for key in (
        "accepted_rounds", "rejected_outer_attempts", "params_by_scene",
        "ranges_by_scene", "incumbents",
    ):
        if state.get(key) != source_state.get(key):
            raise RuntimeError(f"Outer {EFFECTIVE_OUTER} source state differs: {key}")

    scene_updates: dict[str, list[dict[str, Any]]] = {
        scene: [] for scene in base.SCENES
    }
    source_decisions: list[dict[str, Any]] = []
    for outer in range(1, COMPLETED_OUTERS + 1):
        for scene in base.SCENES:
            path = SOURCE / f"outer_{outer:03d}/{scene}.decision.json"
            decision = _regular(path)
            update = base.update_resource_model_from_formal_evaluations(
                scene=scene,
                model=models[scene],
                evaluations=list(decision.get("evaluations") or []),
                outer_attempt=outer,
            )
            scene_updates[scene].append(update)
            source_decisions.append({
                "outer_attempt": outer,
                "scene": scene,
                "path": str(path),
                "sha256": alg.sha(path),
            })
    bootstrap_path = (
        output / f"outer_{EFFECTIVE_OUTER:03d}"
        / "historical_online_resource_model_bootstrap.json"
    )
    bootstrap_path.parent.mkdir(parents=True, exist_ok=True)
    alg.atomic(bootstrap_path, {
        "schema_version": (
            "mysql_algorithm_b_historical_online_resource_model_bootstrap_v1"
        ),
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "effective_from_outer": EFFECTIVE_OUTER,
        "completed_source_outers": list(range(1, COMPLETED_OUTERS + 1)),
        "scene_updates": scene_updates,
        "source_decisions": source_decisions,
        "historical_measurements_or_decisions_rewritten": False,
        "accepted_round_credit_changed": False,
        "qps_used_as_model_input_feature": False,
        "qps_used_for_resource_target": False,
        "qps_used_for_resource_prediction_gradient_or_score": False,
        "inner_candidate_score_modified": False,
    })
    state["online_resource_model_bootstrap"] = {
        "status": "PASS",
        "effective_from_outer": EFFECTIVE_OUTER,
        "path": str(bootstrap_path),
        "sha256": alg.sha(bootstrap_path),
        "added_count_by_scene": {
            scene: sum(int(row["added_count"]) for row in updates)
            for scene, updates in scene_updates.items()
        },
    }


def outer_boundary_v22(**kwargs: Any) -> None:
    repair_outer7_ranges(**kwargs)
    bootstrap_historical_online_model(**kwargs)


def state_document_v22(run_id: str) -> dict[str, Any]:
    document = predecessor.state_document_v21(run_id)
    document["technical_continuation"].update({
        "kind": "post_outer10_online_resource_model_correction_resume_v22",
        "source_run_root": str(SOURCE),
        "source_state_sha256": alg.sha(SOURCE / "run_state.json"),
        "completed_outer_decisions_reconstructed": list(
            range(1, COMPLETED_OUTERS + 1)
        ),
        "outer11_fresh_candidate_ranks_required": [1, 2, 3, 4, 5],
        "clean_boundary_drain_path": str(CLEAN_BOUNDARY_DRAIN),
        "clean_boundary_drain_sha256": alg.sha(CLEAN_BOUNDARY_DRAIN),
        "historical_measurements_or_decisions_rewritten": False,
        "accepted_round_credit_changed": False,
    })
    document["online_resource_model_update_release"] = {
        "release_version": "v22",
        "effective_from_outer": EFFECTIVE_OUTER,
        "historical_valid_top5_bootstrap_outers": list(
            range(1, COMPLETED_OUTERS + 1)
        ),
        "valid_formal_top5_resources_added_after_each_outer": True,
        "accepted_and_rejected_valid_measurements_used": True,
        "qps_used_as_model_input_feature": False,
        "qps_used_for_resource_target": False,
        "qps_used_for_resource_prediction_gradient_or_score": False,
        "inner_candidate_score_modified": False,
        "algorithm_c_state_read": False,
        "core_path": str(Path(base.__file__).resolve()),
        "core_sha256": alg.sha(Path(base.__file__).resolve()),
    }
    document["controller_source_path"] = str(Path(__file__).resolve())
    document["controller_source_sha256"] = alg.sha(Path(__file__).resolve())
    return document


def validate_contract() -> dict[str, Any]:
    if not SCHEDULER_CONTRACT.is_file() or SCHEDULER_CONTRACT.is_symlink():
        raise RuntimeError(f"sealed v22 scheduler contract absent: {SCHEDULER_CONTRACT}")
    contract = alg.load(SCHEDULER_CONTRACT)
    if (
        contract.get("release_version") != "v22"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or int(contract.get("candidate_parallelism") or 0) != 2
        or (contract.get("continuation") or {}).get(
            "historical_predicted_rankings_restored_verified_without_recomputation"
        ) is not True
        or (contract.get("range_shrink") or {}).get("policy")
        != "accepted_candidate_active_subset_only_v1"
        or (contract.get("candidate_diversity") or {}).get("qps_used") is not False
        or (contract.get("tpch_plan_ranking") or {}).get("symmetric_risk") is not True
        or (contract.get("online_resource_model_update") or {}).get(
            "effective_from_outer"
        ) != EFFECTIVE_OUTER
        or (contract.get("online_resource_model_update") or {}).get(
            "qps_used_for_resource_prediction_gradient_or_score"
        ) is not False
    ):
        raise RuntimeError("parallel v22 continuation contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding["path"])
        if (
            not path.is_file() or path.is_symlink()
            or alg.sha(path) != str(binding["sha256"])
        ):
            raise RuntimeError(f"v22 source binding differs: {path}")
    return contract


def install() -> None:
    v20.install()
    base.PREPARED = v20.PREPARED
    base.ACCOUNTS = v20.ACCOUNTS
    base.FreshBaselineObjective.score = scheduler_core._score_with_resource_payload
    runtime.alg.measure_batch = v20.independent_measurement.measure_batch
    runtime._build_single_candidate_assignments = scheduler_core._build_assignments_221
    runtime._measure_batch_with_fair_coordination = measure_resume
    plan_ranker.install()
    base.rank_scene = rank_scene_resume
    base.state_document = state_document_v22
    base.OUTER_BOUNDARY_STATE_TRANSFORM = outer_boundary_v22
    base.ONLINE_RESOURCE_MODEL_UPDATE_EFFECTIVE_OUTER = EFFECTIVE_OUTER
    alg.atomic = atomic_with_historical_identity


def main() -> int:
    validate_contract()
    validate_source()
    v20.validate_source()
    v20.predecessor.validate_recovery_and_plan_capture()
    v20.validate_batched_plan_capture()
    reconstruct_subset_only_ranges()
    install()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())

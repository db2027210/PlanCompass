#!/usr/bin/env python3
"""Seal IO-C Outer-5 using its Top4 fallback-wave evidence."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mysql_algorithm_c_plan_replay_pruning import record_outcomes
from scripts import build_mysql_algorithm_c_independent_round_boundary_checkpoint_v1 as base


HISTORY = ROOT / "history/mysql_four_scene_20260809"
SOURCE = HISTORY / "v225_to_c_top4_io_xp4_20260826_attempt020_xp_io"
OUTPUT = HISTORY / "v250_to_c_top4_io_outer5_checkpoint_20260826"
SCENE = "hybench_xp_io"


def build() -> dict:
    state_path = SOURCE / "run_state.json"
    state = base.base.alg.load(state_path)
    prior_path = Path(str(state.get("checkpoint_path") or "")).resolve()
    prior = base.verifier.verify_checkpoint(prior_path)
    outer = SOURCE / "outer_005"
    incomplete = SOURCE / "outer_006"
    ranking_path = outer / "predicted_rankings.json"
    companion_path = outer / "companion_audit.json"
    decision_path = outer / f"{SCENE}.decision.json"
    decision = base.base.alg.load(decision_path)
    companion = base.base.alg.load(companion_path)
    rows = [base.mapping(row, "decision evaluation") for row in decision["evaluations"]]
    if (
        state.get("algorithm") != "C"
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or not str(state.get("error") or "").startswith(
            "CalledProcessError: Command '['mysql',"
        )
        or state.get("active_scenes") != [SCENE]
        or int(state.get("outer_attempt") or 0) != 5
        or int(prior.get("completed_outer_attempts") or 0) != 4
        or decision.get("scene") != SCENE
        or int(decision.get("outer_attempt") or 0) != 5
        or len(rows) != 2
        or sorted(int(row.get("rank") or 0) for row in rows) != [1, 2]
        or companion.get("status") != "PASS"
        or companion.get("full_workload_contract_preserved") is not True
        or not incomplete.is_dir()
        or list(incomplete.glob("*.decision.json"))
        or not (incomplete / "top5_batch_1/batch_result.json").is_file()
        or (incomplete / "top5_batch_2/batch_result.json").exists()
    ):
        raise RuntimeError("v225 Top4 IO Outer-5 checkpoint boundary differs")

    reconstructed = copy.deepcopy(base.mapping(
        prior.get("reconstructed_state"), "prior reconstructed state",
    ))
    baseline_binding = base.mapping(
        state.get("formal_incumbent_baseline"), "formal incumbent baseline",
    )
    baseline_path = Path(str(baseline_binding["path"])).resolve()
    if (
        baseline_binding.get("status") != "PASS"
        or base.base.alg.sha(baseline_path) != baseline_binding.get("sha256")
    ):
        raise RuntimeError("v225 formal baseline binding differs")
    reconstructed["incumbents"][SCENE] = copy.deepcopy(
        base.mapping(baseline_binding["incumbents"][SCENE], "baseline incumbent")
    )
    reconstructed["full_plan_replay_history_v2"][SCENE] = record_outcomes(
        reconstructed["full_plan_replay_history_v2"][SCENE],
        scene=SCENE, outer_round=5,
        incumbent_qps=float(reconstructed["incumbents"][SCENE]["db_qps"]),
        evaluations=rows,
    )
    moved = base.moved_accepted_rows(
        rows,
        incumbent_params=reconstructed["params_by_scene"][SCENE],
        active_params=list(reconstructed["active_by_scene"][SCENE]),
    )
    if decision.get("accepted") is not True or len(moved) != 1:
        raise RuntimeError("v225 Outer-5 accepted movement differs")
    chosen, _chosen_decision, subset = moved[0]
    reconstructed["params_by_scene"][SCENE] = copy.deepcopy(chosen["params"])
    reconstructed["incumbents"][SCENE] = {
        key: copy.deepcopy(chosen[key])
        for key in ("db_qps", "p95_ms", "p99_ms", "resource_means")
    }
    reconstructed["ranges_by_scene"][SCENE] = base.base.alg.shrink_ranges(
        reconstructed["ranges_by_scene"][SCENE],
        reconstructed["params_by_scene"][SCENE], subset,
    )
    reconstructed["accepted_rounds"][SCENE] += 1
    reconstructed["replayed_param_keys_by_scene"][SCENE] = []
    tier = base.mapping(decision.get("tier_progress"), "tier progress")
    convergence = base.mapping(decision.get("interval_convergence"), "convergence")
    reconstructed["acceptance_tier"][SCENE] = str(tier["acceptance_tier"])
    reconstructed["consecutive_failed_rounds"][SCENE] = int(
        tier["consecutive_failed_rounds"]
    )
    reconstructed["interval_stable_rounds"][SCENE] = int(convergence["stable_rounds"])
    reconstructed["stable_intervals"][SCENE] = copy.deepcopy(
        convergence.get("stable_interval")
    )
    reconstructed["converged"][SCENE] = bool(convergence["converged"])
    reconstructed["outer_attempt"] = 5
    for field in (
        "accepted_rounds", "rejected_outer_attempts", "acceptance_tier",
        "consecutive_failed_rounds", "interval_stable_rounds",
        "stable_intervals", "converged", "full_plan_replay_history_v2",
    ):
        if reconstructed[field][SCENE] != state[field][SCENE]:
            raise RuntimeError(f"v225 reconstructed {field} differs")
    if int(decision["accepted_rounds"]) != 3 or int(decision["rejected_outer_attempts"]) != 2:
        raise RuntimeError("v225 Outer-5 counters differ")

    batch_paths = sorted(outer.rglob("batch_result.json"))
    if len(batch_paths) != 4:
        raise RuntimeError("v225 Outer-5 physical batch evidence differs")
    evidence = {
        "source_state": base.bound(state_path, "source state"),
        "source_checkpoint": base.bound(prior_path, "source checkpoint"),
        "baseline": base.bound(baseline_path, "formal baseline"),
        "outers": {
            "5": {
                "predicted_rankings": base.bound(ranking_path, "predicted rankings"),
                "companion_audit": base.bound(companion_path, "companion audit"),
                "batches": [base.bound(path, "physical batch") for path in batch_paths],
                "decisions": {SCENE: base.bound(decision_path, "outer decision")},
            }
        },
    }
    document = {
        "schema_version": base.base.CONTRACT, "status": "PREPARED",
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "source_run_root": str(SOURCE), "completed_outer_attempts": 5,
        "workload_started": False, "fresh_r0_complete": False,
        "formal_outer_started": False,
        "freeze_path": state["freeze_path"], "freeze_sha256": state["freeze_sha256"],
        "resource_model_contract_path": state["resource_model_contract_path"],
        "resource_model_contract_sha256": state["resource_model_contract_sha256"],
        "reconstructed_state": reconstructed, "evidence": evidence,
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": base.base.alg.sha(Path(__file__).resolve()),
        "independent_scene_boundary": {
            "scene": SCENE, "completed_outer_attempts": 5,
            "discarded_incomplete_outer": 6, "completed_rounds_preserved": True,
        },
        "top4_physical_wave_boundary": {
            "candidate_batches": [[1, 2, 3, 4], [5]],
            "outer5_fallback_candidate_ranks": [1, 2],
            "outer6_completed_candidate_ranks_preserved_separately": [1, 2, 3, 4],
            "accepted_or_rejected_state_credit_increment": 0,
        },
    }
    document["checkpoint_sha256"] = base.base._canonical_sha256(document)
    return document


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh checkpoint output required: {OUTPUT}")
    OUTPUT.mkdir(parents=True, mode=0o700)
    path = OUTPUT / "prepared_binding.json"
    base.base.alg.atomic(path, build())
    base.verifier.verify_checkpoint(path)
    print(json.dumps({
        "status": "PREPARED", "checkpoint": str(path),
        "completed_outer_attempts": 5, "scene": SCENE,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

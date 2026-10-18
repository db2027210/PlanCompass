#!/usr/bin/env python3
"""Seal IO-C at completed Outer-6 and discard diagnostic-only Outer-7 work."""

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
SOURCE = HISTORY / "v256_to_c_top4_io_outer6_partial_20260826_attempt023_xp_io"
PRIOR = HISTORY / "v250_to_c_top4_io_outer5_checkpoint_20260826/prepared_binding.json"
OUTPUT = HISTORY / "v261_to_c_top4_io_outer6_checkpoint_20260826"
SCENE = "hybench_xp_io"
EXPECTED_STATE_SHA256 = "009dc78a2fbf6371ec3e13027212840591fc8ff167c0006504e30aa4432b6c37"


def build() -> dict:
    state_path = SOURCE / "run_state.json"
    state = base.base.alg.load(state_path)
    prior = base.verifier.verify_checkpoint(PRIOR)
    outer6 = SOURCE / "outer_006"
    outer7 = SOURCE / "outer_007"
    ranking_path = outer6 / "predicted_rankings.json"
    companion_path = outer6 / "companion_audit.json"
    decision_path = outer6 / f"{SCENE}.decision.json"
    decision = base.base.alg.load(decision_path)
    companion = base.base.alg.load(companion_path)
    rows = [base.mapping(row, "decision evaluation") for row in decision["evaluations"]]
    outer7_batch = outer7 / "top5_batch_1/batch_result.json"
    if (
        base.base.alg.sha(state_path) != EXPECTED_STATE_SHA256
        or state.get("algorithm") != "C"
        or state.get("status") != "RUNNING"
        or state.get("stage") != "OUTER_007_BATCH_2_RESETTING_XP"
        or state.get("active_scenes") != [SCENE]
        or int(state.get("outer_attempt") or 0) != 6
        or int(prior.get("completed_outer_attempts") or 0) != 5
        or decision.get("scene") != SCENE
        or int(decision.get("outer_attempt") or 0) != 6
        or decision.get("accepted") is not True
        or len(rows) != 4
        or sorted(int(row.get("rank") or 0) for row in rows) != [1, 2, 3, 4]
        or companion.get("status") != "PASS"
        or companion.get("rows") != []
        or not outer7_batch.is_file()
        or list(outer7.glob("*.decision.json"))
        or (outer7 / "top5_batch_2/batch_result.json").exists()
        or list((outer7 / "top5_batch_2").rglob("command.json"))
    ):
        raise RuntimeError("IO-C Outer-6 checkpoint boundary differs")
    outer7_document = base.base.alg.load(outer7_batch)
    if (
        outer7_document.get("status") != "PASS"
        or outer7_document.get("failures") not in ([], None)
        or len(outer7_document.get("measurements") or {}) != 4
        or any(
            int(row.get("rank") or 0) != 0
            or row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            for row in (outer7_document.get("measurements") or {}).values()
        )
    ):
        raise RuntimeError("discarded IO-C Outer-7 companion wave differs")

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
        raise RuntimeError("IO-C Outer-6 baseline binding differs")
    reconstructed["incumbents"][SCENE] = copy.deepcopy(
        base.mapping(baseline_binding["incumbents"][SCENE], "baseline incumbent")
    )
    reconstructed["full_plan_replay_history_v2"][SCENE] = record_outcomes(
        reconstructed["full_plan_replay_history_v2"][SCENE],
        scene=SCENE, outer_round=6,
        incumbent_qps=float(reconstructed["incumbents"][SCENE]["db_qps"]),
        evaluations=rows,
    )
    moved = base.moved_accepted_rows(
        rows,
        incumbent_params=reconstructed["params_by_scene"][SCENE],
        active_params=list(reconstructed["active_by_scene"][SCENE]),
    )
    if not moved:
        raise RuntimeError("IO-C Outer-6 accepted movement differs")
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
    reconstructed["outer_attempt"] = 6
    for field in (
        "accepted_rounds", "rejected_outer_attempts", "acceptance_tier",
        "consecutive_failed_rounds", "interval_stable_rounds",
        "stable_intervals", "converged", "full_plan_replay_history_v2",
    ):
        if reconstructed[field][SCENE] != state[field][SCENE]:
            raise RuntimeError(f"IO-C reconstructed {field} differs")
    if int(decision["accepted_rounds"]) != 4 or int(decision["rejected_outer_attempts"]) != 2:
        raise RuntimeError("IO-C Outer-6 counters differ")

    replay_sources = {}
    for row in rows:
        source = base.mapping(
            row.get("formal_partial_replay_source"), "candidate replay source",
        )
        path = Path(str(source["path"])).resolve()
        replay_sources[str(path)] = base.bound(path, "candidate batch")
    archive = copy.deepcopy((state.get("valid_replay_archive_by_scene") or {}).get(SCENE) or [])
    signatures = copy.deepcopy(
        (state.get("previously_accepted_parameter_signatures") or {}).get(SCENE) or []
    )
    if len(archive) != 4 or len(signatures) != 1:
        raise RuntimeError("IO-C cached replay seed differs")
    evidence = {
        "source_state": base.bound(state_path, "source state"),
        "source_checkpoint": base.bound(PRIOR, "source checkpoint"),
        "baseline": base.bound(baseline_path, "formal baseline"),
        "outers": {
            "6": {
                "predicted_rankings": base.bound(ranking_path, "predicted rankings"),
                "companion_audit": base.bound(companion_path, "companion audit"),
                "candidate_batches": list(replay_sources.values()),
                "decisions": {SCENE: base.bound(decision_path, "outer decision")},
            }
        },
        "discarded_outer7_diagnostic_companion": base.bound(
            outer7_batch, "diagnostic companion batch",
        ),
    }
    document = {
        "schema_version": base.base.CONTRACT, "status": "PREPARED",
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "source_run_root": str(SOURCE), "completed_outer_attempts": 6,
        "workload_started": False, "fresh_r0_complete": False,
        "formal_outer_started": False,
        "freeze_path": state["freeze_path"], "freeze_sha256": state["freeze_sha256"],
        "resource_model_contract_path": state["resource_model_contract_path"],
        "resource_model_contract_sha256": state["resource_model_contract_sha256"],
        "reconstructed_state": reconstructed, "evidence": evidence,
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": base.base.alg.sha(Path(__file__).resolve()),
        "independent_scene_boundary": {
            "scene": SCENE, "completed_outer_attempts": 6,
            "discarded_incomplete_outer": 7, "completed_rounds_preserved": True,
        },
        "runtime_seed": {
            "source_state": base.bound(state_path, "runtime seed state"),
            "valid_replay_archive_by_scene": {SCENE: archive},
            "previously_accepted_parameter_signatures": {SCENE: signatures},
            "outer7_candidate_measurement_credit": 0,
            "outer7_companion_measurement_role": "diagnostic_only_discarded",
        },
        "empty_candidate_wave_recovery": {
            "candidate_set_changed": False,
            "accepted_or_rejected_state_credit_increment": 0,
            "skip_only_when_no_unresolved_candidate_assignment_exists": True,
        },
    }
    document["checkpoint_sha256"] = base.base._canonical_sha256(document)
    return document


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError("fresh IO-C Outer-6 checkpoint required")
    OUTPUT.mkdir(parents=True, mode=0o700)
    path = OUTPUT / "prepared_binding.json"
    base.base.alg.atomic(path, build())
    base.verifier.verify_checkpoint(path)
    print(json.dumps({
        "status": "PREPARED", "checkpoint": str(path),
        "completed_outer_attempts": 6, "scene": SCENE,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

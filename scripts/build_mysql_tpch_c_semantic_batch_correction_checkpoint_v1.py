#!/usr/bin/env python3
"""Seal C outer 13 with corrected PG semantic-batch precedence.

The source controller performed lane-paired normalization after all expansion
batches.  The original base Top-5 then contained an accepted, moving point,
so later fallback/coverage measurements are diagnostic-only under PG ordering.
This builder SHA-binds the source evidence, emits a corrected outer decision,
and prepares a continuation checkpoint without replaying completed base work.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mysql_algorithm_c_plan_replay_pruning import record_outcomes
from scripts import build_mysql_algorithm_c_independent_round_boundary_checkpoint_v5 as base
from scripts import build_mysql_algorithm_c_outer_checkpoint_v4 as verifier
from scripts import mysql_outer_rejection_cycle_v2 as rejection_cycle


SCENE = "tpch_sf4_memory"
CORRECTED_OUTER = 13


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def bound(path: Path, label: str) -> dict[str, str]:
    path = path.resolve()
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"{label} regular evidence required: {path}")
    return {"path": str(path), "sha256": base.base.alg.sha(path)}


def _pre_outer_state(run_root: Path, source_state: Mapping[str, Any]) -> dict[str, Any]:
    prior_path = Path(str(source_state.get("checkpoint_path") or "")).resolve()
    prior = verifier.verify_checkpoint(prior_path)
    state = copy.deepcopy(mapping(
        prior.get("reconstructed_state"), "source checkpoint reconstruction",
    ))
    baseline = mapping(
        source_state.get("formal_incumbent_baseline"), "formal baseline binding",
    )
    state["incumbents"][SCENE] = copy.deepcopy(mapping(
        mapping(baseline.get("incumbents"), "formal baseline incumbents")[SCENE],
        "formal baseline scene incumbent",
    ))
    for outer in range(int(prior["completed_outer_attempts"]) + 1, CORRECTED_OUTER):
        decision_path = run_root / f"outer_{outer:03d}" / f"{SCENE}.decision.json"
        decision = base.base.alg.load(decision_path)
        rows = [mapping(row, "outer evaluation") for row in decision["evaluations"]]
        moved = base.moved_accepted_rows(
            rows,
            incumbent_params=state["params_by_scene"][SCENE],
            active_params=list(state["active_by_scene"][SCENE]),
        )
        accepted = bool(moved)
        if accepted != (decision.get("accepted") is True):
            raise RuntimeError(f"outer {outer} source decision differs")
        if accepted:
            row, _semantic, subset = moved[0]
            state["params_by_scene"][SCENE] = copy.deepcopy(row["params"])
            state["incumbents"][SCENE] = {
                key: copy.deepcopy(row[key])
                for key in ("db_qps", "p95_ms", "p99_ms", "resource_means")
            }
            state["ranges_by_scene"][SCENE] = base.base.alg.shrink_ranges(
                state["ranges_by_scene"][SCENE],
                state["params_by_scene"][SCENE],
                subset,
            )
            state["accepted_rounds"][SCENE] += 1
        else:
            state["rejected_outer_attempts"][SCENE] += 1
        ranges, progress = rejection_cycle.advance_rejection_cycle(
            accepted=accepted,
            consecutive_rejections=int(state["consecutive_failed_rounds"][SCENE]),
            ranges=state["ranges_by_scene"][SCENE],
            incumbent_params=state["params_by_scene"][SCENE],
            active_params=state["active_by_scene"][SCENE],
        )
        state["ranges_by_scene"][SCENE] = ranges
        state["consecutive_failed_rounds"][SCENE] = int(
            progress["consecutive_rejections_after"]
        )
        state["outer_attempt"] = outer
    return state


def build(run_root: Path, output_dir: Path) -> dict[str, Any]:
    run_root = run_root.resolve()
    output_dir = output_dir.resolve()
    source_state_path = run_root / "run_state.json"
    source_state = base.base.alg.load(source_state_path)
    if (
        source_state.get("algorithm") != "C"
        or source_state.get("formal_scope") != "mysql_only"
        or source_state.get("cross_engine_comparison_prohibited") is not True
        or list(source_state.get("active_scenes") or []) != [SCENE]
        or int(source_state.get("outer_attempt") or 0) != CORRECTED_OUTER
        or source_state.get("formal_evidence_eligible") is not True
    ):
        raise RuntimeError("C semantic correction source state differs")

    document = base.build(run_root)
    reconstructed = mapping(
        document.get("reconstructed_state"), "boundary reconstruction",
    )
    decision_path = (
        run_root / f"outer_{CORRECTED_OUTER:03d}" / f"{SCENE}.decision.json"
    )
    source_decision = base.base.alg.load(decision_path)
    rows = [
        mapping(row, "outer 13 evaluation")
        for row in (source_decision.get("evaluations") or [])
    ]
    base_rows = rows[:5]
    if [int(row.get("rank") or 0) for row in base_rows] != [1, 2, 3, 4, 5]:
        raise RuntimeError("outer 13 base Top-5 evidence differs")

    pre = _pre_outer_state(run_root, source_state)
    moved = base.moved_accepted_rows(
        base_rows,
        incumbent_params=pre["params_by_scene"][SCENE],
        active_params=list(pre["active_by_scene"][SCENE]),
    )
    if not moved or int(moved[0][0].get("rank") or 0) != 3:
        raise RuntimeError("outer 13 corrected base-batch winner differs")
    chosen, chosen_decision, subset = moved[0]
    if not (
        float(chosen["db_qps"]) == 0.1990311194180822
        and subset == ["row_evaluate_cost"]
    ):
        raise RuntimeError("outer 13 corrected movement/QPS differs")

    corrected_ranges = base.base.alg.shrink_ranges(
        pre["ranges_by_scene"][SCENE], chosen["params"], subset,
    )
    initial_ranges = base.base.alg.load(
        base.base.alg.TABLE_ROOT / base.base.alg.TABLES[SCENE]
    )["parameter_space"]["ranges"]
    convergence = rejection_cycle.convergence_document(
        ranges=corrected_ranges,
        initial_ranges=initial_ranges,
        active_params=pre["active_by_scene"][SCENE],
    )
    if convergence.get("converged") is not False:
        raise RuntimeError("corrected outer 13 must continue searching")

    corrected_decision = {
        "schema_version": "mysql_tpch_algorithm_c_semantic_batch_correction_v1",
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": SCENE,
        "outer_attempt": CORRECTED_OUTER,
        "source_decision": bound(decision_path, "source outer 13 decision"),
        "correction_reason": (
            "lane_pairing_made_base_top5_acceptable_after_expansions_were_measured"
        ),
        "semantic_batch_order": [
            "base_top5", "fallback_plan_change",
            "current_interval_plan_coverage", "full_range_plan_basin_escape",
        ],
        "selected_semantic_batch_kind": "base_top5",
        "selected_rank": 3,
        "selected_params": copy.deepcopy(chosen["params"]),
        "selected_incumbent": {
            key: copy.deepcopy(chosen[key])
            for key in ("db_qps", "p95_ms", "p99_ms", "resource_means")
        },
        "selected_moved_parameters": subset,
        "evaluations": copy.deepcopy(base_rows),
        "later_source_evaluation_count": len(rows) - len(base_rows),
        "later_source_evaluations_grant_state_credit": False,
        "accepted_state_credit_increment": 1,
        "accepted_round_credit_changed_from_source": False,
        "algorithm_a_parameter_or_measurement_read": False,
        "external_algorithm_evidence_used": False,
        "corrected_interval_convergence": convergence,
    }
    correction_path = output_dir / "outer_013_corrected_semantic_decision.json"
    base.base.alg.atomic(correction_path, corrected_decision)

    reconstructed["params_by_scene"][SCENE] = copy.deepcopy(chosen["params"])
    reconstructed["incumbents"][SCENE] = {
        key: copy.deepcopy(chosen[key])
        for key in ("db_qps", "p95_ms", "p99_ms", "resource_means")
    }
    reconstructed["ranges_by_scene"][SCENE] = corrected_ranges
    reconstructed["consecutive_failed_rounds"][SCENE] = 0
    reconstructed["acceptance_tier"][SCENE] = "fallback"
    reconstructed["interval_stable_rounds"][SCENE] = 0
    reconstructed["stable_intervals"][SCENE] = None
    reconstructed["converged"][SCENE] = False
    reconstructed["replayed_param_keys_by_scene"][SCENE] = []
    history = [
        row for row in reconstructed["full_plan_replay_history_v2"][SCENE]
        if int(row.get("outer_round") or 0) != CORRECTED_OUTER
    ]
    reconstructed["full_plan_replay_history_v2"][SCENE] = record_outcomes(
        history,
        scene=SCENE,
        outer_round=CORRECTED_OUTER,
        incumbent_qps=float(pre["incumbents"][SCENE]["db_qps"]),
        evaluations=base_rows,
    )

    evidence = mapping(document.get("evidence"), "checkpoint evidence")
    outer_evidence = mapping(
        mapping(evidence.get("outers"), "outer evidence")[str(CORRECTED_OUTER)],
        "outer 13 evidence",
    )
    outer_evidence["source_decision_before_semantic_correction"] = bound(
        decision_path, "source outer 13 decision",
    )
    outer_evidence["decisions"] = {
        SCENE: bound(correction_path, "corrected outer 13 decision")
    }
    evidence["outers"][str(CORRECTED_OUTER)] = outer_evidence
    document["evidence"] = evidence
    document["reconstructed_state"] = reconstructed
    document["builder_source_path"] = str(Path(__file__).resolve())
    document["builder_source_sha256"] = base.base.alg.sha(Path(__file__).resolve())
    document["semantic_batch_correction"] = {
        "source_outer": CORRECTED_OUTER,
        "selected_semantic_batch_kind": "base_top5",
        "selected_rank": 3,
        "later_batches_grant_state_credit": False,
        "incumbent_or_range_corrected": True,
        "accepted_round_credit_changed": False,
        "algorithm_a_parameter_or_measurement_read": False,
        "correction_decision": bound(correction_path, "corrected outer 13 decision"),
    }
    payload = copy.deepcopy(document)
    payload.pop("checkpoint_sha256", None)
    document["checkpoint_sha256"] = base.base._canonical_sha256(payload)
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.output_dir.name) is None:
        parser.error("unsafe output directory name")
    if args.output_dir.exists() or args.output_dir.is_symlink():
        raise RuntimeError(f"fresh correction checkpoint required: {args.output_dir}")
    args.output_dir.mkdir(parents=True, mode=0o700)
    document = build(args.run_root, args.output_dir)
    checkpoint_path = args.output_dir / "prepared_binding.json"
    base.base.alg.atomic(checkpoint_path, document)
    verifier.verify_checkpoint(checkpoint_path)
    print(json.dumps({
        "status": "PREPARED",
        "checkpoint": str(checkpoint_path),
        "checkpoint_sha256": base.base.alg.sha(checkpoint_path),
        "completed_outer_attempts": document["completed_outer_attempts"],
        "corrected_converged": document["reconstructed_state"]["converged"][SCENE],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

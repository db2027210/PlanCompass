#!/usr/bin/env python3
"""Seal an independent C scene after runtime interference during measurement.

Completed outer rounds are reconstructed from their SHA-bound decisions.  The
currently measuring outer receives no candidate, acceptance, rejection, or
convergence credit and is replayed from its beginning after a fresh quiet gate.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
from typing import Any, Mapping

from scripts import build_mysql_algorithm_c_independent_quiet_gate_checkpoint_v1 as predecessor


base = predecessor.base
verifier = predecessor.verifier
WATCHDOG_ERROR = (
    "RuntimeError: runtime watchdog stopped formal run after "
    "runtime_interference_detected_under_exclusive_wave_lease"
)


def verify_interrupted_outer(
    run_root: Path, state: Mapping[str, Any], scene: str, completed: int,
) -> dict[str, Any]:
    interruption = state.get("watchdog_interruption")
    if not isinstance(interruption, Mapping):
        raise RuntimeError("runtime interference evidence is absent")
    stage = str(interruption.get("runtime_stage") or "")
    match = re.fullmatch(
        rf"OUTER_{completed + 1:03d}_BATCH_([1-3])_MEASURING", stage,
    )
    if (
        match is None
        or interruption.get("event")
        != "runtime_interference_detected_under_exclusive_wave_lease"
        or interruption.get("formal_state_credit_increment") != 0
        or interruption.get("interrupted_candidate_replay_prohibited") is not True
    ):
        raise RuntimeError("runtime interference boundary differs")

    batch_index = int(match.group(1))
    outer = run_root / f"outer_{completed + 1:03d}"
    ranking = outer / "predicted_rankings.json"
    interrupted_batch = outer / f"top5_batch_{batch_index}"
    if (
        not outer.is_dir()
        or not ranking.is_file()
        or not interrupted_batch.is_dir()
        or list(outer.glob("*.decision.json"))
        or (interrupted_batch / "batch_result.json").exists()
        or any(
            not (outer / f"top5_batch_{prior}" / "batch_result.json").is_file()
            for prior in range(1, batch_index)
        )
    ):
        raise RuntimeError("interrupted undecided outer differs")
    ranking_value = base.alg.load(ranking)
    if (
        int(ranking_value.get("outer_attempt") or 0) != completed + 1
        or scene not in (ranking_value.get("active_decision_scenes") or [])
        or ranking_value.get("formal_evidence_eligible") is not True
    ):
        raise RuntimeError("interrupted outer ranking differs")

    completed_batches = [
        predecessor.bound(
            outer / f"top5_batch_{prior}" / "batch_result.json",
            "completed interrupted-outer batch",
        )
        for prior in range(1, batch_index)
    ]
    return {
        "predicted_rankings": predecessor.bound(ranking, "interrupted rankings"),
        "runtime_stage": stage,
        "interrupted_batch": batch_index,
        "completed_batches_discarded_with_entire_outer": completed_batches,
        "watchdog_interruption": copy.deepcopy(dict(interruption)),
        "formal_state_credit_increment": 0,
        "discarded_entire_undecided_outer": True,
        "fresh_quiet_gate_required_on_resume": True,
    }


def build(run_root: Path) -> dict[str, Any]:
    run_root = run_root.resolve()
    state_path = run_root / "run_state.json"
    state = base.alg.load(state_path)
    active = list(state.get("active_scenes") or [])
    if (
        state.get("algorithm") != "C"
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error") != WATCHDOG_ERROR
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("formal_evidence_eligible") is not True
        or state.get("accepted_state_credit_prohibited") is not False
        or len(active) != 1
        or active[0] not in predecessor.SCENES
    ):
        raise RuntimeError("independent runtime-interference source differs")
    scene = active[0]
    completed = int(state.get("outer_attempt") or 0)
    if completed < 1:
        raise RuntimeError("no completed outer boundary is available")

    source_checkpoint_path = Path(str(state.get("checkpoint_path") or "")).resolve()
    source_checkpoint = verifier.verify_checkpoint(source_checkpoint_path)
    prior_completed = int(source_checkpoint.get("completed_outer_attempts") or 0)
    if state.get("fresh_search") is True:
        reconstructed = predecessor._fresh_reconstructed(state)
        prior_completed = 0
    else:
        reconstructed = copy.deepcopy(predecessor.mapping(
            source_checkpoint.get("reconstructed_state"),
            "source checkpoint reconstructed state",
        ))
        if prior_completed >= completed:
            raise RuntimeError("source has no newly completed outer boundary")

    baseline_binding = predecessor.mapping(
        state.get("formal_incumbent_baseline"), "formal incumbent baseline",
    )
    baseline_path = Path(str(baseline_binding.get("path") or "")).resolve()
    if (
        baseline_binding.get("status") != "PASS"
        or not baseline_path.is_file()
        or baseline_path.is_symlink()
        or base.alg.sha(baseline_path) != baseline_binding.get("sha256")
    ):
        raise RuntimeError("formal incumbent baseline binding differs")
    baseline_incumbents = predecessor.mapping(
        baseline_binding.get("incumbents"), "formal baseline incumbents",
    )
    reconstructed["incumbents"][scene] = copy.deepcopy(
        predecessor.mapping(
            baseline_incumbents[scene], "formal scene baseline incumbent",
        )
    )

    evidence: dict[str, Any] = {
        "source_state": predecessor.bound(state_path, "source state"),
        "source_checkpoint": predecessor.bound(
            source_checkpoint_path, "source checkpoint",
        ),
        "baseline": predecessor.bound(baseline_path, "formal baseline"),
        "outers": {},
    }
    for outer in range(prior_completed + 1, completed + 1):
        root = run_root / f"outer_{outer:03d}"
        ranking_path = root / "predicted_rankings.json"
        companion_path = root / "companion_audit.json"
        decision_path = root / f"{scene}.decision.json"
        decision = base.alg.load(decision_path)
        rows = [
            predecessor.mapping(row, "decision evaluation")
            for row in (decision.get("evaluations") or [])
        ]
        if (
            decision.get("scene") != scene
            or int(decision.get("outer_attempt") or 0) != outer
            or not rows
        ):
            raise RuntimeError(f"outer {outer} decision differs")
        companion = base.alg.load(companion_path)
        if (
            companion.get("status") != "PASS"
            or companion.get("full_workload_contract_preserved") is not True
        ):
            raise RuntimeError(f"outer {outer} companion audit differs")
        batches = []
        for batch_index, expected in ((1, 2), (2, 2), (3, 1)):
            batch_path = root / f"top5_batch_{batch_index}/batch_result.json"
            batch = base.alg.load(batch_path)
            measurements = predecessor.mapping(
                batch.get("measurements"), "batch measurements",
            )
            if (
                batch.get("status") != "PASS"
                or batch.get("failures") not in ([], None)
                or len(measurements) != expected
                or any(
                    predecessor.mapping(row, "batch measurement").get("scene")
                    != scene
                    or predecessor.mapping(row, "batch measurement").get(
                        "hard_gate_passed"
                    ) is not True
                    or predecessor.mapping(row, "batch measurement").get(
                        "coverage_complete"
                    ) is not True
                    for row in measurements.values()
                )
            ):
                raise RuntimeError(f"outer {outer} batch {batch_index} differs")
            batches.append(predecessor.bound(batch_path, "batch result"))

        reconstructed["full_plan_replay_history_v2"][scene] = (
            predecessor.record_outcomes(
                reconstructed["full_plan_replay_history_v2"][scene],
                scene=scene,
                outer_round=outer,
                incumbent_qps=float(reconstructed["incumbents"][scene]["db_qps"]),
                evaluations=[
                    row for row in rows
                    if row.get("cached_valid_replay_redecision") is not True
                ],
            )
        )
        moved = predecessor.prior.moved_accepted_rows(
            rows,
            incumbent_params=reconstructed["params_by_scene"][scene],
            active_params=list(reconstructed["active_by_scene"][scene]),
        )
        accepted = decision.get("accepted") is True
        if accepted != bool(moved):
            raise RuntimeError(f"outer {outer} accepted movement evidence differs")
        if accepted:
            chosen, _outer_decision, subset = moved[0]
            reconstructed["params_by_scene"][scene] = copy.deepcopy(chosen["params"])
            reconstructed["incumbents"][scene] = {
                key: copy.deepcopy(chosen[key])
                for key in ("db_qps", "p95_ms", "p99_ms", "resource_means")
            }
            reconstructed["ranges_by_scene"][scene] = base.alg.shrink_ranges(
                reconstructed["ranges_by_scene"][scene],
                reconstructed["params_by_scene"][scene], subset,
            )
            reconstructed["accepted_rounds"][scene] += 1
            reconstructed["replayed_param_keys_by_scene"][scene] = []
        else:
            reconstructed["rejected_outer_attempts"][scene] += 1

        tier = predecessor.mapping(decision.get("tier_progress"), "tier progress")
        convergence = predecessor.mapping(
            decision.get("interval_convergence"), "convergence",
        )
        reconstructed["acceptance_tier"][scene] = str(tier["acceptance_tier"])
        reconstructed["consecutive_failed_rounds"][scene] = int(
            tier["consecutive_failed_rounds"]
        )
        reconstructed["interval_stable_rounds"][scene] = int(
            convergence["stable_rounds"]
        )
        reconstructed["stable_intervals"][scene] = copy.deepcopy(
            convergence.get("stable_interval")
        )
        reconstructed["converged"][scene] = bool(convergence["converged"])
        if (
            reconstructed["accepted_rounds"][scene]
            != int(decision["accepted_rounds"])
            or reconstructed["rejected_outer_attempts"][scene]
            != int(decision["rejected_outer_attempts"])
        ):
            raise RuntimeError(f"outer {outer} counters differ")
        evidence["outers"][str(outer)] = {
            "predicted_rankings": predecessor.bound(
                ranking_path, "predicted rankings",
            ),
            "companion_audit": predecessor.bound(
                companion_path, "companion audit",
            ),
            "batches": batches,
            "decisions": {
                scene: predecessor.bound(decision_path, "outer decision")
            },
        }

    for field in (
        "accepted_rounds", "rejected_outer_attempts", "acceptance_tier",
        "consecutive_failed_rounds", "interval_stable_rounds",
        "stable_intervals", "converged", "full_plan_replay_history_v2",
        "post_target_stability_confirmation_counts",
    ):
        if reconstructed[field][scene] != state[field][scene]:
            raise RuntimeError(f"source {field} differs after reconstruction")
    reconstructed["outer_attempt"] = completed
    interruption = verify_interrupted_outer(run_root, state, scene, completed)
    document = {
        "schema_version": base.CONTRACT,
        "status": "PREPARED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "source_run_root": str(run_root),
        "completed_outer_attempts": completed,
        "workload_started": False,
        "fresh_r0_complete": False,
        "formal_outer_started": False,
        "freeze_path": state["freeze_path"],
        "freeze_sha256": state["freeze_sha256"],
        "resource_model_contract_path": state["resource_model_contract_path"],
        "resource_model_contract_sha256": state[
            "resource_model_contract_sha256"
        ],
        "reconstructed_state": reconstructed,
        "evidence": evidence,
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": base.alg.sha(Path(__file__).resolve()),
        "independent_scene_runtime_interference_boundary": {
            "scene": scene,
            "completed_outer_attempts": completed,
            "discarded_interrupted_outer": completed + 1,
            "completed_rounds_preserved": True,
            "interruption": interruption,
        },
    }
    payload = copy.deepcopy(document)
    document["checkpoint_sha256"] = base._canonical_sha256(payload)
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.output_dir.name) is None:
        parser.error("unsafe output directory")
    if args.output_dir.exists() or args.output_dir.is_symlink():
        raise RuntimeError("fresh checkpoint output required")
    document = build(args.run_root)
    args.output_dir.mkdir(parents=True, mode=0o700)
    path = args.output_dir / "prepared_binding.json"
    base.alg.atomic(path, document)
    verifier.verify_checkpoint(path)
    boundary = document["independent_scene_runtime_interference_boundary"]
    print(json.dumps({
        "status": "PREPARED",
        "checkpoint": str(path),
        "completed_outer_attempts": document["completed_outer_attempts"],
        "scene": boundary["scene"],
        "discarded_interrupted_outer": boundary["discarded_interrupted_outer"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

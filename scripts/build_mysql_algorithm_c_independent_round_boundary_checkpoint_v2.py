#!/usr/bin/env python3
"""Seal a stopped independent C scene with its frozen Top-4 table state.

An already-started next outer is archived without credit.  Every completed
decision, accepted/rejected counter, incumbent, range, and replay-history
field is reconstructed from SHA-bound evidence and preserved.
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
from scripts import build_mysql_algorithm_c_outer_checkpoint_v2 as base
from scripts import build_mysql_algorithm_c_outer_checkpoint_v4 as verifier
from scripts import mysql_algorithm_b_pg_outer_policy_v31 as policy
from scripts import mysql_outer_rejection_cycle_v1 as rejection_cycle


SCENES = tuple(base.alg.SCENES)
ROOT = Path(__file__).resolve().parents[1]
TOP4_TABLE_BINDINGS = {
    "job_memory": "job_table_unchanged_copy",
    "tpch_sf4_memory": "tpch_table_unchanged_copy",
    "hybench_xp_cpu": "xp_cpu_top4_table",
    "hybench_xp_io": "xp_io_top4_table",
}


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def bound(path: Path, label: str) -> dict[str, str]:
    path = path.resolve()
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"{label} regular evidence required: {path}")
    return {"path": str(path), "sha256": base.alg.sha(path)}


def frozen_fresh_search_tables(state: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Load the exact table freeze used by an XP Top-4 fresh search.

    The predecessor checkpoint predates Top-4 and therefore cannot supply the
    active parameter or range state for a fresh Top-4 run.  The live runtime
    replaced those fields from its frozen table directory before outer 1; a
    boundary reconstruction must reproduce the same transition.
    """
    release = mapping(
        state.get("xp_top4_parameter_release"), "XP Top-4 parameter release",
    )
    contract_path = Path(str(release.get("contract_path") or "")).resolve()
    contract_sha = str(release.get("contract_sha256") or "")
    if (
        not contract_path.is_file()
        or contract_path.is_symlink()
        or base.alg.sha(contract_path) != contract_sha
    ):
        raise RuntimeError("XP Top-4 contract binding differs")
    contract = mapping(base.alg.load(contract_path), "XP Top-4 contract")
    successor = mapping(
        contract.get("xp_top4_parameter_successor"), "XP Top-4 successor",
    )
    active_release = mapping(
        release.get("active_parameters_by_scene"), "XP Top-4 active release",
    )
    if (
        successor.get("active_parameters_by_scene") != active_release
        or successor.get("fresh_search_required") is not True
        or set(active_release) != {"hybench_xp_cpu", "hybench_xp_io"}
        or any(len(list(active_release[name])) != 4 for name in active_release)
    ):
        raise RuntimeError("XP Top-4 active parameter release differs")
    bindings = mapping(contract.get("source_bindings"), "XP Top-4 bindings")
    tables: dict[str, dict[str, Any]] = {}
    for scene, binding_name in TOP4_TABLE_BINDINGS.items():
        binding = mapping(bindings.get(binding_name), f"{scene} table binding")
        table_path = (ROOT / str(binding.get("path") or "")).resolve()
        if (
            not table_path.is_file()
            or table_path.is_symlink()
            or base.alg.sha(table_path) != str(binding.get("sha256") or "")
        ):
            raise RuntimeError(f"{scene} frozen table binding differs")
        table = mapping(base.alg.load(table_path), f"{scene} frozen table")
        space = mapping(table.get("parameter_space"), f"{scene} parameter space")
        active = list(space.get("active_params") or [])
        ranges = mapping(space.get("ranges"), f"{scene} ranges")
        if set(active) != set(ranges):
            raise RuntimeError(f"{scene} active/range set differs")
        if scene in active_release and active != list(active_release[scene]):
            raise RuntimeError(f"{scene} Top-4 table order differs")
        tables[scene] = table
    return tables


def moved_accepted_rows(
    rows: list[dict[str, Any]], *, incumbent_params: Mapping[str, Any],
    active_params: list[str],
) -> list[tuple[dict[str, Any], dict[str, Any], list[str]]]:
    result: list[tuple[dict[str, Any], dict[str, Any], list[str]]] = []
    for row in rows:
        decision = mapping(row.get("outer_decision"), "outer decision")
        if decision.get("accepted") is not True:
            continue
        ranking = mapping(row.get("candidate_ranking"), "candidate ranking")
        try:
            subset = policy.moved_shrink_subset(
                incumbent_params=incumbent_params,
                candidate_ranking=ranking,
                active_params=active_params,
            )
        except RuntimeError as error:
            if str(error) != "accepted candidate has no moved declared parameter":
                raise
            continue
        result.append((row, decision, subset))
    result.sort(
        key=lambda item: policy.accepted_selection_key({
            "row": item[0], "decision": item[1],
        }),
        reverse=True,
    )
    return result


def build(run_root: Path) -> dict[str, Any]:
    run_root = run_root.resolve()
    state_path = run_root / "run_state.json"
    state = base.alg.load(state_path)
    active = list(state.get("active_scenes") or [])
    if (
        state.get("algorithm") != "C"
        or state.get("status") not in {
            "RUNNING", "NON_CONVERGED", "COMPLETE", "FAILED_CLOSED",
        }
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("formal_evidence_eligible") is not True
        or state.get("accepted_state_credit_prohibited") is not False
        or len(active) != 1
        or active[0] not in SCENES
    ):
        raise RuntimeError("independent stopped source state differs")
    scene = active[0]
    if state.get("status") == "FAILED_CLOSED" and (
        state.get("stage") != "FAILED" or not state.get("error")
    ):
        raise RuntimeError("failed source lacks a technical terminal boundary")
    completed = int(state.get("outer_attempt") or 0)
    if completed < 1:
        raise RuntimeError("no completed outer boundary is available")
    incomplete_root = run_root / f"outer_{completed + 1:03d}"
    if incomplete_root.exists() and (
        not incomplete_root.is_dir()
        or list(incomplete_root.glob("*.decision.json"))
    ):
        raise RuntimeError("stopped successor is not after a clean completed boundary")

    prior_path = Path(str(state.get("checkpoint_path") or "")).resolve()
    prior = verifier.verify_checkpoint(prior_path)
    prior_completed = int(prior.get("completed_outer_attempts") or 0)
    reconstructed = copy.deepcopy(mapping(
        prior.get("reconstructed_state"), "prior reconstructed state",
    ))
    if state.get("fresh_search") is True:
        frozen_tables = frozen_fresh_search_tables(state)
        prior_completed = 0
        reconstructed["outer_attempt"] = 0
        reconstructed["accepted_rounds"] = {name: 0 for name in SCENES}
        reconstructed["rejected_outer_attempts"] = {name: 0 for name in SCENES}
        reconstructed["acceptance_tier"] = {
            name: "fallback" for name in SCENES
        }
        reconstructed["consecutive_failed_rounds"] = {
            name: 0 for name in SCENES
        }
        reconstructed["interval_stable_rounds"] = {
            name: 0 for name in SCENES
        }
        reconstructed["stable_intervals"] = {name: None for name in SCENES}
        reconstructed["converged"] = {name: False for name in SCENES}
        reconstructed["replayed_param_keys_by_scene"] = {
            name: [] for name in SCENES
        }
        reconstructed["full_plan_replay_history_v2"] = {
            name: [] for name in SCENES
        }
        reconstructed["params_by_scene"] = {
            name: copy.deepcopy(base.alg.DEFAULTS) for name in SCENES
        }
        for name in SCENES:
            table = frozen_tables[name]
            reconstructed["active_by_scene"][name] = list(
                table["parameter_space"]["active_params"]
            )
            reconstructed["ranges_by_scene"][name] = copy.deepcopy(
                table["parameter_space"]["ranges"]
            )
    elif prior_completed >= completed:
        raise RuntimeError("source has no newly completed outer boundary")
    for name in (
        "incumbents", "params_by_scene", "ranges_by_scene", "active_by_scene",
        "accepted_rounds", "rejected_outer_attempts", "acceptance_tier",
        "consecutive_failed_rounds", "interval_stable_rounds",
        "stable_intervals", "converged", "full_plan_replay_history_v2",
        "replayed_param_keys_by_scene",
    ):
        value = mapping(reconstructed.get(name), f"checkpoint {name}")
        if set(value) != set(SCENES):
            raise RuntimeError(f"checkpoint {name} scene set differs")

    baseline_binding = mapping(
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
    baseline_incumbents = mapping(
        baseline_binding.get("incumbents"), "formal baseline incumbents",
    )
    if scene not in baseline_incumbents:
        raise RuntimeError("formal baseline scene incumbent is absent")
    # Boundary resume re-measures the configured incumbent and uses this live
    # two-lane mean as the outer reference before the first resumed attempt.
    reconstructed["incumbents"][scene] = copy.deepcopy(
        mapping(baseline_incumbents[scene], "formal baseline scene incumbent")
    )

    evidence: dict[str, Any] = {
        "source_state": bound(state_path, "source state"),
        "source_checkpoint": bound(prior_path, "source checkpoint"),
        "baseline": bound(baseline_path, "formal baseline"),
        "outers": {},
    }
    for outer in range(prior_completed + 1, completed + 1):
        root = run_root / f"outer_{outer:03d}"
        ranking_path = root / "predicted_rankings.json"
        companion_path = root / "companion_audit.json"
        decision_path = root / f"{scene}.decision.json"
        decision = base.alg.load(decision_path)
        rows = [
            mapping(row, "decision evaluation")
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
        for batch_index in (1, 2, 3):
            batch_path = root / f"top5_batch_{batch_index}" / "batch_result.json"
            batch = base.alg.load(batch_path)
            measurements = mapping(batch.get("measurements"), "batch measurements")
            expected = 2 if batch_index < 3 else 1
            if (
                batch.get("status") != "PASS"
                or batch.get("failures") not in ([], None)
                or len(measurements) != expected
                or any(
                    mapping(row, "batch measurement").get("scene") != scene
                    or mapping(row, "batch measurement").get("hard_gate_passed")
                    is not True
                    or mapping(row, "batch measurement").get("coverage_complete")
                    is not True
                    for row in measurements.values()
                )
            ):
                raise RuntimeError(f"outer {outer} batch {batch_index} differs")
            batches.append(bound(batch_path, "batch result"))

        reconstructed["full_plan_replay_history_v2"][scene] = record_outcomes(
            reconstructed["full_plan_replay_history_v2"][scene],
            scene=scene,
            outer_round=outer,
            incumbent_qps=float(reconstructed["incumbents"][scene]["db_qps"]),
            evaluations=[
                row for row in rows
                if row.get("cached_valid_replay_redecision") is not True
            ],
        )
        moved = moved_accepted_rows(
            rows,
            incumbent_params=reconstructed["params_by_scene"][scene],
            active_params=list(reconstructed["active_by_scene"][scene]),
        )
        accepted = decision.get("accepted") is True
        if accepted != bool(moved):
            raise RuntimeError(f"outer {outer} accepted movement evidence differs")
        if accepted:
            chosen, _decision, subset = moved[0]
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
            reconstructed["replayed_param_keys_by_scene"][scene].extend(
                base.alg.optimizer_params_key(mapping(row["params"], "evaluated params"))
                for row in rows
                if row.get("cached_valid_replay_redecision") is not True
            )
            reconstructed["replayed_param_keys_by_scene"][scene] = list(dict.fromkeys(
                reconstructed["replayed_param_keys_by_scene"][scene]
            ))

        previous_consecutive_rejections = int(
            reconstructed["consecutive_failed_rounds"][scene]
        )
        tier = mapping(decision.get("tier_progress"), "tier progress")
        convergence = mapping(decision.get("interval_convergence"), "convergence")
        if convergence.get("contract") == rejection_cycle.POLICY_ID:
            updated_ranges, reconstructed_cycle = (
                rejection_cycle.advance_rejection_cycle(
                    accepted=accepted,
                    consecutive_rejections=previous_consecutive_rejections,
                    ranges=reconstructed["ranges_by_scene"][scene],
                    incumbent_params=reconstructed["params_by_scene"][scene],
                    active_params=reconstructed["active_by_scene"][scene],
                )
            )
            decision_cycle = mapping(
                decision.get("rejection_cycle"), "rejection cycle",
            )
            for name in (
                "policy_id", "accepted", "consecutive_rejections_before",
                "consecutive_rejections_after", "forced_incumbent_shrink",
                "accepted_round_credit", "incumbent_changed_by_cycle",
            ):
                if reconstructed_cycle.get(name) != decision_cycle.get(name):
                    raise RuntimeError(
                        f"outer {outer} rejection-cycle {name} differs"
                    )
            # The live boundary-resume runtime clears the replay de-dup set
            # when two consecutive rejected outers force an incumbent-centred
            # range shrink.  Candidates from the wider, pre-shrink interval
            # are eligible again in the newly narrowed interval.  Reproduce
            # that persisted transition before validating retained counts.
            if reconstructed_cycle.get("forced_incumbent_shrink") is True:
                reconstructed["replayed_param_keys_by_scene"][scene] = []
            reconstructed["ranges_by_scene"][scene] = updated_ranges
            reconstructed["interval_stable_rounds"][scene] = 0
            reconstructed["stable_intervals"][scene] = None
            reconstructed["converged"][scene] = bool(convergence["converged"])
        else:
            reconstructed["interval_stable_rounds"][scene] = int(
                convergence["stable_rounds"]
            )
            reconstructed["stable_intervals"][scene] = copy.deepcopy(
                convergence.get("stable_interval")
            )
            reconstructed["converged"][scene] = bool(convergence["converged"])
        reconstructed["acceptance_tier"][scene] = str(tier["acceptance_tier"])
        reconstructed["consecutive_failed_rounds"][scene] = int(
            tier["consecutive_failed_rounds"]
        )
        if (
            reconstructed["accepted_rounds"][scene]
            != int(decision["accepted_rounds"])
            or reconstructed["rejected_outer_attempts"][scene]
            != int(decision["rejected_outer_attempts"])
        ):
            raise RuntimeError(f"outer {outer} counters differ")
        evidence["outers"][str(outer)] = {
            "predicted_rankings": bound(ranking_path, "predicted rankings"),
            "companion_audit": bound(companion_path, "companion audit"),
            "batches": batches,
            "decisions": {scene: bound(decision_path, "outer decision")},
        }

    # v59 persists the replay-dedup audit and retained count but no longer
    # duplicates the concrete key list in run_state.json.  The exact list is
    # deterministically reconstructed above from every SHA-bound completed
    # decision: accepted rounds clear it and rejected rounds add the physically
    # evaluated parameter keys.  Older states that still carry the explicit
    # list must agree with that reconstruction.
    explicit_replayed = state.get("replayed_param_keys_by_scene")
    if explicit_replayed is not None:
        explicit_scene = mapping(
            explicit_replayed, "source replayed parameter keys",
        )[scene]
        if reconstructed["replayed_param_keys_by_scene"][scene] != explicit_scene:
            raise RuntimeError("source replayed parameter keys differ after reconstruction")
    dedup_audit = mapping(
        state.get("candidate_replay_dedup"), "source candidate replay dedup",
    ).get(scene) or {}
    if int(dedup_audit.get("retained_for_next_outer") or 0) != len(
        reconstructed["replayed_param_keys_by_scene"][scene]
    ):
        raise RuntimeError("source replay-dedup retained count differs")
    for field in (
        "accepted_rounds", "rejected_outer_attempts", "acceptance_tier",
        "consecutive_failed_rounds", "interval_stable_rounds",
        "stable_intervals", "converged", "full_plan_replay_history_v2",
        "replayed_param_keys_by_scene",
    ):
        if field == "replayed_param_keys_by_scene" and explicit_replayed is None:
            continue
        if reconstructed[field][scene] != state[field][scene]:
            raise RuntimeError(f"source {field} differs after reconstruction")

    reconstructed["outer_attempt"] = completed
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
        "resource_model_contract_sha256": state["resource_model_contract_sha256"],
        "reconstructed_state": reconstructed,
        "evidence": evidence,
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": base.alg.sha(Path(__file__).resolve()),
        "independent_scene_boundary": {
            "scene": scene,
            "completed_outer_attempts": completed,
            "discarded_incomplete_outer": completed + 1,
            "completed_rounds_preserved": True,
        },
        "independent_round_boundary_successor": {
            "discarded_incomplete_outer": completed + 1 if incomplete_root.exists() else None,
            "discarded_outer_credit": 0,
            "accepted_round_credit_changed": False,
            "rejected_outer_credit_changed": False,
            "replay_history_preserved": True,
            "physical_top5_required_on_resume": True,
            "replay_key_provenance": (
                "explicit_sha_bound_state"
                if explicit_replayed is not None
                else "reconstructed_from_sha_bound_completed_decisions"
            ),
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
    print(json.dumps({
        "status": "PREPARED",
        "checkpoint": str(path),
        "completed_outer_attempts": document["completed_outer_attempts"],
        "scene": document["independent_scene_boundary"]["scene"],
        "discarded_incomplete_outer": document[
            "independent_round_boundary_successor"
        ]["discarded_incomplete_outer"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

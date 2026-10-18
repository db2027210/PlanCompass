#!/usr/bin/env python3
"""Seal a fresh independent C search after a rank-1 quiet-gate timeout.

Only fully decided outer rounds are reconstructed.  The next outer ranking
and failed quiet gate are bound as interruption evidence, but receive no
candidate, acceptance, rejection, or convergence credit.
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
from scripts import build_mysql_algorithm_c_independent_boundary_checkpoint_v2 as prior
from scripts import run_mysql_algorithm_c_async_scene_v54 as runtime_v54


base = prior.base
verifier = prior.verifier
policy = prior.policy
SCENES = tuple(base.alg.SCENES)
QUIET_ERROR = (
    "RuntimeError: cross-project quiet gate exhausted its single bounded attempt"
)


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def bound(path: Path, label: str) -> dict[str, str]:
    path = path.resolve()
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"{label} regular evidence required: {path}")
    return {"path": str(path), "sha256": base.alg.sha(path)}


def _fresh_reconstructed(state: Mapping[str, Any]) -> dict[str, Any]:
    # The legacy checkpoint builder imports the original seven-parameter table
    # root.  This recovery belongs to the sealed v54 Top-4 successor, so bind
    # the exact v52/v54 table routing before rebuilding fresh-search state.
    top4 = runtime_v54.v53.v52
    base.alg.TABLE_ROOT = top4.TABLE_ROOT
    base.alg.TABLES = copy.deepcopy(top4.TABLES)
    prior_path = Path(str(state.get("checkpoint_path") or "")).resolve()
    checkpoint = verifier.verify_checkpoint(prior_path)
    reconstructed = copy.deepcopy(mapping(
        checkpoint.get("reconstructed_state"), "prior reconstructed state",
    ))
    reconstructed["outer_attempt"] = 0
    reconstructed["accepted_rounds"] = {name: 0 for name in SCENES}
    reconstructed["rejected_outer_attempts"] = {name: 0 for name in SCENES}
    reconstructed["acceptance_tier"] = {name: "fallback" for name in SCENES}
    reconstructed["consecutive_failed_rounds"] = {name: 0 for name in SCENES}
    reconstructed["interval_stable_rounds"] = {name: 0 for name in SCENES}
    reconstructed["stable_intervals"] = {name: None for name in SCENES}
    reconstructed["converged"] = {name: False for name in SCENES}
    reconstructed["replayed_param_keys_by_scene"] = {
        name: [] for name in SCENES
    }
    reconstructed["full_plan_replay_history_v2"] = {
        name: [] for name in SCENES
    }
    reconstructed["post_target_stability_confirmation_counts"] = {
        name: 0 for name in SCENES
    }
    reconstructed["params_by_scene"] = {
        name: copy.deepcopy(base.alg.DEFAULTS) for name in SCENES
    }
    for name in SCENES:
        table = base.alg.load(base.alg.TABLE_ROOT / base.alg.TABLES[name])
        reconstructed["active_by_scene"][name] = list(
            table["parameter_space"]["active_params"]
        )
        reconstructed["ranges_by_scene"][name] = copy.deepcopy(
            table["parameter_space"]["ranges"]
        )
    return reconstructed


def _verify_interrupted_outer(
    run_root: Path, state: Mapping[str, Any], scene: str, completed: int,
) -> dict[str, Any]:
    outer = run_root / f"outer_{completed + 1:03d}"
    ranking = outer / "predicted_rankings.json"
    gate = outer / "top5_batch_1_cross_project_wave_quiet_gate_attempt_001.json"
    gate_value = base.alg.load(gate)
    samples = list(gate_value.get("samples") or [])
    if (
        not outer.is_dir()
        or not ranking.is_file()
        or list(outer.glob("*.decision.json"))
        or (outer / "top5_batch_1/batch_result.json").exists()
        or gate_value.get("status") != "BLOCKED_FAIL_CLOSED"
        or gate_value.get("errors") != ["coordinated_quiet_window_timeout"]
        or float(gate_value.get("required_clean_seconds") or 0) != 15.0
        or float(gate_value.get("maximum_wait_seconds") or 0) != 60.0
        or float(gate_value.get("observed_seconds") or 0) < 60.0
        or not samples
        or not any(row.get("persistent_d_state_pids") for row in samples)
        or any(
            set(row.get("errors") or [])
            - {"host_uninterruptible_sleep_present"}
            for row in samples
        )
        or any(
            (row.get("co_resident_pg_isolation") or {}).get("passed") is not True
            for row in samples
        )
    ):
        raise RuntimeError("interrupted rank-1 quiet gate evidence differs")
    ranking_value = base.alg.load(ranking)
    if (
        int(ranking_value.get("outer_attempt") or 0) != completed + 1
        or scene not in (ranking_value.get("active_decision_scenes") or [])
        or ranking_value.get("formal_evidence_eligible") is not True
    ):
        raise RuntimeError("interrupted outer ranking differs")
    return {
        "predicted_rankings": bound(ranking, "interrupted rankings"),
        "blocked_gate": bound(gate, "blocked quiet gate"),
        "formal_state_credit_increment": 0,
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
        or state.get("error") != QUIET_ERROR
        or state.get("fresh_search") is not True
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("formal_evidence_eligible") is not True
        or state.get("accepted_state_credit_prohibited") is not False
        or len(active) != 1
        or active[0] not in SCENES
    ):
        raise RuntimeError("independent fresh quiet-gate source differs")
    scene = active[0]
    completed = int(state.get("outer_attempt") or 0)
    if completed < 1:
        raise RuntimeError("no completed outer boundary is available")

    reconstructed = _fresh_reconstructed(state)
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
    reconstructed["incumbents"][scene] = copy.deepcopy(
        mapping(baseline_incumbents[scene], "formal scene baseline incumbent")
    )

    evidence: dict[str, Any] = {
        "source_state": bound(state_path, "source state"),
        "source_checkpoint": bound(
            Path(str(state["checkpoint_path"])), "source checkpoint",
        ),
        "baseline": bound(baseline_path, "formal baseline"),
        "outers": {},
    }
    for outer in range(1, completed + 1):
        root = run_root / f"outer_{outer:03d}"
        ranking_path = root / "predicted_rankings.json"
        companion_path = root / "companion_audit.json"
        decision_path = root / f"{scene}.decision.json"
        decision = base.alg.load(decision_path)
        rows = [mapping(row, "decision evaluation") for row in decision.get("evaluations") or []]
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
            measurements = mapping(batch.get("measurements"), "batch measurements")
            if (
                batch.get("status") != "PASS"
                or batch.get("failures") not in ([], None)
                or len(measurements) != expected
                or any(
                    mapping(row, "batch measurement").get("scene") != scene
                    or mapping(row, "batch measurement").get("hard_gate_passed") is not True
                    or mapping(row, "batch measurement").get("coverage_complete") is not True
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
        moved = prior.moved_accepted_rows(
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

        tier = mapping(decision.get("tier_progress"), "tier progress")
        convergence = mapping(decision.get("interval_convergence"), "convergence")
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
            reconstructed["accepted_rounds"][scene] != int(decision["accepted_rounds"])
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

    for field in (
        "accepted_rounds", "rejected_outer_attempts", "acceptance_tier",
        "consecutive_failed_rounds", "interval_stable_rounds",
        "stable_intervals", "converged", "full_plan_replay_history_v2",
        "post_target_stability_confirmation_counts",
    ):
        if reconstructed[field][scene] != state[field][scene]:
            raise RuntimeError(f"source {field} differs after reconstruction")
    reconstructed["outer_attempt"] = completed
    interrupted = _verify_interrupted_outer(
        run_root, state, scene, completed,
    )
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
        "independent_scene_quiet_gate_boundary": {
            "scene": scene,
            "completed_outer_attempts": completed,
            "discarded_interrupted_outer": completed + 1,
            "completed_rounds_preserved": True,
            "interruption": interrupted,
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
        "scene": document["independent_scene_quiet_gate_boundary"]["scene"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

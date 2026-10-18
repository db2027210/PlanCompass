#!/usr/bin/env python3
"""Seal completed rounds from one independent Algorithm C scene."""

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
from scripts import build_mysql_algorithm_c_outer_checkpoint_v2 as base
from scripts import build_mysql_algorithm_c_outer_checkpoint_v4 as verifier
from scripts import mysql_algorithm_b_pg_outer_policy_v31 as policy
from mysql_algorithm_c_plan_replay_pruning import record_outcomes


SCENES = tuple(base.alg.SCENES)


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def bound(path: Path, label: str) -> dict[str, Any]:
    path = path.resolve()
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"{label} evidence differs: {path}")
    return {"path": str(path), "sha256": base.alg.sha(path)}


def build(run_root: Path) -> dict[str, Any]:
    run_root = run_root.resolve()
    state_path = run_root / "run_state.json"
    state = base.alg.load(state_path)
    active = list(state.get("active_scenes") or [])
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error")
        != "RuntimeError: accepted candidate has no moved declared parameter"
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("formal_evidence_eligible") is not True
        or state.get("accepted_state_credit_prohibited") is not False
        or len(active) != 1
        or active[0] not in SCENES
    ):
        raise RuntimeError("independent no-move source boundary differs")
    scene = active[0]
    completed = int(state.get("outer_attempt") or 0)
    if completed < 1 or int((state.get("accepted_rounds") or {}).get(scene) or 0) < 1:
        raise RuntimeError("independent completed-round credit is absent")
    failed_outer = run_root / f"outer_{completed + 1:03d}"
    if not failed_outer.is_dir() or list(failed_outer.glob("*.decision.json")):
        raise RuntimeError("no-move failure is not before decision persistence")

    source_checkpoint_path = Path(str(state["checkpoint_path"])).resolve()
    source_checkpoint = verifier.verify_checkpoint(source_checkpoint_path)
    freeze_path = Path(str(state["freeze_path"])).resolve()
    model_path = Path(str(state["resource_model_contract_path"])).resolve()
    reconstructed = copy.deepcopy(source_checkpoint["reconstructed_state"])
    for name in (
        "accepted_rounds", "rejected_outer_attempts", "acceptance_tier",
        "consecutive_failed_rounds", "interval_stable_rounds",
        "stable_intervals", "converged",
    ):
        reconstructed[name] = {
            candidate: (False if name == "converged" else None)
            for candidate in SCENES
        }
    reconstructed["accepted_rounds"] = {candidate: 0 for candidate in SCENES}
    reconstructed["rejected_outer_attempts"] = {candidate: 0 for candidate in SCENES}
    reconstructed["acceptance_tier"] = {candidate: "fallback" for candidate in SCENES}
    reconstructed["consecutive_failed_rounds"] = {candidate: 0 for candidate in SCENES}
    reconstructed["interval_stable_rounds"] = {candidate: 0 for candidate in SCENES}
    reconstructed["stable_intervals"] = {candidate: None for candidate in SCENES}
    reconstructed["converged"] = {candidate: False for candidate in SCENES}
    reconstructed["full_plan_replay_history_v2"] = {candidate: [] for candidate in SCENES}
    reconstructed["replayed_param_keys_by_scene"] = {candidate: [] for candidate in SCENES}
    reconstructed["params_by_scene"] = {
        candidate: copy.deepcopy(base.alg.DEFAULTS) for candidate in SCENES
    }
    reconstructed["active_by_scene"] = {}
    reconstructed["ranges_by_scene"] = {}
    for candidate in SCENES:
        table = base.alg.load(base.alg.TABLE_ROOT / base.alg.TABLES[candidate])
        reconstructed["active_by_scene"][candidate] = list(
            table["parameter_space"]["active_params"]
        )
        reconstructed["ranges_by_scene"][candidate] = copy.deepcopy(
            table["parameter_space"]["ranges"]
        )
    baseline_binding = mapping(
        state.get("formal_incumbent_baseline"), "formal baseline binding"
    )
    baseline_path = Path(str(baseline_binding["path"])).resolve()
    baseline = base.alg.load(baseline_path)
    reconstructed["incumbents"] = copy.deepcopy(
        source_checkpoint["reconstructed_state"]["incumbents"]
    )
    reconstructed["incumbents"][scene] = copy.deepcopy(
        mapping(baseline_binding.get("incumbents"), "baseline incumbents")[scene]
    )
    evidence: dict[str, Any] = {
        "source_state": bound(state_path, "source state"),
        "source_checkpoint": bound(source_checkpoint_path, "source checkpoint"),
        "baseline": bound(baseline_path, "formal baseline"),
        "outers": {},
    }

    for outer in range(1, completed + 1):
        root = run_root / f"outer_{outer:03d}"
        decision_path = root / f"{scene}.decision.json"
        decision = base.alg.load(decision_path)
        rows = [mapping(row, "decision evaluation") for row in decision.get("evaluations") or []]
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
        accepted_rows = [
            {"row": row, "decision": mapping(row.get("outer_decision"), "outer decision")}
            for row in rows
            if mapping(row.get("outer_decision"), "outer decision").get("accepted") is True
        ]
        accepted_rows.sort(key=policy.accepted_selection_key, reverse=True)
        accepted = decision.get("accepted") is True
        if accepted != bool(accepted_rows):
            raise RuntimeError(f"outer {outer} accepted evidence differs")
        if accepted:
            chosen = accepted_rows[0]["row"]
            ranking = mapping(chosen.get("candidate_ranking"), "candidate ranking")
            subset = policy.moved_shrink_subset(
                incumbent_params=reconstructed["params_by_scene"][scene],
                candidate_ranking=ranking,
                active_params=reconstructed["active_by_scene"][scene],
            )
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
            )
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
            "predicted_rankings": bound(root / "predicted_rankings.json", "ranking"),
            "companion_audit": bound(root / "companion_audit.json", "companion"),
            "batches": [
                bound(root / f"top5_batch_{index}/batch_result.json", "batch")
                for index in (1, 2, 3)
            ],
            "decisions": {scene: bound(decision_path, "decision")},
        }

    reconstructed["outer_attempt"] = completed
    reconstructed["post_target_stability_confirmation_counts"] = {
        candidate: 0 for candidate in SCENES
    }
    for field in (
        "accepted_rounds", "rejected_outer_attempts", "acceptance_tier",
        "consecutive_failed_rounds", "interval_stable_rounds",
        "stable_intervals", "converged",
    ):
        if reconstructed[field][scene] != state[field][scene]:
            raise RuntimeError(f"source {field} differs after reconstruction")
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
        "freeze_path": str(freeze_path),
        "freeze_sha256": base.alg.sha(freeze_path),
        "resource_model_contract_path": str(model_path),
        "resource_model_contract_sha256": base.alg.sha(model_path),
        "reconstructed_state": reconstructed,
        "evidence": evidence,
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": base.alg.sha(Path(__file__).resolve()),
        "independent_scene_boundary": {
            "scene": scene,
            "source_failed_outer": completed + 1,
            "source_failure": state["error"],
            "failed_outer_credit": False,
            "completed_rounds_preserved": True,
        },
    }
    document["checkpoint_sha256"] = base._canonical_sha256(document)
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
        "status": "PREPARED", "checkpoint": str(path),
        "completed_outer_attempts": document["completed_outer_attempts"],
        "scene": document["independent_scene_boundary"]["scene"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

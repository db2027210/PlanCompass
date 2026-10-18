#!/usr/bin/env python3
"""Rollback one invalid C shrink that omitted current-interval coverage.

The physical candidate measurements remain SHA-bound and their parameter
signatures remain replay-deduplicated.  Only the second-rejection credit,
forced shrink, and convergence derived from the incomplete search are removed.
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

from scripts import build_mysql_algorithm_c_outer_checkpoint_v4 as verifier
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg


SCENES = tuple(alg.SCENES)
POLICY = "mysql_bc_qps_strict_two_reject_incumbent_shrink_v2"


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def binding(path: Path) -> dict[str, str]:
    resolved = path.resolve()
    if not resolved.is_file() or resolved.is_symlink():
        raise RuntimeError(f"regular evidence required: {resolved}")
    return {"path": str(resolved), "sha256": alg.sha(resolved)}


def physical_parameter_keys(decision: Mapping[str, Any]) -> list[str]:
    keys = []
    for raw in decision.get("evaluations") or []:
        row = mapping(raw, "evaluation")
        if row.get("cached_valid_replay_redecision") is True:
            continue
        keys.append(alg.optimizer_params_key(mapping(row.get("params"), "params")))
    return list(dict.fromkeys(keys))


def build(checkpoint_path: Path, source_root: Path, scene: str) -> dict[str, Any]:
    checkpoint = verifier.verify_checkpoint(checkpoint_path.resolve())
    source_root = source_root.resolve()
    completed = int(checkpoint.get("completed_outer_attempts") or 0)
    if completed != 14 or scene not in SCENES:
        raise RuntimeError("exact completed outer-14 checkpoint required")
    if Path(str(checkpoint.get("source_run_root") or "")).resolve() != source_root:
        raise RuntimeError("checkpoint source run differs")
    outer13_path = source_root / f"outer_{completed - 1:03d}" / f"{scene}.decision.json"
    outer14_path = source_root / f"outer_{completed:03d}" / f"{scene}.decision.json"
    ranking14_path = source_root / f"outer_{completed:03d}" / "predicted_rankings.json"
    decision13 = alg.load(outer13_path)
    decision14 = alg.load(outer14_path)
    cycle13 = mapping(decision13.get("rejection_cycle"), "outer13 cycle")
    cycle14 = mapping(decision14.get("rejection_cycle"), "outer14 cycle")
    convergence13 = mapping(
        decision13.get("interval_convergence"), "outer13 convergence"
    )
    tier13 = mapping(decision13.get("tier_progress"), "outer13 tier")
    range_changes = mapping(cycle14.get("range_changes"), "outer14 range changes")
    ranking14 = alg.load(ranking14_path)
    coverage_path = source_root / f"outer_{completed:03d}" / (
        "current_interval_plan_coverage_audit.json"
    )
    if (
        decision13.get("scene") != scene
        or int(decision13.get("outer_attempt") or 0) != 13
        or decision13.get("accepted") is not False
        or cycle13.get("policy_id") != POLICY
        or cycle13.get("consecutive_rejections_before") != 0
        or cycle13.get("consecutive_rejections_after") != 1
        or cycle13.get("forced_incumbent_shrink") is not False
        or decision14.get("scene") != scene
        or int(decision14.get("outer_attempt") or 0) != 14
        or decision14.get("accepted") is not False
        or cycle14.get("policy_id") != POLICY
        or cycle14.get("consecutive_rejections_before") != 1
        or cycle14.get("consecutive_rejections_after") != 0
        or cycle14.get("forced_incumbent_shrink") is not True
        or not range_changes
        or convergence13.get("converged") is not False
        or int(tier13.get("consecutive_failed_rounds") or 0) != 1
        or coverage_path.exists()
        or ranking14.get("current_interval_plan_coverage_admission")
        not in (None, {})
    ):
        raise RuntimeError("missing-coverage shrink evidence differs")

    document = copy.deepcopy(checkpoint)
    reconstructed = mapping(document.get("reconstructed_state"), "state")
    active = list(reconstructed["active_by_scene"][scene])
    if set(range_changes) != set(active):
        raise RuntimeError("invalidated shrink range set differs")
    reconstructed["ranges_by_scene"][scene] = {
        name: {
            "lower": float(mapping(range_changes[name], name)["old_lower"]),
            "upper": float(mapping(range_changes[name], name)["old_upper"]),
        }
        for name in active
    }
    reconstructed["accepted_rounds"][scene] = int(decision13["accepted_rounds"])
    reconstructed["rejected_outer_attempts"][scene] = int(
        decision13["rejected_outer_attempts"]
    )
    reconstructed["acceptance_tier"][scene] = str(tier13["acceptance_tier"])
    reconstructed["consecutive_failed_rounds"][scene] = 1
    reconstructed["interval_stable_rounds"][scene] = 0
    reconstructed["stable_intervals"][scene] = None
    reconstructed["converged"][scene] = False
    reconstructed["full_plan_replay_history_v2"][scene] = [
        row for row in reconstructed["full_plan_replay_history_v2"][scene]
        if int(mapping(row, "plan replay row").get("outer_round") or 0) <= 13
    ]
    replayed = physical_parameter_keys(decision13) + physical_parameter_keys(decision14)
    reconstructed["replayed_param_keys_by_scene"][scene] = list(dict.fromkeys(replayed))
    reconstructed["outer_attempt"] = 13

    evidence = mapping(document.get("evidence"), "evidence")
    evidence["outers"] = {
        key: value for key, value in mapping(evidence.get("outers"), "outers").items()
        if int(key) <= 13
    }
    document["completed_outer_attempts"] = 13
    document["reconstructed_state"] = reconstructed
    document["evidence"] = evidence
    document["builder_source_path"] = str(Path(__file__).resolve())
    document["builder_source_sha256"] = alg.sha(Path(__file__).resolve())
    document["independent_scene_boundary"] = {
        "scene": scene,
        "completed_outer_attempts": 13,
        "discarded_incomplete_outer": 14,
        "completed_rounds_preserved": True,
    }
    document["missing_current_interval_coverage_rollback"] = {
        "schema_version": "mysql_algorithm_c_missing_coverage_rollback_v1",
        "status": "PASS",
        "scene": scene,
        "invalidated_outer_attempt": 14,
        "rollback_to_completed_outer_attempt": 13,
        "invalidated_second_rejection_credit": 1,
        "invalidated_forced_incumbent_shrink": True,
        "invalidated_convergence": True,
        "valid_physical_measurements_reused_only_for_replay_dedup": True,
        "qps_imported_into_candidate_generation_or_ranking": False,
        "fresh_r0_required": False,
        "outer13_decision": binding(outer13_path),
        "outer14_decision": binding(outer14_path),
        "outer14_rankings": binding(ranking14_path),
        "outer14_coverage_audit_absent": True,
        "preserved_replay_dedup_parameter_count": len(
            reconstructed["replayed_param_keys_by_scene"][scene]
        ),
    }
    document["independent_round_boundary_successor"] = {
        "discarded_incomplete_outer": 14,
        "discarded_outer_credit": 1,
        "accepted_round_credit_changed": False,
        "rejected_outer_credit_changed": True,
        "invalid_semantic_shrink_only": True,
        "physical_measurements_deleted": False,
        "physical_top5_required_on_resume": True,
    }
    payload = copy.deepcopy(document)
    payload.pop("checkpoint_sha256", None)
    document["checkpoint_sha256"] = verifier.predecessor.predecessor._canonical_sha256(
        payload
    )
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--scene", choices=SCENES, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.output_dir.name) is None:
        parser.error("safe output directory required")
    if args.output_dir.exists() or args.output_dir.is_symlink():
        raise RuntimeError("fresh rollback output required")
    document = build(args.checkpoint, args.source_root, args.scene)
    args.output_dir.mkdir(parents=True, mode=0o700)
    output = args.output_dir / "prepared_binding.json"
    alg.atomic(output, document)
    verifier.verify_checkpoint(output)
    print(json.dumps({
        "status": "PREPARED",
        "checkpoint": str(output),
        "scene": args.scene,
        "completed_outer_attempts": 13,
        "preserved_replay_dedup_parameter_count": document[
            "missing_current_interval_coverage_rollback"
        ]["preserved_replay_dedup_parameter_count"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

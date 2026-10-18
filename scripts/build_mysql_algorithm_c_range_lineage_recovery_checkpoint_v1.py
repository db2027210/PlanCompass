#!/usr/bin/env python3
"""Recover a completed rejected outer from a mis-reconstructed range lineage.

The v172 XP/CPU checkpoint replayed historical decisions with a different
accepted-candidate selection than the live controller.  Its reconstructed
range therefore excluded the live incumbent.  Outer 17 completed every real
measurement and lane-paired incumbent revalidation, then failed closed when
the second-rejection shrink checked that invalid range.  This builder restores
the SHA-bound live outer-15 range, applies the outer-16 and outer-17 rejection
transitions, and preserves all completed physical evidence without replay.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_outer_checkpoint_v2 as base
from scripts import build_mysql_algorithm_c_outer_checkpoint_v4 as verifier
from scripts import build_mysql_algorithm_c_independent_round_boundary_checkpoint_v4 as roundbuilder
from scripts import build_mysql_algorithm_c_float_boundary_recovery_checkpoint_v2 as helpers
from scripts import mysql_outer_rejection_cycle_v2 as rejection_cycle


SCENE = "hybench_xp_cpu"
ERROR = "ValueError: disk_temptable_row_cost incumbent lies outside current range"
SCHEMA = "mysql_algorithm_c_range_lineage_recovery_v1"


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def bound(path: Path, label: str) -> dict[str, str]:
    path = path.resolve()
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"{label} regular evidence required: {path}")
    return {"path": str(path), "sha256": base.alg.sha(path)}


def scene_value(document: Mapping[str, Any], field: str) -> Any:
    return mapping(document.get(field), field)[SCENE]


def range_contains(ranges: Mapping[str, Any], params: Mapping[str, Any], active: list[str]) -> bool:
    return all(
        float(mapping(ranges[name], name)["lower"])
        <= float(params[name])
        <= float(mapping(ranges[name], name)["upper"])
        for name in active
    )


def evidence_for_outer(
    run_root: Path, outer: int, decision_path: Path,
) -> dict[str, Any]:
    root = run_root / f"outer_{outer:03d}"
    batches = [
        bound(path, f"outer {outer} batch")
        for path in helpers.candidate_batches(root)
    ]
    if outer == 17:
        batches.append(bound(
            root / "lane_paired_incumbent_revalidation/batch_result.json",
            "outer 17 lane-paired incumbent control",
        ))
    return {
        "predicted_rankings": bound(
            root / "predicted_rankings.json", f"outer {outer} rankings",
        ),
        "companion_audit": bound(
            root / "companion_audit.json", f"outer {outer} companion audit",
        ),
        "batches": batches,
        "decisions": {SCENE: bound(decision_path, f"outer {outer} decision")},
    }


def build(run_root: Path, output_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    run_root = run_root.resolve()
    state_path = run_root / "run_state.json"
    state = base.alg.load(state_path)
    if (
        state.get("algorithm") != "C"
        or state.get("async_scene") != SCENE
        or list(state.get("active_scenes") or []) != [SCENE]
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error") != ERROR
        or int(state.get("outer_attempt") or 0) != 16
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("formal_evidence_eligible") is not True
    ):
        raise RuntimeError("range-lineage failure source differs")

    prior_path = Path(str(state.get("checkpoint_path") or "")).resolve()
    prior = verifier.verify_checkpoint(prior_path)
    if int(prior.get("completed_outer_attempts") or 0) != 15:
        raise RuntimeError("range-lineage predecessor boundary differs")
    prior_source_binding = mapping(
        mapping(prior.get("evidence"), "prior evidence").get("source_state"),
        "prior source state binding",
    )
    prior_source_path = Path(str(prior_source_binding.get("path") or "")).resolve()
    if base.alg.sha(prior_source_path) != prior_source_binding.get("sha256"):
        raise RuntimeError("prior live state binding differs")
    live15 = base.alg.load(prior_source_path)

    reconstructed = copy.deepcopy(mapping(
        prior.get("reconstructed_state"), "prior reconstructed state",
    ))
    active = list(scene_value(reconstructed, "active_by_scene"))
    params = mapping(scene_value(state, "params_by_scene"), "current params")
    live15_params = mapping(scene_value(live15, "params_by_scene"), "live15 params")
    live15_ranges = mapping(scene_value(live15, "ranges_by_scene"), "live15 ranges")
    bad_checkpoint_ranges = mapping(
        scene_value(reconstructed, "ranges_by_scene"), "checkpoint ranges",
    )
    if (
        int(live15.get("outer_attempt") or 0) != 15
        or base.alg.optimizer_params_key(live15_params)
        != base.alg.optimizer_params_key(params)
        or not range_contains(live15_ranges, params, active)
        or range_contains(bad_checkpoint_ranges, params, active)
        or int(scene_value(live15, "accepted_rounds")) != 8
        or int(scene_value(live15, "rejected_outer_attempts")) != 7
        or int(scene_value(live15, "consecutive_failed_rounds")) != 0
    ):
        raise RuntimeError("authoritative outer-15 range lineage differs")

    decision16_path = run_root / f"outer_016/{SCENE}.decision.json"
    decision16 = base.alg.load(decision16_path)
    cycle16 = mapping(decision16.get("rejection_cycle"), "outer16 cycle")
    if (
        decision16.get("accepted") is not False
        or decision16.get("formal_evidence_eligible") is not True
        or cycle16.get("consecutive_rejections_before") != 0
        or cycle16.get("consecutive_rejections_after") != 1
        or cycle16.get("forced_incumbent_shrink") is not False
    ):
        raise RuntimeError("outer-16 first rejection differs")

    outer17 = run_root / "outer_017"
    if (outer17 / f"{SCENE}.decision.json").exists():
        raise RuntimeError("outer-17 unexpectedly has a completed decision")
    paired_path = outer17 / "lane_paired_qps_decision.json"
    paired = base.alg.load(paired_path)
    paired_rows = list(paired.get("candidate_rows") or [])
    candidates = helpers.measured_candidates(outer17, SCENE)
    if (
        paired.get("status") != "PASS"
        or paired.get("formal_evidence_eligible") is not True
        or int(paired.get("outer_attempt") or 0) != 17
        or int(paired.get("paired_eligible_count", -1)) != 0
        or len(paired_rows) != len(candidates)
        or not paired_rows
        or any(row.get("accepted_after_pairing") is not False for row in paired_rows)
        or any(not math.isfinite(float(row["lane_normalized_db_qps"])) for row in paired_rows)
    ):
        raise RuntimeError("outer-17 paired rejection evidence differs")

    after16, progress16 = rejection_cycle.advance_rejection_cycle(
        accepted=False,
        consecutive_rejections=0,
        ranges=live15_ranges,
        incumbent_params=params,
        active_params=active,
    )
    updated_ranges, progress17 = rejection_cycle.advance_rejection_cycle(
        accepted=False,
        consecutive_rejections=int(progress16["consecutive_rejections_after"]),
        ranges=after16,
        incumbent_params=params,
        active_params=active,
    )
    if (
        progress16.get("forced_incumbent_shrink") is not False
        or progress17.get("forced_incumbent_shrink") is not True
        or progress17.get("consecutive_rejections_after") != 0
        or not range_contains(updated_ranges, params, active)
    ):
        raise RuntimeError("corrected two-rejection transition differs")

    tables = roundbuilder.frozen_fresh_search_tables(state)
    initial_ranges = mapping(
        mapping(tables[SCENE].get("parameter_space"), "parameter space").get("ranges"),
        "initial ranges",
    )
    convergence = rejection_cycle.convergence_document(
        ranges=updated_ranges,
        initial_ranges=initial_ranges,
        active_params=active,
    )

    copied_fields = (
        "incumbents", "params_by_scene",
        "accepted_rounds", "rejected_outer_attempts", "acceptance_tier",
        "full_plan_replay_history_v2", "valid_replay_archive_by_scene",
        "previously_accepted_parameter_signatures",
    )
    for field in copied_fields:
        source = mapping(state.get(field), f"source {field}")
        target = mapping(reconstructed.get(field) or {}, f"checkpoint {field}")
        target[SCENE] = copy.deepcopy(source[SCENE])
        reconstructed[field] = target
    for field, value in (
        ("ranges_by_scene", updated_ranges),
        ("consecutive_failed_rounds", 0),
        ("interval_stable_rounds", 0),
        ("stable_intervals", None),
        ("converged", bool(convergence["converged"])),
        ("replayed_param_keys_by_scene", []),
    ):
        target = mapping(reconstructed.get(field), f"checkpoint {field}")
        target[SCENE] = copy.deepcopy(value)
        reconstructed[field] = target
    reconstructed["outer_attempt"] = 17
    if (
        int(scene_value(reconstructed, "accepted_rounds")) != 8
        or int(scene_value(reconstructed, "rejected_outer_attempts")) != 9
    ):
        raise RuntimeError("recovered counters differ")

    decision = {
        "schema_version": SCHEMA,
        "status": "RECOVERED_REJECTED_OUTER",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": SCENE,
        "outer_attempt": 17,
        "accepted": False,
        "formal_evidence_eligible": True,
        "candidate_summaries": candidates,
        "lane_paired_decision": bound(paired_path, "lane-paired decision"),
        "lane_paired_candidate_rows": paired_rows,
        "accepted_round_credit_increment": 0,
        "rejected_outer_attempt_increment": 1,
        "accepted_rounds": 8,
        "rejected_outer_attempts": 9,
        "rejection_cycle": progress17,
        "interval_convergence": convergence,
        "source_failure": {"path": str(state_path), "error": ERROR},
        "failure_classification": "checkpoint_range_lineage_reconstruction_mismatch",
        "authoritative_outer15_range_source": bound(
            prior_source_path, "authoritative outer15 state",
        ),
        "invalid_reconstructed_outer15_ranges": bad_checkpoint_ranges,
        "corrected_outer15_ranges": live15_ranges,
        "candidate_measurements_replayed": False,
        "candidate_measurements_sha_bound_and_reused": True,
    }
    decision_dir = output_dir / "outer_017"
    decision_dir.mkdir(parents=True, mode=0o700)
    decision_path = decision_dir / f"{SCENE}.decision.json"
    base.alg.atomic(decision_path, decision)

    baseline_path = Path(str(
        mapping(state.get("formal_incumbent_baseline"), "baseline").get("path") or ""
    )).resolve()
    evidence = {
        "source_checkpoint": bound(prior_path, "source checkpoint"),
        "source_state": bound(state_path, "failed source state"),
        "baseline": bound(baseline_path, "source baseline"),
        "authoritative_outer15_state": bound(
            prior_source_path, "authoritative outer15 state",
        ),
        "lane_paired_outer17_decision": bound(paired_path, "lane-paired decision"),
        "outers": {
            "16": evidence_for_outer(run_root, 16, decision16_path),
            "17": evidence_for_outer(run_root, 17, decision_path),
        },
    }
    builder = Path(__file__).resolve()
    checkpoint = {
        "schema_version": base.CONTRACT,
        "status": "PREPARED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "source_run_root": str(output_dir.resolve()),
        "completed_outer_attempts": 17,
        "workload_started": False,
        "fresh_r0_complete": False,
        "formal_outer_started": False,
        "freeze_path": state["freeze_path"],
        "freeze_sha256": state["freeze_sha256"],
        "resource_model_contract_path": state["resource_model_contract_path"],
        "resource_model_contract_sha256": state["resource_model_contract_sha256"],
        "reconstructed_state": reconstructed,
        "evidence": evidence,
        "builder_source_path": str(builder),
        "builder_source_sha256": base.alg.sha(builder),
        "independent_round_boundary_successor": True,
        "independent_scene_boundary": {
            "scene": SCENE,
            "completed_outer_attempts": 17,
            "completed_rounds_preserved": True,
            "recovered_failed_outer": 17,
            "candidate_measurements_replayed": False,
            "range_lineage_repaired": True,
            "range_policy": rejection_cycle.POLICY_ID,
        },
        "range_lineage_recovery": {
            "schema_version": SCHEMA,
            "mis_reconstructed_checkpoint_detected": True,
            "authoritative_live_outer15_state_used": True,
            "outer16_first_rejection_preserved": True,
            "outer17_second_rejection_preserved": True,
            "forced_incumbent_shrink_recomputed": True,
            "completed_measurements_replayed": False,
            "candidate_set_or_workload_changed": False,
            "resource_envelope_changed": False,
        },
    }
    checkpoint["checkpoint_sha256"] = base._canonical_sha256(checkpoint)
    return checkpoint, decision


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.output_dir.name) is None:
        parser.error("unsafe output directory name")
    if args.output_dir.exists() or args.output_dir.is_symlink():
        raise RuntimeError(f"fresh recovery output required: {args.output_dir}")
    args.output_dir.mkdir(parents=True, mode=0o700)
    checkpoint, _decision = build(args.run_root, args.output_dir)
    path = args.output_dir / "prepared_binding.json"
    base.alg.atomic(path, checkpoint)
    verifier.verify_checkpoint(path)
    print(json.dumps({
        "status": "PREPARED",
        "checkpoint": str(path),
        "completed_outer_attempts": 17,
        "normalized_width": _decision["interval_convergence"]["normalized_width"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

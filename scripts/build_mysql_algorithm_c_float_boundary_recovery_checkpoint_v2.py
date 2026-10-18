#!/usr/bin/env python3
"""Recover any completed rejected C outer after FLOAT-boundary shrink failure."""

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

from scripts import build_mysql_algorithm_c_independent_round_boundary_checkpoint_v4 as roundbuilder
from scripts import build_mysql_algorithm_c_outer_checkpoint_v2 as checkpoint_base
from scripts import build_mysql_algorithm_c_outer_checkpoint_v4 as checkpoint_verifier
from scripts import mysql_outer_rejection_cycle_v2 as rejection_cycle


FLOAT_BOUNDARY_ERROR = re.compile(
    r"ValueError: ([a-z0-9_]+) incumbent lies outside current range"
)


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def bound(path: Path, label: str) -> dict[str, str]:
    path = path.resolve()
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"{label} regular evidence required: {path}")
    return {"path": str(path), "sha256": checkpoint_base.alg.sha(path)}


def candidate_batches(outer_root: Path) -> list[Path]:
    paths = [
        *outer_root.glob("top5_batch_*/batch_result.json"),
        *outer_root.glob("fallback_plan_change/wave_*/batch_result.json"),
        *outer_root.glob("full_range_plan_basin_escape/wave_*/batch_result.json"),
    ]
    return sorted({path.resolve() for path in paths}, key=str)


def measured_candidates(outer_root: Path, scene: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in candidate_batches(outer_root):
        document = checkpoint_base.alg.load(path)
        measurements = mapping(document.get("measurements"), "measurements")
        if (
            document.get("status") != "PASS"
            or document.get("failures") not in ([], None)
        ):
            raise RuntimeError(f"failed outer candidate evidence: {path}")
        for worker_id, raw in measurements.items():
            row = mapping(raw, f"{worker_id} measurement")
            rank = int(row.get("rank") or 0)
            if rank <= 0:
                continue
            if (
                row.get("scene") != scene
                or row.get("hard_gate_passed") is not True
                or row.get("coverage_complete") is not True
            ):
                raise RuntimeError(f"invalid real candidate: {path}:{worker_id}")
            rows.append({
                "rank": rank,
                "worker_id": worker_id,
                "db_qps": float(row["db_qps"]),
                "params": copy.deepcopy(mapping(row.get("params"), "params")),
                "batch": bound(path, "candidate batch"),
            })
    rows.sort(key=lambda row: (int(row["rank"]), str(row["batch"]["path"])))
    return rows


def evidence_for_outer(
    run_root: Path, outer: int, decision_path: Path, scene: str,
) -> dict[str, Any]:
    root = run_root / f"outer_{outer:03d}"
    ranking = root / "predicted_rankings.json"
    companion = root / "companion_audit.json"
    return {
        "predicted_rankings": bound(ranking, f"outer {outer} rankings"),
        "companion_audit": bound(companion, f"outer {outer} companion audit"),
        "batches": [
            bound(path, f"outer {outer} batch")
            for path in candidate_batches(root)
        ],
        "decisions": {scene: bound(decision_path, f"outer {outer} decision")},
    }


def build(run_root: Path, output_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    run_root = run_root.resolve()
    state_path = run_root / "run_state.json"
    state = checkpoint_base.alg.load(state_path)
    scene = str(state.get("async_scene") or "")
    active_scenes = list(state.get("active_scenes") or [])
    error = str(state.get("error") or "")
    error_match = FLOAT_BOUNDARY_ERROR.fullmatch(error)
    completed = int(state.get("outer_attempt") or 0)
    failed_outer = completed + 1
    accepted_count = int(
        mapping(state.get("accepted_rounds"), "accepted").get(scene) or 0
    )
    rejected_count = int(
        mapping(state.get("rejected_outer_attempts"), "rejected").get(scene) or 0
    )
    consecutive = int(
        mapping(state.get("consecutive_failed_rounds"), "consecutive")
        .get(scene) or 0
    )
    if (
        state.get("algorithm") != "C"
        or scene not in checkpoint_base.alg.SCENES
        or active_scenes != [scene]
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or error_match is None
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("formal_evidence_eligible") is not True
        or completed < 1
        or rejected_count < 1
        or consecutive != 1
    ):
        raise RuntimeError("FLOAT-boundary failure source differs")

    prior_path = Path(str(state.get("checkpoint_path") or "")).resolve()
    prior = checkpoint_verifier.verify_checkpoint(prior_path)
    reconstructed = copy.deepcopy(mapping(
        prior.get("reconstructed_state"), "prior reconstructed state",
    ))
    prior_completed = int(prior.get("completed_outer_attempts") or 0)
    if prior_completed >= completed:
        raise RuntimeError("unexpected predecessor checkpoint boundary")

    outer_root = run_root / f"outer_{failed_outer:03d}"
    if (outer_root / f"{scene}.decision.json").exists():
        raise RuntimeError("source outer unexpectedly has a completed decision")
    for name in ("pg_outer_expansion_audit.json", "companion_audit.json"):
        document = checkpoint_base.alg.load(outer_root / name)
        if document.get("status") != "PASS":
            raise RuntimeError(f"outer {failed_outer} audit differs: {name}")

    incumbent = mapping(mapping(state.get("incumbents"), "incumbents")[scene], "incumbent")
    incumbent_qps = float(incumbent["db_qps"])
    candidates = measured_candidates(outer_root, scene)
    if (
        not 1 <= len(candidates) <= 10
        or any(float(row["db_qps"]) >= incumbent_qps for row in candidates)
    ):
        raise RuntimeError(
            f"outer {failed_outer} is not a strict-QPS rejected admitted batch"
        )

    params = mapping(mapping(state.get("params_by_scene"), "params")[scene], "scene params")
    ranges = mapping(mapping(state.get("ranges_by_scene"), "ranges")[scene], "scene ranges")
    active = list(mapping(reconstructed.get("active_by_scene"), "active")[scene])
    updated_ranges, rejection_progress = rejection_cycle.advance_rejection_cycle(
        accepted=False,
        consecutive_rejections=1,
        ranges=ranges,
        incumbent_params=params,
        active_params=active,
    )
    if (
        rejection_progress.get("forced_incumbent_shrink") is not True
        or rejection_progress.get("consecutive_rejections_after") != 0
        or (rejection_progress.get("range_changes") or {})
        .get(error_match.group(1), {})
        .get("incumbent_range_clamped") is not True
    ):
        raise RuntimeError("FLOAT-tolerant rejection-cycle recovery differs")

    tables = roundbuilder.frozen_fresh_search_tables(state)
    initial_ranges = mapping(
        mapping(tables[scene].get("parameter_space"), "parameter space")
        .get("ranges"),
        "initial ranges",
    )
    convergence = rejection_cycle.convergence_document(
        ranges=updated_ranges,
        initial_ranges=initial_ranges,
        active_params=active,
    )

    for field, value in (
        ("incumbents", incumbent),
        ("params_by_scene", params),
        ("ranges_by_scene", updated_ranges),
        ("accepted_rounds", accepted_count),
        ("rejected_outer_attempts", rejected_count),
        ("acceptance_tier", "fallback"),
        ("consecutive_failed_rounds", 0),
        ("interval_stable_rounds", 0),
        ("stable_intervals", None),
        ("converged", bool(convergence["converged"])),
        ("replayed_param_keys_by_scene", []),
        (
            "full_plan_replay_history_v2",
            copy.deepcopy(mapping(
                state.get("full_plan_replay_history_v2"), "plan history",
            )[scene]),
        ),
    ):
        target = mapping(reconstructed.get(field), f"checkpoint {field}")
        target[scene] = copy.deepcopy(value)
        reconstructed[field] = target
    reconstructed["outer_attempt"] = failed_outer

    recovered_decision = {
        "schema_version": "mysql_algorithm_c_float_boundary_recovery_decision_v1",
        "status": "RECOVERED_REJECTED_OUTER",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": scene,
        "outer_attempt": failed_outer,
        "accepted": False,
        "incumbent_db_qps": incumbent_qps,
        "candidate_summaries": candidates,
        "strict_qps_comparison_proved": True,
        "accepted_round_credit_increment": 0,
        "rejected_outer_attempt_increment": 1,
        "rejection_cycle": rejection_progress,
        "interval_convergence": convergence,
        "source_failure": {"path": str(state_path), "error": error},
        "float_boundary_parameter": error_match.group(1),
        "failure_classification": (
            "mysql_float_rounding_at_range_boundary_not_semantic_out_of_range"
        ),
        "candidate_measurements_replayed": False,
        "candidate_measurements_sha_bound_and_reused": True,
    }
    decision_path = output_dir / f"outer_{failed_outer:03d}_recovered_decision.json"
    checkpoint_base.alg.atomic(decision_path, recovered_decision)

    baseline_path = Path(str(
        mapping(state.get("formal_incumbent_baseline"), "baseline").get("path")
        or ""
    )).resolve()
    evidence = {
        "source_checkpoint": bound(prior_path, "source checkpoint"),
        "source_state": bound(state_path, "failed source state"),
        "baseline": bound(baseline_path, "source baseline"),
        "outers": {},
    }
    for outer in range(prior_completed + 1, completed + 1):
        evidence["outers"][str(outer)] = evidence_for_outer(
            run_root, outer,
            run_root / f"outer_{outer:03d}" / f"{scene}.decision.json",
            scene,
        )
    evidence["outers"][str(failed_outer)] = evidence_for_outer(
        run_root, failed_outer, decision_path, scene,
    )
    builder = Path(__file__).resolve()
    checkpoint = {
        "schema_version": checkpoint_base.CONTRACT,
        "status": "PREPARED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "source_run_root": str(run_root),
        "completed_outer_attempts": failed_outer,
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
        "builder_source_sha256": checkpoint_base.alg.sha(builder),
        "independent_round_boundary_successor": True,
        "independent_scene_boundary": {
            "scene": scene,
            "completed_outer_attempts": failed_outer,
            "completed_rounds_preserved": True,
            "recovered_failed_outer": failed_outer,
            "candidate_measurements_replayed": False,
            "float_boundary_tolerance_policy": rejection_cycle.POLICY_ID,
        },
    }
    checkpoint["checkpoint_sha256"] = checkpoint_base._canonical_sha256(checkpoint)
    return checkpoint, recovered_decision


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
    try:
        checkpoint, _decision = build(args.run_root, args.output_dir)
        path = args.output_dir / "prepared_binding.json"
        checkpoint_base.alg.atomic(path, checkpoint)
        checkpoint_verifier.verify_checkpoint(path)
    except BaseException:
        # Preserve the fresh directory and any audit written before failure.
        raise
    print(json.dumps({
        "status": "PREPARED",
        "checkpoint": str(path),
        "completed_outer_attempts": checkpoint["completed_outer_attempts"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

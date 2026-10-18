#!/usr/bin/env python3
"""Seal a fully converged companion-aware Algorithm C terminal checkpoint."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
from typing import Any, Mapping

from scripts import build_mysql_algorithm_c_outer_checkpoint_v4 as v4
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v20 as confirmation
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v26 as c26


base = v4.predecessor.predecessor
SCENES = {"job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io"}
ACTIVE_OUTER17_SCENES = {"job_memory", "hybench_xp_cpu", "hybench_xp_io"}


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def _expected_rank(scene: str, rank: int) -> int:
    return rank if scene in ACTIVE_OUTER17_SCENES else 0


def _companion_aware_verify_batch(
    path: Path, expected_workers: int, *, expected_rank: int | None = None,
) -> dict[str, Any]:
    value = base.alg.load(path)
    measurements = _mapping(value.get("measurements"), "batch measurements")
    if (
        value.get("status") != "PASS"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("failures") not in ([], None)
        or len(measurements) != expected_workers
        or {str(row.get("scene")) for row in measurements.values()} != SCENES
        or any(
            not isinstance(row, Mapping)
            or row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            or (
                expected_rank is not None
                and int(row.get("rank") or 0)
                != _expected_rank(str(row.get("scene")), expected_rank)
            )
            for row in measurements.values()
        )
    ):
        raise RuntimeError(f"companion-aware completed batch differs: {path}")
    return value


def _companion_aware_verify_timeout_batch(
    path: Path, expected_rank: int,
) -> dict[str, Any]:
    value = base.alg.load(path)
    measurements = _mapping(value.get("measurements"), "timeout measurements")
    failures = [
        _mapping(row, "timeout failure") for row in (value.get("failures") or [])
    ]
    timeout_tasks = {
        str(row.get("task")) for row in failures
        if str(row.get("error") or "").startswith("TimeoutExpired:")
        and "timed out after 900 seconds" in str(row.get("error") or "")
    }
    skipped = {
        str(row.get("task")) for row in failures
        if row.get("error") == "skipped_after_invalid_workload"
    }
    if (
        value.get("status") != "FAILED_CLOSED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or len(measurements) != len(SCENES)
        or {str(row.get("scene")) for row in measurements.values()} != SCENES
        or any(
            int(row.get("rank") or 0)
            != _expected_rank(str(row.get("scene")), expected_rank)
            for row in measurements.values()
        )
        or timeout_tasks != {"pair_1", "xp_cpu_w1", "xp_io_w1"}
        or skipped != {f"{worker}_plan_capture" for worker in measurements}
        or len(failures) != len(timeout_tasks) + len(skipped)
        or not (900 <= float(value.get("elapsed_seconds") or 0) <= 910)
        or any(row.get("hard_gate_passed") is not False for row in measurements.values())
        or any(row.get("coverage_complete") is not False for row in measurements.values())
    ):
        raise RuntimeError(f"companion-aware technical timeout differs: {path}")
    return value


def build_checkpoint(run_root: Path) -> dict[str, Any]:
    run_root = run_root.resolve()
    state_path = run_root / "run_state.json"
    source_state = base.alg.load(state_path)
    converged = _mapping(source_state.get("converged"), "terminal convergence")
    accepted = _mapping(source_state.get("accepted_rounds"), "terminal accepted rounds")
    stable = _mapping(
        source_state.get("interval_stable_rounds"), "terminal stable rounds"
    )
    if (
        source_state.get("status") != "COMPLETE"
        or source_state.get("stage") != "FINISHED"
        or source_state.get("error") not in (None, "")
        or source_state.get("formal_scope") != "mysql_only"
        or source_state.get("cross_engine_comparison_prohibited") is not True
        or source_state.get("formal_evidence_eligible") is not True
        or source_state.get("accepted_state_credit_prohibited") is not False
        or set(converged) != SCENES
        or not all(converged.values())
        or set(accepted) != SCENES
        or any(int(accepted[scene]) != 10 for scene in SCENES)
        or set(stable) != SCENES
        or any(int(stable[scene]) < 2 for scene in SCENES)
    ):
        raise RuntimeError("Algorithm C terminal convergence authority differs")

    prior_path = Path(str(source_state.get("checkpoint_path") or "")).resolve()
    original_verify = base.verify_checkpoint
    original_batch = base._verify_batch
    original_timeout = base._verify_technical_timeout_batch
    confirmation.ConfirmationCount.__deepcopy__ = c26._confirmation_count_deepcopy

    def verify(path: Path) -> dict[str, Any]:
        value = original_verify(path)
        if Path(path).resolve() == prior_path:
            value = copy.deepcopy(value)
            reconstructed = value["reconstructed_state"]
            accepted_rounds = confirmation.ConfirmationAcceptedRounds(
                reconstructed["accepted_rounds"]
            )
            persisted = reconstructed.get(
                "post_target_stability_confirmation_counts"
            ) or {}
            accepted_rounds.confirmation_acceptances = {
                scene: int(persisted.get(scene, 0)) for scene in accepted_rounds
            }
            reconstructed["accepted_rounds"] = accepted_rounds
        return value

    base.verify_checkpoint = verify
    base._verify_batch = _companion_aware_verify_batch
    base._verify_technical_timeout_batch = _companion_aware_verify_timeout_batch
    try:
        document = base.build_checkpoint(run_root)
    finally:
        base.verify_checkpoint = original_verify
        base._verify_batch = original_batch
        base._verify_technical_timeout_batch = original_timeout

    counts = _mapping(
        source_state.get("post_target_stability_confirmation_counts"),
        "terminal confirmation counts",
    )
    if (
        set(counts) != SCENES
        or any(
            int(value) < 0
            or int(value) > confirmation.MAX_POST_TARGET_ACCEPTED_CONFIRMATIONS
            for value in counts.values()
        )
    ):
        raise RuntimeError("terminal confirmation counts differ")
    document["reconstructed_state"][
        "post_target_stability_confirmation_counts"
    ] = {scene: int(counts[scene]) for scene in SCENES}
    document["terminal_convergence_audit"] = {
        "source_state_path": str(state_path),
        "source_state_sha256": base.alg.sha(state_path),
        "all_four_scenes_converged": True,
        "accepted_rounds_exactly_10": True,
        "minimum_stable_rounds": 2,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
    }
    builder = Path(__file__).resolve()
    document["builder_source_path"] = str(builder)
    document["builder_source_sha256"] = base.alg.sha(builder)
    payload = copy.deepcopy(document)
    payload.pop("checkpoint_sha256", None)
    document["checkpoint_sha256"] = base._canonical_sha256(payload)
    return document


def verify_checkpoint(path: Path) -> dict[str, Any]:
    return v4.verify_checkpoint(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.output_dir.name) is None:
        parser.error("unsafe output directory name")
    if args.output_dir.exists():
        raise RuntimeError(f"fresh checkpoint output exists: {args.output_dir}")
    document = build_checkpoint(args.run_root)
    args.output_dir.mkdir(parents=True, mode=0o700)
    path = args.output_dir / "prepared_binding.json"
    base.alg.atomic(path, document)
    verify_checkpoint(path)
    print(json.dumps({
        "status": "PREPARED",
        "checkpoint": str(path),
        "completed_outer_attempts": document["completed_outer_attempts"],
        "post_target_stability_confirmation_counts": document[
            "reconstructed_state"
        ]["post_target_stability_confirmation_counts"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

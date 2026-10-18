#!/usr/bin/env python3
"""Checkpoint completed outers before a post-timeout XP reset failure."""

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


def _companion_aware_verify_batch(
    path: Path, expected_workers: int, *, expected_rank: int | None = None,
) -> dict[str, Any]:
    value = base.alg.load(path)
    measurements = base._mapping(value.get("measurements"), "batch measurements")
    scenes = {str(row.get("scene")) for row in measurements.values()}
    active = {"job_memory", "hybench_xp_cpu", "hybench_xp_io"}
    if (
        value.get("status") != "PASS"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("failures") not in ([], None)
        or len(measurements) != expected_workers
        or scenes != set(base.SCENES)
        or any(
            not isinstance(row, Mapping)
            or row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            or (
                expected_rank is not None
                and int(row.get("rank") or 0)
                != (expected_rank if str(row.get("scene")) in active else 0)
            )
            for row in measurements.values()
        )
    ):
        raise RuntimeError(f"companion-aware completed batch differs: {path}")
    return value


def build_checkpoint(run_root: Path) -> dict[str, Any]:
    run_root = run_root.resolve()
    state_path = run_root / "run_state.json"
    source_state = base.alg.load(state_path)
    if (
        source_state.get("status") != "FAILED_CLOSED"
        or source_state.get("stage") != "FAILED"
        or not str(source_state.get("error") or "").startswith(
            "CalledProcessError: Command '['mysql', "
        )
        or "information_schema.processlist" not in str(source_state.get("error"))
        or int(source_state.get("outer_attempt") or -1) != 16
        or list((run_root / "outer_017").glob("*.decision.json"))
    ):
        raise RuntimeError("post-timeout XP reset checkpoint authority differs")

    prior_path = Path(str(source_state.get("checkpoint_path") or "")).resolve()
    original_load = base.alg.load
    original_verify = base.verify_checkpoint
    original_batch = base._verify_batch
    confirmation.ConfirmationCount.__deepcopy__ = c26._confirmation_count_deepcopy

    def load(path: Path) -> dict[str, Any]:
        value = original_load(path)
        if Path(path).resolve() == state_path:
            value = copy.deepcopy(value)
            value["error"] = (
                "RuntimeError: cross-project quiet gate exhausted its single "
                "bounded attempt"
            )
        return value

    def verify(path: Path) -> dict[str, Any]:
        value = original_verify(path)
        if Path(path).resolve() == prior_path:
            value = copy.deepcopy(value)
            reconstructed = value["reconstructed_state"]
            accepted = confirmation.ConfirmationAcceptedRounds(
                reconstructed["accepted_rounds"]
            )
            accepted.confirmation_acceptances = {
                scene: int(
                    (reconstructed.get(
                        "post_target_stability_confirmation_counts"
                    ) or {}).get(scene, 0)
                )
                for scene in accepted
            }
            reconstructed["accepted_rounds"] = accepted
        return value

    base.alg.load = load
    base.verify_checkpoint = verify
    base._verify_batch = _companion_aware_verify_batch
    try:
        document = base.build_checkpoint(run_root)
    finally:
        base.alg.load = original_load
        base.verify_checkpoint = original_verify
        base._verify_batch = original_batch

    counts = source_state.get("post_target_stability_confirmation_counts") or {}
    if set(counts) != set(base.SCENES) or any(int(value) < 0 for value in counts.values()):
        raise RuntimeError("post-target confirmation checkpoint counts differ")
    document["reconstructed_state"][
        "post_target_stability_confirmation_counts"
    ] = {scene: int(counts[scene]) for scene in base.SCENES}
    document["post_timeout_xp_reset_boundary"] = {
        "source_state_path": str(state_path),
        "source_state_sha256": base.alg.sha(state_path),
        "failed_outer_attempt": 17,
        "completed_outer_attempts": 16,
        "failed_rank_1_technical_state_credit_increment": 0,
        "rank_2_reset_failure_state_credit_increment": 0,
        "outer_017_decision_artifact_count": 0,
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
        "status": "PREPARED", "checkpoint": str(path),
        "completed_outer_attempts": document["completed_outer_attempts"],
        "post_target_stability_confirmation_counts": document[
            "reconstructed_state"
        ]["post_target_stability_confirmation_counts"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

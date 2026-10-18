#!/usr/bin/env python3
"""Seal the clean Outer-17 non-converged confirmation boundary."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
from typing import Any, Mapping

from scripts import build_mysql_algorithm_c_outer_checkpoint_v7 as v7
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v20 as confirmation
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v26 as c26


SCENES = v7.SCENES


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def build_checkpoint(run_root: Path) -> dict[str, Any]:
    run_root = run_root.resolve()
    state_path = run_root / "run_state.json"
    source = v7.base.alg.load(state_path)
    accepted = _mapping(source.get("accepted_rounds"), "accepted rounds")
    converged = _mapping(source.get("converged"), "convergence")
    stable = _mapping(source.get("interval_stable_rounds"), "stable rounds")
    counts = _mapping(
        source.get("post_target_stability_confirmation_counts"),
        "confirmation counts",
    )
    if (
        source.get("status") != "NON_CONVERGED"
        or source.get("stage") != "FINISHED"
        or source.get("error") not in (None, "")
        or source.get("formal_scope") != "mysql_only"
        or source.get("cross_engine_comparison_prohibited") is not True
        or source.get("formal_evidence_eligible") is not True
        or source.get("accepted_state_credit_prohibited") is not False
        or int(source.get("outer_attempt") or 0) != 17
        or source.get("termination_audit") != {
            "rejected_retry_budget_exhausted": [],
            "accepted_target_reached_without_convergence": ["job_memory"],
        }
        or set(accepted) != SCENES
        or any(int(accepted[scene]) != 10 for scene in SCENES)
        or set(converged) != SCENES
        or converged != {
            "job_memory": False,
            "tpch_sf4_memory": True,
            "hybench_xp_cpu": False,
            "hybench_xp_io": False,
        }
        or stable != {
            "job_memory": 1,
            "tpch_sf4_memory": 2,
            "hybench_xp_cpu": 1,
            "hybench_xp_io": 1,
        }
        or counts != {
            "job_memory": 2,
            "tpch_sf4_memory": 0,
            "hybench_xp_cpu": 1,
            "hybench_xp_io": 0,
        }
    ):
        raise RuntimeError("Outer-17 confirmation boundary authority differs")

    prior_path = Path(str(source.get("checkpoint_path") or "")).resolve()
    original_verify = v7.base.verify_checkpoint
    original_batch = v7.base._verify_batch
    original_timeout = v7.base._verify_technical_timeout_batch
    confirmation.ConfirmationCount.__deepcopy__ = c26._confirmation_count_deepcopy

    def verify(path: Path) -> dict[str, Any]:
        value = original_verify(path)
        if Path(path).resolve() == prior_path:
            value = copy.deepcopy(value)
            reconstructed = value["reconstructed_state"]
            rounds = confirmation.ConfirmationAcceptedRounds(
                reconstructed["accepted_rounds"]
            )
            persisted = reconstructed.get(
                "post_target_stability_confirmation_counts"
            ) or {}
            rounds.confirmation_acceptances = {
                scene: int(persisted.get(scene, 0)) for scene in rounds
            }
            reconstructed["accepted_rounds"] = rounds
        return value

    v7.base.verify_checkpoint = verify
    v7.base._verify_batch = v7._companion_aware_verify_batch
    v7.base._verify_technical_timeout_batch = (
        v7._companion_aware_verify_timeout_batch
    )
    try:
        document = v7.base.build_checkpoint(run_root)
    finally:
        v7.base.verify_checkpoint = original_verify
        v7.base._verify_batch = original_batch
        v7.base._verify_technical_timeout_batch = original_timeout

    document["reconstructed_state"][
        "post_target_stability_confirmation_counts"
    ] = {scene: int(counts[scene]) for scene in SCENES}
    document["confirmation_budget_recovery_audit"] = {
        "source_state_path": str(state_path),
        "source_state_sha256": v7.base.alg.sha(state_path),
        "reason": "two_confirmation_cap_exhausted_before_second_stable_interval",
        "formal_accepted_rounds_remain_exactly_10": True,
        "additional_accepted_state_credit_prohibited": True,
        "required_stable_rounds_unchanged": 2,
        "maximum_interval_ratio_unchanged": 1.01,
        "next_outer_required": True,
    }
    builder = Path(__file__).resolve()
    document["builder_source_path"] = str(builder)
    document["builder_source_sha256"] = v7.base.alg.sha(builder)
    payload = copy.deepcopy(document)
    payload.pop("checkpoint_sha256", None)
    document["checkpoint_sha256"] = v7.base._canonical_sha256(payload)
    return document


def verify_checkpoint(path: Path) -> dict[str, Any]:
    return v7.v4.verify_checkpoint(path)


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
    v7.base.alg.atomic(path, document)
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

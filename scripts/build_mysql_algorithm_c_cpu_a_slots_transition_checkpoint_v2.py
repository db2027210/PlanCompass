#!/usr/bin/env python3
"""Rebind an independent-C boundary to mixed CPU-A-slot four-worker XP."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
from typing import Any, Mapping

from scripts import build_mysql_algorithm_c_outer_checkpoint_v2 as base
from scripts import build_mysql_algorithm_c_outer_checkpoint_v4 as verifier


def load(path: Path) -> dict[str, Any]:
    value = base.alg.load(path.resolve())
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def build(
    boundary_path: Path, successor_freeze_path: Path,
    transition_authority_path: Path,
) -> dict[str, Any]:
    boundary_path = boundary_path.resolve()
    successor_freeze_path = successor_freeze_path.resolve()
    transition_authority_path = transition_authority_path.resolve()
    boundary = verifier.verify_checkpoint(boundary_path)
    freeze = load(successor_freeze_path)
    authority = load(transition_authority_path)
    scene = str((boundary.get("independent_scene_boundary") or {}).get("scene") or "")
    source_freeze_path = Path(str(boundary.get("freeze_path") or "")).resolve()
    if (
        scene not in {"hybench_xp_cpu", "hybench_xp_io"}
        or authority.get("status") != "PASS"
        or authority.get("formal_scope") != "mysql_only"
        or authority.get("cross_engine_comparison_prohibited") is not True
        or authority.get("scene") != scene
        or authority.get("round_boundary_preserved") is not True
        or authority.get("accepted_state_credit_increment") != 0
        or authority.get("source_freeze_path") != str(source_freeze_path)
        or authority.get("source_freeze_sha256") != base.alg.sha(source_freeze_path)
        or authority.get("successor_freeze_path") != str(successor_freeze_path)
        or authority.get("successor_freeze_sha256") != base.alg.sha(successor_freeze_path)
        or freeze.get("schema_version")
        != "mysql_four_scene_cpu_a_slots_four_worker_successor_v3"
        or freeze.get("formal_scope") != "mysql_only"
        or freeze.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("Algorithm C CPU-A-slot transition authority differs")

    document = copy.deepcopy(boundary)
    document.update({
        "freeze_path": str(successor_freeze_path),
        "freeze_sha256": base.alg.sha(successor_freeze_path),
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": base.alg.sha(Path(__file__).resolve()),
        "worker_cohort_transition": {
            "scene": scene,
            "from_parallelism": 2,
            "to_parallelism": 4,
            "physical_candidate_batches_before": [[1, 2], [3, 4], [5]],
            "physical_candidate_batches_after": [[1, 2, 3, 4], [5]],
            "semantic_top_k": 5,
            "semantic_top5_changed": False,
            "round_boundary_preserved": True,
            "completed_candidate_replay_repeated": False,
            "accepted_state_credit_increment": 0,
            "boundary_checkpoint_path": str(boundary_path),
            "boundary_checkpoint_sha256": base.alg.sha(boundary_path),
            "transition_authority_path": str(transition_authority_path),
            "transition_authority_sha256": base.alg.sha(transition_authority_path),
        },
    })
    payload = copy.deepcopy(document)
    payload.pop("checkpoint_sha256", None)
    document["checkpoint_sha256"] = base._canonical_sha256(payload)
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boundary-checkpoint", type=Path, required=True)
    parser.add_argument("--successor-freeze", type=Path, required=True)
    parser.add_argument("--transition-authority", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.output_dir.name) is None:
        parser.error("unsafe output directory")
    if args.output_dir.exists() or args.output_dir.is_symlink():
        raise RuntimeError("fresh checkpoint output required")
    document = build(
        args.boundary_checkpoint, args.successor_freeze,
        args.transition_authority,
    )
    args.output_dir.mkdir(parents=True, mode=0o700)
    path = args.output_dir / "prepared_binding.json"
    base.alg.atomic(path, document)
    verifier.verify_checkpoint(path)
    print(json.dumps({
        "status": "PREPARED",
        "checkpoint": str(path),
        "completed_outer_attempts": document["completed_outer_attempts"],
        "scene": document["worker_cohort_transition"]["scene"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Freeze the XP/I/O Algorithm-A Round-4 checkpoint continuation."""

from __future__ import annotations

import argparse
from pathlib import Path

from scripts import run_mysql_algorithm_a_xp_io_round4_checkpoint_resume_v1 as resume


def regular_file(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path.is_symlink() or not path.is_file():
        raise argparse.ArgumentTypeError(f"regular file required: {path}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-authority", type=regular_file, required=True)
    args = parser.parse_args()
    predecessor, candidates = resume.validate_predecessor()
    authority = resume.load(args.live_authority)
    reuse = resume.load(resume.REUSE_MANIFEST)
    if (
        authority.get("status") != "PASS"
        or authority.get("scene") != resume.SCENE
        or authority.get("formal_scope") != "mysql_only"
        or authority.get("cross_engine_comparison_prohibited") is not True
        or authority.get("physical_replay_parallelism") != 8
        or reuse.get("status") != "PASS"
        or ((reuse.get("algorithm_a") or {}).get("feedback_to_b_or_c"))
        is not False
    ):
        raise RuntimeError("XP/I/O Round-4 live launch authority differs")
    output = resume.CONTRACT
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"refusing to replace frozen contract: {output}")
    bindings = {
        "controller": Path(resume.__file__).resolve(),
        "runtime_v12": Path(resume.runtime.__file__).resolve(),
        "source_state": resume.SOURCE_STATE,
        "source_candidates": resume.SOURCE_CANDIDATES,
        "reuse_manifest": resume.REUSE_MANIFEST,
        "live_authority": args.live_authority,
    }
    document = {
        "schema_version": (
            "mysql_algorithm_a_xp_io_round4_checkpoint_resume_contract_v1"
        ),
        "status": "FROZEN_RUNTIME_VALIDATED",
        "created_at": resume.runtime.base.alg.now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm": "A",
        "scene": resume.SCENE,
        "active_parameters": resume.ACTIVE,
        "candidate_count_per_round": 16,
        "accepted_rounds_retained": int(predecessor["accepted"]),
        "interrupted_round": int(predecessor["round_index"]),
        "source_candidate_count": int(candidates["candidate_count"]),
        "rounds_1_to_3_retained": True,
        "round4_completed_candidate_measurements_reused_false": True,
        "round4_new_physical_work_starts_at_rank1": True,
        "canonical_baseline_reused_without_replay": True,
        "incumbent_reference_reused_without_replay": True,
        "complete_top4_candidate_set_required": True,
        "physical_replay_parallelism_eight": True,
        "algorithm_a_independent_posthoc_check_only": True,
        "algorithm_a_feedback_to_b_or_c_false": True,
        "candidate_vectors_used_by_b_or_c": False,
        "qps_used_by_b_or_c": False,
        "parameter_ranges_used_by_b_or_c": False,
        "search_direction_used_by_b_or_c": False,
        "source_bindings": {
            label: {
                "path": (
                    str(path.relative_to(resume.ROOT))
                    if path.is_relative_to(resume.ROOT)
                    else str(path)
                ),
                "sha256": resume.sha(path),
            }
            for label, path in bindings.items()
        },
    }
    resume.runtime.base.alg.atomic(output, document)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

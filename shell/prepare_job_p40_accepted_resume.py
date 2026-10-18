#!/usr/bin/env python3
"""Bind a JOB p40 accepted checkpoint to the live config and contract manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--optimizer", required=True, type=Path)
    parser.add_argument("--bridge", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    checkpoint = json.loads(args.checkpoint.read_text(encoding="utf-8"))
    protocol = config.get("formal_outer_protocol") or {}

    if checkpoint.get("kind") != "job_gradient_accepted_resume_checkpoint_v1":
        raise RuntimeError("invalid accepted resume checkpoint kind")
    expected_counters = {
        "accepted_shrink_count": 1,
        "formal_search_replay_count": 10,
        "global_replay_count": 41,
        "startup_baseline_count": 1,
    }
    if checkpoint.get("counters") != expected_counters:
        raise RuntimeError("accepted resume checkpoint counters changed")
    if protocol.get("formal_search_max_replay_top_k") != 10:
        raise RuntimeError("per-round maximum replay batch must remain ten")
    if protocol.get("formal_search_initial_replay_top_k") != 5:
        raise RuntimeError("per-round initial replay batch must remain five")
    if protocol.get("formal_search_accepted_target") != 10:
        raise RuntimeError("accepted shrink target must remain ten")

    before = {
        "config_sha256": sha256_file(args.config),
        "manifest_sha256": sha256_file(args.manifest),
        "protocol": dict(protocol),
    }
    checkpoint_sha256 = sha256_file(args.checkpoint)
    protocol.update(
        {
            "global_replay_ordinal_at_binding": 41,
            "formal_search_replay_count_start": 10,
            "formal_search_accepted_count_start": 1,
            "initial_real_replay_count": 10,
            "formal_search_attempt_safety_cap": 130,
            "formal_search_maximum_replay_count": 130,
            "maximum_cumulative_real_replay_count": 130,
            "maximum_non_default_real_candidates": 130,
            "formal_search_resume_checkpoint_path": str(args.checkpoint.resolve()),
            "formal_search_resume_checkpoint_sha256": checkpoint_sha256,
            "resume_next_feedback_round": 1,
            "resume_replays_are_evidence_only_no_rerun": True,
            "unstable_candidate_replicate_total": 3,
            "first_stable_replicate_used_directly": True,
            "first_unstable_requires_all_three_valid_replicates": True,
            "later_stable_replicate_short_circuit": False,
            "technical_failure_in_composite": False,
            "composite_point_metrics": ["db_qps", "cpu", "memory", "io", "latency"],
        }
    )
    config["formal_outer_protocol"] = protocol
    atomic_json(args.config, config)

    manifest.update(
        {
            "config_sha256": sha256_file(args.config),
            "optimizer_code_sha256": sha256_file(args.optimizer),
            "replay_bridge_sha256": sha256_file(args.bridge),
            "resume_checkpoint": {
                "path": str(args.checkpoint.resolve()),
                "sha256": checkpoint_sha256,
                "accepted_point_result_path": checkpoint.get(
                    "accepted_point_result_path"
                ),
                "accepted_point_result_sha256": checkpoint.get(
                    "accepted_point_result_sha256"
                ),
                "accepted_shrink_count": 1,
                "next_feedback_round": 1,
                "global_replay_safety_cap": 130,
                "per_outer_replay_batch": [5, 10],
                "point_0032_through_0041_rerun": False,
                "unstable_replicate_policy": {
                    "total_valid_replicates": 3,
                    "first_stable_direct": True,
                    "first_unstable_forces_all_three": True,
                    "later_stability_short_circuit": False,
                    "technical_failures_in_composite": False,
                    "raw_and_composite_ledgers_required": True,
                },
            },
        }
    )
    atomic_json(args.manifest, manifest)

    evidence = {
        "schema_version": 1,
        "event": "job_p40_accepted_resume_bound",
        "before": before,
        "after": {
            "config_sha256": sha256_file(args.config),
            "manifest_sha256": sha256_file(args.manifest),
            "optimizer_code_sha256": sha256_file(args.optimizer),
            "replay_bridge_sha256": sha256_file(args.bridge),
            "checkpoint_sha256": checkpoint_sha256,
            "protocol": protocol,
        },
        "preserved_counters": expected_counters,
        "preserved_points": list(range(31, 42)),
        "next_new_point_ordinal": 42,
    }
    atomic_json(args.evidence, evidence)
    print(json.dumps(evidence["after"], sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Rebind a memory-scene C checkpoint to the full-parallel four-scene freeze."""

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


OLD = ROOT / "config/formal/frozen_mysql_four_scene_cpu_a_slots_two_worker_20260828_v2/four_scene_effective_resource_successor.json"
NEW = ROOT / "config/formal/frozen_mysql_four_scene_cpu_a_slots_four_worker_20260828_v3/four_scene_effective_resource_successor.json"
SCENES = ("job_memory", "tpch_sf4_memory")


def workers(document: Mapping[str, Any], scene: str) -> list[dict[str, Any]]:
    return [copy.deepcopy(dict(row)) for row in document["workers"] if row["scene"] == scene]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--scene", choices=SCENES, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.output_dir.name) is None:
        parser.error("safe output directory required")
    if args.output_dir.exists() or args.output_dir.is_symlink():
        raise RuntimeError("fresh output required")
    checkpoint_path = args.checkpoint.resolve()
    document = verifier.verify_checkpoint(checkpoint_path)
    old, new = alg.load(OLD), alg.load(NEW)
    if (
        Path(str(document.get("freeze_path") or "")).resolve() != OLD.resolve()
        or document.get("freeze_sha256") != alg.sha(OLD)
        or old.get("schema_version") != "mysql_four_scene_cpu_a_slots_two_worker_successor_v2"
        or new.get("schema_version") != "mysql_four_scene_cpu_a_slots_four_worker_successor_v3"
        or workers(old, args.scene) != workers(new, args.scene)
        or len(workers(new, args.scene)) != 2
        or (new.get("xp_capacity_successor") or {}).get(
            "memory_scene_workers_unchanged"
        ) is not True
        or new.get("formal_scope") != "mysql_only"
        or new.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("memory-scene freeze transition differs")
    document["freeze_path"] = str(NEW)
    document["freeze_sha256"] = alg.sha(NEW)
    document["builder_source_path"] = str(Path(__file__).resolve())
    document["builder_source_sha256"] = alg.sha(Path(__file__).resolve())
    document["memory_scene_full_parallel_freeze_rebind"] = {
        "schema_version": "mysql_algorithm_c_memory_scene_full_parallel_freeze_rebind_v1",
        "status": "PASS",
        "scene": args.scene,
        "source_checkpoint": {
            "path": str(checkpoint_path), "sha256": alg.sha(checkpoint_path),
        },
        "predecessor_freeze": {"path": str(OLD), "sha256": alg.sha(OLD)},
        "successor_freeze": {"path": str(NEW), "sha256": alg.sha(NEW)},
        "target_worker_rows_byte_semantically_identical": True,
        "target_worker_count": 2,
        "xp_capacity_change_only": True,
        "candidate_workload_or_memory_scene_resource_changed": False,
        "accepted_or_rejected_credit_changed": False,
        "fresh_r0_required": False,
    }
    payload = copy.deepcopy(document)
    payload.pop("checkpoint_sha256", None)
    document["checkpoint_sha256"] = verifier.predecessor.predecessor._canonical_sha256(payload)
    args.output_dir.mkdir(parents=True, mode=0o700)
    output = args.output_dir / "prepared_binding.json"
    alg.atomic(output, document)
    verifier.verify_checkpoint(output)
    print(json.dumps({
        "status": "PREPARED", "scene": args.scene,
        "checkpoint": str(output), "completed_outer_attempts": 13,
        "freeze_sha256": document["freeze_sha256"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

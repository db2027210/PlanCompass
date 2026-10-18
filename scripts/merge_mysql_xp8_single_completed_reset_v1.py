#!/usr/bin/env python3
"""Merge one completed XP8 reset row with seven unchanged live rows."""

from __future__ import annotations

import argparse
import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_v2 as alg


def load(path: Path) -> dict[str, Any]:
    value = alg.load(path)
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=("hybench_xp_cpu", "hybench_xp_io"), required=True)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--source-full-binding", type=Path, required=True)
    parser.add_argument("--source-single-binding", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    full_path = args.source_full_binding.resolve()
    single_path = args.source_single_binding.resolve()
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh merged binding required: {output}")
    full = load(full_path)
    single = load(single_path)
    prefix = "xp_cpu" if args.scene == "hybench_xp_cpu" else "xp_io"
    expected = {f"{prefix}_w{i}" for i in range(1, 9)}
    rows = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in full.get("workers") or []
        if row.get("worker_id") in expected
    }
    replacements = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in single.get("workers") or []
    }
    if (
        full.get("status") != "PASS"
        or single.get("status") != "PASS"
        or set(rows) != expected
        or set(replacements) != {args.worker_id}
        or args.worker_id not in expected
        or replacements[args.worker_id].get("scene") != args.scene
        or replacements[args.worker_id].get("ready") is not True
        or rows[args.worker_id].get("container")
        != replacements[args.worker_id].get("container")
        or rows[args.worker_id].get("container_id")
        == replacements[args.worker_id].get("container_id")
    ):
        raise RuntimeError("single completed XP8 reset merge boundary differs")
    rows[args.worker_id] = replacements[args.worker_id]
    document = copy.deepcopy(full)
    document.update({
        "schema_version": "mysql_xp8_single_completed_reset_merge_v1",
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "workers": [rows[worker_id] for worker_id in sorted(expected)],
        "reset_worker_ids": [args.worker_id],
        "technical_single_reset_merge": {
            "scene": args.scene,
            "reset_worker": args.worker_id,
            "unchanged_workers": sorted(expected - {args.worker_id}),
            "source_full_binding_path": str(full_path),
            "source_full_binding_sha256": alg.sha(full_path),
            "source_single_binding_path": str(single_path),
            "source_single_binding_sha256": alg.sha(single_path),
            "candidate_measurements_reused": 0,
            "accepted_or_rejected_round_credit": 0,
            "workload_started": False,
        },
    })
    alg.atomic(output, document)
    print({"status": "PASS", "binding": str(output), "reset_worker": args.worker_id})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

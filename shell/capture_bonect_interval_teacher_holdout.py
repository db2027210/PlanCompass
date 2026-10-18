#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bonect_interval_lineage import exact_cost_params, manifest_sha256  # noqa: E402
from shell.run_io_nested_interval_inner_loop import PlanProbe, read_json, write_json  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture a teacher plan only after an interval round is frozen."
    )
    parser.add_argument("--frozen-manifest", required=True)
    parser.add_argument("--teacher-params", required=True)
    parser.add_argument("--output", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    manifest_path = Path(args.frozen_manifest)
    manifest = read_json(manifest_path)
    if str(manifest.get("event")) != "bonect_nested_interval_round_frozen_before_holdout":
        raise RuntimeError("teacher plan capture requires a frozen-before-holdout manifest")
    if str(manifest.get("interval_manifest_sha256")) != manifest_sha256(manifest):
        raise RuntimeError("frozen manifest SHA mismatch")

    # The teacher file is intentionally not opened until the frozen manifest is verified.
    teacher = exact_cost_params(read_json(Path(args.teacher_params)))
    probe = PlanProbe()
    try:
        capture = probe.capture("post_freeze_teacher_holdout", teacher)
    finally:
        probe.close()
    output: dict[str, Any] = {
        "event": "bonect_nested_interval_post_freeze_teacher_plan_capture",
        "frozen_manifest": str(manifest_path),
        "interval_manifest_sha256": manifest["interval_manifest_sha256"],
        "teacher_params_redacted": True,
        "teacher_parameter_effective_values_redacted": True,
        "teacher_plan_hashes": capture["plan_hashes"],
        "teacher_combined_plan_hash": capture["combined_plan_hash"],
        "plan_features": {
            name: capture[name]
            for name in (
                "node_type_counts",
                "category_counts",
                "parallel_aware_node_count",
                "gather_workers_planned_sum",
                "spill_risk_node_count_16mb_proxy",
                "root_total_cost_weighted_sum",
                "root_plan_rows_weighted_sum",
            )
        },
        "query_count": len(capture["plan_hashes"]),
        "error_count": capture["error_count"],
    }
    write_json(Path(args.output), output)
    print(json.dumps(output, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

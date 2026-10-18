#!/usr/bin/env python3
"""Run the XP v46 real EXPLAIN-only integration proof."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path


PROJECT = Path("/home/anonymous/program/resource_adaptive")
TEST = PROJECT / "tests/test_xp_v46_real_explain_selection.py"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_root.resolve()
    if output.exists():
        raise RuntimeError(f"XP v46 validation output already exists: {output}")
    specification = importlib.util.spec_from_file_location(
        "xp_v46_real_explain_validation", TEST
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("XP v46 cannot load integration test")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    os.environ["XP_V46_VALIDATION_ROOT"] = str(output)
    module.test_real_campaign_boundary_generates_v46_probe5_without_workload()
    result = json.loads((output / "validation_result.json").read_text(encoding="utf-8"))
    if not (
        result.get("contract") == "xp_v46_real_explain_selection_validation_v1"
        and int(result.get("candidate_count", -1)) == 5
        and len(set(result.get("fingerprint_ids") or [])) == 5
        and result.get("all_new_noncorner") is True
        and result.get("all_uncertain_diagnostic_probes") is True
        and result.get("pending_repeat_required_before_formal") is True
        and result.get("actual_best_top3_gate_bypassed") is False
        and result.get("workload_started") is False
        and result.get("formal_state_mutation") is False
    ):
        raise RuntimeError("XP v46 integration result contract mismatch")
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


#!/usr/bin/env python3
"""Preflight the dormant TPC-C v35 zero-call handoff package."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import tpcc_autonomous_target10_controller_v25 as v25
from tools import tpcc_autonomous_target10_controller_v35_preworkload_block as v35


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--package-only", action="store_true")
    args = parser.parse_args()
    manifest_path = args.manifest.resolve()
    if v25.sha256(manifest_path) != args.manifest_sha256:
        raise RuntimeError("TPC-C v35 manifest SHA mismatch")
    manifest = v25.load_json(manifest_path)
    v25.verify_pins(manifest)
    if (
        manifest.get("controller_version") != 35
        or manifest.get("v35_zero_call_handoff") is not True
        or manifest.get("v35_activation_requires_failed_group5") is not True
        or manifest.get("v35_no_manual_candidate_injection") is not True
        or manifest.get("v35_no_gate_bypass") is not True
    ):
        raise RuntimeError("TPC-C v35 manifest contract mismatch")
    result = {
        "passed": True,
        "package_only": args.package_only,
        "physical_workload_calls_added": 0,
        "formal_start_authorized": False,
        "manual_candidate_injection": False,
        "gate_bypassed": False,
    }
    if not args.package_only:
        result["handoff"] = v35.handoff_eligibility(
            manifest_path,
            require_unlocked_controller=True,
        )
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

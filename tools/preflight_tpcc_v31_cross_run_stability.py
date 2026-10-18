#!/usr/bin/env python3
"""Zero-workload preflight for the material-fresh TPC-C v31 campaign."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

try:
    import tpcc_autonomous_target10_controller_v25 as v25
    import tpcc_autonomous_target10_controller_v29_prelaunch_fingerprint as v29
    import tpcc_autonomous_target10_controller_v31_cross_run_stability as v31
except ModuleNotFoundError:
    from tools import tpcc_autonomous_target10_controller_v25 as v25
    from tools import tpcc_autonomous_target10_controller_v29_prelaunch_fingerprint as v29
    from tools import tpcc_autonomous_target10_controller_v31_cross_run_stability as v31


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = args.manifest.resolve()
    if v25.sha256(manifest_path) != args.manifest_sha256:
        raise RuntimeError("v31 manifest SHA mismatch")
    manifest = v25.load_json(manifest_path)
    v25.verify_pins(manifest)
    if (
        int(manifest.get("controller_version", -1)) != 31
        or manifest.get("v31_activation_authorized") is not True
        or manifest.get("v31_algorithm_frozen") is not True
        or manifest.get("score_stability_contract")
        != "tpcc_complete_run_aggregate_io_score_repeatability_v31"
        or int(manifest.get("formal_accepted_start", -1)) != 0
    ):
        raise RuntimeError("v31 manifest contract mismatch")
    state_path = Path(manifest["controller_state_path"]).resolve()
    formal_path = Path(manifest["formal_state_path"]).resolve()
    if v25.sha256(state_path) != manifest["initial_controller_state_sha256"]:
        raise RuntimeError("v31 controller state moved after sealing")
    if v25.sha256(formal_path) != manifest["initial_formal_state_sha256"]:
        raise RuntimeError("v31 formal state moved after sealing")
    state = v25.load_json(state_path)
    formal = v25.load_json(formal_path)
    if (
        state.get("current_run") is not None
        or int(state.get("next_ordinal", -1)) != 1
        or int(formal.get("accepted_count", -1)) != 0
        or int(formal.get("inherited_accepted", -1)) != 0
        or int(formal.get("historical_accepted_excluded", -1)) != 2
        or int(formal.get("formal_physical_call_count", -1)) != 0
        or int(formal.get("formal_valid_replay_count", -1)) != 0
        or int(formal.get("formal_technical_failure_count", -1)) != 0
        or int(formal.get("gradient_update_count", -1)) != 0
        or formal.get("gradient_state_reset") is not True
        or not math.isclose(
            float(formal.get("incumbent_score")),
            3.9894795578792417,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    ):
        raise RuntimeError("v31 material fresh0 state boundary mismatch")

    report_path = Path(
        manifest["v31_zero_call_initializer_report_path"]
    ).resolve()
    if (
        not report_path.is_file()
        or v25.sha256(report_path)
        != manifest["v31_zero_call_initializer_report_sha256"]
    ):
        raise RuntimeError("v31 initializer report SHA mismatch")
    report = v25.load_json(report_path)
    if (
        report.get("passed") is not True
        or report.get("physical_workload_calls_added") != 0
        or report.get("formal_accepted_start") != 0
        or report.get("prior_complete_run_observation_count") != 2
        or report.get("required_complete_run_observation_count") != 3
        or report.get("next_mode") != "diagnostic_validation"
        or report.get("selection_fingerprint_collision_ids") != []
    ):
        raise RuntimeError("v31 initializer report contract mismatch")
    selection_path = Path(manifest["v31_frozen_next_selection_path"]).resolve()
    if (
        v25.sha256(selection_path)
        != manifest["v31_frozen_next_selection_sha256"]
    ):
        raise RuntimeError("v31 frozen selection SHA mismatch")
    selection = v25.load_json(selection_path)
    v25.validate_selection(selection, mode="diagnostic_validation")
    fingerprint_gate = v29.global_prelaunch_fingerprint_gate(
        selection,
        "diagnostic_validation",
        state,
    )
    if fingerprint_gate.get("passed") is not True:
        raise RuntimeError("v31 frozen selection collided with training history")

    gate = v31.quality_gate_v31(state.get("audits") or [])
    mode = v31.choose_next_mode_v31(state.get("audits") or [], gate)
    if (
        gate.get("passed") is not False
        or mode != "diagnostic_validation"
        or gate.get("complete_run_repeatability_reroute") is not True
    ):
        raise RuntimeError("v31 prelaunch must fail closed to complete-run repeat 3")
    print(
        json.dumps(
            {
                "passed": True,
                "manifest_sha256": args.manifest_sha256,
                "formal_accepted_count": 0,
                "historical_accepted_excluded": 2,
                "prior_complete_run_observation_count": 2,
                "next_mode": mode,
                "top5_roles": [
                    item["selection_role"] for item in selection["top5"]
                ],
                "fingerprint_collision_count": 0,
                "physical_workload_calls_added": 0,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Zero-workload preflight for TPC-C v32 sealed exact-repeat admission."""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path

try:
    import tpcc_autonomous_target10_controller_v25 as v25
    import tpcc_autonomous_target10_controller_v31_cross_run_stability as v31
    import tpcc_autonomous_target10_controller_v32_validation_repeat as v32
except ModuleNotFoundError:
    from tools import tpcc_autonomous_target10_controller_v25 as v25
    from tools import tpcc_autonomous_target10_controller_v31_cross_run_stability as v31
    from tools import tpcc_autonomous_target10_controller_v32_validation_repeat as v32


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = args.manifest.resolve()
    if v25.sha256(manifest_path) != args.manifest_sha256:
        raise RuntimeError("v32 manifest SHA mismatch")
    manifest = v25.load_json(manifest_path)
    v25.verify_pins(manifest)
    if (
        int(manifest.get("controller_version", -1)) != 32
        or manifest.get("v32_activation_authorized") is not True
        or manifest.get("v32_algorithm_frozen") is not True
        or manifest.get("material_change_fresh0") is not True
        or int(manifest.get("formal_accepted_start", -1)) != 0
        or Path(manifest.get("exact_repeat_builder_path") or "").name
        != "build_tpcc_exact_plan_repeat_validation_v4.py"
        or manifest.get("prelaunch_fingerprint_contract")
        != "new_and_formal_global_novelty_exact_repeat_provenance_v32"
    ):
        raise RuntimeError("v32 manifest contract mismatch")
    state_path = Path(manifest["controller_state_path"]).resolve()
    formal_path = Path(manifest["formal_state_path"]).resolve()
    if v25.sha256(state_path) != manifest["initial_controller_state_sha256"]:
        raise RuntimeError("v32 controller state moved after sealing")
    if v25.sha256(formal_path) != manifest["initial_formal_state_sha256"]:
        raise RuntimeError("v32 formal state moved after sealing")
    state = v25.load_json(state_path)
    formal = v25.load_json(formal_path)
    if (
        state.get("current_run") is not None
        or int(state.get("next_ordinal", -1)) != 1
        or int(formal.get("accepted_count", -1)) != 0
        or int(formal.get("inherited_accepted", -1)) != 0
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
        raise RuntimeError("v32 fresh0 state boundary mismatch")
    latest = (state.get("audits") or [])[-1]
    erroneous = state.get("v31_erroneous_diagnostic_02_training_only") or {}
    if (
        not str(latest.get("root") or "").endswith(
            "autonomous_diagnostic_validation_01"
        )
        or latest.get("v32_repeat_source") is not True
        or erroneous.get("accepted_count_eligible") is not False
        or erroneous.get("repeat_source_eligible") is not False
    ):
        raise RuntimeError("v32 repeat-source ordering/exclusion mismatch")

    report_path = Path(manifest["v32_zero_call_initializer_report_path"]).resolve()
    if (
        not report_path.is_file()
        or v25.sha256(report_path)
        != manifest["v32_zero_call_initializer_report_sha256"]
    ):
        raise RuntimeError("v32 initializer report SHA mismatch")
    report = v25.load_json(report_path)
    if (
        report.get("passed") is not True
        or report.get("physical_workload_calls_added") != 0
        or report.get("formal_accepted_start") != 0
        or report.get("next_mode") != "diagnostic_validation"
        or report.get("prior_complete_run_observation_count") != 3
        or report.get("next_independent_run_ordinal") != 4
        or report.get("novel_fingerprint_credit") != 0
        or report.get("erroneous_diagnostic_02_training_only") is not True
        or report.get("prelaunch_gate_read_only") is not True
    ):
        raise RuntimeError("v32 initializer report contract mismatch")
    selection_path = Path(manifest["v32_frozen_next_selection_path"]).resolve()
    if v25.sha256(selection_path) != manifest["v32_frozen_next_selection_sha256"]:
        raise RuntimeError("v32 frozen repeat selection SHA mismatch")
    selection = v25.load_json(selection_path)
    v25.validate_selection(selection, mode="diagnostic_validation")
    state_before = copy.deepcopy(state)
    fingerprint_gate = v32.validation_repeat_prelaunch_fingerprint_gate(
        selection,
        "diagnostic_validation",
        state,
    )
    if state != state_before:
        raise RuntimeError("v32 prelaunch gate is not read-only")
    gate = v31.quality_gate_v31(state.get("audits") or [])
    mode = v31.choose_next_mode_v31(state.get("audits") or [], gate)
    if (
        fingerprint_gate.get("passed") is not True
        or fingerprint_gate.get("collision_ids") != []
        or fingerprint_gate.get("novel_fingerprint_credit") != 0
        or fingerprint_gate.get("independent_run_ordinal") != 4
        or mode != "diagnostic_validation"
        or gate.get("passed") is not False
    ):
        raise RuntimeError("v32 exact-repeat preflight failed closed")
    print(
        json.dumps(
            {
                "passed": True,
                "manifest_sha256": args.manifest_sha256,
                "formal_accepted_count": 0,
                "next_mode": mode,
                "independent_run_ordinal": 4,
                "novel_fingerprint_credit": 0,
                "allowed_exact_repeat_collision_count": len(
                    fingerprint_gate.get("allowed_exact_repeat_collision_ids")
                    or []
                ),
                "erroneous_diagnostic_02_training_only": True,
                "physical_workload_calls_added": 0,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

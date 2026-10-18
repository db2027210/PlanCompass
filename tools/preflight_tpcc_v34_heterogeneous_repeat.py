#!/usr/bin/env python3
"""Zero-workload preflight for the sealed TPC-C v34 package."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import tpcc_autonomous_target10_controller_v25 as v25
from tools import tpcc_autonomous_target10_controller_v31_cross_run_stability as v31
from tools import tpcc_autonomous_target10_controller_v34_heterogeneous_repeat as v34


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    manifest_path = args.manifest.resolve()
    if v25.sha256(manifest_path) != args.manifest_sha256:
        raise RuntimeError("TPC-C v34 manifest SHA mismatch")
    manifest = v25.load_json(manifest_path)
    v25.verify_pins(manifest)
    if (
        manifest.get("controller_version") != 34
        or manifest.get("v34_algorithm_frozen") is not True
        or manifest.get("v34_v33_batch_training_only") is not True
        or manifest.get("per_batch_physical_cap") != 5
        or manifest.get("qps_role") != "outer_audit_only_not_inner_score"
        or Path(manifest["exact_repeat_builder_path"]).name
        != "build_tpcc_exact_plan_repeat_validation_v5.py"
    ):
        raise RuntimeError("TPC-C v34 manifest contract mismatch")

    state_path = Path(manifest["controller_state_path"]).resolve()
    formal_path = Path(manifest["formal_state_path"]).resolve()
    state = v25.load_json(state_path)
    formal = v25.load_json(formal_path)
    if (
        formal.get("accepted_count") != 0
        or formal.get("inherited_accepted") != 0
        or formal.get("formal_physical_call_count") != 0
        or formal.get("formal_valid_replay_count") != 0
        or formal.get("formal_technical_failure_count") != 0
        or formal.get("gradient_update_count") != 0
        or v25.sha256(formal_path) != manifest["initial_formal_state_sha256"]
        or state.get("formal_state_sha256") != v25.sha256(formal_path)
    ):
        raise RuntimeError("TPC-C v34 fresh formal0 boundary mismatch")
    if len(state.get("audits") or []) != 1:
        raise RuntimeError("TPC-C v34 must inherit one training-only batch")
    origin = state["audits"][0]
    if (
        origin.get("clean") is not True
        or origin.get("physical_calls") != 5
        or origin.get("valid_replays") != 5
        or origin.get("technical_failures") != 0
        or origin.get("baseline_restored") is not True
        or origin.get("complete_same_group_batch_count") != 1
        or origin.get("group_origin_novel_fingerprint_gate_passed") is not True
    ):
        raise RuntimeError("TPC-C v34 origin audit mismatch")

    current = state.get("current_run") or {}
    selection_path = Path(str(current.get("selection_path") or "")).resolve()
    launcher_path = Path(str(current.get("launcher_path") or "")).resolve()
    seal_path = Path(str(current.get("seal_path") or "")).resolve()
    run_root = Path(str(current.get("root") or "")).resolve()
    if (
        state.get("status") != "waiting_current_batch"
        or state.get("next_ordinal") != 2
        or current.get("mode") != "diagnostic_validation"
        or current.get("launch_if_absent") is not True
        or current.get("launcher_invoked") is not False
        or not selection_path.is_file()
        or v25.sha256(selection_path) != current.get("selection_sha256")
        or not launcher_path.is_file()
        or v25.sha256(launcher_path) != current.get("launcher_sha256")
        or not seal_path.is_file()
        or v25.sha256(seal_path) != current.get("seal_sha256")
        or run_root.exists()
    ):
        raise RuntimeError("TPC-C v34 sealed current-run boundary mismatch")
    selection = v25.load_json(selection_path)
    contract = selection.get("validation_repeat_contract") or {}
    counts = [
        int(item["total_exact_observation_count"])
        for item in contract.get("per_point_total_exact_observation_counts") or []
    ]
    if (
        [item.get("selection_role") for item in selection.get("top5") or []]
        != ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
        or sorted(counts) != [1, 2, 2, 2, 2]
        or contract.get("prior_complete_same_group_batch_count") != 1
        or contract.get("independent_same_group_batch_ordinal") != 2
        or contract.get("repeat_cap_basis") != "complete_same_group_batch_count"
    ):
        raise RuntimeError("TPC-C v34 heterogeneous repeat selection mismatch")

    state_before = copy.deepcopy(state)
    admission = v34.validation_repeat_prelaunch_fingerprint_gate_v34(
        selection, "diagnostic_validation", state
    )
    if state != state_before or admission.get("passed") is not True:
        raise RuntimeError("TPC-C v34 prelaunch gate failed or mutated state")
    gate = v34.quality_gate_v34(state["audits"])
    if gate.get("passed") is True or v34.choose_next_mode_v34(state["audits"], gate) != "diagnostic_validation":
        raise RuntimeError("TPC-C v34 must require additional exact repeats")
    if v31.MIN_COMPLETE_RUN_REPEATS != 3 or v31.MAX_COMPLETE_RUN_REPEATS != 5:
        raise RuntimeError("TPC-C v34 repeat limits changed")

    print(
        json.dumps(
            {
                "passed": True,
                "formal_accepted_count": 0,
                "physical_workload_calls_added": 0,
                "inherited_training_batches": 1,
                "next_same_group_batch_ordinal": 2,
                "per_point_total_exact_observation_counts": counts,
                "formal_gate_minimum_per_point_observations": 3,
                "maximum_complete_same_group_batches": 5,
                "selection_roles": [
                    item["selection_role"] for item in selection["top5"]
                ],
                "run_root_absent": True,
                "formal_state_sha256": v25.sha256(formal_path),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

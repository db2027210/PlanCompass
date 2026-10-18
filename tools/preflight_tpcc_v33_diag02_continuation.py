#!/usr/bin/env python3
"""Zero-workload preflight for the sealed TPC-C v33 continuation."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import build_tpcc_v33_diag02_trusted3_probe2 as builder_v33
from tools import tpcc_autonomous_target10_controller_v25 as v25
from tools import tpcc_autonomous_target10_controller_v29_prelaunch_fingerprint as v29


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = args.manifest.resolve()
    if v25.sha256(manifest_path) != args.manifest_sha256:
        raise RuntimeError("TPC-C v33 manifest SHA mismatch")
    manifest = v25.load_json(manifest_path)
    v25.verify_pins(manifest)
    if (
        manifest.get("controller_version") != 33
        or manifest.get("v33_activation_authorized") is not True
        or manifest.get("v33_algorithm_frozen") is not True
        or manifest.get("v33_diag02_training_only") is not True
        or manifest.get("v33_no_retroactive_trust") is not True
        or manifest.get("formal_accepted_start") != 0
        or manifest.get("accepted_target") != 10
        or manifest.get("per_batch_physical_cap") != 5
        or manifest.get("qps_role") != "outer_audit_only_not_inner_score"
        or (manifest.get("v33_bounded_no_solution") or {}).get(
            "maximum_generation_attempts"
        )
        != 1
        or (manifest.get("v33_bounded_no_solution") or {}).get(
            "automatic_unbounded_retry"
        )
        is not False
    ):
        raise RuntimeError("TPC-C v33 manifest contract mismatch")

    formal_path = Path(manifest["formal_state_path"]).resolve()
    state_path = Path(manifest["controller_state_path"]).resolve()
    if (
        v25.sha256(formal_path) != manifest["initial_formal_state_sha256"]
        or v25.sha256(state_path) != manifest["initial_controller_state_sha256"]
    ):
        raise RuntimeError("TPC-C v33 formal/controller state moved after sealing")
    formal = v25.load_json(formal_path)
    state = v25.load_json(state_path)
    if (
        formal.get("accepted_count") != 0
        or formal.get("inherited_accepted") != 0
        or formal.get("formal_physical_call_count") != 0
        or formal.get("formal_valid_replay_count") != 0
        or formal.get("formal_technical_failure_count") != 0
        or formal.get("gradient_update_count") != 0
        or state.get("status") != "waiting_current_batch"
        or state.get("next_ordinal") != 2
        or len(state.get("sources") or []) != 1
        or (state.get("sources") or [{}])[0].get("training_only") is not True
        or (state.get("sources") or [{}])[0].get(
            "source_probe_rows_retroactively_trusted"
        )
        is not False
    ):
        raise RuntimeError("TPC-C v33 fresh0 state boundary mismatch")
    current = state.get("current_run") or {}
    selection_path = Path(str(current.get("selection_path") or "")).resolve()
    launcher_path = Path(str(current.get("launcher_path") or "")).resolve()
    seal_path = Path(str(current.get("seal_path") or "")).resolve()
    run_root = Path(str(current.get("root") or "")).resolve()
    if (
        current.get("mode") != "diagnostic_validation"
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
        raise RuntimeError("TPC-C v33 sealed current-run boundary mismatch")

    source = state["sources"][0]
    source_selection_path = Path(source["selection_path"]).resolve()
    source_ledger_path = Path(source["ledger_path"]).resolve()
    source_contract = builder_v33.validate_clean_probe_source(
        v25.load_json(source_selection_path),
        v25.load_json(source_ledger_path),
    )
    exclusions = list(state.get("quarantined_fingerprints") or [])
    selection = v25.load_json(selection_path)
    gate = builder_v33.validate_generated_top5(
        selection,
        source_contract=source_contract,
        globally_used_ids=exclusions,
    )
    state_before = copy.deepcopy(state)
    global_gate = v29.global_prelaunch_fingerprint_gate(
        selection,
        "diagnostic_validation",
        state,
    )
    if state != state_before or global_gate.get("passed") is not True:
        raise RuntimeError("TPC-C v33 global novelty gate failed or mutated state")
    print(
        json.dumps(
            {
                "passed": True,
                "formal_accepted_count": 0,
                "physical_workload_calls_added": 0,
                "selection_roles": gate["roles"],
                "novel_fingerprint_count": len(gate["fingerprint_ids"]),
                "source_probe_rows_retroactively_trusted": False,
                "maximum_generation_attempts": 1,
                "run_root_absent": True,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Prepare a diagnostic-only TPC-H candidate generation from accepted state 1."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(
    "/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/"
    "pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/"
    "experience_sgd/attempt48_global_replay_cap5_20260726"
)
SOURCE = ROOT / "gradient_debug_round17_bound_generic_custom_v1_20260729"
PRIOR_FORMAL = ROOT / "formal10_exact_plan_regime_round23_candidate_v2_20260729T1540"
OUTPUT = ROOT / "gradient_debug_round24_accepted1_candidates_v2_20260729T1706"
ACCEPTED_COUNTER = (
    ROOT
    / "run/attempt48_resource_correction_22c1730m16r20w36w_rolling_v2."
    "accepted_shrink.counter.txt"
)

EXPECTED_SOURCE_CONFIG_SHA = (
    "e0eab9801ee9980d166f3173f78e99ddadbf47ef9b98a3ada8d8033c60163199"
)
EXPECTED_FORMAL_STATE_SHA = (
    "13829704af08c96e7c63b3b4d9515b747319af7a219af7e463ded5d1eb66fca5"
)
EXPECTED_FORMAL_SUMMARY_SHA = (
    "93566bb770f930a229f5a1650d1c009fb82c8ccaf830e9c249d6b53a0dfcae07"
)
EXPECTED_OPTIMIZER_SHA = (
    "9a6b67e4ec1433e8c577a9bf18b5728610f0af24967746a4e37838d18e17a200"
)
EXPECTED_PLAN_SIGNATURE_CALIBRATION_SHA = (
    "9bd2bb9e5e195abb03702c305c1d088e64496c82e7292b32cb08f628497590d4"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=str(path.parent),
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    atomic_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"output already exists: {OUTPUT}")
    bindings = {
        SOURCE / "config.json": EXPECTED_SOURCE_CONFIG_SHA,
        SOURCE / "code/parameter_optimizer.py": EXPECTED_OPTIMIZER_SHA,
        SOURCE / "plan_signature_calibration.json": (
            EXPECTED_PLAN_SIGNATURE_CALIBRATION_SHA
        ),
        PRIOR_FORMAL / "formal_state.json": EXPECTED_FORMAL_STATE_SHA,
        PRIOR_FORMAL / "summary.json": EXPECTED_FORMAL_SUMMARY_SHA,
    }
    for path, expected in bindings.items():
        observed = sha256(path)
        if observed != expected:
            raise RuntimeError(f"SHA mismatch for {path}: {observed} != {expected}")
    if int(ACCEPTED_COUNTER.read_text(encoding="utf-8").strip()) != 1:
        raise RuntimeError("formal accepted counter is not exactly 1")

    state = json.loads((PRIOR_FORMAL / "formal_state.json").read_text(encoding="utf-8"))
    if (
        state.get("status") != "complete"
        or state.get("baseline_restored") is not True
        or int(state.get("accepted_count", -1)) != 1
        or int(state.get("batch_added_accepted_count", -1)) != 1
        or int(state.get("physical_call_count", -1)) != 5
        or int(state.get("valid_replay_count", -1)) != 5
        or int(state.get("technical_failure_count", -1)) != 0
    ):
        raise RuntimeError("prior formal state does not match the accepted-1 lineage")

    config = json.loads((SOURCE / "config.json").read_text(encoding="utf-8"))
    config["initial_params"] = dict(state["incumbent_params"])
    for name, range_state in state["range_state"].items():
        param_range = config["param_ranges"][name]
        param_range["min_value"] = float(range_state["min_value"])
        param_range["max_value"] = float(range_state["max_value"])
        param_range["transform"] = str(range_state["transform"])
    gradient = config["optimizer"]["gradient"]
    gradient.update(
        {
            "relative_step_probe_pct": [5, 10, 20, 40, 80],
            "max_param_delta_rel_pct": 80,
            "subset_log_dir": str(OUTPUT / "subset_logs"),
        }
    )
    config["output"]["directory"] = str(OUTPUT)
    config["logging"]["history_path"] = str(OUTPUT / "optimizer_history.jsonl")
    config["final_evaluation"]["feedback_loop"]["enabled"] = False
    config["final_evaluation"]["run_validation"] = False
    config["formal_outer_protocol"].update(
        {
            "formal_search_accepted_count_start": 0,
            "formal_search_replay_count_start": 0,
            "initial_real_replay_count": 0,
            "valid_initial_batch_count_start": 0,
        }
    )
    for key in (
        "formal_search_accepted_counter_path",
        "formal_search_replay_counter_path",
        "global_replay_counter_path",
    ):
        config["formal_outer_protocol"].pop(key, None)
    config["_tpch_round24_generation"] = {
        "schema_version": "tpch_accepted1_diagnostic_candidate_generation_v1",
        "diagnostic_only": True,
        "accepted_update_allowed": False,
        "incumbent_update_allowed": False,
        "range_update_allowed": False,
        "gradient_update_allowed": False,
        "qps_used_for_inner_scoring": False,
        "generation_only_environment_required": True,
        "maximum_physical_workload_calls": 1,
        "source_formal_state": str(PRIOR_FORMAL / "formal_state.json"),
        "source_formal_state_sha256": EXPECTED_FORMAL_STATE_SHA,
        "inherited_accepted_count": 1,
        "incumbent_params": dict(state["incumbent_params"]),
        "inherited_range_state": dict(state["range_state"]),
    }

    OUTPUT.mkdir(parents=True)
    shutil.copy2(
        SOURCE / "plan_signature_calibration.json",
        OUTPUT / "plan_signature_calibration.json",
    )
    if (
        sha256(OUTPUT / "plan_signature_calibration.json")
        != EXPECTED_PLAN_SIGNATURE_CALIBRATION_SHA
    ):
        raise RuntimeError("copied plan-signature calibration SHA mismatch")
    atomic_json(OUTPUT / "config.json", config)
    manifest = {
        "schema_version": "tpch_round24_candidate_generation_manifest_v1",
        "output": str(OUTPUT),
        "config_sha256": sha256(OUTPUT / "config.json"),
        "source_config_sha256": EXPECTED_SOURCE_CONFIG_SHA,
        "source_formal_state_sha256": EXPECTED_FORMAL_STATE_SHA,
        "source_formal_summary_sha256": EXPECTED_FORMAL_SUMMARY_SHA,
        "parameter_optimizer_sha256": EXPECTED_OPTIMIZER_SHA,
        "plan_signature_calibration_sha256": (
            EXPECTED_PLAN_SIGNATURE_CALIBRATION_SHA
        ),
        "inherited_accepted_count": 1,
        "diagnostic_only": True,
        "maximum_physical_workload_calls": 1,
        "qps_used_for_inner_scoring": False,
    }
    atomic_json(OUTPUT / "manifest.json", manifest)
    print(json.dumps(manifest, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

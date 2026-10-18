#!/usr/bin/env python3
"""Zero-workload preflight for a sealed TPC-C v30 controller handover."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

try:
    import tpcc_autonomous_target10_controller_v25 as v25
    import tpcc_autonomous_target10_controller_v30_score_stability as v30
except ModuleNotFoundError:
    from tools import tpcc_autonomous_target10_controller_v25 as v25
    from tools import tpcc_autonomous_target10_controller_v30_score_stability as v30


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = args.manifest.resolve()
    if v25.sha256(manifest_path) != args.manifest_sha256:
        raise RuntimeError("v30 manifest SHA mismatch")
    manifest = v25.load_json(manifest_path)
    v25.verify_pins(manifest)
    if (
        int(manifest.get("controller_version", -1)) != 30
        or manifest.get("v30_activation_authorized") is not True
        or manifest.get("score_stability_contract")
        != "cpu_io_memory_window_stability_plus_separable_score_ci_v30"
    ):
        raise RuntimeError("v30 manifest contract mismatch")
    reclassification_path = Path(
        manifest["v30_zero_call_reclassification_path"]
    ).resolve()
    if (
        not reclassification_path.is_file()
        or v25.sha256(reclassification_path)
        != manifest["v30_zero_call_reclassification_sha256"]
    ):
        raise RuntimeError("v30 zero-call reclassification SHA mismatch")
    reclassification = v25.load_json(reclassification_path)
    if (
        reclassification.get("physical_workload_calls_added") != 0
        or reclassification.get("formal_state_mutated") is not False
        or reclassification.get("all_high_io_cv_rows_probe_only") is not True
        or reclassification.get("all_calibration_ineligible") is not True
        or reclassification.get("formal_admission_authorized") is not False
    ):
        raise RuntimeError("v30 zero-call reclassification contract failed")
    state_path = Path(manifest["controller_state_path"]).resolve()
    formal_path = Path(manifest["formal_state_path"]).resolve()
    if v25.sha256(state_path) != manifest["initial_controller_state_sha256"]:
        raise RuntimeError("controller state moved after v30 manifest sealing")
    if v25.sha256(formal_path) != manifest["initial_formal_state_sha256"]:
        raise RuntimeError("formal state moved after v30 manifest sealing")
    formal = v25.load_json(formal_path)
    expected_accepted = int(manifest.get("formal_accepted_start", -1))
    expected_incumbent = (
        3.9894795578792417
        if expected_accepted == 0
        else 4.708709193584568
    )
    if (
        formal.get("status") != "ready"
        or int(formal.get("accepted_count", -1)) != expected_accepted
        or not math.isclose(
            float(formal.get("incumbent_score")),
            expected_incumbent,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    ):
        raise RuntimeError(
            f"formal state is not the accepted={expected_accepted} v30 boundary"
        )
    if expected_accepted == 0 and (
        int(formal.get("inherited_accepted", -1)) != 0
        or int(formal.get("historical_accepted_excluded", -1)) != 2
        or int(formal.get("formal_physical_call_count", -1)) != 0
        or int(formal.get("formal_valid_replay_count", -1)) != 0
        or int(formal.get("formal_technical_failure_count", -1)) != 0
    ):
        raise RuntimeError("fresh0 accounting boundary mismatch")
    if manifest.get("v30_algorithm_frozen") is not True:
        raise RuntimeError("v30 algorithm is not frozen")
    freeze_path = Path(str(manifest.get("v30_freeze_report_path") or "")).resolve()
    if (
        not freeze_path.is_file()
        or v25.sha256(freeze_path) != manifest.get("v30_freeze_report_sha256")
    ):
        raise RuntimeError("v30 freeze report SHA mismatch")
    freeze = v25.load_json(freeze_path)
    if (
        freeze.get("passed") is not True
        or freeze.get("physical_workload_calls_added") != 0
        or freeze.get("formal_accepted_start") != expected_accepted
        or freeze.get("historical_accepted_excluded") != 2
        or freeze.get("selection_mode") != "diagnostic"
        or freeze.get("selection_fingerprint_collision_ids") != []
    ):
        raise RuntimeError("v30 freeze report contract mismatch")

    regression = manifest["v30_real_regression_validation153"]
    selection_path = Path(regression["selection_path"]).resolve()
    ledger_path = Path(regression["ledger_path"]).resolve()
    selection = v25.load_json(selection_path)
    ledger = v25.load_json(ledger_path)
    if (
        int(ledger.get("real_workload_invocation_count", -1))
        != int(regression["expected_physical"])
        or len(ledger.get("successful") or []) != int(regression["expected_valid"])
        or len(ledger.get("technical_failures") or [])
        != int(regression["expected_technical"])
    ):
        raise RuntimeError("real validation153 regression accounting mismatch")
    base_audit = {
        "mode": "diagnostic_validation",
        "physical_calls": 5,
        "valid_replays": 5,
        "technical_failures": 0,
        "trusted_spearman": float(regression["expected_raw_trusted_spearman"]),
        "calibration_rows": [
            {
                "plan": row["workload_plan_fingerprint"],
                "rank": int(row["rank"]),
                "resource_scalar": {"cpu": 1.0, "io": 1.0, "memory": 1.0},
                "actual_resource": row["actual_resource"],
                "actual_score": row["actual_score"],
            }
            for row in ledger["successful"]
        ],
    }
    upgraded = v30.apply_score_stability_audit(base_audit, selection, ledger)
    if (
        upgraded["formal_admission_blocked_by_score_stability"] is not True
        or upgraded["stable_trusted_count"] != 0
        or upgraded["calibration_rows"] != []
        or upgraded["valid_rejected_probe_only_count"] != 5
        or upgraded["score_stability_rejection_counts_as_technical_failure"]
        is not False
    ):
        raise RuntimeError("real validation153 did not fail closed under v30")

    print(
        json.dumps(
            {
                "passed": True,
                "manifest_sha256": args.manifest_sha256,
                "formal_accepted_count": expected_accepted,
                "historical_accepted_excluded": int(
                    formal.get("historical_accepted_excluded", 0)
                ),
                "validation153_physical": 5,
                "validation153_valid": 5,
                "validation153_technical": 0,
                "validation153_v30_stable_trusted": 0,
                "validation153_v30_calibration_eligible": 0,
                "validation153_formal_admission": False,
                "physical_workload_calls_added": 0,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

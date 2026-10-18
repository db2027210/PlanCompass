#!/usr/bin/env python3
"""Zero-call v30 score-stability reclassification for validation153/154."""

from __future__ import annotations

import argparse
import copy
from pathlib import Path
from typing import Any, Dict

try:
    import tpcc_autonomous_target10_controller_v25 as v25
    import tpcc_autonomous_target10_controller_v30_score_stability as v30
except ModuleNotFoundError:
    from tools import tpcc_autonomous_target10_controller_v25 as v25
    from tools import tpcc_autonomous_target10_controller_v30_score_stability as v30


PROJECT_ROOT = Path("/home/anonymous/program/resource_adaptive")
FORMAL_STATE = (
    PROJECT_ROOT
    / "history/tpcc_final_algorithm_fresh10_v29_20260731T2005/formal_state.json"
)
CONTROLLER_ROOT = (
    PROJECT_ROOT
    / "history/tpcc_final_algorithm_fresh10_v29_20260731T2005/autonomous_controller_v32"
)
BATCH_PARENT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "formal10_final_algorithm_fresh10_v29_20260731T2005/autonomous_runs_v32"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    return parser.parse_args()


def _base_audit(selection: Dict[str, Any], ledger: Dict[str, Any]) -> Dict[str, Any]:
    trusted = [item for item in selection["top5"] if item["selection_role"] == "trusted"]
    by_rank = {int(item["rank"]): item for item in ledger["successful"]}
    pairs = [
        (float(item["predicted_score"]), float(by_rank[int(item["rank"])]["actual_score"]))
        for item in trusted
    ]
    return {
        "mode": "diagnostic_validation",
        "clean": True,
        "physical_calls": 5,
        "valid_replays": 5,
        "technical_failures": 0,
        "baseline_restored": True,
        "trusted_count": 3,
        "trusted_pairs": pairs,
        "trusted_spearman": v25.spearman(
            [item[0] for item in pairs], [item[1] for item in pairs]
        ),
        "calibration_rows": [
            {
                "plan": result["workload_plan_fingerprint"],
                "rank": int(result["rank"]),
                "resource_scalar": {"cpu": 1.0, "io": 1.0, "memory": 1.0},
                "actual_resource": copy.deepcopy(result["actual_resource"]),
                "actual_score": float(result["actual_score"]),
            }
            for result in ledger["successful"]
        ],
    }


def _reclassify(ordinal: int) -> Dict[str, Any]:
    selection_path = CONTROLLER_ROOT / f"prepare_{ordinal}/sealed_run/top5.json"
    run_root = BATCH_PARENT / f"autonomous_diagnostic_validation_{ordinal}"
    ledger_path = run_root / "strict_top5_real_replay_round14_ledger_v1.json"
    summary_path = run_root / "top5_real_replay_round14_diagnostic_v1/summary.json"
    selection = v25.load_json(selection_path)
    ledger = v25.load_json(ledger_path)
    summary = v25.load_json(summary_path)
    if (
        int(ledger.get("real_workload_invocation_count", -1)) != 5
        or len(ledger.get("successful") or []) != 5
        or ledger.get("technical_failures") not in ([], None)
        or summary.get("baseline_params_restored") is not True
        or summary.get("baseline_snapshot_restored") is not True
        or summary.get("accepted_count_delta") != 0
        or summary.get("incumbent_updated") is not False
        or summary.get("range_updated") is not False
        or summary.get("gradient_update_applied") is not False
        or summary.get("state_commit_eligible") is not False
    ):
        raise RuntimeError(f"validation{ordinal} is not clean state-neutral5")
    upgraded = v30.apply_score_stability_audit(
        _base_audit(selection, ledger), selection, ledger
    )
    return {
        "ordinal": ordinal,
        "selection_path": str(selection_path),
        "selection_sha256": v25.sha256(selection_path),
        "ledger_path": str(ledger_path),
        "ledger_sha256": v25.sha256(ledger_path),
        "summary_path": str(summary_path),
        "summary_sha256": v25.sha256(summary_path),
        "physical_calls": 5,
        "valid_replays": 5,
        "technical_failures": 0,
        "baseline_restored": True,
        "raw_trusted_spearman": upgraded[
            "raw_trusted_spearman_before_score_stability"
        ],
        "stable_trusted_count": upgraded["stable_trusted_count"],
        "trusted_score_confidence_intervals_separable": upgraded[
            "trusted_score_confidence_intervals_separable"
        ],
        "valid_rejected_probe_only_count": upgraded[
            "valid_rejected_probe_only_count"
        ],
        "calibration_eligible_replay_count": upgraded[
            "calibration_eligible_replay_count"
        ],
        "formal_admission_blocked": upgraded[
            "formal_admission_blocked_by_score_stability"
        ],
        "score_stability_assessments": upgraded[
            "score_stability_assessments"
        ],
    }


def main() -> int:
    args = parse_args()
    formal_sha_before = v25.sha256(FORMAL_STATE)
    formal = v25.load_json(FORMAL_STATE)
    if formal.get("status") != "ready" or int(formal.get("accepted_count", -1)) != 2:
        raise RuntimeError("formal state is not ready accepted=2")
    batches = [_reclassify(153), _reclassify(154)]
    formal_sha_after = v25.sha256(FORMAL_STATE)
    if formal_sha_after != formal_sha_before:
        raise RuntimeError("zero-call v30 reclassification changed formal state")
    output = {
        "schema_version": "tpcc_v30_zero_call_reclassification_v1",
        "contract": "cpu_io_memory_stability_plus_score_ci_separation_v30",
        "batches": batches,
        "physical_workload_calls_added": 0,
        "formal_state_mutated": False,
        "formal_state_sha256_before": formal_sha_before,
        "formal_state_sha256_after": formal_sha_after,
        "formal_accepted_count": 2,
        "all_high_io_cv_rows_probe_only": all(
            batch["valid_rejected_probe_only_count"] == 5 for batch in batches
        ),
        "all_calibration_ineligible": all(
            batch["calibration_eligible_replay_count"] == 0 for batch in batches
        ),
        "formal_admission_authorized": False,
    }
    if (
        output["all_high_io_cv_rows_probe_only"] is not True
        or output["all_calibration_ineligible"] is not True
        or any(batch["formal_admission_blocked"] is not True for batch in batches)
    ):
        raise RuntimeError("validation153/154 did not fail closed under v30")
    v25.atomic_json(args.output.resolve(), output)
    print(f"output={args.output.resolve()}", flush=True)
    print(f"output_sha256={v25.sha256(args.output.resolve())}", flush=True)
    print("physical_workload_calls_added=0", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

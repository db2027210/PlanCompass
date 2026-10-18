#!/usr/bin/env python3
"""Audit v40 and automatically hand a passing clean5 batch to formal v3."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys

try:
    import audit_tpcc_v40_postrun_gate as gate_module
    import build_tpcc_unique_exact_validation_v6 as v6
    import tpcc_v39_postrun_gate_and_formal_handoff as v39_handoff
except ModuleNotFoundError:
    from tools import audit_tpcc_v40_postrun_gate as gate_module
    from tools import build_tpcc_unique_exact_validation_v6 as v6
    from tools import tpcc_v39_postrun_gate_and_formal_handoff as v39_handoff


def promote_v40(diagnostic_selection, diagnostic_ledger, gate, selection_path, incumbent_score):
    """Use all sealed exact evidence for prediction without rewriting its roles."""
    original = copy.deepcopy(diagnostic_selection)
    adapted = copy.deepcopy(diagnostic_selection)
    promoted_key = str(
        (adapted.get("v39_plan_regime_absorption_contract") or {}).get(
            "absorbed_probe_exact_point_key"
        )
    )
    for item in adapted.get("top5") or []:
        provenance = item.get("source_provenance") or {}
        if str(item.get("exact_point_key")) == promoted_key:
            provenance["trusted_observations"] = copy.deepcopy(
                provenance.get("all_clean_observations") or []
            )
        elif item.get("selection_role") == "uncertain_probe":
            provenance["probe_observations_excluded_from_trusted"] = copy.deepcopy(
                provenance.get("all_clean_observations") or []
            )
    promoted = v39_handoff.base_handoff.promote(
        adapted, diagnostic_ledger, gate, selection_path, incumbent_score
    )
    original_by_key = {
        str(item["exact_point_key"]): item for item in original.get("top5") or []
    }
    for item in promoted.get("top5") or []:
        key = str(item["exact_point_key"])
        item["source_provenance"] = copy.deepcopy(original_by_key[key]["source_provenance"])
        confidence = item.get("confidence") or {}
        confidence.pop("probe_rows_used_for_trusted_calibration", None)
        confidence.update(
            {
                "contract": "tpcc_v40_formal_role_preserving_exact_confidence_v1",
                "historical_roles_rewritten": False,
                "formal_state_update_rows_from_historical_evidence": 0,
            }
        )
        calibration = item.get("plan_regime_calibration") or {}
        calibration.pop("probe_rows_used_for_trusted_calibration", None)
        calibration.update(
            {
                "contract": "tpcc_v40_formal_role_preserving_exact_calibration_v1",
                "historical_roles_rewritten": False,
                "formal_state_update_rows_from_historical_evidence": 0,
            }
        )
    promoted["probe_observations_affect_formal_acceptance"] = False
    promoted["v40_historical_evidence_policy"] = {
        "historical_roles_rewritten": False,
        "formal_state_update_rows": 0,
        "trusted_evidence_used_for_prediction_only_until_current_formal_call": True,
        "probe_candidates_formal_acceptance_eligible": False,
    }
    return promoted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--formal-state", type=Path, required=True)
    parser.add_argument("--expected-formal-sha256", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--formal-batch-root", type=Path, required=True)
    parser.add_argument("--formal-runner", type=Path, required=True)
    parser.add_argument("--diagnostic-runner", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--armed", action="store_true")
    args = parser.parse_args()
    output_root = args.output_root.resolve()
    if output_root.exists():
        raise FileExistsError(f"handoff output root already exists: {output_root}")
    output_root.mkdir(parents=True)
    gate = gate_module.audit_v40(
        args.selection.resolve(), args.ledger.resolve(), args.events.resolve(),
        args.summary.resolve(), args.formal_state.resolve(), args.expected_formal_sha256,
    )
    gate_path = output_root / "postrun_gate.json"
    gate_module.base.base.durable._atomic_json(gate_path, gate)
    if gate.get("passed") is not True:
        print(json.dumps({"status": "gate_failed", "gate_path": str(gate_path)}, sort_keys=True), flush=True)
        return 3
    formal_state = v6._load(args.formal_state.resolve())
    promoted = promote_v40(
        v6._load(args.selection.resolve()),
        v6._load(args.ledger.resolve()),
        gate,
        args.selection.resolve(),
        float(formal_state["incumbent_score"]),
    )
    promoted["promoted_from_v40_clean5_exact_recalibration"] = {
        "contract": "tpcc_v40_clean_gate_bound_formal_promotion_v1",
        "diagnostic_selection_sha256": v6._sha256(args.selection.resolve()),
        "diagnostic_gate": copy.deepcopy(gate["v40_algorithm_gate"]),
        "threshold_changed": False,
        "historical_probe_formal_state_update_rows": 0,
        "manual_candidate_selection": False,
    }
    formal_selection_path = output_root / "formal_top5.json"
    v6.repeat_v2.import_base().atomic_json(formal_selection_path, promoted)
    formal_selection_sha = v6._sha256(formal_selection_path)
    formal_gate = {
        "schema_version": "tpcc_v40_autonomous_formal_gate_v1",
        "passed": True,
        "formal_start_authorized": True,
        "selection_sha256": formal_selection_sha,
        "quality_gate": copy.deepcopy(gate["quality_gate"]),
        "v39_algorithm_gate": copy.deepcopy(gate["v39_algorithm_gate"]),
        "v40_algorithm_gate": copy.deepcopy(gate["v40_algorithm_gate"]),
        "source_diagnostic_selection_sha256": gate["selection_sha256"],
        "score_span_gate": copy.deepcopy(gate["score_span_gate"]),
        "qps_role": "outer_audit_only_not_inner_score",
    }
    formal_gate_path = output_root / "formal_gate.json"
    gate_module.base.base.durable._atomic_json(formal_gate_path, formal_gate)
    handoff = {
        "schema_version": "tpcc_v40_formal_handoff_seal_v1",
        "armed": args.armed,
        "workload_calls_added_during_preparation": 0,
        "formal_state_sha256": v6._sha256(args.formal_state.resolve()),
        "postrun_gate_sha256": v6._sha256(gate_path),
        "formal_selection_sha256": formal_selection_sha,
        "formal_gate_sha256": v6._sha256(formal_gate_path),
        "formal_runner_sha256": v6._sha256(args.formal_runner.resolve()),
        "diagnostic_runner_sha256": v6._sha256(args.diagnostic_runner.resolve()),
        "formal_batch_root": str(args.formal_batch_root.resolve()),
        "manual_candidate_selection": False,
    }
    handoff_path = output_root / "handoff_seal.json"
    gate_module.base.base.durable._atomic_json(handoff_path, handoff)
    if not args.armed:
        print(json.dumps({"status": "prepared_not_launched", "handoff": handoff}, sort_keys=True), flush=True)
        return 0
    command = [
        sys.executable, "-u", str(args.formal_runner.resolve()),
        str(formal_selection_path), formal_selection_sha,
        str(formal_gate_path), v6._sha256(formal_gate_path),
        str(args.diagnostic_runner.resolve()), str(args.baseline.resolve()),
        str(args.formal_state.resolve()), str(args.formal_batch_root.resolve()),
    ]
    completed = subprocess.run(command, check=False, timeout=10800)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())

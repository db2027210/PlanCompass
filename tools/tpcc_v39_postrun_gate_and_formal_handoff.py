#!/usr/bin/env python3
"""Gate v39 diagnostics and automatically hand a passed batch to formal v3."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys

try:
    import audit_tpcc_v39_postrun_gate as gate_module
    import build_tpcc_unique_exact_validation_v6 as v6
    import tpcc_v37_postrun_gate_and_formal_handoff as base_handoff
except ModuleNotFoundError:
    from tools import audit_tpcc_v39_postrun_gate as gate_module
    from tools import build_tpcc_unique_exact_validation_v6 as v6
    from tools import tpcc_v37_postrun_gate_and_formal_handoff as base_handoff


def promote_v39(
    diagnostic_selection,
    diagnostic_ledger,
    postrun_gate,
    source_selection_path: Path,
    incumbent_score: float,
):
    """Promote with certificate evidence without rewriting historical roles."""
    original = copy.deepcopy(diagnostic_selection)
    adapted = copy.deepcopy(diagnostic_selection)
    contract = adapted.get("v39_plan_regime_absorption_contract") or {}
    certificate = contract.get("promotion_certificate") or {}
    promoted_key = str(certificate.get("exact_point_key") or "")
    source_rows = copy.deepcopy(certificate.get("source_rows") or [])
    if (
        certificate.get("passed") is not True
        or int(certificate.get("promotion_evaluation_rows", -1)) != 3
        or int(certificate.get("formal_state_update_rows", -1)) != 0
        or len(source_rows) != 3
        or not promoted_key
    ):
        raise RuntimeError("v39 accumulated-probe certificate is invalid")
    adapted_rows = [
        item for item in adapted.get("top5") or []
        if str(item.get("exact_point_key")) == promoted_key
    ]
    if len(adapted_rows) != 1 or adapted_rows[0].get("selection_role") != "trusted":
        raise RuntimeError("v39 promoted candidate is not uniquely trusted")
    # The v37 numeric promotion routine requires a role-evidence input list.
    # This temporary adapter is never serialized; the original immutable probe
    # provenance is restored immediately below.
    adapted_rows[0]["source_provenance"]["trusted_observations"] = source_rows
    promoted = base_handoff.promote(
        adapted,
        diagnostic_ledger,
        postrun_gate,
        source_selection_path,
        incumbent_score,
    )
    original_by_key = {
        str(item["exact_point_key"]): item for item in original.get("top5") or []
    }
    for candidate in promoted.get("top5") or []:
        key = str(candidate["exact_point_key"])
        candidate["source_provenance"] = copy.deepcopy(
            original_by_key[key]["source_provenance"]
        )
        if key != promoted_key:
            continue
        candidate["accumulated_probe_promotion_certificate"] = copy.deepcopy(certificate)
        confidence = candidate.get("confidence") or {}
        confidence.pop("probe_rows_used_for_trusted_calibration", None)
        confidence.update(
            {
                "contract": "tpcc_v39_formal_accumulated_probe_promotion_confidence_v1",
                "historical_probe_evaluation_rows_used": 3,
                "formal_state_update_rows_from_historical_probe_evidence": 0,
                "historical_roles_rewritten": False,
            }
        )
        calibration = candidate.get("plan_regime_calibration") or {}
        calibration.pop("probe_rows_used_for_trusted_calibration", None)
        calibration.update(
            {
                "contract": "tpcc_v39_certificate_promoted_exact_formal_calibration_v1",
                "promotion_evaluation_rows": 3,
                "formal_state_update_rows": 0,
                "historical_roles_rewritten": False,
            }
        )
    promoted["promoted_from_diagnostic_validation"]["v39_promotion_certificate"] = (
        copy.deepcopy(certificate)
    )
    promoted["probe_observations_affect_formal_acceptance"] = False
    promoted["v39_historical_probe_evidence_policy"] = {
        "promotion_evaluation_rows": 3,
        "formal_state_update_rows": 0,
        "historical_roles_rewritten": False,
        "post_certificate_evidence_role": "promotion_evaluation_only",
    }
    return promoted


def main() -> int:
    parser = argparse.ArgumentParser()
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
    gate = gate_module.audit_v39(
        args.selection.resolve(),
        args.ledger.resolve(),
        args.events.resolve(),
        args.summary.resolve(),
        args.formal_state.resolve(),
        args.expected_formal_sha256,
    )
    gate_path = output_root / "postrun_gate.json"
    gate_module.base.durable._atomic_json(gate_path, gate)
    if gate.get("passed") is not True:
        print(
            json.dumps(
                {"status": "gate_failed", "gate_path": str(gate_path)},
                sort_keys=True,
            ),
            flush=True,
        )
        return 3
    formal_state = v6._load(args.formal_state.resolve())
    promoted = promote_v39(
        v6._load(args.selection.resolve()),
        v6._load(args.ledger.resolve()),
        gate,
        args.selection.resolve(),
        float(formal_state["incumbent_score"]),
    )
    promoted["promoted_from_v39_plan_regime_absorption"] = {
        "contract": "tpcc_v39_clean_gate_bound_formal_promotion_v1",
        "diagnostic_selection_sha256": v6._sha256(args.selection.resolve()),
        "diagnostic_gate": copy.deepcopy(gate["v39_algorithm_gate"]),
        "historical_probe_promotion_evaluation_rows": 3,
        "historical_probe_formal_state_update_rows": 0,
        "manual_candidate_selection": False,
    }
    formal_selection_path = output_root / "formal_top5.json"
    v6.repeat_v2.import_base().atomic_json(formal_selection_path, promoted)
    formal_selection_sha = v6._sha256(formal_selection_path)
    formal_gate = {
        "schema_version": "tpcc_v39_autonomous_formal_gate_v1",
        "passed": True,
        "formal_start_authorized": True,
        "selection_sha256": formal_selection_sha,
        "quality_gate": copy.deepcopy(gate["quality_gate"]),
        "v39_algorithm_gate": copy.deepcopy(gate["v39_algorithm_gate"]),
        "source_diagnostic_selection_sha256": gate["selection_sha256"],
        "score_span_gate": copy.deepcopy(gate["score_span_gate"]),
        "qps_role": "outer_audit_only_not_inner_score",
    }
    formal_gate_path = output_root / "formal_gate.json"
    gate_module.base.durable._atomic_json(formal_gate_path, formal_gate)
    handoff = {
        "schema_version": "tpcc_v39_formal_handoff_seal_v1",
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
    gate_module.base.durable._atomic_json(handoff_path, handoff)
    if not args.armed:
        print(
            json.dumps(
                {"status": "prepared_not_launched", "handoff": handoff},
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    command = [
        sys.executable,
        "-u",
        str(args.formal_runner.resolve()),
        str(formal_selection_path),
        formal_selection_sha,
        str(formal_gate_path),
        v6._sha256(formal_gate_path),
        str(args.diagnostic_runner.resolve()),
        str(args.baseline.resolve()),
        str(args.formal_state.resolve()),
        str(args.formal_batch_root.resolve()),
    ]
    completed = subprocess.run(command, check=False, timeout=10800)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())

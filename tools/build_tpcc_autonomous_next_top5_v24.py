#!/usr/bin/env python3
"""Build the next TPC-C Top-5 from a sealed dynamic evidence manifest."""

from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tools.build_tpcc_round14_robust_regime_top5_v24 as robust


def main() -> int:
    if len(sys.argv) != 5:
        raise SystemExit(
            "usage: build_tpcc_autonomous_next_top5.py "
            "SOURCE_MANIFEST FORMAL_STATE OUTPUT MODE"
        )
    source_manifest, formal_state, output = map(
        lambda item: Path(item).resolve(), sys.argv[1:4]
    )
    mode = sys.argv[4]
    manifest = json.loads(source_manifest.read_text(encoding="utf-8"))
    if (
        manifest.get("schema_version")
        != "tpcc_autonomous_evidence_manifest_v1"
        or mode not in {"formal", "diagnostic", "diagnostic_validation"}
    ):
        raise RuntimeError("autonomous builder input contract mismatch")
    sources = []
    for item in manifest.get("sources") or []:
        selection = Path(item["selection_path"]).resolve()
        ledger = Path(item["ledger_path"]).resolve()
        if (
            not selection.is_file()
            or not ledger.is_file()
            or robust.common.sha256(selection) != item["selection_sha256"]
            or robust.common.sha256(ledger) != item["ledger_sha256"]
        ):
            raise RuntimeError(f"source SHA mismatch: {item.get('name')}")
        sources.append((selection, ledger, str(item["name"])))
    if not sources or output.exists():
        raise RuntimeError("empty sources or existing output")

    robust.SOURCES = tuple(sources)
    robust.OUTPUT = output
    # Every autonomous mode is generated inside the authoritative formal
    # range.  The former global-domain diagnostic fallback could select
    # 1%/99% near-vertices many orders of magnitude away from the incumbent
    # and, more seriously, could later admit a trusted point outside the
    # shrink range.
    robust.PARAM_RANGES_OVERRIDE = formal_state
    robust.FORMAL_STATE_AUDIT_SOURCE = formal_state
    robust.CANDIDATE_RANGE_POLICY = (
        "authoritative_formal_range_safe_frontier_v2"
    )
    robust.PROBES_MUST_BE_UNKNOWN = False
    robust.ALLOW_SPARSE_EXACT_REGIME_PROBES = True
    robust.SPARSE_EXACT_REGIME_MAX_OBSERVATIONS = 1
    robust.TRUSTED_PLAN_DIVERSITY_REQUIRED = mode != "formal"
    robust.DIAGNOSTIC_PROBE_ONLY = mode == "diagnostic"
    robust.MINIMUM_DIAGNOSTIC_SCORE_SPAN = float(
        manifest.get("minimum_diagnostic_score_span") or 0.0
    )
    robust.MIN_TRUSTED_PREDICTED_SCORE_SPAN = float(
        manifest.get("minimum_trusted_predicted_score_span")
        or manifest.get("minimum_diagnostic_score_span")
        or 0.0
    )
    # The fixed experiment contract authorizes formal execution after the
    # diagnostic quality gate passes.  Expected improvement remains a ranking
    # signal; it is not an extra admission gate.  A non-improving real replay
    # is handled as a formal rejection by the runner.
    robust.FORMAL_EXPECTED_IMPROVEMENT_REQUIRED = False
    robust.ALLOW_VALIDATED_ANCHOR_REPLAY = (
        mode in {"formal", "diagnostic_validation"}
    )
    robust.EXTRA_EXCLUDED_FINGERPRINTS = {
        str(item)
        for item in manifest.get("excluded_fingerprints") or []
    }
    robust.main()

    if mode == "diagnostic":
        value = json.loads(output.read_text(encoding="utf-8"))
        value["selection_contract"] = (
            "tpcc_round12_current_plan_discovery_probe5_v1"
        )
        value["diagnostic_acquisition_reason"] = manifest.get(
            "diagnostic_acquisition_reason"
        )
        for item in value["top5"]:
            item.update(
                {
                    "selection_role": "uncertain_probe",
                    "trusted": False,
                    "uncertain_probe": True,
                    "diagnostic_only": True,
                    "incumbent_update_eligible": False,
                    "probe_kind": "autonomous_gate_recovery_probe",
                }
            )
        output.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    elif mode == "diagnostic_validation":
        value = json.loads(output.read_text(encoding="utf-8"))
        value["diagnostic_role_evaluation_only"] = True
        value["state_mutation_contract"] = {
            "accepted_update": False,
            "incumbent_update": False,
            "range_update": False,
            "gradient_update": False,
        }
        value["diagnostic_acquisition_reason"] = manifest.get(
            "diagnostic_acquisition_reason"
        )
        output.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    else:
        value = json.loads(output.read_text(encoding="utf-8"))
        trusted = [
            item
            for item in value["top5"]
            if item["selection_role"] == "trusted"
        ]
        if (
            len(trusted) != 3
            or value.get("formal_admission_score")
            != "predicted_score_interval.lower"
            or any(item.get("formal_lcb_score") is None for item in trusted)
        ):
            raise RuntimeError(
                "formal Top-3 lacks trusted confidence-ranked candidates"
            )
        value["formal_expected_improvement_required"] = False
        value["formal_entry_admission_contract"] = (
            "diagnostic_quality_gate_passed_then_real_replay_accept_or_reject_v1"
        )
        value["formal_non_improving_candidate_policy"] = (
            "real_replay_rejected_without_state_update"
        )
        output.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

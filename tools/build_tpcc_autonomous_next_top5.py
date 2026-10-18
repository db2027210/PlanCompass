#!/usr/bin/env python3
"""Build the next TPC-C Top-5 from a sealed dynamic evidence manifest."""

from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tools.build_tpcc_round14_robust_regime_top5 as robust


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
    robust.FORMAL_EXPECTED_IMPROVEMENT_REQUIRED = mode == "formal"
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
            value.get("formal_expected_improvement_required") is not True
            or len(trusted) != 3
            or trusted[0].get("formal_improvement_eligible") is not True
            or float(
                trusted[0]["predicted_improvement_lower_bound"]
            )
            <= float(value["minimum_predicted_improvement"])
        ):
            raise RuntimeError(
                "formal Top-3 fails confidence-lower-bound improvement gate"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

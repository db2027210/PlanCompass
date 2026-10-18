#!/usr/bin/env python3
"""Strict v40 postrun gate layered on v39 durable/accounting checks."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Dict

try:
    import audit_tpcc_v39_postrun_gate as base
    import build_tpcc_v40_clean5_exact_recalibration as builder
except ModuleNotFoundError:
    from tools import audit_tpcc_v39_postrun_gate as base
    from tools import build_tpcc_v40_clean5_exact_recalibration as builder


def audit_v40(
    selection_path: Path,
    ledger_path: Path,
    events_path: Path,
    summary_path: Path,
    formal_state_path: Path,
    expected_formal_sha256: str,
) -> Dict[str, Any]:
    result = base.audit_v39(
        selection_path,
        ledger_path,
        events_path,
        summary_path,
        formal_state_path,
        expected_formal_sha256,
    )
    selection = base.base.durable._load(selection_path)
    contract = selection.get("v40_build_contract") or {}
    top5 = selection.get("top5") or []
    identifiers = [str(item["three_level_fingerprint"]["id"]) for item in top5]
    historical = set()
    for descriptor in selection.get("source_batches") or []:
        source = base.base.durable._load(Path(str(descriptor["selection_path"])))
        historical.update(
            str((item.get("three_level_fingerprint") or {}).get("id") or "")
            for item in source.get("top5") or []
        )
    checks = {
        "algorithm_contract": contract.get("contract") == builder.BUILD_CONTRACT,
        "sealed_clean5_authority": bool(
            contract.get("source_gate_only_failure") == "trusted_score_mae_within_gate"
            and contract.get("threshold_changed") is False
            and int(contract.get("current_clean_trusted_rows_absorbed", -1)) == 3
            and contract.get("direct_formal_promotion") is False
            and all(
                Path(str(pin.get("path") or "")).is_file()
                and base.base.durable._sha256(Path(str(pin["path"]))) == pin.get("sha256")
                for pin in (contract.get("sealed_authority") or {}).values()
            )
        ),
        "five_historically_novel_fingerprints": bool(
            len(identifiers) == 5
            and len(set(identifiers)) == 5
            and not (set(identifiers) & historical)
        ),
        "trusted3_probe2": [item.get("selection_role") for item in top5]
        == ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"],
        "state_neutral_selection": selection.get("state_mutation_contract")
        == {
            "accepted_update": False,
            "gradient_update": False,
            "incumbent_update": False,
            "range_update": False,
        },
        "no_manual_selection": contract.get("manual_candidate_selection") is False,
    }
    passed = bool(result.get("passed") is True and all(checks.values()))
    result = copy.deepcopy(result)
    result.update(
        {
            "schema_version": "tpcc_v40_strict_postrun_entry_gate_v1",
            "passed": passed,
            "formal_start_authorized": passed,
            "v40_algorithm_gate": {
                "passed": all(checks.values()),
                "checks": checks,
                "contract": copy.deepcopy(contract),
            },
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--formal-state", type=Path, required=True)
    parser.add_argument("--expected-formal-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit_v40(
        args.selection.resolve(), args.ledger.resolve(), args.events.resolve(),
        args.summary.resolve(), args.formal_state.resolve(), args.expected_formal_sha256,
    )
    base.base.durable._atomic_json(args.output.resolve(), result)
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["passed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())

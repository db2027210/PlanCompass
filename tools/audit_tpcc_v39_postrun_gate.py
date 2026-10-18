#!/usr/bin/env python3
"""Strict v39 TPC-C postrun gate layered on the sealed v38 accounting gate."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Dict

try:
    import audit_tpcc_v37_postrun_gate as base
    import build_tpcc_v39_plan_regime_absorption as builder
except ModuleNotFoundError:
    from tools import audit_tpcc_v37_postrun_gate as base
    from tools import build_tpcc_v39_plan_regime_absorption as builder


def audit_v39(
    selection_path: Path,
    ledger_path: Path,
    events_path: Path,
    summary_path: Path,
    formal_state_path: Path,
    expected_formal_sha256: str,
) -> Dict[str, Any]:
    result = base.audit_v37(
        selection_path,
        ledger_path,
        events_path,
        summary_path,
        formal_state_path,
        expected_formal_sha256,
    )
    selection = base.durable._load(selection_path)
    contract = selection.get("v39_plan_regime_absorption_contract") or {}
    top5 = selection.get("top5") or []
    identifiers = [
        str((item.get("three_level_fingerprint") or {}).get("id") or "")
        for item in top5
    ]
    historical = set()
    for descriptor in selection.get("source_batches") or []:
        source = base.durable._load(Path(str(descriptor["selection_path"])))
        historical.update(
            str((item.get("three_level_fingerprint") or {}).get("id") or "")
            for item in source.get("top5") or []
        )
    certificate = contract.get("promotion_certificate") or {}
    promoted_key = str(contract.get("absorbed_probe_exact_point_key") or "")
    promoted = [item for item in top5 if str(item.get("exact_point_key")) == promoted_key]
    source_rows = certificate.get("source_rows") or []
    checks = {
        "algorithm_contract": contract.get("contract") == builder.BUILD_CONTRACT,
        "absorbed_actual_best_probe_promoted_by_certificate": bool(
            int(contract.get("absorbed_probe_rank", -1)) == 5
            and contract.get("absorbed_probe_updates_plan_prediction") is True
            and contract.get("historical_probe_roles_rewritten") is False
            and contract.get("promotion_certificate_updates_current_selection_role") is True
            and contract.get("absorbed_probe_updates_formal_acceptance") is False
            and contract.get("absorbed_exact_point_promoted_by_certificate") is True
            and int(contract.get("promotion_evaluation_rows", -1)) == 3
            and int(contract.get("formal_state_update_rows", -1)) == 0
        ),
        "promotion_certificate_complete_and_passed": bool(
            certificate.get("contract") == builder.PROMOTION_CONTRACT
            and certificate.get("passed") is True
            and certificate.get("exact_point_key") == promoted_key
            and certificate.get("validated_plan") == contract.get("absorbed_probe_plan")
            and isinstance(certificate.get("params"), dict)
            and len(source_rows) == 3
            and len({row.get("selection_sha256") for row in source_rows}) == 3
            and len({row.get("ledger_sha256") for row in source_rows}) == 3
            and all(
                row.get("selection_role") == "uncertain_probe"
                and row.get("trusted_training_eligible") is False
                for row in source_rows
            )
            and int(certificate.get("formal_state_update_rows", -1)) == 0
            and certificate.get("history_role_rewrite_applied") is False
            and all((certificate.get("checks") or {}).values())
        ),
        "promoted_exact_is_trusted_top3": bool(
            len(promoted) == 1
            and int(promoted[0].get("rank", -1)) <= 3
            and promoted[0].get("selection_role") == "trusted"
            and promoted[0].get("trusted") is True
            and (promoted[0].get("accumulated_probe_promotion_certificate") or {}).get(
                "passed"
            )
            is True
        ),
        "five_historically_novel_fingerprints": bool(
            len(identifiers) == 5
            and "" not in identifiers
            and len(set(identifiers)) == 5
            and not (set(identifiers) & historical)
        ),
        "trusted3_probe2": [item.get("selection_role") for item in top5]
        == ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"],
        "probes_remain_state_neutral": all(
            item.get("trusted") is False
            and item.get("incumbent_update_eligible") is False
            and item.get("used_for_gradient") is False
            and item.get("used_for_inner_incumbent_update") is False
            for item in top5[3:]
        ),
        "no_manual_selection": contract.get("manual_candidate_selection") is False,
    }
    passed = bool(result.get("passed") is True and all(checks.values()))
    result = copy.deepcopy(result)
    result.update(
        {
            "schema_version": "tpcc_v39_strict_postrun_entry_gate_v1",
            "passed": passed,
            "formal_start_authorized": passed,
            "v39_algorithm_gate": {
                "passed": all(checks.values()),
                "checks": checks,
                "contract": copy.deepcopy(contract),
            },
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--formal-state", type=Path, required=True)
    parser.add_argument("--expected-formal-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit_v39(
        args.selection.resolve(),
        args.ledger.resolve(),
        args.events.resolve(),
        args.summary.resolve(),
        args.formal_state.resolve(),
        args.expected_formal_sha256,
    )
    base.durable._atomic_json(args.output.resolve(), result)
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["passed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())

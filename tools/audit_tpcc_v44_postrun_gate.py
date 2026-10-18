#!/usr/bin/env python3
"""Audit TPC-C v44 trusted3/probe2 or score-separation acquisition batches."""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
from typing import Any, Dict

try:
    import audit_tpcc_v43_postrun_gate as v43
    import build_tpcc_v44_plan_regime_score_separation as v44
except ModuleNotFoundError:
    from tools import audit_tpcc_v43_postrun_gate as v43
    from tools import build_tpcc_v44_plan_regime_score_separation as v44


def _acquisition_audit(
    selection_path: Path,
    ledger_path: Path,
    events_path: Path,
    summary_path: Path,
    formal_state_path: Path,
    expected_formal_sha256: str,
) -> Dict[str, Any]:
    base = v43.durable.audit(
        selection_path,
        ledger_path,
        events_path,
        summary_path,
        formal_state_path,
        expected_formal_sha256,
    )
    selection = v43.durable._load(selection_path)
    ledger = v43.durable._load(ledger_path)
    summary = v43.durable._load(summary_path)
    formal = v43.durable._load(formal_state_path)
    contract = selection.get("v44_build_contract") or {}
    candidates = {
        int(item["rank"]): item for item in selection.get("top5") or []
    }
    observations = {
        int(item["rank"]): item for item in ledger.get("successful") or []
    }
    identities_match = True
    for rank, observation in observations.items():
        candidate = candidates.get(rank) or {}
        protocol = observation.get("measurement_protocol") or {}
        identities_match = bool(
            identities_match
            and observation.get("workload_plan_fingerprint")
            == candidate.get("workload_plan_fingerprint")
            and (observation.get("three_level_fingerprint") or {}).get("id")
            == (candidate.get("three_level_fingerprint") or {}).get("id")
            and protocol.get("stable") is True
            and protocol.get("correctness_passed") is True
            and protocol.get("technical_failure") is False
        )
    complete = set(candidates) == set(observations) == {1, 2, 3, 4, 5}
    actual_scores = [
        float(observations[rank]["actual_score"])
        for rank in sorted(observations)
    ] if complete else []
    finite_scores = bool(actual_scores) and all(
        math.isfinite(value) for value in actual_scores
    )
    actual_span = max(actual_scores) - min(actual_scores) if finite_scores else 0.0
    identifiers = [
        str((candidates[rank].get("three_level_fingerprint") or {}).get("id") or "")
        for rank in sorted(candidates)
    ]
    keys = [
        str(candidates[rank].get("exact_point_key") or "")
        for rank in sorted(candidates)
    ]
    plans = [
        str(candidates[rank].get("workload_plan_fingerprint") or "")
        for rank in sorted(candidates)
    ]
    accounting = base["accounting"]
    isolation = base["state_isolation"]
    summary_clean = bool(
        int(summary.get("real_workload_invocation_count", -1)) == 5
        and int(summary.get("successful_real_replay_count", -1)) == 5
        and int(summary.get("technical_failure_count", -1)) == 0
        and int(summary.get("preworkload_failure_count", -1)) == 0
        and sorted(int(value) for value in summary.get("invoked_ranks") or [])
        == [1, 2, 3, 4, 5]
        and summary.get("fatal_error") is None
    )
    checks = {
        "durable_accounting_clean5": bool(
            accounting.get("passed") is True
            and int(accounting.get("physical_calls", -1)) == 5
            and int(accounting.get("valid_replays", -1)) == 5
            and int(accounting.get("technical_failures", -1)) == 0
            and int(accounting.get("preworkload_failures", -1)) == 0
            and summary_clean
            and complete
            and identities_match
        ),
        "baseline_restored_and_state_neutral": bool(
            isolation.get("passed") is True
            and v43.durable._sha256(formal_state_path) == expected_formal_sha256
            and int(formal.get("accepted_count", -1)) == 0
            and int(formal.get("formal_physical_call_count", -1)) == 0
            and int(formal.get("formal_valid_replay_count", -1)) == 0
            and int(formal.get("formal_technical_failure_count", -1)) == 0
        ),
        "score_separation_contract": bool(
            contract.get("contract") == v44.BUILD_CONTRACT
            and contract.get("mode") == "score_separation_acquisition"
            and contract.get("acquisition_contract") == v44.ACQUISITION_CONTRACT
            and contract.get("selection_algorithm")
            == "score_extrema_then_score_parameter_maximin_v1"
            and contract.get("manual_candidate_selection") is False
            and contract.get("threshold_changed") is False
            and int(contract.get("maximum_physical_calls", -1)) == 5
        ),
        "probe5_state_isolated": bool(
            [candidates[rank].get("selection_role") for rank in sorted(candidates)]
            == ["uncertain_probe"] * 5
            and all(
                candidates[rank].get("trusted") is False
                and candidates[rank].get("incumbent_update_eligible") is False
                and candidates[rank].get("used_for_gradient") is False
                and candidates[rank].get("used_for_inner_incumbent_update") is False
                for rank in candidates
            )
            and selection.get("state_mutation_contract")
            == {
                "accepted_update": False,
                "gradient_update": False,
                "incumbent_update": False,
                "range_update": False,
            }
        ),
        "five_distinct_exact_points_and_fingerprints": bool(
            len(keys) == len(set(keys)) == 5
            and "" not in keys
            and len(identifiers) == len(set(identifiers)) == 5
            and "" not in identifiers
            and all(
                candidates[rank].get("boundary_corner") is False
                for rank in candidates
            )
        ),
        "single_target_plan_regime": bool(
            len(plans) == 5
            and len(set(plans)) == 1
            and plans[0] == contract.get("target_plan_regime")
        ),
        "actual_score_separation_resolvable": bool(
            finite_scores and actual_span > 1e-12
        ),
    }
    acquisition_complete = all(checks.values())
    # Acquisition success is not formal-entry success.  The controller must
    # rebuild a 3T2P batch and run the unchanged quality gate next.
    return {
        "schema_version": v44.ACQUISITION_GATE_SCHEMA,
        "passed": False,
        "formal_start_authorized": False,
        "acquisition_complete": acquisition_complete,
        "selection_path": str(selection_path.resolve()),
        "selection_sha256": v43.durable._sha256(selection_path),
        "ledger_path": str(ledger_path.resolve()),
        "ledger_sha256": v43.durable._sha256(ledger_path),
        "events_path": str(events_path.resolve()),
        "events_sha256": v43.durable._sha256(events_path),
        "summary_path": str(summary_path.resolve()),
        "summary_sha256": v43.durable._sha256(summary_path),
        "formal_state_path": str(formal_state_path.resolve()),
        "formal_state_sha256": v43.durable._sha256(formal_state_path),
        "quality_gate": {
            "passed": False,
            "checks": {
                "durable_accounting_clean5": checks["durable_accounting_clean5"],
                "baseline_restored_and_state_neutral": checks[
                    "baseline_restored_and_state_neutral"
                ],
                "selection_algorithm_gate": all(
                    checks[name]
                    for name in (
                        "score_separation_contract",
                        "probe5_state_isolated",
                        "five_distinct_exact_points_and_fingerprints",
                        "single_target_plan_regime",
                    )
                ),
            },
            "probe_rows_excluded_from_trust_and_formal_state": True,
        },
        "acquisition_gate": {
            "passed": acquisition_complete,
            "checks": checks,
            "actual_score_span": actual_span,
            "next_action": (
                "automatic_rebuild_then_formal_gate_or_reexplore"
                if acquisition_complete
                else "fail_closed"
            ),
        },
        "accounting": copy.deepcopy(accounting),
        "state_isolation": copy.deepcopy(isolation),
    }


def audit_v44(
    selection_path: Path,
    ledger_path: Path,
    events_path: Path,
    summary_path: Path,
    formal_state_path: Path,
    expected_formal_sha256: str,
) -> Dict[str, Any]:
    selection = v43.durable._load(selection_path)
    mode = (selection.get("v44_build_contract") or {}).get("mode")
    if mode == "score_separation_acquisition":
        return _acquisition_audit(
            selection_path,
            ledger_path,
            events_path,
            summary_path,
            formal_state_path,
            expected_formal_sha256,
        )
    return v43.audit_v43(
        selection_path,
        ledger_path,
        events_path,
        summary_path,
        formal_state_path,
        expected_formal_sha256,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--formal-state", type=Path, required=True)
    parser.add_argument("--expected-formal-state-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit_v44(
        args.selection.resolve(),
        args.ledger.resolve(),
        args.events.resolve(),
        args.summary.resolve(),
        args.formal_state.resolve(),
        args.expected_formal_state_sha256,
    )
    v43.durable._atomic_json(args.output.resolve(), result)
    print(json.dumps({
        "output": str(args.output.resolve()),
        "passed": result["passed"],
        "acquisition_complete": result.get("acquisition_complete", False),
        "workload_calls_added": 0,
    }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

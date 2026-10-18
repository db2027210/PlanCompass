#!/usr/bin/env python3
"""Strict postrun entry gate for TPC-C v37/v38 diagnostic Top-5 batches."""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
from typing import Any, Dict, Mapping

try:
    import audit_tpcc_v36_postrun_events as durable
except ModuleNotFoundError:
    from tools import audit_tpcc_v36_postrun_events as durable


RESOURCE_MAE_PP_MAX = {"cpu": 5.0, "io": 5.0, "memory": 2.0}
SCORE_MAE_MAX = 0.05


def audit_v37(
    selection_path: Path,
    ledger_path: Path,
    events_path: Path,
    summary_path: Path,
    formal_state_path: Path,
    expected_formal_sha256: str,
) -> Dict[str, Any]:
    base = durable.audit(
        selection_path,
        ledger_path,
        events_path,
        summary_path,
        formal_state_path,
        expected_formal_sha256,
    )
    selection = durable._load(selection_path)
    ledger = durable._load(ledger_path)
    summary = durable._load(summary_path)
    formal = durable._load(formal_state_path)
    selection_contract = selection.get("selection_contract")
    if selection_contract not in {
        "tpcc_v37_exact_plan_param_drift_lcb_trusted3_probe2_v1",
        "tpcc_v38_fresh_unique_no_retry_trusted3_probe2_v1",
    }:
        raise RuntimeError("postrun input is not a supported v37/v38 selection")
    is_v38 = selection_contract == "tpcc_v38_fresh_unique_no_retry_trusted3_probe2_v1"
    if (
        int(formal.get("accepted_count", -1)) != 0
        or int(formal.get("formal_physical_call_count", -1)) != 0
        or int(formal.get("formal_valid_replay_count", -1)) != 0
        or int(formal.get("formal_technical_failure_count", -1)) != 0
    ):
        raise RuntimeError("TPC-C diagnostic changed the formal fresh0 counters")

    candidates = {int(item["rank"]): item for item in selection.get("top5") or []}
    observations = {int(item["rank"]): item for item in ledger.get("successful") or []}
    if set(candidates) != set(observations) or set(candidates) != {1, 2, 3, 4, 5}:
        raise RuntimeError("TPC-C result rank binding is incomplete")
    for rank in candidates:
        candidate = candidates[rank]
        observation = observations[rank]
        if (
            observation.get("workload_plan_fingerprint")
            != candidate.get("workload_plan_fingerprint")
            or (observation.get("three_level_fingerprint") or {}).get("id")
            != (candidate.get("three_level_fingerprint") or {}).get("id")
            or (observation.get("measurement_protocol") or {}).get("stable") is not True
            or (observation.get("measurement_protocol") or {}).get("correctness_passed") is not True
            or (observation.get("measurement_protocol") or {}).get("technical_failure") is not False
        ):
            raise RuntimeError(f"TPC-C candidate/result evidence mismatch: rank={rank}")

    trusted_ranks = [1, 2, 3]
    resource_errors: Dict[str, list[float]] = {name: [] for name in durable.RESOURCES}
    resource_ratios: Dict[str, list[float]] = {name: [] for name in durable.RESOURCES}
    capabilities = {"cpu": 3.8, "io": 54.0, "memory": 6.0511731087}
    score_errors = []
    for rank in trusted_ranks:
        predicted_score = float(candidates[rank]["predicted_score"])
        actual_score = float(observations[rank]["actual_score"])
        score_errors.append(abs(predicted_score - actual_score))
        for name in durable.RESOURCES:
            predicted = float(candidates[rank]["predicted_resource"][name])
            actual = float(observations[rank]["actual_resource"][name])
            resource_errors[name].append(abs(predicted - actual) / capabilities[name] * 100.0)
            resource_ratios[name].append(actual / predicted if predicted else math.inf)
    score_mae = sum(score_errors) / len(score_errors)
    resource_mae = {
        name: sum(values) / len(values) for name, values in resource_errors.items()
    }
    magnitude_passed = all(
        0.1 <= ratio <= 10.0 for values in resource_ratios.values() for ratio in values
    )
    trusted_spearman = base["quality"]["trusted_spearman"]
    actual_best_rank = int(base["quality"]["actual_best_predicted_rank"])
    trusted_predicted = [float(candidates[rank]["predicted_score"]) for rank in trusted_ranks]
    trusted_actual = [float(observations[rank]["actual_score"]) for rank in trusted_ranks]
    predicted_span = max(trusted_predicted) - min(trusted_predicted)
    actual_span = max(trusted_actual) - min(trusted_actual)
    score_span_passed = bool(predicted_span > 1e-12 and actual_span > 1e-12)
    accounting = base["accounting"]
    state_isolation = base["state_isolation"]
    summary_semantics_passed = bool(
        int(summary.get("successful_real_replay_count", -1)) == 5
        and int(summary.get("technical_failure_count", -1)) == 0
        and int(summary.get("preworkload_failure_count", -1)) == 0
        and int(summary.get("real_workload_invocation_count", -1)) == 5
        and sorted(int(value) for value in summary.get("invoked_ranks") or [])
        == [1, 2, 3, 4, 5]
        and summary.get("fatal_error") is None
    )
    checks = {
        "durable_accounting_5_5_0": bool(
            accounting["passed"] is True
            and accounting["physical_calls"] == 5
            and accounting["valid_replays"] == 5
            and accounting["technical_failures"] == 0
            and accounting["preworkload_failures"] == 0
        ),
        "runner_summary_semantics": summary_semantics_passed,
        "baseline_and_formal_state_isolation": state_isolation["passed"] is True,
        "trusted_spearman_positive": trusted_spearman is not None and trusted_spearman > 0.0,
        "actual_best_in_predicted_top3": actual_best_rank <= 3,
        "trusted_score_mae_within_gate": score_mae <= SCORE_MAE_MAX,
        "trusted_cpu_mae_within_gate": resource_mae["cpu"] <= RESOURCE_MAE_PP_MAX["cpu"],
        "trusted_io_mae_within_gate": resource_mae["io"] <= RESOURCE_MAE_PP_MAX["io"],
        "trusted_memory_mae_within_gate": resource_mae["memory"] <= RESOURCE_MAE_PP_MAX["memory"],
        "trusted_resource_no_order_of_magnitude_error": magnitude_passed,
        "trusted_score_span_resolvable": score_span_passed,
        "five_new_noncorner_fingerprints": bool(
            (
                selection.get(
                    "v38_build_contract" if is_v38 else "v37_build_contract"
                )
                or {}
            ).get("five_new_noncorner_fingerprints") is True
            and base["quality"]["checks"]["five_unique_noncorner_fingerprints"] is True
        ),
        "trusted3_probe2": base["quality"]["checks"]["trusted3_probe2"] is True,
    }
    passed = all(checks.values())
    return {
        "schema_version": (
            "tpcc_v38_strict_postrun_entry_gate_v1"
            if is_v38
            else "tpcc_v37_strict_postrun_entry_gate_v1"
        ),
        "passed": passed,
        "formal_start_authorized": passed,
        "selection_path": str(selection_path.resolve()),
        "selection_sha256": durable._sha256(selection_path),
        "source_diagnostic_selection_sha256": durable._sha256(selection_path),
        "ledger_path": str(ledger_path.resolve()),
        "ledger_sha256": durable._sha256(ledger_path),
        "events_path": str(events_path.resolve()),
        "events_sha256": durable._sha256(events_path),
        "summary_path": str(summary_path.resolve()),
        "summary_sha256": durable._sha256(summary_path),
        "formal_state_path": str(formal_state_path.resolve()),
        "formal_state_sha256": durable._sha256(formal_state_path),
        "quality_gate": {
            "passed": passed,
            "checks": checks,
            "trusted_spearman": trusted_spearman,
            "actual_best_predicted_rank": actual_best_rank,
            "trusted_score_mae": score_mae,
            "trusted_resource_mae_pp": resource_mae,
            "trusted_actual_to_predicted_resource_ratios": resource_ratios,
            "thresholds": {
                "score_mae_max": SCORE_MAE_MAX,
                "resource_mae_pp_max": copy.deepcopy(RESOURCE_MAE_PP_MAX),
                "trusted_spearman_min_exclusive": 0.0,
                "actual_best_predicted_rank_max": 3,
            },
            "probe_rows_excluded_from_ranking_and_error_gates": True,
        },
        "score_span_gate": {
            "passed": score_span_passed,
            "trusted_predicted_score_span": predicted_span,
            "trusted_actual_score_span": actual_span,
        },
        "accounting": copy.deepcopy(accounting),
        "state_isolation": copy.deepcopy(state_isolation),
        "accepted_count_delta": 0,
        "formal_state_mutated": False,
        "qps_role": "outer_audit_only_not_inner_score",
    }


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
    result = audit_v37(
        args.selection.resolve(),
        args.ledger.resolve(),
        args.events.resolve(),
        args.summary.resolve(),
        args.formal_state.resolve(),
        args.expected_formal_sha256,
    )
    durable._atomic_json(args.output.resolve(), result)
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["passed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())

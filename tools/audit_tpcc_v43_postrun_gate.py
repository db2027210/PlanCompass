#!/usr/bin/env python3
"""Audit one completed v42 diagnostic against the fixed formal-entry gate."""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
from typing import Any, Dict

try:
    import audit_tpcc_v36_postrun_events as durable
    import build_tpcc_v42_exact_plan_io_recalibration as v42
except ModuleNotFoundError:
    from tools import audit_tpcc_v36_postrun_events as durable
    from tools import build_tpcc_v42_exact_plan_io_recalibration as v42


RESOURCE_MAE_PP_MAX = {"cpu": 5.0, "io": 5.0, "memory": 2.0}
SCORE_MAE_MAX = 0.05
CAPABILITY = {"cpu": 3.8, "io": 54.0, "memory": 6.0511731087}
TRUSTED_RANKS = (1, 2, 3)


def _false_quality() -> Dict[str, Any]:
    return {
        "trusted_spearman": None,
        "actual_best_predicted_rank": None,
        "trusted_score_mae": math.inf,
        "trusted_resource_mae_pp": {name: math.inf for name in durable.RESOURCES},
        "trusted_actual_to_predicted_resource_ratios": {
            name: [] for name in durable.RESOURCES
        },
        "trusted_predicted_score_span": 0.0,
        "trusted_actual_score_span": 0.0,
    }


def audit_v43(
    selection_path: Path,
    ledger_path: Path,
    events_path: Path,
    summary_path: Path,
    formal_state_path: Path,
    expected_formal_sha256: str,
) -> Dict[str, Any]:
    """Return a fail-closed v43 gate without changing any input."""
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
    is_v43 = bool(selection.get("v43_build_contract"))
    contract = (
        selection.get("v43_build_contract")
        if is_v43
        else selection.get("v42_build_contract")
    ) or {}
    expected_contract = (
        "tpcc_v43_exact_plan_nonstationary_score_resource_recalibration_v1"
        if is_v43
        else v42.BUILD_CONTRACT
    )
    trusted_eligibility_field = "trusted_eligible_v43" if is_v43 else "trusted_eligible_v42"
    candidates = {
        int(item["rank"]): item for item in selection.get("top5") or []
    }
    observations = {
        int(item["rank"]): item for item in ledger.get("successful") or []
    }
    roles = [candidates[rank].get("selection_role") for rank in sorted(candidates)]
    identifiers = [
        str((candidates[rank].get("three_level_fingerprint") or {}).get("id") or "")
        for rank in sorted(candidates)
    ]
    historical = set()
    history_shas_valid = True
    for descriptor in selection.get("source_batches") or []:
        path = Path(str(descriptor.get("selection_path") or "")).resolve()
        if (
            not path.is_file()
            or durable._sha256(path) != descriptor.get("selection_sha256")
        ):
            history_shas_valid = False
            continue
        source = durable._load(path)
        historical.update(
            str((item.get("three_level_fingerprint") or {}).get("id") or "")
            for item in source.get("top5") or []
        )

    identities_match = True
    for rank, observation in observations.items():
        candidate = candidates.get(rank) or {}
        identities_match = bool(
            identities_match
            and observation.get("workload_plan_fingerprint")
            == candidate.get("workload_plan_fingerprint")
            and (observation.get("three_level_fingerprint") or {}).get("id")
            == (candidate.get("three_level_fingerprint") or {}).get("id")
            and (observation.get("measurement_protocol") or {}).get("stable") is True
            and (observation.get("measurement_protocol") or {}).get(
                "correctness_passed"
            )
            is True
            and (observation.get("measurement_protocol") or {}).get(
                "technical_failure"
            )
            is False
        )

    complete = set(candidates) == set(observations) == {1, 2, 3, 4, 5}
    quality = _false_quality()
    magnitude_passed = False
    score_span_passed = False
    if complete and identities_match:
        trusted_predicted = [
            float(candidates[rank]["predicted_score"]) for rank in TRUSTED_RANKS
        ]
        trusted_actual = [
            float(observations[rank]["actual_score"]) for rank in TRUSTED_RANKS
        ]
        resource_errors = {name: [] for name in durable.RESOURCES}
        resource_ratios = {name: [] for name in durable.RESOURCES}
        for rank in TRUSTED_RANKS:
            for name in durable.RESOURCES:
                predicted = float(candidates[rank]["predicted_resource"][name])
                actual = float(observations[rank]["actual_resource"][name])
                resource_errors[name].append(
                    abs(predicted - actual) / CAPABILITY[name] * 100.0
                )
                resource_ratios[name].append(
                    actual / predicted if predicted else math.inf
                )
        resource_mae = {
            name: sum(values) / len(values)
            for name, values in resource_errors.items()
        }
        actual_best = max(
            observations, key=lambda rank: float(observations[rank]["actual_score"])
        )
        predicted_span = max(trusted_predicted) - min(trusted_predicted)
        actual_span = max(trusted_actual) - min(trusted_actual)
        quality = {
            "trusted_spearman": durable._spearman(
                trusted_predicted, trusted_actual
            ),
            "actual_best_predicted_rank": int(actual_best),
            "trusted_score_mae": sum(
                abs(predicted - actual)
                for predicted, actual in zip(trusted_predicted, trusted_actual)
            )
            / len(TRUSTED_RANKS),
            "trusted_resource_mae_pp": resource_mae,
            "trusted_actual_to_predicted_resource_ratios": resource_ratios,
            "trusted_predicted_score_span": predicted_span,
            "trusted_actual_score_span": actual_span,
        }
        magnitude_passed = all(
            0.1 <= ratio <= 10.0
            for values in resource_ratios.values()
            for ratio in values
        )
        score_span_passed = predicted_span > 1e-12 and actual_span > 1e-12

    accounting = base["accounting"]
    state_isolation = base["state_isolation"]
    summary_clean = bool(
        int(summary.get("real_workload_invocation_count", -1)) == 5
        and int(summary.get("successful_real_replay_count", -1)) == 5
        and int(summary.get("technical_failure_count", -1)) == 0
        and int(summary.get("preworkload_failure_count", -1)) == 0
        and sorted(int(value) for value in summary.get("invoked_ranks") or [])
        == [1, 2, 3, 4, 5]
        and summary.get("fatal_error") is None
    )
    fixed_thresholds = bool(
        contract.get("threshold_changed") is False
        and float(contract.get("score_mae_max", -1)) == SCORE_MAE_MAX
        and contract.get("resource_mae_pp_max") == RESOURCE_MAE_PP_MAX
    )
    required_fields = {
        "feature_ratio",
        "predicted_resource",
        "utilization",
        "dScore/dU",
        "T",
        "confidence",
        "g_j",
        "eta",
        "delta_param",
        "candidate_score",
    }
    algorithm_checks = {
        "selection_algorithm_contract": contract.get("contract") == expected_contract,
        "fixed_thresholds_unchanged": fixed_thresholds,
        "five_historically_novel_noncorner_fingerprints": bool(
            history_shas_valid
            and len(identifiers) == 5
            and "" not in identifiers
            and len(set(identifiers)) == 5
            and not (set(identifiers) & historical)
            and all(item.get("boundary_corner") is False for item in candidates.values())
            and all(not (required_fields - set(item)) for item in candidates.values())
        ),
        "trusted3_probe2": roles
        == ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"],
        "probe_state_isolation": all(
            candidates[rank].get("incumbent_update_eligible") is False
            and candidates[rank].get("used_for_gradient") is False
            and candidates[rank].get("used_for_inner_incumbent_update") is False
            and candidates[rank].get(trusted_eligibility_field) is False
            for rank in (4, 5)
        ),
        "selection_state_neutral": selection.get("state_mutation_contract")
        == {
            "accepted_update": False,
            "gradient_update": False,
            "incumbent_update": False,
            "range_update": False,
        },
        "no_manual_selection": contract.get("manual_candidate_selection") is False,
    }
    checks = {
        "durable_accounting_clean5": bool(
            accounting["passed"] is True
            and accounting["physical_calls"] == 5
            and accounting["valid_replays"] == 5
            and accounting["technical_failures"] == 0
            and accounting["preworkload_failures"] == 0
            and summary_clean
            and complete
            and identities_match
        ),
        "baseline_restored_and_state_neutral": bool(
            state_isolation["passed"] is True
            and durable._sha256(formal_state_path) == expected_formal_sha256
            and int(formal.get("accepted_count", -1)) == 0
            and int(formal.get("formal_physical_call_count", -1)) == 0
            and int(formal.get("formal_valid_replay_count", -1)) == 0
            and int(formal.get("formal_technical_failure_count", -1)) == 0
        ),
        "trusted_spearman_positive": quality["trusted_spearman"] is not None
        and quality["trusted_spearman"] > 0.0,
        "actual_best_in_predicted_top3": quality["actual_best_predicted_rank"]
        is not None
        and quality["actual_best_predicted_rank"] <= 3,
        "trusted_score_mae_within_gate": quality["trusted_score_mae"]
        <= SCORE_MAE_MAX,
        "trusted_cpu_mae_within_gate": quality["trusted_resource_mae_pp"]["cpu"]
        <= RESOURCE_MAE_PP_MAX["cpu"],
        "trusted_io_mae_within_gate": quality["trusted_resource_mae_pp"]["io"]
        <= RESOURCE_MAE_PP_MAX["io"],
        "trusted_memory_mae_within_gate": quality["trusted_resource_mae_pp"]["memory"]
        <= RESOURCE_MAE_PP_MAX["memory"],
        "trusted_resource_no_order_of_magnitude_error": magnitude_passed,
        "trusted_score_span_resolvable": score_span_passed,
        "selection_algorithm_gate": all(algorithm_checks.values()),
    }
    passed = all(checks.values())
    return {
        "schema_version": "tpcc_v43_v42_compatible_strict_postrun_gate_v1",
        "passed": passed,
        "formal_start_authorized": passed,
        "selection_path": str(selection_path.resolve()),
        "selection_sha256": durable._sha256(selection_path),
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
            **copy.deepcopy(quality),
            "thresholds": {
                "score_mae_max": SCORE_MAE_MAX,
                "resource_mae_pp_max": copy.deepcopy(RESOURCE_MAE_PP_MAX),
                "trusted_spearman_min_exclusive": 0.0,
                "actual_best_predicted_rank_max": 3,
            },
            "probe_rows_excluded_from_all_quality_metrics": True,
        },
        "selection_algorithm_gate": {
            "passed": all(algorithm_checks.values()),
            "checks": algorithm_checks,
            "contract": copy.deepcopy(contract),
        },
        "score_span_gate": {
            "passed": score_span_passed,
            "trusted_predicted_score_span": quality["trusted_predicted_score_span"],
            "trusted_actual_score_span": quality["trusted_actual_score_span"],
        },
        "accounting": copy.deepcopy(accounting),
        "state_isolation": copy.deepcopy(state_isolation),
        "accepted_count_delta": 0,
        "formal_state_mutated": False,
        "qps_role": "outer_audit_only_not_inner_score",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("selection", "ledger", "events", "summary"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--formal-state", type=Path, required=True)
    parser.add_argument("--expected-formal-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit_v43(
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

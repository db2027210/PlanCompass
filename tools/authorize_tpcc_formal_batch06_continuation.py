#!/usr/bin/env python3
"""Authorize TPC-C formal batch 06 against immutable batch 05 evidence."""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys
from typing import Any, Dict, List, Set

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tools.authorize_tpcc_formal_batch04_continuation as common
import tools.build_tpcc_round14_robust_regime_top5 as robust


ENTRY_GATE_SHA = common.ENTRY_GATE_SHA
FORMAL_STATE_SHA = "4a273208d8503f2d18a108293bf5714043418bdf72766bb44e8e10f16e5f9d5f"
BATCH05_TOP5_SHA = "f39d2b4d404897a1f976c9f457a1fbb06c807d7f0eeee5e00040262ade860b6a"
BATCH05_LEDGER_SHA = "b77c02279567dfa12fead8dadfb2558cc7d4efd5e9d41e0c6f74f1ca7a43f8d2"
BATCH05_SUMMARY_SHA = "fac96d49db5e88104db8e6057ad4c993d3530d0da589a105984f5255d3541925"
REQUIRED_AUDIT_FIELDS = common.REQUIRED_AUDIT_FIELDS


def _spearman_without_ties(predicted: List[float], actual: List[float]) -> float:
    if len(predicted) != len(actual) or len(predicted) < 2:
        raise RuntimeError("trusted Spearman requires at least two paired points")

    def ranks(values: List[float]) -> List[int]:
        if len(set(values)) != len(values):
            raise RuntimeError("trusted Spearman input contains ties")
        order = sorted(range(len(values)), key=lambda index: -values[index])
        result = [0] * len(values)
        for rank, index in enumerate(order, 1):
            result[index] = rank
        return result

    predicted_ranks = ranks(predicted)
    actual_ranks = ranks(actual)
    count = len(predicted)
    squared = sum(
        (predicted_ranks[index] - actual_ranks[index]) ** 2
        for index in range(count)
    )
    return 1.0 - (6.0 * squared) / (count * (count * count - 1))


def historical_evidence(selection: Dict[str, Any]) -> tuple[Set[str], Set[str]]:
    ids: Set[str] = set()
    params: Set[str] = set()
    source_batches = selection.get("source_batches")
    if not isinstance(source_batches, list) or not source_batches:
        raise RuntimeError("selection has no immutable source batch manifest")
    names = set()
    for source in source_batches:
        name = str(source.get("name") or "")
        selection_path = Path(str(source.get("selection_path") or "")).resolve()
        ledger_path = Path(str(source.get("ledger_path") or "")).resolve()
        if (
            not name
            or not selection_path.is_file()
            or not ledger_path.is_file()
            or common.sha256(selection_path) != source.get("selection_sha256")
            or common.sha256(ledger_path) != source.get("ledger_sha256")
        ):
            raise RuntimeError(f"source batch manifest mismatch: {name}")
        names.add(name)
        historical = common.load(selection_path)
        for item in historical.get("top5", []):
            ids.add(item["three_level_fingerprint"]["id"])
            params.add(robust.parameter_key(item["optimized_params"]))
    required = {
        "formal_batch01",
        "formal_batch02",
        "formal_batch03",
        "formal_batch04",
        "formal_batch05",
    }
    if not required.issubset(names):
        raise RuntimeError("one or more formal batches are missing from history")
    return ids, params


def main() -> int:
    if len(sys.argv) != 6:
        raise SystemExit(
            "usage: authorize_tpcc_formal_batch06_continuation.py "
            "ENTRY_GATE FORMAL_STATE BATCH05_ROOT NEW_TOP5 OUTPUT_GATE"
        )
    entry_path, state_path, batch05_root, selection_path, output_path = map(
        lambda item: Path(item).resolve(), sys.argv[1:]
    )
    batch05_top5 = batch05_root / "top5.json"
    batch05_ledger = (
        batch05_root / "strict_top5_real_replay_round14_ledger_v1.json"
    )
    batch05_summary = batch05_root / "formal_batch_summary.json"
    for path, expected, label in (
        (entry_path, ENTRY_GATE_SHA, "original entry gate"),
        (state_path, FORMAL_STATE_SHA, "formal state"),
        (batch05_top5, BATCH05_TOP5_SHA, "batch05 Top-5"),
        (batch05_ledger, BATCH05_LEDGER_SHA, "batch05 ledger"),
        (batch05_summary, BATCH05_SUMMARY_SHA, "batch05 summary"),
    ):
        if not path.is_file() or common.sha256(path) != expected:
            raise RuntimeError(f"{label} SHA or path mismatch: {path}")

    entry = common.load(entry_path)
    if (
        entry.get("passed") is not True
        or entry.get("formal_start_authorized") is not True
    ):
        raise RuntimeError("original entry gate no longer authorizes formal search")
    state = common.load(state_path)
    if (
        state.get("status") != "ready"
        or state.get("accepted_count") != 1
        or state.get("accepted_target") != 10
        or state.get("formal_physical_call_count") != 25
        or state.get("formal_valid_replay_count") != 25
        or state.get("formal_technical_failure_count") != 0
        or state.get("qps_used_for_inner_scoring") is not False
        or Path(str(state.get("last_batch_root") or "")).resolve() != batch05_root
    ):
        raise RuntimeError("formal state is not the clean post-batch05 state")
    summary = common.load(batch05_summary)
    if (
        summary.get("status") != "complete"
        or summary.get("physical_call_count") != 5
        or summary.get("valid_replay_count") != 5
        or summary.get("technical_failure_count") != 0
        or summary.get("accepted_before") != 1
        or summary.get("accepted_after") != 1
        or summary.get("batch_added_accepted") != 0
        or summary.get("baseline_restored") is not True
        or summary.get("gradient_update_applied") is not False
        or summary.get("incumbent_update_applied") is not False
        or summary.get("range_update_applied") is not False
        or summary.get("formal_state_sha256") != FORMAL_STATE_SHA
    ):
        raise RuntimeError("batch05 summary violates continuation contract")
    ledger = common.load(batch05_ledger)
    successful = ledger.get("successful") or []
    if (
        ledger.get("real_workload_invocation_count") != 5
        or ledger.get("technical_failures") != []
        or len(successful) != 5
    ):
        raise RuntimeError("batch05 ledger is not clean 5/5 evidence")
    previous_selection = common.load(batch05_top5)
    selected_by_rank = {
        int(item["rank"]): item for item in previous_selection.get("top5", [])
    }
    trusted_results = [
        item
        for item in successful
        if selected_by_rank[int(item["rank"])].get("selection_role") == "trusted"
    ]
    if len(trusted_results) != 3:
        raise RuntimeError("batch05 does not contain exactly three trusted results")
    trusted_predicted = [float(item["predicted_score"]) for item in trusted_results]
    trusted_actual = [float(item["actual_score"]) for item in trusted_results]
    trusted_spearman = _spearman_without_ties(
        trusted_predicted,
        trusted_actual,
    )
    if trusted_spearman <= 0.0:
        raise RuntimeError("batch05 trusted-only Spearman is not positive")
    actual_best_rank = int(
        max(successful, key=lambda item: float(item["actual_score"]))["rank"]
    )
    for result in trusted_results:
        for resource_name in ("cpu", "io", "memory"):
            predicted = float(result["predicted_resource"][resource_name])
            actual = float(result["actual_resource"][resource_name])
            ratio = predicted / actual
            if not math.isfinite(ratio) or not 0.1 <= ratio <= 10.0:
                raise RuntimeError(
                    f"batch05 trusted {resource_name} magnitude mismatch"
                )

    selection = common.load(selection_path)
    if selection.get("selection_contract") != (
        "tpcc_robust_exact_plan_regime_trusted3_probe2_v1"
    ):
        raise RuntimeError("batch06 selection contract mismatch")
    range_policy = selection.get("candidate_range_policy")
    state_preserving_exploration = (
        range_policy == "state_preserving_exploration_domain_v1"
    )
    if state_preserving_exploration:
        if (
            Path(
                str(selection.get("formal_state_audit_source") or "")
            ).resolve()
            != state_path
            or selection.get("formal_state_audit_source_sha256")
            != FORMAL_STATE_SHA
            or selection.get("formal_range_state_at_generation")
            != state.get("range_state")
            or selection.get("formal_range_state_source") is not None
            or selection.get("formal_range_state_source_sha256") is not None
        ):
            raise RuntimeError(
                "batch06 exploration did not pin the immutable formal state"
            )
    elif (
        Path(
            str(selection.get("formal_range_state_source") or "")
        ).resolve()
        != state_path
        or selection.get("formal_range_state_source_sha256")
        != FORMAL_STATE_SHA
        or selection.get("candidate_param_ranges") != state.get("range_state")
    ):
        raise RuntimeError("batch06 candidate range is not pinned to formal state")
    top5 = selection.get("top5") or []
    roles = [item.get("selection_role") for item in top5]
    if roles != [
        "trusted",
        "trusted",
        "trusted",
        "uncertain_probe",
        "uncertain_probe",
    ]:
        raise RuntimeError("batch06 selection role contract mismatch")
    historical_ids, historical_params = historical_evidence(selection)
    new_ids = [item["three_level_fingerprint"]["id"] for item in top5]
    new_params = [robust.parameter_key(item["optimized_params"]) for item in top5]
    if (
        len(set(new_ids)) != 5
        or historical_ids.intersection(new_ids)
        or len(set(new_params)) != 5
        or historical_params.intersection(new_params)
    ):
        raise RuntimeError("batch06 candidates are not five distinct novel points")
    for item in top5:
        params = item.get("optimized_params") or {}
        in_formal_range = all(
            float(bounds["min_value"]) < float(params[name])
            < float(bounds["max_value"])
            for name, bounds in state["range_state"].items()
        )
        in_candidate_range = all(
            float(bounds["min_value"]) < float(params[name])
            < float(bounds["max_value"])
            for name, bounds in selection["candidate_param_ranges"].items()
        )
        if (
            not in_candidate_range
            or (
                not state_preserving_exploration
                and not in_formal_range
            )
            or (
                state_preserving_exploration
                and item.get("inside_formal_range_at_generation")
                != in_formal_range
            )
            or item.get("boundary_corner") is not False
            or item.get("qps_used_for_inner_scoring") is not False
            or item.get("diagnostic_only") is not True
            or item.get("incumbent_update_eligible") is not False
            or any(
                field not in item or item[field] is None
                for field in REQUIRED_AUDIT_FIELDS
            )
            or item.get("three_level_fingerprint", {}).get("valid") is not True
        ):
            raise RuntimeError(
                f"batch06 candidate contract failure: rank={item.get('rank')}"
            )
        calibration = item.get("plan_regime_calibration") or {}
        if item.get("selection_role") == "trusted":
            regime = calibration.get("regime") or {}
            if (
                item.get("trusted") is not True
                or item.get("exact_plan_regime_validated") is not True
                or calibration.get("mode")
                != "exact_regime_componentwise_median_with_interval"
                or calibration.get("trusted_eligible") is not True
                or regime.get("plan") != item.get("workload_plan_fingerprint")
            ):
                raise RuntimeError(
                    f"trusted rank lacks exact regime: {item.get('rank')}"
                )
        elif (
            item.get("trusted") is not False
            or item.get("exact_plan_regime_validated") not in {False, True}
            or (
                item.get("exact_plan_regime_validated") is False
                and (
                    calibration.get("mode")
                    != "unknown_plan_regime_probe_only"
                    or calibration.get("regime") is not None
                    or calibration.get("trusted_eligible") is not False
                )
            )
            or (
                item.get("exact_plan_regime_validated") is True
                and (
                    item.get("probe_kind")
                    != "sparse_exact_plan_regime_probe"
                    or not str(item.get("probe_reason") or "").startswith(
                        "exact_plan_regime_has_only_"
                    )
                    or calibration.get("mode")
                    != "exact_regime_componentwise_median_with_interval"
                    or not isinstance(calibration.get("regime"), dict)
                    or int(
                        calibration["regime"].get("observation_count") or 0
                    )
                    > 1
                )
            )
        ):
            raise RuntimeError(
                f"probe uncertainty contract failure: rank={item.get('rank')}"
            )

    regimes = selection.get("robust_regimes") or {}
    for result in successful:
        plan = str(result["workload_plan_fingerprint"])
        evidence = (regimes.get(plan) or {}).get("evidence") or []
        matching = [
            item
            for item in evidence
            if item.get("source") == "formal_batch05"
            and int(item.get("rank") or 0) == int(result["rank"])
            and item.get("actual_resource") == result.get("actual_resource")
            and math.isclose(
                float(item.get("actual_score")),
                float(result["actual_score"]),
                rel_tol=0.0,
                abs_tol=1e-12,
            )
        ]
        if len(matching) != 1:
            raise RuntimeError(
                f"batch05 residual is absent from exact regime: rank={result['rank']}"
            )

    diagnostic_evidence = [
        item
        for regime in regimes.values()
        for item in (regime.get("evidence") or [])
        if item.get("source") == "diagnostic_minimal_probe2"
    ]
    if len(diagnostic_evidence) != 2:
        raise RuntimeError(
            "minimal probe2 residuals are absent from exact plan regimes"
        )

    output = {
        "schema_version": "tpcc_formal_continuation_gate_v1",
        "passed": True,
        "formal_start_authorized": True,
        "authorization_kind": (
            "formal_continuation_after_clean_batch05_and_probe2_calibration"
        ),
        "original_entry_gate_path": str(entry_path),
        "original_entry_gate_sha256": ENTRY_GATE_SHA,
        "formal_state_path": str(state_path),
        "formal_state_sha256": FORMAL_STATE_SHA,
        "previous_batch_root": str(batch05_root),
        "previous_batch_summary_sha256": BATCH05_SUMMARY_SHA,
        "previous_batch_ledger_sha256": BATCH05_LEDGER_SHA,
        "accepted_count_before": 1,
        "accepted_target": 10,
        "selection_path": str(selection_path),
        "selection_sha256": common.sha256(selection_path),
        "batch05_trusted_only_spearman": trusted_spearman,
        "batch05_actual_best_predicted_rank": actual_best_rank,
        "candidate_range_policy": range_policy,
        "candidate_count_inside_formal_range": sum(
            1
            for item in top5
            if item.get("inside_formal_range_at_generation") is True
        ),
        "candidate_count_outside_formal_range": sum(
            1
            for item in top5
            if item.get("inside_formal_range_at_generation") is False
        ),
        "checks": {
            "original_entry_gate_passed": True,
            "formal_state_ready_and_sha_pinned": True,
            "batch05_clean_5_of_5": True,
            "batch05_no_formal_state_mutation": True,
            "baseline_restored": True,
            "batch05_trusted_only_spearman_positive": True,
            "batch05_actual_best_rank_recorded_for_residual_update": actual_best_rank,
            "batch05_trusted_resource_magnitude_sane": True,
            "batch05_residual_in_exact_plan_regimes": True,
            "minimal_probe2_clean_2_of_2": True,
            "minimal_probe2_residual_in_exact_plan_regimes": True,
            "formal_state_and_range_sha_pinned_without_diagnostic_mutation": True,
            "all_candidates_strictly_inside_declared_candidate_range": True,
            "five_new_distinct_noncorner_fingerprints": True,
            "five_new_distinct_parameter_points": True,
            "trusted3_probe2": True,
            "trusted_exact_plan_regime_only": True,
            "uncertain_probe_is_unknown_or_sparse_exact_regime_only": True,
            "candidate_audit_fields_complete": True,
            "all_source_batches_sha_pinned": True,
            "qps_outer_only": True,
            "same_scenario_real_replay_serial": True,
            "maximum_physical_calls": 5,
        },
    }
    if output_path.exists():
        raise FileExistsError(f"output already exists: {output_path}")
    common.atomic_json(output_path, output)
    print(
        json.dumps(
            {"gate": str(output_path), "sha256": common.sha256(output_path)}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""TPC-C target-10 controller with score-contributing stability admission.

The frozen point runner intentionally stops when its configured target resource
is stable.  TPC-C inner scoring, however, depends on CPU, IO and memory.  This
successor therefore wraps the existing controller and reclassifies every
successful replay after the runner has produced its sealed summary:

* CPU, IO and memory receive separate window-mean stability and 95% CIs;
* an unstable scoring dimension remains a valid physical replay, but is
  probe-only and ineligible for calibration;
* trusted admission requires all three dimensions to be stable and the trusted
  score confidence intervals to be strictly separated in predicted order;
* instability is an algorithmic rejection, never a technical failure.

No frozen runner, replay implementation, workload invocation, or formal-state
mutation code is changed here.
"""

from __future__ import annotations

import copy
import itertools
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping, Sequence

try:
    import tpcc_autonomous_target10_controller_v25 as v25
    import tpcc_autonomous_target10_controller_v28_role_aware as v28
    import tpcc_autonomous_target10_controller_v29_prelaunch_fingerprint as v29
except ModuleNotFoundError:
    from tools import tpcc_autonomous_target10_controller_v25 as v25
    from tools import tpcc_autonomous_target10_controller_v28_role_aware as v28
    from tools import tpcc_autonomous_target10_controller_v29_prelaunch_fingerprint as v29


_BASE_AUDIT_COMPLETED_RUN = v25.audit_completed_run
_BASE_QUALITY_GATE = v25.quality_gate
_BASE_BUILD_EXACT_CALIBRATION = v25.build_exact_calibration
_T_CRITICAL_95 = {
    1: 12.706,
    2: 4.303,
    3: 3.182,
    4: 2.776,
    5: 2.571,
    6: 2.447,
    7: 2.365,
    8: 2.306,
    9: 2.262,
    10: 2.228,
}


def _finite(value: Any, label: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise RuntimeError(f"non-finite {label}: {value!r}")
    return number


def _latest_cross_window_group(summary: Mapping[str, Any]) -> Mapping[str, Any]:
    assessment = summary.get("measurement_assessment") or {}
    groups = [
        item
        for item in assessment.get("cross_window_resource_checks") or []
        if isinstance(item, Mapping)
    ]
    if not groups:
        raise RuntimeError("score stability evidence has no cross-window group")
    return max(groups, key=lambda item: int(item.get("end_elapsed_seconds") or -1))


def _student_t_critical_95(sample_count: int) -> float:
    degrees_of_freedom = sample_count - 1
    if degrees_of_freedom <= 0:
        raise RuntimeError("score stability CI requires at least two windows")
    if degrees_of_freedom in _T_CRITICAL_95:
        return _T_CRITICAL_95[degrees_of_freedom]
    return 1.96 if degrees_of_freedom >= 30 else 2.086


def score_contributing_resource_assessment(
    summary: Mapping[str, Any],
) -> Dict[str, Any]:
    """Return deterministic all-resource stability and confidence intervals."""
    group = _latest_cross_window_group(summary)
    by_field = {
        str(item.get("field") or ""): item
        for item in group.get("fields") or []
        if isinstance(item, Mapping)
    }
    resources: Dict[str, Any] = {}
    for name in v25.RESOURCES:
        field_name = f"{name}_utilization"
        field = by_field.get(field_name)
        if field is None:
            raise RuntimeError(f"score stability evidence missing {field_name}")
        window_means = [
            _finite(value, f"{field_name}.window_mean")
            for value in field.get("window_means") or []
        ]
        if len(window_means) < 2:
            raise RuntimeError(
                f"score stability evidence has too few windows: {field_name}"
            )
        mean = statistics.fmean(window_means)
        sample_std = statistics.stdev(window_means)
        half_width = (
            _student_t_critical_95(len(window_means))
            * sample_std
            / math.sqrt(len(window_means))
        )
        utilization_lower = max(0.0, mean - half_width)
        utilization_upper = mean + half_width
        coefficient_of_variation = _finite(
            field.get("coefficient_of_variation"),
            f"{field_name}.coefficient_of_variation",
        )
        limit = _finite(field.get("limit"), f"{field_name}.limit")
        stable = bool(
            field.get("stable") is True
            and coefficient_of_variation <= limit + 1e-12
        )
        capability = float(v25.TPCC_CAPABILITY[name])
        resources[name] = {
            "field": field_name,
            "window_count": len(window_means),
            "window_means": window_means,
            "mean_utilization": mean,
            "sample_standard_deviation": sample_std,
            "coefficient_of_variation": coefficient_of_variation,
            "coefficient_of_variation_limit": limit,
            "stable": stable,
            "confidence_interval_95": {
                "contract": "student_t_window_mean_ci_v1",
                "lower_utilization": utilization_lower,
                "center_utilization": mean,
                "upper_utilization": utilization_upper,
                "lower_resource": utilization_lower * capability,
                "center_resource": mean * capability,
                "upper_resource": utilization_upper * capability,
            },
        }
    coverage = _finite(
        group.get("cumulative_coverage"),
        "cross_window_resource_checks.cumulative_coverage",
    )
    coverage_passed = coverage >= 0.95
    return {
        "contract": "tpcc_all_score_resources_window_stability_ci_v1",
        "score_contributing_resources": list(v25.RESOURCES),
        "end_elapsed_seconds": int(group.get("end_elapsed_seconds") or 0),
        "cumulative_coverage": coverage,
        "coverage_passed": coverage_passed,
        "resources": resources,
        "all_resources_stable": bool(
            coverage_passed
            and all(resources[name]["stable"] for name in v25.RESOURCES)
        ),
    }


def _score_interval(
    resource_assessment: Mapping[str, Any],
) -> Dict[str, Any]:
    values_by_resource: Dict[str, list[float]] = {}
    for name in v25.RESOURCES:
        confidence_interval = resource_assessment["resources"][name][
            "confidence_interval_95"
        ]
        lower = float(confidence_interval["lower_resource"])
        center = float(confidence_interval["center_resource"])
        upper = float(confidence_interval["upper_resource"])
        if not 0.0 <= lower <= center <= upper:
            raise RuntimeError(f"invalid measured resource CI: {name}")
        values = {lower, center, upper}
        normalized = v25.TPCC_SCORE._normalize_resource_name(name)
        target = v25.TPCC_SCORE.resource_target_ranges[normalized]
        for key in ("min", "max", "hard"):
            breakpoint = float(target[key]) * float(v25.TPCC_CAPABILITY[name])
            if lower <= breakpoint <= upper:
                values.add(breakpoint)
        values_by_resource[name] = sorted(values)

    evaluated = []
    for combination in itertools.product(
        *(values_by_resource[name] for name in v25.RESOURCES)
    ):
        resource = {
            name: float(combination[index])
            for index, name in enumerate(v25.RESOURCES)
        }
        evaluated.append((v25.tpcc_score_resource(resource), resource))
    lower_score, lower_resource = min(evaluated, key=lambda item: item[0])
    upper_score, upper_resource = max(evaluated, key=lambda item: item[0])
    center_resource = {
        name: float(
            resource_assessment["resources"][name]["confidence_interval_95"][
                "center_resource"
            ]
        )
        for name in v25.RESOURCES
    }
    return {
        "contract": "tpcc_score_ci_from_resource_window_ci_v1",
        "lower": float(lower_score),
        "center": v25.tpcc_score_resource(center_resource),
        "upper": float(upper_score),
        "lower_witness_resource": lower_resource,
        "upper_witness_resource": upper_resource,
        "evaluated_point_count": len(evaluated),
    }


def _load_result_summary(result: Mapping[str, Any]) -> Dict[str, Any]:
    protocol = result.get("measurement_protocol") or {}
    summary_path = Path(str(protocol.get("validated_summary_path") or "")).resolve()
    expected_sha256 = str(protocol.get("validated_summary_sha256") or "")
    if (
        not summary_path.is_file()
        or not expected_sha256
        or v25.sha256(summary_path) != expected_sha256
    ):
        raise RuntimeError(f"score stability summary SHA mismatch: {summary_path}")
    return v25.load_json(summary_path)


def apply_score_stability_audit(
    base_audit: Mapping[str, Any],
    selection: Mapping[str, Any],
    ledger: Mapping[str, Any],
) -> Dict[str, Any]:
    """Add v30 classification without changing physical replay accounting."""
    audit = copy.deepcopy(dict(base_audit))
    selected = {int(item["rank"]): item for item in selection.get("top5") or []}
    successful = ledger.get("successful") or []
    assessments: list[Dict[str, Any]] = []
    eligible_trusted_pairs = []
    eligible_calibration_ranks = set()
    raw_trusted_spearman = audit.get("trusted_spearman")

    for result in successful:
        rank = int(result["rank"])
        candidate = selected[rank]
        resource_assessment = score_contributing_resource_assessment(
            _load_result_summary(result)
        )
        score_interval = _score_interval(resource_assessment)
        all_stable = resource_assessment["all_resources_stable"] is True
        trusted_role = candidate.get("selection_role") == "trusted"
        calibration_eligible = bool(all_stable)
        if calibration_eligible:
            eligible_calibration_ranks.add(rank)
        if trusted_role and all_stable:
            eligible_trusted_pairs.append(
                (float(result["predicted_score"]), float(result["actual_score"]))
            )
        unstable_resources = [
            name
            for name in v25.RESOURCES
            if resource_assessment["resources"][name]["stable"] is not True
        ]
        assessments.append(
            {
                "rank": rank,
                "selection_role": candidate.get("selection_role"),
                "three_level_fingerprint": (
                    candidate.get("three_level_fingerprint") or {}
                ).get("id"),
                "valid_replay": True,
                "technical_failure": False,
                "resource_stability": resource_assessment,
                "score_confidence_interval_95": score_interval,
                "all_score_contributing_resources_stable": all_stable,
                "unstable_score_contributing_resources": unstable_resources,
                "ranking_eligible": bool(trusted_role and all_stable),
                "calibration_eligible": calibration_eligible,
                "effective_role": (
                    candidate.get("selection_role")
                    if all_stable
                    else "uncertain_probe"
                ),
                "classification": (
                    "trusted_score_stable"
                    if trusted_role and all_stable
                    else (
                        "valid_rejected_probe_only_unstable_score_resource"
                        if not all_stable
                        else "stable_probe_only"
                    )
                ),
            }
        )

    by_rank = {int(item["rank"]): item for item in assessments}
    trusted_candidates = [
        item
        for item in selection.get("top5") or []
        if item.get("selection_role") == "trusted"
    ]
    trusted_candidates.sort(key=lambda item: float(item["predicted_score"]), reverse=True)
    trusted_intervals = [
        {
            "rank": int(candidate["rank"]),
            **copy.deepcopy(
                by_rank[int(candidate["rank"])]["score_confidence_interval_95"]
            ),
        }
        for candidate in trusted_candidates
        if int(candidate["rank"]) in by_rank
    ]
    all_trusted_stable = bool(
        len(trusted_candidates) == 3
        and len(trusted_intervals) == 3
        and all(
            by_rank[int(candidate["rank"])][
                "all_score_contributing_resources_stable"
            ]
            is True
            for candidate in trusted_candidates
        )
    )
    separation_pairs = []
    if len(trusted_intervals) == 3:
        for better, worse in zip(trusted_intervals, trusted_intervals[1:]):
            separated = float(better["lower"]) > float(worse["upper"]) + 1e-12
            separation_pairs.append(
                {
                    "predicted_better_rank": int(better["rank"]),
                    "predicted_worse_rank": int(worse["rank"]),
                    "better_lower": float(better["lower"]),
                    "worse_upper": float(worse["upper"]),
                    "separated": separated,
                }
            )
    intervals_separable = bool(
        all_trusted_stable
        and len(separation_pairs) == 2
        and all(item["separated"] for item in separation_pairs)
    )

    calibration_rows = []
    excluded_rows = []
    for row in audit.get("calibration_rows") or []:
        enriched = copy.deepcopy(row)
        rank = int(row["rank"])
        assessment = by_rank.get(rank)
        eligible = bool(rank in eligible_calibration_ranks and assessment is not None)
        enriched["calibration_eligible"] = eligible
        enriched["score_stability_contract"] = (
            "tpcc_all_score_resources_window_stability_ci_v1"
        )
        if eligible:
            calibration_rows.append(enriched)
        else:
            enriched["calibration_exclusion_reason"] = (
                "unstable_score_contributing_resource"
                if assessment is not None
                and assessment["all_score_contributing_resources_stable"] is not True
                else "missing_score_stability_evidence"
            )
            excluded_rows.append(enriched)

    audit.update(
        {
            "score_stability_contract": (
                "tpcc_all_score_resources_and_separable_score_ci_v30"
            ),
            "score_stability_assessments": assessments,
            "raw_trusted_spearman_before_score_stability": raw_trusted_spearman,
            "trusted_pairs": eligible_trusted_pairs,
            "trusted_spearman": (
                v25.spearman(
                    [item[0] for item in eligible_trusted_pairs],
                    [item[1] for item in eligible_trusted_pairs],
                )
                if len(eligible_trusted_pairs) == 3
                else None
            ),
            "selected_trusted_count": len(trusted_candidates),
            "stable_trusted_count": len(eligible_trusted_pairs),
            "all_trusted_score_resources_stable": all_trusted_stable,
            "trusted_score_confidence_intervals": trusted_intervals,
            "trusted_score_confidence_interval_separation": separation_pairs,
            "trusted_score_confidence_intervals_separable": intervals_separable,
            "calibration_rows": calibration_rows,
            "calibration_excluded_rows": excluded_rows,
            "calibration_eligible_replay_count": len(calibration_rows),
            "valid_rejected_probe_only_count": sum(
                item["classification"]
                == "valid_rejected_probe_only_unstable_score_resource"
                for item in assessments
            ),
            "score_stability_algorithmic_rejection": bool(
                any(
                    item["all_score_contributing_resources_stable"] is not True
                    for item in assessments
                )
                or not intervals_separable
            ),
            "score_stability_rejection_counts_as_technical_failure": False,
            "formal_admission_blocked_by_score_stability": bool(
                audit.get("mode") == "diagnostic_validation"
                and (not all_trusted_stable or not intervals_separable)
            ),
        }
    )
    audit["gradient_reversal_detected"] = bool(
        audit["trusted_spearman"] is not None
        and float(audit["trusted_spearman"]) <= 0.0
    )
    return audit


def audit_completed_run_v30(
    run: Mapping[str, Any],
    formal_state: Path,
    *,
    verify_current_state_sha: bool = True,
) -> Dict[str, Any]:
    audit = _BASE_AUDIT_COMPLETED_RUN(
        run,
        formal_state,
        verify_current_state_sha=verify_current_state_sha,
    )
    if audit.get("mode") == "formal":
        return audit
    selection = v25.load_json(Path(audit["selection_path"]))
    ledger = v25.load_json(Path(audit["ledger_path"]))
    return apply_score_stability_audit(audit, selection, ledger)


def _retrofit_latest_validation(audits: Sequence[Mapping[str, Any]]) -> None:
    """Upgrade a v29-completed validation at controller handover, zero-call."""
    latest = next(
        (
            item
            for item in reversed(audits)
            if item.get("mode") == "diagnostic_validation"
        ),
        None,
    )
    if latest is None or latest.get("score_stability_contract") is not None:
        return
    if not isinstance(latest, MutableMapping):
        raise RuntimeError("latest validation audit is not mutable")
    selection_path = Path(str(latest["selection_path"])).resolve()
    ledger_path = Path(str(latest["ledger_path"])).resolve()
    if (
        not selection_path.is_file()
        or v25.sha256(selection_path) != latest["selection_sha256"]
        or not ledger_path.is_file()
        or v25.sha256(ledger_path) != latest["ledger_sha256"]
    ):
        raise RuntimeError("v30 audit retrofit SHA mismatch")
    upgraded = apply_score_stability_audit(
        latest,
        v25.load_json(selection_path),
        v25.load_json(ledger_path),
    )
    latest.clear()
    latest.update(upgraded)
    latest["score_stability_retrofit"] = {
        "contract": "v29_to_v30_zero_call_audit_upgrade_v1",
        "physical_workload_calls_added": 0,
        "formal_state_mutated": False,
    }


def quality_gate_v30(audits: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    _retrofit_latest_validation(audits)
    gate = _BASE_QUALITY_GATE(audits)
    latest = next(
        (
            item
            for item in reversed(audits)
            if item.get("mode") == "diagnostic_validation"
        ),
        None,
    )
    checks = gate.setdefault("checks", {})
    checks["trusted_cpu_io_memory_stable"] = bool(
        latest is not None
        and latest.get("all_trusted_score_resources_stable") is True
        and int(latest.get("stable_trusted_count", -1)) == 3
    )
    checks["trusted_score_confidence_intervals_separable"] = bool(
        latest is not None
        and latest.get("trusted_score_confidence_intervals_separable") is True
    )
    checks["unstable_score_resources_excluded_from_calibration"] = bool(
        latest is not None
        and all(
            row.get("calibration_eligible") is False
            for row in latest.get("calibration_excluded_rows") or []
        )
        and latest.get("score_stability_rejection_counts_as_technical_failure")
        is False
    )
    gate["passed"] = all(checks.values())
    gate["score_stability_contract"] = (
        "tpcc_all_score_resources_and_separable_score_ci_v30"
    )
    gate["formal_start_authorized"] = bool(gate["passed"])
    return gate


def build_exact_calibration_v30(
    audits: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Use only rows explicitly admitted by the v30 stability wrapper."""
    sanitized = []
    for audit in audits:
        item = copy.deepcopy(dict(audit))
        item["calibration_rows"] = [
            copy.deepcopy(row)
            for row in audit.get("calibration_rows") or []
            if row.get("calibration_eligible") is True
        ]
        sanitized.append(item)
    output = _BASE_BUILD_EXACT_CALIBRATION(sanitized)
    output["score_stability_contract"] = (
        "only_v30_explicit_calibration_eligible_rows_v1"
    )
    output["legacy_rows_without_v30_stability_excluded"] = True
    return output


def choose_next_mode_v30(
    audits: Sequence[Mapping[str, Any]],
    gate: Mapping[str, Any],
) -> str:
    latest = audits[-1] if audits else None
    if (
        latest is not None
        and latest.get("mode") == "diagnostic_validation"
        and latest.get("formal_admission_blocked_by_score_stability") is True
    ):
        if isinstance(gate, MutableMapping):
            gate["score_stability_zero_call_reroute"] = True
            gate["score_stability_zero_call_reason"] = (
                "trusted_score_resources_unstable_or_score_ci_not_separable"
            )
        return "diagnostic"
    return v28.choose_next_mode_v28(audits, gate)


def main() -> int:
    v29._configure_from_argv(sys.argv)
    v25.prelaunch_fingerprint_gate = v29.global_prelaunch_fingerprint_gate
    v25.accumulated_observation_formal_selection = (
        v28.accumulated_observation_formal_selection_v28
    )
    v25.audit_completed_run = audit_completed_run_v30
    v25.quality_gate = quality_gate_v30
    v25.build_exact_calibration = build_exact_calibration_v30
    v25.choose_next_mode = choose_next_mode_v30
    return v25.main()


if __name__ == "__main__":
    raise SystemExit(main())

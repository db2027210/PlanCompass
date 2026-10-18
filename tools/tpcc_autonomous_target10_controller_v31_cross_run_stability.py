#!/usr/bin/env python3
"""TPC-C v31 admission based on repeatable complete-run aggregates.

TPC-C has a periodic IO phase pattern.  Window-to-window IO CV therefore
measures workload phase variation, not experiment reproducibility.  V31 keeps
the within-run CPU and memory checks, requires complete IO coverage, and makes
IO/score stability decisions only from at least three independent complete
runs of the exact same parameter point.
"""

from __future__ import annotations

import copy
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping, Sequence

try:
    import tpcc_autonomous_target10_controller_v25 as v25
    import tpcc_autonomous_target10_controller_v28_role_aware as v28
    import tpcc_autonomous_target10_controller_v29_prelaunch_fingerprint as v29
    import tpcc_autonomous_target10_controller_v30_score_stability as v30
except ModuleNotFoundError:
    from tools import tpcc_autonomous_target10_controller_v25 as v25
    from tools import tpcc_autonomous_target10_controller_v28_role_aware as v28
    from tools import tpcc_autonomous_target10_controller_v29_prelaunch_fingerprint as v29
    from tools import tpcc_autonomous_target10_controller_v30_score_stability as v30


MIN_COMPLETE_RUN_REPEATS = 3
MAX_COMPLETE_RUN_REPEATS = 5
IO_AGGREGATE_CV_LIMIT = 0.10
SCORE_AGGREGATE_CV_LIMIT = 0.01
IO_RELATIVE_CI_HALF_WIDTH_LIMIT = 0.20
SCORE_RELATIVE_CI_HALF_WIDTH_LIMIT = 0.02

_BASE_AUDIT_COMPLETED_RUN = v25.audit_completed_run
_BASE_QUALITY_GATE = v25.quality_gate
_BASE_BUILD_EXACT_CALIBRATION = v25.build_exact_calibration
_CONTROLLER_STATE_PATH: Path | None = None


def _finite(value: Any, label: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise RuntimeError(f"non-finite {label}: {value!r}")
    return number


def _point_key(params: Mapping[str, Any]) -> str:
    normalized = {name: _finite(params[name], name) for name in sorted(params)}
    return v25.canonical_sha256(normalized)


def _mean_ci(values: Sequence[float], label: str) -> Dict[str, Any]:
    numbers = [_finite(value, label) for value in values]
    if not numbers:
        raise RuntimeError(f"empty complete-run values: {label}")
    mean = statistics.fmean(numbers)
    sample_std = statistics.stdev(numbers) if len(numbers) >= 2 else 0.0
    cv = sample_std / abs(mean) if mean != 0.0 else math.inf
    if len(numbers) >= 2:
        half_width = (
            v30._student_t_critical_95(len(numbers))
            * sample_std
            / math.sqrt(len(numbers))
        )
    else:
        half_width = math.inf
    relative_half_width = half_width / abs(mean) if mean != 0.0 else math.inf
    return {
        "sample_count": len(numbers),
        "values": numbers,
        "mean": mean,
        "sample_standard_deviation": sample_std,
        "coefficient_of_variation": cv,
        "confidence_interval_95": {
            "contract": "student_t_complete_run_mean_ci_v1",
            "lower": mean - half_width,
            "center": mean,
            "upper": mean + half_width,
            "half_width": half_width,
            "relative_half_width": relative_half_width,
        },
    }


def _load_audit_rows(audit: Mapping[str, Any]) -> list[Dict[str, Any]]:
    selection_path = Path(str(audit.get("selection_path") or "")).resolve()
    ledger_path = Path(str(audit.get("ledger_path") or "")).resolve()
    if (
        not selection_path.is_file()
        or not ledger_path.is_file()
        or v25.sha256(selection_path) != audit.get("selection_sha256")
        or v25.sha256(ledger_path) != audit.get("ledger_sha256")
    ):
        raise RuntimeError("v31 prior audit source SHA mismatch")
    selection = v25.load_json(selection_path)
    selected = {int(item["rank"]): item for item in selection.get("top5") or []}
    ledger = v25.load_json(ledger_path)
    rows = []
    for result in ledger.get("successful") or []:
        rank = int(result["rank"])
        candidate = selected[rank]
        params = result.get("candidate_params") or candidate.get("candidate_params")
        if not isinstance(params, Mapping):
            raise RuntimeError(f"v31 missing exact params: rank={rank}")
        summary = v30._load_result_summary(result)
        within = v30.score_contributing_resource_assessment(summary)
        rows.append(
            {
                "point_key": _point_key(params),
                "params": copy.deepcopy(dict(params)),
                "actual_resource": copy.deepcopy(result["actual_resource"]),
                "actual_score": _finite(result["actual_score"], "actual_score"),
                "summary_path": str(
                    (result.get("measurement_protocol") or {})[
                        "validated_summary_path"
                    ]
                ),
                "summary_sha256": str(
                    (result.get("measurement_protocol") or {})[
                        "validated_summary_sha256"
                    ]
                ),
                "cpu_within_run_stable": bool(
                    within["resources"]["cpu"]["stable"]
                ),
                "memory_within_run_stable": bool(
                    within["resources"]["memory"]["stable"]
                ),
                "io_coverage_complete": bool(within["coverage_passed"]),
                "io_within_run_cv_audit_only": float(
                    within["resources"]["io"]["coefficient_of_variation"]
                ),
                "source_mode": audit.get("mode"),
                "source_rank": rank,
            }
        )
    return rows


def _repeatability(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    unique = {}
    for row in rows:
        unique[str(row["summary_sha256"])] = copy.deepcopy(dict(row))
    observations = list(unique.values())
    observations.sort(key=lambda item: str(item["summary_path"]))
    io = _mean_ci(
        [float(item["actual_resource"]["io"]) for item in observations],
        "aggregate_io",
    )
    score = _mean_ci(
        [float(item["actual_score"]) for item in observations],
        "aggregate_score",
    )
    enough = len(observations) >= MIN_COMPLETE_RUN_REPEATS
    cpu_memory_stable = all(
        item["cpu_within_run_stable"] is True
        and item["memory_within_run_stable"] is True
        for item in observations
    )
    io_coverage_complete = all(
        item["io_coverage_complete"] is True for item in observations
    )
    io_repeatable = bool(
        enough
        and io["coefficient_of_variation"] <= IO_AGGREGATE_CV_LIMIT
        and io["confidence_interval_95"]["relative_half_width"]
        <= IO_RELATIVE_CI_HALF_WIDTH_LIMIT
    )
    score_repeatable = bool(
        enough
        and score["coefficient_of_variation"] <= SCORE_AGGREGATE_CV_LIMIT
        and score["confidence_interval_95"]["relative_half_width"]
        <= SCORE_RELATIVE_CI_HALF_WIDTH_LIMIT
    )
    passed = bool(
        enough
        and cpu_memory_stable
        and io_coverage_complete
        and io_repeatable
        and score_repeatable
    )
    return {
        "contract": "tpcc_exact_point_complete_run_repeatability_v31",
        "minimum_complete_run_repeats": MIN_COMPLETE_RUN_REPEATS,
        "maximum_complete_run_repeats": MAX_COMPLETE_RUN_REPEATS,
        "observation_count": len(observations),
        "observations": observations,
        "within_run_cpu_memory_stable": cpu_memory_stable,
        "io_complete_run_coverage": io_coverage_complete,
        "io_within_run_cv_role": "audit_only_periodic_phase_variation",
        "aggregate_io": io,
        "aggregate_score": score,
        "limits": {
            "io_aggregate_cv": IO_AGGREGATE_CV_LIMIT,
            "score_aggregate_cv": SCORE_AGGREGATE_CV_LIMIT,
            "io_relative_ci_half_width": IO_RELATIVE_CI_HALF_WIDTH_LIMIT,
            "score_relative_ci_half_width": SCORE_RELATIVE_CI_HALF_WIDTH_LIMIT,
        },
        "enough_complete_run_repeats": enough,
        "aggregate_io_repeatable": io_repeatable,
        "aggregate_score_repeatable": score_repeatable,
        "passed": passed,
    }


def apply_cross_run_stability_audit(
    base_audit: Mapping[str, Any],
    selection: Mapping[str, Any],
    ledger: Mapping[str, Any],
    prior_audits: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Classify the current batch using exact-point complete-run repeats."""
    audit = copy.deepcopy(dict(base_audit))
    historical_rows = []
    for prior in prior_audits:
        if prior.get("clean") is True:
            historical_rows.extend(_load_audit_rows(prior))
    synthetic_current = copy.deepcopy(dict(base_audit))
    synthetic_current["selection_path"] = str(base_audit["selection_path"])
    synthetic_current["ledger_path"] = str(base_audit["ledger_path"])
    current_rows = _load_audit_rows(synthetic_current)
    rows_by_key: Dict[str, list[Dict[str, Any]]] = {}
    for row in [*historical_rows, *current_rows]:
        rows_by_key.setdefault(str(row["point_key"]), []).append(row)

    selected = {int(item["rank"]): item for item in selection.get("top5") or []}
    results = {int(item["rank"]): item for item in ledger.get("successful") or []}
    assessments = []
    trusted_pairs = []
    calibration_eligible_ranks = set()
    aggregate_actual_by_rank = {}
    observation_counts = []
    for rank in sorted(results):
        result = results[rank]
        candidate = selected[rank]
        params = result.get("candidate_params") or candidate.get("candidate_params")
        point_key = _point_key(params)
        repeatability = _repeatability(rows_by_key[point_key])
        observation_counts.append(int(repeatability["observation_count"]))
        trusted_role = candidate.get("selection_role") == "trusted"
        trusted_eligible = bool(trusted_role and repeatability["passed"])
        if trusted_eligible:
            calibration_eligible_ranks.add(rank)
            trusted_pairs.append(
                (
                    float(result["predicted_score"]),
                    float(repeatability["aggregate_score"]["mean"]),
                )
            )
        aggregate_actual_by_rank[rank] = float(
            repeatability["aggregate_score"]["mean"]
        )
        assessments.append(
            {
                "rank": rank,
                "selection_role": candidate.get("selection_role"),
                "effective_role": (
                    "trusted" if trusted_eligible else "uncertain_probe"
                ),
                "ranking_eligible": trusted_eligible,
                "calibration_eligible": trusted_eligible,
                "technical_failure": False,
                "valid_replay": True,
                "point_key": point_key,
                "repeatability": repeatability,
                "classification": (
                    "trusted_complete_run_repeatable"
                    if trusted_eligible
                    else "valid_rejected_probe_only_incomplete_or_unrepeatable"
                ),
            }
        )

    calibration_rows = []
    calibration_excluded_rows = []
    for row in audit.get("calibration_rows") or []:
        enriched = copy.deepcopy(row)
        eligible = int(row["rank"]) in calibration_eligible_ranks
        enriched["calibration_eligible"] = eligible
        enriched["stability_contract"] = (
            "tpcc_exact_point_complete_run_repeatability_v31"
        )
        if eligible:
            calibration_rows.append(enriched)
        else:
            enriched["calibration_exclusion_reason"] = (
                "insufficient_or_unrepeatable_complete_runs_or_probe_role"
            )
            calibration_excluded_rows.append(enriched)

    trusted_candidates = [
        item
        for item in selection.get("top5") or []
        if item.get("selection_role") == "trusted"
    ]
    all_trusted_repeatable = bool(
        len(trusted_candidates) == 3
        and len(trusted_pairs) == 3
    )
    best_rank = (
        max(aggregate_actual_by_rank, key=aggregate_actual_by_rank.get)
        if aggregate_actual_by_rank
        else None
    )
    audit.update(
        {
            "score_stability_contract": (
                "tpcc_complete_run_aggregate_io_score_repeatability_v31"
            ),
            "score_stability_assessments": assessments,
            "trusted_pairs": trusted_pairs,
            "trusted_spearman": (
                v25.spearman(
                    [item[0] for item in trusted_pairs],
                    [item[1] for item in trusted_pairs],
                )
                if len(trusted_pairs) == 3
                else None
            ),
            "stable_trusted_count": len(trusted_pairs),
            "all_trusted_complete_run_repeatable": all_trusted_repeatable,
            "minimum_total_exact_observation_count": (
                min(observation_counts) if observation_counts else 0
            ),
            "maximum_total_exact_observation_count": (
                max(observation_counts) if observation_counts else 0
            ),
            "actual_best_predicted_rank": best_rank,
            "top3_hit": bool(best_rank is not None and best_rank <= 3),
            "calibration_rows": calibration_rows,
            "calibration_excluded_rows": calibration_excluded_rows,
            "calibration_eligible_replay_count": len(calibration_rows),
            "valid_rejected_probe_only_count": sum(
                item["effective_role"] == "uncertain_probe"
                for item in assessments
            ),
            "score_stability_algorithmic_rejection": not all_trusted_repeatable,
            "score_stability_rejection_counts_as_technical_failure": False,
            "formal_admission_blocked_by_score_stability": bool(
                audit.get("mode") == "diagnostic_validation"
                and not all_trusted_repeatable
            ),
        }
    )
    audit["gradient_reversal_detected"] = bool(
        audit["trusted_spearman"] is not None
        and float(audit["trusted_spearman"]) <= 0.0
    )
    return audit


def audit_completed_run_v31(
    run: Mapping[str, Any],
    formal_state: Path,
    *,
    verify_current_state_sha: bool = True,
) -> Dict[str, Any]:
    base = _BASE_AUDIT_COMPLETED_RUN(
        run,
        formal_state,
        verify_current_state_sha=verify_current_state_sha,
    )
    if base.get("mode") == "formal":
        return base
    if _CONTROLLER_STATE_PATH is None:
        raise RuntimeError("v31 controller state path is not configured")
    prior = v25.load_json(_CONTROLLER_STATE_PATH).get("audits") or []
    selection = v25.load_json(Path(base["selection_path"]))
    ledger = v25.load_json(Path(base["ledger_path"]))
    return apply_cross_run_stability_audit(base, selection, ledger, prior)


def quality_gate_v31(audits: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
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
    assessments = list((latest or {}).get("score_stability_assessments") or [])
    trusted = [item for item in assessments if item.get("selection_role") == "trusted"]
    checks["trusted_cpu_memory_within_run_stable"] = bool(
        len(trusted) == 3
        and all(
            item["repeatability"]["within_run_cpu_memory_stable"] is True
            for item in trusted
        )
    )
    checks["trusted_io_complete_run_repeatable"] = bool(
        len(trusted) == 3
        and all(
            item["repeatability"]["aggregate_io_repeatable"] is True
            for item in trusted
        )
    )
    checks["trusted_score_complete_run_repeatable"] = bool(
        len(trusted) == 3
        and all(
            item["repeatability"]["aggregate_score_repeatable"] is True
            for item in trusted
        )
    )
    checks["unstable_or_probe_rows_excluded_from_calibration"] = bool(
        latest is not None
        and all(
            row.get("calibration_eligible") is False
            for row in latest.get("calibration_excluded_rows") or []
        )
        and latest.get("score_stability_rejection_counts_as_technical_failure")
        is False
    )
    gate["passed"] = all(checks.values())
    gate["formal_start_authorized"] = bool(gate["passed"])
    gate["score_stability_contract"] = (
        "tpcc_complete_run_aggregate_io_score_repeatability_v31"
    )
    return gate


def build_exact_calibration_v31(
    audits: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
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
        "only_v31_complete_run_repeatable_trusted_rows_v1"
    )
    output["within_run_io_cv_used_for_admission"] = False
    return output


def choose_next_mode_v31(
    audits: Sequence[Mapping[str, Any]],
    gate: Mapping[str, Any],
) -> str:
    latest = audits[-1] if audits else None
    if latest is not None and latest.get("mode") == "diagnostic_validation":
        maximum_repeats = int(latest.get("maximum_total_exact_observation_count", 0))
        if (
            latest.get("clean") is True
            and gate.get("passed") is not True
            and maximum_repeats < MAX_COMPLETE_RUN_REPEATS
            and (
                int(latest.get("minimum_total_exact_observation_count", 0))
                < MIN_COMPLETE_RUN_REPEATS
                or latest.get("all_trusted_complete_run_repeatable") is not True
            )
        ):
            if isinstance(gate, MutableMapping):
                gate["complete_run_repeatability_reroute"] = True
                gate["complete_run_repeatability_next_mode"] = (
                    "diagnostic_validation"
                )
            return "diagnostic_validation"
    return v28.choose_next_mode_v28(audits, gate)


def _configure(manifest_path: Path) -> None:
    global _CONTROLLER_STATE_PATH
    manifest = v25.load_json(manifest_path.resolve())
    _CONTROLLER_STATE_PATH = Path(manifest["controller_state_path"]).resolve()


def main() -> int:
    manifest_path = Path(sys.argv[1]).resolve()
    _configure(manifest_path)
    v29._configure_from_argv(sys.argv)
    v25.prelaunch_fingerprint_gate = v29.global_prelaunch_fingerprint_gate
    v25.accumulated_observation_formal_selection = (
        v28.accumulated_observation_formal_selection_v28
    )
    v25.audit_completed_run = audit_completed_run_v31
    v25.quality_gate = quality_gate_v31
    v25.build_exact_calibration = build_exact_calibration_v31
    v25.choose_next_mode = choose_next_mode_v31
    return v25.main()


if __name__ == "__main__":
    raise SystemExit(main())

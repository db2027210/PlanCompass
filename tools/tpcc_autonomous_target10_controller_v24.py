#!/usr/bin/env python3
"""TPC-C target-10 controller with three-observation formal acceptance.

Default execution is dry-run.  Armed mode requires the exact SHA-256 of a
sealed manifest and is the only mode allowed to execute builders or launchers.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

PROJECT_ROOT = Path("/home/anonymous/program/resource_adaptive")
sys.path.insert(0, str(PROJECT_ROOT))
from resource_utility import ResourceUtilityCalculator


TARGET = 10
CAP = 5
RESOURCES = ("cpu", "io", "memory")
TPCC_CAPABILITY = {"cpu": 3.8, "memory": 6.0511731087, "io": 54.0}
TPCC_SCORE = ResourceUtilityCalculator(
    score_mode="band_target",
    resource_weights={"cpu": 1.0, "memory": 1.0, "io": 3.0},
    resource_target_ranges={
        "cpu": {
            "target_min_pct": 85.0,
            "target_max_pct": 90.0,
            "hard_overload_pct": 95.0,
            "role": "target_bottleneck",
        },
        "memory": {
            "target_min_pct": 30.0,
            "target_max_pct": 50.0,
            "hard_overload_pct": 90.0,
            "role": "non_bottleneck",
        },
        "io": {
            "target_min_pct": 30.0,
            "target_max_pct": 50.0,
            "hard_overload_pct": 90.0,
            "role": "non_bottleneck",
        },
    },
)
REQUIRED_CANDIDATE_FIELDS = (
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
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_text(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def retire_failed_current_run(
    current: Mapping[str, Any],
    state: Dict[str, Any],
    formal_state: Path,
) -> Dict[str, Any]:
    """Quarantine a sealed nonzero launcher result without losing accounting."""

    returncode = state.get("last_launcher_returncode")
    if not isinstance(returncode, int) or returncode == 0:
        raise RuntimeError("failed-run retirement requires a nonzero return code")
    if sha256(formal_state) != current.get("formal_state_sha256_before"):
        raise RuntimeError("failed current run changed formal state")
    for field, sha_field in (
        ("selection_path", "selection_sha256"),
        ("launcher_path", "launcher_sha256"),
        ("seal_path", "seal_sha256"),
    ):
        path = Path(str(current.get(field) or "")).resolve()
        if not path.is_file() or sha256(path) != current.get(sha_field):
            raise RuntimeError(f"failed current run {field} SHA mismatch")

    run_root = Path(str(current["root"])).resolve()
    ledger_path = Path(str(current["ledger_path"])).resolve()
    summary_path = Path(str(current["summary_path"])).resolve()
    if summary_path.exists():
        raise RuntimeError("failed-run retirement cannot discard an existing summary")
    physical_calls = 0
    fingerprint_ids: List[str] = []
    if ledger_path.is_file():
        ledger = load_json(ledger_path)
        physical_calls = int(ledger.get("real_workload_invocation_count", -1))
        if not 0 <= physical_calls <= CAP:
            raise RuntimeError("failed current run ledger physical count is invalid")
        for key in ("successful", "technical_failures"):
            rows = ledger.get(key) or []
            if not isinstance(rows, list):
                raise RuntimeError(f"failed current run ledger {key} is invalid")
            for row in rows:
                if not isinstance(row, dict):
                    raise RuntimeError("failed current run ledger row is invalid")
                fingerprint = row.get("three_level_fingerprint") or {}
                fingerprint_id = str(fingerprint.get("id") or "")
                if fingerprint_id.startswith("three-level-sha256:"):
                    fingerprint_ids.append(fingerprint_id)
    elif run_root.exists():
        files = sorted(
            str(path.relative_to(run_root))
            for path in run_root.rglob("*")
            if path.is_file()
        )
        if files not in ([], ["top5.json"]):
            raise RuntimeError(
                "failed current run has unaccounted files without a ledger: "
                f"{files}"
            )

    timestamp = time.strftime("%Y%m%dT%H%M%S")
    quarantine: Optional[str] = None
    if run_root.exists():
        target = run_root.with_name(
            f"quarantine_{run_root.name}_launcher_rc{returncode}_{timestamp}"
        )
        if target.exists():
            raise RuntimeError(f"failed-run quarantine already exists: {target}")
        os.replace(run_root, target)
        quarantine = str(target)
    launcher_log = Path(current["launcher_path"]).resolve().with_name("launcher.log")
    if not launcher_log.is_file():
        raise RuntimeError("failed current run launcher log is absent")
    audit = {
        "event": "sealed_current_run_nonzero_quarantined",
        "mode": current["mode"],
        "returncode": returncode,
        "root": str(run_root),
        "quarantine_root": quarantine,
        "selection_path": current["selection_path"],
        "selection_sha256": current["selection_sha256"],
        "launcher_log": str(launcher_log),
        "launcher_log_sha256": sha256(launcher_log),
        "actual_workload_calls": physical_calls,
        "contract_physical_budget_consumed": max(1, physical_calls),
        "valid_replays": 0,
        "fingerprint_ids": sorted(set(fingerprint_ids)),
        "formal_state_sha256": sha256(formal_state),
        "formal_state_mutated": False,
    }
    state.setdefault("technical_failure_audits", []).append(audit)
    state["quarantined_fingerprints"] = sorted(
        set(state.get("quarantined_fingerprints") or []) | set(fingerprint_ids)
    )
    state["last_failed_current_run"] = copy.deepcopy(dict(current))
    state["current_run"] = None
    state["status"] = "failed_current_run_quarantined"
    state["formal_state_sha256"] = sha256(formal_state)
    return audit


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def tpcc_score_resource(resource: Mapping[str, Any]) -> float:
    normalized = {name: float(resource[name]) for name in RESOURCES}
    return float(TPCC_SCORE.calculate_utility_score(normalized, TPCC_CAPABILITY))


def accumulated_observation_formal_selection(
    diagnostic_selection: Mapping[str, Any],
    diagnostic_ledger: Mapping[str, Any],
    quality_gate_payload: Mapping[str, Any],
    source_path: Path,
) -> Dict[str, Any]:
    """Seal every independent exact-point observation into formal Top-5.

    Admission requires at least three observations per point after the latest
    clean validation.  Never collapse that history to two endpoints: formal
    ranking and later acceptance must be reproducible from the full sealed
    observation sequence.
    """
    promoted = copy.deepcopy(dict(diagnostic_selection))
    successful = diagnostic_ledger.get("successful") or []
    if (
        int(diagnostic_ledger.get("real_workload_invocation_count", -1)) != CAP
        or len(successful) != CAP
        or diagnostic_ledger.get("technical_failures") not in ([], None)
    ):
        raise RuntimeError("formal promotion requires a clean exact-repeat ledger")
    by_rank = {int(item["rank"]): item for item in successful}
    if set(by_rank) != set(range(1, CAP + 1)):
        raise RuntimeError("exact-repeat ledger does not cover ranks 1..5")

    for candidate in promoted["top5"]:
        rank = int(candidate["rank"])
        observation = by_rank[rank]
        protocol = observation.get("measurement_protocol") or {}
        if (
            protocol.get("stable") is not True
            or protocol.get("correctness_passed") is not True
            or protocol.get("technical_failure") is not False
            or observation.get("workload_plan_fingerprint")
            != candidate.get("workload_plan_fingerprint")
            or observation.get("three_level_fingerprint", {}).get("id")
            != candidate.get("three_level_fingerprint", {}).get("id")
        ):
            raise RuntimeError(f"formal promotion evidence mismatch: rank={rank}")

        prior_scores = [
            float(value)
            for value in candidate.get("independent_actual_scores") or []
        ]
        prior_resources = [
            {name: float(row[name]) for name in RESOURCES}
            for row in candidate.get("independent_actual_resources") or []
        ]
        if len(prior_scores) < 2 or len(prior_scores) != len(prior_resources):
            raise RuntimeError(
                "formal promotion requires at least two sealed prior exact "
                f"observations: rank={rank}"
            )
        scores = prior_scores + [float(observation["actual_score"])]
        resources = prior_resources + [
            {
                name: float(observation["actual_resource"][name])
                for name in RESOURCES
            }
        ]
        observed_center_score = float(statistics.median(scores))
        resource = {
            name: float(statistics.median(row[name] for row in resources))
            for name in RESOURCES
        }
        resource_median_score = tpcc_score_resource(resource)
        lower_score = min(scores)
        upper_score = max(scores)
        utilization = {
            name: resource[name] / TPCC_CAPABILITY[name]
            for name in RESOURCES
        }
        resource_interval = {
            name: {
                "lower": min(row[name] for row in resources),
                "median": resource[name],
                "upper": max(row[name] for row in resources),
            }
            for name in RESOURCES
        }
        old_fingerprint = candidate.get("three_level_fingerprint") or {}
        feature_level = copy.deepcopy(old_fingerprint.get("feature_ratio_level") or {})
        if set(feature_level) != set(RESOURCES):
            feature_level = {
                name: int(round(float(candidate["feature_ratio"][name]) * 1000.0))
                for name in RESOURCES
            }
        fingerprint_payload = {
            "contract": "plan_feature_resource_three_level_v1",
            "plan_level": candidate["workload_plan_fingerprint"],
            "feature_ratio_level": feature_level,
            "predicted_resource_level": {
                name: int(round(resource[name] * 1000.0)) for name in RESOURCES
            },
            "prediction_source": (
                "accumulated_exact_point_median_v3"
            ),
        }
        candidate.update(
            {
                "independent_actual_scores": scores,
                "independent_actual_resources": resources,
                "predicted_score": resource_median_score,
                "candidate_score": resource_median_score,
                "candidate_score_recomputed": resource_median_score,
                "final_score": resource_median_score,
                "plan_regime_empirical_score": observed_center_score,
                "robust_acceptance_score": observed_center_score,
                "robust_acceptance_statistic": (
                    "median_of_independent_exact_point_actual_scores"
                ),
                "formal_lcb_score": lower_score,
                "predicted_score_interval": {
                    "lower": lower_score,
                    "center": observed_center_score,
                    "upper": upper_score,
                    "source": "accumulated_exact_point_empirical_v3",
                },
                "predicted_resource": resource,
                "uncalibrated_predicted_resource": copy.deepcopy(resource),
                "final_resource": copy.deepcopy(resource),
                "predicted_resource_interval": resource_interval,
                "utilization": utilization,
                "confidence": {
                    "exact_plan_regime": 1.0,
                    "measurement_count": len(scores),
                    "score_lower": lower_score,
                    "score_median": observed_center_score,
                    "prediction_score": resource_median_score,
                    "resource_median_rescored_score": resource_median_score,
                    "score_upper": upper_score,
                    "actual_score_span": upper_score - lower_score,
                    "independent_actual_scores": scores,
                    "independent_repeat_complete": 1.0,
                },
                "prediction_source": "accumulated_exact_point_median_v3",
                "repeat_prediction_source": "accumulated_exact_point_median_v3",
                "three_level_fingerprint": {
                    **fingerprint_payload,
                    "id": "three-level-sha256:"
                    + canonical_sha256(fingerprint_payload),
                    "source_fingerprint_id": old_fingerprint.get("id"),
                    "parameter_fallback_used": False,
                    "valid": True,
                    "validation_errors": [],
                },
            }
        )
        calibration = copy.deepcopy(candidate.get("plan_regime_calibration") or {})
        calibration.update(
            {
                "trusted_eligible": candidate["selection_role"] == "trusted",
                "observation_count": len(scores),
                "prediction_source": "accumulated_exact_point_median_v3",
                "actual_resource_median": copy.deepcopy(resource),
                "actual_resource_interval": resource_interval,
                "actual_score_median": observed_center_score,
                "prediction_score_from_resource_median": resource_median_score,
                "actual_score_span": upper_score - lower_score,
                "regime": {"plan": candidate["workload_plan_fingerprint"]},
            }
        )
        candidate["plan_regime_calibration"] = calibration

    promoted["formal_expected_improvement_required"] = False
    promoted["formal_entry_admission_contract"] = (
        "diagnostic_quality_gate_passed_then_real_replay_accept_or_reject_v1"
    )
    promoted["formal_non_improving_candidate_policy"] = (
        "real_replay_rejected_without_state_update"
    )
    promoted["formal_acceptance_policy"] = (
        "median_of_at_least_three_independent_exact_point_actual_scores_v3"
    )
    promoted["formal_admission_score"] = "predicted_score_interval.lower"
    promoted["formal_prediction_contract"] = (
        "resource_consistent_candidate_score_plus_independent_"
        "actual_score_median_v4"
    )
    promoted["promoted_from_diagnostic_validation"] = {
        "selection_path": str(source_path),
        "selection_sha256": sha256(source_path),
        "quality_gate": copy.deepcopy(dict(quality_gate_payload)),
        "observation_count_per_candidate": min(
            len(item["independent_actual_scores"]) for item in promoted["top5"]
        ),
    }
    return promoted


def average_ranks(values: Sequence[float]) -> List[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    result = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and math.isclose(
            values[order[end]], values[order[cursor]], rel_tol=0.0, abs_tol=1e-12
        ):
            end += 1
        rank = (cursor + 1 + end) / 2.0
        for index in order[cursor:end]:
            result[index] = rank
        cursor = end
    return result


def spearman(predicted: Sequence[float], actual: Sequence[float]) -> float | None:
    if len(predicted) != len(actual) or len(predicted) < 2:
        return None
    left = average_ranks(predicted)
    right = average_ranks(actual)
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum(
        (left[index] - left_mean) * (right[index] - right_mean)
        for index in range(len(left))
    )
    left_norm = math.sqrt(sum((value - left_mean) ** 2 for value in left))
    right_norm = math.sqrt(sum((value - right_mean) ** 2 for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return None
    return numerator / (left_norm * right_norm)


def verify_pins(manifest: Mapping[str, Any]) -> None:
    if manifest.get("schema_version") != "tpcc_autonomous_target10_manifest_v1":
        raise RuntimeError("manifest schema mismatch")
    for item in manifest.get("pinned_files") or []:
        path = Path(item["path"]).resolve()
        if not path.is_file() or sha256(path) != item["sha256"]:
            raise RuntimeError(f"pinned file SHA mismatch: {path}")
    if int(manifest.get("accepted_target", -1)) != TARGET:
        raise RuntimeError("accepted target mismatch")
    if int(manifest.get("per_batch_physical_cap", -1)) != CAP:
        raise RuntimeError("physical cap mismatch")
    if manifest.get("qps_role") != "outer_audit_only_not_inner_score":
        raise RuntimeError("QPS contract mismatch")


def validate_selection(selection: Mapping[str, Any], *, mode: str) -> None:
    candidates = selection.get("top5") or []
    if len(candidates) != CAP:
        raise RuntimeError("selection does not contain exactly five candidates")
    roles = [item.get("selection_role") for item in candidates]
    expected = (
        ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
        if mode in {"formal", "diagnostic_validation"}
        else ["uncertain_probe"] * CAP
    )
    if (
        roles != expected
        or selection.get("maximum_real_workload_invocations") != CAP
        or selection.get("qps_role") != "outer_audit_only_not_inner_score"
    ):
        raise RuntimeError("selection role/cap/QPS contract mismatch")
    if mode == "formal" and (
        selection.get("formal_expected_improvement_required") is not False
        or selection.get("formal_entry_admission_contract")
        != "diagnostic_quality_gate_passed_then_real_replay_accept_or_reject_v1"
        or selection.get("formal_non_improving_candidate_policy")
        != "real_replay_rejected_without_state_update"
        or selection.get("formal_admission_score")
        != "predicted_score_interval.lower"
        or candidates[0].get("formal_lcb_score") is None
        or not math.isclose(
            float(candidates[0]["formal_lcb_score"]),
            float(
                (candidates[0].get("predicted_score_interval") or {})[
                    "lower"
                ]
            ),
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    ):
        raise RuntimeError(
            "formal selection failed quality-gate admission contract"
        )
    if mode == "formal":
        for rank, item in enumerate(candidates, 1):
            scores = item.get("independent_actual_scores")
            if (
                not isinstance(scores, list)
                or len(scores) < 3
                or any(not isinstance(value, (int, float)) for value in scores)
                or (item.get("confidence") or {}).get(
                    "independent_actual_scores"
                )
                != scores
            ):
                raise RuntimeError(
                    "formal candidate lacks three sealed actual scores: "
                    f"rank={rank}"
                )
    ids: List[str] = []
    for rank, item in enumerate(candidates, 1):
        fingerprint = item.get("three_level_fingerprint") or {}
        if (
            int(item.get("rank", -1)) != rank
            or item.get("boundary_corner") is not False
            or item.get("diagnostic_only") is not True
            or item.get("incumbent_update_eligible") is not False
            or fingerprint.get("valid") is not True
            or not str(fingerprint.get("id") or "").startswith(
                "three-level-sha256:"
            )
            or any(item.get(field) is None for field in REQUIRED_CANDIDATE_FIELDS)
        ):
            raise RuntimeError(f"candidate contract mismatch: rank={rank}")
        if item["selection_role"] == "trusted":
            calibration = item.get("plan_regime_calibration") or {}
            if (
                item.get("trusted") is not True
                or item.get("exact_plan_regime_validated") is not True
                or calibration.get("trusted_eligible") is not True
                or (calibration.get("regime") or {}).get("plan")
                != item.get("workload_plan_fingerprint")
            ):
                raise RuntimeError(f"unvalidated trusted rank: {rank}")
        else:
            if item.get("trusted") is not False:
                raise RuntimeError(f"probe marked trusted: rank={rank}")
        ids.append(fingerprint["id"])
    if len(set(ids)) != CAP:
        raise RuntimeError("selection fingerprints are not unique")


def audit_completed_run(
    run: Mapping[str, Any],
    formal_state: Path,
    *,
    verify_current_state_sha: bool = True,
) -> Dict[str, Any]:
    mode = str(run["mode"])
    selection_path = Path(run["selection_path"]).resolve()
    ledger_path = Path(run["ledger_path"]).resolve()
    summary_path = Path(run["summary_path"]).resolve()
    for path, expected in (
        (selection_path, run["selection_sha256"]),
        (ledger_path, run["ledger_sha256"]),
        (summary_path, run["summary_sha256"]),
    ):
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"completed-run SHA mismatch: {path}")
    selection = load_json(selection_path)
    ledger = load_json(ledger_path)
    summary = load_json(summary_path)
    validate_selection(selection, mode=mode)
    physical = int(ledger.get("real_workload_invocation_count", -1))
    successful = ledger.get("successful") or []
    technical = ledger.get("technical_failures") or []
    if (
        not 0 <= physical <= CAP
        or physical != len(successful) + len(technical)
        or len(successful) > CAP
    ):
        raise RuntimeError("physical/valid/technical accounting mismatch")
    selected = {
        int(item["rank"]): item for item in selection["top5"]
    }
    calibration_rows = []
    trusted_pairs = []
    resource_ratios = []
    actual_best_rank = None
    if successful:
        actual_best_rank = int(
            max(successful, key=lambda item: float(item["actual_score"]))["rank"]
        )
    for result in successful:
        rank = int(result["rank"])
        candidate = selected[rank]
        protocol = result.get("measurement_protocol") or {}
        evidence_path = Path(str(protocol.get("validated_summary_path") or ""))
        if (
            protocol.get("stable") is not True
            or protocol.get("correctness_passed") is not True
            or protocol.get("technical_failure") is not False
            or result.get("workload_plan_fingerprint")
            != candidate.get("workload_plan_fingerprint")
            or result.get("three_level_fingerprint", {}).get("id")
            != candidate.get("three_level_fingerprint", {}).get("id")
            or not evidence_path.is_file()
            or sha256(evidence_path) != protocol.get("validated_summary_sha256")
        ):
            raise RuntimeError(f"successful replay evidence mismatch: rank={rank}")
        raw = candidate.get("uncalibrated_predicted_resource") or candidate[
            "predicted_resource"
        ]
        scalar = {
            name: float(result["actual_resource"][name]) / float(raw[name])
            for name in RESOURCES
        }
        if any(not 0.1 <= value <= 10.0 for value in scalar.values()):
            raise RuntimeError("exact regime scalar has magnitude failure")
        calibration_rows.append(
            {
                "plan": result["workload_plan_fingerprint"],
                "rank": rank,
                "resource_scalar": scalar,
                "actual_resource": copy.deepcopy(result["actual_resource"]),
                "actual_score": float(result["actual_score"]),
                "summary_path": str(evidence_path),
                "summary_sha256": sha256(evidence_path),
            }
        )
        if candidate["selection_role"] == "trusted":
            trusted_pairs.append(
                (
                    float(result["predicted_score"]),
                    float(result["actual_score"]),
                )
            )
            for name in RESOURCES:
                resource_ratios.append(
                    float(result["predicted_resource"][name])
                    / float(result["actual_resource"][name])
                )

    baseline_restored = bool(
        summary.get("baseline_params_restored") is True
        and summary.get("baseline_snapshot_restored") is True
    ) if mode != "formal" else bool(summary.get("baseline_restored") is True)
    clean = bool(
        physical == CAP
        and len(successful) == CAP
        and not technical
        and baseline_restored
    )
    if mode != "formal":
        if (
            summary.get("accepted_count_delta") != 0
            or summary.get("incumbent_updated") is not False
            or summary.get("range_updated") is not False
            or summary.get("gradient_update_applied") is not False
            or summary.get("state_commit_eligible") is not False
        ):
            raise RuntimeError("diagnostic run mutated formal state")
    else:
        current_state_sha = sha256(formal_state)
        if (
            verify_current_state_sha
            and summary.get("formal_state_sha256") != current_state_sha
        ):
            raise RuntimeError("formal state SHA does not match completed summary")
        if not clean and (
            summary.get("incumbent_update_applied") is not False
            or summary.get("range_update_applied") is not False
        ):
            raise RuntimeError("technical formal batch mutated incumbent/range")

    predicted = [item[0] for item in trusted_pairs]
    actual = [item[1] for item in trusted_pairs]
    trusted_spearman = spearman(predicted, actual)
    resource_factor_two = bool(
        resource_ratios
        and all(0.5 <= value <= 2.0 for value in resource_ratios)
    )
    resource_within_order_of_magnitude = bool(
        resource_ratios
        and all(0.1 <= value <= 10.0 for value in resource_ratios)
    )
    top3_hit = actual_best_rank is not None and actual_best_rank <= 3
    prior_observation_counts = [
        int((item.get("confidence") or {}).get("measurement_count") or 1)
        for item in selection["top5"]
    ]
    total_exact_observation_counts = [
        count + 1 for count in prior_observation_counts
    ]
    return {
        "mode": mode,
        "root": run["root"],
        "clean": clean,
        "physical_calls": physical,
        "valid_replays": len(successful),
        "technical_failures": len(technical),
        "baseline_restored": baseline_restored,
        "trusted_count": len(trusted_pairs),
        "trusted_pairs": trusted_pairs,
        "trusted_spearman": trusted_spearman,
        "actual_best_predicted_rank": actual_best_rank,
        "top3_hit": top3_hit,
        "trusted_resource_within_factor_two": resource_factor_two,
        "trusted_resource_no_order_of_magnitude_error": (
            resource_within_order_of_magnitude
        ),
        "gradient_reversal_detected": (
            trusted_spearman is not None and trusted_spearman <= 0.0
        ),
        "score_span_gate_passed": bool(
            selection.get("score_span_gate_passed") is True
        ),
        "observed_exact_repeat_score_noise_floor": selection.get(
            "observed_exact_repeat_score_noise_floor"
        ),
        "minimum_required_score_span": selection.get(
            "minimum_required_score_span"
        ),
        "minimum_prior_exact_observation_count": min(
            prior_observation_counts
        ),
        "maximum_prior_exact_observation_count": max(
            prior_observation_counts
        ),
        "minimum_total_exact_observation_count": min(
            total_exact_observation_counts
        ),
        "maximum_total_exact_observation_count": max(
            total_exact_observation_counts
        ),
        "accepted_before": (
            int(summary["accepted_before"])
            if mode == "formal" and summary.get("accepted_before") is not None
            else None
        ),
        "accepted_after": (
            int(summary["accepted_after"])
            if mode == "formal" and summary.get("accepted_after") is not None
            else None
        ),
        "batch_added_accepted": (
            int(summary["batch_added_accepted"])
            if mode == "formal"
            and summary.get("batch_added_accepted") is not None
            else None
        ),
        "formal_prediction_miss": bool(
            mode == "formal"
            and clean
            and int(summary.get("batch_added_accepted") or 0) == 0
        ),
        "calibration_rows": calibration_rows,
        "fingerprint_ids": [
            item["three_level_fingerprint"]["id"]
            for item in selection["top5"]
        ],
        "physical_fingerprint_ids": [
            selected[int(item["rank"])]["three_level_fingerprint"]["id"]
            for item in [*successful, *technical]
        ],
        "selection_path": str(selection_path),
        "selection_sha256": sha256(selection_path),
        "ledger_path": str(ledger_path),
        "ledger_sha256": sha256(ledger_path),
        "summary_path": str(summary_path),
        "summary_sha256": sha256(summary_path),
    }


def choose_next_mode(
    audits: Sequence[Mapping[str, Any]],
    gate: Mapping[str, Any],
) -> str:
    """Route a clean formal miss back through state-neutral recalibration."""
    latest = audits[-1] if audits else None
    if (
        latest is not None
        and latest.get("mode") == "diagnostic"
        and latest.get("clean") is True
        and latest.get("valid_replays") == CAP
    ):
        return "diagnostic_validation"
    if (
        latest is not None
        and latest.get("mode") == "diagnostic_validation"
        and latest.get("clean") is True
        and gate.get("passed") is not True
        and latest.get("score_span_gate_passed") is True
        and int(latest.get("maximum_total_exact_observation_count", 0)) < 3
    ):
        # Keep a signal-bearing exact Top-5 for a bounded robust repeat when
        # one independent replay reverses close ranks.  The next selection is
        # ranked by accumulated exact-point medians, never by a manual point.
        return "diagnostic_validation"
    if latest is not None and latest.get("mode") == "formal":
        if latest.get("clean") is not True:
            return "diagnostic"
        if (
            latest.get("formal_prediction_miss") is True
            or latest.get("batch_added_accepted") == 0
        ):
            return "diagnostic"
    return "formal" if gate.get("passed") is True else "diagnostic"


def quality_gate(audits: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    validations = [
        item for item in audits
        if item["mode"] == "diagnostic_validation"
    ]
    latest = validations[-1] if validations else None
    fingerprints = list((latest or {}).get("fingerprint_ids") or [])
    previous_fingerprints = {
        str(identifier)
        for audit in audits
        if audit is not latest
        for identifier in audit.get("fingerprint_ids") or []
    }
    latest_calibration_rows = list(
        (latest or {}).get("calibration_rows") or []
    )
    # The fixed experiment contract rejects order-of-magnitude prediction
    # failures; it does not impose the older factor-of-two research heuristic.
    # Older persisted audits predate the explicit boolean below, but their
    # audit path already fail-closed when any exact-regime scalar is outside
    # [0.1, 10].  Re-evaluate those sealed scalar rows without changing the
    # diagnostic or formal state.
    trusted_resource_no_magnitude_error = bool(
        latest is not None
        and (
            latest.get(
                "trusted_resource_no_order_of_magnitude_error"
            ) is True
            or (
                latest_calibration_rows
                and all(
                    0.1 <= float(value) <= 10.0
                    for row in latest_calibration_rows
                    for value in (
                        row.get("resource_scalar") or {}
                    ).values()
                )
            )
        )
    )
    checks = {
        "corrected_top5_present": latest is not None,
        "five_novel_noncorner_fingerprints": (
            latest is not None
            and len(fingerprints) == CAP
            and len(set(fingerprints)) == CAP
            and not (set(fingerprints) & previous_fingerprints)
        ),
        "trusted3_probe2": (
            latest is not None and latest.get("trusted_count") == 3
        ),
        "top5_clean_no_technical_failure": (
            latest is not None
            and latest.get("clean") is True
            and latest.get("physical_calls") == CAP
            and latest.get("valid_replays") == CAP
            and latest.get("technical_failures") == 0
        ),
        "trusted_spearman_positive": (
            latest is not None
            and latest.get("trusted_spearman") is not None
            and float(latest["trusted_spearman"]) > 0.0
        ),
        "actual_best_in_predicted_top3": (
            latest is not None and latest.get("top3_hit") is True
        ),
        "trusted_resource_no_order_of_magnitude_error": (
            trusted_resource_no_magnitude_error
        ),
        "baseline_restored": (
            latest is not None and latest.get("baseline_restored") is True
        ),
        "trusted_score_span_above_empirical_noise": (
            latest is not None
            and latest.get("score_span_gate_passed") is True
        ),
        "at_least_three_exact_observations_per_candidate": (
            latest is not None
            and int(latest.get("minimum_total_exact_observation_count", 0)) >= 3
        ),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "diagnostic_validation_batch_count": len(validations),
        "latest_trusted_spearman": (
            latest.get("trusted_spearman") if latest is not None else None
        ),
        "latest_actual_best_predicted_rank": (
            latest.get("actual_best_predicted_rank")
            if latest is not None else None
        ),
    }


def build_exact_calibration(audits: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    regimes: Dict[str, List[Dict[str, Any]]] = {}
    for audit in audits:
        if not audit["clean"]:
            continue
        for row in audit.get("calibration_rows") or []:
            regimes.setdefault(row["plan"], []).append(copy.deepcopy(row))
    output = {}
    for plan, rows in regimes.items():
        output[plan] = {
            "plan": plan,
            "observation_count": len(rows),
            "resource_scalar_median": {
                name: sorted(float(row["resource_scalar"][name]) for row in rows)[
                    len(rows) // 2
                ]
                for name in RESOURCES
            },
            "evidence": rows,
        }
    return {
        "schema_version": "tpcc_autonomous_exact_plan_regime_calibration_v1",
        "scope": "exact_workload_plan_fingerprint_only",
        "regimes": output,
    }


def run_checked(argv: Sequence[str], *, timeout: int) -> None:
    completed = subprocess.run(
        list(argv),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed rc={completed.returncode}: {completed.stdout[-4000:]}"
        )


def seal_next_run(
    manifest: Mapping[str, Any],
    preparation: Path,
    selection_path: Path,
    mode: str,
    ordinal: int,
    quality: Mapping[str, Any],
) -> Dict[str, Any]:
    selection_sha = sha256(selection_path)
    package_root = preparation / "sealed_run"
    package_root.mkdir(parents=True, exist_ok=False)
    sealed_selection = package_root / "top5.json"
    sealed_selection.write_bytes(selection_path.read_bytes())
    if sha256(sealed_selection) != selection_sha:
        raise RuntimeError("sealed selection copy SHA mismatch")
    run_root = (
        Path(manifest["batch_parent"]).resolve()
        / f"autonomous_{mode}_{ordinal:02d}"
    )
    if run_root.exists():
        raise FileExistsError(f"next run root already exists: {run_root}")
    python = str(manifest["python"])
    baseline = Path(manifest["baseline_path"]).resolve()
    diagnostic_runner = Path(manifest["diagnostic_runner_path"]).resolve()
    formal_state = Path(manifest["formal_state_path"]).resolve()
    launcher = package_root / "launch_foreground.sh"
    if mode == "formal":
        promoted_validation = load_json(sealed_selection).get(
            "promoted_from_diagnostic_validation"
        ) or {}
        gate = {
            "schema_version": "tpcc_autonomous_formal_gate_v1",
            "passed": True,
            "formal_start_authorized": True,
            "selection_sha256": selection_sha,
            "quality_gate": copy.deepcopy(quality),
            "source_diagnostic_selection_sha256": promoted_validation.get(
                "selection_sha256"
            ),
            "score_span_gate": {
                "passed": bool(
                    quality.get("checks", {}).get(
                        "trusted_score_span_above_empirical_noise"
                    ) is True
                )
            },
            "qps_role": "outer_audit_only_not_inner_score",
        }
        gate_path = package_root / "gate.json"
        atomic_json(gate_path, gate)
        gate_sha = sha256(gate_path)
        formal_runner = Path(manifest["formal_runner_path"]).resolve()
        command = [
            python,
            "-u",
            str(formal_runner),
            str(sealed_selection),
            selection_sha,
            str(gate_path),
            gate_sha,
            str(diagnostic_runner),
            str(baseline),
            str(formal_state),
            str(run_root),
        ]
        ledger_path = run_root / "strict_top5_real_replay_round14_ledger_v1.json"
        summary_path = run_root / "formal_batch_summary.json"
        prelude = ""
    else:
        copied_selection = run_root / "top5.json"
        selection_contract = load_json(sealed_selection).get(
            "selection_contract"
        )
        generation_names = {
            "tpcc_round12_current_plan_discovery_probe5_v1": (
                "round12",
                "strict_top5_real_replay_round12_ledger_v1.json",
            ),
            "tpcc_round13_exact_plan_regime_corrected_top5_v1": (
                "round13",
                "strict_top5_real_replay_round13_ledger_v1.json",
            ),
            "tpcc_robust_exact_plan_regime_trusted3_probe2_v1": (
                "round14",
                "strict_top5_real_replay_round14_ledger_v1.json",
            ),
        }
        if selection_contract not in generation_names:
            raise RuntimeError(
                "unsupported diagnostic selection contract: "
                f"{selection_contract}"
            )
        replay_generation, ledger_name = generation_names[
            selection_contract
        ]
        # The diagnostic runner binds the replay child name to the selection
        # generation.  Preserve that binding instead of forcing every
        # acquisition/validation batch through the robust-round14 prefix.
        replay_root = run_root / (
            f"top5_real_replay_{replay_generation}_diagnostic_v1"
        )
        project_root = (
            Path(manifest["formal_runner_path"]).resolve().parents[1]
        )
        diagnostic_pythonpath = ":".join(
            dict.fromkeys(
                (str(project_root), str(diagnostic_runner.parent))
            )
        )
        command = [
            "env",
            f"PYTHONPATH={diagnostic_pythonpath}",
            python,
            "-u",
            str(diagnostic_runner),
            str(copied_selection),
            str(replay_root),
            str(baseline),
        ]
        ledger_path = run_root / ledger_name
        summary_path = replay_root / "summary.json"
        prelude = (
            f"test ! -e {shlex.quote(str(run_root))}\n"
            f"mkdir -p {shlex.quote(str(run_root))}\n"
            f"cp {shlex.quote(str(sealed_selection))} "
            f"{shlex.quote(str(copied_selection))}\n"
            f'test "$(sha256sum {shlex.quote(str(copied_selection))} '
            f'| awk \'{{print $1}}\')" = "{selection_sha}"\n'
        )
    pinned_checks = []
    for key in (
        "baseline_path",
        "diagnostic_runner_path",
        "formal_runner_path",
    ):
        if key == "formal_runner_path" and mode != "formal":
            continue
        path = Path(manifest[key]).resolve()
        expected = next(
            item["sha256"]
            for item in manifest["pinned_files"]
            if Path(item["path"]).resolve() == path
        )
        pinned_checks.append(
            f'test "$(sha256sum {shlex.quote(str(path))} '
            f'| awk \'{{print $1}}\')" = "{expected}"'
        )
    formal_state_sha = sha256(formal_state)
    pinned_checks.append(
        f'test "$(sha256sum {shlex.quote(str(formal_state))} '
        f'| awk \'{{print $1}}\')" = "{formal_state_sha}"'
    )
    script = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'test "$(sha256sum {shlex.quote(str(sealed_selection))} '
        f'| awk \'{{print $1}}\')" = "{selection_sha}"\n'
        + "\n".join(pinned_checks)
        + "\n"
        + prelude
        + "exec "
        + " ".join(shlex.quote(item) for item in command)
        + "\n"
    )
    atomic_text(launcher, script, mode=0o700)
    seal = {
        "schema_version": "tpcc_autonomous_sealed_run_v1",
        "mode": mode,
        "ordinal": ordinal,
        "selection_path": str(sealed_selection),
        "selection_sha256": selection_sha,
        "launcher_path": str(launcher),
        "launcher_sha256": sha256(launcher),
        "run_root": str(run_root),
        "ledger_path": str(ledger_path),
        "summary_path": str(summary_path),
        "physical_cap": CAP,
        "formal_state_sha256_before": formal_state_sha,
        "qps_role": "outer_audit_only_not_inner_score",
    }
    seal_path = package_root / "run_seal.json"
    atomic_json(seal_path, seal)
    seal["seal_path"] = str(seal_path)
    seal["seal_sha256"] = sha256(seal_path)
    return seal


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--armed", action="store_true")
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    manifest_path = args.manifest.resolve()
    if args.armed and (
        not args.manifest_sha256
        or sha256(manifest_path) != args.manifest_sha256
    ):
        raise RuntimeError("armed mode requires exact sealed manifest SHA")
    manifest = load_json(manifest_path)
    verify_pins(manifest)
    root = Path(manifest["controller_root"]).resolve()
    state_path = root / "controller_state.json"
    lock_path = root / "controller.lock"
    formal_state = Path(manifest["formal_state_path"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = (
            load_json(state_path)
            if state_path.exists()
            else {
                "schema_version": "tpcc_autonomous_target10_state_v1",
                "status": "waiting_current_batch",
                "dry_run": not args.armed,
                "audits": copy.deepcopy(manifest.get("seed_audits") or []),
                "sources": copy.deepcopy(manifest.get("seed_sources") or []),
                "current_run": copy.deepcopy(manifest["current_run"]),
                "next_ordinal": int(manifest.get("next_ordinal", 7)),
                "formal_state_sha256": manifest[
                    "initial_formal_state_sha256"
                ],
            }
        )
        while True:
            formal = load_json(formal_state)
            if int(formal["accepted_count"]) >= TARGET:
                state["status"] = "target_reached"
                state["formal_state_sha256"] = sha256(formal_state)
                atomic_json(state_path, state)
                return 0
            current = state.get("current_run")
            if current is not None:
                summary_path = Path(current["summary_path"])
                ledger_path = Path(current["ledger_path"])
                if not summary_path.is_file() or not ledger_path.is_file():
                    if (
                        state.get("status")
                        in {"auditing_completed_run", "waiting_current_batch"}
                        and isinstance(state.get("last_launcher_returncode"), int)
                        and int(state["last_launcher_returncode"]) != 0
                    ):
                        retire_failed_current_run(current, state, formal_state)
                        atomic_json(state_path, state)
                        continue
                    run_root = Path(current["root"])
                    if (
                        args.armed
                        and current.get("launch_if_absent") is True
                        and current.get("launcher_invoked") is not True
                        and not run_root.exists()
                    ):
                        launcher = Path(current["launcher_path"]).resolve()
                        if (
                            not launcher.is_file()
                            or sha256(launcher) != current["launcher_sha256"]
                            or sha256(formal_state)
                            != current["formal_state_sha256_before"]
                        ):
                            raise RuntimeError(
                                "current sealed launcher/state SHA mismatch"
                            )
                        current["launcher_invoked"] = True
                        state.pop("last_launcher_returncode", None)
                        state["status"] = "launching_current_sealed_run"
                        atomic_json(state_path, state)
                        completed = subprocess.run(
                            ["bash", str(launcher)],
                            check=False,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            timeout=int(
                                manifest.get("run_timeout_seconds", 21600)
                            ),
                        )
                        atomic_text(
                            root / "current_launcher.log",
                            completed.stdout,
                        )
                        state["last_launcher_returncode"] = (
                            completed.returncode
                        )
                        state["formal_state_sha256"] = sha256(formal_state)
                        atomic_json(state_path, state)
                        continue
                    state["status"] = "waiting_current_batch"
                    state["formal_state_sha256"] = sha256(formal_state)
                    atomic_json(state_path, state)
                    if not args.armed or args.once:
                        return 0
                    time.sleep(int(manifest.get("poll_seconds", 15)))
                    continue
                current["summary_sha256"] = sha256(summary_path)
                current["ledger_sha256"] = sha256(ledger_path)
                audit = audit_completed_run(current, formal_state)
                if (
                    current["mode"] != "formal"
                    and current.get("formal_state_sha256_before")
                    != sha256(formal_state)
                ):
                    raise RuntimeError(
                        "diagnostic acquisition changed formal state SHA"
                    )
                state["audits"].append(audit)
                if not audit["clean"]:
                    state["quarantined_fingerprints"] = sorted(
                        set(state.get("quarantined_fingerprints") or [])
                        | set(
                            audit.get("physical_fingerprint_ids") or []
                        )
                    )
                if audit["clean"]:
                    state["sources"].append(
                        {
                            "name": f"autonomous_{current['mode']}_{state['next_ordinal'] - 1}",
                            "selection_path": audit["selection_path"],
                            "selection_sha256": audit["selection_sha256"],
                            "ledger_path": audit["ledger_path"],
                            "ledger_sha256": audit["ledger_sha256"],
                        }
                    )
                state["current_run"] = None

            gate = quality_gate(state["audits"])
            calibration = build_exact_calibration(state["audits"])
            ordinal = int(state["next_ordinal"])
            preparation = root / f"prepare_{ordinal:02d}"
            evidence_path = preparation / "evidence_manifest.json"
            calibration_path = preparation / "exact_regime_calibration.json"
            selection_path = preparation / "top5.json"
            mode = choose_next_mode(state["audits"], gate)
            evidence = {
                "schema_version": "tpcc_autonomous_evidence_manifest_v1",
                "sources": state["sources"],
                "excluded_fingerprints": sorted(
                    set(state.get("quarantined_fingerprints") or [])
                ),
                "diagnostic_acquisition_reason": (
                    None if mode == "formal" else gate
                ),
                # A trusted ranking must carry more signal than the observed
                # replay noise.  Keep the floor sealed with each preparation
                # so restarts reproduce the same admission decision.
                "minimum_trusted_predicted_score_span": float(
                    manifest.get("minimum_trusted_predicted_score_span")
                    or manifest.get("minimum_diagnostic_score_span")
                    or 0.0
                ),
            }
            state["last_quality_gate"] = gate
            state["next_mode"] = mode
            state["status"] = (
                "dry_run_next_preparation_ready"
                if not args.armed
                else "preparing_next_batch"
            )
            atomic_json(state_path, state)
            if not args.armed:
                return 0

            if preparation.exists():
                # A controller can be interrupted after atomically writing
                # evidence/calibration but before the zero-workload builder
                # produces Top-5.  Resume only that precisely bounded state;
                # any selection, seal, or unknown file fails closed so an
                # already-started batch can never be duplicated.
                allowed_partial_names = {
                    "evidence_manifest.json",
                    "exact_regime_calibration.json",
                }
                observed_names = {
                    item.name for item in preparation.iterdir()
                }
                if (
                    state.get("current_run") is not None
                    or not observed_names.issubset(allowed_partial_names)
                ):
                    raise RuntimeError(
                        "unsafe partially prepared batch cannot be resumed: "
                        f"{preparation} contains {sorted(observed_names)}"
                    )
            else:
                preparation.mkdir(parents=True, exist_ok=False)
            atomic_json(evidence_path, evidence)
            atomic_json(calibration_path, calibration)
            builder = Path(manifest["builder_path"]).resolve()
            builder_argv = [
                manifest["python"],
                str(builder),
                str(evidence_path),
                str(formal_state),
                str(selection_path),
                mode,
            ]
            latest_audit = (
                state["audits"][-1] if state.get("audits") else None
            )
            build_exact_repeat_validation = bool(
                mode == "diagnostic_validation"
                and latest_audit is not None
                and latest_audit.get("mode") in {
                    "diagnostic",
                    "diagnostic_validation",
                }
                and latest_audit.get("clean") is True
                and int(latest_audit.get("physical_calls", -1)) == CAP
                and int(latest_audit.get("valid_replays", -1)) == CAP
                and int(latest_audit.get("technical_failures", -1)) == 0
            )
            promote_validated_top5 = bool(
                mode == "formal"
                and gate.get("passed") is True
                and latest_audit is not None
                and latest_audit.get("mode")
                == "diagnostic_validation"
                and latest_audit.get("clean") is True
                and int(latest_audit.get("physical_calls", -1)) == CAP
                and int(latest_audit.get("valid_replays", -1)) == CAP
                and int(latest_audit.get("technical_failures", -1)) == 0
            )
            if promote_validated_top5:
                promoted_source = Path(
                    latest_audit["selection_path"]
                ).resolve()
                if (
                    not promoted_source.is_file()
                    or sha256(promoted_source)
                    != latest_audit["selection_sha256"]
                ):
                    raise RuntimeError(
                        "gate-passing validation Top-5 SHA mismatch"
                    )
                promoted_diagnostic = load_json(promoted_source)
                validate_selection(
                    promoted_diagnostic,
                    mode="diagnostic_validation",
                )
                promoted_ledger = Path(latest_audit["ledger_path"]).resolve()
                if (
                    not promoted_ledger.is_file()
                    or sha256(promoted_ledger) != latest_audit["ledger_sha256"]
                ):
                    raise RuntimeError(
                        "gate-passing validation ledger SHA mismatch"
                    )
                promoted = accumulated_observation_formal_selection(
                    promoted_diagnostic,
                    load_json(promoted_ledger),
                    gate,
                    promoted_source,
                )
                atomic_json(selection_path, promoted)
            elif build_exact_repeat_validation:
                # A clean discovery starts exact-point evidence; a bounded
                # failed validation continues the same evidence chain.  Do
                # not ask the global pool builder to manufacture new trusted
                # points while the existing Top-5 still has signal above its
                # empirical noise floor.
                # This transition is deterministic and performs no workload;
                # the sealed diagnostic-validation launcher below owns the
                # only five physical calls.
                repeat_builder = Path(
                    manifest["exact_repeat_builder_path"]
                ).resolve()
                if not repeat_builder.is_file():
                    raise RuntimeError("exact-repeat builder is missing")
                repeat_argv = [
                    manifest["python"],
                    str(repeat_builder),
                    "--selection",
                    str(Path(latest_audit["selection_path"]).resolve()),
                    "--ledger",
                    str(Path(latest_audit["ledger_path"]).resolve()),
                    "--output",
                    str(selection_path),
                    "--minimum-score-span",
                    str(
                        float(
                            manifest.get(
                                "minimum_trusted_predicted_score_span"
                            )
                            or manifest.get("minimum_diagnostic_score_span")
                            or 0.0
                        )
                    ),
                ]
                run_checked(
                    repeat_argv,
                    timeout=int(
                        manifest.get("builder_timeout_seconds", 1800)
                    ),
                )
            else:
                try:
                    run_checked(
                        builder_argv,
                        timeout=int(
                            manifest.get(
                                "builder_timeout_seconds",
                                1800,
                            )
                        ),
                    )
                except RuntimeError as exc:
                    if mode in {"formal", "diagnostic_validation"}:
                        # A passed quality gate proves the measured Top-5, not
                        # that the successor pool still contains five diverse
                        # fingerprints.  Pool exhaustion is therefore an
                        # acquisition-state transition, not a terminal error
                        # and never a reason to hand-inject a formal point.
                        # The failed builder has made no workload call and the
                        # formal state is still pinned, so route the same
                        # ordinal through state-neutral diagnostic discovery.
                        if selection_path.exists():
                            raise RuntimeError(
                                "formal builder failed after writing a "
                                "selection; refusing ambiguous reroute"
                            ) from exc
                        failure = {
                            "event": (
                                "formal_pool_exhausted_reroute_diagnostic"
                                if mode == "formal"
                                else "validation_rankability_failed_"
                                "reroute_diagnostic"
                            ),
                            "ordinal": ordinal,
                            "error": str(exc),
                            "workload_calls_added": 0,
                            "formal_state_sha256": sha256(formal_state),
                            "accepted_count": int(formal["accepted_count"]),
                        }
                        state.setdefault("acquisition_transitions", []).append(
                            failure
                        )
                        mode = "diagnostic"
                        state["next_mode"] = mode
                        state["status"] = (
                            "formal_pool_exhausted_preparing_diagnostic"
                            if mode == "formal"
                            else "validation_rankability_failed_"
                            "preparing_diagnostic"
                        )
                        evidence["diagnostic_acquisition_reason"] = {
                            "reason": (
                                "formal_successor_pool_insufficient"
                                if mode == "formal"
                                else "validation_trusted_ranking_not_"
                                "distinguishable_from_noise"
                            ),
                            "formal_quality_gate": copy.deepcopy(gate),
                            "builder_failure": str(exc),
                            "workload_calls_added_before_reroute": 0,
                        }
                        atomic_json(evidence_path, evidence)
                        atomic_json(state_path, state)
                        builder_argv[-1] = mode
                        run_checked(
                            builder_argv,
                            timeout=int(
                                manifest.get(
                                    "builder_timeout_seconds",
                                    1800,
                                )
                            ),
                        )
                    else:
                        raise
            selection = load_json(selection_path)
            validate_selection(selection, mode=mode)
            seal = seal_next_run(
                manifest,
                preparation,
                selection_path,
                mode,
                ordinal,
                gate,
            )
            if (
                sha256(Path(seal["launcher_path"])) != seal["launcher_sha256"]
                or sha256(Path(seal["seal_path"])) != seal["seal_sha256"]
            ):
                raise RuntimeError("new run seal verification failed")
            state["prepared_selection_path"] = str(selection_path)
            state["prepared_selection_sha256"] = sha256(selection_path)
            state["current_run"] = {
                "mode": mode,
                "root": seal["run_root"],
                "selection_path": seal["selection_path"],
                "selection_sha256": seal["selection_sha256"],
                "ledger_path": seal["ledger_path"],
                "summary_path": seal["summary_path"],
                "launcher_path": seal["launcher_path"],
                "launcher_sha256": seal["launcher_sha256"],
                "seal_path": seal["seal_path"],
                "seal_sha256": seal["seal_sha256"],
                "formal_state_sha256_before": seal[
                    "formal_state_sha256_before"
                ],
            }
            state["next_ordinal"] = ordinal + 1
            state.pop("last_launcher_returncode", None)
            state["status"] = "launching_sealed_run"
            atomic_json(state_path, state)
            completed = subprocess.run(
                ["bash", seal["launcher_path"]],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=int(manifest.get("run_timeout_seconds", 21600)),
            )
            atomic_text(
                preparation / "sealed_run/launcher.log",
                completed.stdout,
            )
            state["last_launcher_returncode"] = completed.returncode
            state["formal_state_sha256"] = sha256(formal_state)
            state["status"] = "auditing_completed_run"
            atomic_json(state_path, state)
            if args.once:
                return 0


if __name__ == "__main__":
    raise SystemExit(main())

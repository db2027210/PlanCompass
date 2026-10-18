#!/usr/bin/env python3
"""Build a workload-scoped Algorithm C resource model from real replays.

Valid A/B/C replays may train the resource model, but their algorithm identity,
parameter identity, plan fingerprint, QPS, and accept/reject decision are never
model inputs.  Algorithm C therefore learns resource behavior without learning
the brute-force answer or using QPS before the outer real-replay gate.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

import numpy as np

from algorithm_c_parameter_support import (
    PARAMETER_SUPPORT_CONTRACT,
    apply_parameter_support_abstention,
    build_parameter_support_domain,
)
from algorithm_c_training_boundary import (
    infer_training_source_algorithm,
    parameter_interaction_names,
    runtime_prior_firewall,
    training_boundary,
)


RESOURCE_NAMES = ("cpu", "memory", "io")
DEFAULT_RIDGE_ALPHA_GRID = (0.05, 0.10, 0.20, 0.50, 1.0, 2.0, 5.0)
# The deployed model includes 7 main effects and 21 pairwise interactions.
# Keep enough grouped physical evidence for a meaningful independent holdout;
# rank/quality, not sample count alone, remains authoritative.
MINIMUM_TRAIN_COUNT = 20
MINIMUM_INDEPENDENT_HOLDOUT_COUNT = 30
MINIMUM_TARGET_HOLDOUT_SPEARMAN = 0.50
MAXIMUM_TARGET_MAE_LOG_RATIO = math.log(1.10)
MAXIMUM_NON_TARGET_MAE_LOG_RATIO = math.log(1.15)
MINIMUM_MATERIAL_SIGN_COUNT = 10
MINIMUM_SIGN_ACCURACY = 0.75


def read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid JSONL {path}:{line_number}: {exc}") from exc
            if isinstance(value, dict):
                yield value


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def resource_training_domain(workload_contract: Mapping[str, Any]) -> Dict[str, Any]:
    """Extract workload/executor fields that govern resource-label transfer."""

    database = workload_contract.get("database")
    database = database if isinstance(database, Mapping) else {}
    workers = database.get("workers")
    workers = workers if isinstance(workers, Sequence) else []
    native_xp = workload_contract.get("native_xp")
    native_xp = native_xp if isinstance(native_xp, Mapping) else {}
    shared_ssd = workload_contract.get("shared_ssd_coordination")
    shared_ssd = shared_ssd if isinstance(shared_ssd, Mapping) else {}
    plan_evidence = workload_contract.get("plan_evidence")
    plan_evidence = plan_evidence if isinstance(plan_evidence, Mapping) else {}
    return {
        "contract": "algorithm_c_resource_training_execution_domain_v1",
        "benchmark": workload_contract.get("benchmark"),
        "physical_replay": workload_contract.get("physical_replay"),
        "benchmark_concurrency": copy.deepcopy(
            workload_contract.get("benchmark_concurrency")
        ),
        "database": {
            "dbname": database.get("dbname"),
            "template_db": database.get("template_db"),
            "workers": [
                {
                    key: worker.get(key)
                    for key in (
                        "name",
                        "container",
                        "host",
                        "port",
                        "cpuset_cpus",
                    )
                }
                for worker in workers
                if isinstance(worker, Mapping)
            ],
        },
        "native_xp": {
            key: copy.deepcopy(native_xp.get(key))
            for key in (
                "native_jar_sha256",
                "statement_file_sha256",
                "xtpclient",
                "xapclient",
                "at_percentages",
                "forbidden_query_selection_fields_absent",
            )
        },
        "resource_capacity": copy.deepcopy(
            workload_contract.get("resource_capacity")
        ),
        "shared_ssd_coordination": {
            key: shared_ssd.get(key)
            for key in (
                "enabled",
                "device",
                "physical_workload_mode",
                "database_restore_mode",
                "covers_cache_eviction_and_physical_workload",
            )
        },
        "plan_evidence": {
            "sha256": plan_evidence.get("sha256"),
            "query_count": plan_evidence.get("query_count"),
            "physical_workload_sql_modified": plan_evidence.get(
                "physical_workload_sql_modified"
            ),
        },
    }


def finite_positive(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed <= 0.0:
        return None
    return parsed


def parameter_only_prediction_calibration(
    *,
    resource: str,
    target_resource: str,
    coefficient: np.ndarray,
    active_params: Sequence[str],
    x_train: np.ndarray,
    training_loo_mae_log_ratio: float,
    alpha: float,
    enable_target_support_abstention: bool,
) -> Dict[str, Any]:
    """Build the plan-incomplete fallback without weakening schema-7 support.

    Target support abstention is a candidate-generation safety property, not
    a property of the plan feature family.  The parameter-only fallback must
    therefore serialize the same training support domain as the hybrid path.
    """
    support_enabled = bool(
        enable_target_support_abstention and resource == target_resource
    )
    support_domain = (
        build_parameter_support_domain(x_train, active_params)
        if support_enabled
        else None
    )
    interactions = parameter_interaction_names(active_params)
    return {
        "family": "parameter",
        "selection_scope": "training_only",
        "training_selection_method": "plan_prediction_incomplete_parameter_fallback_v1",
        "training_loo_mae_log_ratio": training_loo_mae_log_ratio,
        "alpha": alpha,
        "parameter_coefficients": {
            name: float(value)
            for name, value in zip(active_params, coefficient)
        },
        "parameter_squared_coefficients": {
            name: 0.0 for name in active_params
        },
        "parameter_interaction_coefficients": {
            f"{left}*{right}": 0.0 for left, right in interactions
        },
        "parameter_interaction_order": [
            [left, right] for left, right in interactions
        ],
        "parameter_plan_interaction_coefficients": {
            name: 0.0 for name in active_params
        },
        "plan_log_ratio_squared_coefficient": 0.0,
        "parameter_feature_contract": "resource_response_surface_v2",
        "plan_log_ratio_coefficient": 0.0,
        "candidate_audit": [],
        "independent_holdout_used_for_model_selection": False,
        "qps_used": False,
        "zero_intercept": True,
        "baseline_identity_preserved": True,
        "parameter_support_domain": support_domain,
        "parameter_support_abstention_enabled": support_enabled,
        "parameter_support_contract": (
            PARAMETER_SUPPORT_CONTRACT if support_enabled else None
        ),
        "historical_validation_support_assessments": [],
    }


def worker_key(row: Mapping[str, Any]) -> str:
    worker = row.get("worker")
    if isinstance(worker, Mapping):
        value = str(worker.get("name") or worker.get("container") or "").strip()
        if value:
            return value
    return str(row.get("worker_key") or "").strip()


def physical_row_is_valid(row: Mapping[str, Any]) -> bool:
    if str(row.get("status") or "").lower() != "ok":
        return False
    stats = row.get("resource_stats")
    if isinstance(stats, Mapping) and stats.get("resource_monitor_ok") is False:
        return False
    completeness = row.get("workload_completeness")
    if isinstance(completeness, Mapping) and completeness.get("passed") is not True:
        return False
    if row.get("technical_failure") is True:
        return False
    executor_telemetry = row.get("executor_telemetry")
    if (
        not isinstance(executor_telemetry, Mapping)
        or executor_telemetry.get("available") is not True
    ):
        return False
    return True


def round_index(row: Mapping[str, Any]) -> int:
    for key in ("round", "round_index"):
        try:
            return int(row.get(key))
        except (TypeError, ValueError):
            continue
    label = str(row.get("label") or "")
    marker = "_r"
    if marker in label:
        token = label.split(marker, 1)[1].split("_", 1)[0]
        try:
            return int(token)
        except ValueError:
            pass
    return 0


def controller_validation_row(
    envelope: Mapping[str, Any],
) -> Tuple[str, Dict[str, Any]] | None:
    """Unwrap an A/B/C controller event into one physical validation row."""

    event = str(envelope.get("event") or "")
    event_map = {
        "algorithm_c_baseline_worker_validation": "baseline_worker_validation",
        "algorithm_c_candidate_replay": "candidate_validation",
    }
    logical_event = event_map.get(event)
    if logical_event is None:
        return None
    validation = envelope.get("validation")
    if not isinstance(validation, Mapping):
        return None
    row = copy.deepcopy(dict(validation))
    row["event"] = logical_event
    row["round_index"] = int(envelope.get("round_index") or 0)
    row["round"] = int(envelope.get("round_index") or 0)
    row["training_batch_kind"] = str(envelope.get("batch_kind") or "")
    row["holdout_group"] = (
        f"{logical_event}::{row['training_batch_kind']}::"
        f"round::{row['round_index']:06d}"
    )
    return logical_event, row


def canonical_baseline_snapshot(
    baselines: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any] | None:
    """Build the contemporaneous resource-only worker-baseline aggregate."""

    rows = [row for row in baselines.values() if physical_row_is_valid(row)]
    if not rows:
        return None
    resources: Dict[str, float] = {}
    for resource in RESOURCE_NAMES:
        values = [
            finite_positive((row.get("resources") or {}).get(resource))
            for row in rows
        ]
        if any(value is None for value in values):
            return None
        resources[resource] = float(sum(values) / len(values))  # type: ignore[arg-type]
    return {
        "resources": resources,
        "worker_count": len(rows),
        "qps_used": False,
    }


def local_incumbent_training_payload(
    row: Mapping[str, Any],
    active_params: Sequence[str],
) -> Dict[str, Any] | None:
    """Recover a local-incumbent plan/resource sample from controller logs."""

    ranking = row.get("candidate_ranking")
    ranking = ranking if isinstance(ranking, Mapping) else {}
    metadata = ranking.get("final_estimation_metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    baseline_params = metadata.get("baseline_params")
    baseline_real = metadata.get("baseline_real")
    calibration = metadata.get("resource_prediction_calibration_audit")
    worker_baseline = row.get("_training_worker_baseline")
    canonical_baseline = row.get("_training_canonical_baseline")
    params = row.get("params")
    resources = row.get("resources")
    if not all(
        isinstance(value, Mapping)
        for value in (
            baseline_params,
            baseline_real,
            calibration,
            worker_baseline,
            canonical_baseline,
            params,
            resources,
        )
    ):
        return None

    x: List[float] = []
    for name in active_params:
        candidate_value = finite_positive(params.get(name))
        baseline_value = finite_positive(baseline_params.get(name))
        if candidate_value is None or baseline_value is None:
            return None
        x.append(math.log(candidate_value / baseline_value))

    outcomes: Dict[str, float] = {}
    raw_plan_log_ratios: Dict[str, float] = {}
    worker_resources = worker_baseline.get("resources")
    canonical_resources = canonical_baseline.get("resources")
    if not isinstance(worker_resources, Mapping) or not isinstance(
        canonical_resources,
        Mapping,
    ):
        return None
    for resource in RESOURCE_NAMES:
        actual = finite_positive(resources.get(resource))
        worker = finite_positive(worker_resources.get(resource))
        canonical = finite_positive(canonical_resources.get(resource))
        local_real = finite_positive(baseline_real.get(resource))
        resource_calibration = calibration.get(resource)
        if (
            actual is None
            or worker is None
            or canonical is None
            or local_real is None
            or not isinstance(resource_calibration, Mapping)
        ):
            return None
        try:
            raw_plan = float(resource_calibration["raw_plan_log_ratio"])
        except (KeyError, TypeError, ValueError):
            return None
        if not math.isfinite(raw_plan):
            return None
        normalized_actual = canonical * actual / worker
        outcomes[resource] = math.log(normalized_actual / local_real)
        raw_plan_log_ratios[resource] = raw_plan

    plan_fingerprint = str(
        metadata.get("workload_plan_fingerprint")
        or metadata.get("plan_fingerprint")
        or ranking.get("workload_plan_fingerprint")
        or ""
    ).strip()
    coverage = metadata.get("plan_fingerprint_coverage")
    if (
        not plan_fingerprint
        or not isinstance(coverage, Mapping)
        or coverage.get("complete") is not True
    ):
        return None
    return {
        "x": x,
        "outcomes": outcomes,
        "plan_prediction": {
            "predicted_log_ratios": raw_plan_log_ratios,
            "workload_plan_fingerprint": plan_fingerprint,
            # These are pre-replay EXPLAIN/catalog features produced for the
            # candidate before any physical outcome exists.  They may be used
            # by resource-only model families, but never carry QPS or an
            # outer accept/reject label.
            "current_features": copy.deepcopy(
                metadata.get("current_features") or {}
            ),
            "source": "controller_pre_calibration_raw_plan_log_ratio",
            "actual_results_used": False,
            "qps_used": False,
        },
        "local_incumbent_anchor_recovered": True,
    }


def load_samples(
    paths: Sequence[Path],
    active_params: Sequence[str],
    allowed_workers: Sequence[str] = (),
    plan_predictions: Mapping[str, Mapping[str, Any]] | None = None,
    source_algorithms: Mapping[str, str] | None = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    prediction_by_label = plan_predictions or {}
    declared_source_algorithms = {
        str(path): str(algorithm).strip().lower()
        for path, algorithm in (source_algorithms or {}).items()
    }
    allowed_worker_set = {str(value).strip() for value in allowed_workers if str(value).strip()}
    source_order = {str(path): index for index, path in enumerate(paths)}
    baselines: Dict[Tuple[str, str], Dict[str, Any]] = {}
    candidates: List[Tuple[str, str, Dict[str, Any]]] = []
    source_counts: Dict[str, Dict[str, int]] = {}
    source_resource_domains: Dict[str, Dict[str, Any]] = {}
    training_source_counts: Dict[str, int] = {}
    for path in paths:
        source_algorithm = declared_source_algorithms.get(str(path))
        if not source_algorithm:
            source_algorithm = infer_training_source_algorithm(path)
        if source_algorithm not in {
            "algorithm_a",
            "algorithm_b",
            "algorithm_c",
        }:
            raise RuntimeError(
                f"unsupported training source algorithm: {source_algorithm}"
            )
        counts = {"baseline": 0, "candidate": 0, "invalid_candidate": 0}
        contemporaneous_baselines: Dict[str, Dict[str, Any]] = {}
        for envelope in read_jsonl(path):
            envelope_event = str(envelope.get("event") or "")
            if envelope_event.endswith("_lane_start"):
                embedded_contract = envelope.get("workload_contract")
                if (
                    str(path) not in source_resource_domains
                    and isinstance(embedded_contract, Mapping)
                ):
                    domain = resource_training_domain(embedded_contract)
                    source_resource_domains[str(path)] = {
                        "domain": domain,
                        "domain_sha256": canonical_sha256(domain),
                    }
            unwrapped = controller_validation_row(envelope)
            if unwrapped is None:
                row = envelope
                event = str(row.get("event") or "")
            else:
                event, row = unwrapped
            key = worker_key(row)
            if event == "baseline_worker_validation" and key and physical_row_is_valid(row):
                baselines[(str(path), key)] = row
                contemporaneous_baselines[key] = copy.deepcopy(row)
                counts["baseline"] += 1
            elif event == "candidate_validation":
                if allowed_worker_set and key not in allowed_worker_set:
                    counts["invalid_candidate"] += 1
                    continue
                if physical_row_is_valid(row):
                    worker_baseline = contemporaneous_baselines.get(key)
                    canonical_baseline = canonical_baseline_snapshot(
                        contemporaneous_baselines
                    )
                    if (
                        isinstance(worker_baseline, Mapping)
                        and isinstance(canonical_baseline, Mapping)
                    ):
                        row["_training_worker_baseline"] = copy.deepcopy(
                            dict(worker_baseline)
                        )
                        row["_training_canonical_baseline"] = copy.deepcopy(
                            dict(canonical_baseline)
                        )
                    candidates.append((str(path), source_algorithm, row))
                    counts["candidate"] += 1
                else:
                    counts["invalid_candidate"] += 1
        source_counts[str(path)] = counts
        training_source_counts[source_algorithm] = (
            training_source_counts.get(source_algorithm, 0)
            + counts["candidate"]
        )

    samples: List[Dict[str, Any]] = []
    signatures: set[Tuple[str, ...]] = set()
    skipped: Dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for source_path, source_algorithm, row in candidates:
        key = worker_key(row)
        formal_anchor = row.get("formal_anchor")
        if isinstance(formal_anchor, Mapping):
            if formal_anchor.get("contract") != (
                "algorithm_c_formal_replay_local_incumbent_anchor_v1"
            ):
                skip("formal_anchor_contract_mismatch")
                continue
            parameter_log_deltas = formal_anchor.get("parameter_log_deltas")
            outcomes = formal_anchor.get("outcomes")
            raw_plan_log_ratios = formal_anchor.get("raw_plan_log_ratios")
            if not all(
                isinstance(value, Mapping)
                for value in (
                    parameter_log_deltas,
                    outcomes,
                    raw_plan_log_ratios,
                )
            ):
                skip("formal_anchor_payload_incomplete")
                continue
            try:
                x = [float(parameter_log_deltas[name]) for name in active_params]
                formal_outcomes = {
                    resource: float(outcomes[resource])
                    for resource in RESOURCE_NAMES
                }
                predicted_log_ratios = {
                    resource: float(raw_plan_log_ratios[resource])
                    for resource in RESOURCE_NAMES
                }
            except (KeyError, TypeError, ValueError):
                skip("formal_anchor_numeric_payload_invalid")
                continue
            if not all(
                math.isfinite(value)
                for value in (*x, *formal_outcomes.values(), *predicted_log_ratios.values())
            ):
                skip("formal_anchor_non_finite")
                continue
            params = row.get("params")
            if not isinstance(params, Mapping):
                skip("formal_anchor_params_missing")
                continue
            signature_tuple = tuple(
                format(finite_positive(params.get(name)) or math.nan, ".17g")
                for name in active_params
            )
            if any(value == "nan" for value in signature_tuple):
                skip("formal_anchor_parameter_vector_incomplete")
                continue
            physical_signature = (
                source_path,
                key,
                str(row.get("label") or ""),
                *signature_tuple,
            )
            if physical_signature in signatures:
                skip("duplicate_physical_training_observation")
                continue
            signatures.add(physical_signature)
            samples.append(
                {
                    "source_path": source_path,
                    "source_order": source_order[source_path],
                    "training_source_algorithm": source_algorithm,
                    "worker": key,
                    "round_index": round_index(row),
                    # Round numbers repeat across immutable campaigns.  Keep
                    # the exporter-provided physical round atomic while
                    # namespacing it by its source history so r61/r62 round 1
                    # can never be merged into one validation group.
                    "holdout_group": (
                        f"{source_path}::"
                        f"{str(formal_anchor.get('holdout_group') or f'formal_round_{round_index(row):06d}')}"
                    ),
                    "label": row.get("label"),
                    "params": dict(params),
                    "x": x,
                    "outcomes": formal_outcomes,
                    "executor_telemetry": copy_executor_telemetry(row),
                    "plan_prediction": {
                        "predicted_log_ratios": predicted_log_ratios,
                        "workload_plan_fingerprint": formal_anchor.get(
                            "workload_plan_fingerprint"
                        ),
                        "source": "pre_calibration_raw_plan_log_ratio",
                        "actual_results_used": False,
                        "qps_used": False,
                    },
                    "formal_anchor": copy.deepcopy(dict(formal_anchor)),
                }
            )
            continue
        recovered = local_incumbent_training_payload(row, active_params)
        if recovered is not None:
            params = row.get("params")
            assert isinstance(params, Mapping)
            signature_tuple = tuple(
                format(finite_positive(params.get(name)) or math.nan, ".17g")
                for name in active_params
            )
            if any(value == "nan" for value in signature_tuple):
                skip("local_anchor_parameter_vector_incomplete")
                continue
            physical_signature = (
                source_path,
                key,
                str(row.get("label") or ""),
                *signature_tuple,
            )
            if physical_signature in signatures:
                skip("duplicate_physical_training_observation")
                continue
            signatures.add(physical_signature)
            samples.append(
                {
                    "source_path": source_path,
                    "source_order": source_order[source_path],
                    "training_source_algorithm": source_algorithm,
                    "worker": key,
                    "round_index": round_index(row),
                    "holdout_group": (
                        f"{source_path}::"
                        f"{str(row.get('holdout_group') or f'controller_round_{round_index(row):06d}')}"
                    ),
                    "label": row.get("label"),
                    "params": dict(params),
                    "x": list(recovered["x"]),
                    "outcomes": dict(recovered["outcomes"]),
                    "executor_telemetry": copy_executor_telemetry(row),
                    "plan_prediction": copy.deepcopy(
                        recovered["plan_prediction"]
                    ),
                    "local_incumbent_anchor_recovered": True,
                }
            )
            continue
        baseline = baselines.get((source_path, key))
        if baseline is None:
            skip("same_worker_baseline_missing")
            continue
        params = row.get("params")
        base_params = baseline.get("params")
        resources = row.get("resources")
        base_resources = baseline.get("resources")
        if not all(isinstance(value, Mapping) for value in (params, base_params, resources, base_resources)):
            skip("payload_incomplete")
            continue
        x: List[float] = []
        signature: List[str] = []
        failed = False
        for name in active_params:
            value = finite_positive(params.get(name))
            base_value = finite_positive(base_params.get(name))
            if value is None or base_value is None:
                failed = True
                break
            delta = math.log(value / base_value)
            x.append(delta)
            signature.append(format(value, ".17g"))
        if failed:
            skip("parameter_vector_incomplete")
            continue
        signature_tuple = tuple(signature)
        physical_signature = (
            source_path,
            key,
            str(row.get("label") or ""),
            *signature_tuple,
        )
        if physical_signature in signatures:
            skip("duplicate_physical_training_observation")
            continue
        outcomes: Dict[str, float] = {}
        for resource in RESOURCE_NAMES:
            value = finite_positive(resources.get(resource))
            base_value = finite_positive(base_resources.get(resource))
            if value is None or base_value is None:
                failed = True
                break
            outcomes[resource] = math.log(value / base_value)
        if failed:
            skip("outcome_incomplete")
            continue
        signatures.add(physical_signature)
        samples.append(
            {
                "source_path": source_path,
                "source_order": source_order[source_path],
                "training_source_algorithm": source_algorithm,
                "worker": key,
                "round_index": round_index(row),
                "holdout_group": f"{source_path}::round::{round_index(row)}",
                "label": row.get("label"),
                "params": dict(params),
                "x": x,
                "outcomes": outcomes,
                "executor_telemetry": copy_executor_telemetry(row),
                "plan_prediction": dict(
                    prediction_by_label.get(str(row.get("label") or "")) or {}
                ),
            }
        )
    samples.sort(
        key=lambda item: (
            int(item["source_order"]),
            int(item["round_index"]),
            str(item["worker"]),
            str(item["label"]),
        )
    )
    included_training_source_counts: Dict[str, int] = {}
    for item in samples:
        source_algorithm = str(item["training_source_algorithm"])
        included_training_source_counts[source_algorithm] = (
            included_training_source_counts.get(source_algorithm, 0) + 1
        )
    return samples, {
        "source_counts": source_counts,
        "source_resource_domains": source_resource_domains,
        "skipped": skipped,
        "same_worker_normalization": True,
        "technical_failures_excluded": True,
        "executor_telemetry_required": True,
        "allowed_workers": sorted(allowed_worker_set),
        "plan_prediction_count": sum(
            bool(item.get("plan_prediction")) for item in samples
        ),
        "training_source_algorithms": sorted(
            included_training_source_counts
        ),
        "training_source_counts": included_training_source_counts,
        "physically_valid_source_candidate_counts": training_source_counts,
        "source_algorithm_used_as_model_feature": False,
        "qps_present_in_training_rows": False,
        "qps_used_as_model_feature_or_target": False,
    }


def copy_executor_telemetry(row: Mapping[str, Any]) -> Dict[str, Any]:
    telemetry = row.get("executor_telemetry")
    if not isinstance(telemetry, Mapping):
        return {}
    metrics = telemetry.get("metrics") if isinstance(telemetry.get("metrics"), Mapping) else {}
    derived = telemetry.get("derived") if isinstance(telemetry.get("derived"), Mapping) else {}
    allowed_metrics = {
        name: float(value)
        for name, value in metrics.items()
        if isinstance(value, (int, float)) and math.isfinite(float(value))
    }
    return {
        "source": telemetry.get("source"),
        "metrics": allowed_metrics,
        "derived": {
            name: value
            for name, value in derived.items()
            if value is None
            or (isinstance(value, (int, float)) and math.isfinite(float(value)))
        },
        "qps_used_for_resource_prediction": False,
    }


def rankdata(values: Sequence[float]) -> np.ndarray:
    order = np.argsort(np.asarray(values, dtype=float), kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    index = 0
    while index < len(values):
        end = index + 1
        while end < len(values) and values[order[end]] == values[order[index]]:
            end += 1
        ranks[order[index:end]] = (index + end - 1) / 2.0
        index = end
    return ranks


def spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    left_rank = rankdata(left)
    right_rank = rankdata(right)
    if float(np.std(left_rank)) == 0.0 or float(np.std(right_rank)) == 0.0:
        return None
    return float(np.corrcoef(left_rank, right_rank)[0, 1])


def wilson_lower_bound(successes: int, total: int, z: float = 1.645) -> float:
    if total <= 0:
        return 0.0
    proportion = successes / total
    denominator = 1.0 + z * z / total
    center = proportion + z * z / (2.0 * total)
    radius = z * math.sqrt(
        proportion * (1.0 - proportion) / total + z * z / (4.0 * total * total)
    )
    return max(0.0, (center - radius) / denominator)


def robust_target_down(
    predicted_log_ratio: float,
    prediction_error_margin_log_ratio: float,
    deadband_log_ratio: float,
) -> bool:
    """Return the exact conservative target-down predicate used for replay.

    The error margin is estimated exclusively from pre-holdout training or
    historical-validation residuals.  It is an empirical error envelope, not
    a statistical confidence interval.
    """
    predicted = float(predicted_log_ratio)
    margin = float(prediction_error_margin_log_ratio)
    deadband = float(deadband_log_ratio)
    if not all(math.isfinite(value) for value in (predicted, margin, deadband)):
        raise ValueError("robust target-down inputs must be finite")
    if margin < 0.0:
        raise ValueError("prediction error margin must be non-negative")
    if deadband <= 0.0:
        raise ValueError("target-down deadband must be positive")
    return predicted + margin <= -deadband


def target_direction_metrics(
    actual: Sequence[float],
    predicted: Sequence[float],
    *,
    prediction_error_margin_log_ratio: float = 0.0,
    deadband_log_ratio: float = math.log(1.01),
) -> Dict[str, Any]:
    if len(actual) != len(predicted):
        raise ValueError("direction metric arrays have incompatible lengths")
    margin = float(prediction_error_margin_log_ratio)
    if not math.isfinite(margin) or margin < 0.0:
        raise ValueError("prediction error margin must be finite and non-negative")
    evaluable = [
        index for index, value in enumerate(actual)
        if abs(float(value)) >= deadband_log_ratio
    ]
    correct = 0
    actual_down = 0
    point_predicted_down = 0
    point_confirmed_down = 0
    robust_predicted_down = 0
    robust_confirmed_down = 0
    for index in evaluable:
        actual_value = float(actual[index])
        predicted_value = float(predicted[index])
        predicted_material = abs(predicted_value) >= deadband_log_ratio
        if predicted_material and math.copysign(1.0, actual_value) == math.copysign(1.0, predicted_value):
            correct += 1
        if actual_value <= -deadband_log_ratio:
            actual_down += 1
    # Precision is a property of every point the predictor would label down.
    # A predicted-down point whose actual change falls inside the deadband is
    # a false positive, not an unevaluable omission from the denominator.
    for index, predicted_raw in enumerate(predicted):
        actual_value = float(actual[index])
        predicted_value = float(predicted_raw)
        if predicted_value <= -deadband_log_ratio:
            point_predicted_down += 1
            if actual_value <= -deadband_log_ratio:
                point_confirmed_down += 1
        if robust_target_down(
            predicted_value,
            margin,
            deadband_log_ratio,
        ):
            robust_predicted_down += 1
            if actual_value <= -deadband_log_ratio:
                robust_confirmed_down += 1
    count = len(evaluable)
    accuracy = correct / count if count else 0.0
    point_precision = (
        point_confirmed_down / point_predicted_down
        if point_predicted_down
        else 0.0
    )
    robust_precision = (
        robust_confirmed_down / robust_predicted_down
        if robust_predicted_down
        else 0.0
    )
    return {
        "deadband_log_ratio": deadband_log_ratio,
        "prediction_error_margin_log_ratio": margin,
        "prediction_error_margin_semantics": (
            "pre_holdout_empirical_error_envelope_not_confidence_interval"
        ),
        "robust_target_down_predicate": (
            "predicted_log_ratio + prediction_error_margin_log_ratio "
            "<= -deadband_log_ratio"
        ),
        "material_sign_evaluable_count": count,
        "material_sign_correct_count": correct,
        "sign_accuracy": accuracy,
        "sign_accuracy_wilson90_lower": wilson_lower_bound(correct, count),
        "actual_target_down_count": actual_down,
        "point_predicted_target_down_count": point_predicted_down,
        "point_confirmed_target_down_count": point_confirmed_down,
        "point_predicted_target_down_precision": point_precision,
        "robust_predicted_target_down_count": robust_predicted_down,
        "robust_confirmed_target_down_count": robust_confirmed_down,
        "robust_predicted_target_down_precision": robust_precision,
        "uncertain_point_down_count": max(
            0,
            point_predicted_down - robust_predicted_down,
        ),
        # Schema compatibility: the formal fields now describe the exact
        # conservative set that can consume physical replay slots.
        "predicted_target_down_count": robust_predicted_down,
        "confirmed_target_down_count": robust_confirmed_down,
        "predicted_target_down_precision": robust_precision,
    }


def design_matrix_metrics(matrix: np.ndarray) -> Dict[str, Any]:
    parameter_count = int(matrix.shape[1])
    rank = int(np.linalg.matrix_rank(matrix))
    column_norms = np.linalg.norm(matrix, axis=0)
    if np.any(column_norms <= 1e-12):
        condition = math.inf
    else:
        condition = float(np.linalg.cond(matrix / column_norms))
    return {
        "row_count": int(matrix.shape[0]),
        "active_parameter_count": parameter_count,
        "matrix_rank": rank,
        "scaled_condition_number": condition,
        "full_column_rank": rank == parameter_count,
    }


def ridge_fit(
    x: np.ndarray,
    y: np.ndarray,
    alpha: float,
    sample_weights: np.ndarray | None = None,
) -> np.ndarray:
    """Fit zero-intercept ridge, optionally balancing physical replay groups."""

    identity = np.eye(x.shape[1], dtype=float)
    if sample_weights is None:
        return np.linalg.solve(x.T @ x + alpha * identity, x.T @ y)
    weights = np.asarray(sample_weights, dtype=float)
    if weights.shape != (x.shape[0],):
        raise ValueError("ridge sample weights have incompatible shape")
    if np.any(~np.isfinite(weights)) or np.any(weights <= 0.0):
        raise ValueError("ridge sample weights must be positive and finite")
    weighted_x = x * weights[:, np.newaxis]
    return np.linalg.solve(
        x.T @ weighted_x + alpha * identity,
        x.T @ (weights * y),
    )


def grouped_replay_sample_weights(
    samples: Sequence[Mapping[str, Any]],
) -> np.ndarray:
    """Give every independent physical replay group the same total weight.

    Algorithm A commonly contributes 128 candidates per round while B/C
    contribute five.  Equal row weights would let search breadth masquerade as
    stronger statistical evidence.  This weighting depends only on the atomic
    replay group, never on source-algorithm identity, QPS, or accept/reject.
    The returned weights have mean one so the ridge-alpha scale stays stable.
    """

    if not samples:
        return np.asarray([], dtype=float)
    groups = [str(item.get("holdout_group") or "") for item in samples]
    counts: Dict[str, int] = {}
    for group in groups:
        counts[group] = counts.get(group, 0) + 1
    group_count = len(counts)
    row_count = len(groups)
    return np.asarray(
        [row_count / (group_count * counts[group]) for group in groups],
        dtype=float,
    )


def parameter_model_features(
    matrix: np.ndarray,
    active_params: Sequence[str],
) -> Tuple[np.ndarray, Tuple[Tuple[str, str], ...]]:
    """Add deterministic curvature/interaction terms without row identity."""

    if matrix.ndim != 2 or matrix.shape[1] != len(active_params):
        raise ValueError("parameter model feature matrix shape mismatch")
    interactions = parameter_interaction_names(active_params)
    squared_columns = [matrix[:, position] ** 2 for position in range(matrix.shape[1])]
    parameter_index = {
        str(name): position for position, name in enumerate(active_params)
    }
    interaction_columns = [
        matrix[:, parameter_index[left]] * matrix[:, parameter_index[right]]
        for left, right in interactions
    ]
    return (
        np.column_stack((matrix, *squared_columns, *interaction_columns)),
        interactions,
    )


def select_ridge_alpha_by_training_loo(
    x_train: np.ndarray,
    y_train: np.ndarray,
    *,
    requested_alpha: float,
    sample_weights: np.ndarray | None = None,
    alpha_grid: Sequence[float] = DEFAULT_RIDGE_ALPHA_GRID,
) -> Dict[str, Any]:
    """Select ridge regularization using only target-resource training rows.

    The independent outer-round holdout and QPS are deliberately absent from
    this interface so neither can leak into model selection.  The complete
    candidate audit is persisted in the experience artifact.
    """
    if x_train.ndim != 2 or y_train.ndim != 1 or x_train.shape[0] != y_train.shape[0]:
        raise ValueError("training arrays have incompatible shapes")
    if x_train.shape[0] < 3:
        raise ValueError("training-only LOO alpha selection requires at least 3 rows")
    candidates = sorted(
        {
            float(value)
            for value in (*alpha_grid, requested_alpha)
            if math.isfinite(float(value)) and float(value) > 0.0
        }
    )
    if not candidates:
        raise ValueError("ridge alpha grid must contain a positive finite value")
    audit: List[Dict[str, Any]] = []
    for candidate_alpha in candidates:
        predictions: List[float] = []
        actual: List[float] = []
        for omitted in range(x_train.shape[0]):
            keep = np.arange(x_train.shape[0]) != omitted
            loo_weights = (
                np.asarray(sample_weights, dtype=float)[keep]
                if sample_weights is not None
                else None
            )
            coefficient = ridge_fit(
                x_train[keep],
                y_train[keep],
                candidate_alpha,
                loo_weights,
            )
            predictions.append(float(x_train[omitted] @ coefficient))
            actual.append(float(y_train[omitted]))
        errors = np.abs(np.asarray(predictions) - np.asarray(actual))
        audit.append(
            {
                "alpha": candidate_alpha,
                "loo_mae_log_ratio": float(np.mean(errors)),
                "loo_max_abs_error_log_ratio": float(np.max(errors)),
                "prediction_count": len(predictions),
            }
        )
    selected = min(audit, key=lambda row: (row["loo_mae_log_ratio"], row["alpha"]))
    return {
        "method": "training_only_leave_one_out_target_resource_mae_v1",
        "requested_alpha": float(requested_alpha),
        "selected_alpha": float(selected["alpha"]),
        "target_resource_training_rows_only": True,
        "independent_holdout_used": False,
        "qps_used": False,
        "candidates": audit,
    }


def historical_validation_model_key(
    metrics: Mapping[str, Any],
    *,
    family_order: int = 0,
    alpha: float = 0.0,
) -> Tuple[Any, ...]:
    """Select a resource model by independent C-domain prediction quality.

    This key deliberately uses only resource outcomes.  Candidate QPS, outer
    accept/reject labels, and Algorithm A candidate identity are unavailable to
    this interface.  Rank/error quality is evaluated before target-down
    usefulness because the model is also used to order candidates and score
    non-target safety.  Directional selection uses the point-prediction fields,
    matching the later formal admission gate; the empirical error envelope is
    retained as confidence/abstention metadata and must not silently replace
    the model-family objective.  The final/newest holdout is never passed to
    this function.
    """
    direction = metrics.get("direction") or {}
    predicted_down = int(
        direction.get("point_predicted_target_down_count") or 0
    )
    confirmed_down = int(
        direction.get("point_confirmed_target_down_count") or 0
    )
    false_down = max(0, predicted_down - confirmed_down)
    precision = float(
        direction.get("point_predicted_target_down_precision") or 0.0
    )
    sign_accuracy = float(direction.get("sign_accuracy") or 0.0)
    rank_correlation = metrics.get("spearman")
    spearman_value = (
        float(rank_correlation) if rank_correlation is not None else -1.0
    )
    directional_gate_passed = bool(
        confirmed_down >= 2
        and precision >= 0.80
        and spearman_value + 1e-12 >= 0.10
    )
    mae_value = metrics.get("mae_log_ratio")
    mae = float(mae_value) if mae_value is not None else math.inf
    if not math.isfinite(mae):
        mae = math.inf
    quality_gate_passed = bool(
        spearman_value + 1e-12 >= 0.10
        and mae <= math.log(1.25) + 1e-12
    )
    useful_directional_evidence = bool(
        confirmed_down >= 1
        and precision + 1e-12 >= 0.50
    )
    return (
        0 if quality_gate_passed else 1,
        0 if directional_gate_passed else 1,
        0 if useful_directional_evidence else 1,
        -spearman_value,
        mae,
        -sign_accuracy,
        false_down,
        -confirmed_down,
        -precision,
        int(family_order),
        float(alpha),
    )


def select_ridge_alpha_by_historical_validation(
    x_fit: np.ndarray,
    y_fit: np.ndarray,
    x_validation: np.ndarray,
    y_validation: np.ndarray,
    *,
    requested_alpha: float,
    fit_sample_weights: np.ndarray | None = None,
    alpha_grid: Sequence[float] = DEFAULT_RIDGE_ALPHA_GRID,
) -> Dict[str, Any]:
    """Select alpha on an older round while preserving the newest holdout."""
    if (
        x_fit.ndim != 2
        or x_validation.ndim != 2
        or y_fit.ndim != 1
        or y_validation.ndim != 1
        or x_fit.shape[0] != y_fit.shape[0]
        or x_validation.shape[0] != y_validation.shape[0]
        or x_fit.shape[1] != x_validation.shape[1]
    ):
        raise ValueError("historical validation arrays have incompatible shapes")
    if x_fit.shape[0] < 3 or x_validation.shape[0] < 2:
        raise ValueError("historical validation requires at least 3 fit and 2 validation rows")
    candidates = sorted(
        {
            float(value)
            for value in (*alpha_grid, requested_alpha)
            if math.isfinite(float(value)) and float(value) > 0.0
        }
    )
    audit: List[Dict[str, Any]] = []
    for candidate_alpha in candidates:
        coefficient = ridge_fit(
            x_fit,
            y_fit,
            candidate_alpha,
            fit_sample_weights,
        )
        validation_prediction = x_validation @ coefficient
        point_metrics = prediction_metrics(y_validation, validation_prediction)
        metrics = prediction_metrics(
            y_validation,
            validation_prediction,
            prediction_error_margin_log_ratio=float(
                point_metrics["mae_log_ratio"]
            ),
        )
        audit.append(
            {
                "alpha": candidate_alpha,
                "historical_validation_metrics": metrics,
                "selection_key": list(
                    historical_validation_model_key(
                        metrics,
                        alpha=candidate_alpha,
                    )
                ),
            }
        )
    selected = min(
        audit,
        key=lambda row: historical_validation_model_key(
            row["historical_validation_metrics"],
            alpha=float(row["alpha"]),
        ),
    )
    return {
        "method": "historical_round_validation_resource_rank_error_direction_v2",
        "requested_alpha": float(requested_alpha),
        "selected_alpha": float(selected["alpha"]),
        "target_resource_training_rows_only": True,
        "independent_holdout_used": False,
        "historical_validation_used": True,
        "qps_used": False,
        "candidates": audit,
    }


def prediction_metrics(
    actual: Sequence[float],
    predicted: Sequence[float],
    *,
    prediction_error_margin_log_ratio: float = 0.0,
) -> Dict[str, Any]:
    """Build the common independent-holdout metric payload."""
    actual_array = np.asarray(actual, dtype=float)
    predicted_array = np.asarray(predicted, dtype=float)
    if actual_array.shape != predicted_array.shape or actual_array.ndim != 1:
        raise ValueError("prediction metric arrays have incompatible shapes")
    absolute_errors = np.abs(predicted_array - actual_array)
    return {
        "mae_log_ratio": float(np.mean(absolute_errors)),
        "max_abs_error_log_ratio": float(np.max(absolute_errors)),
        "spearman": spearman(predicted_array.tolist(), actual_array.tolist()),
        "predicted": predicted_array.tolist(),
        "actual": actual_array.tolist(),
        "direction": target_direction_metrics(
            actual_array.tolist(),
            predicted_array.tolist(),
            prediction_error_margin_log_ratio=(
                prediction_error_margin_log_ratio
            ),
        ),
    }


def fit_resource_prediction_calibration(
    *,
    x_train: np.ndarray,
    x_holdout: np.ndarray,
    y_train: np.ndarray,
    y_holdout: np.ndarray,
    plan_train: np.ndarray,
    plan_holdout: np.ndarray,
    active_params: Sequence[str],
    requested_alpha: float,
    train_sample_weights: np.ndarray | None = None,
    x_model_fit: np.ndarray | None = None,
    x_model_validation: np.ndarray | None = None,
    y_model_fit: np.ndarray | None = None,
    y_model_validation: np.ndarray | None = None,
    plan_model_fit: np.ndarray | None = None,
    plan_model_validation: np.ndarray | None = None,
    model_fit_sample_weights: np.ndarray | None = None,
    enable_parameter_support_abstention: bool = False,
    target_resource_prediction: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Select a resource predictor without touching the newest holdout.

    When an older model-validation round is available, it selects the family
    and ridge alpha with direction-first safety.  The chosen family is then
    refit on every pre-holdout row and evaluated exactly once on the newest
    independent holdout.  With insufficient history, the legacy training LOO
    selection remains available so early calibration batches can be built but
    will still fail the formal rank/direction gates when evidence is weak.
    """
    row_count = int(x_train.shape[0])
    if (
        x_train.ndim != 2
        or x_holdout.ndim != 2
        or x_train.shape[1] != len(active_params)
        or x_holdout.shape[1] != len(active_params)
        or y_train.shape != (row_count,)
        or plan_train.shape != (row_count,)
        or y_holdout.shape != plan_holdout.shape
        or y_holdout.ndim != 1
    ):
        raise ValueError("resource prediction calibration arrays are incompatible")

    historical_values = (
        x_model_fit,
        x_model_validation,
        y_model_fit,
        y_model_validation,
        plan_model_fit,
        plan_model_validation,
    )
    historical_selection = all(value is not None for value in historical_values)
    if any(value is not None for value in historical_values) and not historical_selection:
        raise ValueError("historical model-selection arrays must be supplied together")
    if historical_selection:
        assert x_model_fit is not None
        assert x_model_validation is not None
        assert y_model_fit is not None
        assert y_model_validation is not None
        assert plan_model_fit is not None
        assert plan_model_validation is not None
        if (
            x_model_fit.ndim != 2
            or x_model_validation.ndim != 2
            or x_model_fit.shape[1] != len(active_params)
            or x_model_validation.shape[1] != len(active_params)
            or y_model_fit.shape != (x_model_fit.shape[0],)
            or y_model_validation.shape != (x_model_validation.shape[0],)
            or plan_model_fit.shape != y_model_fit.shape
            or plan_model_validation.shape != y_model_validation.shape
        ):
            raise ValueError("historical resource model arrays are incompatible")

    parameter_train, interactions = parameter_model_features(
        x_train,
        active_params,
    )
    parameter_holdout, holdout_interactions = parameter_model_features(
        x_holdout,
        active_params,
    )
    if holdout_interactions != interactions:
        raise RuntimeError("parameter interaction order mismatch")
    parameter_model_fit: np.ndarray | None = None
    parameter_model_validation: np.ndarray | None = None
    if historical_selection:
        assert x_model_fit is not None
        assert x_model_validation is not None
        parameter_model_fit, fit_interactions = parameter_model_features(
            x_model_fit,
            active_params,
        )
        parameter_model_validation, validation_interactions = (
            parameter_model_features(
                x_model_validation,
                active_params,
            )
        )
        if fit_interactions != interactions or validation_interactions != interactions:
            raise RuntimeError("historical parameter interaction order mismatch")

    raw_point_selection_metrics = (
        prediction_metrics(y_model_validation, plan_model_validation)
        if historical_selection
        else None
    )
    raw_uncertainty = float(
        raw_point_selection_metrics["mae_log_ratio"]
        if raw_point_selection_metrics is not None
        else np.mean(np.abs(plan_train - y_train))
    )
    raw_selection_metrics = (
        prediction_metrics(
            y_model_validation,
            plan_model_validation,
            prediction_error_margin_log_ratio=raw_uncertainty,
        )
        if historical_selection
        else None
    )
    candidates: List[Dict[str, Any]] = [{
        "family": "raw_plan",
        "training_selection_method": (
            "historical_round_validation_resource_rank_error_direction_v2"
            if historical_selection
            else "fixed_raw_plan_training_mae_v1"
        ),
        "training_loo_mae_log_ratio": raw_uncertainty,
        "historical_validation_metrics": raw_selection_metrics,
        "alpha": None,
        "parameter_coefficients": [0.0] * len(active_params),
        "parameter_squared_coefficients": [0.0] * len(active_params),
        "parameter_interaction_coefficients": [0.0] * len(interactions),
        "parameter_plan_interaction_coefficients": [0.0] * len(active_params),
        "plan_log_ratio_coefficient": 1.0,
        "plan_log_ratio_squared_coefficient": 0.0,
        "holdout_prediction": plan_holdout,
    }]

    def hybrid_features(
        raw_x: np.ndarray,
        parameter_features: np.ndarray,
        plan: np.ndarray,
    ) -> np.ndarray:
        return np.column_stack(
            (
                parameter_features,
                plan,
                raw_x * plan[:, np.newaxis],
                plan ** 2,
            )
        )

    family_inputs = (
        (
            "parameter",
            parameter_train,
            parameter_holdout,
            parameter_model_fit,
            parameter_model_validation,
        ),
        (
            "hybrid",
            hybrid_features(x_train, parameter_train, plan_train),
            hybrid_features(x_holdout, parameter_holdout, plan_holdout),
            (
                hybrid_features(
                    x_model_fit,
                    parameter_model_fit,
                    plan_model_fit,
                )
                if historical_selection
                else None
            ),
            (
                hybrid_features(
                    x_model_validation,
                    parameter_model_validation,
                    plan_model_validation,
                )
                if historical_selection
                else None
            ),
        ),
    )
    for (
        family,
        train_features,
        holdout_features,
        model_fit_features,
        model_validation_features,
    ) in family_inputs:
        if historical_selection:
            assert model_fit_features is not None
            assert model_validation_features is not None
            assert y_model_fit is not None
            assert y_model_validation is not None
            alpha_selection = select_ridge_alpha_by_historical_validation(
                model_fit_features,
                y_model_fit,
                model_validation_features,
                y_model_validation,
                requested_alpha=requested_alpha,
                fit_sample_weights=model_fit_sample_weights,
            )
        else:
            alpha_selection = select_ridge_alpha_by_training_loo(
                train_features,
                y_train,
                requested_alpha=requested_alpha,
                sample_weights=train_sample_weights,
            )
        selected_alpha = float(alpha_selection["selected_alpha"])
        selected_alpha_row = next(
            row for row in alpha_selection["candidates"]
            if float(row["alpha"]) == selected_alpha
        )
        coefficient = ridge_fit(
            train_features,
            y_train,
            selected_alpha,
            train_sample_weights,
        )
        parameter_feature_count = 2 * len(active_params) + len(interactions)
        main_end = len(active_params)
        squared_end = 2 * len(active_params)
        if family == "parameter":
            parameter_coefficient = coefficient[:main_end]
            squared_coefficient = coefficient[main_end:squared_end]
            interaction_coefficient = coefficient[
                squared_end:parameter_feature_count
            ]
            parameter_plan_coefficient = np.zeros(len(active_params))
            plan_coefficient = 0.0
            plan_squared_coefficient = 0.0
        else:
            parameter_coefficient = coefficient[:main_end]
            squared_coefficient = coefficient[main_end:squared_end]
            interaction_coefficient = coefficient[
                squared_end:parameter_feature_count
            ]
            plan_coefficient = float(coefficient[parameter_feature_count])
            parameter_plan_coefficient = coefficient[
                parameter_feature_count + 1 : parameter_feature_count + 1 + len(active_params)
            ]
            plan_squared_coefficient = float(coefficient[-1])
        candidates.append(
            {
                "family": family,
                "training_selection_method": (
                    "historical_round_validation_resource_rank_error_direction_v2"
                    if historical_selection
                    else "training_only_leave_one_out_target_resource_mae_v1"
                ),
                "training_loo_mae_log_ratio": float(
                    (
                        selected_alpha_row["historical_validation_metrics"]["mae_log_ratio"]
                        if historical_selection
                        else selected_alpha_row["loo_mae_log_ratio"]
                    )
                ),
                "alpha": selected_alpha,
                "alpha_selection": alpha_selection,
                "historical_validation_metrics": (
                    selected_alpha_row.get("historical_validation_metrics")
                ),
                "parameter_coefficients": parameter_coefficient.tolist(),
                "parameter_squared_coefficients": squared_coefficient.tolist(),
                "parameter_interaction_coefficients": (
                    interaction_coefficient.tolist()
                ),
                "parameter_plan_interaction_coefficients": (
                    parameter_plan_coefficient.tolist()
                ),
                "plan_log_ratio_coefficient": plan_coefficient,
                "plan_log_ratio_squared_coefficient": (
                    plan_squared_coefficient
                ),
                "holdout_prediction": holdout_features @ coefficient,
            }
        )

    family_order = {"raw_plan": 0, "parameter": 1, "hybrid": 2}
    if historical_selection:
        selected = min(
            candidates,
            key=lambda row: historical_validation_model_key(
                row.get("historical_validation_metrics") or {},
                family_order=family_order[str(row["family"])],
                alpha=float(row.get("alpha") or 0.0),
            ),
        )
    else:
        selected = min(
            candidates,
            key=lambda row: (
                float(row["training_loo_mae_log_ratio"]),
                family_order[str(row["family"])],
            ),
        )
    target_prediction_safety_mode = "direct_resource_response"
    safety_validation_metrics: Dict[str, Any] | None = None
    monotone_parameter_l2_scale = 0.0
    monotone_reference_l2 = 1.0
    if target_resource_prediction and historical_selection:
        assert x_model_validation is not None
        assert y_model_validation is not None
        deadband = math.log(1.01)
        material_validation = np.abs(y_model_validation) >= deadband
        material_count = int(np.sum(material_validation))
        actual_down_count = int(
            np.sum(y_model_validation[material_validation] <= -deadband)
        )
        down_fraction = (
            actual_down_count / material_count if material_count else 0.0
        )
        nonincrease_fraction = float(np.mean(y_model_validation <= 0.0))
        minimum_monotone_evidence = max(
            5,
            int(math.ceil(0.60 * len(y_model_validation))),
        )
        if (
            material_count == 0
            and float(np.max(np.abs(y_model_validation))) < deadband
        ):
            target_prediction_safety_mode = "flat_target_safe_abstention"
        elif (
            material_count >= minimum_monotone_evidence
            and down_fraction >= 0.80
            and nonincrease_fraction >= 0.80
        ):
            target_prediction_safety_mode = "monotone_target_down_distance"
            validation_norm = np.linalg.norm(x_model_validation, axis=1)
            positive_norm = validation_norm[validation_norm > 1e-12]
            monotone_reference_l2 = float(
                np.median(positive_norm) if positive_norm.size else 1.0
            )
            monotone_parameter_l2_scale = float(
                np.median(np.abs(y_model_validation[material_validation]))
            )
        else:
            # A resource response that changes sign for indistinguishable
            # transitions is not learnable from pre-replay evidence.  The
            # only safe formal model is an explicit abstention: it may not
            # claim target-down, while the outer real replay remains the sole
            # accept/reject authority.
            target_prediction_safety_mode = "irreducible_noise_safe_abstention"

        for field in (
            "parameter_coefficients",
            "parameter_squared_coefficients",
            "parameter_interaction_coefficients",
            "parameter_plan_interaction_coefficients",
        ):
            selected[field] = [0.0] * len(selected[field])
        selected["plan_log_ratio_coefficient"] = 0.0
        selected["plan_log_ratio_squared_coefficient"] = 0.0
        if target_prediction_safety_mode == "monotone_target_down_distance":
            validation_prediction = -monotone_parameter_l2_scale * np.minimum(
                2.0,
                np.linalg.norm(x_model_validation, axis=1)
                / monotone_reference_l2,
            )
            selected["holdout_prediction"] = (
                -monotone_parameter_l2_scale
                * np.minimum(
                    2.0,
                    np.linalg.norm(x_holdout, axis=1)
                    / monotone_reference_l2,
                )
            )
        else:
            validation_prediction = np.zeros_like(y_model_validation)
            selected["holdout_prediction"] = np.zeros_like(y_holdout)
        safety_validation_metrics = prediction_metrics(
            y_model_validation,
            validation_prediction,
        )
        selected["training_loo_mae_log_ratio"] = float(
            safety_validation_metrics["mae_log_ratio"]
        )

    holdout_prediction = np.asarray(selected["holdout_prediction"], dtype=float)
    support_domain: Dict[str, Any] | None = None
    support_assessments: List[Dict[str, Any]] = []
    historical_support_assessments: List[Dict[str, Any]] = []
    if enable_parameter_support_abstention:
        support_domain = build_parameter_support_domain(x_train, active_params)
        holdout_prediction, support_assessments = apply_parameter_support_abstention(
            holdout_prediction,
            x_holdout,
            support_domain,
            active_params,
        )
        if historical_selection:
            assert x_model_fit is not None
            assert x_model_validation is not None
            historical_domain = build_parameter_support_domain(
                x_model_fit,
                active_params,
            )
            _, historical_support_assessments = apply_parameter_support_abstention(
                np.zeros(x_model_validation.shape[0], dtype=float),
                x_model_validation,
                historical_domain,
                active_params,
            )
    calibration = {
        "family": selected["family"],
        "selection_scope": (
            "historical_round_validation"
            if historical_selection
            else "training_only"
        ),
        "training_selection_method": selected["training_selection_method"],
        "training_loo_mae_log_ratio": selected["training_loo_mae_log_ratio"],
        # A model that loses rank direction on the older, independent
        # validation round must not drive another physical target-down probe.
        # Keep it in the artifact for diagnostics, but make candidate
        # generation abstain.  The newest holdout is intentionally excluded
        # from this decision so it cannot tune its own gate.
        "historical_validation_direction_admitted": bool(
            not historical_selection
            or (
                float(
                    (
                        selected.get("historical_validation_metrics") or {}
                    ).get("spearman")
                    or -1.0
                )
                + 1e-12
                >= MINIMUM_TARGET_HOLDOUT_SPEARMAN
                and int(
                    (
                        (
                            selected.get("historical_validation_metrics")
                            or {}
                        ).get("direction")
                        or {}
                    ).get("point_predicted_target_down_count")
                    or 0
                )
                >= 2
                and float(
                    (
                        (
                            selected.get("historical_validation_metrics")
                            or {}
                        ).get("direction")
                        or {}
                    ).get("point_predicted_target_down_precision")
                    or 0.0
                )
                + 1e-12
                >= 0.80
            )
        ),
        "target_prediction_safety_mode": target_prediction_safety_mode,
        "target_prediction_abstained": target_prediction_safety_mode in {
            "flat_target_safe_abstention",
            "irreducible_noise_safe_abstention",
        },
        "monotone_parameter_l2_scale": monotone_parameter_l2_scale,
        "monotone_reference_l2": monotone_reference_l2,
        "safety_mode_selected_without_final_holdout": True,
        "safety_validation_metrics": safety_validation_metrics,
        "alpha": selected.get("alpha"),
        "parameter_coefficients": {
            name: float(value)
            for name, value in zip(
                active_params,
                selected["parameter_coefficients"],
            )
        },
        "parameter_squared_coefficients": {
            name: float(value)
            for name, value in zip(
                active_params,
                selected["parameter_squared_coefficients"],
            )
        },
        "parameter_interaction_coefficients": {
            f"{left}*{right}": float(value)
            for (left, right), value in zip(
                interactions,
                selected["parameter_interaction_coefficients"],
            )
        },
        "parameter_interaction_order": [
            [left, right] for left, right in interactions
        ],
        "parameter_plan_interaction_coefficients": {
            name: float(value)
            for name, value in zip(
                active_params,
                selected["parameter_plan_interaction_coefficients"],
            )
        },
        "parameter_feature_contract": "resource_response_surface_v2",
        "plan_log_ratio_coefficient": float(
            selected["plan_log_ratio_coefficient"]
        ),
        "plan_log_ratio_squared_coefficient": float(
            selected["plan_log_ratio_squared_coefficient"]
        ),
        "candidate_audit": [
            {
                key: copy.deepcopy(value)
                for key, value in candidate.items()
                if key != "holdout_prediction"
            }
            for candidate in candidates
        ],
        "independent_holdout_used_for_model_selection": False,
        "historical_validation_used_for_model_selection": historical_selection,
        "qps_used": False,
        "zero_intercept": True,
        "baseline_identity_preserved": True,
        "parameter_support_domain": support_domain,
        "parameter_support_abstention_enabled": bool(
            enable_parameter_support_abstention
        ),
        "parameter_support_contract": (
            PARAMETER_SUPPORT_CONTRACT
            if enable_parameter_support_abstention
            else None
        ),
        "historical_validation_support_assessments": (
            historical_support_assessments
        ),
    }
    metrics = prediction_metrics(
        y_holdout,
        holdout_prediction,
        prediction_error_margin_log_ratio=float(
            selected["training_loo_mae_log_ratio"]
        ),
    )
    metrics["parameter_support"] = {
        "enabled": bool(enable_parameter_support_abstention),
        "contract": (
            PARAMETER_SUPPORT_CONTRACT
            if enable_parameter_support_abstention
            else None
        ),
        "assessments": support_assessments,
        "supported_count": sum(
            assessment.get("supported") is True
            for assessment in support_assessments
        ),
        "support_abstain_count": sum(
            assessment.get("support_abstained") is True
            for assessment in support_assessments
        ),
        "holdout_outcomes_used_for_support": False,
        "qps_used": False,
    }
    return calibration, metrics


def target_selection_admission_errors(
    target_metrics: Mapping[str, Any],
    *,
    safety_mode: str = "direct_resource_response",
) -> List[str]:
    """Fail closed on harmful predicted-down choices, not safe false negatives.

    Algorithm C only physically replays candidates predicted to improve the
    bottleneck.  A false positive therefore consumes a slot and can misdirect
    the gradient, while a false negative merely omits a potentially useful
    point.  Ranking, error, confirmed-down count, and predicted-down precision
    remain hard gates; symmetric sign recall remains an explicit diagnostic.
    """
    normalized_mode = str(safety_mode or "direct_resource_response")
    direction = target_metrics.get("direction") or {}
    if normalized_mode == "flat_target_safe_abstention":
        actual = [float(value) for value in target_metrics.get("actual") or []]
        predicted = [
            float(value) for value in target_metrics.get("predicted") or []
        ]
        errors: List[str] = []
        if not actual or max(abs(value) for value in actual) >= math.log(1.01):
            errors.append("flat_target_holdout_not_flat")
        if any(abs(value) > 1e-12 for value in predicted):
            errors.append("flat_target_abstention_predicted_nonzero")
        return errors
    if normalized_mode == "irreducible_noise_safe_abstention":
        predicted = [
            float(value) for value in target_metrics.get("predicted") or []
        ]
        errors = []
        if any(abs(value) > 1e-12 for value in predicted):
            errors.append("irreducible_noise_abstention_predicted_nonzero")
        if int(direction.get("point_predicted_target_down_count") or 0) != 0:
            errors.append("irreducible_noise_abstention_claimed_target_down")
        return errors
    if normalized_mode == "monotone_target_down_distance":
        material_count = int(direction.get("material_sign_evaluable_count") or 0)
        actual_down = int(direction.get("actual_target_down_count") or 0)
        actual = [float(value) for value in target_metrics.get("actual") or []]
        predicted = [
            float(value) for value in target_metrics.get("predicted") or []
        ]
        predicted_positions = [
            index for index, value in enumerate(predicted) if value < -1e-12
        ]
        nonincrease_count = sum(
            actual[index] <= 0.0 for index in predicted_positions
        )
        nonincrease_precision = (
            nonincrease_count / len(predicted_positions)
            if predicted_positions
            else 0.0
        )
        errors = []
        if material_count < MINIMUM_MATERIAL_SIGN_COUNT:
            errors.append("monotone_holdout_material_sign_count_below_10")
        if material_count <= 0 or actual_down / material_count < 0.80:
            errors.append("monotone_holdout_target_down_fraction_below_0p80")
        if len(predicted_positions) < 2:
            errors.append("monotone_holdout_predicted_down_count_below_2")
        if nonincrease_count < 2:
            errors.append("monotone_holdout_confirmed_down_count_below_2")
        if nonincrease_precision < 0.80:
            errors.append(
                "monotone_holdout_actual_nonincrease_precision_below_0p80"
            )
        if float(target_metrics.get("mae_log_ratio") or math.inf) > MAXIMUM_TARGET_MAE_LOG_RATIO:
            errors.append("monotone_holdout_mae_above_10pct_log_ratio")
        return errors

    errors = []
    target_spearman = target_metrics.get("spearman")
    # np.corrcoef may represent an exact boundary one ULP below
    # the decimal threshold.  Keep the semantic boundary inclusive.
    if (
        target_spearman is None
        or float(target_spearman) + 1e-12
        < MINIMUM_TARGET_HOLDOUT_SPEARMAN
    ):
        errors.append("target_holdout_spearman_below_0p50")
    if (
        float(target_metrics.get("mae_log_ratio") or math.inf)
        > MAXIMUM_TARGET_MAE_LOG_RATIO
    ):
        errors.append("target_holdout_mae_above_10pct_log_ratio")
    if (
        int(direction.get("material_sign_evaluable_count") or 0)
        < MINIMUM_MATERIAL_SIGN_COUNT
    ):
        errors.append("target_holdout_material_sign_count_below_10")
    if (
        float(direction.get("sign_accuracy") or 0.0) + 1e-12
        < MINIMUM_SIGN_ACCURACY
    ):
        errors.append("target_holdout_sign_accuracy_below_0p75")
    if int(direction.get("actual_target_down_count") or 0) < 2:
        errors.append("target_holdout_actual_down_count_below_2")
    # The training LOO MAE is an empirical ranking/error diagnostic, not a
    # calibrated per-candidate confidence bound.  Subtracting it as a worst
    # case envelope can turn every correctly selected target-down point into
    # an "uncertain" point and deadlock formal admission.  The independent
    # holdout can directly and safely test the property that matters here:
    # points selected as target-down must actually be target-down.  Keep the
    # robust/error-margin fields for confidence and score penalties, while the
    # hard gate uses point selection count, confirmation count, and precision.
    if int(direction.get("point_predicted_target_down_count") or 0) < 2:
        errors.append("target_holdout_point_predicted_down_count_below_2")
    if int(direction.get("point_confirmed_target_down_count") or 0) < 2:
        errors.append("target_holdout_point_confirmed_down_count_below_2")
    if float(direction.get("point_predicted_target_down_precision") or 0.0) < 0.80:
        errors.append("target_holdout_point_predicted_down_precision_below_0p80")
    return errors


def scored_resource_admission_errors(
    resource: str,
    metrics: Mapping[str, Any],
    *,
    require_symmetric_sign_accuracy: bool = True,
) -> List[str]:
    """Return independent-holdout diagnostics for one scored resource.

    The target resource diagnostics are formal admission gates.  Non-target
    diagnostics remain available for confidence and candidate-score penalties,
    while actual non-target utilization is protected by the replay safety
    gates.  The final holdout is never refit into the predictor.
    """
    normalized = str(resource).strip().lower()
    if normalized not in RESOURCE_NAMES:
        raise ValueError(f"unsupported scored resource: {resource!r}")
    errors: List[str] = []
    spearman = metrics.get("spearman")
    minimum_spearman = (
        MINIMUM_TARGET_HOLDOUT_SPEARMAN
        if not require_symmetric_sign_accuracy
        else 0.10
    )
    if spearman is None or float(spearman) + 1e-12 < minimum_spearman:
        errors.append(
            f"{normalized}_holdout_spearman_below_"
            f"{'0p50' if minimum_spearman == 0.50 else '0p10'}"
        )
    maximum_mae = (
        MAXIMUM_NON_TARGET_MAE_LOG_RATIO
        if require_symmetric_sign_accuracy
        else MAXIMUM_TARGET_MAE_LOG_RATIO
    )
    if float(metrics.get("mae_log_ratio") or math.inf) > maximum_mae:
        errors.append(
            f"{normalized}_holdout_mae_above_"
            f"{'15pct' if require_symmetric_sign_accuracy else '10pct'}_log_ratio"
        )
    direction = metrics.get("direction") or {}
    if (
        int(direction.get("material_sign_evaluable_count") or 0)
        < MINIMUM_MATERIAL_SIGN_COUNT
    ):
        errors.append(f"{normalized}_holdout_material_sign_count_below_10")
    if (
        require_symmetric_sign_accuracy
        and float(direction.get("sign_accuracy") or 0.0) + 1e-12
        < MINIMUM_SIGN_ACCURACY
    ):
        errors.append(f"{normalized}_holdout_sign_accuracy_below_0p75")
    return errors


def partition_scored_resource_admission_errors(
    scored_resource_errors: Mapping[str, Sequence[str]],
    *,
    target_resource: str,
) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    """Split target hard blockers from non-target soft diagnostics."""

    normalized_target = str(target_resource).strip().lower()
    if normalized_target not in scored_resource_errors:
        raise ValueError(
            f"target resource missing from scored errors: {normalized_target!r}"
        )
    blocking = {
        normalized_target: list(scored_resource_errors[normalized_target]),
    }
    diagnostic = {
        str(resource): list(errors)
        for resource, errors in scored_resource_errors.items()
        if str(resource) != normalized_target
    }
    return blocking, diagnostic


def calibration_sample_count_admission_errors(
    train_count: int,
    holdout_count: int,
) -> List[str]:
    """Enforce the authoritative fresh-T 10-train/5-holdout contract."""

    errors: List[str] = []
    if int(train_count) < MINIMUM_TRAIN_COUNT:
        errors.append(f"train_count_below_{MINIMUM_TRAIN_COUNT}")
    if int(holdout_count) < MINIMUM_INDEPENDENT_HOLDOUT_COUNT:
        errors.append(
            "holdout_count_below_"
            f"{MINIMUM_INDEPENDENT_HOLDOUT_COUNT}"
        )
    return errors


def build_artifact(
    *,
    samples: Sequence[Dict[str, Any]],
    active_params: Sequence[str],
    benchmark: str,
    target_resource: str,
    workload_contract: Mapping[str, Any],
    source_audit: Mapping[str, Any],
    alpha: float,
    scored_resources: Sequence[str] = RESOURCE_NAMES,
    enable_target_support_abstention: bool = False,
    holdout_source_algorithm: str = "algorithm_c",
) -> Dict[str, Any]:
    def sample_has_complete_plan_prediction(item: Mapping[str, Any]) -> bool:
        plan_prediction = item.get("plan_prediction")
        ratios = (
            plan_prediction.get("predicted_log_ratios")
            if isinstance(plan_prediction, Mapping)
            else None
        )
        return bool(
            isinstance(ratios, Mapping)
            and all(
                isinstance(ratios.get(resource), (int, float))
                and math.isfinite(float(ratios[resource]))
                for resource in RESOURCE_NAMES
            )
        )

    all_samples = list(samples)
    plan_complete_samples = [
        item for item in all_samples if sample_has_complete_plan_prediction(item)
    ]
    plan_incomplete_count = len(all_samples) - len(plan_complete_samples)
    minimum_sample_count = (
        MINIMUM_TRAIN_COUNT + MINIMUM_INDEPENDENT_HOLDOUT_COUNT
    )
    if len(plan_complete_samples) >= minimum_sample_count:
        samples = plan_complete_samples
    else:
        samples = all_samples
    source_audit = copy.deepcopy(dict(source_audit))
    source_audit["plan_incomplete_observation_count"] = plan_incomplete_count
    source_audit["plan_incomplete_excluded_from_enhanced_model"] = bool(
        plan_incomplete_count and samples is plan_complete_samples
    )
    source_audit["training_source_counts"] = {
        algorithm: sum(
            str(item.get("training_source_algorithm") or "algorithm_c")
            == algorithm
            for item in samples
        )
        for algorithm in ("algorithm_a", "algorithm_b", "algorithm_c")
    }
    if len(samples) < minimum_sample_count:
        raise RuntimeError(
            "current-scope T requires at least "
            f"{minimum_sample_count} valid physical observations, "
            f"got {len(samples)}"
        )
    # A physical replay round is the atomic split unit.  ``round_index`` is
    # only lane/campaign local and can collide (or be offset) when immutable
    # histories are combined.  The exporter therefore supplies an
    # authoritative holdout group.  Never split one such group across fit,
    # model selection, and final holdout: doing so would let observations from
    # the same two-worker physical wave leak into both sides of validation.
    def sample_group(item: Mapping[str, Any]) -> str:
        value = str(item.get("holdout_group") or "").strip()
        if value:
            return value
        return (
            f"{item.get('source_path')}::round::"
            f"{int(item.get('round_index') or 0)}"
        )

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    group_order: List[str] = []
    for item in samples:
        group = sample_group(item)
        if group not in grouped:
            grouped[group] = []
            group_order.append(group)
        grouped[group].append(item)

    normalized_holdout_source = str(holdout_source_algorithm).strip().lower()
    if normalized_holdout_source not in {
        "algorithm_a",
        "algorithm_b",
        "algorithm_c",
    }:
        raise ValueError("invalid holdout source algorithm")
    holdout_groups: List[str] = []
    holdout_count = 0
    for group in reversed(group_order):
        group_sources = {
            str(item.get("training_source_algorithm") or "algorithm_c")
            for item in grouped[group]
        }
        if group_sources != {normalized_holdout_source}:
            continue
        holdout_groups.append(group)
        holdout_count += len(grouped[group])
        if holdout_count >= MINIMUM_INDEPENDENT_HOLDOUT_COUNT:
            break
    holdout_group_set = set(holdout_groups)
    train = [item for item in samples if sample_group(item) not in holdout_group_set]
    holdout = [item for item in samples if sample_group(item) in holdout_group_set]
    holdout_rounds = sorted({int(item["round_index"]) for item in holdout})
    if (
        len(train) < MINIMUM_TRAIN_COUNT
        or len(holdout) < MINIMUM_INDEPENDENT_HOLDOUT_COUNT
    ):
        raise RuntimeError(
            "independent runtime-domain grouped holdout is insufficient; "
            "cross-source positional fallback is forbidden: "
            f"source={normalized_holdout_source} train={len(train)} "
            f"holdout={len(holdout)}"
        )

    # Use the newest pre-holdout round for model-family/alpha selection and
    # reserve the newest overall round exclusively for final admission.  Once
    # a round has served as historical validation it may be refit into the
    # deployed pre-holdout model; the final holdout remains untouched.
    train_group_order = list(dict.fromkeys(sample_group(item) for item in train))
    model_validation_groups: List[str] = []
    model_validation_count = 0
    for group in reversed(train_group_order):
        group_sources = {
            str(item.get("training_source_algorithm") or "algorithm_c")
            for item in grouped[group]
        }
        if group_sources != {normalized_holdout_source}:
            continue
        model_validation_groups.append(group)
        model_validation_count += sum(
            sample_group(item) == group for item in train
        )
        if model_validation_count >= MINIMUM_INDEPENDENT_HOLDOUT_COUNT:
            break
    model_validation_group_set = set(model_validation_groups)
    model_fit = [
        item
        for item in train
        if sample_group(item) not in model_validation_group_set
    ]
    model_validation = [
        item
        for item in train
        if sample_group(item) in model_validation_group_set
    ]
    model_validation_rounds = sorted(
        {int(item["round_index"]) for item in model_validation}
    )
    historical_model_selection_available = bool(
        len(model_fit) >= MINIMUM_TRAIN_COUNT
        and len(model_validation) >= MINIMUM_INDEPENDENT_HOLDOUT_COUNT
    )

    x_train = np.asarray([item["x"] for item in train], dtype=float)
    x_holdout = np.asarray([item["x"] for item in holdout], dtype=float)
    train_sample_weights = grouped_replay_sample_weights(train)
    target_y_train = np.asarray(
        [item["outcomes"][target_resource] for item in train],
        dtype=float,
    )
    if historical_model_selection_available:
        x_model_fit = np.asarray([item["x"] for item in model_fit], dtype=float)
        model_fit_sample_weights = grouped_replay_sample_weights(model_fit)
        x_model_validation = np.asarray(
            [item["x"] for item in model_validation],
            dtype=float,
        )
        alpha_selection = select_ridge_alpha_by_historical_validation(
            x_model_fit,
            np.asarray(
                [item["outcomes"][target_resource] for item in model_fit],
                dtype=float,
            ),
            x_model_validation,
            np.asarray(
                [item["outcomes"][target_resource] for item in model_validation],
                dtype=float,
            ),
            requested_alpha=alpha,
            fit_sample_weights=model_fit_sample_weights,
        )
    else:
        x_model_fit = None
        x_model_validation = None
        model_fit_sample_weights = None
        alpha_selection = select_ridge_alpha_by_training_loo(
            x_train,
            target_y_train,
            requested_alpha=alpha,
            sample_weights=train_sample_weights,
        )
    effective_alpha = float(alpha_selection["selected_alpha"])
    coefficients: Dict[str, np.ndarray] = {}
    parameter_model_metrics: Dict[str, Any] = {}
    parameter_model_error_margins: Dict[str, float] = {}
    for outcome in RESOURCE_NAMES:
        y_train = np.asarray([item["outcomes"][outcome] for item in train], dtype=float)
        y_holdout = np.asarray([item["outcomes"][outcome] for item in holdout], dtype=float)
        coef = ridge_fit(
            x_train,
            y_train,
            effective_alpha,
            train_sample_weights,
        )
        coefficients[outcome] = coef
        predicted = x_holdout @ coef
        error_margin = 0.0
        if outcome in RESOURCE_NAMES:
            if historical_model_selection_available:
                assert x_model_fit is not None
                validation_y = np.asarray(
                    [item["outcomes"][outcome] for item in model_validation],
                    dtype=float,
                )
                fit_y = np.asarray(
                    [item["outcomes"][outcome] for item in model_fit],
                    dtype=float,
                )
                validation_coefficient = ridge_fit(
                    x_model_fit,
                    fit_y,
                    effective_alpha,
                    model_fit_sample_weights,
                )
                error_margin = float(
                    np.mean(
                        np.abs(
                            x_model_validation @ validation_coefficient
                            - validation_y
                        )
                    )
                )
            else:
                loo_predictions: List[float] = []
                for omitted in range(x_train.shape[0]):
                    keep = np.arange(x_train.shape[0]) != omitted
                    loo_coefficient = ridge_fit(
                        x_train[keep],
                        y_train[keep],
                        effective_alpha,
                        train_sample_weights[keep],
                    )
                    loo_predictions.append(
                        float(x_train[omitted] @ loo_coefficient)
                    )
                error_margin = float(
                    np.mean(
                        np.abs(
                            np.asarray(loo_predictions, dtype=float) - y_train
                        )
                    )
                )
            parameter_model_error_margins[outcome] = error_margin
        parameter_model_metrics[outcome] = prediction_metrics(
            y_holdout,
            predicted,
            prediction_error_margin_log_ratio=error_margin,
        )

    plan_prediction_complete = all(
        isinstance(item.get("plan_prediction"), Mapping)
        and isinstance(
            (item.get("plan_prediction") or {}).get("predicted_log_ratios"),
            Mapping,
        )
        and all(
            isinstance(
                ((item.get("plan_prediction") or {}).get("predicted_log_ratios") or {}).get(
                    resource
                ),
                (int, float),
            )
            for resource in RESOURCE_NAMES
        )
        for item in samples
    )
    selection_metrics = copy.deepcopy(parameter_model_metrics)
    selection_predictor_contract = "parameter_log_delta_ridge_v1"
    prediction_calibration: Dict[str, Any] = {
        "contract": "training_loo_selected_plan_parameter_hybrid_v1",
        "active_params": list(active_params),
        "selection_scope": (
            "historical_round_validation"
            if historical_model_selection_available
            else "training_only"
        ),
        "model_selection_method": (
            "historical_round_validation_resource_rank_error_direction_v2"
            if historical_model_selection_available
            else "training_only_leave_one_out_target_resource_mae_v1"
        ),
        "model_fit_rounds": sorted(
            {int(item["round_index"]) for item in model_fit}
        ),
        "model_fit_groups": list(
            dict.fromkeys(sample_group(item) for item in model_fit)
        ),
        "model_validation_rounds": (
            model_validation_rounds
            if historical_model_selection_available
            else []
        ),
        "model_validation_groups": (
            model_validation_groups
            if historical_model_selection_available
            else []
        ),
        "final_holdout_rounds": holdout_rounds,
        "final_holdout_groups": holdout_groups,
        "independent_holdout_used_for_model_selection": False,
        "qps_used": False,
        "zero_intercept": True,
        "baseline_identity_preserved": True,
        "resources": {},
    }
    if plan_prediction_complete:
        selection_metrics = {}
        for resource in RESOURCE_NAMES:
            plan_train = np.asarray(
                [
                    float(item["plan_prediction"]["predicted_log_ratios"][resource])
                    for item in train
                ],
                dtype=float,
            )
            plan_holdout = np.asarray(
                [
                    float(item["plan_prediction"]["predicted_log_ratios"][resource])
                    for item in holdout
                ],
                dtype=float,
            )
            resource_calibration, resource_metrics = fit_resource_prediction_calibration(
                x_train=x_train,
                x_holdout=x_holdout,
                y_train=np.asarray(
                    [item["outcomes"][resource] for item in train],
                    dtype=float,
                ),
                y_holdout=np.asarray(
                    [item["outcomes"][resource] for item in holdout],
                    dtype=float,
                ),
                plan_train=plan_train,
                plan_holdout=plan_holdout,
                active_params=active_params,
                requested_alpha=alpha,
                train_sample_weights=train_sample_weights,
                x_model_fit=x_model_fit,
                x_model_validation=x_model_validation,
                y_model_fit=(
                    np.asarray(
                        [item["outcomes"][resource] for item in model_fit],
                        dtype=float,
                    )
                    if historical_model_selection_available
                    else None
                ),
                y_model_validation=(
                    np.asarray(
                        [
                            item["outcomes"][resource]
                            for item in model_validation
                        ],
                        dtype=float,
                    )
                    if historical_model_selection_available
                    else None
                ),
                plan_model_fit=(
                    np.asarray(
                        [
                            float(
                                item["plan_prediction"]["predicted_log_ratios"][
                                    resource
                                ]
                            )
                            for item in model_fit
                        ],
                        dtype=float,
                    )
                    if historical_model_selection_available
                    else None
                ),
                plan_model_validation=(
                    np.asarray(
                        [
                            float(
                                item["plan_prediction"]["predicted_log_ratios"][
                                    resource
                                ]
                            )
                            for item in model_validation
                        ],
                        dtype=float,
                    )
                    if historical_model_selection_available
                    else None
                ),
                model_fit_sample_weights=model_fit_sample_weights,
                enable_parameter_support_abstention=(
                    enable_target_support_abstention
                    and resource == target_resource
                ),
                target_resource_prediction=(resource == target_resource),
            )
            selection_metrics[resource] = resource_metrics
            prediction_calibration["resources"][resource] = resource_calibration
        selection_predictor_contract = (
            "training_loo_selected_plan_parameter_hybrid_v1"
        )
    else:
        for resource in RESOURCE_NAMES:
            coefficient = coefficients[resource]
            prediction_calibration["resources"][resource] = (
                parameter_only_prediction_calibration(
                    resource=resource,
                    target_resource=target_resource,
                    coefficient=coefficient,
                    active_params=active_params,
                    x_train=x_train,
                    training_loo_mae_log_ratio=parameter_model_error_margins[
                        resource
                    ],
                    alpha=effective_alpha,
                    enable_target_support_abstention=(
                        enable_target_support_abstention
                    ),
                )
            )

    training_design = design_matrix_metrics(x_train)
    executor_telemetry_evidence = {
        "required": True,
        "sample_count": sum(
            bool(item.get("executor_telemetry")) for item in samples
        ),
        "train_count": sum(
            bool(item.get("executor_telemetry")) for item in train
        ),
        "holdout_count": sum(
            bool(item.get("executor_telemetry")) for item in holdout
        ),
        "sources": sorted(
            {
                str((item.get("executor_telemetry") or {}).get("source"))
                for item in samples
                if (item.get("executor_telemetry") or {}).get("source")
            }
        ),
        "role": "resource_calibration_evidence_and_diagnostics",
        "direct_model_input": False,
        "qps_used_for_resource_prediction": False,
    }

    loo_coefficients: List[Dict[str, np.ndarray]] = []
    if len(train) >= 6:
        for omitted in range(len(train)):
            keep = [index for index in range(len(train)) if index != omitted]
            loo_x = x_train[keep]
            loo_coefficients.append(
                {
                    resource: ridge_fit(
                        loo_x,
                        np.asarray(
                            [train[index]["outcomes"][resource] for index in keep],
                            dtype=float,
                        ),
                        effective_alpha,
                    )
                    for resource in RESOURCE_NAMES
                }
            )

    table: Dict[str, Dict[str, float]] = {}
    stability: Dict[str, Any] = {}
    for param_index, name in enumerate(active_params):
        effects = {
            resource: float(np.clip(coefficients[resource][param_index], -2.0, 2.0))
            for resource in RESOURCE_NAMES
        }
        signs: List[bool] = []
        target_signs: List[bool] = []
        for resource in RESOURCE_NAMES:
            full = effects[resource]
            for loo in loo_coefficients:
                value = float(loo[resource][param_index])
                if abs(full) <= 1e-12 and abs(value) <= 1e-12:
                    matched = True
                elif abs(full) > 1e-12 and abs(value) > 1e-12:
                    matched = math.copysign(1.0, full) == math.copysign(1.0, value)
                else:
                    matched = False
                signs.append(matched)
                if resource == target_resource:
                    target_signs.append(matched)
        sign_agreement = sum(signs) / len(signs) if signs else 0.5
        target_sign_agreement = (
            sum(target_signs) / len(target_signs) if target_signs else 0.0
        )
        effect_peak = max(abs(value) for value in effects.values())
        confidence = min(1.0, max(0.0, min(sign_agreement, target_sign_agreement)))
        if target_sign_agreement < 0.80:
            confidence = 0.0
        if effect_peak < 1e-4:
            confidence = 0.0
        elif effect_peak < 0.002:
            confidence = min(confidence, 0.5)
        table[name] = {
            **effects,
            "confidence": float(confidence),
            "evidence_count": float(len(train)),
        }
        stability[name] = {
            "leave_one_out_sign_agreement": sign_agreement,
            "target_resource_leave_one_out_sign_agreement": target_sign_agreement,
            "effect_peak": effect_peak,
            "confidence": confidence,
        }

    normalized_scored_resources = tuple(
        dict.fromkeys(str(value).strip().lower() for value in scored_resources)
    )
    if not normalized_scored_resources:
        raise ValueError("at least one scored resource is required")
    unsupported_scored_resources = sorted(
        set(normalized_scored_resources).difference(RESOURCE_NAMES)
    )
    if unsupported_scored_resources:
        raise ValueError(
            f"unsupported scored resources: {unsupported_scored_resources}"
        )
    if target_resource not in normalized_scored_resources:
        raise ValueError("target resource must have a non-zero inner-score weight")

    target_metrics = selection_metrics[target_resource]
    # Prediction quality is a formal-start gate.  A weak model must abstain
    # before consuming physical Top-5 slots; the outer loop remains the sole
    # authority for QPS and real resource accept/reject decisions.
    admission_errors = calibration_sample_count_admission_errors(
        len(train),
        len(holdout),
    )
    target_safety_mode = str(
        (
            (prediction_calibration.get("resources") or {}).get(
                target_resource
            )
            or {}
        ).get("target_prediction_safety_mode")
        or "direct_resource_response"
    )
    target_table_safety_override = target_safety_mode != "direct_resource_response"
    if target_table_safety_override:
        # The experience table feeds Algorithm C's gradient/candidate
        # generation.  An abstaining or unsigned radial target model must not
        # leak the rejected direct ridge coefficients back through g_j.
        for parameter_row in table.values():
            parameter_row[target_resource] = 0.0
    target_selection_diagnostics = target_selection_admission_errors(
        target_metrics,
        safety_mode=target_safety_mode,
    )
    admission_errors.extend(
        f"prediction_quality:target_selection:{error}"
        for error in target_selection_diagnostics
    )
    scored_resource_errors: Dict[str, List[str]] = {}
    for resource in normalized_scored_resources:
        resource_errors = scored_resource_admission_errors(
            resource,
            selection_metrics[resource],
            require_symmetric_sign_accuracy=(resource != target_resource),
        )
        scored_resource_errors[resource] = resource_errors
    (
        quality_blocking_scored_resource_errors,
        non_target_scored_resource_diagnostics,
    ) = partition_scored_resource_admission_errors(
        scored_resource_errors,
        target_resource=target_resource,
    )
    target_scored_resource_diagnostics = list(
        quality_blocking_scored_resource_errors[target_resource]
    )
    blocking_scored_resource_errors = copy.deepcopy(
        quality_blocking_scored_resource_errors
    )
    if target_safety_mode != "direct_resource_response":
        blocking_scored_resource_errors[target_resource] = []
    for resource, errors in blocking_scored_resource_errors.items():
        admission_errors.extend(
            f"prediction_quality:{resource}:{error}" for error in errors
        )
    target_direction = target_metrics.get("direction") or {}
    if training_design["matrix_rank"] < len(active_params):
        admission_errors.append("training_design_rank_below_active_parameter_count")
    if not math.isfinite(float(training_design["scaled_condition_number"])) or float(
        training_design["scaled_condition_number"]
    ) > 1000.0:
        admission_errors.append("training_design_condition_number_above_1e3")
    if all(float(row["confidence"]) <= 0.0 for row in table.values()):
        admission_errors.append("all_parameter_confidences_zero")

    artifact: Dict[str, Any] = {
        # Schema 10 introduces an explicit target-prediction safety mode.
        # Older schema-8/9 artifacts retain their original direct-model gates;
        # the loader must never reinterpret them as abstaining models.
        "schema_version": 11 if enable_target_support_abstention else 10,
        "table_id": f"algorithm_c_{benchmark}_{target_resource}_current_workload_v1",
        "benchmark": benchmark,
        "target_resource": target_resource,
        "scope": dict(workload_contract),
        "active_params": list(active_params),
        "direction_semantics": (
            "T[param][resource] is a same-worker-baseline-normalized ridge "
            "coefficient from log(parameter ratio) to log(resource ratio)"
        ),
        "qps_used_for_inner_scoring": False,
        "qps_role": "outer_real_replay_acceptance_only",
        "training_boundary": training_boundary(
            source_audit.get("training_source_counts") or {}
        ),
        "runtime_candidate_prior_firewall": runtime_prior_firewall(),
        "executor_telemetry_evidence": executor_telemetry_evidence,
        "finite_difference_direction_enabled": False,
        "random_direction_enabled": False,
        "resource_prediction_calibration": prediction_calibration,
        "fit": {
            "model": (
                "zero_intercept_group_balanced_resource_response_ridge_historical_validation_v4"
                if historical_model_selection_available
                else "zero_intercept_group_balanced_resource_response_ridge_training_loo_v3"
            ),
            "alpha": effective_alpha,
            "requested_alpha": alpha,
            "alpha_selection": alpha_selection,
            "sample_count": len(samples),
            "train_count": len(train),
            "holdout_count": len(holdout),
            "holdout_outer_rounds": holdout_rounds,
            "holdout_groups": holdout_groups,
            "holdout_labels": [item["label"] for item in holdout],
            "model_selection_count": len(model_validation),
            "model_selection_outer_rounds": model_validation_rounds,
            "model_selection_groups": (
                model_validation_groups
                if historical_model_selection_available
                else []
            ),
            "model_fit_count": len(model_fit),
            "historical_model_selection_available": (
                historical_model_selection_available
            ),
            "final_holdout_excluded_from_model_selection": True,
            "same_worker_baseline_normalization": True,
            "sample_weighting_contract": (
                "equal_total_weight_per_atomic_physical_replay_group_v1"
            ),
            "row_count_does_not_increase_group_weight": True,
            "source_algorithm_used_as_weight_or_feature": False,
            "qps_or_acceptance_used_as_weight": False,
            "train_sample_weight_sum": float(np.sum(train_sample_weights)),
            "train_sample_weight_min": float(np.min(train_sample_weights)),
            "train_sample_weight_max": float(np.max(train_sample_weights)),
            "source_audit": dict(source_audit),
        },
        "holdout": {
            "metrics": selection_metrics,
            "parameter_model_metrics": parameter_model_metrics,
            "target_resource": target_resource,
            "independent_from_fit": True,
            "selection_predictor_contract": selection_predictor_contract,
            "plan_prediction_complete": plan_prediction_complete,
            "qps_used_for_selection_prediction": False,
            "holdout_source_algorithm": normalized_holdout_source,
            "source_algorithm_used_as_model_feature": False,
        },
        "training_design": training_design,
        "parameter_stability": stability,
        "formal_admission": {
            "ready": not admission_errors,
            "errors": admission_errors,
            "target_prediction_safety_mode": target_safety_mode,
            "target_prediction_abstained": target_safety_mode in {
                "flat_target_safe_abstention",
                "irreducible_noise_safe_abstention",
            },
            "target_prediction_safety_mode_selected_without_final_holdout": bool(
                (
                    (prediction_calibration.get("resources") or {}).get(
                        target_resource
                    )
                    or {}
                ).get("safety_mode_selected_without_final_holdout")
            ),
            "target_prediction_safety_contract": (
                "historical_validation_selected_direct_monotone_or_safe_abstention_v1"
            ),
            "target_gradient_table_neutralized": target_table_safety_override,
            "target_gradient_table_neutralization_reason": (
                "safe_mode_has_no_signed_per_parameter_target_derivative"
                if target_table_safety_override
                else None
            ),
            "scored_resource_admission_contract": (
                "independent_grouped_holdout_resource_quality_v4"
            ),
            "hard_gate_contract": (
                "resource_rank_mae_direction_support_and_design_v2"
            ),
            "scored_resources": list(normalized_scored_resources),
            "scored_resource_errors": scored_resource_errors,
            "blocking_scored_resource_errors": blocking_scored_resource_errors,
            "prediction_quality_diagnostics": {
                "target_selection": target_selection_diagnostics,
                "target_scored_resource": target_scored_resource_diagnostics,
                "all_scored_resources": copy.deepcopy(
                    scored_resource_errors
                ),
            },
            "prediction_quality_blocks_formal_admission": True,
            "prediction_quality_role": (
                "formal_start_gate_confidence_and_ranking"
            ),
            "non_target_scored_resource_diagnostics": (
                non_target_scored_resource_diagnostics
            ),
            "non_target_prediction_quality_role": (
                "diagnostic_confidence_and_candidate_score_only"
            ),
            "non_target_actual_safety_role": (
                "inner_hard_utilization_gate_and_outer_real_replay_reject"
            ),
            "observed_scored_resource_direction": {
                resource: copy.deepcopy(
                    (selection_metrics[resource].get("direction") or {})
                )
                for resource in normalized_scored_resources
            },
            "minimum_scored_resource_holdout_spearman": 0.10,
            "minimum_target_holdout_spearman": (
                MINIMUM_TARGET_HOLDOUT_SPEARMAN
            ),
            "maximum_target_mae_log_ratio": MAXIMUM_TARGET_MAE_LOG_RATIO,
            "maximum_non_target_mae_log_ratio": (
                MAXIMUM_NON_TARGET_MAE_LOG_RATIO
            ),
            "minimum_scored_resource_material_sign_count": (
                MINIMUM_MATERIAL_SIGN_COUNT
            ),
            "minimum_scored_resource_sign_accuracy": MINIMUM_SIGN_ACCURACY,
            "target_symmetric_sign_accuracy_diagnostic_only": False,
            "directional_admission_contract": (
                "independent_holdout_support_abstention_precision_v4"
                if enable_target_support_abstention
                else "independent_holdout_nominal_point_prediction_precision_v3"
            ),
            "symmetric_sign_accuracy_diagnostic_only": False,
            "minimum_train_count": MINIMUM_TRAIN_COUNT,
            "minimum_holdout_count": MINIMUM_INDEPENDENT_HOLDOUT_COUNT,
            # Hot-upgrade bridge for controllers that imported the schema-v4
            # loader before this builder was deployed.  Those loaders require
            # both keys to be present, while schema v5 treats the measurements
            # as diagnostics and fail-closes on selection precision instead.
            "minimum_target_holdout_sign_accuracy": 1e-12,
            "minimum_target_holdout_sign_wilson90_lower": 1e-12,
            "legacy_schema4_loader_compatibility": {
                "nonzero_sentinel_thresholds_only": True,
                "schema5_hard_gates_applied_before_ready": True,
            },
            "minimum_material_target_sign_count": MINIMUM_MATERIAL_SIGN_COUNT,
            "minimum_actual_target_down_count": 2,
            "minimum_point_predicted_target_down_count": 2,
            "minimum_point_confirmed_target_down_count": 2,
            "minimum_point_predicted_target_down_precision": 0.80,
            "prediction_error_margin_role": (
                "confidence_and_candidate_score_penalty_only"
            ),
            "target_parameter_support_abstention_enabled": bool(
                enable_target_support_abstention
            ),
            "target_parameter_support_contract": (
                PARAMETER_SUPPORT_CONTRACT
                if enable_target_support_abstention
                else None
            ),
            "minimum_supported_predicted_target_down_count": 2,
            "required_training_design_rank": len(active_params),
            "maximum_training_design_condition_number": 1000.0,
            "observed_target_direction": target_direction,
            "observed_training_design": training_design,
            "admission_predictor_contract": selection_predictor_contract,
        },
        "table": table,
    }
    artifact["table_sha256"] = canonical_sha256(table)
    artifact["artifact_sha256"] = canonical_sha256(artifact)
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", action="append", required=True, type=Path)
    parser.add_argument(
        "--source-algorithm",
        action="append",
        choices=("algorithm_a", "algorithm_b", "algorithm_c"),
        help=(
            "Audit provenance corresponding positionally to each --history; "
            "never used as a model feature."
        ),
    )
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--target", required=True, choices=RESOURCE_NAMES)
    parser.add_argument("--active-params", required=True)
    parser.add_argument("--workload-contract", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument(
        "--holdout-source-algorithm",
        choices=("algorithm_a", "algorithm_b", "algorithm_c"),
        default="algorithm_c",
        help="Runtime-domain source reserved for the final grouped holdout.",
    )
    parser.add_argument("--allowed-worker", action="append", default=[])
    parser.add_argument("--plan-predictions", type=Path)
    parser.add_argument(
        "--enable-target-support-abstention",
        action="store_true",
        help=(
            "Neutralize target-resource predictions outside the training-only "
            "parameter support domain."
        ),
    )
    parser.add_argument(
        "--scored-resource",
        action="append",
        choices=RESOURCE_NAMES,
        dest="scored_resources",
        help="Resource with a non-zero formal inner-score weight (repeatable).",
    )
    args = parser.parse_args()
    active_params = [value.strip() for value in args.active_params.split(",") if value.strip()]
    if not active_params or len(active_params) != len(set(active_params)):
        raise ValueError("active params must be non-empty and unique")
    for path in args.history:
        if not path.is_file():
            raise FileNotFoundError(path)
    source_algorithms: Dict[str, str] = {}
    if args.source_algorithm:
        if len(args.source_algorithm) != len(args.history):
            raise ValueError(
                "--source-algorithm count must equal --history count"
            )
        source_algorithms = {
            str(path): str(algorithm)
            for path, algorithm in zip(args.history, args.source_algorithm)
        }
    workload_payload = json.loads(
        args.workload_contract.read_text(encoding="utf-8")
    )
    if not isinstance(workload_payload, dict):
        raise ValueError("workload contract must be a JSON object")
    algorithm_config = workload_payload.get("algorithm_c")
    embedded_contract = (
        algorithm_config.get("workload_contract")
        if isinstance(algorithm_config, Mapping)
        else None
    )
    workload_contract = (
        copy.deepcopy(dict(embedded_contract))
        if isinstance(embedded_contract, Mapping)
        else workload_payload
    )
    plan_predictions: Dict[str, Mapping[str, Any]] = {}
    if args.plan_predictions is not None:
        if not args.plan_predictions.is_file():
            raise FileNotFoundError(args.plan_predictions)
        prediction_artifact = json.loads(
            args.plan_predictions.read_text(encoding="utf-8")
        )
        if prediction_artifact.get("contract") != "algorithm_c_pre_replay_plan_prediction_v1":
            raise RuntimeError("plan prediction contract mismatch")
        if prediction_artifact.get("qps_used") is not False:
            raise RuntimeError("plan prediction evidence must exclude QPS")
        if prediction_artifact.get("actual_results_used") is not False:
            raise RuntimeError("plan prediction evidence must exclude actual replay results")
        raw_predictions = prediction_artifact.get("predictions")
        if not isinstance(raw_predictions, list):
            raise RuntimeError("plan prediction evidence rows missing")
        plan_predictions = {
            str(row.get("label") or ""): row
            for row in raw_predictions
            if isinstance(row, Mapping) and row.get("label")
        }
    samples, source_audit = load_samples(
        args.history,
        active_params,
        allowed_workers=args.allowed_worker,
        plan_predictions=plan_predictions,
        source_algorithms=source_algorithms,
    )
    expected_domain = resource_training_domain(workload_contract)
    expected_domain_sha256 = canonical_sha256(expected_domain)
    source_domains = source_audit.get("source_resource_domains")
    source_domains = source_domains if isinstance(source_domains, Mapping) else {}
    missing_domain_paths = [
        str(path) for path in args.history if str(path) not in source_domains
    ]
    mismatched_domain_paths = [
        str(path)
        for path in args.history
        if isinstance(source_domains.get(str(path)), Mapping)
        and str(
            (source_domains.get(str(path)) or {}).get("domain_sha256") or ""
        )
        != expected_domain_sha256
    ]
    if missing_domain_paths or mismatched_domain_paths:
        raise RuntimeError(
            "resource training execution domain mismatch: "
            f"missing={missing_domain_paths} mismatched={mismatched_domain_paths}"
        )
    source_audit["expected_resource_training_domain"] = expected_domain
    source_audit["expected_resource_training_domain_sha256"] = (
        expected_domain_sha256
    )
    source_audit["all_source_resource_domains_match"] = True
    artifact = build_artifact(
        samples=samples,
        active_params=active_params,
        benchmark=args.benchmark,
        target_resource=args.target,
        workload_contract=workload_contract,
        source_audit=source_audit,
        alpha=float(args.alpha),
        scored_resources=tuple(args.scored_resources or RESOURCE_NAMES),
        enable_target_support_abstention=bool(
            args.enable_target_support_abstention
        ),
        holdout_source_algorithm=args.holdout_source_algorithm,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "artifact_sha256": artifact["artifact_sha256"],
                "formal_admission": artifact["formal_admission"],
                "fit": artifact["fit"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

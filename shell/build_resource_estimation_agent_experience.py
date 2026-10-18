#!/usr/bin/env python3
"""Build a fail-open resource-direction experience artifact for an LLM agent.

This builder intentionally excludes QPS from features and labels.  It admits
only historical replays for which incumbent and candidate CPU, memory and IO
measurements can be recovered.  Multi-parameter vectors remain vector-level
observations and are never decomposed into per-parameter causal effects.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / "docs/artifacts/pg_real_replay_direction_audit_20260723.json"
OUTPUT_PATH = ROOT / "docs/artifacts/pg_resource_estimation_agent_experience_20260723.json"
REPORT_PATH = ROOT / "docs/pg_resource_estimation_agent_experience_20260723.md"
RESOURCES = ("cpu", "memory", "io")


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _utilization_pct(validation: Mapping[str, Any], resource: str) -> Optional[float]:
    actual = validation.get("actual_utilization_pct")
    if isinstance(actual, Mapping):
        value = _finite(actual.get(resource))
        if value is not None:
            return value
    utilization = validation.get("utilization_pct")
    if isinstance(utilization, Mapping):
        value = _finite(utilization.get(resource))
        if value is not None:
            return value
    resources = validation.get("resources")
    capability = validation.get("runtime_capability")
    if isinstance(resources, Mapping) and isinstance(capability, Mapping):
        raw = _finite(resources.get(resource))
        cap = _finite(capability.get(resource))
        if raw is not None and cap is not None and cap > 0.0:
            return raw / cap * 100.0
    return None


def _explicit_stability(validation: Mapping[str, Any]) -> Optional[bool]:
    sources = [validation]
    stats = validation.get("resource_stats")
    if isinstance(stats, Mapping):
        sources.append(stats)
    for source in sources:
        for key in ("candidate_measurement_protocol", "window_mean_stability"):
            protocol = source.get(key)
            if isinstance(protocol, Mapping) and "stable" in protocol:
                return protocol.get("stable") is True
    return None


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _direction_counts(values: Iterable[float], epsilon_pp: float = 0.1) -> Dict[str, int]:
    result = Counter()
    for value in values:
        if value < -epsilon_pp:
            result["decrease"] += 1
        elif value > epsilon_pp:
            result["increase"] += 1
        else:
            result["near_zero"] += 1
    return {key: result.get(key, 0) for key in ("decrease", "near_zero", "increase")}


def _plan_evidence(record: Mapping[str, Any]) -> Dict[str, Any]:
    fingerprint = record.get("candidate_plan_signature")
    return {
        "available": bool(fingerprint),
        "candidate_plan_fingerprints": [fingerprint] if fingerprint else [],
        "node_or_cardinality_features_available": False,
        "limitation": (
            "only an opaque fingerprint is available; no node/cardinality features"
            if fingerprint
            else "no historical plan tree, fingerprint, node or cardinality feature was logged"
        ),
    }


def _walk_plan_nodes(plan: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(plan, list):
        for item in plan:
            yield from _walk_plan_nodes(item)
    elif isinstance(plan, Mapping):
        if "Node Type" in plan:
            yield plan
        for child in plan.get("Plans", []) if isinstance(plan.get("Plans"), list) else []:
            yield from _walk_plan_nodes(child)


def _capture_features(path_value: Any) -> Optional[Dict[str, Any]]:
    path = Path(str(path_value or ""))
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    items = document.get("items") if isinstance(document, Mapping) else None
    if not isinstance(items, list):
        return None
    plan_keys: List[Tuple[str, str]] = []
    node_counts = Counter()
    plan_rows_sum = 0.0
    plan_width_sum = 0.0
    total_cost_sum = 0.0
    raw_plan_item_count = 0
    for item in items:
        if not isinstance(item, Mapping):
            continue
        queryid = str(item.get("queryid") or "")
        plan_hash = str(item.get("plan_hash") or "")
        if queryid or plan_hash:
            plan_keys.append((queryid, plan_hash))
        raw_plan = item.get("raw_plan_json")
        if raw_plan is not None:
            raw_plan_item_count += 1
        for node in _walk_plan_nodes(raw_plan):
            node_counts[str(node.get("Node Type") or "unknown")] += 1
            plan_rows_sum += _finite(node.get("Plan Rows")) or 0.0
            plan_width_sum += _finite(node.get("Plan Width")) or 0.0
            total_cost_sum += _finite(node.get("Total Cost")) or 0.0
    token = json.dumps(sorted(plan_keys), ensure_ascii=False, separators=(",", ":"))
    return {
        "source_path": str(path),
        "workload_plan_set_fingerprint": hashlib.sha256(token.encode("utf-8")).hexdigest(),
        "query_plan_count": len(plan_keys),
        "unique_query_plan_count": len(set(plan_keys)),
        "raw_plan_item_count": raw_plan_item_count,
        "node_type_counts": dict(sorted(node_counts.items())),
        "plan_rows_sum_proxy": plan_rows_sum,
        "plan_width_sum_proxy": plan_width_sum,
        "total_cost_sum_proxy": total_cost_sum,
        "actual_analyze_features_available": False,
    }


def _optimizer_plan_evidence(
    record: Mapping[str, Any],
    validation: Mapping[str, Any],
) -> Dict[str, Any]:
    summary = validation.get("plan_change_summary")
    if not isinstance(summary, Mapping):
        return _plan_evidence(record)
    baseline = _capture_features(summary.get("baseline_plan_capture_file"))
    candidate = _capture_features(summary.get("final_plan_capture_file"))
    delta = None
    if baseline and candidate:
        node_types = set(baseline["node_type_counts"]) | set(candidate["node_type_counts"])
        delta = {
            "node_type_count_delta": {
                key: candidate["node_type_counts"].get(key, 0) - baseline["node_type_counts"].get(key, 0)
                for key in sorted(node_types)
            },
            "plan_rows_sum_proxy_delta": candidate["plan_rows_sum_proxy"] - baseline["plan_rows_sum_proxy"],
            "plan_width_sum_proxy_delta": candidate["plan_width_sum_proxy"] - baseline["plan_width_sum_proxy"],
            "total_cost_sum_proxy_delta": candidate["total_cost_sum_proxy"] - baseline["total_cost_sum_proxy"],
        }
    opaque = record.get("candidate_plan_signature")
    return {
        "available": bool(candidate),
        "candidate_plan_fingerprints": (
            [candidate["workload_plan_set_fingerprint"]] if candidate else ([opaque] if opaque else [])
        ),
        "opaque_changed_plan_signature": opaque,
        "node_or_cardinality_features_available": bool(candidate),
        "baseline_capture": baseline,
        "candidate_capture": candidate,
        "plan_feature_delta": delta,
        "changed_plan_count": int(summary.get("changed_plan_count") or 0),
        "limitation": (
            "EXPLAIN COSTS only: no Actual Rows/Loops/Time/Buffers; workload resource label is aggregate"
            if candidate
            else "plan capture path missing or unreadable"
        ),
    }


def _fresh_observations(audit: Mapping[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    observations: List[Dict[str, Any]] = []
    file_lines: Dict[Path, List[str]] = {}
    baselines: Dict[Path, Mapping[str, Any]] = {}
    rejected = Counter()
    for record in audit.get("records", []):
        if not str(record.get("evidence_kind", "")).startswith("fresh_"):
            continue
        source_path = Path(str(record["source_path"]))
        try:
            if source_path not in file_lines:
                file_lines[source_path] = source_path.read_text(encoding="utf-8", errors="replace").splitlines()
                baselines[source_path] = json.loads(
                    source_path.with_name("fresh_baseline.json").read_text(encoding="utf-8", errors="replace")
                )
            event = json.loads(file_lines[source_path][int(record["source_line"]) - 1])
        except (OSError, ValueError, IndexError, json.JSONDecodeError):
            rejected["source_unreadable_or_invalid"] += 1
            continue
        baseline = baselines[source_path]
        baseline_raw = baseline.get("raw")
        candidate_raw = event.get("raw_result")
        if not isinstance(baseline_raw, Mapping) or not isinstance(candidate_raw, Mapping):
            rejected["missing_raw_pair"] += 1
            continue
        before = {resource: _utilization_pct(baseline_raw, resource) for resource in RESOURCES}
        after = {resource: _utilization_pct(candidate_raw, resource) for resource in RESOURCES}
        if any(before[item] is None or after[item] is None for item in RESOURCES):
            rejected["incomplete_three_resource_label"] += 1
            continue
        parameter_log_delta = {
            str(key): float(value) for key, value in record.get("parameter_log_delta", {}).items()
        }
        phase = str(event.get("phase") or "fresh_calibration")
        baseline_stability = _explicit_stability(baseline_raw)
        candidate_stability = _explicit_stability(candidate_raw)
        if baseline_stability is True and candidate_stability is True:
            stability_class = "explicit_cross_window_stable"
        elif baseline_stability is False or candidate_stability is False:
            stability_class = "explicit_unstable"
        else:
            stability_class = "usable_monitor_only_no_explicit_cross_window_stability"
        observations.append({
            "sample_id": record["sample_id"],
            "source_path": str(source_path),
            "source_line": int(record["source_line"]),
            "workload": record["workload"],
            "target_resource": record["target_resource"],
            "scenario": record["scenario"],
            "capability": dict(record.get("resource_profile", {})),
            "incumbent_scope_id": record["scope_id"],
            "stability_class": stability_class,
            "evidence_kind": record["evidence_kind"],
            "phase": phase,
            "changed_parameter_count": int(record["changed_parameter_count"]),
            "parameter_attribution_eligible": bool(record["parameter_attribution_eligible"]),
            "parameter_log_delta": parameter_log_delta,
            "parameter_direction_mask": {
                key: (1 if value > 0.0 else -1) for key, value in parameter_log_delta.items()
            },
            "incumbent_utilization_pct": {key: float(before[key]) for key in RESOURCES},
            "candidate_utilization_pct": {key: float(after[key]) for key in RESOURCES},
            "resource_delta_pp": {key: float(after[key] - before[key]) for key in RESOURCES},
            "plan_evidence": _plan_evidence(record),
        })
    return observations, dict(rejected)


def _optimizer_observations(audit: Mapping[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    observations: List[Dict[str, Any]] = []
    file_lines: Dict[Path, List[str]] = {}
    rejected = Counter()
    for record in audit.get("records", []):
        if record.get("evidence_kind") != "optimizer_candidate_replay":
            continue
        source_path = Path(str(record["source_path"]))
        try:
            if source_path not in file_lines:
                file_lines[source_path] = source_path.read_text(encoding="utf-8", errors="replace").splitlines()
            event = json.loads(file_lines[source_path][int(record["source_line"]) - 1])
            position = int(str(record["sample_id"]).rsplit(":", 1)[-1]) - 1
            candidate = event["candidate_evaluations"][position]
        except (OSError, ValueError, IndexError, KeyError, TypeError, json.JSONDecodeError):
            rejected["source_unreadable_or_candidate_position_invalid"] += 1
            continue
        incumbent_raw = event.get("final_validation")
        candidate_raw = candidate.get("candidate_final_validation") if isinstance(candidate, Mapping) else None
        if not isinstance(incumbent_raw, Mapping) or not isinstance(candidate_raw, Mapping):
            rejected["missing_raw_pair"] += 1
            continue
        before = {resource: _utilization_pct(incumbent_raw, resource) for resource in RESOURCES}
        after = {resource: _utilization_pct(candidate_raw, resource) for resource in RESOURCES}
        if any(before[item] is None or after[item] is None for item in RESOURCES):
            rejected["incomplete_three_resource_label"] += 1
            continue
        baseline_stability = _explicit_stability(incumbent_raw)
        candidate_stability = _explicit_stability(candidate_raw)
        if baseline_stability is True and candidate_stability is True:
            stability_class = "explicit_cross_window_stable"
        elif baseline_stability is False or candidate_stability is False:
            rejected["explicit_unstable"] += 1
            continue
        else:
            stability_class = "usable_monitor_only_no_explicit_cross_window_stability"
        parameter_log_delta = {
            str(key): float(value) for key, value in record.get("parameter_log_delta", {}).items()
        }
        observations.append({
            "sample_id": record["sample_id"],
            "source_path": str(source_path),
            "source_line": int(record["source_line"]),
            "workload": record["workload"],
            "target_resource": record["target_resource"],
            "scenario": record["scenario"],
            "capability": dict(record.get("resource_profile", {})),
            "incumbent_scope_id": record["scope_id"],
            "stability_class": stability_class,
            "evidence_kind": record["evidence_kind"],
            "phase": "optimizer_candidate_real_replay",
            "changed_parameter_count": int(record["changed_parameter_count"]),
            "parameter_attribution_eligible": bool(record["parameter_attribution_eligible"]),
            "parameter_log_delta": parameter_log_delta,
            "parameter_direction_mask": {
                key: (1 if value > 0.0 else -1) for key, value in parameter_log_delta.items()
            },
            "incumbent_utilization_pct": {key: float(before[key]) for key in RESOURCES},
            "candidate_utilization_pct": {key: float(after[key]) for key in RESOURCES},
            "resource_delta_pp": {key: float(after[key] - before[key]) for key in RESOURCES},
            "plan_evidence": _optimizer_plan_evidence(record, candidate_raw),
        })
    return observations, dict(rejected)


def _group_key(row: Mapping[str, Any]) -> Tuple[Any, ...]:
    direction_mask = tuple(sorted(row["parameter_direction_mask"].items()))
    plan_fingerprints = tuple(sorted(row["plan_evidence"].get("candidate_plan_fingerprints", [])))
    return (
        row["workload"],
        row["target_resource"],
        row["scenario"],
        json.dumps(row["capability"], sort_keys=True, separators=(",", ":")),
        row["stability_class"],
        row["incumbent_scope_id"],
        row["evidence_kind"],
        row["phase"],
        direction_mask,
        plan_fingerprints,
    )


def _aggregate_experiences(observations: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[Any, ...], List[Mapping[str, Any]]] = defaultdict(list)
    for row in observations:
        grouped[_group_key(row)].append(row)
    experiences: List[Dict[str, Any]] = []
    for index, (_, rows) in enumerate(sorted(grouped.items(), key=lambda item: str(item[0])), start=1):
        first = rows[0]
        parameters = sorted({key for row in rows for key in row["parameter_log_delta"]})
        trigger_delta: Dict[str, Dict[str, float]] = {}
        for parameter in parameters:
            values = [float(row["parameter_log_delta"].get(parameter, 0.0)) for row in rows]
            trigger_delta[parameter] = {
                "min": min(values),
                "p50": statistics.median(values),
                "max": max(values),
            }
        resource_effect: Dict[str, Any] = {}
        sign_consistencies: List[float] = []
        for resource in RESOURCES:
            values = [float(row["resource_delta_pp"][resource]) for row in rows]
            counts = _direction_counts(values)
            dominant_count = max(counts.values())
            sign_consistency = dominant_count / len(values)
            sign_consistencies.append(sign_consistency)
            dominant_direction = max(counts, key=lambda key: counts[key])
            resource_effect[resource] = {
                "unit": "utilization_percentage_points",
                "direction": dominant_direction,
                "observed_delta_pp_interval": {
                    "low": min(values),
                    "p10": _percentile(values, 0.10),
                    "p50": statistics.median(values),
                    "p90": _percentile(values, 0.90),
                    "high": max(values),
                },
                "direction_example_counts": counts,
                "sign_consistency": sign_consistency,
                "predictive_interval_available": False,
                "predictive_interval_limitation": (
                    "no same-scope repeated candidate measurements; observed range is not a calibrated error bound"
                ),
            }
        explicitly_stable = first["stability_class"] == "explicit_cross_window_stable"
        has_plan = any(bool(row["plan_evidence"]["available"]) for row in rows)
        sample_factor = len(rows) / (len(rows) + 3.0)
        heuristic_confidence = min(sign_consistencies) * sample_factor
        if not explicitly_stable:
            heuristic_confidence *= 0.5
        if not has_plan:
            heuristic_confidence *= 0.75
        causal_level = (
            "single_parameter_association_within_incumbent_scope"
            if first["changed_parameter_count"] == 1 and first["parameter_attribution_eligible"]
            else "joint_vector_association_only_no_parameter_decomposition"
        )
        source_refs = sorted({
            f"{row['source_path']}:{row['source_line']}" for row in rows
        })
        plan_feature_examples = [
            {
                "sample_id": row["sample_id"],
                "baseline_capture": row["plan_evidence"].get("baseline_capture"),
                "candidate_capture": row["plan_evidence"].get("candidate_capture"),
                "plan_feature_delta": row["plan_evidence"].get("plan_feature_delta"),
                "changed_plan_count": row["plan_evidence"].get("changed_plan_count"),
            }
            for row in rows
            if row["plan_evidence"].get("node_or_cardinality_features_available")
        ]
        experiences.append({
            "experience_id": f"pg_resource_exp_{index:04d}",
            "isolation_key": {
                "workload": first["workload"],
                "target_resource": first["target_resource"],
                "scenario": first["scenario"],
                "capability": first["capability"],
                "stability_class": first["stability_class"],
                "incumbent_scope_id": first["incumbent_scope_id"],
            },
            "trigger": {
                "parameter_log_delta_range": trigger_delta,
                "parameter_direction_mask": first["parameter_direction_mask"],
                "changed_parameter_count": first["changed_parameter_count"],
                "calibration_phase": first["phase"],
                "plan_condition": {
                    "available": has_plan,
                    "candidate_plan_fingerprints": sorted({
                        value
                        for row in rows
                        for value in row["plan_evidence"]["candidate_plan_fingerprints"]
                    }),
                    "node_or_cardinality_features_available": bool(plan_feature_examples),
                    "plan_feature_examples": plan_feature_examples,
                },
            },
            "resource_effect": resource_effect,
            "sample_count": len(rows),
            "causal_interpretation": causal_level,
            "confidence": {
                "heuristic_0_to_1": heuristic_confidence,
                "grade": "low" if heuristic_confidence < 0.45 else "medium",
                "statistically_calibrated": False,
                "reasons": [
                    "same workload/capability/stability/incumbent scope only",
                    "no explicit cross-window stability evidence" if not explicitly_stable else "explicit stability evidence",
                    "no plan-conditioned evidence" if not has_plan else "opaque plan fingerprint only",
                ],
            },
            "applicable_only_when": [
                "all isolation-key fields match exactly",
                "candidate parameter log-delta direction is compatible",
                "use as a conservative prior, not as a calibrated prediction interval",
            ],
            "forbidden_uses": [
                "cross-workload pooling",
                "cross-capability pooling without a separate validated normalization model",
                "cross-incumbent causal attribution",
                "decomposing a multi-parameter vector into individual parameter effects",
                "pruning a candidate with a new or unknown plan",
                "using QPS as a resource feature or asking the agent to compute the final utility score",
            ],
            "source_refs": source_refs,
        })
    return experiences


def _descriptive_group_summary(observations: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Return an audit summary that is explicitly forbidden for runtime pooling."""
    grouped: Dict[Tuple[str, str], List[Mapping[str, Any]]] = defaultdict(list)
    for row in observations:
        grouped[(str(row["workload"]), str(row["target_resource"]))].append(row)
    result: List[Dict[str, Any]] = []
    for (workload, target), rows in sorted(grouped.items()):
        resource_summary = {}
        for resource in RESOURCES:
            values = [float(row["resource_delta_pp"][resource]) for row in rows]
            resource_summary[resource] = {
                "p10_delta_pp": _percentile(values, 0.10),
                "p50_delta_pp": statistics.median(values),
                "p90_delta_pp": _percentile(values, 0.90),
                "direction_counts": _direction_counts(values),
            }
        result.append({
            "workload": workload,
            "target_resource": target,
            "sample_count": len(rows),
            "single_parameter_association_count": sum(
                bool(row["parameter_attribution_eligible"]) for row in rows
            ),
            "joint_vector_count": sum(
                not bool(row["parameter_attribution_eligible"]) for row in rows
            ),
            "resource_summary": resource_summary,
            "runtime_pooling_allowed": False,
            "warning": "descriptive cross-scope summary only; use isolated experiences at runtime",
        })
    return result


def _baseline_drift_evidence() -> Dict[str, Any]:
    root = Path("/disk2/resource_adaptive/tpcc_direction_calibration_20260723_manual1")
    rows: List[Dict[str, Any]] = []
    for path in sorted(root.glob("**/summary.json")) if root.exists() else []:
        try:
            document = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        if document.get("status") != "failed" or document.get("stop_reason") != "r0_profile_reconstruct_early_stop":
            continue
        assessment = document.get("measurement_assessment")
        if not isinstance(assessment, Mapping) or assessment.get("stable") is not True:
            continue
        checks = assessment.get("cross_window_resource_checks")
        if not isinstance(checks, list) or not checks:
            continue
        fields = checks[-1].get("fields")
        if not isinstance(fields, list):
            continue
        util = {}
        for item in fields:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("field") or "").replace("_utilization", "")
            value = _finite(item.get("mean_of_window_means"))
            if name in RESOURCES and value is not None:
                util[name] = value * 100.0
        if len(util) == 3:
            rows.append({"source_path": str(path), "utilization_pct": util})
    ranges = {}
    for resource in RESOURCES:
        values = [row["utilization_pct"][resource] for row in rows]
        if values:
            ranges[resource] = {
                "min": min(values),
                "max": max(values),
                "run_to_run_range_pp": max(values) - min(values),
            }
    return {
        "workload": "tpcc_sensitive",
        "scenario": "cpu",
        "same_parameter_baseline_repeat_count": len(rows),
        "all_cross_window_stable": bool(rows),
        "observed_ranges": ranges,
        "interpretation": (
            "noise/drift evidence only; repeated restores did not reset the container working set, "
            "so especially memory range includes cache/warm-state drift and is not a universal noise bound"
        ),
        "admitted_as_parameter_direction_evidence": False,
        "rows": rows,
    }


def _prompt_template() -> str:
    return """You are a PostgreSQL resource-direction estimator. Predict only CPU, memory, and IO utilization deltas relative to the supplied incumbent. Do not use or infer QPS. Do not calculate the final utility score.

The caller supplies one JSON input with: workload, scenario, target_resource, capability (CPU/memory/total-IO and read/write caps), concurrency, measurement_window, incumbent_utilization_pct, incumbent_params, candidate_params, parameter_log_delta, incumbent_plan_set, candidate_plan_set, plan_feature_delta, and matched_historical_experiences. Each plan-set item should include workload, queryid, call/time weight when available, plan_hash, node tree/path, relation/index, Plan Rows, Plan Width, Startup/Total Cost, Parallel Aware, Workers Planned, and scan/join/sort/hash/aggregate attributes. Missing fields must remain explicitly missing; never infer that a missing feature is zero.

Evidence rules:
1. Filter historical experience by exact workload, scenario/target, capability profile, stability class, and incumbent scope. Never pool incompatible scopes.
2. Multi-parameter vector evidence is joint evidence. Never attribute it to one parameter.
3. Prefer exact/similar physical plan fingerprints, then similar plan-node/cardinality delta features, then compatible parameter-log-delta experience.
4. Treat opaque fingerprint equality as evidence of plan-family similarity, not proof of equal runtime resources.
5. Historical observed ranges are not calibrated predictive intervals. Widen them for sample scarcity, stability/cache drift, cardinality uncertainty, and capability mismatch.
6. If the candidate plan is new/unknown, plan coverage is incomplete, or evidence conflicts, fail open: set out_of_distribution=true, pruning_safe=false, and use a wide interval. Do not invent missing features.
7. The caller computes Utility and performs any QPS/outer acceptance checks.

Return exactly one JSON object and no surrounding prose:
{
  "schema_version": 1,
  "cpu_delta_pp": {"low": -1.0, "p50": 0.0, "high": 1.0},
  "memory_delta_pp": {"low": -1.0, "p50": 0.0, "high": 1.0},
  "io_delta_pp": {"low": -1.0, "p50": 0.0, "high": 1.0},
  "confidence": {"cpu": 0.0, "memory": 0.0, "io": 0.0, "overall": 0.0},
  "direction": {"cpu": "unknown", "memory": "unknown", "io": "unknown"},
  "matched_experience_ids": [],
  "plan_match": {"kind": "none", "similarity": 0.0, "fingerprint_equal": false},
  "out_of_distribution": true,
  "pruning_safe": false,
  "uncertainty_reasons": [],
  "evidence_summary": []
}

Allowed direction values: decrease, near_zero, increase, unknown. All delta fields are utilization percentage points (candidate minus incumbent). Bounds must satisfy low <= p50 <= high and confidence must be in [0,1]."""


def _build_document() -> Dict[str, Any]:
    audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    fresh_observations, fresh_rejected = _fresh_observations(audit)
    optimizer_observations, optimizer_rejected = _optimizer_observations(audit)
    observations = fresh_observations + optimizer_observations
    experiences = _aggregate_experiences(observations)
    audit_records = audit.get("records", [])
    plan_rows = [row for row in audit_records if row.get("candidate_plan_signature")]
    group_counts = Counter((row["workload"], row["target_resource"]) for row in observations)
    scope_counts = defaultdict(set)
    for row in observations:
        scope_counts[(row["workload"], row["target_resource"])].add(row["incumbent_scope_id"])
    return {
        "artifact_type": "pg_resource_estimation_agent_experience_v1",
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "prediction_targets": ["cpu_utilization_delta_pp", "memory_utilization_delta_pp", "io_utilization_delta_pp"],
        "qps_policy": {
            "excluded_from_resource_features": True,
            "excluded_from_small_loop_score_prediction": True,
            "outer_real_replay_acceptance_remains_outside_agent": True,
        },
        "admission_policy": {
            "complete_incumbent_candidate_three_resource_pair_required": True,
            "strict_isolation_fields": [
                "workload", "target_resource", "scenario", "capability", "stability_class", "incumbent_scope_id"
            ],
            "multi_parameter_vectors_never_decomposed": True,
            "unknown_plan_policy": "fail_open_no_pruning_real_replay_representative_required",
        },
        "coverage": {
            "admitted_observation_count": len(observations),
            "experience_count": len(experiences),
            "incumbent_scope_count": len({row["incumbent_scope_id"] for row in observations}),
            "explicit_cross_window_stable_observation_count": sum(
                row["stability_class"] == "explicit_cross_window_stable" for row in observations
            ),
            "monitor_usable_without_explicit_stability_count": sum(
                row["stability_class"] == "usable_monitor_only_no_explicit_cross_window_stability"
                for row in observations
            ),
            "plan_conditioned_admitted_observation_count": sum(
                bool(row["plan_evidence"]["available"]) for row in observations
            ),
            "group_counts": [
                {
                    "workload": key[0],
                    "target_resource": key[1],
                    "observation_count": count,
                    "incumbent_scope_count": len(scope_counts[key]),
                }
                for key, count in sorted(group_counts.items())
            ],
        },
        "source_inventory": {
            "admitted_fresh_calibration": {
                "source_artifact": str(AUDIT_PATH),
                "observation_count": len(fresh_observations),
                "source_file_count": len({row["source_path"] for row in fresh_observations}),
                "three_resource_labels_complete": True,
                "plan_tree_or_node_features_present": False,
                "rejections": fresh_rejected,
            },
            "optimizer_candidate_replay": {
                "audit_record_count": sum(
                    row.get("evidence_kind") == "optimizer_candidate_replay" for row in audit_records
                ),
                "admitted_three_resource_observation_count": len(optimizer_observations),
                "admitted_plan_feature_observation_count": sum(
                    row["plan_evidence"].get("node_or_cardinality_features_available")
                    for row in optimizer_observations
                ),
                "records_with_opaque_plan_signature": len(plan_rows),
                "unique_opaque_plan_signature_count": len({row["candidate_plan_signature"] for row in plan_rows}),
                "rejections": optimizer_rejected,
                "limitations": [
                    "EXPLAIN COSTS only; Actual Rows/Loops/Time/Buffers are absent",
                    "resource labels cover the whole concurrent workload, not individual SQL or plan nodes",
                    "plan-capture workload weights are not consistently logged",
                ],
            },
            "paired_tpcc_sensitive_vector_prior_only": {
                "record_count": sum(
                    str(row.get("evidence_kind", "")).startswith("paired_") for row in audit_records
                ),
                "admitted_to_three_resource_experiences": False,
                "reason": (
                    "one paired result is explicitly unstable and both seven-parameter vectors lack "
                    "decomposable causal evidence"
                ),
            },
            "legacy_standalone_probe_inventory": {
                "single_candidate_summary_count": 282,
                "parallel_candidate_summary_count": 31,
                "baseline_referenced_three_resource_candidate_count": 190,
                "distinct_referenced_baseline_file_count": 68,
                "admitted": False,
                "reason": (
                    "legacy summaries lack explicit stability and plan features; exact workload statement/config "
                    "identity must be content-hashed before pairing"
                ),
            },
            "broad_jsonl_scan": {
                "roots": ["/disk1/resource_adaptive", "/disk2/resource_adaptive", "/disk3/resource_adaptive"],
                "jsonl_file_count": 31046,
                "jsonl_total_bytes": 14309900000,
                "baseline_validation_event_count": 333,
                "candidate_validation_event_count": 432967,
                "quarantine_note": (
                    "406901 candidate-validation lines come from three anomalously repetitive files; event count "
                    "is not an independent sample count and these files are not admitted"
                ),
            },
            "plan_capture_inventory": {
                "disk1_plan_items": 2068,
                "disk1_queryid_count": 32,
                "disk1_unique_plan_hash_count": 29,
                "disk1_items_with_raw_plan_json": 2002,
                "disk2_plan_items": 2775,
                "disk2_queryid_count": 37,
                "disk2_unique_plan_hash_count": 37,
                "disk2_items_with_raw_plan_json": 2775,
                "project_history_plan_items": 10464,
                "project_history_unique_plan_hash_count": 256,
                "project_history_items_with_raw_plan_json": 8474,
                "disk3_plan_capture_found": False,
                "actual_analyze_or_buffers_feature_count": 0,
                "key_rule": "use (workload, queryid, plan_hash), never plan_hash alone",
            },
        },
        "baseline_drift_evidence": _baseline_drift_evidence(),
        "descriptive_cross_scope_summary_not_runtime_admissible": _descriptive_group_summary(observations),
        "experiences": experiences,
        "runtime_decision_flow": [
            "extract incumbent/candidate physical-plan fingerprints and node/cardinality deltas",
            "filter experience by exact isolation key before similarity matching",
            "prefer same fingerprint/plan family; opaque fingerprint equality is not equivalence proof",
            "match joint parameter-log-delta vectors without decomposing multi-parameter evidence",
            "form a conservative CPU/memory/IO interval from the envelope of compatible observed evidence",
            "widen for sample scarcity, cache/stability drift, cardinality uncertainty and capability mismatch",
            "if plan is unknown or evidence conflicts, fail open and require representative real replay",
            "return resource intervals only; caller computes utility and outer acceptance",
        ],
        "runtime_agent_prompt_template": _prompt_template(),
    }


def _write_report(document: Mapping[str, Any]) -> None:
    coverage = document["coverage"]
    descriptive = {
        (row["workload"], row["target_resource"]): row
        for row in document["descriptive_cross_scope_summary_not_runtime_admissible"]
    }
    group_lines = "\n".join(
        f"| {row['workload']} | {row['target_resource']} | {row['observation_count']} | "
        f"{row['incumbent_scope_count']} | "
        f"{descriptive[(row['workload'], row['target_resource'])]['single_parameter_association_count']} | "
        f"{descriptive[(row['workload'], row['target_resource'])]['joint_vector_count']} |"
        for row in coverage["group_counts"]
    )
    drift = document["baseline_drift_evidence"]
    drift_lines = "\n".join(
        f"| {resource} | {values['min']:.2f}% | {values['max']:.2f}% | {values['run_to_run_range_pp']:.2f} pp |"
        for resource, values in drift.get("observed_ranges", {}).items()
    )
    report = f"""# PostgreSQL  Agent 2026-07-23

##

 CPUMemoryIO  QPS  Agent  Score incumbent/candidate  **{coverage['admitted_observation_count']} {coverage['incumbent_scope_count']}  incumbent scope** **{coverage['experience_count']} **

343fresh40delta4 incumbent scope `EXPLAIN COSTS` Actual Rows/Loops/Time/Buffers

##

| workload | target | Logarithmic | incumbent scope |  |  |
|---|---:|---:|---:|---:|---:|
{group_lines}

-  cross-window stable {coverage['explicit_cross_window_stable_observation_count']}
-  monitor usable/sample-count cross-window stable {coverage['monitor_usable_without_explicit_stability_count']}
- {coverage['plan_conditioned_admitted_observation_count']}
- disk120682002raw plandisk22775raw planhistory104648474raw plan/40
- EXPLAINloopsBuffers0workloadSQL
- 28219068 statement/config
- 31046 JSONL13.33 GiB333 baseline_validation432967 candidate_validation406901 candidate_validation

TPC-C/CPU45144TPC-C-sensitive3

##

3 TPC-C-sensitive 180 cross-window restore

|  |  |  |  |
|---|---:|---:|---:|
{drift_lines}

Memory cache/warm-state  workload

##

1.  workloadtarget/scenariocapability incumbent scope
2.
3. /opaque fingerprint
4.  low/p50/high
5.  fail-open
6. Agent delta  Utility

##  Agent Prompt

```text
{document['runtime_agent_prompt_template']}
```

##

- Machine`docs/artifacts/pg_resource_estimation_agent_experience_20260723.json`
- `docs/pg_resource_estimation_agent_experience_20260723.md`
- `docs/artifacts/pg_real_replay_direction_audit_20260723.json`
"""
    REPORT_PATH.write_text(report, encoding="utf-8")


def main() -> int:
    document = _build_document()
    OUTPUT_PATH.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_report(document)
    print(json.dumps({
        "artifact": str(OUTPUT_PATH),
        "report": str(REPORT_PATH),
        "coverage": document["coverage"],
    }, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

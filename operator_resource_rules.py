#!/usr/bin/env python3
"""Evidence-backed operator-transition rules for pre-replay rejection.

The hard gate deliberately uses only real resource outcomes.  QPS is never an
input.  A rule is scoped to one explicit workload contract and target resource,
and matches an exact set of operator-count directions inside the observed
magnitude envelope.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


SCHEMA_VERSION = 1
OPERATOR_FEATURES = (
    "plan_bitmap_heap_scan_count",
    "plan_gather_count",
    "plan_hash_count",
    "plan_hash_join_count",
    "plan_hashagg_count",
    "plan_index_scan_count",
    "plan_materialize_count",
    "plan_memoize_count",
    "plan_merge_join_count",
    "plan_nested_loop_count",
    "plan_parallel_aware_nodes",
    "plan_seq_scan_count",
    "plan_sort_count",
)
OPERATOR_FEATURE_SET = set(OPERATOR_FEATURES)


def _safe_float(value: Any, default: float = math.nan) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_json_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def operator_changes_from_feature_inputs(
    baseline_features: Dict[str, Any],
    candidate_features: Dict[str, Any],
) -> Dict[str, Any]:
    changes: List[Dict[str, Any]] = []
    for feature in OPERATOR_FEATURES:
        baseline = _safe_float(baseline_features.get(feature), 0.0)
        candidate = _safe_float(candidate_features.get(feature), 0.0)
        delta = candidate - baseline
        if abs(delta) <= 1e-9:
            continue
        changes.append(
            {
                "operator": feature,
                "baseline": baseline,
                "candidate": candidate,
                "delta": delta,
                "direction": "increase" if delta > 0 else "decrease",
            }
        )
    return {
        "changed": bool(changes),
        "changed_count": len(changes),
        "changes": changes,
        "transition_key": operator_transition_key(changes),
    }


def _canonical_operator_name(raw_name: Any) -> str:
    name = str(raw_name or "")
    if name.startswith("memory_shape_"):
        name = name[len("memory_shape_") :]
    return name if name in OPERATOR_FEATURE_SET else ""


def extract_operator_changes(plan_input_changes: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return canonical operator-count deltas from new or legacy probe logs."""
    explicit = plan_input_changes.get("operator_changes")
    if isinstance(explicit, dict) and isinstance(explicit.get("changes"), list):
        rows = explicit.get("changes") or []
    elif isinstance(explicit, list):
        rows = explicit
    else:
        rows = []
        features = plan_input_changes.get("features")
        if isinstance(features, dict):
            for resource in ("cpu", "memory", "io"):
                resource_changes = features.get(resource)
                if not isinstance(resource_changes, dict):
                    continue
                for item in resource_changes.get("top_changes") or []:
                    if isinstance(item, dict):
                        rows.append(item)

    by_operator: Dict[str, Dict[str, Any]] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        operator = _canonical_operator_name(item.get("operator") or item.get("feature"))
        if not operator:
            continue
        baseline = _safe_float(item.get("baseline"), 0.0)
        candidate = _safe_float(item.get("candidate"), math.nan)
        delta = _safe_float(item.get("delta"), math.nan)
        if not math.isfinite(delta) and math.isfinite(candidate):
            delta = candidate - baseline
        if not math.isfinite(delta) or abs(delta) <= 1e-9:
            continue
        if not math.isfinite(candidate):
            candidate = baseline + delta
        by_operator[operator] = {
            "operator": operator,
            "baseline": baseline,
            "candidate": candidate,
            "delta": delta,
            "direction": "increase" if delta > 0 else "decrease",
        }
    return [by_operator[name] for name in sorted(by_operator)]


def operator_transition_key(changes: Sequence[Dict[str, Any]]) -> str:
    parts = []
    for item in sorted(changes, key=lambda row: str(row.get("operator") or "")):
        operator = _canonical_operator_name(item.get("operator") or item.get("feature"))
        if not operator:
            continue
        delta = _safe_float(item.get("delta"), 0.0)
        if abs(delta) <= 1e-9:
            continue
        parts.append(f"{operator}:{'increase' if delta > 0 else 'decrease'}")
    return "|".join(parts)


def wilson_lower_bound(successes: int, total: int, z: float = 1.96) -> float:
    if total <= 0:
        return 0.0
    ratio = successes / total
    z2 = z * z
    denominator = 1.0 + z2 / total
    center = ratio + z2 / (2.0 * total)
    radius = z * math.sqrt((ratio * (1.0 - ratio) + z2 / (4.0 * total)) / total)
    return max(0.0, (center - radius) / denominator)


def _percentile(values: Sequence[float], fraction: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    position = max(0.0, min(1.0, fraction)) * (len(ordered) - 1)
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return ordered[low]
    weight = position - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _point_holdout(point: Dict[str, Any], modulus: int) -> bool:
    group = str(point.get("plan_fingerprint") or point.get("param_signature") or point.get("point_id"))
    value = int(hashlib.sha256(group.encode("utf-8")).hexdigest()[:16], 16)
    return value % max(2, modulus) == 0


def _real_resource_point(
    row: Dict[str, Any],
    *,
    source_path: str,
    contract_id: str,
    target: str,
    worsening_margin_ratio: float,
) -> Tuple[Optional[Dict[str, Any]], str]:
    if row.get("event") != "candidate_validation":
        return None, "not_candidate_validation"
    if str(row.get("target") or "").lower() != target:
        return None, "target_mismatch"
    if str(row.get("status") or "").lower() != "ok" or int(_safe_float(row.get("returncode"), 1)) != 0:
        return None, "technical_or_workload_failure"
    resource_stats = row.get("resource_stats") if isinstance(row.get("resource_stats"), dict) else {}
    if resource_stats.get("resource_monitor_ok") is not True:
        return None, "resource_monitor_invalid"
    if str(row.get("comparison_baseline_mode") or "") != "same_worker":
        return None, "not_same_worker_baseline"
    if row.get("comparison_baseline_matched") is not True:
        return None, "same_worker_baseline_unmatched"
    score = row.get("real_score_c") if isinstance(row.get("real_score_c"), dict) else {}
    if score.get("valid") is not True:
        return None, "real_score_c_invalid"
    target_drop_ratio = _safe_float(score.get("target_drop_ratio"), math.nan)
    if not math.isfinite(target_drop_ratio):
        return None, "missing_target_resource_delta"
    plan_changes = row.get("plan_input_changes") if isinstance(row.get("plan_input_changes"), dict) else {}
    if str(plan_changes.get("status") or "").lower() != "ok":
        return None, "plan_probe_not_ok"
    operator_changes = extract_operator_changes(plan_changes)
    transition_key = operator_transition_key(operator_changes)
    if not transition_key:
        return None, "no_observed_operator_count_change"
    params = row.get("params") if isinstance(row.get("params"), dict) else {}
    param_signature = stable_json_hash(params)
    point_id = stable_json_hash(
        {
            "source": source_path,
            "round": row.get("round"),
            "candidate_index": row.get("candidate_index"),
            "params": params,
        }
    )
    worsening_ratio = -target_drop_ratio
    return {
        "point_id": point_id,
        "contract_id": contract_id,
        "source_path": source_path,
        "source_run": str(Path(source_path).parent),
        "round_key": f"{source_path}#{row.get('round')}",
        "candidate_index": row.get("candidate_index"),
        "param_signature": param_signature,
        "plan_fingerprint": str(
            row.get("candidate_plan_fingerprint")
            or plan_changes.get("candidate_plan_fingerprint")
            or ""
        ),
        "operator_changes": operator_changes,
        "transition_key": transition_key,
        "target_drop_ratio": target_drop_ratio,
        "target_worsening_ratio": worsening_ratio,
        "harmful": worsening_ratio >= worsening_margin_ratio,
        "qps_used": False,
    }, "usable"


def _rule_statistics(
    points: Sequence[Dict[str, Any]],
    *,
    holdout_modulus: int,
) -> Dict[str, Any]:
    unique = {str(point["point_id"]): point for point in points}
    rows = list(unique.values())
    train = [point for point in rows if not _point_holdout(point, holdout_modulus)]
    holdout = [point for point in rows if _point_holdout(point, holdout_modulus)]
    worsening = [float(point["target_worsening_ratio"]) for point in rows]
    harmful_count = sum(1 for point in rows if point.get("harmful"))
    train_harmful = sum(1 for point in train if point.get("harmful"))
    holdout_harmful = sum(1 for point in holdout if point.get("harmful"))
    return {
        "support": len(rows),
        "harmful_count": harmful_count,
        "contradiction_count": len(rows) - harmful_count,
        "harmful_rate": harmful_count / len(rows) if rows else 0.0,
        "wilson_lower_95": wilson_lower_bound(harmful_count, len(rows)),
        "train_support": len(train),
        "train_harmful_count": train_harmful,
        "train_harmful_rate": train_harmful / len(train) if train else 0.0,
        "holdout_support": len(holdout),
        "holdout_harmful_count": holdout_harmful,
        "holdout_harmful_rate": holdout_harmful / len(holdout) if holdout else 0.0,
        "distinct_source_logs": len({str(point["source_path"]) for point in rows}),
        "distinct_rounds": len({str(point["round_key"]) for point in rows}),
        "distinct_param_signatures": len({str(point["param_signature"]) for point in rows}),
        "distinct_plan_fingerprints": len(
            {str(point["plan_fingerprint"]) for point in rows if point.get("plan_fingerprint")}
        ),
        "minimum_worsening_ratio": min(worsening) if worsening else None,
        "median_worsening_ratio": statistics.median(worsening) if worsening else None,
        "worsening_ratio_p10": _percentile(worsening, 0.10),
        "evidence_point_ids": sorted(str(point["point_id"]) for point in rows),
    }


def build_rule_artifact(
    manifest: Dict[str, Any],
    *,
    thresholds: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    policy = {
        "worsening_margin_ratio": 0.005,
        "min_support": 12,
        "min_train_support": 8,
        "min_holdout_support": 2,
        "min_distinct_source_logs": 2,
        "min_distinct_rounds": 4,
        "min_distinct_param_signatures": 8,
        "min_distinct_plan_fingerprints": 3,
        "min_wilson_lower_95": 0.75,
        "holdout_modulus": 5,
        "envelope_relative_tolerance": 0.05,
        "require_zero_contradictions": True,
        "match_mode": "exact_operator_direction_set_with_observed_magnitude_envelope",
        "qps_used": False,
    }
    if isinstance(thresholds, dict):
        policy.update(thresholds)

    contract_defs: Dict[str, Dict[str, Any]] = {}
    for raw_contract in manifest.get("contracts") or []:
        if not isinstance(raw_contract, dict):
            continue
        contract_id = str(raw_contract.get("contract_id") or "")
        if not contract_id:
            raise ValueError("contract_id is required")
        contract = dict(raw_contract)
        plan_path = Path(str(contract.pop("plan_sql_path", ""))) if contract.get("plan_sql_path") else None
        if plan_path is not None:
            if not plan_path.is_file():
                raise FileNotFoundError(plan_path)
            contract["plan_sql_sha256"] = sha256_file(plan_path)
        contract_defs[contract_id] = contract

    points_by_contract: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    source_audit: List[Dict[str, Any]] = []
    for source in manifest.get("sources") or []:
        if not isinstance(source, dict):
            continue
        source_path = Path(str(source.get("path") or ""))
        contract_id = str(source.get("contract_id") or "")
        if contract_id not in contract_defs:
            raise ValueError(f"source references unknown contract_id={contract_id}")
        contract = contract_defs[contract_id]
        target = str(contract.get("target") or "").lower()
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        counts: Dict[str, int] = defaultdict(int)
        usable_points: List[Dict[str, Any]] = []
        with source_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    counts["invalid_json"] += 1
                    continue
                point, reason = _real_resource_point(
                    row,
                    source_path=str(source_path),
                    contract_id=contract_id,
                    target=target,
                    worsening_margin_ratio=float(policy["worsening_margin_ratio"]),
                )
                counts[reason] += 1
                if point is not None:
                    usable_points.append(point)
        if _to_bool(source.get("eligible_for_hard_rules"), True):
            points_by_contract[contract_id].extend(usable_points)
        source_audit.append(
            {
                "path": str(source_path),
                "sha256": sha256_file(source_path),
                "contract_id": contract_id,
                "eligible_for_hard_rules": _to_bool(source.get("eligible_for_hard_rules"), True),
                "usable_point_count": len(usable_points),
                "event_counts": dict(sorted(counts.items())),
            }
        )

    contracts: Dict[str, Dict[str, Any]] = {}
    for contract_id, contract in sorted(contract_defs.items()):
        points = points_by_contract.get(contract_id, [])
        transition_points: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        atomic_points: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for point in points:
            transition_points[str(point["transition_key"])].append(point)
            for change in point["operator_changes"]:
                atomic_key = f"{change['operator']}:{change['direction']}"
                atomic_points[atomic_key].append(point)

        atomic_statistics = []
        for atomic_key, atomic_rows in sorted(atomic_points.items()):
            stats = _rule_statistics(atomic_rows, holdout_modulus=int(policy["holdout_modulus"]))
            atomic_statistics.append({"operator_direction": atomic_key, **stats})

        trusted_rules = []
        transition_statistics = []
        for transition_key, transition_rows in sorted(transition_points.items()):
            stats = _rule_statistics(transition_rows, holdout_modulus=int(policy["holdout_modulus"]))
            requirements = {
                "support": stats["support"] >= int(policy["min_support"]),
                "train_support": stats["train_support"] >= int(policy["min_train_support"]),
                "holdout_support": stats["holdout_support"] >= int(policy["min_holdout_support"]),
                "source_logs": stats["distinct_source_logs"] >= int(policy["min_distinct_source_logs"]),
                "rounds": stats["distinct_rounds"] >= int(policy["min_distinct_rounds"]),
                "param_signatures": stats["distinct_param_signatures"]
                >= int(policy["min_distinct_param_signatures"]),
                "plan_fingerprints": stats["distinct_plan_fingerprints"]
                >= int(policy["min_distinct_plan_fingerprints"]),
                "wilson_lower": stats["wilson_lower_95"] >= float(policy["min_wilson_lower_95"]),
                "zero_contradictions": stats["contradiction_count"] == 0,
                "train_all_harmful": stats["train_support"] > 0
                and stats["train_harmful_count"] == stats["train_support"],
                "holdout_all_harmful": stats["holdout_support"] > 0
                and stats["holdout_harmful_count"] == stats["holdout_support"],
            }
            trusted = all(requirements.values())
            transition_statistics.append(
                {
                    "transition_key": transition_key,
                    "trusted_for_hard_reject": trusted,
                    "requirements": requirements,
                    **stats,
                }
            )
            if not trusted:
                continue
            envelope: Dict[str, Dict[str, float]] = {}
            for operator_direction in transition_key.split("|"):
                operator, direction = operator_direction.rsplit(":", 1)
                magnitudes = [
                    abs(float(change["delta"]))
                    for point in transition_rows
                    for change in point["operator_changes"]
                    if change["operator"] == operator and change["direction"] == direction
                ]
                envelope[operator] = {
                    "direction": direction,
                    "min_abs_delta": min(magnitudes),
                    "max_abs_delta": max(magnitudes),
                }
            rule_core = {
                "contract_id": contract_id,
                "target": contract.get("target"),
                "transition_key": transition_key,
                "operator_delta_envelope": envelope,
                "statistics": stats,
            }
            trusted_rules.append(
                {
                    "rule_id": "oprr-" + stable_json_hash(rule_core)[:20],
                    "trusted_for_hard_reject": True,
                    **rule_core,
                }
            )

        contracts[contract_id] = {
            **contract,
            "contract_id": contract_id,
            "usable_real_replay_points": len({str(point["point_id"]) for point in points}),
            "trusted_rule_count": len(trusted_rules),
            "rules": trusted_rules,
            "transition_statistics": transition_statistics,
            "atomic_operator_statistics": atomic_statistics,
        }

    artifact = {
        "schema_version": SCHEMA_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "policy": policy,
        "source_manifest_sha256": stable_json_hash(manifest),
        "sources": source_audit,
        "contracts": contracts,
    }
    artifact["evidence_sha256"] = stable_json_hash(artifact)
    return artifact


def load_rule_gate(
    gate_cfg: Dict[str, Any],
    *,
    workload: str,
    target: str,
    plan_sql_path: Optional[Path] = None,
) -> Dict[str, Any]:
    enabled = _to_bool(gate_cfg.get("enabled"), False)
    base = {
        "enabled": enabled,
        "active": False,
        "contract_id": str(gate_cfg.get("contract_id") or ""),
        "workload": workload,
        "target": target,
        "artifact_path": str(gate_cfg.get("artifact_path") or ""),
        "rule_count": 0,
    }
    if not enabled:
        return {**base, "reason": "disabled", "rules_by_transition": {}}
    artifact_path = Path(base["artifact_path"])
    if not artifact_path.is_file():
        raise FileNotFoundError(f"operator rule artifact missing: {artifact_path}")
    actual_sha = sha256_file(artifact_path)
    expected_sha = str(gate_cfg.get("artifact_sha256") or "")
    if not expected_sha:
        raise ValueError("operator rule gate requires artifact_sha256")
    if actual_sha != expected_sha:
        raise ValueError(
            f"operator rule artifact SHA mismatch: expected={expected_sha} actual={actual_sha}"
        )
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if int(artifact.get("schema_version", 0)) != SCHEMA_VERSION:
        raise ValueError(f"unsupported operator rule schema: {artifact.get('schema_version')}")
    contract_id = base["contract_id"]
    contracts = artifact.get("contracts") if isinstance(artifact.get("contracts"), dict) else {}
    contract = contracts.get(contract_id)
    if not isinstance(contract, dict):
        raise ValueError(f"operator rule contract not found: {contract_id}")
    if str(contract.get("workload") or "") != workload:
        raise ValueError(
            f"operator rule workload mismatch: contract={contract.get('workload')} runtime={workload}"
        )
    if str(contract.get("target") or "").lower() != target.lower():
        raise ValueError(
            f"operator rule target mismatch: contract={contract.get('target')} runtime={target}"
        )
    expected_plan_sha = str(contract.get("plan_sql_sha256") or "")
    actual_plan_sha = sha256_file(plan_sql_path) if plan_sql_path is not None else ""
    if expected_plan_sha and actual_plan_sha != expected_plan_sha:
        raise ValueError(
            f"operator rule plan SQL SHA mismatch: expected={expected_plan_sha} actual={actual_plan_sha}"
        )
    rules = [
        rule
        for rule in contract.get("rules") or []
        if isinstance(rule, dict) and rule.get("trusted_for_hard_reject") is True
    ]
    rules_by_transition = {str(rule.get("transition_key") or ""): rule for rule in rules}
    return {
        **base,
        "active": True,
        "reason": "trusted_artifact_loaded",
        "artifact_sha256": actual_sha,
        "artifact_evidence_sha256": artifact.get("evidence_sha256"),
        "plan_sql_sha256": actual_plan_sha,
        "rule_count": len(rules),
        "match_mode": (artifact.get("policy") or {}).get("match_mode"),
        "envelope_relative_tolerance": float(
            gate_cfg.get(
                "envelope_relative_tolerance",
                (artifact.get("policy") or {}).get("envelope_relative_tolerance", 0.05),
            )
        ),
        "rules_by_transition": rules_by_transition,
    }


def summarize_rule_gate(gate: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in gate.items() if key != "rules_by_transition"}


def evaluate_rule_gate(plan_input_changes: Dict[str, Any], gate: Dict[str, Any]) -> Dict[str, Any]:
    changes = extract_operator_changes(plan_input_changes)
    transition_key = operator_transition_key(changes)
    base = {
        "enabled": bool(gate.get("enabled")),
        "active": bool(gate.get("active")),
        "evaluated": bool(gate.get("active")),
        "rejected": False,
        "contract_id": gate.get("contract_id"),
        "target": gate.get("target"),
        "transition_key": transition_key,
        "operator_changes": changes,
        "qps_used": False,
        "physical_workload_invocations": 0,
    }
    if not gate.get("active"):
        return {**base, "reason": gate.get("reason") or "gate_inactive"}
    if not transition_key:
        return {**base, "reason": "no_operator_count_change"}
    rules = gate.get("rules_by_transition") if isinstance(gate.get("rules_by_transition"), dict) else {}
    rule = rules.get(transition_key)
    if not isinstance(rule, dict):
        return {**base, "reason": "no_exact_trusted_transition_rule"}
    by_operator = {str(item["operator"]): item for item in changes}
    tolerance = max(0.0, float(gate.get("envelope_relative_tolerance", 0.05)))
    envelope = rule.get("operator_delta_envelope") if isinstance(rule.get("operator_delta_envelope"), dict) else {}
    envelope_checks: Dict[str, Any] = {}
    for operator, bounds in envelope.items():
        if not isinstance(bounds, dict) or operator not in by_operator:
            return {**base, "reason": "operator_delta_envelope_mismatch", "matched_rule_id": rule.get("rule_id")}
        magnitude = abs(float(by_operator[operator]["delta"]))
        lower = max(0.0, float(bounds.get("min_abs_delta", 0.0)) * (1.0 - tolerance))
        upper = float(bounds.get("max_abs_delta", 0.0)) * (1.0 + tolerance)
        within = lower <= magnitude <= upper
        envelope_checks[operator] = {
            "magnitude": magnitude,
            "lower": lower,
            "upper": upper,
            "within": within,
        }
        if not within:
            return {
                **base,
                "reason": "operator_delta_outside_observed_envelope",
                "matched_rule_id": rule.get("rule_id"),
                "envelope_checks": envelope_checks,
            }
    return {
        **base,
        "rejected": True,
        "reason": "exact_operator_transition_historically_worsens_target_resource",
        "matched_rule_id": rule.get("rule_id"),
        "rule_statistics": rule.get("statistics"),
        "envelope_checks": envelope_checks,
        "state_updates_permitted": {
            "accepted": False,
            "incumbent": False,
            "range": False,
            "gradient": False,
        },
    }

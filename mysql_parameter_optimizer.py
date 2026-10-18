"""Module."""

from __future__ import annotations

import argparse
from contextlib import AbstractContextManager
from concurrent.futures import Future
import copy
from datetime import datetime
import fcntl
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from benchmark_estimator import build_resource_estimator
from common_outer_loop import (
    ACCEPTANCE_TIER_RANK,
    DEFAULT_ACCEPTANCE_TIER,
    DEFAULT_ACCEPTANCE_TIER_DOWNGRADE_AFTER_ROUNDS,
    OuterShrinkProgress,
    advance_acceptance_tier,
    advance_outer_shrink_progress,
    decide_validation_acceptance,
    prune_ranked_candidates_for_replay,
    sanitize_optimistic_pruning_metadata_tree,
)
from gradient_descent_optimizer import ParameterRange
from experience_table_subset_optimizer import (
    FORMAL_OPTIMIZER_OPTION_KEYS,
    optimize_all_experience_table_subsets,
)
from experience_table_escape_policy import generate_escape_batch
from mysql_cost_adapter import MYSQL_COST_KEYS, MySQLCLIClient, MySQLCostAdapter, SQLClient
from mysql_plan_clustering import (
    cluster_ranked_candidates,
    formal_replay_cap,
    validate_canonical_plan_bundle,
)
from mysql_three_level_fingerprint import (
    L2_FIELDS,
    L3_SCENARIO_FIELDS,
    build_candidate_fingerprint,
    normalize_scenario,
)
from formal_mysql_sgd_checkpoint import FormalOuterReplayLedger
from resource_utility import ResourceUtilityCalculator


DEFAULT_CONFIG = Path(__file__).resolve().parent / "config" / "mysql_optimizer_config.json"
EstimatePayload = Tuple[Dict[str, Any], Any, Any]
RealReplayCallback = Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]
REQUIRED_REAL_REPLAY_EVIDENCE = (
    "campaign_method_benchmark_scenario",
    "envelope_and_template_versions",
    "round_candidate_repeat",
    "lane_host_container_port",
    "software_checksums",
    "workload_seed_registry_weights_clients",
    "cpuset_and_numa",
    "resource_limit_readback",
    "fixed_mysql_settings_before_after",
    "requested_effective_costs",
    "fresh_connection_generation",
    "explain_and_fingerprints",
    "estimator_model_formula",
    "per_query_operator_breakdown",
    "per_second_samples",
    "stability_windows_and_coverage",
    "correctness_errors_oom_restarts",
    "utility_components",
    "range_before_after",
    "subset_sgd_trajectory_and_winner",
)


def _formal_checkpoint_path(
    configured: Any, output_dir: Path, round_index: int,
) -> Path:
    """Resolve an explicit checkpoint safely to one file per outer round."""
    if configured:
        raw = str(configured)
        if "{round_index}" in raw:
            return Path(raw.format(round_index=round_index))
        base = Path(raw)
        if round_index == 1:
            return base
        suffix = base.suffix or ".json"
        stem = base.stem if base.suffix else base.name
        return base.with_name(f"{stem}.round_{round_index}{suffix}")
    return output_dir / f"formal_outer_checkpoint_round_{round_index}.json"


def _build_replay_suppression_log(
    *, non_incumbent_rankings: Sequence[Mapping[str, Any]],
    plan_representatives: Sequence[Mapping[str, Any]],
    replay_pruned: Sequence[Mapping[str, Any]],
    replay_kept: Sequence[Mapping[str, Any]], scenario_replay_cap: int,
) -> List[Dict[str, Any]]:
    representative_subset_ids = {
        item.get("subset_id") for item in plan_representatives
    }
    cluster_suppressed = [
        {
            **copy.deepcopy(dict(row)),
            "suppression_reason": "non_representative_strict_plan_cluster_member",
        }
        for row in non_incumbent_rankings
        if row.get("subset_id") not in representative_subset_ids
    ]
    cap_suppressed = [
        {
            **copy.deepcopy(dict(row)),
            "suppression_reason": "outside_fixed_top5_initial_batch",
            "scenario_replay_cap": scenario_replay_cap,
            "post_top5_extension_eligibility": (
                "fallback_qps_and_strict_l1_changed_from_incumbent"
            ),
        }
        for row in replay_kept[scenario_replay_cap:]
    ]
    return [
        *copy.deepcopy(list(replay_pruned)), *cluster_suppressed, *cap_suppressed,
    ]


def _enforce_strict_plan_replay_quota(
    *,
    plan_clustering: Mapping[str, Any],
    replay_kept: Sequence[Mapping[str, Any]],
    replay_pruned: Sequence[Mapping[str, Any]],
    pruning_checks: Sequence[Mapping[str, Any]],
    required_plan_clusters: int,
    scenario_replay_cap: int,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Restore strict-plan representatives and place them before diagnostics.

    Optimistic resource bounds may rank candidates inside a physical plan
    cluster, but they must not erase the only real-replay representative of a
    strict physical plan. Missing fingerprints remain diagnostic singleton
    clusters and never satisfy the plan-diversity quota.
    """
    if required_plan_clusters <= 0:
        raise ValueError("required_plan_clusters must be positive")
    if scenario_replay_cap <= 0:
        raise ValueError("scenario_replay_cap must be positive")

    representatives = [
        copy.deepcopy(dict(item))
        for item in (plan_clustering.get("representatives") or [])
        if isinstance(item, Mapping)
    ]
    strict_cluster_keys = {
        str(cluster.get("cluster_key"))
        for cluster in (plan_clustering.get("clusters") or [])
        if isinstance(cluster, Mapping)
        and cluster.get("strict_fingerprint_present") is True
        and str(cluster.get("cluster_key") or "").startswith("strict:")
    }

    def identity(candidate: Mapping[str, Any]) -> tuple[Any, Any]:
        return candidate.get("subset_id"), candidate.get("rank")

    strict_representatives = [
        item
        for item in representatives
        if str(item.get("strict_plan_cluster_key") or "") in strict_cluster_keys
    ]
    strict_representatives.sort(
        key=lambda item: (
            int(item.get("rank") or 2**63 - 1),
            str(item.get("subset_id") or ""),
        )
    )
    strict_ids = {identity(item) for item in strict_representatives}
    kept_by_id = {
        identity(item): copy.deepcopy(dict(item)) for item in replay_kept
    }
    pruned_by_id = {
        identity(item): copy.deepcopy(dict(item)) for item in replay_pruned
    }
    restored: List[Dict[str, Any]] = []
    for representative in strict_representatives:
        candidate_id = identity(representative)
        candidate = kept_by_id.get(candidate_id) or pruned_by_id.get(candidate_id)
        if candidate is None:
            candidate = copy.deepcopy(representative)
        if candidate_id not in kept_by_id:
            candidate["optimistic_pruning_override"] = {
                "applied": True,
                "reason": "sole_strict_plan_cluster_representative_must_reach_replay_queue",
                "strict_plan_cluster_key": candidate.get("strict_plan_cluster_key"),
            }
            restored.append(copy.deepcopy(candidate))
        candidate["strict_plan_replay_priority"] = True
        kept_by_id[candidate_id] = candidate

    ordered_kept = [
        kept_by_id[identity(item)]
        for item in strict_representatives
        if identity(item) in kept_by_id
    ]
    ordered_kept.extend(
        copy.deepcopy(dict(item))
        for item in replay_kept
        if identity(item) not in strict_ids
    )
    ordered_ids = {identity(item) for item in ordered_kept}
    ordered_kept.extend(
        copy.deepcopy(item)
        for item in restored
        if identity(item) not in ordered_ids
    )
    remaining_pruned = [
        copy.deepcopy(dict(item))
        for item in replay_pruned
        if identity(item) not in strict_ids
    ]

    restored_ids = {identity(item) for item in restored}
    updated_checks: List[Dict[str, Any]] = []
    for raw_check in pruning_checks:
        check = copy.deepcopy(dict(raw_check))
        candidate_id = (check.get("candidate_subset_id"), check.get("candidate_rank"))
        if candidate_id[0] is None:
            matching = next(
                (
                    item for item in restored
                    if item.get("rank") == check.get("candidate_rank")
                ),
                None,
            )
            if matching is not None:
                candidate_id = identity(matching)
        if candidate_id in restored_ids:
            check["policy_override"] = copy.deepcopy(
                kept_by_id[candidate_id]["optimistic_pruning_override"]
            )
        updated_checks.append(check)

    final_queue = ordered_kept[:scenario_replay_cap]
    final_strict_keys = {
        str(item.get("strict_plan_cluster_key"))
        for item in final_queue
        if str(item.get("strict_plan_cluster_key") or "") in strict_cluster_keys
    }
    available_strict_count = len(strict_cluster_keys)
    uncovered_strict_keys = sorted(strict_cluster_keys - final_strict_keys)
    audit = {
        "contract": "strict_plan_replay_quota_after_optimistic_pruning_v1",
        "required_plan_clusters": required_plan_clusters,
        "available_strict_plan_clusters": available_strict_count,
        "restored_after_optimistic_pruning_count": len(restored),
        "restored_subset_ids": [item.get("subset_id") for item in restored],
        "missing_fingerprints_count_toward_quota": False,
        "scenario_replay_cap": scenario_replay_cap,
        "final_queue_strict_plan_clusters": len(final_strict_keys),
        "final_queue_strict_plan_cluster_keys": sorted(final_strict_keys),
        "uncovered_strict_plan_cluster_keys": uncovered_strict_keys,
        "all_available_strict_plans_covered": not uncovered_strict_keys,
        "strict_plan_coverage_conflict": bool(uncovered_strict_keys),
        "quota_satisfied": len(final_strict_keys) >= required_plan_clusters,
        "quota_satisfiable": (
            available_strict_count >= required_plan_clusters
            and scenario_replay_cap >= required_plan_clusters
        ),
    }
    return ordered_kept, remaining_pruned, updated_checks, audit


FROZEN_MYSQL_V2_ID = "frozen_mysql_workloads_and_parameters_20260722_v2"
FROZEN_MYSQL_V3_ID = "frozen_mysql_workloads_and_parameters_20260723_v3"
FROZEN_MYSQL_V2_ACTIVE = {
    "hybench_ap": [
        "io_block_read_cost",
        "memory_block_read_cost",
        "row_evaluate_cost",
        "key_compare_cost",
        "disk_temptable_create_cost",
        "disk_temptable_row_cost",
        "memory_temptable_create_cost",
        "memory_temptable_row_cost",
    ],
    "job": [
        "io_block_read_cost",
        "memory_block_read_cost",
        "row_evaluate_cost",
        "memory_temptable_row_cost",
    ],
    "tpch": [
        "io_block_read_cost",
        "memory_block_read_cost",
        "row_evaluate_cost",
        "disk_temptable_row_cost",
    ],
    "tpcc": [
        "memory_block_read_cost",
        "row_evaluate_cost",
        "disk_temptable_row_cost",
        "memory_temptable_row_cost",
    ],
    "hybench_xp": [
        "io_block_read_cost",
        "memory_block_read_cost",
        "row_evaluate_cost",
        "key_compare_cost",
        "disk_temptable_create_cost",
        "disk_temptable_row_cost",
        "memory_temptable_row_cost",
    ],
}
FROZEN_MYSQL_V2_RANGES = {
    "io_block_read_cost": (0.1, 10.0),
    "memory_block_read_cost": (0.025, 2.5),
    "row_evaluate_cost": (0.01, 1.0),
    "key_compare_cost": (0.005, 0.5),
    "disk_temptable_create_cost": (2.0, 200.0),
    "disk_temptable_row_cost": (0.05, 5.0),
    "memory_temptable_create_cost": (0.1, 10.0),
    "memory_temptable_row_cost": (0.01, 1.0),
}
FROZEN_MYSQL_V3_ACTIVE = {
    benchmark: copy.deepcopy(active)
    for benchmark, active in FROZEN_MYSQL_V2_ACTIVE.items()
    if benchmark in {"job", "hybench_xp", "tpch", "tpcc"}
}
FORMAL_FROZEN_ACTIVE = {
    FROZEN_MYSQL_V2_ID: FROZEN_MYSQL_V2_ACTIVE,
    FROZEN_MYSQL_V3_ID: FROZEN_MYSQL_V3_ACTIVE,
}
FORMAL_FROZEN_RANGES = {
    FROZEN_MYSQL_V2_ID: FROZEN_MYSQL_V2_RANGES,
    FROZEN_MYSQL_V3_ID: FROZEN_MYSQL_V2_RANGES,
}


class ReplayRecoveryError(RuntimeError):
    """A real point did not prove restoration; the mutable lane is unsafe."""

    def __init__(self, message: str, evaluation: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.evaluation = copy.deepcopy(dict(evaluation))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _LaneLease(AbstractContextManager):
    """Advisory cross-process lease for one mutable MySQL optimizer lane."""

    def __init__(self, path: Path, *, timeout_s: float = 0.0) -> None:
        self.path = path
        self.timeout_s = float(timeout_s)
        self._handle: Any = None

    def __enter__(self) -> "_LaneLease":
        if self.timeout_s < 0.0 or not math.isfinite(self.timeout_s):
            raise ValueError("lane lease timeout must be finite and non-negative")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+", encoding="utf-8")
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    self._handle.close()
                    self._handle = None
                    raise RuntimeError(f"MySQL SGD lane lease is busy: {self.path}")
                time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        self._handle.seek(0)
        self._handle.truncate()
        self._handle.write(
            json.dumps(
                {"pid": os.getpid(), "acquired_at": datetime.now().astimezone().isoformat()},
                sort_keys=True,
            )
            + "\n"
        )
        self._handle.flush()
        os.fsync(self._handle.fileno())
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None


def _default_lane_lease_path(config: Mapping[str, Any]) -> Path:
    mysql_cfg = config.get("mysql") if isinstance(config.get("mysql"), Mapping) else {}
    identity = json.dumps(
        {
            "host": mysql_cfg.get("host", "localhost"),
            "port": mysql_cfg.get("port", 3306),
            "socket": mysql_cfg.get("socket"),
            "engine_name": mysql_cfg.get("engine_name", "default"),
            "device_type": mysql_cfg.get("device_type", 0),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    token = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return Path("/tmp") / f"resource_adaptive_mysql_sgd_{token}.lane.lock"


def _candidate_canonical_tuple(
    candidate: Mapping[str, Any], canonical_params: Mapping[str, Any]
) -> tuple[float, ...]:
    params = candidate.get("optimized_params") or {}
    return tuple(float(params[name]) for name in canonical_params)


def _finite_measurement(value: Any, field: str, reasons: List[str]) -> Optional[float]:
    parsed = _finite_or_none(value)
    if parsed is None:
        reasons.append(f"missing_or_nonfinite_{field}")
    return parsed


def _evaluate_outer_eligibility(
    measurement: Mapping[str, Any],
    *,
    baseline: Mapping[str, Any],
    target_resource: str,
    benchmark: str,
    acceptance_tier: str = DEFAULT_ACCEPTANCE_TIER,
) -> Dict[str, Any]:
    """Evaluate immutable hard gates, then the active tiered acceptance rule."""
    reasons: List[str] = []
    checks: Dict[str, bool] = {}

    qps = _finite_measurement(measurement.get("qps"), "qps", reasons)
    db_qps = _finite_measurement(measurement.get("db_qps"), "db_qps", reasons)
    base_db_qps = _finite_measurement(
        baseline.get("db_qps", baseline.get("qps")), "baseline_db_qps", reasons
    )
    checks["qps_equals_db_qps"] = qps is not None and db_qps is not None and qps == db_qps
    if not checks["qps_equals_db_qps"]:
        reasons.append("qps_db_qps_mismatch")
    checks["db_qps_is_database_side"] = (
        measurement.get("db_qps_source") == "database_status_counter"
    )

    checks["correctness_passed"] = measurement.get("correctness_passed") is True
    error_rate = _finite_measurement(measurement.get("error_rate"), "error_rate", reasons)
    checks["error_rate_lte_0_01"] = error_rate is not None and 0.0 <= error_rate <= 0.01
    checks["stable"] = measurement.get("stable") is True
    coverage = _finite_measurement(measurement.get("sample_coverage"), "sample_coverage", reasons)
    checks["sample_coverage_gte_0_90"] = coverage is not None and 0.90 <= coverage <= 1.0
    checks["no_oom"] = measurement.get("oom") is False
    checks["no_mysql_restart"] = measurement.get("mysql_restart") is False
    checks["no_container_restart"] = measurement.get("container_restart") is False
    fingerprint = measurement.get("three_level_fingerprint")
    checks["three_level_fingerprint_complete"] = (
        isinstance(fingerprint, Mapping)
        and fingerprint.get("true_duplicate_usable") is True
        and fingerprint.get("qps_in_fingerprint") is False
        and isinstance(fingerprint.get("l1"), Mapping)
        and isinstance(fingerprint.get("l2"), Mapping)
        and isinstance(fingerprint.get("l3"), Mapping)
    )
    if not checks["three_level_fingerprint_complete"]:
        reasons.append("incomplete_l1_l2_l3_fingerprint")

    p95 = _finite_measurement(measurement.get("p95_ms"), "p95_ms", reasons)
    p99 = _finite_measurement(measurement.get("p99_ms"), "p99_ms", reasons)
    base_p95 = _finite_measurement(baseline.get("p95_ms"), "baseline_p95_ms", reasons)
    base_p99 = _finite_measurement(baseline.get("p99_ms"), "baseline_p99_ms", reasons)
    checks["p95_lte_incumbent_1_10"] = (
        p95 is not None and base_p95 is not None and base_p95 >= 0.0 and p95 <= base_p95 * 1.10
    )
    checks["p99_lte_incumbent_1_10"] = (
        p99 is not None and base_p99 is not None and base_p99 >= 0.0 and p99 <= base_p99 * 1.10
    )

    utilization = measurement.get("utilization_pct") or {}
    baseline_utilization = baseline.get("utilization_pct") or {}
    if not isinstance(utilization, Mapping) or not isinstance(baseline_utilization, Mapping):
        reasons.append("invalid_utilization_payload")
        utilization = {}
        baseline_utilization = {}
    values = {
        resource: _finite_measurement(
            utilization.get(resource), f"utilization_{resource}", reasons
        )
        for resource in ("cpu", "memory", "io")
    }
    baseline_target = _finite_measurement(
        baseline_utilization.get(target_resource),
        f"baseline_utilization_{target_resource}",
        reasons,
    )
    baseline_non_targets = {
        resource: _finite_measurement(
            baseline_utilization.get(resource),
            f"baseline_utilization_{resource}",
            reasons,
        )
        for resource in values
        if resource != target_resource
    }
    checks["non_target_resources_strictly_below_0_8"] = all(
        values[name] is not None and 0.0 <= float(values[name]) < 0.80
        for name in values
        if name != target_resource
    )
    checks["all_resource_utilizations_in_unit_interval"] = all(
        value is not None and 0.0 <= float(value) <= 1.0 for value in values.values()
    )
    checks["db_qps_strictly_increased"] = (
        db_qps is not None and base_db_qps is not None and db_qps > base_db_qps
    )
    checks["target_utilization_strictly_decreased"] = (
        values[target_resource] is not None
        and baseline_target is not None
        and float(values[target_resource]) < baseline_target
    )
    checks["all_non_target_resources_strictly_increased"] = all(
        values[name] is not None
        and baseline_non_targets.get(name) is not None
        and float(values[name]) > float(baseline_non_targets[name])
        for name in values
        if name != target_resource
    )

    if str(benchmark).strip().lower() == "hybench_xp":
        regressions = measurement.get("xp_stream_regression") or {}
        checks["xp_stream_regression_lte_0_05"] = (
            isinstance(regressions, Mapping)
            and bool(regressions)
            and all(
                _finite_or_none(value) is not None
                and 0.0 <= float(value) <= 0.05
                for value in regressions.values()
            )
        )

    evidence = measurement.get("evidence") or {}
    missing_evidence = [
        name for name in REQUIRED_REAL_REPLAY_EVIDENCE
        if not isinstance(evidence, Mapping) or not evidence.get(name)
    ]
    checks["complete_evidence_references"] = not missing_evidence
    acceptance_diagnostic_checks = {
        "non_target_resources_strictly_below_0_8",
        "db_qps_strictly_increased",
        "target_utilization_strictly_decreased",
        "all_non_target_resources_strictly_increased",
    }
    for name, passed in checks.items():
        if name in acceptance_diagnostic_checks:
            continue
        if not passed and name not in reasons:
            reasons.append(name)
    acceptance = decide_validation_acceptance(
        baseline,
        measurement,
        target_resource=target_resource,
        hard_gate_passed=not reasons,
        acceptance_tier=acceptance_tier,
    )
    eligible = acceptance.accepted
    if not eligible and acceptance.reason not in reasons:
        reasons.append(acceptance.reason)
    return {
        "eligible": eligible,
        "checks": checks,
        "reasons": reasons,
        "missing_evidence_references": missing_evidence,
        "acceptance_decision": acceptance.to_dict(),
        "required_acceptance_tier": acceptance.required_acceptance_tier,
        "satisfied_acceptance_tier": acceptance.satisfied_acceptance_tier,
        "contract": "mysql_formal_outer_hard_gates_tiered_acceptance_v3",
    }


def _validate_replay_baseline(
    baseline: Mapping[str, Any], target_resource: str
) -> None:
    qps = _finite_or_none(baseline.get("qps"))
    db_qps = _finite_or_none(baseline.get("db_qps"))
    if qps is None or db_qps is None or qps <= 0.0 or qps != db_qps:
        raise ValueError("baseline requires positive finite qps == db_qps")
    if baseline.get("db_qps_source") != "database_status_counter":
        raise ValueError("baseline db_qps must come from database_status_counter")
    for name in ("p95_ms", "p99_ms"):
        value = _finite_or_none(baseline.get(name))
        if value is None or value < 0.0:
            raise ValueError(f"baseline requires finite non-negative {name}")
    utilization = baseline.get("utilization_pct") or {}
    if not isinstance(utilization, Mapping):
        raise ValueError("baseline utilization_pct must be a mapping")
    for name in ("cpu", "memory", "io"):
        value = _finite_or_none(utilization.get(name))
        if value is None or not 0.0 <= value <= 1.0:
            raise ValueError(f"baseline utilization {name} must be within [0, 1]")
    if target_resource not in utilization:
        raise ValueError(f"baseline lacks target utilization {target_resource}")
    for name in ("cpu", "memory", "io"):
        value = float(utilization[name])
        if name == target_resource:
            if not 0.85 <= value <= 0.95:
                raise ValueError("R0 target utilization must be within [0.85, 0.95]")
        elif not 0.30 <= value <= 0.50:
            raise ValueError("R0 non-target utilization must be within [0.30, 0.50]")


def _replay_diagnostic(evaluations: List[Mapping[str, Any]]) -> Dict[str, Any]:
    reasons: Dict[str, int] = {}
    for row in evaluations:
        validation = row.get("candidate_final_validation") or {}
        row_reasons = list((validation.get("outer_gate") or {}).get("reasons") or [])
        if row.get("callback_error"):
            row_reasons.append("real_replay_callback_failed")
        if not row_reasons:
            row_reasons.append("outer_eligible_false")
        for reason in set(str(item) for item in row_reasons):
            reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "code": "no_outer_eligible_candidate_after_max_real_replays",
        "candidate_count": len(evaluations),
        "reason_counts": dict(sorted(reasons.items())),
        "candidate_reasons": [
            {
                "replay_ordinal": row.get("replay_ordinal"),
                "subset_id": row.get("candidate_subset_id"),
                "reasons": list(
                    ((row.get("candidate_final_validation") or {}).get("outer_gate") or {}).get("reasons")
                    or (["real_replay_callback_failed"] if row.get("callback_error") else ["outer_eligible_false"])
                ),
                "missing_evidence_references": list(
                    ((row.get("candidate_final_validation") or {}).get("outer_gate") or {}).get("missing_evidence_references")
                    or []
                ),
                "evidence": copy.deepcopy(
                    (row.get("candidate_final_validation") or {}).get("evidence") or {}
                ),
            }
            for row in evaluations
        ],
        "requires_root_cause_review_before_mutation": True,
    }


def _eligible_replay_selection_key(
    row: Mapping[str, Any],
    *,
    queue: Sequence[Mapping[str, Any]],
    baseline_params: Mapping[str, Any],
    target_resource: str,
) -> tuple[int, float, tuple[float, ...]]:
    """Prefer the strictest satisfied tier inside the sealed replay batch."""
    validation = row.get("candidate_final_validation") or {}
    decision = (validation.get("outer_gate") or {}).get(
        "acceptance_decision"
    ) or {}
    satisfied_rank = int(decision.get("satisfied_acceptance_tier_rank") or 0)
    utilization = validation.get("utilization_pct") or {}
    target = float(utilization.get(target_resource, math.inf))
    candidate = next(
        item
        for item in queue
        if item.get("subset_id") == row.get("candidate_subset_id")
    )
    return (
        -satisfied_rank,
        target,
        _candidate_canonical_tuple(candidate, baseline_params),
    )


def _incumbent_l1_sha256(
    baseline_validation: Mapping[str, Any],
) -> Optional[str]:
    """Return the strict incumbent L1 identity, or fail closed with ``None``.

    A completed R0 may expose the already-built three-level fingerprint or the
    canonical plan bundle that is used to build it.  Both forms are verified;
    a caller-supplied bare hash is deliberately not trusted.
    """
    fingerprint = baseline_validation.get("three_level_fingerprint")
    if isinstance(fingerprint, Mapping):
        l1 = fingerprint.get("l1")
        if isinstance(l1, Mapping):
            value = str(l1.get("sha256") or "")
            if len(value) == 64 and not any(
                character not in "0123456789abcdef" for character in value
            ):
                return value
    bundle = baseline_validation.get("canonical_plan_bundle")
    if not isinstance(bundle, Mapping):
        plan_capture = baseline_validation.get("plan_capture")
        if isinstance(plan_capture, Mapping):
            bundle = plan_capture.get("canonical_plan_bundle")
    verification = validate_canonical_plan_bundle(bundle)
    if verification.get("valid") is not True or not isinstance(bundle, Mapping):
        return None
    if bundle.get("strict_cluster_usable") is not True:
        return None
    value = str(bundle.get("strict_cluster_id_sha256") or "")
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        return None
    return value


def _plan_tree_extension_schedule(
    candidates: Sequence[Mapping[str, Any]],
    *,
    incumbent_l1_sha256: Optional[str],
    already_replayed_ranks: Sequence[int],
    maximum_additional_replays: int,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Select only sealed, not-yet-replayed candidates with a changed strict L1."""
    replayed = {int(rank) for rank in already_replayed_ranks}
    selected: List[Dict[str, Any]] = []
    decisions: List[Dict[str, Any]] = []
    incumbent_key = (
        f"strict:{incumbent_l1_sha256}" if incumbent_l1_sha256 is not None else None
    )
    for raw in candidates:
        candidate = copy.deepcopy(dict(raw))
        rank = int(candidate.get("rank") or 0)
        key = str(candidate.get("strict_plan_cluster_key") or "")
        reason: str
        appended = False
        if rank in replayed:
            reason = "already_replayed"
        elif incumbent_key is None:
            reason = "incumbent_strict_l1_missing_fail_closed"
        elif not key.startswith("strict:"):
            reason = "candidate_strict_l1_missing_fail_closed"
        elif key == incumbent_key:
            reason = "same_plan_tree_as_incumbent"
        elif len(selected) >= maximum_additional_replays:
            reason = "maximum_additional_replays_reached"
        else:
            reason = "appended_changed_plan_tree"
            appended = True
            selected.append(candidate)
        decisions.append({
            "candidate_rank": rank,
            "candidate_subset_id": candidate.get("subset_id"),
            "candidate_strict_plan_cluster_key": key or None,
            "incumbent_strict_plan_cluster_key": incumbent_key,
            "strict_l1_changed": (
                incumbent_key is not None
                and key.startswith("strict:")
                and key != incumbent_key
            ),
            "appended": appended,
            "reason": reason,
        })
    return selected, decisions


def _orchestrate_staged_real_replays(
    candidates: List[Dict[str, Any]],
    *,
    callback: RealReplayCallback,
    baseline_params: Mapping[str, Any],
    baseline_validation: Mapping[str, Any],
    sensitivity_table: Dict[str, Dict[str, float]],
    target_resource: str,
    benchmark: str = "",
    initial_top_k: int = 5,
    max_top_k: int = 5,
    plan_clustering_log: Optional[Mapping[str, Any]] = None,
    pruning_decision_log: Sequence[Mapping[str, Any]] = (),
    suppressed_candidate_log: Sequence[Mapping[str, Any]] = (),
    replay_ledger: Optional[FormalOuterReplayLedger] = None,
    acceptance_tier: str = DEFAULT_ACCEPTANCE_TIER,
    plan_tree_extension_enabled: bool = True,
    plan_tree_extension_batch_size: int = 5,
    plan_tree_extension_maximum_additional_replays: Optional[int] = None,
) -> Dict[str, Any]:
    """Execute fixed Top-5, then a lowest-tier changed-plan extension if needed.

    ``callback(candidate, context)`` must run one fresh §14 point and return the
    raw measurement plus all ``REQUIRED_REAL_REPLAY_EVIDENCE`` references.  Its
    ``restore(candidate, context)`` method is invoked in all cases (including a
    measurement exception) and must prove exact baseline-cost restoration,
    fixed-setting readback, and a clean template.  Eligibility claimed by the
    callback is ignored; this function recomputes every hard gate.
    """
    if not callable(callback):
        raise TypeError("real replay callback must be callable")
    restore_callback = getattr(callback, "restore", None)
    if not callable(restore_callback):
        raise TypeError(
            "formal real replay callback must expose restore(candidate, context)"
        )
    if initial_top_k != 5 or max_top_k != 5:
        raise ValueError("formal real replay uses one global fixed Top-5 batch")
    if plan_tree_extension_batch_size <= 0:
        raise ValueError("plan-tree replay extension batch size must be positive")
    extension_maximum = (
        max(0, len(candidates) - initial_top_k)
        if plan_tree_extension_maximum_additional_replays is None
        else int(plan_tree_extension_maximum_additional_replays)
    )
    if extension_maximum < 0:
        raise ValueError("plan-tree replay extension maximum cannot be negative")
    _validate_replay_baseline(baseline_validation, target_resource)
    _gradient_replay_stage_action(
        validated_count=0, eligible_count=0, available_count=len(candidates),
        initial_top_k=initial_top_k, max_top_k=max_top_k,
    )
    # The entire predicted order is sealed before any replay.  The first five
    # remain the immutable primary batch; extension never re-ranks this list.
    queue = copy.deepcopy(candidates)
    if replay_ledger is not None and replay_ledger.inflight_replays():
        reconcile = getattr(callback, "reconcile_started_replay", None)
        if not callable(reconcile):
            raise ReplayRecoveryError(
                "started replay is unresolved and callback has no reconciliation service",
                {"inflight_replays": replay_ledger.inflight_replays()},
            )
        for started in replay_ledger.inflight_replays():
            try:
                reconciled = reconcile(copy.deepcopy(started))
            except Exception as exc:
                raise ReplayRecoveryError(
                    "started replay reconciliation failed; refusing to rerun rank",
                    {"started": started, "error": f"{type(exc).__name__}: {exc}"},
                ) from exc
            if not isinstance(reconciled, Mapping) or reconciled.get("status") != "completed":
                raise ReplayRecoveryError(
                    "started replay is not provably complete; refusing to rerun rank",
                    {"started": started, "reconciliation": reconciled},
                )
            measurement = reconciled.get("measurement")
            recovery = reconciled.get("recovery")
            if not isinstance(measurement, Mapping) or not isinstance(recovery, Mapping):
                raise ReplayRecoveryError(
                    "reconciliation lacks measurement/recovery evidence",
                    {"started": started},
                )
            candidate = started.get("candidate") or {}
            restored_params = recovery.get("effective_params") or {}
            recovery_checks = {
                "restore_succeeded": recovery.get("succeeded") is True,
                "baseline_params_restored_exactly": (
                    isinstance(restored_params, Mapping)
                    and _exact_parameter_signature(restored_params)
                    == _exact_parameter_signature(baseline_params)
                ),
                "fixed_settings_verified": recovery.get("fixed_settings_verified") is True,
                "clean_template_verified": recovery.get("clean_template_verified") is True,
                "recovery_evidence_present": bool(recovery.get("evidence")),
            }
            if not all(recovery_checks.values()):
                raise ReplayRecoveryError(
                    "reconciled replay recovery proof is invalid",
                    {"started": started, "recovery_checks": recovery_checks},
                )
            validation = _attach_replay_three_level_fingerprint(
                measurement, candidate, benchmark,
            )
            gate = _evaluate_outer_eligibility(
                validation, baseline=baseline_validation,
                target_resource=target_resource, benchmark=benchmark,
                acceptance_tier=acceptance_tier,
            )
            validation["outer_gate"] = gate
            validation["outer_eligible"] = gate["eligible"]
            row = {
                "replay_ordinal": len(replay_ledger.replayed_evaluations()) + 1,
                "outer_replay_attempt_token": str(started["attempt_token"]),
                "stage": {
                    "top_5": "initial_top_k", "top_10": "expanded_to_top_10",
                    "top_k": "expanded_plan_tree_changes",
                }[started["stage"]],
                "candidate_rank": started["rank"],
                "candidate_subset_id": candidate.get("subset_id"),
                "candidate_plan_cluster_key": candidate.get("strict_plan_cluster_key"),
                "candidate_plan_cluster_members": copy.deepcopy(
                    candidate.get("strict_plan_cluster_member_subset_ids") or []
                ),
                "candidate_optimized_params": copy.deepcopy(
                    candidate.get("optimized_params") or {}
                ),
                "candidate_active_subset": copy.deepcopy(
                    candidate.get("active_subset") or []
                ),
                "candidate_predicted_initial_resource": copy.deepcopy(
                    candidate.get("initial_resource") or {}
                ),
                "candidate_predicted_final_resource": copy.deepcopy(
                    candidate.get("final_resource") or {}
                ),
                "candidate_final_validation": validation,
                "recovery": {**copy.deepcopy(dict(recovery)),
                             "checks": recovery_checks, "verified": True},
                "reconciled_from_started_attempt": True,
            }
            replay_ledger.mark_replayed(
                int(started["rank"]), stage=str(started["stage"]), evidence=row,
                attempt_token=str(started["attempt_token"]),
            )
    evaluations: List[Dict[str, Any]] = (
        replay_ledger.replayed_evaluations() if replay_ledger is not None else []
    )

    def replay(candidate: Dict[str, Any], stage: str) -> None:
        ledger_stage = {
            "initial_top_k": "top_5",
            "expanded_to_top_10": "top_10",
            "expanded_to_scenario_cap_k": "top_k",
            "expanded_plan_tree_changes": "top_k",
        }[stage]
        attempt_token = (
            replay_ledger.start_replay(
                int(candidate["rank"]), stage=ledger_stage, candidate=candidate,
            )
            if replay_ledger is not None else None
        )
        context = {
            "stage": stage,
            "replay_ordinal": len(evaluations) + 1,
            "initial_top_k": initial_top_k,
            "maximum_top_k": max_top_k,
            "baseline_params": copy.deepcopy(dict(baseline_params)),
            "baseline_validation": copy.deepcopy(dict(baseline_validation)),
            "required_evidence_references": list(REQUIRED_REAL_REPLAY_EVIDENCE),
            "restore_required_after_every_attempt": True,
            "outer_replay_attempt_token": attempt_token,
        }
        row: Dict[str, Any] = {
            "replay_ordinal": context["replay_ordinal"],
            "outer_replay_attempt_token": attempt_token,
            "stage": stage,
            "candidate_rank": candidate.get("rank"),
            "candidate_subset_id": candidate.get("subset_id"),
            "candidate_plan_cluster_key": candidate.get("strict_plan_cluster_key"),
            "candidate_plan_cluster_members": copy.deepcopy(
                candidate.get("strict_plan_cluster_member_subset_ids") or []
            ),
            "candidate_optimized_params": copy.deepcopy(candidate.get("optimized_params") or {}),
            "candidate_active_subset": copy.deepcopy(candidate.get("active_subset") or []),
            "candidate_predicted_initial_resource": copy.deepcopy(
                candidate.get("initial_resource") or {}
            ),
            "candidate_predicted_final_resource": copy.deepcopy(
                candidate.get("final_resource") or {}
            ),
        }
        callback_error: Optional[Exception] = None
        try:
            validation = callback(copy.deepcopy(candidate), copy.deepcopy(context))
            if not isinstance(validation, Mapping):
                raise TypeError("real replay callback must return a mapping")
            validation = _attach_replay_three_level_fingerprint(
                validation, candidate, benchmark,
            )
            gate = _evaluate_outer_eligibility(
                validation,
                baseline=baseline_validation,
                target_resource=target_resource,
                benchmark=benchmark,
                acceptance_tier=acceptance_tier,
            )
            validation["outer_gate"] = gate
            validation["outer_eligible"] = gate["eligible"]
            validation["outer_acceptance_reason"] = (
                (gate.get("acceptance_decision") or {}).get("reason")
                or "outer_hard_gate_or_tier_rejected"
            )
            row["candidate_final_validation"] = validation
        except Exception as exc:
            callback_error = exc
            row["callback_error"] = f"{type(exc).__name__}: {exc}"
            row["candidate_final_validation"] = {
                "outer_eligible": False,
                "outer_acceptance_reason": "real_replay_callback_failed",
                "outer_gate": {
                    "eligible": False,
                    "checks": {},
                    "reasons": ["real_replay_callback_failed"],
                    "missing_evidence_references": list(REQUIRED_REAL_REPLAY_EVIDENCE),
                    "contract": "mysql_formal_outer_hard_gates_tiered_acceptance_v3",
                    "required_acceptance_tier": acceptance_tier,
                },
            }
        try:
            restore_context = copy.deepcopy(context)
            restore_context["measurement"] = copy.deepcopy(
                row.get("candidate_final_validation")
            )
            restore_context["measurement_error"] = (
                f"{type(callback_error).__name__}: {callback_error}"
                if callback_error is not None else None
            )
            recovery = restore_callback(
                copy.deepcopy(candidate), restore_context
            )
            if not isinstance(recovery, Mapping):
                raise TypeError("restore callback must return a mapping")
            recovery = copy.deepcopy(dict(recovery))
            restored_params = recovery.get("effective_params") or {}
            recovery_checks = {
                "restore_succeeded": recovery.get("succeeded") is True,
                "baseline_params_restored_exactly": (
                    isinstance(restored_params, Mapping)
                    and _exact_parameter_signature(restored_params)
                    == _exact_parameter_signature(baseline_params)
                ),
                "fixed_settings_verified": recovery.get("fixed_settings_verified") is True,
                "clean_template_verified": recovery.get("clean_template_verified") is True,
                "recovery_evidence_present": bool(recovery.get("evidence")),
            }
            recovery["checks"] = recovery_checks
            recovery["verified"] = all(recovery_checks.values())
            row["recovery"] = recovery
        except Exception as exc:
            row["recovery"] = {
                "verified": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        evaluations.append(row)
        if not (row.get("recovery") or {}).get("verified"):
            raise ReplayRecoveryError(
                "real replay recovery was not proven; refusing further points",
                row,
            )
        if replay_ledger is not None:
            rank = candidate.get("rank")
            if callback_error is None:
                replay_ledger.mark_replayed(
                    int(rank), stage=ledger_stage, evidence=row,
                    attempt_token=attempt_token,
                )
            else:
                replay_ledger.mark_replay_technical_failure(
                    int(rank), stage=ledger_stage,
                    error=f"{type(callback_error).__name__}: {callback_error}",
                    attempt_token=attempt_token,
                )

    def pending(candidates_for_stage: Sequence[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
        if not candidates_for_stage:
            return []
        if replay_ledger is None:
            return list(candidates_for_stage)[:limit]
        replayed_ranks = {
            int(row["candidate_rank"])
            for row in replay_ledger.replayed_evaluations()
            if row.get("candidate_rank") is not None
        }
        return [
            candidate
            for candidate in candidates_for_stage
            if int(candidate.get("rank")) not in replayed_ranks
        ][:limit]

    first_count = min(initial_top_k, len(queue))
    for candidate in pending(queue[:first_count], first_count):
        replay(candidate, "initial_top_k")
    eligible = [
        row for row in evaluations
        if (row.get("candidate_final_validation") or {}).get("outer_eligible") is True
    ]
    extension_audit: Dict[str, Any] = {
        "enabled": bool(plan_tree_extension_enabled),
        "triggered": False,
        "trigger_condition": (
            "required_acceptance_tier_is_fallback_qps_and_fixed_top5_has_no_eligible_point"
        ),
        "acceptance_tier": acceptance_tier,
        "fixed_top5_replayed_count": sum(
            row.get("stage") == "initial_top_k" for row in evaluations
        ),
        "fixed_top5_eligible_count": len(eligible),
        "batch_size": int(plan_tree_extension_batch_size),
        "maximum_additional_replays": extension_maximum,
        "incumbent_l1_sha256": _incumbent_l1_sha256(baseline_validation),
        "candidate_scan": [],
        "batches": [],
        "additional_replay_count": 0,
        "stop_reason": "not_triggered",
        "predicted_order_reused_without_rerank": True,
        "real_replay_results_used_for_rerank": False,
        "qps_used_for_extension_selection": False,
    }
    if (
        plan_tree_extension_enabled
        and acceptance_tier == "fallback_qps"
        and not eligible
        and extension_maximum > 0
    ):
        extension_audit["triggered"] = True
        already_replayed = [
            int(row["candidate_rank"])
            for row in evaluations
            if row.get("candidate_rank") is not None
        ]
        extension_candidates, scan = _plan_tree_extension_schedule(
            queue[first_count:],
            incumbent_l1_sha256=extension_audit["incumbent_l1_sha256"],
            already_replayed_ranks=already_replayed,
            maximum_additional_replays=extension_maximum,
        )
        extension_audit["candidate_scan"] = scan
        if not extension_candidates:
            extension_audit["stop_reason"] = (
                "incumbent_l1_missing_fail_closed"
                if extension_audit["incumbent_l1_sha256"] is None
                else "no_unreplayed_changed_strict_l1_candidate"
            )
        else:
            for offset in range(0, len(extension_candidates), plan_tree_extension_batch_size):
                batch = extension_candidates[
                    offset: offset + plan_tree_extension_batch_size
                ]
                before = len(evaluations)
                for candidate in pending(batch, len(batch)):
                    replay(candidate, "expanded_plan_tree_changes")
                batch_rows = evaluations[before:]
                batch_eligible = [
                    row for row in batch_rows
                    if (row.get("candidate_final_validation") or {}).get(
                        "outer_eligible"
                    ) is True
                ]
                extension_audit["batches"].append({
                    "batch_index": len(extension_audit["batches"]) + 1,
                    "candidate_ranks": [row.get("candidate_rank") for row in batch_rows],
                    "candidate_subset_ids": [
                        row.get("candidate_subset_id") for row in batch_rows
                    ],
                    "eligible_count": len(batch_eligible),
                    "stop_after_batch": bool(batch_eligible),
                })
                if batch_eligible:
                    extension_audit["stop_reason"] = (
                        "eligible_changed_plan_candidate_found"
                    )
                    break
            else:
                extension_audit["stop_reason"] = (
                    "all_scheduled_changed_plan_candidates_replayed_without_acceptance"
                )
        eligible = [
            row for row in evaluations
            if (row.get("candidate_final_validation") or {}).get("outer_eligible") is True
        ]
        extension_audit["additional_replay_count"] = sum(
            row.get("stage") == "expanded_plan_tree_changes" for row in evaluations
        )
    elif acceptance_tier != "fallback_qps":
        extension_audit["stop_reason"] = "acceptance_tier_not_lowest"
    elif eligible:
        extension_audit["stop_reason"] = "fixed_top5_found_eligible_point"
    elif not plan_tree_extension_enabled:
        extension_audit["stop_reason"] = "extension_disabled"
    else:
        extension_audit["stop_reason"] = "extension_maximum_is_zero"
    updated_table = copy.deepcopy(sensitivity_table)
    feedback_audit: Dict[str, Any] = {
        "enabled": False,
        "reason": "accepted_batch_needs_no_rejection_feedback",
        "qps_role": "outer_acceptance_only",
        "qps_fields_read": False,
        "corrections": [],
        "next_outer_eta_scale": 1.0,
    }
    root_cause_review: Optional[Dict[str, Any]] = None
    if not eligible:
        updated_table, feedback_audit = _resource_only_post_top5_feedback(
            sensitivity_table,
            baseline_params=baseline_params,
            baseline_validation=baseline_validation,
            evaluations=evaluations,
        )
        root_cause_diagnostic = _replay_diagnostic(evaluations)
        root_cause_review = {
            "diagnostic": copy.deepcopy(root_cause_diagnostic),
            "decision": "reject_outer_round_after_plan_tree_extension",
            "trigger": "no_outer_eligible_candidate_after_extension_policy",
            "manual_approval_required": True,
        }

    def eligible_key(
        row: Mapping[str, Any],
    ) -> tuple[int, float, tuple[float, ...]]:
        # The replay order remains the sealed predicted order. Selection among
        # measured rows never changes which candidates were admitted.
        return _eligible_replay_selection_key(
            row,
            queue=queue,
            baseline_params=baseline_params,
            target_resource=target_resource,
        )

    chosen = min(eligible, key=eligible_key) if eligible else None
    stage_names = ("initial_top_k", "expanded_plan_tree_changes")
    stage_audit = []
    for stage_name in stage_names:
        stage_rows = [row for row in evaluations if row.get("stage") == stage_name]
        stage_audit.append({
            "stage": stage_name,
            "executed": bool(stage_rows),
            "candidate_ranks": [row.get("candidate_rank") for row in stage_rows],
            "candidate_subset_ids": [row.get("candidate_subset_id") for row in stage_rows],
        })
    replay_audit = {
        "contract": "global_raw_all_subset_fixed_top5_then_changed_l1_extension_v2",
        "required_acceptance_tier": acceptance_tier,
        "eligible_selection_priority": (
            "highest_satisfied_tier_then_lowest_target_utilization"
        ),
        "sealed_candidate_order": [
            {"rank": candidate.get("rank"), "subset_id": candidate.get("subset_id")}
            for candidate in queue
        ],
        "pruning_decisions": copy.deepcopy(list(pruning_decision_log)),
        "plan_clustering": copy.deepcopy(dict(plan_clustering_log or {})),
        "representatives": copy.deepcopy(queue),
        "suppressed_candidates": copy.deepcopy(list(suppressed_candidate_log)),
        "stage_transitions": stage_audit,
        "root_cause_review_after_top_5": root_cause_review,
        "plan_tree_replay_extension": extension_audit,
        "stage_boundaries": {
            "top_5": first_count,
            "extension_batch_size": plan_tree_extension_batch_size,
            "extension_maximum_additional_replays": extension_maximum,
            "hard_cap": first_count + extension_maximum,
        },
    }
    return {
        "evaluations": evaluations,
        "validated_count": len(evaluations),
        "eligible_count": len(eligible),
        "chosen": copy.deepcopy(chosen),
        "online_t_update": feedback_audit,
        "updated_sensitivity_table": updated_table,
        "final_replay_queue": [
            copy.deepcopy(candidate)
            for candidate in queue
            if int(candidate.get("rank") or 0) in {
                int(row.get("candidate_rank") or 0) for row in evaluations
            }
        ],
        "staged_replay_contract": (
            "global_raw_all_subset_fixed_top5_then_changed_l1_extension_v2"
        ),
        "cluster_count_below_10_replays_all": False,
        "staged_replay_audit": replay_audit,
        "diagnostic": _replay_diagnostic(evaluations) if not eligible else None,
    }


class _SharedEstimateFutureCache:
    """Exact-parameter Future cache copied from the source all-subset path."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._futures: Dict[tuple[tuple[str, str], ...], Future[EstimatePayload]] = {}
        self._request_count = 0
        self._cache_hit_count = 0
        self._inflight_hit_count = 0
        self._failed_unique_count = 0

    def get_or_compute(
        self,
        params: Mapping[str, Any],
        compute: Callable[[], EstimatePayload],
    ) -> tuple[EstimatePayload, Dict[str, Any]]:
        signature = _exact_parameter_signature(params)
        owner = False
        inflight_hit = False
        with self._lock:
            self._request_count += 1
            future = self._futures.get(signature)
            if future is None:
                future = Future()
                self._futures[signature] = future
                owner = True
            else:
                self._cache_hit_count += 1
                if not future.done():
                    self._inflight_hit_count += 1
                    inflight_hit = True
        if owner:
            try:
                future.set_result(copy.deepcopy(compute()))
            except BaseException as exc:
                with self._lock:
                    self._failed_unique_count += 1
                future.set_exception(exc)
        return copy.deepcopy(future.result()), {
            "cache_hit": not owner,
            "shared_inflight_future": inflight_hit,
            "parameter_signature": [list(item) for item in signature],
        }

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "request_count": self._request_count,
                "unique_parameter_count": len(self._futures),
                "cache_hit_count": self._cache_hit_count,
                "inflight_hit_count": self._inflight_hit_count,
                "failed_unique_count": self._failed_unique_count,
            }


def _gradient_replay_stage_action(
    *,
    validated_count: int,
    eligible_count: int,
    available_count: int,
    initial_top_k: int = 5,
    max_top_k: int = 5,
) -> str:
    """Return the fixed global Top-5 replay action."""
    if validated_count < 0 or eligible_count < 0 or available_count < 0:
        raise ValueError("replay counts cannot be negative")
    if eligible_count > validated_count:
        raise ValueError("eligible_count cannot exceed validated_count")
    if initial_top_k != 5 or max_top_k != 5:
        raise ValueError("formal real replay uses one global fixed Top-5 batch")
    if validated_count < min(initial_top_k, available_count):
        return "continue_initial"
    if eligible_count > 0:
        return "accept_current_batch"
    return "reject_exploration"


def _finite_or_none(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _resource_only_post_top5_feedback(
    sensitivity_table: Mapping[str, Mapping[str, Any]],
    *,
    baseline_params: Mapping[str, Any],
    baseline_validation: Mapping[str, Any],
    evaluations: Sequence[Mapping[str, Any]],
    minimum_resource_delta: float = 0.01,
    strong_single_sample_delta: float = 0.03,
) -> tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """Correct next-round resource directions from attributable real evidence.

    This function deliberately does not inspect QPS. Only stable, recovered,
    single-parameter replays can change the experience table. Two consistent
    votes are normally required; one large resource movement is sufficient.
    """
    resources = ("cpu", "memory", "io")
    updated = copy.deepcopy({
        str(name): dict(values) for name, values in sensitivity_table.items()
    })
    baseline_utilization = baseline_validation.get("utilization_pct") or {}
    votes: Dict[tuple[str, str], List[Dict[str, Any]]] = {}
    sample_audit: List[Dict[str, Any]] = []

    for row in evaluations:
        validation = row.get("candidate_final_validation") or {}
        recovery = row.get("recovery") or {}
        if (
            not isinstance(validation, Mapping)
            or validation.get("stable") is not True
            or validation.get("correctness_passed") is not True
            or validation.get("oom") is not False
            or validation.get("mysql_restart") is not False
            or validation.get("container_restart") is not False
            or not isinstance(recovery, Mapping)
            or recovery.get("verified") is not True
        ):
            continue
        params = row.get("candidate_optimized_params") or {}
        if not isinstance(params, Mapping):
            continue
        changed = []
        for name, baseline_value in baseline_params.items():
            candidate_value = _finite_or_none(params.get(name, baseline_value))
            baseline_numeric = _finite_or_none(baseline_value)
            if (
                candidate_value is None
                or baseline_numeric is None
                or candidate_value <= 0.0
                or baseline_numeric <= 0.0
            ):
                continue
            log_delta = math.log(candidate_value / baseline_numeric)
            if abs(log_delta) > 1e-12:
                changed.append((str(name), log_delta))
        if len(changed) != 1:
            continue
        parameter, log_delta = changed[0]
        if parameter not in updated:
            continue
        actual = validation.get("utilization_pct") or {}
        predicted_initial = row.get("candidate_predicted_initial_resource") or {}
        predicted_final = row.get("candidate_predicted_final_resource") or {}
        if not isinstance(actual, Mapping):
            continue
        for resource in resources:
            baseline_value = _finite_or_none(
                baseline_utilization.get(resource) if isinstance(baseline_utilization, Mapping) else None
            )
            actual_value = _finite_or_none(actual.get(resource))
            if baseline_value is None or actual_value is None:
                continue
            actual_delta = actual_value - baseline_value
            if abs(actual_delta) < minimum_resource_delta:
                continue
            derivative_sign = 1 if actual_delta / log_delta > 0.0 else -1
            predicted_before = _finite_or_none(
                predicted_initial.get(resource) if isinstance(predicted_initial, Mapping) else None
            )
            predicted_after = _finite_or_none(
                predicted_final.get(resource) if isinstance(predicted_final, Mapping) else None
            )
            predicted_delta = (
                predicted_after - predicted_before
                if predicted_before is not None and predicted_after is not None
                else None
            )
            vote = {
                "replay_ordinal": row.get("replay_ordinal"),
                "subset_id": row.get("candidate_subset_id"),
                "parameter": parameter,
                "resource": resource,
                "parameter_log_delta": log_delta,
                "actual_utilization_delta": actual_delta,
                "actual_derivative_sign": derivative_sign,
                "predicted_resource_delta": predicted_delta,
                "predicted_direction_matches_actual": (
                    None
                    if predicted_delta is None or abs(predicted_delta) <= 1e-15
                    else (predicted_delta > 0.0) == (actual_delta > 0.0)
                ),
            }
            votes.setdefault((parameter, resource), []).append(vote)
            sample_audit.append(copy.deepcopy(vote))

    corrections: List[Dict[str, Any]] = []
    for (parameter, resource), resource_votes in sorted(votes.items()):
        positive = sum(vote["actual_derivative_sign"] > 0 for vote in resource_votes)
        negative = len(resource_votes) - positive
        consensus_sign = 1 if positive > negative else -1 if negative > positive else 0
        consensus_count = max(positive, negative)
        consensus_ratio = consensus_count / len(resource_votes)
        strongest_delta = max(abs(vote["actual_utilization_delta"]) for vote in resource_votes)
        enough_evidence = (
            consensus_sign != 0
            and consensus_ratio >= 0.75
            and (
                consensus_count >= 2
                or strongest_delta >= strong_single_sample_delta
            )
        )
        current = _finite_or_none(updated[parameter].get(resource))
        if not enough_evidence or current is None:
            continue
        current_sign = 1 if current > 0.0 else -1 if current < 0.0 else 0
        if current_sign == consensus_sign:
            continue
        if current_sign == 0:
            magnitudes = [
                abs(value)
                for row_values in updated.values()
                for value in [_finite_or_none(row_values.get(resource))]
                if value is not None and value != 0.0
            ]
            magnitude = float(np.median(magnitudes)) if magnitudes else 0.05
            corrected = consensus_sign * magnitude
        else:
            corrected = -current
        updated[parameter][resource] = corrected
        corrections.append({
            "parameter": parameter,
            "resource": resource,
            "before": current,
            "after": corrected,
            "evidence_count": len(resource_votes),
            "consensus_ratio": consensus_ratio,
            "strongest_actual_utilization_delta": strongest_delta,
            "reason": "stable_single_parameter_real_resource_direction_conflict",
        })

    return updated, {
        "enabled": bool(corrections),
        "reason": (
            "resource_only_direction_correction_for_next_outer_round"
            if corrections
            else "no_confident_attributable_resource_direction_conflict"
        ),
        "qps_role": "outer_acceptance_only",
        "qps_fields_read": False,
        "attributable_resource_samples": sample_audit,
        "corrections": corrections,
        "next_outer_eta_scale": 0.5 if corrections else 1.0,
        "reranks_current_top5": False,
    }


def _advance_rejected_escape_state(
    *,
    current_radius: float,
    maximum_radius: float,
    radius_gamma: float,
    current_eta: float,
    minimum_eta: float,
    consecutive_rejected_batches: int,
    maximum_rejected_batches: int,
    resource_feedback: Mapping[str, Any],
) -> Dict[str, Any]:
    """Expand plan coverage after rejection and damp corrected directions."""
    rejected = consecutive_rejected_batches + 1
    eta = current_eta
    if resource_feedback.get("enabled") is True:
        eta_scale = float(resource_feedback.get("next_outer_eta_scale", 0.5))
        if not 0.0 < eta_scale <= 1.0:
            raise ValueError("resource feedback eta scale must be within (0, 1]")
        eta = max(minimum_eta, current_eta * eta_scale)
    return {
        "escape_radius": min(maximum_radius, current_radius / radius_gamma),
        "eta": eta,
        "consecutive_rejected_batches": rejected,
        "exhausted": rejected >= maximum_rejected_batches,
        "qps_fields_read": False,
    }


def _online_update_experience_table_and_rerank(
    sensitivity_table: Dict[str, Dict[str, float]],
    *,
    baseline_params: Mapping[str, Any],
    baseline_validation: Mapping[str, Any],
    evaluations: List[Mapping[str, Any]],
    remaining_candidates: List[Dict[str, Any]],
    low_confidence_freeze_threshold: float = 0.50,
    target_resource: str = "memory",
) -> tuple[Dict[str, Dict[str, float]], List[Dict[str, Any]], Dict[str, Any]]:
    """Copy the source online T-delta update and rerank the unseen suffix."""
    updated = copy.deepcopy(sensitivity_table)
    target_resource = str(target_resource).strip().lower()
    if target_resource not in {"cpu", "memory", "io"}:
        raise ValueError(f"unsupported online T target_resource: {target_resource}")
    baseline_util = dict(baseline_validation.get("utilization_pct") or {})
    samples: List[Dict[str, Any]] = []
    for evaluation in evaluations:
        params = evaluation.get("candidate_optimized_params") or {}
        validation = evaluation.get("candidate_final_validation") or {}
        utilization = validation.get("utilization_pct") or {}
        if not isinstance(params, Mapping):
            continue
        outcomes = {
            name: float(utilization[name]) - float(baseline_util[name])
            for name in ("cpu", "memory", "io")
            if name in utilization and name in baseline_util
        }
        if len(outcomes) != 3:
            continue
        samples.append({"params": dict(params), "outcomes": outcomes})

    rows: List[Dict[str, Any]] = []
    for param_name, effects in updated.items():
        prior_confidence = float(effects.get("confidence", 1.0) or 0.0)
        prior_evidence = max(0.0, float(effects.get("evidence_count", 0.0) or 0.0))
        xs: List[float] = []
        ys: Dict[str, List[float]] = {
            name: [] for name in ("cpu", "memory", "io")
        }
        base_value = _finite_or_none(baseline_params.get(param_name))
        if base_value is not None and base_value > 0.0:
            for sample in samples:
                candidate_value = _finite_or_none(sample["params"].get(param_name))
                if candidate_value is None or candidate_value <= 0.0:
                    continue
                xs.append(math.log(candidate_value / base_value))
                for outcome_name in ys:
                    ys[outcome_name].append(float(sample["outcomes"][outcome_name]))
        supported = len(xs) >= 3 and max(xs) - min(xs) > 1e-12
        frozen = prior_confidence < low_confidence_freeze_threshold
        correlations: Dict[str, float] = {}
        if supported and not frozen:
            x_array = np.asarray(xs, dtype=float)
            for outcome_name, values in ys.items():
                y_array = np.asarray(values, dtype=float)
                correlation = 0.0
                if float(np.std(y_array)) > 1e-12:
                    correlation = float(np.corrcoef(x_array, y_array)[0, 1])
                    if not math.isfinite(correlation):
                        correlation = 0.0
                correlations[outcome_name] = correlation
                empirical_effect = max(-0.20, min(0.20, 0.20 * correlation))
                effects[outcome_name] = (
                    0.50 * float(effects.get(outcome_name, 0.0) or 0.0)
                    + 0.50 * empirical_effect
                )
            signal_confidence = sum(abs(value) for value in correlations.values()) / max(
                1, len(correlations)
            )
            effects["confidence"] = min(
                0.95, max(prior_confidence * 0.80, signal_confidence)
            )
        effects["evidence_count"] = prior_evidence + float(len(xs))
        rows.append(
            {
                "parameter": param_name,
                "supported": supported,
                "frozen_low_confidence": frozen,
                "prior_confidence": prior_confidence,
                "updated_confidence": float(effects.get("confidence", prior_confidence)),
                "new_evidence_count": len(xs),
                "correlations": correlations,
                "updated_effects": copy.deepcopy(effects),
            }
        )

    def ranking_key(candidate: Dict[str, Any]) -> tuple[int, float, int]:
        params = candidate.get("optimized_params") or {}
        predicted = {target_resource: 0.0}
        for param_name, effects in updated.items():
            confidence = float(effects.get("confidence", 1.0) or 0.0)
            if confidence < low_confidence_freeze_threshold:
                continue
            base_value = _finite_or_none(baseline_params.get(param_name))
            candidate_value = _finite_or_none(params.get(param_name))
            if (
                base_value is None
                or candidate_value is None
                or base_value <= 0.0
                or candidate_value <= 0.0
            ):
                continue
            delta = math.log(candidate_value / base_value)
            for outcome_name in predicted:
                predicted[outcome_name] += (
                    delta
                    * float(effects.get(outcome_name, 0.0) or 0.0)
                    * confidence
                )
        candidate["gradient_online_delta_prediction"] = predicted
        candidate["gradient_pre_online_rank"] = int(candidate.get("rank") or 0)
        return (
            int(candidate.get("strict_plan_replay_priority") is True),
            -predicted[target_resource],
            -int(candidate.get("rank") or 0),
        )

    reranked = sorted(copy.deepcopy(remaining_candidates), key=ranking_key, reverse=True)
    for online_rank, candidate in enumerate(reranked, start=1):
        candidate["gradient_online_rank"] = online_rank
    return updated, reranked, {
        "sample_count": len(samples),
        "low_confidence_freeze_threshold": low_confidence_freeze_threshold,
        "parameter_updates": rows,
        "remaining_candidate_count": len(reranked),
        "target_resource": target_resource,
        "remaining_pre_online_ranks": [
            int(candidate.get("gradient_pre_online_rank", 0))
            for candidate in reranked
        ],
    }


def _exact_parameter_signature(params: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """Use exact float hex values; pruning/dedup must never round neighbors."""
    signature: List[tuple[str, str]] = []
    for name in sorted(params):
        value = float(params[name])
        if not math.isfinite(value):
            raise ValueError(f"candidate parameter is not finite: {name}={value!r}")
        signature.append((str(name), value.hex()))
    return tuple(signature)


_REQUIRED_GRADIENT_EVENT_FIELDS = frozenset({
    "feature_ratio", "predicted_resource", "utilization", "dScore/dU", "T",
    "confidence", "g_j", "eta", "delta_param", "candidate_score",
})


def _contains_qps_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized == "qps" or normalized.endswith("_qps"):
                return True
            if _contains_qps_key(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_qps_key(item) for item in value)
    return False


def _finite_gradient_value(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise RuntimeError(f"{label} must be a finite number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label} must be a finite number") from exc
    if not math.isfinite(parsed):
        raise RuntimeError(f"{label} must be a finite number")
    return parsed


def _validate_original_gradient_event(
    event: Mapping[str, Any], active_subset: Sequence[str],
) -> None:
    """Recompute raw g_j and its parameter update before a candidate can seal."""
    missing = sorted(_REQUIRED_GRADIENT_EVENT_FIELDS - set(event))
    if missing:
        raise RuntimeError(
            "formal candidate gradient event lacks required fields: "
            + ",".join(missing)
        )
    if event.get("gradient_source") != (
        "original_dScore_dU_times_T_times_confidence"
    ):
        raise RuntimeError("formal candidate did not use the original gradient")
    qps_forbidden_inputs = {
        name: event.get(name)
        for name in (
            "feature_ratio", "predicted_resource", "utilization",
            "dScore/dU", "T", "confidence",
        )
    }
    if _contains_qps_key(qps_forbidden_inputs):
        raise RuntimeError("QPS is forbidden in candidate gradient/score inputs")
    slopes = event.get("dScore/dU")
    table = event.get("T")
    confidence = event.get("confidence")
    gradients = event.get("g_j")
    deltas = event.get("delta_param")
    if not all(
        isinstance(item, Mapping)
        for item in (slopes, table, confidence, gradients, deltas)
    ):
        raise RuntimeError("formal candidate gradient event mappings are malformed")
    eta = _finite_gradient_value(event.get("eta"), "eta")
    if eta <= 0.0:
        raise RuntimeError("formal candidate eta must be positive")
    _finite_gradient_value(event.get("candidate_score"), "candidate_score")
    for parameter in active_subset:
        parameter_slopes = slopes.get(parameter)
        parameter_table = table.get(parameter)
        if not isinstance(parameter_slopes, Mapping) or not isinstance(parameter_table, Mapping):
            raise RuntimeError(f"formal candidate lacks gradient inputs for {parameter}")
        if set(parameter_slopes) != set(parameter_table) or not parameter_slopes:
            raise RuntimeError(f"formal candidate gradient resource shape differs for {parameter}")
        parameter_confidence = confidence.get(parameter)
        expected_gradient = 0.0
        for resource in sorted(parameter_slopes):
            if isinstance(parameter_confidence, Mapping):
                confidence_value = parameter_confidence.get(resource)
            else:
                confidence_value = parameter_confidence
            expected_gradient += (
                _finite_gradient_value(
                    parameter_slopes[resource], f"dScore/dU[{parameter},{resource}]"
                )
                * _finite_gradient_value(
                    parameter_table[resource], f"T[{parameter},{resource}]"
                )
                * _finite_gradient_value(
                    confidence_value, f"confidence[{parameter},{resource}]"
                )
            )
        actual_gradient = _finite_gradient_value(
            gradients.get(parameter), f"g_j[{parameter}]"
        )
        if not math.isclose(
            actual_gradient, expected_gradient, rel_tol=1e-12, abs_tol=1e-12,
        ):
            raise RuntimeError(
                f"formal candidate g_j for {parameter} is not "
                "sum(dScore/dU*T*confidence)"
            )
        delta = deltas.get(parameter)
        if not isinstance(delta, Mapping):
            raise RuntimeError(f"formal candidate lacks delta_param for {parameter}")
        before = _finite_gradient_value(delta.get("before"), f"delta[{parameter}].before")
        proposed = _finite_gradient_value(
            delta.get("proposed_before_clip"), f"delta[{parameter}].proposed_before_clip"
        )
        expected_proposed = before * (1.0 + eta * actual_gradient)
        if not math.isclose(proposed, expected_proposed, rel_tol=1e-12, abs_tol=1e-12):
            raise RuntimeError(
                f"formal candidate delta_param for {parameter} is not derived from g_j and eta"
            )
        after = _finite_gradient_value(
            delta.get("after_clip"), f"delta[{parameter}].after_clip"
        )
        absolute = _finite_gradient_value(
            delta.get("absolute"), f"delta[{parameter}].absolute"
        )
        if not math.isclose(absolute, after - before, rel_tol=1e-12, abs_tol=1e-12):
            raise RuntimeError(f"formal candidate absolute delta differs for {parameter}")


def _attach_replay_three_level_fingerprint(
    validation: Mapping[str, Any], candidate: Mapping[str, Any], benchmark: str,
) -> Dict[str, Any]:
    """Attach L1/L2/L3 evidence; missing levels stay independent and ineligible."""
    attached = copy.deepcopy(dict(validation))
    fingerprint_candidate = copy.deepcopy(dict(candidate))
    fingerprint_candidate["candidate_optimized_params"] = copy.deepcopy(
        candidate.get("optimized_params") or {}
    )
    fingerprint_candidate["candidate_final_validation"] = attached
    fingerprint = build_candidate_fingerprint(fingerprint_candidate, benchmark)
    attached["three_level_fingerprint"] = fingerprint
    attached["true_duplicate_eligible"] = fingerprint.get(
        "true_duplicate_usable"
    ) is True
    attached["missing_fingerprint_kept_independent"] = fingerprint.get(
        "true_duplicate_usable"
    ) is not True
    return attached


def _parameter_signature_token(params: Mapping[str, Any]) -> str:
    payload = json.dumps(
        _exact_parameter_signature(params),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _formal_raw_all_subset_rankings(
    all_candidate_rankings: Sequence[Mapping[str, Any]],
    active_param_count: int,
) -> List[Dict[str, Any]]:
    """Seal one globally score-ranked winner for every non-empty subset."""
    if active_param_count <= 0:
        raise ValueError("active_param_count must be positive")
    expected = (1 << active_param_count) - 1
    rankings = [copy.deepcopy(dict(item)) for item in all_candidate_rankings]
    if len(rankings) != expected:
        raise RuntimeError(
            "formal all-subset ranking is incomplete: "
            f"actual={len(rankings)}, expected={expected}"
        )
    subset_ids = [str(item.get("subset_id") or "") for item in rankings]
    if any(not subset_id for subset_id in subset_ids):
        raise RuntimeError("formal all-subset ranking contains an empty subset_id")
    if len(set(subset_ids)) != expected:
        raise RuntimeError("formal all-subset ranking contains duplicate subset_id values")
    previous_score = math.inf
    unresponsive_nonbaseline: List[str] = []
    nonbaseline_candidate_count = 0
    for rank, ranking in enumerate(rankings, 1):
        try:
            candidate_score = float(ranking["final_score"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"formal all-subset ranking {rank} lacks finite final_score"
            ) from exc
        if not math.isfinite(candidate_score):
            raise RuntimeError(
                f"formal all-subset ranking {rank} has non-finite final_score"
            )
        if candidate_score > previous_score:
            raise RuntimeError(
                "formal all-subset ranking is not globally ordered by "
                "candidate_score descending"
            )
        previous_score = candidate_score
        params = ranking.get("optimized_params")
        initial_params = ranking.get("initial_params")
        if not isinstance(params, Mapping) or not isinstance(initial_params, Mapping):
            raise RuntimeError(
                f"formal all-subset ranking {rank} lacks parameter identity"
            )
        expected_signature = [
            list(item) for item in _exact_parameter_signature(params)
        ]
        identity = ranking.get("candidate_estimation_identity")
        if (
            not isinstance(identity, Mapping)
            or identity.get("verified") is not True
            or identity.get("requested_parameter_signature") != expected_signature
            or identity.get("metadata_parameter_signature") != expected_signature
        ):
            raise RuntimeError(
                f"formal all-subset ranking {rank} lacks exact candidate estimator identity"
            )
        plan_validation = validate_canonical_plan_bundle(
            ranking.get("canonical_plan_bundle")
        )
        if plan_validation.get("valid") is not True:
            raise RuntimeError(
                f"formal all-subset ranking {rank} lacks a valid canonical plan bundle: "
                f"{plan_validation.get('reasons')}"
            )
        final_resource = ranking.get("final_resource")
        initial_resource = ranking.get("initial_resource")
        if not isinstance(final_resource, Mapping) or not isinstance(initial_resource, Mapping):
            raise RuntimeError(
                f"formal all-subset ranking {rank} lacks initial/final resource evidence"
            )
        changed_params = (
            _exact_parameter_signature(params)
            != _exact_parameter_signature(initial_params)
        )
        same_resource = (
            _exact_parameter_signature(final_resource)
            == _exact_parameter_signature(initial_resource)
        )
        same_plan_work = (
            identity.get("candidate_plan_work_sha256")
            == identity.get("baseline_plan_work_sha256")
        )
        if changed_params:
            nonbaseline_candidate_count += 1
            if same_resource and same_plan_work:
                unresponsive_nonbaseline.append(str(ranking.get("subset_id") or rank))
        gradient_events = ranking.get("gradient_events")
        active_subset = ranking.get("active_subset")
        if (
            not isinstance(active_subset, Sequence)
            or isinstance(active_subset, (str, bytes))
            or not active_subset
            or not isinstance(gradient_events, Sequence)
            or isinstance(gradient_events, (str, bytes))
            or not gradient_events
        ):
            raise RuntimeError(
                f"formal all-subset ranking {rank} lacks raw gradient events"
            )
        for event in gradient_events:
            if not isinstance(event, Mapping):
                raise RuntimeError(
                    f"formal all-subset ranking {rank} has malformed gradient event"
                )
            _validate_original_gradient_event(event, [str(name) for name in active_subset])
        ranking["candidate_score"] = candidate_score
        ranking["rank"] = rank
        ranking["formal_rank"] = rank
        ranking["formal_ranking_contract"] = (
            "candidate_score_desc_global_all_nonempty_subsets_fixed_top5_v2"
        )
    if (
        nonbaseline_candidate_count > 0
        and len(unresponsive_nonbaseline) == nonbaseline_candidate_count
    ):
        raise RuntimeError(
            "formal all-subset candidate estimator is unresponsive: every "
            "nonbaseline candidate has baseline resource and plan work; "
            "refusing to seal a stale Top-5"
        )
    if nonbaseline_candidate_count == 0:
        raise RuntimeError(
            "formal all-subset candidate exploration produced only baseline "
            "winners; refusing to seal an all-equal Top-5"
        )
    return rankings


def _augment_rankings_for_plan_escape(
    rankings: Sequence[Mapping[str, Any]],
    *,
    config: Mapping[str, Any],
    adapter: MySQLCostAdapter,
    baseline: Mapping[str, float],
    ranges: Mapping[str, ParameterRange],
    sensitivity: Mapping[str, Mapping[str, Any]],
    capability: Mapping[str, float],
    target_resource: str,
    optimizer_options: Mapping[str, Any],
    estimators: List[Any],
    excluded_signature_tokens: Sequence[str] = (),
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Add deterministic escape batches until plan diversity or the cap."""
    outer_cfg = config.get("formal_outer_protocol") or {}
    escape_cfg = outer_cfg.get("plan_basin_escape") or {}
    if not isinstance(escape_cfg, Mapping) or escape_cfg.get("enabled") is not True:
        return copy.deepcopy(list(rankings)), {
            "enabled": False,
            "reason": "plan_basin_escape_not_enabled",
            "batches_used": 1,
        }

    max_batches = int(escape_cfg.get("maximum_batches", 5))
    required_clusters = int(escape_cfg.get("required_plan_clusters", 2))
    maximum_candidates = int(escape_cfg.get("maximum_candidates_per_batch", 128))
    radius = float(escape_cfg.get("orthogonal_radius", 0.08))
    if not 1 <= max_batches <= 5:
        raise ValueError("plan_basin_escape.maximum_batches must be in [1, 5]")
    if required_clusters <= 0 or maximum_candidates <= 0:
        raise ValueError("plan-basin escape cluster/candidate limits must be positive")

    score_keys = {
        "reward_type", "penalty_type", "use_continuous", "continuous_type",
        "overload_threshold", "score_mode", "resource_weights",
        "resource_reward_weights", "resource_penalty_weights",
        "resource_target_ranges",
    }
    calculator = ResourceUtilityCalculator(
        **{
            key: value
            for key, value in optimizer_options.items()
            if key in score_keys and value is not None
        }
    )
    excluded_tokens = {str(value) for value in excluded_signature_tokens if str(value)}
    combined = [
        copy.deepcopy(dict(item))
        for item in rankings
        if _parameter_signature_token(item.get("optimized_params") or {})
        not in excluded_tokens
    ]
    excluded_initial_count = len(rankings) - len(combined)
    signatures = {
        _exact_parameter_signature(item.get("optimized_params") or {})
        for item in rankings
    }
    normal_candidates = copy.deepcopy(list(rankings))
    batch_audit: List[Dict[str, Any]] = []

    def strict_cluster_count(clustered: Mapping[str, Any]) -> int:
        return sum(
            1
            for cluster in (clustered.get("clusters") or [])
            if isinstance(cluster, Mapping)
            and cluster.get("strict_fingerprint_present") is True
        )

    def rerank() -> None:
        combined.sort(
            key=lambda item: (
                -float(item.get("final_score", -math.inf)),
                int(item.get("escape_batch_index", 1)),
                str(item.get("subset_id") or ""),
                _exact_parameter_signature(item.get("optimized_params") or {}),
            )
        )
        for rank, item in enumerate(combined, 1):
            item["rank"] = rank

    rerank()
    initial_clusters = cluster_ranked_candidates(combined)
    batch_audit.append({
        "batch_index": 1,
        "kind": "all_nonempty_subset_local_optimization",
        "new_candidates": len(combined),
        "strict_plan_cluster_count": strict_cluster_count(initial_clusters),
        "diagnostic_cluster_count": int(initial_clusters["strict_plan_cluster_count"]),
    })

    for batch_index in range(2, max_batches + 1):
        cluster_count = strict_cluster_count(cluster_ranked_candidates(combined))
        if cluster_count >= required_clusters:
            break
        generated = generate_escape_batch(
            batch_index=batch_index,
            incumbent=baseline,
            parameter_ranges=ranges,
            sensitivity_table=sensitivity,
            target_resource=target_resource,
            base_candidates=normal_candidates,
            seen_signatures=signatures,
            radius=radius,
            maximum_candidates=maximum_candidates,
        )
        added = 0
        for ordinal, candidate in enumerate(generated, 1):
            params = candidate["optimized_params"]
            signature = _exact_parameter_signature(params)
            if (
                signature in signatures
                or _parameter_signature_token(params) in excluded_tokens
            ):
                continue
            estimator = _build_estimator_for_incumbent(
                config, adapter, baseline
            )
            setattr(estimator, "current_label", f"plan_escape_{batch_index:02d}_{ordinal:04d}")
            resource = estimator(config.get("sql_set"), copy.deepcopy(params))
            stats = copy.deepcopy(getattr(estimator, "last_stats", None))
            metadata = copy.deepcopy(getattr(estimator, "last_metadata", None))
            estimators.append(estimator)
            final_score = float(
                calculator.calculate_utility_score(resource, dict(capability))
            )
            initial_score = float(
                normal_candidates[0].get("initial_score", final_score)
                if normal_candidates else final_score
            )
            row = {
                **copy.deepcopy(candidate),
                "subset_id": f"escape_{batch_index:02d}_{ordinal:04d}",
                "active_subset": [
                    name for name in baseline
                    if float(params[name]) != float(baseline[name])
                ],
                "canonical_subset_order": len(combined) + added + 1,
                "initial_params": copy.deepcopy(dict(baseline)),
                "initial_resource": copy.deepcopy(
                    normal_candidates[0].get("initial_resource") if normal_candidates else {}
                ),
                "final_resource": copy.deepcopy(resource),
                "initial_score": initial_score,
                "final_score": final_score,
                "score_improvement": final_score - initial_score,
                "iterations": 0,
                "estimator_stats": stats,
            }
            if isinstance(metadata, Mapping):
                row["final_estimation_metadata"] = copy.deepcopy(dict(metadata))
                pruning_input = metadata.get("optimistic_replay_pruning_input")
                if isinstance(pruning_input, Mapping):
                    row["optimistic_replay_pruning_input"] = (
                        sanitize_optimistic_pruning_metadata_tree(pruning_input)
                    )
            signatures.add(signature)
            combined.append(row)
            added += 1
        rerank()
        updated_clusters = cluster_ranked_candidates(combined)
        batch_audit.append({
            "batch_index": batch_index,
            "kind": generated[0]["candidate_origin"] if generated else "exhausted",
            "generated_candidates": len(generated),
            "new_candidates": added,
            "strict_plan_cluster_count": strict_cluster_count(updated_clusters),
            "diagnostic_cluster_count": int(updated_clusters["strict_plan_cluster_count"]),
        })
        if added == 0 and batch_index == max_batches:
            break

    final_clusters = cluster_ranked_candidates(combined)
    final_strict_cluster_count = strict_cluster_count(final_clusters)
    return combined, {
        "enabled": True,
        "contract": "deterministic_plan_basin_escape_v1",
        "maximum_batches": max_batches,
        "batches_used": len(batch_audit),
        "required_plan_clusters": required_clusters,
        "final_strict_plan_cluster_count": final_strict_cluster_count,
        "final_diagnostic_cluster_count": int(final_clusters["strict_plan_cluster_count"]),
        "missing_fingerprints_count_toward_quota": False,
        "excluded_candidate_count_before_quota": excluded_initial_count,
        "quota_satisfied": final_strict_cluster_count >= required_clusters,
        "stop_reason": (
            "plan_cluster_quota_satisfied"
            if final_strict_cluster_count >= required_clusters
            else "maximum_batches_or_candidate_space_exhausted"
        ),
        "batches": batch_audit,
    }


def _write_inner_audit_artifacts(
    *,
    output_dir: Path,
    stamp: str,
    subset_summary: Any,
    active_params: List[str],
    sensitivity_table: Mapping[str, Any],
    capability: Mapping[str, float],
    config: Mapping[str, Any],
) -> Dict[str, Any]:
    """Materialize the §26 append-only inner JSONL and subset audit checksum."""
    audit_dir = output_dir / f"formal_audit_{stamp}_{os.getpid()}_{time.time_ns()}"
    audit_dir.mkdir(parents=True, exist_ok=False)
    inner_path = audit_dir / "inner_iterations.jsonl"
    subset_path = audit_dir / "subset_audit.json"
    subset_path.write_text(
        json.dumps(subset_summary.audit_records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    estimator_cfg = config.get("estimator") or {}
    estimator_version = estimator_cfg.get("version") or estimator_cfg.get("model_version")
    estimator_checksum = estimator_cfg.get("sha256") or estimator_cfg.get("checksum")
    sql_registry_checksum = config.get("sql_registry_sha256")
    record_count = 0
    with inner_path.open("x", encoding="utf-8") as destination:
        for subset in subset_summary.audit_records:
            if subset.get("status") != "success":
                continue
            active_subset = list(subset.get("active_subset") or [])
            fixed_params = [name for name in active_params if name not in active_subset]
            source = Path(str(subset["log_path"]))
            for raw_line in source.read_text(encoding="utf-8").splitlines():
                source_row = json.loads(raw_line)
                if source_row.get("event") != "experience_table_sgd_step":
                    continue
                resource_candidate = source_row.get("resource") or {}
                utilization = {
                    name: float(resource_candidate[name]) / float(capability[name])
                    for name in ("cpu", "memory", "io")
                    if name in resource_candidate and float(capability[name]) > 0.0
                }
                params_before = copy.deepcopy(subset.get("p_real") or {})
                candidate_params = copy.deepcopy(source_row.get("params") or {})
                changed = [
                    name for name in active_subset
                    if params_before.get(name) != candidate_params.get(name)
                ]
                record = {
                    "outer_iter": subset_summary.round_index,
                    "subset_id": subset["subset_id"],
                    "canonical_subset_order": subset["canonical_subset_order"],
                    "active_subset": active_subset,
                    "fixed_params": fixed_params,
                    "inner_iter": source_row.get("iteration"),
                    "inner_algorithm": "experience_table_analytic_sgd_v1",
                    "params_before": params_before,
                    "candidate_params": candidate_params,
                    "params_after": candidate_params,
                    "changed_param_set": changed,
                    "experience_table": {
                        name: copy.deepcopy(sensitivity_table.get(name) or {})
                        for name in active_subset
                    },
                    "resource_utilization": utilization,
                    "gradient_source": source_row.get("gradient_source"),
                    "direction": source_row.get("directions"),
                    "eta_before": source_row.get("learning_rate"),
                    "eta_after": source_row.get("learning_rate"),
                    "gamma": None,
                    "patience": None,
                    "no_improve_count": None,
                    "resource_real": None,
                    "resource_opt_before": None,
                    "resource_candidate": resource_candidate,
                    "candidate_score": source_row.get("score"),
                    "accepted_inner": source_row.get("accepted_inner") is True,
                    "acceptance_reference": "strict_estimated_resource_utility_improvement",
                    "estimator_version": estimator_version,
                    "estimator_sha256": estimator_checksum,
                    "sql_registry_sha256": sql_registry_checksum,
                    "explain_feature_evidence_pointer": (
                        source_row.get("estimation_metadata") or {}
                    ).get("summary_path"),
                }
                destination.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                destination.flush()
                record_count += 1
        os.fsync(destination.fileno())
    return {
        "directory": str(audit_dir.resolve()),
        "inner_jsonl_path": str(inner_path.resolve()),
        "inner_jsonl_sha256": _sha256_file(inner_path),
        "inner_record_count": record_count,
        "subset_audit_path": str(subset_path.resolve()),
        "subset_audit_sha256": _sha256_file(subset_path),
    }


def load_config(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("optimizer config must be a JSON object")
    return payload


def validate_config(config: Mapping[str, Any]) -> None:
    capability = config.get("capability")
    if not isinstance(capability, Mapping):
        raise ValueError("config.capability is required")
    for resource in ("cpu", "memory", "io"):
        if float(capability.get(resource, 0.0)) <= 0:
            raise ValueError(f"capability.{resource} must be > 0")
    cost_cfg = config.get("cost_parameters")
    if not isinstance(cost_cfg, Mapping):
        raise ValueError("config.cost_parameters is required")
    active = cost_cfg.get("active")
    if not isinstance(active, list) or not active:
        raise ValueError("cost_parameters.active must be a non-empty list")
    unknown = sorted(set(str(item) for item in active) - set(MYSQL_COST_KEYS))
    if unknown:
        raise ValueError(f"unknown MySQL cost parameters: {unknown}")
    freeze_id = str(config.get("freeze_id") or "").strip()
    formal_scope = str(config.get("formal_scope") or "").strip().lower()
    if formal_scope == "mysql_only" and freeze_id not in FORMAL_FROZEN_ACTIVE:
        raise ValueError(
            "formal mysql_only optimizer requires a registered freeze_id: "
            f"actual={freeze_id!r}"
        )
    frozen_active_by_benchmark = FORMAL_FROZEN_ACTIVE.get(freeze_id)
    if frozen_active_by_benchmark is not None:
        freeze_label = "v2" if freeze_id == FROZEN_MYSQL_V2_ID else "v3"
        frozen_outer_cfg = config.get("formal_outer_protocol") or {}
        if not isinstance(frozen_outer_cfg, Mapping):
            raise ValueError("config.formal_outer_protocol must be an object")
        if int(frozen_outer_cfg.get("formal_search_initial_replay_top_k", 5)) != 5:
            raise ValueError(
                f"{freeze_label} formal_search_initial_replay_top_k must equal 5"
            )
        benchmark = str(config.get("benchmark") or "").strip().lower()
        expected_active = frozen_active_by_benchmark.get(benchmark)
        if expected_active is None:
            raise ValueError(
                f"unsupported {freeze_label} benchmark: {benchmark!r}"
            )
        if [str(item) for item in active] != expected_active:
            raise ValueError(
                f"{freeze_label} active parameters mismatch for {benchmark}: "
                f"actual={active}, expected={expected_active}"
            )
        ranges_cfg = cost_cfg.get("ranges") or {}
        prior_accepted_shrinks = int(
            ((config.get("formal_outer_protocol") or {}).get("accepted_shrink_count", 0))
        )
        for name in expected_active:
            item = ranges_cfg.get(name) or {}
            expected_low, expected_high = FORMAL_FROZEN_RANGES[freeze_id][name]
            actual_low = float(item.get("min_value", math.nan))
            actual_high = float(item.get("max_value", math.nan))
            valid_initial = actual_low == expected_low and actual_high == expected_high
            valid_shrunk = (
                prior_accepted_shrinks > 0
                and expected_low <= actual_low < actual_high <= expected_high
            )
            if not (valid_initial or valid_shrunk):
                raise ValueError(
                    f"{freeze_label} range mismatch for {name}: actual={item}, "
                    f"expected=[{expected_low}, {expected_high}]"
                )
    baseline_values = cost_cfg.get("baseline_values") or {}
    if not isinstance(baseline_values, Mapping):
        raise ValueError("cost_parameters.baseline_values must be an object")
    unknown_baseline = sorted(set(str(item) for item in baseline_values) - set(str(item) for item in active))
    if unknown_baseline:
        raise ValueError(f"baseline values must be active parameters: {unknown_baseline}")
    if any(float(value) <= 0 for value in baseline_values.values()):
        raise ValueError("cost parameter baseline values must be positive")
    estimator_cfg = config.get("estimator")
    if not isinstance(estimator_cfg, Mapping):
        raise ValueError("config.estimator is required")
    estimator_type = str(estimator_cfg.get("type", "command"))
    if estimator_type == "command" and not estimator_cfg.get("command"):
        raise ValueError("estimator.command is required for command mode")
    if estimator_type == "python" and not estimator_cfg.get("callable"):
        raise ValueError("estimator.callable is required for python mode")
    if estimator_type in {"trained_models", "plan_physics", "ratio_scale"}:
        feature_source = estimator_cfg.get("feature_source")
        if not isinstance(feature_source, Mapping):
            raise ValueError(
                f"estimator.feature_source is required for {estimator_type} mode"
            )
        if frozen_active_by_benchmark is not None:
            registry_ids = feature_source.get("registry_query_ids")
            registry_sha = str(feature_source.get("workload_registry_sha256") or "").lower()
            registry_path = feature_source.get("workload_registry_path")
            if not isinstance(registry_ids, list) or not registry_ids or len(set(map(str, registry_ids))) != len(registry_ids):
                raise ValueError(
                    "formal trained estimator requires ordered unique feature_source.registry_query_ids"
                )
            if len(registry_sha) != 64 or any(character not in "0123456789abcdef" for character in registry_sha):
                raise ValueError(
                    "formal trained estimator requires feature_source.workload_registry_sha256"
                )
            if (
                not isinstance(registry_path, str)
                or not registry_path
                or not Path(registry_path).is_absolute()
            ):
                raise ValueError(
                    "formal trained estimator requires absolute feature_source.workload_registry_path"
                )
            if feature_source.get("require_strict_plan_registry") is not True:
                raise ValueError(
                    "formal trained estimator requires require_strict_plan_registry=true"
                )
            if (
                feature_source.get("workload_freeze_id") != freeze_id
                or feature_source.get("benchmark_id") != str(config.get("benchmark") or "")
            ):
                raise ValueError(
                    "formal plan registry must bind the active freeze_id and benchmark"
                )
            registry = Path(registry_path)
            try:
                registry_payload = json.loads(registry.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"formal plan registry cannot be read: {registry}: {exc}"
                ) from exc
            if hashlib.sha256(registry.read_bytes()).hexdigest() != registry_sha:
                raise ValueError("formal plan registry SHA-256 mismatch")
            if not isinstance(registry_payload, Mapping):
                raise ValueError("formal plan registry root must be an object")
            if (
                registry_payload.get("freeze_id") != freeze_id
                or registry_payload.get("benchmark") != str(config.get("benchmark") or "")
            ):
                raise ValueError("formal plan registry artifact binding mismatch")
            if registry_payload.get("execution_authorized") is False:
                raise ValueError("formal plan registry execution is not authorized")
            if not (
                registry_payload.get("immutable") is True
                or registry_payload.get("execution_authorized") is True
            ):
                raise ValueError(
                    "formal plan registry lacks immutable or execution-authorized status"
                )
            queries = registry_payload.get("queries")
            if isinstance(queries, list):
                incomplete_dynamic = [
                    str(query.get("query_id") or query.get("statement_id") or index)
                    for index, query in enumerate(queries, 1)
                    if isinstance(query, Mapping)
                    and query.get("dynamic") is True
                    and not query.get("representative_bindings")
                ]
                if incomplete_dynamic:
                    raise ValueError(
                        "formal dynamic plan registry lacks representative bindings: "
                        f"{incomplete_dynamic[:5]}"
                    )
    if estimator_type in {"plan_physics", "ratio_scale"}:
        baseline_real = estimator_cfg.get("baseline_real")
        if not isinstance(baseline_real, Mapping):
            raise ValueError(
                f"estimator.baseline_real is required for {estimator_type} mode"
            )
        for resource in ("cpu", "memory", "io"):
            try:
                value = float(baseline_real[resource])
            except (KeyError, TypeError, ValueError):
                raise ValueError(
                    "estimator.baseline_real must contain numeric cpu/memory/io"
                ) from None
            if not math.isfinite(value) or value < 0:
                raise ValueError(
                    f"estimator.baseline_real.{resource} must be finite and non-negative"
                )
    if estimator_type not in {
        "command", "python", "trained_models", "plan_physics", "ratio_scale",
    }:
        raise ValueError(
            "estimator.type must be command, python, trained_models, plan_physics or ratio_scale"
        )
    optimizer_cfg = config.get("optimizer") or {}
    if not isinstance(optimizer_cfg, Mapping):
        raise ValueError("config.optimizer must be an object")
    if frozen_active_by_benchmark is not None:
        if optimizer_cfg.get("candidate_generator") != (
            "all_nonempty_subsets_experience_table_analytic_sgd_v1"
        ):
            raise ValueError(
                f"{freeze_label} optimizer candidate_generator must be "
                "experience-table analytic SGD"
            )
    subset_max_workers = int(optimizer_cfg.get("subset_max_workers", 1))
    if subset_max_workers <= 0:
        raise ValueError("optimizer.subset_max_workers must be a positive integer")
    outer_cfg = config.get("formal_outer_protocol") or {}
    if not isinstance(outer_cfg, Mapping):
        raise ValueError("config.formal_outer_protocol must be an object")
    if frozen_active_by_benchmark is not None and int(
        outer_cfg.get("target_accepted_shrinks", 10)
    ) != 10:
        raise ValueError(
            f"{freeze_label} formal outer target_accepted_shrinks must equal 10"
        )
    accepted_shrinks = int(outer_cfg.get("accepted_shrink_count", 0))
    rejected_count = int(outer_cfg.get("rejected_count", 0))
    if not 0 <= accepted_shrinks <= int(outer_cfg.get("target_accepted_shrinks", 10)):
        raise ValueError("accepted_shrink_count must be between zero and target")
    if rejected_count < 0:
        raise ValueError("rejected_count must be non-negative")
    if int(outer_cfg.get("max_outer_attempts", 100)) <= 0:
        raise ValueError("max_outer_attempts must be positive")
    acceptance_tier = str(
        outer_cfg.get("acceptance_tier", DEFAULT_ACCEPTANCE_TIER)
    ).strip().lower()
    if acceptance_tier not in ACCEPTANCE_TIER_RANK:
        raise ValueError(
            "formal_outer_protocol.acceptance_tier must be one of "
            f"{sorted(ACCEPTANCE_TIER_RANK)}"
        )
    downgrade_after = int(
        outer_cfg.get(
            "acceptance_tier_downgrade_after_rounds",
            DEFAULT_ACCEPTANCE_TIER_DOWNGRADE_AFTER_ROUNDS,
        )
    )
    if downgrade_after != DEFAULT_ACCEPTANCE_TIER_DOWNGRADE_AFTER_ROUNDS:
        raise ValueError(
            "formal acceptance tier downgrade threshold must equal 2 outer rounds"
        )
    tier_failed_rounds = int(
        outer_cfg.get("acceptance_tier_consecutive_failed_rounds", 0)
    )
    valid_tier_failure_state = (
        0 <= tier_failed_rounds <= downgrade_after
        if acceptance_tier == "fallback_qps"
        else 0 <= tier_failed_rounds < downgrade_after
    )
    if not valid_tier_failure_state:
        raise ValueError(
            "acceptance_tier_consecutive_failed_rounds is inconsistent with tier"
        )
    initial_top_k = int(outer_cfg.get("formal_search_initial_replay_top_k", 5))
    benchmark_name = str(config.get("benchmark") or "").strip()
    scenario_cap = formal_replay_cap(benchmark_name) if benchmark_name else 5
    max_top_k = int(
        outer_cfg.get("formal_search_max_replay_top_k", scenario_cap)
    )
    if initial_top_k != 5 or max_top_k != 5:
        raise ValueError(
            "formal search requires initial and maximum replay Top-K to equal 5: "
            f"initial={initial_top_k}, maximum={max_top_k}"
        )
    extension_cfg = outer_cfg.get("plan_tree_replay_extension") or {}
    if not isinstance(extension_cfg, Mapping):
        raise ValueError("formal plan_tree_replay_extension must be an object")
    extension_enabled = extension_cfg.get("enabled", True)
    if not isinstance(extension_enabled, bool):
        raise ValueError("plan_tree_replay_extension.enabled must be boolean")
    extension_batch_size = int(extension_cfg.get("batch_size", 5))
    extension_maximum = extension_cfg.get("maximum_additional_replays")
    if extension_batch_size <= 0 or extension_batch_size > 127:
        raise ValueError("plan-tree replay extension batch_size must be in [1, 127]")
    if extension_maximum is not None and not 0 <= int(extension_maximum) <= 127:
        raise ValueError(
            "plan-tree replay extension maximum_additional_replays must be in [0, 127]"
        )
    _gradient_replay_stage_action(
        validated_count=0,
        eligible_count=0,
        available_count=1,
        initial_top_k=initial_top_k,
        max_top_k=max_top_k,
    )


def _build_ranges(
    config: Mapping[str, Any],
    baseline: Mapping[str, float],
    active: List[str],
) -> Dict[str, ParameterRange]:
    ranges_cfg = config.get("ranges") if isinstance(config.get("ranges"), Mapping) else {}
    ranges: Dict[str, ParameterRange] = {}
    for name in active:
        item = ranges_cfg.get(name, {}) if isinstance(ranges_cfg, Mapping) else {}
        if not isinstance(item, Mapping):
            raise ValueError(f"range for {name} must be an object")
        base = float(baseline[name])
        minimum = float(item.get("min_value", base * float(item.get("min_multiplier", 0.25))))
        maximum = float(item.get("max_value", base * float(item.get("max_multiplier", 4.0))))
        if minimum <= 0 or maximum <= minimum:
            raise ValueError(f"invalid positive range for {name}: [{minimum}, {maximum}]")
        ranges[name] = ParameterRange(minimum, maximum)
    return ranges


def _build_estimator(config: Mapping[str, Any], adapter: MySQLCostAdapter) -> Any:
    estimator_config = copy.deepcopy(dict(config["estimator"]))
    if estimator_config.get("type") in {"plan_physics", "ratio_scale"}:
        # Every independently-created subset/escape estimator must calibrate
        # against the incumbent, never against whichever candidate calls it first.
        configured_baseline: Any = config.get("baseline")
        if not isinstance(configured_baseline, Mapping) or not configured_baseline:
            cost_parameters = config.get("cost_parameters")
            configured_baseline = (
                cost_parameters.get("baseline_values")
                if isinstance(cost_parameters, Mapping)
                else None
            )
        if not isinstance(configured_baseline, Mapping) or not configured_baseline:
            raise ValueError(
                "plan/ratio estimator requires an explicit non-empty incumbent "
                "baseline"
            )
        estimator_config.setdefault(
            "baseline_params", copy.deepcopy(dict(configured_baseline))
        )
    return build_resource_estimator(adapter, estimator_config)


def _build_estimator_for_incumbent(
    config: Mapping[str, Any],
    adapter: MySQLCostAdapter,
    baseline: Mapping[str, float],
) -> Any:
    if not baseline:
        raise ValueError("runtime incumbent baseline must not be empty")
    runtime_config = copy.deepcopy(dict(config))
    runtime_config["baseline"] = copy.deepcopy(dict(baseline))
    return _build_estimator(runtime_config, adapter)


def _run_optimization_unleased(
    config: Mapping[str, Any],
    *,
    client: Optional[SQLClient] = None,
    real_replay_callback: Optional[RealReplayCallback] = None,
    baseline_validation: Optional[Mapping[str, Any]] = None,
    method_id: str = "experience_table_sgd",
) -> Any:
    """Run tuning; restore exact original values unless ``commit_best`` is true."""
    validate_config(config)
    mysql_cfg = config.get("mysql") if isinstance(config.get("mysql"), Mapping) else {}
    sql_client = client or MySQLCLIClient(mysql_cfg)
    adapter = MySQLCostAdapter(
        sql_client,
        engine_name=str(mysql_cfg.get("engine_name", "default")),
        device_type=int(mysql_cfg.get("device_type", 0)),
    )
    original_snapshot = adapter.snapshot()
    dirty_cost_overrides = [
        item.name
        for item in original_snapshot
        if item.name in MYSQL_COST_KEYS and item.raw_value is not None
    ]
    if dirty_cost_overrides:
        raise RuntimeError(
            "formal baseline requires all MySQL optimizer-cost overrides NULL: "
            f"{dirty_cost_overrides}"
        )
    baseline_values = {
        str(name): float(value)
        for name, value in (config["cost_parameters"].get("baseline_values") or {}).items()
    }
    if baseline_values:
        try:
            adapter.apply(baseline_values)
            snapshot = adapter.snapshot()
        except BaseException:
            adapter.restore(original_snapshot)
            raise
    else:
        snapshot = original_snapshot
    baseline_all = {item.name: item.effective_value for item in snapshot}
    cost_cfg = config["cost_parameters"]
    active = [str(item) for item in cost_cfg["active"]]
    baseline = {name: baseline_all[name] for name in active}
    ranges = _build_ranges(cost_cfg, baseline, active)
    sensitivity_cfg = config.get("sensitivity_table", {})
    sensitivity = {
        name: {
            resource: float(value)
            for resource, value in (sensitivity_cfg.get(name) or {}).items()
        }
        for name in active
    }
    output_cfg = config.get("output") if isinstance(config.get("output"), Mapping) else {}
    output_dir = Path(str(output_cfg.get("directory", "history/mysql")))
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    snapshot_path = output_dir / f"cost_snapshot_{stamp}.json"
    adapter.save_snapshot(snapshot_path, snapshot)
    original_snapshot_path = output_dir / f"original_cost_snapshot_{stamp}.json"
    if baseline_values:
        adapter.save_snapshot(original_snapshot_path, original_snapshot)
    optimizer_cfg = dict(config.get("optimizer", {}))
    subset_max_workers = int(optimizer_cfg.get("subset_max_workers", 1))
    if subset_max_workers <= 0:
        raise ValueError("optimizer.subset_max_workers must be a positive integer")
    capability = {key: float(value) for key, value in config["capability"].items()}
    outer_cfg = config.get("formal_outer_protocol") or {}
    round_index = int(optimizer_cfg.get("round_index", 1))
    subset_schedule = [
        f"subset_{order:04d}__{'__'.join(subset)}"
        for order, subset in enumerate(
            (
                subset
                for size in range(1, len(active) + 1)
                for subset in itertools.combinations(active, size)
            ),
            1,
        )
    ]
    scoring_cfg = config.get("scoring") if isinstance(config.get("scoring"), Mapping) else {}
    optimizer_options: Dict[str, Any] = {
        "reward_type": str(scoring_cfg.get("reward_type", "log")),
        "penalty_type": str(scoring_cfg.get("penalty_type", "power")),
        "overload_threshold": float(scoring_cfg.get("overload_threshold", 0.8)),
        "score_mode": str(scoring_cfg.get("score_mode", "band_target")),
        "resource_weights": scoring_cfg.get("resource_weights"),
        "resource_reward_weights": scoring_cfg.get("resource_reward_weights"),
        "resource_penalty_weights": scoring_cfg.get("resource_penalty_weights"),
        "resource_target_ranges": scoring_cfg.get("resource_target_ranges"),
        "verbose": bool(output_cfg.get("verbose", True)),
    }
    for key, value in optimizer_cfg.items():
        if key in FORMAL_OPTIMIZER_OPTION_KEYS:
            optimizer_options[key] = value

    replay_ledger: FormalOuterReplayLedger | None = None
    if real_replay_callback is not None:
        checkpoint_path = _formal_checkpoint_path(
            outer_cfg.get("formal_checkpoint_path"), output_dir, round_index,
        )
        campaign_identity = {
            "exp_id": config.get("exp_id"),
            "benchmark": config.get("benchmark"),
            "method_id": method_id,
            "outer_iter": round_index,
            "active_params": active,
            "baseline_float_hex": {
                name: float(value).hex() for name, value in sorted(baseline.items())
            },
            "capability_float_hex": {
                name: float(value).hex() for name, value in sorted(capability.items())
            },
            "target_resource": config.get("target_resource", config.get("bottleneck")),
            "parameter_ranges_float_hex": {
                name: {
                    "min_value": float(item.min_value).hex(),
                    "max_value": float(item.max_value).hex(),
                }
                for name, item in sorted(ranges.items())
            },
            "sensitivity_table_float_hex": {
                name: {
                    resource: float(value).hex()
                    for resource, value in sorted(row.items())
                }
                for name, row in sorted(sensitivity.items())
            },
            "optimizer_contract": copy.deepcopy(optimizer_options),
            "estimator_contract": copy.deepcopy(config.get("estimator")),
            "workload_plan_freeze": {
                name: copy.deepcopy(config.get(name))
                for name in (
                    "freeze_id", "benchmark_manifest", "workload_registry",
                    "workload", "sql_set", "plan_contract", "plan_capture",
                    "evidence",
                )
            },
        }
        if checkpoint_path.exists():
            replay_ledger = FormalOuterReplayLedger.load_campaign_verified(
                checkpoint_path, campaign_identity=campaign_identity,
                ordered_subset_ids=subset_schedule,
            )
        else:
            replay_ledger = FormalOuterReplayLedger.create_campaign(
                checkpoint_path, outer_iter=round_index,
                campaign_identity=campaign_identity,
                ordered_subset_ids=subset_schedule,
            )

    estimators: List[Any] = []
    estimate_cache = _SharedEstimateFutureCache()

    def resource_estimation_factory(active_subset: tuple[str, ...], subset_id: str) -> Any:
        underlying = _build_estimator_for_incumbent(
            config, adapter, baseline
        )
        setattr(underlying, "active_subset", tuple(active_subset))
        setattr(underlying, "subset_id", subset_id)
        estimators.append(underlying)

        class CachedSubsetEstimator:
            def __call__(self, sql_set: Any, params: Dict[str, float]) -> EstimatePayload:
                def compute() -> EstimatePayload:
                    is_r0 = _exact_parameter_signature(params) == _exact_parameter_signature(baseline)
                    if is_r0 and replay_ledger is not None:
                        restored_r0 = replay_ledger.restored_r0_result()
                        if restored_r0 is not None:
                            return (
                                copy.deepcopy(dict(restored_r0["resource"])),
                                copy.deepcopy(restored_r0.get("stats")),
                                copy.deepcopy(restored_r0.get("metadata")),
                            )
                    resource = underlying(sql_set, params)
                    payload = (
                        resource,
                        copy.deepcopy(getattr(underlying, "last_stats", None)),
                        copy.deepcopy(getattr(underlying, "last_metadata", None)),
                    )
                    if is_r0 and replay_ledger is not None:
                        replay_ledger.mark_r0_completed({
                            "resource": copy.deepcopy(payload[0]),
                            "stats": copy.deepcopy(payload[1]),
                            "metadata": copy.deepcopy(payload[2]),
                        })
                    return payload

                payload, cache_metadata = estimate_cache.get_or_compute(params, compute)
                resource, stats, metadata = payload
                metadata_dict = dict(metadata) if isinstance(metadata, Mapping) else {}
                metadata_dict.update(
                    {
                        "subset_id": subset_id,
                        "active_subset": list(active_subset),
                        "estimate_cache": cache_metadata,
                    }
                )
                observed_params = metadata_dict.get("params")
                if not isinstance(observed_params, Mapping):
                    raise RuntimeError(
                        "formal estimator omitted candidate parameter identity evidence"
                    )
                requested_signature = _exact_parameter_signature(params)
                if _exact_parameter_signature(observed_params) != requested_signature:
                    raise RuntimeError(
                        "formal estimator metadata params differ from requested candidate"
                    )
                metadata_dict["candidate_estimator_parameter_signature"] = [
                    list(item) for item in requested_signature
                ]
                metadata_dict["candidate_estimator_identity_verified"] = True
                self.last_stats = copy.deepcopy(stats)
                self.last_metadata = metadata_dict
                return resource, stats, metadata_dict

        return CachedSubsetEstimator()

    target_resource_raw = str(
        config.get("target_resource", config.get("bottleneck", "")) or ""
    ).strip().lower()
    ranking_target_resource = (
        target_resource_raw if target_resource_raw in capability else None
    )
    commit_best = bool(config.get("commit_best", False))
    try:
        subset_summary = optimize_all_experience_table_subsets(
            resource_estimation_factory=resource_estimation_factory,
            capability=capability,
            p_real=baseline,
            sensitivity_table=sensitivity,
            param_ranges=ranges,
            sql_set=config.get("sql_set"),
            active_params=active,
            # Subsets perform model/experience-table estimation only.  They may
            # share exact-parameter Futures, but never mutate or replay against
            # MySQL here; real replays remain staged and serial below.
            max_workers=subset_max_workers,
            round_index=optimizer_cfg.get("round_index", 1),
            log_dir=str(output_dir / "experience_table_subsets"),
            optimizer_options=optimizer_options,
            ranking_target_resource=ranking_target_resource,
            restored_subset_results=(
                replay_ledger.restored_subset_results()
                if replay_ledger is not None else None
            ),
            on_subset_completed=(
                replay_ledger.mark_subset_completed
                if replay_ledger is not None else None
            ),
        )
        result = subset_summary.best_result
        formal_rankings = _formal_raw_all_subset_rankings(
            subset_summary.all_candidate_rankings,
            len(active),
        )

        # Clustering/fingerprinting is diagnostic only.  The formal candidate
        # universe is the globally score-ranked winner of every one of the
        # 2^k-1 non-empty subspaces, and no candidate may be removed before the
        # fixed Top-5 replay merely because L1 happens to match.
        plan_escape_audit = {
            "enabled": False,
            "reason": "disabled_for_raw_all_subset_top5_contract",
        }
        non_incumbent_rankings = formal_rankings
        plan_clustering = cluster_ranked_candidates(
            formal_rankings,
            scenario=str(config.get("scenario") or config.get("benchmark") or ""),
            fingerprint_config=(
                config.get("three_level_fingerprint")
                if isinstance(config.get("three_level_fingerprint"), Mapping)
                else None
            ),
        )
        plan_clustering["diagnostic_cluster_count"] = int(
            plan_clustering["strict_plan_cluster_count"]
        )
        plan_clustering["strict_fingerprint_cluster_count"] = sum(
            1
            for cluster in (plan_clustering.get("clusters") or [])
            if isinstance(cluster, Mapping)
            and cluster.get("strict_fingerprint_present") is True
        )
        plan_representatives = formal_rankings
        strict_cluster_by_subset = {
            subset_id: cluster["cluster_key"]
            for cluster in plan_clustering.get("clusters") or []
            for subset_id in cluster.get("member_subset_ids") or []
        }
        sealed_ranked_candidates = [
            {
                **copy.deepcopy(ranking),
                "strict_plan_cluster_key": strict_cluster_by_subset.get(
                    ranking.get("subset_id")
                ),
            }
            for ranking in non_incumbent_rankings
        ]
        replay_kept = copy.deepcopy(sealed_ranked_candidates)
        replay_pruned: List[Dict[str, Any]] = []
        replay_pruning_checks: List[Dict[str, Any]] = []
        outer_cfg = config.get("formal_outer_protocol") or {}
        acceptance_tier = str(
            outer_cfg.get("acceptance_tier", DEFAULT_ACCEPTANCE_TIER)
        ).strip().lower()
        initial_replay_top_k = int(
            outer_cfg.get("formal_search_initial_replay_top_k", 5)
        )
        benchmark_name = str(config.get("benchmark") or "").strip()
        default_replay_cap = formal_replay_cap(benchmark_name) if benchmark_name else 5
        maximum_replay_top_k = int(
            outer_cfg.get("formal_search_max_replay_top_k", default_replay_cap)
        )
        if initial_replay_top_k != 5 or maximum_replay_top_k != 5:
            raise ValueError(
                "formal real replay uses one global fixed Top-5 batch"
            )
        strict_plan_replay_quota = {
            "contract": "diagnostic_only_no_formal_reordering_v1",
            "required_plan_clusters": 0,
            "scenario_replay_cap": maximum_replay_top_k,
        }
        replay_queue = replay_kept[:maximum_replay_top_k]
        full_suppression_log = _build_replay_suppression_log(
            non_incumbent_rankings=non_incumbent_rankings,
            plan_representatives=plan_representatives,
            replay_pruned=replay_pruned, replay_kept=replay_kept,
            scenario_replay_cap=maximum_replay_top_k,
        )
        if replay_ledger is not None:
            replay_ledger.seal_replay(
                ranked_candidates=sealed_ranked_candidates,
                # Seal every predicted rank before replay. Top-5 is still the
                # immutable first batch; a lowest-tier extension may consume
                # only later ranks whose strict L1 differs from the incumbent.
                representative_ranks=[
                    int(item["rank"]) for item in sealed_ranked_candidates
                ],
                pruning_log=replay_pruning_checks,
                plan_cluster_log=plan_clustering.get("clusters") or [],
                representative_log=plan_representatives,
                suppression_log=full_suppression_log,
            )
        replay_orchestration = None
        if real_replay_callback is not None:
            if not isinstance(baseline_validation, Mapping):
                raise ValueError(
                    "baseline_validation is required when real_replay_callback is provided"
                )
            if ranking_target_resource is None:
                raise ValueError(
                    "target_resource must be cpu, memory, or io for real replay orchestration"
                )
            replay_orchestration = _orchestrate_staged_real_replays(
                replay_kept,
                callback=real_replay_callback,
                baseline_params=baseline,
                baseline_validation=baseline_validation,
                sensitivity_table=sensitivity,
                target_resource=ranking_target_resource,
                benchmark=str(config.get("benchmark") or ""),
                initial_top_k=initial_replay_top_k,
                max_top_k=maximum_replay_top_k,
                plan_clustering_log={
                    **copy.deepcopy(plan_clustering),
                    "replay_quota_after_optimistic_pruning": copy.deepcopy(
                        strict_plan_replay_quota
                    ),
                },
                pruning_decision_log=replay_pruning_checks,
                suppressed_candidate_log=full_suppression_log,
                replay_ledger=replay_ledger,
                acceptance_tier=acceptance_tier,
                plan_tree_extension_enabled=bool(
                    (outer_cfg.get("plan_tree_replay_extension") or {}).get(
                        "enabled", True
                    )
                ),
                plan_tree_extension_batch_size=int(
                    (outer_cfg.get("plan_tree_replay_extension") or {}).get(
                        "batch_size", 5
                    )
                ),
                plan_tree_extension_maximum_additional_replays=(
                    int(
                        (outer_cfg.get("plan_tree_replay_extension") or {})[
                            "maximum_additional_replays"
                        ]
                    )
                    if (
                        outer_cfg.get("plan_tree_replay_extension") or {}
                    ).get("maximum_additional_replays") is not None
                    else None
                ),
            )
            replay_queue = replay_orchestration["final_replay_queue"]
        setattr(result, "replay_candidate_queue", replay_queue)
        setattr(result, "real_replay_orchestration", replay_orchestration)
        setattr(result, "optimistically_pruned_candidates", replay_pruned)
        setattr(result, "optimistic_pruning_checks", replay_pruning_checks)
        setattr(result, "real_replay_initial_top_k", initial_replay_top_k)
        setattr(result, "real_replay_max_top_k", maximum_replay_top_k)
        setattr(result, "strict_plan_clustering", copy.deepcopy(plan_clustering))
        setattr(result, "strict_plan_replay_quota", copy.deepcopy(strict_plan_replay_quota))
        setattr(result, "plan_basin_escape", copy.deepcopy(plan_escape_audit))
        setattr(result, "candidate_space_exhausted", not bool(non_incumbent_rankings))
        if bool(config["estimator"].get("final_revalidation", False)):
            final_estimator = _build_estimator_for_incumbent(
                config, adapter, baseline
            )
            setattr(final_estimator, "current_label", "final_revalidation")
            final_estimator(config.get("sql_set"), dict(result.optimized_params))
            estimators.append(final_estimator)
        setattr(
            result,
            "measurements",
            [
                measurement
                for estimator in estimators
                for measurement in list(getattr(estimator, "measurements", []))
            ],
        )
        if commit_best:
            if real_replay_callback is None:
                adapter.apply(result.optimized_params)
            else:
                # Formal point drivers restore/tear down every fresh replay.  A
                # logical outer incumbent must never be re-applied through this
                # generic adapter because that would bypass the formal
                # drain/flush/fresh-readback protocol and its evidence.
                adapter.restore(original_snapshot)
        else:
            adapter.restore(original_snapshot)
    except BaseException:
        adapter.restore(original_snapshot)
        raise
    audit_artifacts = _write_inner_audit_artifacts(
        output_dir=output_dir,
        stamp=stamp,
        subset_summary=subset_summary,
        active_params=active,
        sensitivity_table=sensitivity,
        capability=capability,
        config=config,
    )
    total_subset_iterations = sum(
        int(row.get("iterations") or 0) for row in subset_summary.audit_records
    )
    replay_orchestration = getattr(result, "real_replay_orchestration", None)
    chosen_replay = (replay_orchestration or {}).get("chosen")
    outer_cfg = config.get("formal_outer_protocol") or {}
    chosen_validation = (chosen_replay or {}).get("candidate_final_validation") or {}
    chosen_params = (chosen_replay or {}).get("candidate_optimized_params") or baseline
    outer_decision = decide_validation_acceptance(
        baseline_validation or {},
        chosen_validation,
        target_resource=ranking_target_resource or "",
        hard_gate_passed=chosen_replay is not None,
        acceptance_tier=str(
            outer_cfg.get("acceptance_tier", DEFAULT_ACCEPTANCE_TIER)
        ).strip().lower(),
    )
    if real_replay_callback is not None:
        outer_progress = advance_outer_shrink_progress(
            ranges=ranges,
            accepted_shrink_count=int(outer_cfg.get("accepted_shrink_count", 0)),
            rejected_count=int(outer_cfg.get("rejected_count", 0)),
            target_accepted_shrinks=int(outer_cfg.get("target_accepted_shrinks", 10)),
            decision=outer_decision,
            candidate_params=chosen_params,
            active_params=active,
        )
    else:
        accepted_before = int(outer_cfg.get("accepted_shrink_count", 0))
        target_shrinks = int(outer_cfg.get("target_accepted_shrinks", 10))
        outer_progress = OuterShrinkProgress(
            ranges=copy.deepcopy(ranges),
            accepted_shrink_count=accepted_before,
            rejected_count=int(outer_cfg.get("rejected_count", 0)),
            target_accepted_shrinks=target_shrinks,
            complete=accepted_before >= target_shrinks,
            range_changes={},
        )
    range_after = {
        name: {
            "min_value": float(item.min_value),
            "max_value": float(item.max_value),
        }
        for name, item in outer_progress.ranges.items()
    }
    returned_params = (
        (chosen_replay or {}).get("candidate_optimized_params")
        or (baseline if real_replay_callback is not None else result.optimized_params)
    )
    flat_payload = {
                "optimized_params": result.optimized_params,
                "initial_params": result.initial_params,
                "initial_resource": result.initial_resource,
                "final_resource": result.final_resource,
                "initial_score": result.initial_score,
                "final_score": result.final_score,
                "score_improvement": result.score_improvement,
                "iterations": result.iterations,
                "initial_qps": result.initial_qps,
                "final_qps": result.final_qps,
                "qps_improvement_pct": result.qps_improvement_pct,
                "degraded": result.degraded,
                "degradation_pct": result.degradation_pct,
                "initial_query_time": result.initial_query_time,
                "final_query_time": result.final_query_time,
                "measurements": getattr(result, "measurements", []),
                "subset_count": subset_summary.subset_count,
                "successful_subset_count": subset_summary.successful_subset_count,
                "failed_subset_count": subset_summary.failed_subset_count,
                "unique_candidate_count": subset_summary.unique_candidate_count,
                "independent_candidate_count":
                    subset_summary.unique_candidate_count,
                "candidate_identity_contract":
                    "no_duplicate_claim_before_parameter_l1_l2_l3_replay_evidence",
                "best_subset": list(getattr(result, "best_subset", ())),
                "best_subset_id": getattr(result, "best_subset_id", None),
                "candidate_rankings": formal_rankings,
                "all_candidate_rankings": formal_rankings,
                "subset_audit": subset_summary.audit_records,
                "subset_log_dir": subset_summary.log_dir,
                "estimate_cache": estimate_cache.stats(),
                "incumbent_candidate_count_removed": 0,
                "strict_plan_clustering": copy.deepcopy(plan_clustering),
                "strict_plan_replay_quota": copy.deepcopy(strict_plan_replay_quota),
                "strict_plan_cluster_count": plan_clustering[
                    "strict_fingerprint_cluster_count"
                ],
                "diagnostic_plan_cluster_count": plan_clustering[
                    "diagnostic_cluster_count"
                ],
                "plan_cluster_representative_count_diagnostic_only": len(
                    plan_clustering.get("representatives") or []
                ),
                "optimistic_pruning_framework": "resource_utility_joint_box_v2",
                "optimistically_pruned_count": len(replay_pruned),
                "optimistically_pruned_candidates": replay_pruned,
                "optimistic_pruning_checks": replay_pruning_checks,
                "real_replay_initial_top_k": initial_replay_top_k,
                "real_replay_max_top_k": maximum_replay_top_k,
                "replay_candidate_queue": replay_queue,
                "real_replay_orchestration": replay_orchestration,
                "no_eligible_diagnostic": (
                    (replay_orchestration or {}).get("diagnostic")
                ),
                "audit_artifacts": audit_artifacts,
                "outer_acceptance_decision": outer_decision.to_dict(),
                "accepted_shrink_count": outer_progress.accepted_shrink_count,
                "rejected_count": outer_progress.rejected_count,
                "target_accepted_shrinks": outer_progress.target_accepted_shrinks,
                "outer_target_complete": outer_progress.complete,
                "range_changes": outer_progress.range_changes,
                "range_after": range_after,
                "committed": commit_best,
                "snapshot_path": str(snapshot_path.resolve()),
                "original_snapshot_path": (
                    str(original_snapshot_path.resolve()) if baseline_values else str(snapshot_path.resolve())
                ),
                "baseline_values": baseline_values,
    }
    formal_payload = {
        "meta": {
            "exp_id": config.get("exp_id"),
            "benchmark": config.get("benchmark"),
            "db": "mysql8.0",
            "method": str(method_id),
            "method_alias": (
                "gradient_descent" if method_id == "experience_table_sgd" else str(method_id)
            ),
            "shared_candidate_generator": "all_nonempty_subsets_experience_table_analytic_sgd_v1",
            "freeze_id": config.get("freeze_id"),
            "bottleneck": config.get("bottleneck", config.get("target_resource")),
            "bottleneck_target": config.get("bottleneck_target"),
            "repeat_id": config.get("repeat_id"),
            "experience_table": {
                "version": config.get("experience_table_version"),
                "sha256": config.get("experience_table_sha256"),
            },
            "machine_spec": copy.deepcopy(config.get("machine_spec") or {}),
            "lane_lease_path": str(
                (config.get("formal_outer_protocol") or {}).get("lane_lease_path")
                or _default_lane_lease_path(config)
            ),
        },
        "outer_loop": [
            {
                "iter": optimizer_cfg.get("round_index", 1),
                "inner_loop": {
                    "active_param_count": len(active),
                    "active_params": active,
                    "candidate_policy": "all_nonempty_parameter_subsets",
                    "expected_parameter_subsets": (1 << len(active)) - 1,
                    "parameter_subsets_enumerated": subset_summary.subset_count,
                    "successful_parameter_subsets": subset_summary.successful_subset_count,
                    "failed_parameter_subsets": subset_summary.failed_subset_count,
                    "inner_algorithm": subset_summary.inner_algorithm,
                    "gradient_source": "frozen_experience_table_times_analytic_utility_derivative",
                    "evaluated_total_sgd_iterations": subset_summary.total_inner_iterations,
                    "joint_candidates_evaluated": total_subset_iterations,
                    "resource_estimation_requests": estimate_cache.stats()["request_count"],
                    "formal_all_subset_candidate_count": len(formal_rankings),
                    "diagnostic_independent_candidate_count":
                        subset_summary.unique_candidate_count,
                    "formal_candidates_removed_before_top5": 0,
                    "strict_plan_clusters": plan_clustering[
                        "strict_fingerprint_cluster_count"
                    ],
                    "diagnostic_plan_clusters": plan_clustering[
                        "diagnostic_cluster_count"
                    ],
                    "strict_plan_cluster_representatives_diagnostic_only": len(
                        plan_clustering.get("representatives") or []
                    ),
                    "plan_cluster_contract": plan_clustering["contract"],
                    "strict_plan_replay_quota": copy.deepcopy(strict_plan_replay_quota),
                    "missing_plan_fingerprints_are_independent_clusters": True,
                    "approximate_plan_fingerprints_used_for_clustering": False,
                    "optimistically_pruned_candidates": len(replay_pruned),
                    "initial_real_replay_top_k": initial_replay_top_k,
                    "maximum_real_replay_top_k": maximum_replay_top_k,
                    "staged_real_replays": len((replay_orchestration or {}).get("evaluations") or []),
                    "acceptance_reference": (
                        "tiered_outer_qps_resource_improvement_v1"
                    ),
                    "qps_role": "outer_acceptance_only",
                    **audit_artifacts,
                },
                "params_before": baseline,
                "params": returned_params,
                "outer_accepted": chosen_replay is not None,
                "required_acceptance_tier": str(
                    outer_cfg.get("acceptance_tier", DEFAULT_ACCEPTANCE_TIER)
                ).strip().lower(),
                "outer_acceptance_decision": outer_decision.to_dict(),
                "outer_acceptance_reason": (
                    ((chosen_replay or {}).get("candidate_final_validation") or {}).get("outer_acceptance_reason")
                    if chosen_replay else "no_outer_eligible_candidate"
                ),
                "accepted_shrink_count": outer_progress.accepted_shrink_count,
                "rejected_count": outer_progress.rejected_count,
                "target_accepted_shrinks": outer_progress.target_accepted_shrinks,
                "target_complete": outer_progress.complete,
                "range_changes": outer_progress.range_changes,
                "range_after": range_after,
                "real_replay": {
                    "callback_contract": "callable_measure_plus_mandatory_restore_v2",
                    "evaluations": copy.deepcopy(
                        (replay_orchestration or {}).get("evaluations") or []
                    ),
                    "chosen": copy.deepcopy(chosen_replay),
                    "online_t_update": copy.deepcopy(
                        (replay_orchestration or {}).get("online_t_update")
                    ),
                    "no_eligible_diagnostic": copy.deepcopy(
                        (replay_orchestration or {}).get("diagnostic")
                    ),
                    "required_evidence_references": list(
                        REQUIRED_REAL_REPLAY_EVIDENCE
                    ),
                },
            }
        ],
        "inner_loop_summary": {
            "total_outer_calls": 1,
            "total_subset_runs": subset_summary.subset_count,
            "total_subset_iterations": total_subset_iterations,
            "total_joint_candidates_evaluated": total_subset_iterations,
            "inner_algorithm": subset_summary.inner_algorithm,
            "gradient_source": "frozen_experience_table_times_analytic_utility_derivative",
            "total_parameter_subsets_enumerated": subset_summary.subset_count,
            "total_resource_estimation_requests": estimate_cache.stats()["request_count"],
            "total_formal_candidates": len(formal_rankings),
            "diagnostic_independent_candidate_count":
                subset_summary.unique_candidate_count,
            "plan_basin_escape": copy.deepcopy(plan_escape_audit),
            "seen_candidate_count_filtered": 0,
            "candidate_space_exhausted": not bool(non_incumbent_rankings),
            "total_optimistically_pruned_candidates": len(replay_pruned),
            "real_candidates_validated": len((replay_orchestration or {}).get("evaluations") or []),
            "outer_eligible_candidates": int((replay_orchestration or {}).get("eligible_count") or 0),
            "best_estimated_utility": result.final_score,
            "best_estimated_utility_role": "diagnostic_only",
            "best_params": result.optimized_params,
            "returned_params": returned_params,
        },
        "result": {
            "final_params": returned_params,
            "qps_before": (baseline_validation or {}).get("db_qps", (baseline_validation or {}).get("qps")),
            "db_qps_before": (baseline_validation or {}).get("db_qps"),
            "qps_after": ((chosen_replay or {}).get("candidate_final_validation") or {}).get("db_qps"),
            "db_qps_after": ((chosen_replay or {}).get("candidate_final_validation") or {}).get("db_qps"),
            "converge_outer_iters": 1,
            "no_eligible_diagnostic": (replay_orchestration or {}).get("diagnostic"),
        },
    }
    payload = sanitize_optimistic_pruning_metadata_tree(
        {**flat_payload, **formal_payload}
    )
    result_path = output_dir / f"result_{stamp}.json"
    result_path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    setattr(result, "result_path", str(result_path.resolve()))
    setattr(result, "snapshot_path", str(snapshot_path.resolve()))
    setattr(result, "formal_document", formal_payload)
    setattr(result, "audit_artifacts", audit_artifacts)
    setattr(result, "outer_shrink_progress", outer_progress)
    return result


def _run_formal_outer_campaign_unleased(
    config: Mapping[str, Any],
    *,
    client: Optional[SQLClient],
    real_replay_callback: RealReplayCallback,
    baseline_validation: Mapping[str, Any],
    method_id: str,
) -> Any:
    """Run fresh outer attempts until ten accepted range shrinks or a hard cap."""
    campaign_config = copy.deepcopy(dict(config))
    outer_cfg = dict(campaign_config.get("formal_outer_protocol") or {})
    target = int(outer_cfg.get("target_accepted_shrinks", 10))
    max_attempts = int(outer_cfg.get("max_outer_attempts", 100))
    accepted_count = int(outer_cfg.get("accepted_shrink_count", 0))
    rejected_count = int(outer_cfg.get("rejected_count", 0))
    acceptance_tier = str(
        outer_cfg.get("acceptance_tier", DEFAULT_ACCEPTANCE_TIER)
    ).strip().lower()
    tier_downgrade_after = int(
        outer_cfg.get(
            "acceptance_tier_downgrade_after_rounds",
            DEFAULT_ACCEPTANCE_TIER_DOWNGRADE_AFTER_ROUNDS,
        )
    )
    tier_failed_rounds = int(
        outer_cfg.get("acceptance_tier_consecutive_failed_rounds", 0)
    )
    if acceptance_tier not in ACCEPTANCE_TIER_RANK:
        raise ValueError(f"invalid formal acceptance tier: {acceptance_tier!r}")
    if tier_downgrade_after != 2:
        raise ValueError("formal acceptance tier downgrade threshold must equal 2")
    valid_tier_failure_state = (
        0 <= tier_failed_rounds <= tier_downgrade_after
        if acceptance_tier == "fallback_qps"
        else 0 <= tier_failed_rounds < tier_downgrade_after
    )
    if not valid_tier_failure_state:
        raise ValueError("invalid acceptance tier consecutive-failure state")
    if target != 10:
        raise ValueError("formal campaign target_accepted_shrinks must equal 10")
    if max_attempts <= 0:
        raise ValueError("max_outer_attempts must be positive")
    if accepted_count >= target:
        raise ValueError("formal campaign is already complete; no outer attempt is allowed")
    if not isinstance(baseline_validation, Mapping):
        raise ValueError("formal campaign requires the current incumbent validation")

    current_validation = copy.deepcopy(dict(baseline_validation))
    current_incumbent: Optional[Dict[str, float]] = None
    configured_baseline = (
        (campaign_config.get("cost_parameters") or {}).get("baseline_values") or {}
    )
    if configured_baseline:
        current_incumbent = {
            str(name): float(value) for name, value in configured_baseline.items()
        }
    campaign_rounds: List[Dict[str, Any]] = []
    aggregate_outer_loop: List[Dict[str, Any]] = []
    total_recovery_count = 0
    last_result: Any = None
    seen_candidate_tokens = {
        str(value)
        for value in (outer_cfg.get("seen_candidate_signatures") or [])
        if str(value)
    }
    current_sensitivity = copy.deepcopy(
        dict(campaign_config.get("sensitivity_table") or {})
    )
    candidate_space_exhausted = False
    escape_cfg = dict(outer_cfg.get("plan_basin_escape") or {})
    initial_escape_radius = float(escape_cfg.get("orthogonal_radius", 0.08))
    escape_radius_gamma = float(escape_cfg.get("radius_gamma", 0.5))
    maximum_escape_radius = float(
        escape_cfg.get(
            "maximum_radius",
            min(0.8, initial_escape_radius / (escape_radius_gamma ** 2)),
        )
    )
    maximum_rejected_batches = int(
        escape_cfg.get("maximum_rejected_batches", 5)
    )
    if not (
        0.0 < initial_escape_radius <= maximum_escape_radius < 1.0
        and 0.0 < escape_radius_gamma < 1.0
        and 1 <= maximum_rejected_batches <= 5
    ):
        raise ValueError(
            "plan_basin_escape requires 0 < orthogonal_radius <= "
            "maximum_radius < 1, 0 < radius_gamma < 1, and "
            "1 <= maximum_rejected_batches <= 5"
        )
    current_escape_radius = initial_escape_radius
    base_eta = float((campaign_config.get("optimizer") or {}).get("eta", 0.0051))
    minimum_eta = float(escape_cfg.get("minimum_eta", base_eta / 16.0))
    if not 0.0 < minimum_eta <= base_eta:
        raise ValueError("plan_basin_escape requires 0 < minimum_eta <= optimizer.eta")
    current_eta = base_eta
    consecutive_rejected_batches = 0
    local_converged = False

    for attempt in range(1, max_attempts + 1):
        acceptance_tier_before = acceptance_tier
        tier_failed_rounds_before = tier_failed_rounds
        round_config = copy.deepcopy(campaign_config)
        round_outer = dict(round_config.get("formal_outer_protocol") or {})
        round_outer.update(
            {
                "accepted_shrink_count": accepted_count,
                "rejected_count": rejected_count,
                "target_accepted_shrinks": target,
                "max_outer_attempts": max_attempts,
                "seen_candidate_signatures": sorted(seen_candidate_tokens),
                "acceptance_tier": acceptance_tier,
                "acceptance_tier_downgrade_after_rounds": tier_downgrade_after,
                "acceptance_tier_consecutive_failed_rounds": tier_failed_rounds,
            }
        )
        round_escape = dict(round_outer.get("plan_basin_escape") or {})
        round_escape["orthogonal_radius"] = current_escape_radius
        round_outer["plan_basin_escape"] = round_escape
        round_config["formal_outer_protocol"] = round_outer
        round_config["sensitivity_table"] = copy.deepcopy(current_sensitivity)
        round_optimizer = dict(round_config.get("optimizer") or {})
        round_optimizer["round_index"] = attempt
        round_optimizer["eta"] = current_eta
        round_config["optimizer"] = round_optimizer
        if current_incumbent is not None:
            round_costs = dict(round_config.get("cost_parameters") or {})
            round_costs["baseline_values"] = copy.deepcopy(current_incumbent)
            round_config["cost_parameters"] = round_costs

        round_result = _run_optimization_unleased(
            round_config,
            client=client,
            real_replay_callback=real_replay_callback,
            baseline_validation=current_validation,
            method_id=method_id,
        )
        last_result = round_result
        if current_incumbent is None:
            current_incumbent = {
                str(name): float(value)
                for name, value in round_result.initial_params.items()
            }
        progress = round_result.outer_shrink_progress
        chosen = (getattr(round_result, "real_replay_orchestration", None) or {}).get(
            "chosen"
        )
        accepted_this_round = progress.accepted_shrink_count == accepted_count + 1
        atomic_accounting_committed = False
        if accepted_this_round:
            if not isinstance(chosen, Mapping):
                raise RuntimeError("accepted outer round is missing its chosen replay")
            chosen_params = chosen.get("candidate_optimized_params")
            chosen_validation = chosen.get("candidate_final_validation")
            if not isinstance(chosen_params, Mapping) or not isinstance(
                chosen_validation, Mapping
            ):
                raise RuntimeError("accepted outer round lacks incumbent handoff evidence")
            current_incumbent = {
                str(name): float(value) for name, value in chosen_params.items()
            }
            current_validation = copy.deepcopy(dict(chosen_validation))
            if campaign_config.get("accepted_round_authorized") is True:
                accounting_token = str(
                    chosen_validation.get("accounting_token") or ""
                )
                journal_root = round_outer.get(
                    "formal_point_journal_root"
                )
                if not accounting_token or not journal_root:
                    raise RuntimeError(
                        "accepted outer round lacks point-journal accounting authority"
                    )
                from formal_mysql_sgd_orchestration import FormalPointJournal
                if not FormalPointJournal.claim_accounting(
                    Path(str(journal_root)), accounting_token,
                ):
                    raise RuntimeError(
                        "accepted outer round accounting token was already committed"
                    )
                atomic_accounting_committed = True

        accepted_count = int(progress.accepted_shrink_count)
        rejected_count = int(progress.rejected_count)
        round_costs = dict(campaign_config.get("cost_parameters") or {})
        round_costs["baseline_values"] = copy.deepcopy(current_incumbent)
        round_costs["ranges"] = {
            name: {
                "min_value": float(item.min_value),
                "max_value": float(item.max_value),
            }
            for name, item in progress.ranges.items()
        }
        campaign_config["cost_parameters"] = round_costs

        evaluations = list(
            ((getattr(round_result, "real_replay_orchestration", None) or {}).get(
                "evaluations"
            ) or [])
        )
        orchestration = getattr(round_result, "real_replay_orchestration", None) or {}
        updated_sensitivity = orchestration.get("updated_sensitivity_table")
        if isinstance(updated_sensitivity, Mapping):
            current_sensitivity = copy.deepcopy(dict(updated_sensitivity))
            campaign_config["sensitivity_table"] = copy.deepcopy(current_sensitivity)
        if accepted_this_round:
            seen_candidate_tokens.clear()
            current_escape_radius = initial_escape_radius
            current_eta = base_eta
            consecutive_rejected_batches = 0
            local_converged = False
        else:
            for evaluation in evaluations:
                params = evaluation.get("candidate_optimized_params") or {}
                if isinstance(params, Mapping) and params:
                    seen_candidate_tokens.add(_parameter_signature_token(params))
            feedback = orchestration.get("online_t_update") or {}
            escape_state = _advance_rejected_escape_state(
                current_radius=current_escape_radius,
                maximum_radius=maximum_escape_radius,
                radius_gamma=escape_radius_gamma,
                current_eta=current_eta,
                minimum_eta=minimum_eta,
                consecutive_rejected_batches=consecutive_rejected_batches,
                maximum_rejected_batches=maximum_rejected_batches,
                resource_feedback=(
                    feedback if isinstance(feedback, Mapping) else {}
                ),
            )
            current_escape_radius = float(escape_state["escape_radius"])
            current_eta = float(escape_state["eta"])
            consecutive_rejected_batches = int(
                escape_state["consecutive_rejected_batches"]
            )
            local_converged = bool(escape_state["exhausted"])
        tier_progress = advance_acceptance_tier(
            acceptance_tier=acceptance_tier,
            consecutive_failed_rounds=tier_failed_rounds,
            accepted=accepted_this_round,
            downgrade_after_rounds=tier_downgrade_after,
        )
        acceptance_tier = tier_progress.acceptance_tier
        tier_failed_rounds = tier_progress.consecutive_failed_rounds
        if not accepted_this_round and acceptance_tier_before != "fallback_qps":
            # Search/eta exhaustion cannot terminate a stricter tier before its
            # two-round downgrade threshold has been honored.
            local_converged = False
        if tier_progress.downgraded:
            # The next round may reconsider the same sealed parameter points
            # under the newly authorized tier.  Reset only search-escape state;
            # correctness/stability/recovery gates and accepted accounting stay
            # unchanged.
            seen_candidate_tokens.clear()
            current_escape_radius = initial_escape_radius
            current_eta = base_eta
            consecutive_rejected_batches = 0
            local_converged = False
        persisted_outer = campaign_config.setdefault("formal_outer_protocol", {})
        persisted_outer["seen_candidate_signatures"] = sorted(seen_candidate_tokens)
        persisted_outer["acceptance_tier"] = acceptance_tier
        persisted_outer[
            "acceptance_tier_downgrade_after_rounds"
        ] = tier_downgrade_after
        persisted_outer[
            "acceptance_tier_consecutive_failed_rounds"
        ] = tier_failed_rounds
        recovery_count = sum(
            1
            for row in evaluations
            if isinstance(row.get("recovery"), Mapping)
            and row["recovery"].get("verified") is True
        )
        total_recovery_count += recovery_count
        round_record = {
            "outer_attempt": attempt,
            "accepted": accepted_this_round,
            "accepted_round_authorized":
                campaign_config.get("accepted_round_authorized") is True,
            "atomic_accounting_committed": atomic_accounting_committed,
            "accepted_accounting_token": (
                str(
                    (
                        (chosen or {}).get("candidate_final_validation") or {}
                    ).get("accounting_token") or ""
                )
                if accepted_this_round else None
            ),
            "accepted_shrink_count": accepted_count,
            "rejected_count": rejected_count,
            "incumbent_after": copy.deepcopy(current_incumbent),
            "incumbent_validation_after": copy.deepcopy(current_validation),
            "range_changes": copy.deepcopy(progress.range_changes),
            "range_after": copy.deepcopy(round_result.formal_document["outer_loop"][0].get("range_after") or {}),
            "fresh_real_replay_count": len(evaluations),
            "verified_recovery_count": recovery_count,
            "round_result_path": str(round_result.result_path),
            "escape_radius_after": current_escape_radius,
            "eta_after": current_eta,
            "consecutive_rejected_batches": consecutive_rejected_batches,
            "acceptance_tier_before": acceptance_tier_before,
            "acceptance_tier_after": acceptance_tier,
            "acceptance_tier_consecutive_failed_rounds_before": (
                tier_failed_rounds_before
            ),
            "acceptance_tier_consecutive_failed_rounds_after": tier_failed_rounds,
            "acceptance_tier_downgrade_after_rounds": tier_downgrade_after,
            "acceptance_tier_downgrade": tier_progress.to_dict(),
            "resource_only_feedback": copy.deepcopy(
                orchestration.get("online_t_update")
            ),
            "local_converged": local_converged,
        }
        campaign_rounds.append(round_record)
        outer_row = copy.deepcopy(round_result.formal_document["outer_loop"][0])
        outer_row["iter"] = attempt
        outer_row["campaign_round"] = copy.deepcopy(round_record)
        aggregate_outer_loop.append(outer_row)
        if accepted_count >= target:
            break
        candidate_space_exhausted = bool(
            getattr(round_result, "candidate_space_exhausted", False)
        )
        if local_converged:
            break

    if last_result is None or current_incumbent is None:
        raise RuntimeError("formal outer campaign produced no round")
    complete = accepted_count == target
    diagnostic = None if complete else {
        "code": (
            "resource_feedback_escape_batches_exhausted_without_acceptance"
            if local_converged
            else "candidate_space_exhausted_before_target_accepted_shrinks"
            if candidate_space_exhausted
            else "max_outer_attempts_before_target_accepted_shrinks"
        ),
        "max_outer_attempts": max_attempts,
        "accepted_shrink_count": accepted_count,
        "rejected_count": rejected_count,
        "remaining_accepted_shrinks": target - accepted_count,
        "seen_candidate_count": len(seen_candidate_tokens),
        "escape_radius": current_escape_radius,
        "maximum_escape_radius": maximum_escape_radius,
        "maximum_rejected_batches": maximum_rejected_batches,
        "consecutive_rejected_batches": consecutive_rejected_batches,
        "acceptance_tier": acceptance_tier,
        "acceptance_tier_consecutive_failed_rounds": tier_failed_rounds,
        "acceptance_tier_downgrade_after_rounds": tier_downgrade_after,
        "eta": current_eta,
    }
    campaign_status = "complete" if complete else "incomplete"
    campaign_document = copy.deepcopy(last_result.formal_document)
    campaign_document["outer_loop"] = aggregate_outer_loop
    campaign_document["inner_loop_summary"]["total_outer_calls"] = len(campaign_rounds)
    campaign_document["result"].update(
        {
            "final_params": copy.deepcopy(current_incumbent),
            "converge_outer_iters": len(campaign_rounds),
            "campaign_status": campaign_status,
            "accepted_shrink_count": accepted_count,
            "rejected_count": rejected_count,
            "target_accepted_shrinks": target,
            "target_complete": complete,
            "safety_cap_diagnostic": diagnostic,
            "verified_recovery_count": total_recovery_count,
            "acceptance_tier": acceptance_tier,
            "acceptance_tier_consecutive_failed_rounds": tier_failed_rounds,
            "acceptance_tier_downgrade_after_rounds": tier_downgrade_after,
        }
    )
    output_cfg = campaign_config.get("output") or {}
    output_dir = Path(str(output_cfg.get("directory", "history/mysql")))
    output_dir.mkdir(parents=True, exist_ok=True)
    campaign_path = output_dir / (
        f"campaign_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json"
    )
    campaign_path.write_text(
        json.dumps(
            sanitize_optimistic_pruning_metadata_tree(campaign_document),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    last_result.optimized_params = copy.deepcopy(current_incumbent)
    last_result.formal_document = campaign_document
    last_result.result_path = str(campaign_path.resolve())
    setattr(last_result, "campaign_status", campaign_status)
    setattr(last_result, "campaign_complete", complete)
    setattr(last_result, "campaign_diagnostic", diagnostic)
    setattr(last_result, "campaign_rounds", campaign_rounds)
    setattr(last_result, "accepted_shrink_count", accepted_count)
    setattr(last_result, "rejected_count", rejected_count)
    setattr(last_result, "verified_recovery_count", total_recovery_count)
    return last_result


def run_optimization(
    config: Mapping[str, Any],
    *,
    client: Optional[SQLClient] = None,
    real_replay_callback: Optional[RealReplayCallback] = None,
    baseline_validation: Optional[Mapping[str, Any]] = None,
    method_id: str = "experience_table_sgd",
) -> Any:
    """Run the formal campaign, or one estimator-only round without a replay callback."""
    if method_id != "experience_table_sgd":
        raise ValueError("formal small loop method_id must be experience_table_sgd")
    outer_cfg = config.get("formal_outer_protocol") or {}
    lease_path = Path(
        str(outer_cfg.get("lane_lease_path") or _default_lane_lease_path(config))
    )
    timeout_s = float(outer_cfg.get("lane_lease_timeout_s", 0.0))
    with _LaneLease(lease_path, timeout_s=timeout_s):
        if real_replay_callback is not None:
            if not isinstance(baseline_validation, Mapping):
                raise ValueError("formal campaign requires baseline_validation")
            return _run_formal_outer_campaign_unleased(
                config,
                client=client,
                real_replay_callback=real_replay_callback,
                baseline_validation=baseline_validation,
                method_id=method_id,
            )
        return _run_optimization_unleased(
            config,
            client=client,
            real_replay_callback=real_replay_callback,
            baseline_validation=baseline_validation,
            method_id=method_id,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(os.environ.get("MYSQL_OPTIMIZER_CONFIG", str(DEFAULT_CONFIG))),
    )
    parser.add_argument("--validate-config", action="store_true")
    parser.add_argument("--list-costs", action="store_true")
    parser.add_argument("--restore-snapshot", type=Path)
    parser.add_argument(
        "--method",
        choices=("experience_table_sgd",),
        default="experience_table_sgd",
        help="Formal small loop uses the experience-table analytic-SGD pipeline.",
    )
    args = parser.parse_args()
    config = load_config(args.config)
    validate_config(config)
    if args.validate_config:
        print(f"config valid: {args.config}", flush=True)
        return 0
    mysql_cfg = config.get("mysql", {})
    adapter = MySQLCostAdapter(
        MySQLCLIClient(mysql_cfg),
        engine_name=str(mysql_cfg.get("engine_name", "default")),
        device_type=int(mysql_cfg.get("device_type", 0)),
    )
    if args.restore_snapshot:
        adapter.restore(adapter.load_snapshot(args.restore_snapshot))
        print(f"restored MySQL costs from {args.restore_snapshot}", flush=True)
        return 0
    if args.list_costs:
        print(json.dumps(adapter.effective_values(), indent=2, sort_keys=True), flush=True)
        return 0
    parser.error(
        "formal execution is available only through formal_mysql_sgd_campaign.py "
        "with an explicit --service-factory; this CLI is diagnostics-only"
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

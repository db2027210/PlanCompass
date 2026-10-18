#!/usr/bin/env python3
"""Run one current-workload PostgreSQL Algorithm C lane.

This is the Docker replay bridge for the experience-table all-subset SGD
implementation.  Candidate generation is read-only EXPLAIN/ratio-scale; QPS is
used only by the outer three-tier acceptance gate.  Up to five distinct-plan
candidates are replayed in two-worker waves; a smaller retained interval is
never padded with duplicate or baseline candidates.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import math
import os
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Set, Tuple


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binary_plan_resource_tuner import (  # noqa: E402
    PlanFeatureProbe,
    acceptance_policy_for_target,
    acquire_configured_shared_ssd_workload_coordination,
    aggregate_parallel_baseline_validation,
    apply_pg_params,
    is_none_target,
    outer_semantic_acceptance_details,
    pg_conn_from_worker,
    restore_worker_from_hybench_template,
    release_shared_ssd_coordination,
    run_real_validation_safe,
    safe_int,
    validation_workers,
    workload_queries_with_sampling,
    worker_with_prepared_restore,
)
from common_outer_loop import (  # noqa: E402
    binary_shrink_parameter_ranges,
    resolve_parameter_transform,
)
from experience_table_subset_optimizer import (  # noqa: E402
    IncompleteSubsetOptimizationError,
    attach_three_level_fingerprint,
    optimize_all_experience_table_subsets,
)
from gradient_descent_optimizer import ParameterRange  # noqa: E402
from resource_utility import ResourceUtilityCalculator  # noqa: E402
from algorithm_c_plan_portfolio import (  # noqa: E402
    CONTRACT as PLAN_PORTFOLIO_CONTRACT,
    configured_policy as plan_portfolio_policy,
    diverse_order as plan_portfolio_order,
    identity as plan_portfolio_identity,
)
from shell.algorithm_c_parameter_support import (  # noqa: E402
    PARAMETER_SUPPORT_CONTRACT,
    parameter_support_assessment,
)
from shell.algorithm_c_training_boundary import (  # noqa: E402
    parameter_interaction_names,
    validate_training_runtime_boundary,
)


ACTIVE_PARAMS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
DEFAULT_RANGES = {
    "seq_page_cost": (0.1, 10.0),
    "random_page_cost": (0.1, 20.0),
    "cpu_tuple_cost": (0.001, 0.1),
    "cpu_index_tuple_cost": (0.0005, 0.05),
    "cpu_operator_cost": (0.00025, 0.025),
    "parallel_setup_cost": (100.0, 10000.0),
    "parallel_tuple_cost": (0.001, 1.0),
}
STATE_VERSION = 3
DEFAULT_ACCEPTED_TARGET = 10
DEFAULT_NORMALIZED_WIDTH_THRESHOLD = 0.5 ** DEFAULT_ACCEPTED_TARGET
BASE_TOP_K = 5
REPLAY_PARALLELISM = 2
LOCAL_PLAN_CANDIDATE_REAL_REPLAY_LIMIT = 10
GLOBAL_PLAN_COVERAGE_SAMPLE_COUNT = 256
GLOBAL_PLAN_COVERAGE_REAL_REPLAY_LIMIT = 10
DEFAULT_MIN_TMP_AVAILABLE_BYTES = 4 * 1024 * 1024 * 1024
GLOBAL_PLAN_COVERAGE_CONTRACT = (
    "deterministic_current_interval_scored_top10_coverage_rescue_v3"
)
OUTER_BOUNDARY_REFINEMENT_CONTRACT = (
    "algorithm_c_outer_feasibility_boundary_refinement_v1"
)
UNFINISHED_OUTER_ROUND_PHASES = frozenset(
    {
        "offline_algorithm_c_ranking",
        "base_top5_real_replay",
        "local_plan_tail_real_replay",
        "current_interval_plan_coverage_expansion_real_replay",
        "current_interval_plan_basin_escape_real_replay",
        "global_plan_coverage_rescue_real_replay",
    }
)
DEFAULT_GRADIENT_PROBE_STEP_PCT = (10.0, 20.0, 35.0)
# The ordinary local probes can remain inside one PostgreSQL plan basin even
# when the frozen experience table has a trustworthy non-zero direction.  A
# formal candidate-space expansion therefore keeps the exact same g_j and
# only increases eta until the full parameter boundary is reachable.  These
# are still gradient candidates: no random sign, zero-confidence parameter,
# baseline control, or uncertain probe is allowed to pad the Top-5.
BEHAVIOR_CLUSTER_GRADIENT_PROBE_STEP_PCT = (
    10.0,
    20.0,
    35.0,
    52.0,
    72.0,
    95.0,
    130.0,
    180.0,
    260.0,
    400.0,
    650.0,
    900.0,
)
TOP5_AUDIT_FIELDS = {
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
INNER_SCORING_CONTRACT = "algorithm_c_directional_band_target_io_cost_v2"


def _parameter_value_at_fraction(
    name: str,
    low: float,
    high: float,
    fraction: float,
) -> float:
    """Map a deterministic unit-interval point into a parameter domain."""

    bounded = min(1.0, max(0.0, float(fraction)))
    transform = resolve_parameter_transform(name)
    if transform == "log":
        if low <= 0.0 or high <= 0.0:
            raise ValueError(f"log-domain parameter must be positive: {name}")
        return math.exp(math.log(low) + bounded * (math.log(high) - math.log(low)))
    return float(low) + bounded * (float(high) - float(low))


def deterministic_global_coverage_vectors(
    incumbent_params: Mapping[str, float],
    parameter_ranges: Mapping[str, Any],
    *,
    sample_count: int = GLOBAL_PLAN_COVERAGE_SAMPLE_COUNT,
) -> List[Dict[str, float]]:
    """Build a QPS-blind coverage set inside the current formal interval.

    The design combines sparse one/two-parameter probes with a deterministic
    Halton low-discrepancy sequence.  It is derived only from the configured
    current ranges and the current incumbent; no other algorithm's
    candidates, parameters, measurements, or conclusions are inputs.
    """

    if sample_count < BASE_TOP_K:
        raise ValueError("global plan coverage requires at least five points")
    incumbent = {name: float(incumbent_params[name]) for name in ACTIVE_PARAMS}
    vectors: List[Dict[str, float]] = []
    signatures: Set[Tuple[str, ...]] = set()

    def bounds(name: str) -> Tuple[float, float]:
        value = parameter_ranges[name]
        if hasattr(value, "min_value") and hasattr(value, "max_value"):
            low = float(value.min_value)
            high = float(value.max_value)
        else:
            low = float(value[0])
            high = float(value[1])
        if not math.isfinite(low) or not math.isfinite(high) or high < low:
            raise ValueError(f"invalid current parameter range: {name}={value!r}")
        return low, high

    def add(fractions: Mapping[str, float]) -> None:
        params = copy.deepcopy(incumbent)
        for name, fraction in fractions.items():
            low, high = bounds(name)
            params[name] = _parameter_value_at_fraction(
                name,
                float(low),
                float(high),
                float(fraction),
            )
        signature = exact_param_signature(params)
        if signature == exact_param_signature(incumbent) or signature in signatures:
            return
        signatures.add(signature)
        vectors.append(params)

    # Sparse probes avoid the all-parameter over-perturbation of a pure
    # factorial design and expose plan boundaries hidden from local g_j.
    for name in ACTIVE_PARAMS:
        for fraction in (0.20, 0.35, 0.65, 0.80):
            add({name: fraction})
    for left_index, left in enumerate(ACTIVE_PARAMS):
        for right in ACTIVE_PARAMS[left_index + 1 :]:
            for left_fraction, right_fraction in itertools.product(
                (0.25, 0.75),
                repeat=2,
            ):
                add({left: left_fraction, right: right_fraction})

    def radical_inverse(index: int, base: int) -> float:
        inverse = 1.0 / float(base)
        factor = inverse
        result = 0.0
        value = int(index)
        while value > 0:
            result += float(value % base) * factor
            value //= base
            factor *= inverse
        return result

    # Deterministic low-discrepancy fill points reduce axis correlation
    # without randomness or empirical labels from any algorithm.
    prime_bases = (2, 3, 5, 7, 11, 13, 17)
    fill_index = 1
    while len(vectors) < sample_count:
        fractions = {
            name: radical_inverse(fill_index, base)
            for name, base in zip(ACTIVE_PARAMS, prime_bases)
        }
        add(fractions)
        fill_index += 1
        if fill_index > sample_count * 20:
            raise RuntimeError("global plan coverage could not produce unique points")
    return vectors[:sample_count]


def resolve_convergence_contract(algorithm_cfg: Mapping[str, Any]) -> Dict[str, Any]:
    """resolve convergence contract."""

    adaptive_interval_control = isinstance(
        (algorithm_cfg.get("budgeted_trial") or {}).get("adaptive_search"), Mapping,
    )
    accepted_target = int(
        algorithm_cfg.get("accepted_target", DEFAULT_ACCEPTED_TARGET)
    )
    if accepted_target < 1:
        raise ValueError("Algorithm C accepted_target must be positive")
    normalized_width_threshold = float(
        algorithm_cfg.get(
            "normalized_width_threshold",
            0.01 if adaptive_interval_control else 0.5 ** accepted_target,
        )
    )
    if not 0.0 < normalized_width_threshold <= 1.0:
        raise ValueError(
            "Algorithm C normalized_width_threshold must be in (0, 1]"
        )
    expected_normalized_width = 0.5 ** accepted_target
    if not adaptive_interval_control and expected_normalized_width > normalized_width_threshold + 1e-12:
        raise ValueError(
            "Algorithm C accepted_target cannot reach normalized width threshold: "
            f"0.5 ** {accepted_target}={expected_normalized_width} > "
            f"{normalized_width_threshold}"
        )
    # Stability replay is an engineering check, not part of Algorithm A/B/C
    # convergence. Legacy configurations can still contain the old fields,
    # but the formal controller resolves them to zero and never issues a
    # post-precision confirmation workload.
    stability_confirmation_rounds = 0
    stability_confirmation_max_attempts = 0
    outer_round_limit = int(
        algorithm_cfg.get("outer_round_limit", accepted_target)
    )
    if outer_round_limit < 1:
        raise ValueError("Algorithm B/C outer_round_limit must be positive")
    # Two complete no-improvement rounds are search evidence for an interval
    # refinement. They are not post-convergence stability confirmation and
    # remain identical across Algorithm B and C for fair comparison.
    no_improvement_convergence_rounds = 2
    return {
        "accepted_target": accepted_target,
        "normalized_width_threshold": normalized_width_threshold,
        "expected_normalized_width": expected_normalized_width,
        "stability_confirmation_rounds": stability_confirmation_rounds,
        "stability_confirmation_max_attempts": (
            stability_confirmation_max_attempts
        ),
        "outer_round_limit": outer_round_limit,
        "no_improvement_convergence_rounds": (
            no_improvement_convergence_rounds
        ),
    }


STABILITY_CONFIRMATION_PROTOCOL = (
    "normalized_width_1pct_two_consecutive_outer_accepts_v6"
)
POST_CONVERGENCE_STABILITY_POLICY = (
    "post_convergence_stability_not_required_engineering_only_v1"
)
INCUMBENT_CONVERGENCE_CONTRACT = (
    "effective_1pct_interval_without_stability_confirmation_v5"
)
EFFECTIVE_SEARCH_PROGRESS_CONTRACT = (
    "qps_accepts_plus_incumbent_centered_binary_refinements_v1"
)
OUTER_PRUNING_CONTRACT = "algorithm_c_outer_accept_reject_plan_context_v2"
OUTER_QPS_PROGRESS_CONTRACT = (
    "candidate_qps_strictly_above_previous_accepted_round_v1"
)
RESOURCE_PREDICTION_CALIBRATION_CONTRACT = (
    "training_loo_selected_plan_parameter_hybrid_v1"
)
ACCEPTANCE_TIER_RANK = {"strict": 3, "secondary": 2, "fallback": 1}
OUTER_PRUNING_LEARNABLE_REJECTS = frozenset(
    {
        "db_qps_not_up_vs_canonical_baseline",
        "db_qps_not_up_vs_last_accepted",
        "non_target_resource_became_bottleneck",
    }
)


def formal_inner_scoring_options(target: str) -> Dict[str, Any]:
    """Return the explicit fail-closed Algorithm C inner scoring contract."""
    normalized_target = str(target).strip().lower()
    if normalized_target not in {"cpu", "memory", "io", "none"}:
        raise ValueError(f"unsupported Algorithm C target resource: {target!r}")
    target_ranges: Dict[str, Dict[str, Any]] = {}
    for resource in ("cpu", "memory", "io"):
        if normalized_target != "none" and resource == normalized_target:
            target_ranges[resource] = {
                "min": 0.85,
                "max": 0.90,
                "hard": 0.95,
                "role": "target_bottleneck",
            }
        elif resource == "io":
            target_ranges[resource] = {
                "min": 0.30,
                "max": 0.50,
                "hard": 0.80 if normalized_target == "none" else 0.90,
                "role": "non_bottleneck_io_cost",
            }
        else:
            target_ranges[resource] = {
                "min": 0.30,
                "max": 0.50,
                "hard": 0.80 if normalized_target == "none" else 0.90,
                "role": "non_bottleneck",
            }
    return {
        "score_mode": "band_target",
        "resource_weights": {
            "cpu": 1.0,
            "memory": 1.0,
            "io": 3.0 if normalized_target == "io" else 0.5,
        },
        "resource_target_ranges": target_ranges,
        "directional_scoring_contract_required": True,
    }


def _decision_io_up_ratio(
    decision: Mapping[str, Any],
    *,
    comparison: str,
) -> float:
    """Return only the positive non-target IO change used for soft ranking."""
    if str(decision.get("target_resource") or "").lower() == "io":
        return 0.0
    gains = decision.get(f"other_gain_vs_{comparison}")
    gains = gains if isinstance(gains, Mapping) else {}
    value = gains.get("io")
    try:
        return max(0.0, float(value)) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def accepted_batch_selection_key(evaluation: Mapping[str, Any]) -> Tuple[Any, ...]:
    """Preserve outer semantics and use IO only as the final soft tie-break."""
    decision = evaluation.get("decision")
    decision = decision if isinstance(decision, Mapping) else {}
    target_drop = float(decision.get("target_drop_vs_reference") or 0.0)
    qps_gain = float(decision.get("qps_gate_gain") or -math.inf)
    io_up = _decision_io_up_ratio(decision, comparison="reference")
    return (
        ACCEPTANCE_TIER_RANK.get(
            str(decision.get("acceptance_tier") or ""),
            0,
        ),
        target_drop,
        qps_gain,
        -io_up,
    )


def feasible_champion_sort_key(champion: Mapping[str, Any]) -> Tuple[Any, ...]:
    """Rank process-wide accepted points without changing search acceptance."""
    # Search acceptance may be relative to the current incumbent.  Final
    # authority is always relative to the canonical baseline, so prefer the
    # canonical re-decision when it is available.  Keeping both decisions in
    # the archive makes the path-dependent search semantics auditable without
    # allowing a transient ``strict`` label to dominate final selection.
    decision = champion.get("final_decision") or champion.get("decision")
    decision = decision if isinstance(decision, Mapping) else {}
    normalized = champion.get("normalized")
    normalized = normalized if isinstance(normalized, Mapping) else {}
    qps_gain = float(decision.get("qps_gain_vs_baseline") or -math.inf)
    target_drop = float(decision.get("target_drop_vs_baseline") or 0.0)
    io_up = _decision_io_up_ratio(decision, comparison="baseline")
    return (
        ACCEPTANCE_TIER_RANK.get(
            str(decision.get("acceptance_tier") or ""),
            0,
        ),
        qps_gain,
        target_drop,
        -io_up,
        float(normalized.get("db_qps") or -math.inf),
        -int(champion.get("accepted_index") or 0),
    )


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(
                json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True)
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def algorithm_c_tmp_space_preflight(
    *,
    path: Path = Path("/tmp"),
    minimum_available_bytes: Optional[int] = None,
) -> Dict[str, Any]:
    """Fail closed before a cohort marker or physical wave can be started."""
    if minimum_available_bytes is None:
        raw_minimum = os.environ.get(
            "BONECT_ALGORITHM_C_MIN_TMP_AVAILABLE_BYTES",
            str(DEFAULT_MIN_TMP_AVAILABLE_BYTES),
        )
        try:
            minimum_available_bytes = max(0, int(raw_minimum))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "invalid BONECT_ALGORITHM_C_MIN_TMP_AVAILABLE_BYTES: "
                f"{raw_minimum!r}"
            ) from exc
    stat = os.statvfs(path)
    available_bytes = int(stat.f_bavail) * int(stat.f_frsize)
    free_bytes = int(stat.f_bfree) * int(stat.f_frsize)
    result = {
        "contract": "algorithm_c_tmp_space_preflight_v1",
        "path": str(path),
        "available_bytes": available_bytes,
        "free_bytes": free_bytes,
        "minimum_available_bytes": int(minimum_available_bytes),
        "passed": available_bytes >= int(minimum_available_bytes),
    }
    if result["passed"] is not True:
        raise RuntimeError(
            "Algorithm C tmp space preflight failed: "
            f"available={available_bytes} "
            f"required={int(minimum_available_bytes)} path={path}"
        )
    return result


def _validated_cohort_rows(
    barrier_dir: Path,
    *,
    suffix: str,
    participant_identity_fields: Mapping[str, Any],
    expected_fields: Mapping[str, Any],
    require_wave_valid: bool = False,
    require_live_pid: bool = False,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Read only complete, stage-matching, uniquely identified cohort rows."""
    rows_by_scene: Dict[str, Dict[str, Any]] = {}
    invalid_files: List[str] = []
    for path in barrier_dir.glob(f"*{suffix}"):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            invalid_files.append(path.name)
            continue
        if not isinstance(row, dict):
            invalid_files.append(path.name)
            continue
        scene = str(row.get("scene") or "")
        if not scene:
            invalid_files.append(path.name)
            continue
        if any(row.get(key) != value for key, value in expected_fields.items()):
            invalid_files.append(path.name)
            continue
        expected_name = (
            canonical_sha256(
                {
                    "scene": scene,
                    **dict(participant_identity_fields),
                }
            )
            + suffix
        )
        if path.name != expected_name:
            invalid_files.append(path.name)
            continue
        if require_wave_valid and not isinstance(row.get("wave_valid"), bool):
            invalid_files.append(path.name)
            continue
        if require_live_pid:
            try:
                pid = int(row.get("pid") or 0)
                if pid <= 0:
                    raise ValueError("missing pid")
                os.kill(pid, 0)
            except (OSError, TypeError, ValueError):
                invalid_files.append(path.name)
                continue
        if scene in rows_by_scene:
            invalid_files.append(path.name)
            continue
        rows_by_scene[scene] = row
    return list(rows_by_scene.values()), sorted(set(invalid_files))


def append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(value), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def validate_experience_artifact(artifact: Mapping[str, Any], target: str) -> None:
    schema_version = int(artifact.get("schema_version") or 0)
    if schema_version < 4:
        raise RuntimeError("experience table schema must include direction/design admission gates")
    if str(artifact.get("target_resource") or "") != target:
        raise RuntimeError("experience table target resource mismatch")
    if schema_version >= 8:
        validate_training_runtime_boundary(artifact)
    admission = artifact.get("formal_admission")
    if not isinstance(admission, Mapping) or admission.get("ready") is not True:
        raise RuntimeError(
            "experience table holdout admission failed: "
            f"{(admission or {}).get('errors') if isinstance(admission, Mapping) else 'missing'}"
        )
    none_target_contract = is_none_target(target)
    hard_structural_contract = (
        str(admission.get("scored_resource_admission_contract") or "")
        == "hard_structural_prediction_quality_diagnostic_v3"
    )
    fit = artifact.get("fit")
    if not isinstance(fit, Mapping):
        raise RuntimeError("experience table fit evidence missing")
    train_count = int(fit.get("train_count") or 0)
    holdout_count = int(fit.get("holdout_count") or 0)
    required_train_count = (
        20 if schema_version >= 8 else (7 if hard_structural_contract else 10)
    )
    required_holdout_count = (
        30
        if schema_version >= 10
        else (10 if schema_version >= 8 else (3 if hard_structural_contract else 5))
    )
    if (
        train_count < required_train_count
        or holdout_count < required_holdout_count
    ):
        raise RuntimeError(
            "experience table has insufficient training/holdout rows: "
            f"required={required_train_count}/{required_holdout_count} "
            f"observed={train_count}/{holdout_count}"
        )
    if int(admission.get("minimum_train_count") or 0) < required_train_count:
        raise RuntimeError("experience table minimum train contract is too low")
    if int(admission.get("minimum_holdout_count") or 0) < required_holdout_count:
        raise RuntimeError("experience table minimum holdout contract is too low")
    calibration = artifact.get("resource_prediction_calibration")
    if not isinstance(calibration, Mapping):
        raise RuntimeError("experience table resource prediction calibration missing")
    if str(calibration.get("contract") or "") != RESOURCE_PREDICTION_CALIBRATION_CONTRACT:
        raise RuntimeError("experience table resource prediction calibration contract mismatch")
    if list(calibration.get("active_params") or []) != list(ACTIVE_PARAMS):
        raise RuntimeError("resource prediction calibration active parameter mismatch")
    if calibration.get("qps_used") is not False:
        raise RuntimeError("resource prediction calibration must exclude QPS")
    if calibration.get("independent_holdout_used_for_model_selection") is not False:
        raise RuntimeError("resource prediction calibration leaked independent holdout")
    if calibration.get("zero_intercept") is not True:
        raise RuntimeError("resource prediction calibration must preserve zero intercept")
    calibration_resources = calibration.get("resources")
    if not isinstance(calibration_resources, Mapping):
        raise RuntimeError("resource prediction calibration resources missing")
    target_safety_mode = "direct_resource_response"
    allowed_target_safety_modes = {
        "direct_resource_response",
        "flat_target_safe_abstention",
        "monotone_target_down_distance",
        "irreducible_noise_safe_abstention",
    }
    for resource in ("cpu", "memory", "io"):
        row = calibration_resources.get(resource)
        if not isinstance(row, Mapping):
            raise RuntimeError(f"resource prediction calibration missing {resource}")
        if str(row.get("family") or "") not in {"raw_plan", "parameter", "hybrid"}:
            raise RuntimeError(f"resource prediction calibration family invalid: {resource}")
        coefficients = row.get("parameter_coefficients")
        if not isinstance(coefficients, Mapping) or set(coefficients) != set(ACTIVE_PARAMS):
            raise RuntimeError(
                f"resource prediction calibration coefficients invalid: {resource}"
            )
        numeric_values = [
            *coefficients.values(),
            row.get("plan_log_ratio_coefficient"),
        ]
        if not all(
            isinstance(value, (int, float)) and math.isfinite(float(value))
            for value in numeric_values
        ):
            raise RuntimeError(
                f"resource prediction calibration contains non-finite values: {resource}"
            )
        if schema_version >= 8:
            interaction_coefficients = row.get(
                "parameter_interaction_coefficients"
            )
            expected_interactions = {
                f"{left}*{right}"
                for left, right in parameter_interaction_names(ACTIVE_PARAMS)
            }
            if (
                str(row.get("parameter_feature_contract") or "")
                != "resource_response_surface_v2"
                or not isinstance(interaction_coefficients, Mapping)
                or set(interaction_coefficients) != expected_interactions
                or not all(
                    isinstance(value, (int, float))
                    and math.isfinite(float(value))
                    for value in interaction_coefficients.values()
                )
            ):
                raise RuntimeError(
                    "resource prediction interaction calibration invalid: "
                    f"{resource}"
                )
            for coefficient_field in (
                "parameter_squared_coefficients",
                "parameter_plan_interaction_coefficients",
            ):
                coefficient_map = row.get(coefficient_field)
                if (
                    not isinstance(coefficient_map, Mapping)
                    or set(coefficient_map) != set(ACTIVE_PARAMS)
                    or not all(
                        isinstance(value, (int, float))
                        and math.isfinite(float(value))
                        for value in coefficient_map.values()
                    )
                ):
                    raise RuntimeError(
                        "resource prediction response-surface calibration "
                        f"invalid: {resource}:{coefficient_field}"
                    )
            plan_squared = row.get("plan_log_ratio_squared_coefficient")
            if (
                not isinstance(plan_squared, (int, float))
                or not math.isfinite(float(plan_squared))
            ):
                raise RuntimeError(
                    "resource prediction plan curvature invalid: "
                    f"{resource}"
                )
        if schema_version >= 10:
            safety_mode = str(
                row.get("target_prediction_safety_mode")
                or "direct_resource_response"
            )
            if safety_mode not in allowed_target_safety_modes:
                raise RuntimeError(
                    f"resource prediction safety mode invalid: {resource}={safety_mode}"
                )
            if resource != target and safety_mode != "direct_resource_response":
                raise RuntimeError(
                    "target prediction safety mode applied to non-target resource: "
                    f"{resource}={safety_mode}"
                )
            if resource == target:
                target_safety_mode = safety_mode
                if row.get("safety_mode_selected_without_final_holdout") is not True:
                    raise RuntimeError(
                        "target safety mode must be selected without final holdout"
                    )
                zero_fields = [
                    *list((row.get("parameter_coefficients") or {}).values()),
                    *list(
                        (row.get("parameter_squared_coefficients") or {}).values()
                    ),
                    *list(
                        (row.get("parameter_interaction_coefficients") or {}).values()
                    ),
                    *list(
                        (
                            row.get("parameter_plan_interaction_coefficients")
                            or {}
                        ).values()
                    ),
                    row.get("plan_log_ratio_coefficient"),
                    row.get("plan_log_ratio_squared_coefficient"),
                ]
                if safety_mode != "direct_resource_response" and any(
                    abs(float(value or 0.0)) > 1e-12 for value in zero_fields
                ):
                    raise RuntimeError(
                        "safe target predictor must neutralize direct-model coefficients"
                    )
                scale = float(row.get("monotone_parameter_l2_scale") or 0.0)
                reference = float(row.get("monotone_reference_l2") or 0.0)
                if safety_mode == "monotone_target_down_distance":
                    if not math.isfinite(scale) or scale <= 0.0:
                        raise RuntimeError("monotone target scale must be positive")
                    if not math.isfinite(reference) or reference <= 0.0:
                        raise RuntimeError("monotone target reference must be positive")
                elif safety_mode in {
                    "flat_target_safe_abstention",
                    "irreducible_noise_safe_abstention",
                } and scale != 0.0:
                    raise RuntimeError("abstaining target predictor has non-zero scale")
        if schema_version >= 6:
            error_margin = row.get("training_loo_mae_log_ratio")
            if (
                not isinstance(error_margin, (int, float))
                or not math.isfinite(float(error_margin))
                or float(error_margin) < 0.0
            ):
                raise RuntimeError(
                    "resource prediction calibration contains invalid "
                    f"pre-holdout error margin: {resource}"
                )
        support_domain = row.get("parameter_support_domain")
        support_enabled = row.get("parameter_support_abstention_enabled") is True
        if support_enabled:
            if none_target_contract or resource != target:
                raise RuntimeError(
                    "parameter support abstention is only valid for target resource"
                )
            if not isinstance(support_domain, Mapping):
                raise RuntimeError("target parameter support domain missing")
            if str(support_domain.get("contract") or "") != PARAMETER_SUPPORT_CONTRACT:
                raise RuntimeError("target parameter support contract mismatch")
            reference_points = support_domain.get("reference_points")
            if not isinstance(reference_points, list) or len(reference_points) < 2:
                raise RuntimeError("target parameter support references missing")
            parameter_support_assessment(
                [0.0] * len(ACTIVE_PARAMS),
                support_domain,
                ACTIVE_PARAMS,
            )
        elif schema_version >= 7 and resource == target:
            raise RuntimeError("schema 7 target support abstention is disabled")
    observed_design = admission.get("observed_training_design")
    if not isinstance(observed_design, Mapping):
        raise RuntimeError("experience table admission evidence missing")
    if schema_version >= 10:
        if (
            str(admission.get("target_prediction_safety_mode") or "")
            != target_safety_mode
            or str(admission.get("target_prediction_safety_contract") or "")
            != "historical_validation_selected_direct_monotone_or_safe_abstention_v1"
            or admission.get(
                "target_prediction_safety_mode_selected_without_final_holdout"
            )
            is not True
        ):
            raise RuntimeError(
                "experience table target prediction safety admission mismatch"
            )
        target_table_neutralized = target_safety_mode != "direct_resource_response"
        if admission.get("target_gradient_table_neutralized") is not target_table_neutralized:
            raise RuntimeError("experience table target gradient safety mismatch")
        table = artifact.get("table")
        if not isinstance(table, Mapping):
            raise RuntimeError("experience table parameter rows missing")
        if target_table_neutralized and any(
            not isinstance(row, Mapping)
            or abs(float(row.get(target) or 0.0)) > 1e-12
            for row in table.values()
        ):
            raise RuntimeError(
                "safe target predictor leaked a signed target gradient"
            )
    if schema_version >= 8:
        if (
            str(admission.get("scored_resource_admission_contract") or "")
            != "independent_grouped_holdout_resource_quality_v4"
            or admission.get("prediction_quality_blocks_formal_admission")
            is not True
            or str(admission.get("hard_gate_contract") or "")
            != "resource_rank_mae_direction_support_and_design_v2"
        ):
            raise RuntimeError(
                "experience table fail-closed prediction quality contract mismatch"
            )
        holdout = artifact.get("holdout")
        metrics_by_resource = (
            holdout.get("metrics") if isinstance(holdout, Mapping) else None
        )
        if not isinstance(metrics_by_resource, Mapping):
            raise RuntimeError("experience table holdout metrics missing")
        target_mae_limit = float(
            admission.get("maximum_target_mae_log_ratio") or math.nan
        )
        non_target_mae_limit = float(
            admission.get("maximum_non_target_mae_log_ratio") or math.nan
        )
        minimum_target_spearman = float(
            admission.get("minimum_target_holdout_spearman") or math.nan
        )
        minimum_sign_count = int(
            admission.get("minimum_scored_resource_material_sign_count") or 0
        )
        minimum_sign_accuracy = float(
            admission.get("minimum_scored_resource_sign_accuracy") or math.nan
        )
        if (
            not math.isclose(
                target_mae_limit,
                math.log(1.10),
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            or not math.isclose(
                non_target_mae_limit,
                math.log(1.15),
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            or minimum_target_spearman < 0.50
            or minimum_sign_count < 10
            or minimum_sign_accuracy < 0.75
        ):
            raise RuntimeError("experience table prediction thresholds weakened")
        for resource in ("cpu", "memory", "io"):
            resource_metrics = metrics_by_resource.get(resource)
            if not isinstance(resource_metrics, Mapping):
                raise RuntimeError(
                    f"experience table holdout resource missing: {resource}"
                )
            mae = float(resource_metrics.get("mae_log_ratio") or math.inf)
            mae_limit = target_mae_limit if resource == target else non_target_mae_limit
            if not math.isfinite(mae):
                raise RuntimeError(
                    f"experience table {resource} MAE is not finite"
                )
            direction = resource_metrics.get("direction")
            if not isinstance(direction, Mapping):
                raise RuntimeError(
                    f"experience table {resource} direction metrics missing"
                )
            if resource == target:
                material_count = int(
                    direction.get("material_sign_evaluable_count") or 0
                )
                actual_down = int(direction.get("actual_target_down_count") or 0)
                predicted_down = int(
                    direction.get("point_predicted_target_down_count") or 0
                )
                confirmed_down = int(
                    direction.get("point_confirmed_target_down_count") or 0
                )
                predicted_precision = float(
                    direction.get("point_predicted_target_down_precision") or 0.0
                )
                predicted_values = [
                    float(value)
                    for value in resource_metrics.get("predicted") or []
                ]
                actual_values = [
                    float(value) for value in resource_metrics.get("actual") or []
                ]
                if target_safety_mode == "direct_resource_response":
                    observed_spearman = resource_metrics.get("spearman")
                    if mae > mae_limit:
                        raise RuntimeError(
                            f"experience table target MAE gate failed: {mae} > {mae_limit}"
                        )
                    if (
                        material_count < minimum_sign_count
                        or float(direction.get("sign_accuracy") or 0.0) + 1e-12
                        < minimum_sign_accuracy
                    ):
                        raise RuntimeError("experience table target sign gate failed")
                    if (
                        observed_spearman is None
                        or float(observed_spearman) + 1e-12
                        < minimum_target_spearman
                    ):
                        raise RuntimeError(
                            "experience table target Spearman gate failed: "
                            f"{observed_spearman} < {minimum_target_spearman}"
                        )
                elif target_safety_mode == "flat_target_safe_abstention":
                    if (
                        not actual_values
                        or max(abs(value) for value in actual_values) >= math.log(1.01)
                        or any(abs(value) > 1e-12 for value in predicted_values)
                    ):
                        raise RuntimeError("flat target safety holdout gate failed")
                elif target_safety_mode == "irreducible_noise_safe_abstention":
                    if (
                        any(abs(value) > 1e-12 for value in predicted_values)
                        or predicted_down != 0
                    ):
                        raise RuntimeError(
                            "irreducible target abstention holdout gate failed"
                        )
                elif target_safety_mode == "monotone_target_down_distance":
                    predicted_positions = [
                        index
                        for index, value in enumerate(predicted_values)
                        if value < -1e-12
                    ]
                    nonincrease_count = sum(
                        actual_values[index] <= 0.0
                        for index in predicted_positions
                    )
                    nonincrease_precision = (
                        nonincrease_count / len(predicted_positions)
                        if predicted_positions
                        else 0.0
                    )
                    if (
                        material_count < minimum_sign_count
                        or actual_down / max(1, material_count) < 0.80
                        or len(predicted_positions) < 2
                        or nonincrease_count < 2
                        or nonincrease_precision + 1e-12 < 0.80
                        or mae > mae_limit
                    ):
                        raise RuntimeError("monotone target holdout gate failed")
            elif schema_version < 10:
                if mae > mae_limit:
                    raise RuntimeError(
                        f"experience table {resource} MAE gate failed: "
                        f"{mae} > {mae_limit}"
                    )
                if (
                    int(direction.get("material_sign_evaluable_count") or 0)
                    < minimum_sign_count
                    or float(direction.get("sign_accuracy") or 0.0) + 1e-12
                    < minimum_sign_accuracy
                ):
                    raise RuntimeError(
                        f"experience table {resource} sign gate failed"
                    )
    if none_target_contract:
        if str(admission.get("none_target_admission_contract") or "") != (
            "all_resource_holdout_mae_no_bottleneck_v1"
        ):
            raise RuntimeError("none-target experience admission contract mismatch")
        holdout = artifact.get("holdout")
        holdout = holdout if isinstance(holdout, Mapping) else {}
        holdout_metrics = holdout.get("metrics")
        holdout_metrics = (
            holdout_metrics if isinstance(holdout_metrics, Mapping) else {}
        )
        maximum_mae = float(
            admission.get("maximum_all_resource_mae_log_ratio") or math.nan
        )
        if not math.isfinite(maximum_mae) or maximum_mae <= 0.0:
            raise RuntimeError("none-target maximum all-resource MAE is invalid")
        observed_mae: Dict[str, float] = {}
        for resource in ("cpu", "memory", "io"):
            metrics = holdout_metrics.get(resource)
            metrics = metrics if isinstance(metrics, Mapping) else {}
            mae = float(metrics.get("mae_log_ratio") or math.inf)
            if not math.isfinite(mae) or mae > maximum_mae:
                raise RuntimeError(
                    "none-target experience all-resource MAE gate failed: "
                    f"{resource}={mae} > {maximum_mae}"
                )
            observed_mae[resource] = mae
        declared_mae = admission.get("observed_all_resource_mae_log_ratio")
        if not isinstance(declared_mae, Mapping) or any(
            not math.isclose(
                float(declared_mae.get(resource) or math.inf),
                observed_mae[resource],
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            for resource in ("cpu", "memory", "io")
        ):
            raise RuntimeError("none-target declared all-resource MAE mismatch")
        directional_contract = ""
        comparisons: Sequence[Tuple[float, float, str]] = ()
        point_prediction_contract = False
    else:
        observed_direction = admission.get("observed_target_direction")
        if not isinstance(observed_direction, Mapping):
            raise RuntimeError("experience table target direction evidence missing")
        directional_contract = str(
            admission.get("directional_admission_contract") or ""
        )
    if none_target_contract:
        pass
    elif hard_structural_contract:
        if admission.get("prediction_quality_blocks_formal_admission") is not False:
            raise RuntimeError(
                "experience table hard structural admission contract mismatch"
            )
        if str(admission.get("hard_gate_contract") or "") != (
            "train_holdout_full_rank_condition_nonzero_confidence_v1"
        ):
            raise RuntimeError("experience table hard gate contract mismatch")
    point_prediction_contract = False
    if none_target_contract:
        comparisons = ()
    elif hard_structural_contract:
        # Independent-holdout quality remains in the artifact and affects
        # confidence/ranking, but it is not repeated as a formal-start gate.
        comparisons = ()
    elif schema_version >= 10 and target_safety_mode != "direct_resource_response":
        comparisons = ()
    elif schema_version >= 6:
        if directional_contract not in {
            "robust_error_margin_target_down_selection_precision_v2",
            "independent_holdout_point_selection_precision_v3",
            "independent_holdout_nominal_point_prediction_precision_v3",
            "independent_holdout_support_abstention_precision_v4",
        }:
            raise RuntimeError("experience table directional admission contract mismatch")
        if str(observed_direction.get("prediction_error_margin_semantics") or "") != (
            "pre_holdout_empirical_error_envelope_not_confidence_interval"
        ):
            raise RuntimeError(
                "experience table prediction error margin semantics mismatch"
            )
        point_prediction_contract = directional_contract in {
            "independent_holdout_point_selection_precision_v3",
            "independent_holdout_nominal_point_prediction_precision_v3",
            "independent_holdout_support_abstention_precision_v4",
        }
        if point_prediction_contract:
            comparisons = (
                (
                    float(
                        observed_direction.get(
                            "point_predicted_target_down_precision"
                        )
                        or 0.0
                    ),
                    float(
                        admission.get(
                            "minimum_point_predicted_target_down_precision"
                        )
                        or math.inf
                    ),
                    "point target-down precision",
                ),
            )
        else:
            comparisons = (
                (
                    float(
                        observed_direction.get(
                            "robust_predicted_target_down_precision"
                        )
                        or 0.0
                    ),
                    float(
                        admission.get("minimum_predicted_target_down_precision")
                        or math.inf
                    ),
                    "robust target-down precision",
                ),
            )
    elif schema_version >= 5:
        if directional_contract != "predicted_target_down_selection_precision_v1":
            raise RuntimeError("experience table directional admission contract mismatch")
        comparisons = (
            (
                float(observed_direction.get("predicted_target_down_precision") or 0.0),
                float(admission.get("minimum_predicted_target_down_precision") or math.inf),
                "target-down precision",
            ),
        )
        holdout = artifact.get("holdout") if isinstance(artifact.get("holdout"), Mapping) else {}
        metrics = holdout.get("metrics") if isinstance(holdout.get("metrics"), Mapping) else {}
        target_metrics = metrics.get(target) if isinstance(metrics.get(target), Mapping) else {}
        observed_spearman = float(target_metrics.get("spearman") or -math.inf)
        required_spearman = float(
            admission.get("minimum_target_holdout_spearman") or math.inf
        )
        if observed_spearman < required_spearman:
            raise RuntimeError(
                f"experience table target Spearman gate failed: "
                f"{observed_spearman} < {required_spearman}"
            )
        observed_mae = float(target_metrics.get("mae_log_ratio") or math.inf)
        maximum_mae = float(admission.get("maximum_target_mae_log_ratio") or math.nan)
        if not math.isfinite(observed_mae) or not math.isfinite(maximum_mae) or observed_mae > maximum_mae:
            raise RuntimeError(
                f"experience table target MAE gate failed: {observed_mae} > {maximum_mae}"
            )
    else:
        comparisons = (
            (
                float(observed_direction.get("sign_accuracy") or 0.0),
                float(admission.get("minimum_target_holdout_sign_accuracy") or math.inf),
                "target sign accuracy",
            ),
            (
                float(observed_direction.get("sign_accuracy_wilson90_lower") or 0.0),
                float(admission.get("minimum_target_holdout_sign_wilson90_lower") or math.inf),
                "target sign Wilson lower bound",
            ),
            (
                float(observed_direction.get("predicted_target_down_precision") or 0.0),
                float(admission.get("minimum_predicted_target_down_precision") or math.inf),
                "target-down precision",
            ),
        )
    for observed, required, label in comparisons:
        if not math.isfinite(observed) or not math.isfinite(required) or observed < required:
            raise RuntimeError(f"experience table {label} gate failed: {observed} < {required}")
    integer_comparisons = [
        (
            int(observed_design.get("matrix_rank") or 0),
            int(admission.get("required_training_design_rank") or 0),
            "training design rank",
        )
    ]
    if (
        not hard_structural_contract
        and not none_target_contract
        and not (schema_version >= 10 and target_safety_mode != "direct_resource_response")
    ):
        integer_comparisons[0:0] = [
            (
                int(observed_direction.get("material_sign_evaluable_count") or 0),
                int(admission.get("minimum_material_target_sign_count") or 0),
                "material target sign count",
            ),
            (
                int(observed_direction.get("actual_target_down_count") or 0),
                int(admission.get("minimum_actual_target_down_count") or 0),
                "actual target-down count",
            ),
            *(
                [
                    (
                        int(
                            observed_direction.get(
                                "point_predicted_target_down_count"
                                if point_prediction_contract
                                else "robust_predicted_target_down_count"
                            )
                            or 0
                        ),
                        int(
                            admission.get(
                                "minimum_point_predicted_target_down_count"
                                if point_prediction_contract
                                else "minimum_robust_predicted_target_down_count"
                            )
                            or 0
                        ),
                        "point predicted target-down count"
                        if point_prediction_contract
                        else "robust predicted target-down count",
                    )
                ]
                if schema_version >= 6
                else []
            ),
            (
                int(
                    observed_direction.get(
                        "point_confirmed_target_down_count"
                        if point_prediction_contract
                        else "confirmed_target_down_count"
                    )
                    or 0
                ),
                int(
                    admission.get(
                        "minimum_point_confirmed_target_down_count"
                        if point_prediction_contract
                        else "minimum_confirmed_target_down_count"
                    )
                    or 0
                ),
                "point confirmed target-down count"
                if point_prediction_contract
                else "confirmed target-down count",
            ),
        ]
    for observed, required, label in integer_comparisons:
        if required <= 0 or observed < required:
            raise RuntimeError(f"experience table {label} gate failed: {observed} < {required}")
    condition = float(observed_design.get("scaled_condition_number") or math.inf)
    maximum_condition = float(
        admission.get("maximum_training_design_condition_number") or math.nan
    )
    if not math.isfinite(condition) or not math.isfinite(maximum_condition) or condition > maximum_condition:
        raise RuntimeError("experience table training design condition gate failed")
    expected_artifact_sha = str(artifact.get("artifact_sha256") or "")
    payload = copy.deepcopy(dict(artifact))
    payload.pop("artifact_sha256", None)
    if not expected_artifact_sha or canonical_sha256(payload) != expected_artifact_sha:
        raise RuntimeError("experience table artifact SHA mismatch")


def exact_param_signature(params: Mapping[str, Any]) -> Tuple[str, ...]:
    return tuple(format(float(params[name]), ".17g") for name in ACTIVE_PARAMS)


def physical_replay_artifact_identity_token(
    candidate: Mapping[str, Any],
) -> str:
    """Return a deterministic token that prevents resumed artifact overwrites."""

    params = candidate.get("optimized_params")
    fingerprint = candidate.get("three_level_fingerprint")
    plan_level = (
        fingerprint.get("plan_level")
        if isinstance(fingerprint, Mapping)
        else None
    )
    if not isinstance(params, Mapping):
        raise RuntimeError("physical replay artifact identity requires params")
    if not str(plan_level or ""):
        plan_level = str(candidate.get("candidate_plan_signature") or "")
    if not plan_level:
        # Lightweight derivative/unit-test candidates can predate the formal
        # full-plan field. Hash their complete immutable ranking payload so
        # resumed artifacts still cannot collide merely because a slot label
        # is reused. Formal B/C candidates are validated with plan_level.
        plan_level = f"candidate-fallback:{canonical_sha256(candidate)}"
    if all(name in params for name in ACTIVE_PARAMS):
        param_identity: Any = list(exact_param_signature(params))
    else:
        param_identity = {
            str(name): format(float(value), ".17g")
            for name, value in sorted(params.items())
        }
    payload = {
        "exact_param_signature": param_identity,
        "full_plan_fingerprint": str(plan_level),
    }
    return canonical_sha256(payload)[:12]


def parameter_vector_range_audit(
    params: Any,
    ranges: Mapping[str, ParameterRange],
) -> Dict[str, Any]:
    """Return a fail-closed audit that every formal parameter is in range."""

    violations: Dict[str, Dict[str, Any]] = {}
    if not isinstance(params, Mapping):
        return {
            "passed": False,
            "reason": "optimized_params_missing",
            "violations": {},
        }
    for name in ACTIVE_PARAMS:
        current_range = ranges.get(name)
        if current_range is None or name not in params:
            violations[name] = {
                "reason": "parameter_or_range_missing",
            }
            continue
        try:
            value = float(params[name])
            low = float(current_range.min_value)
            high = float(current_range.max_value)
        except (TypeError, ValueError) as exc:
            violations[name] = {
                "reason": "non_numeric_parameter_or_range",
                "error": str(exc),
            }
            continue
        tolerance = max(1e-15, 1e-12 * max(abs(low), abs(high), 1.0))
        if (
            not math.isfinite(value)
            or not math.isfinite(low)
            or not math.isfinite(high)
            or value < low - tolerance
            or value > high + tolerance
        ):
            violations[name] = {
                "reason": "outside_formal_range",
                "value": value,
                "min_value": low,
                "max_value": high,
                "tolerance": tolerance,
            }
    return {
        "passed": not violations,
        "reason": "within_formal_ranges" if not violations else "outside_formal_ranges",
        "violations": violations,
    }


def partition_formal_range_candidates(
    candidates: Sequence[Dict[str, Any]],
    ranges: Mapping[str, ParameterRange],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Partition offline candidates before formal plan-cluster Top-K ranking."""

    eligible: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    for candidate in candidates:
        audit = parameter_vector_range_audit(
            candidate.get("optimized_params"),
            ranges,
        )
        if audit.get("passed") is True:
            eligible.append(candidate)
        else:
            excluded.append(
                {
                    "candidate": candidate,
                    "range_audit": audit,
                }
            )
    return eligible, excluded


def validate_zero_gradient_delta(candidate: Mapping[str, Any], position: int) -> None:
    """Enforce the formal invariant: an all-zero g_j cannot move parameters."""

    raw_gradient = candidate.get("g_j")
    raw_delta = candidate.get("delta_param")
    if not isinstance(raw_gradient, Mapping) or not raw_gradient:
        raise RuntimeError(
            f"formal Algorithm C Top-5 rank {position} has invalid g_j"
        )
    if not isinstance(raw_delta, Mapping):
        raise RuntimeError(
            f"formal Algorithm C Top-5 rank {position} has invalid delta_param"
        )

    gradient_values: List[float] = []
    for name, raw_value in raw_gradient.items():
        try:
            value = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"formal Algorithm C Top-5 rank {position} has non-numeric g_j[{name!r}]"
            ) from exc
        if not math.isfinite(value):
            raise RuntimeError(
                f"formal Algorithm C Top-5 rank {position} has non-finite g_j[{name!r}]"
            )
        gradient_values.append(value)

    if not gradient_values or any(value != 0.0 for value in gradient_values):
        return

    nonzero_delta: Dict[str, float] = {}
    for name, raw_value in raw_delta.items():
        value_source = (
            raw_value.get("absolute")
            if isinstance(raw_value, Mapping)
            else raw_value
        )
        try:
            value = float(value_source)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"formal Algorithm C Top-5 rank {position} has invalid delta_param[{name!r}]"
            ) from exc
        if not math.isfinite(value):
            raise RuntimeError(
                f"formal Algorithm C Top-5 rank {position} has non-finite delta_param[{name!r}]"
            )
        if value != 0.0:
            nonzero_delta[str(name)] = value
    if nonzero_delta:
        raise RuntimeError(
            "formal Algorithm C Top-5 rank "
            f"{position} has all-zero g_j but nonzero delta_param: {nonzero_delta}"
        )


def physical_batch_counts(rows: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    """Count technical failures per invocation without poisoning valid peers."""

    valid_count = sum(1 for row in rows if physical_valid(row))
    out_of_band_count = sum(
        1 for row in rows if candidate_completion_out_of_band_invalid(row)
    )
    counts = {
        "physical_workload_invocations": len(rows),
        "valid_replays": valid_count,
        "technical_failures": len(rows) - valid_count - out_of_band_count,
    }
    if out_of_band_count:
        counts["out_of_band_invalid_replays"] = out_of_band_count
    return counts


def partition_baseline_controls(
    rows: Sequence[Dict[str, Any]],
    *,
    worker_keys: Sequence[str],
    repetitions_per_worker: int,
    burn_in_repetitions: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Keep a predeclared burn-in separate from two measured controls per worker."""
    if repetitions_per_worker not in {2, 3} or burn_in_repetitions != repetitions_per_worker - 2:
        raise ValueError("baseline requires exactly two final measured repetitions")
    expected = {
        (repetition, key)
        for repetition in range(1, repetitions_per_worker + 1)
        for key in worker_keys
    }
    observed = [
        (int(row.get("baseline_repetition") or 0), str(row.get("worker_key") or ""))
        for row in rows
    ]
    if len(observed) != len(expected) or set(observed) != expected:
        raise RuntimeError("baseline controls are incomplete or duplicated")
    burn_in = [
        row for row in rows
        if int(row["baseline_repetition"]) <= burn_in_repetitions
    ]
    measured = [
        row for row in rows
        if int(row["baseline_repetition"]) > burn_in_repetitions
    ]
    return measured, burn_in


def account_new_physical_rows(
    state: MutableMapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    already_accounted: int,
) -> Tuple[int, Dict[str, int]]:
    """Account only the suffix not yet committed by the current controller.

    Baseline replay consists of two physical waves.  A stop request can arrive
    after wave one was measured but while wave two is being restored.  Keeping
    a controller-local cursor makes every stop branch commit completed rows
    exactly once without treating the in-progress restore as an invocation.
    """

    cursor = max(0, int(already_accounted))
    if cursor > len(rows):
        raise RuntimeError(
            f"physical accounting cursor {cursor} exceeds row count {len(rows)}"
        )
    new_rows = rows[cursor:]
    counts = physical_batch_counts(new_rows)
    for key, value in counts.items():
        state[key] = int(state.get(key) or 0) + int(value)
    return len(rows), counts


def postgres_guc_numeric_readback_matches(
    expected_value: float,
    observed_value: float,
) -> bool:
    """Compare a numeric cost GUC at PostgreSQL ``SHOW`` precision.

    PostgreSQL renders these floating-point settings with six significant
    digits.  That rounded rendering is the strongest available readback, so a
    tighter Python-literal comparison creates false mismatches for valid
    warm-start parameters.
    """
    if not math.isfinite(expected_value) or not math.isfinite(observed_value):
        return False
    rendered_expected = float(format(expected_value, ".6g"))
    return math.isclose(
        observed_value,
        rendered_expected,
        rel_tol=1e-12,
        abs_tol=1e-15,
    )


def baseline_parameter_readback_audit(
    expected: Mapping[str, Any],
    readback: Any,
) -> Dict[str, Any]:
    """Verify ALTER SYSTEM/reload returned every baseline cost parameter."""

    mismatches: Dict[str, Dict[str, Any]] = {}
    if not isinstance(readback, Mapping):
        return {
            "passed": False,
            "reason": "parameter_readback_missing",
            "mismatches": {},
        }
    for name in ACTIVE_PARAMS:
        if name not in expected or name not in readback:
            mismatches[name] = {"reason": "parameter_missing"}
            continue
        try:
            expected_value = float(expected[name])
            observed_value = float(readback[name])
        except (TypeError, ValueError) as exc:
            mismatches[name] = {
                "reason": "non_numeric_parameter_readback",
                "expected": expected.get(name),
                "observed": readback.get(name),
                "error": str(exc),
            }
            continue
        if not postgres_guc_numeric_readback_matches(
            expected_value,
            observed_value,
        ):
            mismatches[name] = {
                "reason": "parameter_readback_mismatch",
                "expected": expected_value,
                "observed": observed_value,
            }
    return {
        "passed": not mismatches,
        "reason": "baseline_parameters_verified" if not mismatches else "baseline_parameter_mismatch",
        "mismatches": mismatches,
        "readback": copy.deepcopy(dict(readback)),
    }


def distinct_validated_behavior_rankings(
    rankings: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Keep one best-ranked formal candidate per full physical-plan cluster.

    PostgreSQL planner cost parameters cannot change executor behaviour when
    the complete physical plan is unchanged.  Feature/resource prediction
    levels therefore remain audit metadata and must not make an identical
    plan eligible for another formal replay.
    """
    selected: List[Dict[str, Any]] = []
    seen_plan_ids = set()
    duplicate_subset_ids: List[str] = []
    for position, row in enumerate(rankings, start=1):
        fingerprint = row.get("three_level_fingerprint")
        fingerprint = fingerprint if isinstance(fingerprint, Mapping) else {}
        fingerprint_id = str(fingerprint.get("id") or "").strip()
        if fingerprint.get("valid") is not True or not fingerprint_id:
            raise RuntimeError(
                "Algorithm C formal candidate rank "
                f"{position} lacks a validated three-level behavior fingerprint"
            )
        plan_id = str(fingerprint.get("plan_level") or "").strip()
        if not plan_id:
            raise RuntimeError(
                "Algorithm C formal candidate rank "
                f"{position} lacks a validated full physical-plan fingerprint"
            )
        if plan_id in seen_plan_ids:
            duplicate_subset_ids.append(str(row.get("subset_id") or ""))
            continue
        seen_plan_ids.add(plan_id)
        selected.append(row)
    return selected, duplicate_subset_ids


def saturated_plan_space_partial_rankings(
    error: IncompleteSubsetOptimizationError,
) -> List[Dict[str, Any]]:
    """Return only trusted partial Top-K rows from plan-space saturation.

    ``generate_rankings`` deliberately raises when it cannot form five
    distinct non-baseline full-plan clusters.  That is a hard failure for an
    ordinary formal Top-5 until the wider-step/boundary fallbacks are
    exhausted, but it is not a technical failure while probing an already
    narrowed interval before deterministic plan coverage.  In that latter
    path, replay the genuine eligible rows (K <= 5) without padding.  Missing
    eligibility evidence or a technical candidate-generation failure remains
    fail-closed.
    """

    if (
        getattr(error, "plan_space_saturation_evidence", False) is not True
        or getattr(error, "technical_candidate_generation_failure", False)
        is True
    ):
        raise error
    rows = getattr(error, "eligible_candidate_rankings", None)
    if not isinstance(rows, list) or any(
        not isinstance(row, Mapping) for row in rows
    ):
        raise error
    return copy.deepcopy([dict(row) for row in rows])


def candidate_target_support_assessment(
    candidate: Mapping[str, Any],
    target: str,
) -> Mapping[str, Any] | None:
    """Return the serialized target support assessment, when enabled."""
    metadata = candidate.get("final_estimation_metadata")
    if not isinstance(metadata, Mapping):
        return None
    calibration = metadata.get("resource_prediction_calibration_audit")
    if not isinstance(calibration, Mapping):
        return None
    target_row = calibration.get(target)
    if not isinstance(target_row, Mapping):
        return None
    support = target_row.get("parameter_support")
    return support if isinstance(support, Mapping) else None


def target_support_abstention_enabled(
    experience_artifact: Mapping[str, Any],
    target: str,
) -> bool:
    """Return whether formal candidates must stay inside predictor support."""
    calibration = experience_artifact.get("resource_prediction_calibration")
    calibration = calibration if isinstance(calibration, Mapping) else {}
    resources = calibration.get("resources")
    resources = resources if isinstance(resources, Mapping) else {}
    target_row = resources.get(target)
    target_row = target_row if isinstance(target_row, Mapping) else {}
    return bool(target_row.get("parameter_support_abstention_enabled"))


def predicted_target_direction_audit(
    candidate: Mapping[str, Any],
    target: str,
) -> Dict[str, Any]:
    """Fail closed on the predicted bottleneck direction for formal replay.

    This gate is deliberately independent of QPS.  Strict and secondary
    Outer acceptance both require the bottleneck resource to decrease, so a
    candidate predicted to move it upward cannot be a formal improvement
    candidate.  Fallback remains QPS-only and therefore does not use this
    directional filter.
    """
    if is_none_target(target):
        return {
            "ready": True,
            "predicted_target_down": None,
            "reason": "no_target_resource_not_applicable",
            "qps_used": False,
        }
    initial = candidate.get("initial_resource")
    predicted = candidate.get("predicted_resource")
    if not isinstance(initial, Mapping) or not isinstance(predicted, Mapping):
        return {
            "ready": False,
            "predicted_target_down": False,
            "reason": "missing_predicted_target_resource_fail_closed",
        }
    try:
        initial_value = float(initial[target])
        predicted_value = float(predicted[target])
    except (KeyError, TypeError, ValueError):
        return {
            "ready": False,
            "predicted_target_down": False,
            "reason": "invalid_predicted_target_resource_fail_closed",
        }
    if (
        not math.isfinite(initial_value)
        or not math.isfinite(predicted_value)
        or initial_value <= 0.0
    ):
        return {
            "ready": False,
            "predicted_target_down": False,
            "reason": "non_finite_predicted_target_resource_fail_closed",
        }
    ratio = predicted_value / initial_value
    return {
        "ready": True,
        "predicted_target_down": predicted_value < initial_value,
        "initial_target_resource": initial_value,
        "predicted_target_resource": predicted_value,
        "predicted_target_ratio": ratio,
        "predicted_target_drop_fraction": 1.0 - ratio,
        "qps_used": False,
        "reason": (
            "predicted_target_down"
            if predicted_value < initial_value
            else "predicted_target_not_down"
        ),
    }


def worker_key(worker: Mapping[str, Any]) -> str:
    return str(worker.get("name") or worker.get("container") or "").strip()


def candidate_completion_out_of_band_invalid(row: Mapping[str, Any]) -> bool:
    """Return True when a peer/support failure invalidated the whole wave."""
    completion = row.get("candidate_cohort_completion_barrier")
    return bool(
        isinstance(completion, Mapping)
        and completion.get("enabled") is True
        and completion.get("passed") is not True
    )


def physical_valid(row: Mapping[str, Any]) -> bool:
    if candidate_completion_out_of_band_invalid(row):
        return False
    if str(row.get("status") or "").lower() != "ok":
        return False
    if row.get("technical_failure") is True:
        return False
    stats = row.get("resource_stats")
    if isinstance(stats, Mapping) and stats.get("resource_monitor_ok") is False:
        return False
    completeness = row.get("workload_completeness")
    if isinstance(completeness, Mapping) and completeness.get("passed") is not True:
        return False
    # Re-evaluate the authoritative benchmark wall marker when accounting
    # rows written by an older live controller.  This prevents a complete but
    # over-budget JOB/TPC-H replay from becoming training or accepted state
    # merely because the older parser did not copy the marker into its
    # workload-completeness errors.
    hybench = row.get("hybench")
    benchmark = hybench.get("benchmark") if isinstance(hybench, Mapping) else None
    if (
        isinstance(benchmark, Mapping)
        and benchmark.get("completed_within_target_wall_time") is False
    ):
        return False
    ssd_qualification = row.get("ssd_qualification")
    if isinstance(ssd_qualification, Mapping) and ssd_qualification.get("passed") is not True:
        return False
    return True


def outer_pruning_config(cfg: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the context-conditioned Outer accept/reject pruning policy."""
    algorithm_cfg = cfg.get("algorithm_c")
    if not isinstance(algorithm_cfg, Mapping):
        return {}
    value = algorithm_cfg.get("outer_accept_reject_pruning")
    return dict(value) if isinstance(value, Mapping) else {}


def candidate_full_plan_fingerprint(candidate: Mapping[str, Any]) -> str:
    """Extract the exact full-workload physical-plan fingerprint or fail open."""
    three_level = candidate.get("three_level_fingerprint")
    if isinstance(three_level, Mapping):
        plan_level = str(three_level.get("plan_level") or "").strip()
        if plan_level:
            return plan_level
    for field in ("workload_plan_fingerprint", "plan_fingerprint"):
        value = str(candidate.get(field) or "").strip()
        if value:
            return value
    return ""


VALID_REPLAY_IDENTITY_CONTRACT = (
    "algorithm_c_valid_exact_param_full_plan_identity_v1"
)
VALID_REPLAY_EVIDENCE_REDECISION_CONTRACT = (
    "algorithm_c_same_campaign_valid_replay_outer_redecision_v1"
)
HISTORICAL_VALID_REPLAY_SEED_CONTRACT = (
    "algorithm_c_cross_campaign_valid_replay_seed_v1"
)


def empty_valid_replay_identity_memory(
    workload_contract_sha256: str,
    target: str,
) -> Dict[str, Any]:
    """Return lane-scoped memory of valid physical candidate identities."""
    return {
        "contract": VALID_REPLAY_IDENTITY_CONTRACT,
        "workload_contract_sha256": workload_contract_sha256,
        "target": target,
        "technical_failures_excluded": True,
        "identities": {},
    }


def valid_replay_identity_key(
    candidate: Mapping[str, Any],
    *,
    workload_contract_sha256: str,
    target: str,
) -> str:
    """Key an exact parameter vector together with its complete plan tree."""
    params = candidate.get("optimized_params")
    plan_fingerprint = candidate_full_plan_fingerprint(candidate)
    if (
        not isinstance(params, Mapping)
        or not all(name in params for name in ACTIVE_PARAMS)
        or not plan_fingerprint
    ):
        return ""
    return canonical_sha256(
        {
            "contract": VALID_REPLAY_IDENTITY_CONTRACT,
            "workload_contract_sha256": workload_contract_sha256,
            "target": target,
            "parameter_signature": exact_param_signature(params),
            "full_plan_fingerprint": plan_fingerprint,
        }
    )


def explicit_user_authorized_no_mutex_from_env() -> bool:
    """Return whether the operator explicitly selected shared-SSD no-mutex mode."""
    coordination_env = os.environ.get(
        "BONECT_SHARED_SSD_COORDINATION_ENABLED"
    )
    workload_lock_mode = str(
        os.environ.get("BONECT_SHARED_SSD_WORKLOAD_LOCK_MODE") or "shared"
    ).strip().lower()
    coordination_explicitly_disabled = str(coordination_env).strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }
    return bool(
        coordination_env is not None
        and coordination_explicitly_disabled
        and workload_lock_mode == "none"
    )


def stability_required_resource_names(target: str) -> Tuple[str, ...]:
    """Return resources whose temporal repeatability is semantically required."""
    if explicit_user_authorized_no_mutex_from_env() and target != "io":
        return ("cpu", "memory")
    return ("cpu", "memory", "io")


def assess_stability_attempts(
    attempts: Sequence[Mapping[str, Any]],
    *,
    required_consecutive_rounds: int = 2,
    qps_relative_gap_limit: float = 0.15,
    resource_relative_gap_limit: float = 0.20,
    required_resource_names: Sequence[str] = ("cpu", "memory", "io"),
) -> Dict[str, Any]:
    """Find a reproducible consecutive temporal window.

    Each attempt contains the same isolated worker cohort.  Cross-worker dispersion in a
    single attempt is diagnostic only: workers may have a persistent execution
    domain offset that same-worker baseline normalization cannot remove fully.
    Repeatability is therefore proved by comparing each worker with itself in
    the next attempt, while also requiring the worker-cohort aggregate means to be
    stable.  This rejects temporal drift without treating a fixed worker offset
    as non-repeatability.
    """
    if required_consecutive_rounds != 2:
        raise ValueError("stability assessment currently requires exactly two rounds")
    required_resources = tuple(dict.fromkeys(str(name) for name in required_resource_names))
    if not required_resources or any(
        name not in {"cpu", "memory", "io"} for name in required_resources
    ):
        raise ValueError(f"invalid stability resource scope: {required_resources}")
    selected_indices: Optional[Tuple[int, int]] = None
    selected_qps_gap: Optional[float] = None
    selected_resource_gaps: Dict[str, Optional[float]] = {
        name: None for name in ("cpu", "memory", "io")
    }
    selected_same_worker_gaps: Dict[str, Dict[str, float]] = {}
    selected_temporal_diagnostics_passed = False

    def relative_gap(first_value: float, second_value: float) -> float:
        return abs(first_value - second_value) / max(
            (first_value + second_value) / 2.0,
            1e-12,
        )

    for index in range(1, len(attempts)):
        first = attempts[index - 1]
        second = attempts[index]
        if (
            first.get("technical_failure") is True
            or second.get("technical_failure") is True
            or first.get("passed") is not True
            or second.get("passed") is not True
        ):
            continue
        first_confirmations = first.get("confirmations")
        second_confirmations = second.get("confirmations")
        if not isinstance(first_confirmations, Sequence) or not isinstance(
            second_confirmations,
            Sequence,
        ):
            continue
        first_by_worker = {
            str(row.get("worker_key") or ""): row
            for row in first_confirmations
            if isinstance(row, Mapping) and row.get("worker_key")
        }
        second_by_worker = {
            str(row.get("worker_key") or ""): row
            for row in second_confirmations
            if isinstance(row, Mapping) and row.get("worker_key")
        }
        if (
            len(first_by_worker) < 2
            or set(first_by_worker) != set(second_by_worker)
        ):
            continue
        first_qps = float(first["qps_mean"])
        second_qps = float(second["qps_mean"])
        qps_gap = relative_gap(first_qps, second_qps)
        resource_gaps: Dict[str, Optional[float]] = {}
        for name in ("cpu", "memory", "io"):
            first_value = float(first["resource_means"][name])
            second_value = float(second["resource_means"][name])
            resource_gaps[name] = relative_gap(first_value, second_value)
        same_worker_gaps: Dict[str, Dict[str, float]] = {}
        same_worker_resource_passed = True
        same_worker_qps_passed = True
        for worker_key in sorted(first_by_worker):
            first_row = first_by_worker[worker_key]
            second_row = second_by_worker[worker_key]
            worker_qps_gap = relative_gap(
                float(first_row["db_qps"]),
                float(second_row["db_qps"]),
            )
            worker_resource_gaps = {
                name: relative_gap(
                    float(first_row["resources"][name]),
                    float(second_row["resources"][name]),
                )
                for name in ("cpu", "memory", "io")
            }
            same_worker_gaps[worker_key] = {
                "qps": worker_qps_gap,
                **worker_resource_gaps,
            }
            same_worker_qps_passed = bool(
                same_worker_qps_passed
                and worker_qps_gap <= qps_relative_gap_limit
            )
            same_worker_resource_passed = bool(
                same_worker_resource_passed
                and all(
                    worker_resource_gaps[name] <= resource_relative_gap_limit
                    for name in required_resources
                )
            )
        temporal_diagnostics_passed = bool(
            same_worker_qps_passed
            and qps_gap <= qps_relative_gap_limit
            and same_worker_resource_passed
            and all(
                resource_gaps[name] is not None
                and resource_gaps[name] <= resource_relative_gap_limit
                for name in required_resources
            )
        )
        # Formal stability is outcome reproducibility: both consecutive full
        # replays must independently pass Outer acceptance and within-wave
        # dispersion.  Temporal metric gaps remain explicit diagnostics; they
        # cannot overturn two repeated accepted outcomes or relax Outer.
        temporal_resources_passed = bool(
            same_worker_resource_passed
            and all(
                resource_gaps[name] is not None
                and resource_gaps[name] <= resource_relative_gap_limit
                for name in required_resources
            )
        )
        if (
            first.get("passed") is True
            and second.get("passed") is True
            and temporal_resources_passed
        ):
            selected_indices = (index - 1, index)
            selected_qps_gap = qps_gap
            selected_resource_gaps = resource_gaps
            selected_same_worker_gaps = same_worker_gaps
            selected_temporal_diagnostics_passed = temporal_diagnostics_passed
            break
    selected_attempts: List[int] = []
    if selected_indices is not None:
        selected_attempts = [
            int(attempts[position].get("attempt") or position + 1)
            for position in selected_indices
        ]
    return {
        "passed": selected_indices is not None,
        "selected_attempts": selected_attempts,
        "consecutive_qps_relative_gap": selected_qps_gap,
        "consecutive_resource_relative_gaps": selected_resource_gaps,
        "same_worker_consecutive_relative_gaps": selected_same_worker_gaps,
        "temporal_metric_diagnostics_passed": (
            selected_temporal_diagnostics_passed
            if selected_indices is not None
            else False
        ),
        "temporal_metric_gaps_are_diagnostic_only": False,
        "qps_temporal_gap_is_diagnostic_only": True,
        "required_resource_temporal_gaps_are_hard": True,
        "required_resource_names": list(required_resources),
        "diagnostic_only_resource_names": [
            name for name in ("cpu", "memory", "io") if name not in required_resources
        ],
    }


def canonicalize_stability_attempt_outcomes(
    attempts: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Re-evaluate old stability rows using the minimum valid Outer tier."""
    canonical: List[Dict[str, Any]] = []
    for raw in attempts:
        row = copy.deepcopy(dict(raw))
        decisions = row.get("confirmation_decisions")
        decisions = decisions if isinstance(decisions, Sequence) else []
        confirmations = row.get("confirmations")
        confirmations = (
            confirmations if isinstance(confirmations, Sequence) else []
        )
        outer_accepted = bool(
            len(confirmations) >= 2
            and len(decisions) == len(confirmations)
            and all(
                isinstance(decision, Mapping)
                and decision.get("accepted") is True
                and ACCEPTANCE_TIER_RANK.get(
                    str(decision.get("acceptance_tier") or ""), 0
                ) >= ACCEPTANCE_TIER_RANK["fallback"]
                for decision in decisions
            )
        )
        qps_gap = float(row.get("qps_relative_gap_between_workers") or 0.0)
        resource_gaps = row.get("resource_relative_gaps_between_workers")
        resource_gaps = resource_gaps if isinstance(resource_gaps, Mapping) else {}
        dispersion_passed = bool(
            qps_gap <= 0.15
            and all(
                float(resource_gaps.get(name) or 0.0) <= 0.20
                for name in ("cpu", "memory", "io")
            )
        )
        row["original_required_acceptance_tier"] = row.get(
            "required_acceptance_tier"
        )
        row["required_acceptance_tier"] = "fallback"
        row["semantic_tier_preserved"] = outer_accepted
        row["passed"] = outer_accepted
        row["within_attempt_worker_dispersion_passed"] = dispersion_passed
        row["within_attempt_worker_dispersion_is_diagnostic_only"] = True
        row["stability_outcome_redecision_contract"] = (
            "minimum_valid_outer_tier_fallback_cross_worker_diagnostic_v2"
        )
        canonical.append(row)
    return canonical


def algorithm_c_cohort_config_from_env() -> Dict[str, Any]:
    """Return the optional cross-lane baseline synchronization contract."""
    cohort_id = str(
        os.environ.get("BONECT_ALGORITHM_C_BASELINE_COHORT_ID") or ""
    ).strip()
    raw_size = os.environ.get("BONECT_ALGORITHM_C_BASELINE_COHORT_SIZE")
    raw_timeout = os.environ.get("BONECT_ALGORITHM_C_BASELINE_COHORT_TIMEOUT_S")
    try:
        cohort_size = int(raw_size) if raw_size is not None else 1
        timeout_s = float(raw_timeout) if raw_timeout is not None else 900.0
    except (TypeError, ValueError) as exc:
        raise RuntimeError("invalid Algorithm C baseline cohort environment") from exc
    if cohort_size <= 0 or timeout_s <= 0.0:
        raise RuntimeError("Algorithm C baseline cohort size/timeout must be positive")
    if cohort_size > 1 and not cohort_id:
        raise RuntimeError("multi-lane Algorithm C baseline cohort requires a cohort id")
    return {
        "enabled": cohort_size > 1,
        "cohort_id": cohort_id,
        "cohort_id_sha256": canonical_sha256({"cohort_id": cohort_id}),
        "cohort_size": cohort_size,
        "timeout_s": timeout_s,
    }


def algorithm_c_candidate_cohort_config_from_env() -> Dict[str, Any]:
    """Return the optional cross-lane formal-wave synchronization contract."""
    cohort_id = str(
        os.environ.get("BONECT_ALGORITHM_C_CANDIDATE_COHORT_ID") or ""
    ).strip()
    raw_size = os.environ.get("BONECT_ALGORITHM_C_CANDIDATE_COHORT_SIZE")
    raw_timeout = os.environ.get("BONECT_ALGORITHM_C_CANDIDATE_COHORT_TIMEOUT_S")
    try:
        cohort_size = int(raw_size) if raw_size is not None else 1
        timeout_s = float(raw_timeout) if raw_timeout is not None else 1800.0
    except (TypeError, ValueError) as exc:
        raise RuntimeError("invalid Algorithm C candidate cohort environment") from exc
    if cohort_size <= 0 or timeout_s <= 0.0:
        raise RuntimeError("Algorithm C candidate cohort size/timeout must be positive")
    if cohort_size > 1 and not cohort_id:
        raise RuntimeError("multi-lane Algorithm C candidate cohort requires a cohort id")
    return {
        "enabled": cohort_size > 1,
        "cohort_id": cohort_id,
        "cohort_id_sha256": canonical_sha256({"cohort_id": cohort_id}),
        "cohort_size": cohort_size,
        "timeout_s": timeout_s,
    }


def next_unfinished_algorithm_c_candidate_wave_sequence(
    after_sequence: int,
) -> Dict[str, Any]:
    """Skip immutable historical cohort waves after a controller restart."""
    cfg = algorithm_c_candidate_cohort_config_from_env()
    sequence = max(0, int(after_sequence)) + 1
    skipped: List[int] = []
    if cfg["enabled"] is not True:
        return {"wave_sequence": sequence, "skipped_completed_waves": skipped}
    while True:
        stage_key = canonical_sha256(
            {
                "cohort_id": cfg["cohort_id"],
                "wave_sequence": sequence,
            }
        )
        stage_dir = (
            Path("/tmp")
            / f"resource_adaptive_algorithm_c_candidate_cohort_{stage_key}"
        )
        done_rows, _invalid_files = _validated_cohort_rows(
            stage_dir,
            suffix=".done",
            participant_identity_fields={"wave_sequence": sequence},
            expected_fields={"wave_sequence": sequence},
            require_wave_valid=True,
        )
        if len(done_rows) < int(cfg["cohort_size"]):
            return {
                "wave_sequence": sequence,
                "skipped_completed_waves": skipped,
            }
        skipped.append(sequence)
        sequence += 1


def unfinished_algorithm_c_round_resume_index(
    state: Mapping[str, Any],
    *,
    allow_legacy_stopped_clean: bool = False,
) -> Optional[int]:
    """Return the in-progress outer round that a restarted lane must resume."""
    phase = str(state.get("phase") or "")
    if phase == "stopped_by_signal_clean":
        interrupted_phase = str(state.get("signal_stop_interrupted_phase") or "")
        if interrupted_phase:
            phase = interrupted_phase
        elif (
            allow_legacy_stopped_clean
            and isinstance(state.get("signal_stop_clean_receipt"), Mapping)
            and state.get("signal_stop_clean_receipt", {}).get("passed") is True
            and not state.get("inflight_physical_wave")
        ):
            phase = "offline_algorithm_c_ranking"
    try:
        round_index = int(state.get("round_index") or 0)
    except (TypeError, ValueError):
        return None
    if round_index <= 0 or phase not in UNFINISHED_OUTER_ROUND_PHASES:
        return None
    return round_index


def workload_contract_execution_domain_audit(
    algorithm_cfg: Mapping[str, Any],
    *,
    expected_no_mutex: bool,
    cohort: Mapping[str, Any],
    candidate_cohort: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Validate the immutable workload contract against runtime SSD policy."""
    workload_contract = algorithm_cfg.get("workload_contract")
    workload_contract = (
        workload_contract if isinstance(workload_contract, Mapping) else {}
    )
    shared = workload_contract.get("shared_ssd_coordination")
    shared = shared if isinstance(shared, Mapping) else {}
    baseline_cohort = workload_contract.get("baseline_cohort")
    baseline_cohort = (
        baseline_cohort if isinstance(baseline_cohort, Mapping) else {}
    )
    candidate_cohort = candidate_cohort if isinstance(candidate_cohort, Mapping) else {}
    workload_candidate_cohort = workload_contract.get("candidate_cohort")
    workload_candidate_cohort = (
        workload_candidate_cohort
        if isinstance(workload_candidate_cohort, Mapping)
        else {}
    )
    errors: List[str] = []
    if expected_no_mutex:
        if shared.get("contract") != "pg_algorithm_c_user_authorized_no_mutex_v1":
            errors.append("workload_contract_no_mutex_contract_mismatch")
        if shared.get("enabled") is not False:
            errors.append("workload_contract_no_mutex_enabled_not_false")
        if shared.get("physical_workload_mode") != "none":
            errors.append("workload_contract_physical_workload_mode_not_none")
        if shared.get("database_restore_mode") != "none":
            errors.append("workload_contract_database_restore_mode_not_none")
        if shared.get("covers_cache_eviction_and_physical_workload") is not False:
            errors.append("workload_contract_no_mutex_coverage_not_false")
        if baseline_cohort.get("contract") != (
            "algorithm_c_four_lane_no_mutex_baseline_cohort_v1"
        ):
            errors.append("workload_contract_baseline_cohort_missing")
        if int(baseline_cohort.get("lane_count") or 0) != int(
            cohort.get("cohort_size") or 0
        ):
            errors.append("workload_contract_baseline_cohort_size_mismatch")
        if candidate_cohort.get("enabled") is True:
            if workload_candidate_cohort.get("contract") != (
                "algorithm_c_four_lane_no_mutex_candidate_wave_cohort_v1"
            ):
                errors.append("workload_contract_candidate_cohort_missing")
            if int(workload_candidate_cohort.get("lane_count") or 0) != int(
                candidate_cohort.get("cohort_size") or 0
            ):
                errors.append("workload_contract_candidate_cohort_size_mismatch")
    else:
        if shared.get("physical_workload_mode") == "none":
            errors.append("coordinated_runtime_has_no_mutex_workload_contract")
    result = {
        "contract": "algorithm_c_workload_runtime_execution_domain_v1",
        "passed": not errors,
        "expected_no_mutex": expected_no_mutex,
        "runtime_cohort_size": int(cohort.get("cohort_size") or 1),
        "runtime_candidate_cohort_size": int(
            candidate_cohort.get("cohort_size") or 1
        ),
        "workload_shared_ssd_coordination": copy.deepcopy(dict(shared)),
        "workload_baseline_cohort": copy.deepcopy(dict(baseline_cohort)),
        "workload_candidate_cohort": copy.deepcopy(dict(workload_candidate_cohort)),
        "errors": errors,
    }
    result["audit_sha256"] = canonical_sha256(result)
    return result


def native_xp_runtime_contract_audit(
    cfg: Mapping[str, Any],
    *,
    target: str,
) -> Dict[str, Any]:
    """Fail closed when a no-bottleneck XP contract differs from runtime props."""
    algorithm_cfg = cfg.get("algorithm_c")
    algorithm_cfg = algorithm_cfg if isinstance(algorithm_cfg, Mapping) else {}
    workload_contract = algorithm_cfg.get("workload_contract")
    workload_contract = (
        workload_contract if isinstance(workload_contract, Mapping) else {}
    )
    benchmark = str(workload_contract.get("benchmark") or "").strip().lower()
    required = target == "none" and benchmark == "hybench_xp"
    native_xp = workload_contract.get("native_xp")
    native_xp = native_xp if isinstance(native_xp, Mapping) else {}
    validation = (
        ((cfg.get("binary_plan_tuning") or {}).get("validation") or {})
        if isinstance(cfg.get("binary_plan_tuning"), Mapping)
        else {}
    )
    runtime = validation.get("hybench_xp")
    runtime = runtime if isinstance(runtime, Mapping) else {}
    errors: List[str] = []
    compared: Dict[str, Dict[str, Any]] = {}
    if required:
        expected_values = {
            "xtpclient": native_xp.get("xtpclient"),
            "xapclient": native_xp.get("xapclient"),
            "xp_run_mins": native_xp.get("xp_run_mins"),
            **dict(native_xp.get("at_percentages") or {}),
            "native_jar_sha256": native_xp.get("native_jar_sha256"),
            "statement_file_sha256": native_xp.get("statement_file_sha256"),
        }
        keys = (
            "xtpclient",
            "xapclient",
            "xp_run_mins",
            "at1_percent",
            "at2_percent",
            "at3_percent",
            "at4_percent",
            "at5_percent",
            "at6_percent",
            "native_jar_sha256",
            "statement_file_sha256",
        )
        for key in keys:
            expected = expected_values.get(key)
            actual = runtime.get(key)
            compared[key] = {"contract": expected, "runtime": actual}
            if expected is None:
                errors.append(f"native_xp_contract_missing_{key}")
            elif actual != expected:
                errors.append(f"native_xp_runtime_mismatch_{key}")
    result = {
        "contract": "algorithm_bc_native_xp_runtime_identity_v1",
        "required": required,
        "passed": not errors,
        "target": target,
        "benchmark": benchmark,
        "compared": compared,
        "errors": errors,
    }
    result["audit_sha256"] = canonical_sha256(result)
    return result


def wait_for_algorithm_c_baseline_cohort(
    *,
    scene: str,
    repetition: int,
    stop_requested: Callable[[], bool],
) -> Dict[str, Any]:
    """Synchronize each baseline repetition across independently launched lanes."""
    cfg = algorithm_c_cohort_config_from_env()
    if cfg["enabled"] is not True:
        return {**cfg, "passed": True, "wait_s": 0.0, "ready_count": 1}
    stage_key = canonical_sha256(
        {
            "cohort_id": cfg["cohort_id"],
            "repetition": int(repetition),
        }
    )
    barrier_dir = Path("/tmp") / f"resource_adaptive_algorithm_c_cohort_{stage_key}"
    barrier_dir.mkdir(parents=True, exist_ok=True)
    algorithm_c_tmp_space_preflight(path=barrier_dir)
    participant_key = canonical_sha256(
        {"scene": str(scene), "repetition": int(repetition)}
    )
    ready_path = barrier_dir / f"{participant_key}.ready"
    write_json(
        ready_path,
        {
            "scene": str(scene),
            "repetition": int(repetition),
            "pid": os.getpid(),
            "time": time.time(),
        },
    )
    started = time.monotonic()
    ready_count = 0
    invalid_files: List[str] = []
    while True:
        algorithm_c_tmp_space_preflight(path=barrier_dir)
        ready_rows, invalid_files = _validated_cohort_rows(
            barrier_dir,
            suffix=".ready",
            participant_identity_fields={"repetition": int(repetition)},
            expected_fields={"repetition": int(repetition)},
        )
        ready_count = len(ready_rows)
        if ready_count >= int(cfg["cohort_size"]):
            break
        if stop_requested():
            raise RuntimeError("Algorithm C baseline cohort wait cancelled")
        wait_s = time.monotonic() - started
        if wait_s >= float(cfg["timeout_s"]):
            raise RuntimeError(
                "Algorithm C baseline cohort timeout: "
                f"ready={ready_count}/{cfg['cohort_size']} "
                f"invalid={invalid_files}"
            )
        time.sleep(0.2)
    return {
        **cfg,
        "passed": True,
        "wait_s": time.monotonic() - started,
        "ready_count": ready_count,
        "invalid_files": invalid_files,
        "barrier_stage_sha256": stage_key,
    }


def wait_for_algorithm_c_baseline_cohort_completion(
    *,
    scene: str,
    repetition: int,
    wave_valid: bool,
    stop_requested: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """Prevent a faster lane's next restore from overlapping a slow replay."""
    cfg = algorithm_c_cohort_config_from_env()
    if cfg["enabled"] is not True:
        return {
            **cfg,
            "completion_contract": (
                "algorithm_c_four_lane_baseline_completion_cohort_v1"
            ),
            "passed": bool(wave_valid),
            "wait_s": 0.0,
            "completed_count": 1,
            "all_wave_valid": bool(wave_valid),
        }
    stage_key = canonical_sha256(
        {
            "cohort_id": cfg["cohort_id"],
            "repetition": int(repetition),
        }
    )
    barrier_dir = Path("/tmp") / f"resource_adaptive_algorithm_c_cohort_{stage_key}"
    barrier_dir.mkdir(parents=True, exist_ok=True)
    algorithm_c_tmp_space_preflight(path=barrier_dir)
    participant_key = canonical_sha256(
        {"scene": str(scene), "repetition": int(repetition)}
    )
    done_path = barrier_dir / f"{participant_key}.done"
    write_json(
        done_path,
        {
            "scene": str(scene),
            "repetition": int(repetition),
            "pid": os.getpid(),
            "time": time.time(),
            "wave_valid": bool(wave_valid),
        },
    )
    started = time.monotonic()
    completed_count = 0
    done_rows: List[Dict[str, Any]] = []
    invalid_files: List[str] = []
    while True:
        algorithm_c_tmp_space_preflight(path=barrier_dir)
        done_rows, invalid_files = _validated_cohort_rows(
            barrier_dir,
            suffix=".done",
            participant_identity_fields={"repetition": int(repetition)},
            expected_fields={"repetition": int(repetition)},
            require_wave_valid=True,
        )
        completed_count = len(done_rows)
        if completed_count >= int(cfg["cohort_size"]):
            break
        wait_s = time.monotonic() - started
        if wait_s >= float(cfg["timeout_s"]):
            raise RuntimeError(
                "Algorithm C baseline completion cohort timeout: "
                f"completed={completed_count}/{cfg['cohort_size']} "
                f"invalid={invalid_files}"
            )
        if stop_requested is not None and stop_requested():
            return {
                **cfg,
                "completion_contract": (
                    "algorithm_c_four_lane_baseline_completion_cohort_v1"
                ),
                "passed": False,
                "cancelled": True,
                "wait_s": time.monotonic() - started,
                "completed_count": completed_count,
                "invalid_files": invalid_files,
                "barrier_stage_sha256": stage_key,
                "repetition": int(repetition),
                "all_wave_valid": False,
            }
        time.sleep(0.2)
    all_wave_valid = all(row.get("wave_valid") is True for row in done_rows)
    return {
        **cfg,
        "completion_contract": (
            "algorithm_c_four_lane_baseline_completion_cohort_v1"
        ),
        "passed": all_wave_valid,
        "wait_s": time.monotonic() - started,
        "completed_count": completed_count,
        "invalid_files": invalid_files,
        "barrier_stage_sha256": stage_key,
        "repetition": int(repetition),
        "all_wave_valid": all_wave_valid,
    }


def wait_for_algorithm_c_candidate_cohort(
    *,
    scene: str,
    wave_sequence: int,
    round_index: int,
    batch_kind: str,
    wave_start: int,
    stop_requested: Callable[[], bool],
) -> Dict[str, Any]:
    """Synchronize each formal physical wave across independently launched lanes."""
    cfg = algorithm_c_candidate_cohort_config_from_env()
    if cfg["enabled"] is not True:
        return {**cfg, "passed": True, "wait_s": 0.0, "ready_count": 1}
    stage_key = canonical_sha256(
        {
            "cohort_id": cfg["cohort_id"],
            "wave_sequence": int(wave_sequence),
        }
    )
    barrier_dir = (
        Path("/tmp")
        / f"resource_adaptive_algorithm_c_candidate_cohort_{stage_key}"
    )
    barrier_dir.mkdir(parents=True, exist_ok=True)
    algorithm_c_tmp_space_preflight(path=barrier_dir)
    participant_key = canonical_sha256(
        {"scene": str(scene), "wave_sequence": int(wave_sequence)}
    )
    ready_path = barrier_dir / f"{participant_key}.ready"
    write_json(
        ready_path,
        {
            "scene": str(scene),
            "wave_sequence": int(wave_sequence),
            "round_index": int(round_index),
            "batch_kind": str(batch_kind),
            "wave_start": int(wave_start),
            "pid": os.getpid(),
            "time": time.time(),
        },
    )
    started = time.monotonic()
    ready_count = 0
    invalid_files: List[str] = []
    while True:
        algorithm_c_tmp_space_preflight(path=barrier_dir)
        ready_rows, invalid_files = _validated_cohort_rows(
            barrier_dir,
            suffix=".ready",
            participant_identity_fields={"wave_sequence": int(wave_sequence)},
            # The candidate cohort is a global physical-wave barrier.  Lanes
            # advance their semantic outer rounds independently (for example,
            # one lane may accept cached evidence while another needs a
            # current-interval expansion), so local round/batch/wave offsets
            # are audit context rather than barrier identity.  Requiring them
            # to match deadlocks the first mixed-round cohort.
            expected_fields={"wave_sequence": int(wave_sequence)},
            require_live_pid=True,
        )
        ready_count = len(ready_rows)
        if ready_count >= int(cfg["cohort_size"]):
            break
        if stop_requested():
            raise RuntimeError("Algorithm C candidate cohort wait cancelled")
        wait_s = time.monotonic() - started
        if wait_s >= float(cfg["timeout_s"]):
            raise RuntimeError(
                "Algorithm C candidate cohort timeout: "
                f"ready={ready_count}/{cfg['cohort_size']} "
                f"wave_sequence={wave_sequence} invalid={invalid_files}"
            )
        time.sleep(0.2)
    participant_contexts = sorted(
        [
            {
                "scene": str(row.get("scene") or ""),
                "round_index": int(row.get("round_index") or 0),
                "batch_kind": str(row.get("batch_kind") or ""),
                "wave_start": int(row.get("wave_start") or 0),
            }
            for row in ready_rows
        ],
        key=lambda row: row["scene"],
    )
    return {
        **cfg,
        "passed": True,
        "wait_s": time.monotonic() - started,
        "ready_count": ready_count,
        "invalid_files": invalid_files,
        "barrier_stage_sha256": stage_key,
        "wave_sequence": int(wave_sequence),
        "round_index": int(round_index),
        "batch_kind": str(batch_kind),
        "wave_start": int(wave_start),
        "alignment_contract": (
            "algorithm_c_global_wave_sequence_mixed_local_context_v2"
        ),
        "local_context_is_not_barrier_identity": True,
        "participant_contexts": participant_contexts,
        "participant_round_indices": sorted(
            {row["round_index"] for row in participant_contexts}
        ),
    }


def wait_for_algorithm_c_candidate_cohort_completion(
    *,
    scene: str,
    wave_sequence: int,
    round_index: int,
    batch_kind: str,
    wave_start: int,
    wave_valid: bool,
    stop_requested: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """Hold every lane at wave end until all physical replays have finished."""
    cfg = algorithm_c_candidate_cohort_config_from_env()
    if cfg["enabled"] is not True:
        return {
            **cfg,
            "completion_contract": (
                "algorithm_c_four_lane_candidate_completion_cohort_v1"
            ),
            "passed": bool(wave_valid),
            "wait_s": 0.0,
            "completed_count": 1,
            "wave_sequence": int(wave_sequence),
            "all_wave_valid": bool(wave_valid),
        }
    stage_key = canonical_sha256(
        {
            "cohort_id": cfg["cohort_id"],
            "wave_sequence": int(wave_sequence),
        }
    )
    barrier_dir = (
        Path("/tmp")
        / f"resource_adaptive_algorithm_c_candidate_cohort_{stage_key}"
    )
    barrier_dir.mkdir(parents=True, exist_ok=True)
    algorithm_c_tmp_space_preflight(path=barrier_dir)
    participant_key = canonical_sha256(
        {"scene": str(scene), "wave_sequence": int(wave_sequence)}
    )
    done_path = barrier_dir / f"{participant_key}.done"
    write_json(
        done_path,
        {
            "scene": str(scene),
            "wave_sequence": int(wave_sequence),
            "round_index": int(round_index),
            "batch_kind": str(batch_kind),
            "wave_start": int(wave_start),
            "pid": os.getpid(),
            "time": time.time(),
            "wave_valid": bool(wave_valid),
        },
    )
    started = time.monotonic()
    completed_count = 0
    done_rows: List[Dict[str, Any]] = []
    invalid_files: List[str] = []
    while True:
        algorithm_c_tmp_space_preflight(path=barrier_dir)
        done_rows, invalid_files = _validated_cohort_rows(
            barrier_dir,
            suffix=".done",
            participant_identity_fields={"wave_sequence": int(wave_sequence)},
            # Completion must use the same global wave identity as the ready
            # barrier.  Each lane's local semantic round remains independent.
            expected_fields={"wave_sequence": int(wave_sequence)},
            require_wave_valid=True,
            require_live_pid=True,
        )
        completed_count = len(done_rows)
        if completed_count >= int(cfg["cohort_size"]):
            break
        wait_s = time.monotonic() - started
        if wait_s >= float(cfg["timeout_s"]):
            raise RuntimeError(
                "Algorithm C candidate completion cohort timeout: "
                f"completed={completed_count}/{cfg['cohort_size']} "
                f"wave_sequence={wave_sequence} invalid={invalid_files}"
            )
        if stop_requested is not None and stop_requested():
            return {
                **cfg,
                "completion_contract": (
                    "algorithm_c_four_lane_candidate_completion_cohort_v1"
                ),
                "passed": False,
                "cancelled": True,
                "wait_s": time.monotonic() - started,
                "completed_count": completed_count,
                "invalid_files": invalid_files,
                "barrier_stage_sha256": stage_key,
                "wave_sequence": int(wave_sequence),
                "round_index": int(round_index),
                "batch_kind": str(batch_kind),
                "wave_start": int(wave_start),
                "all_wave_valid": False,
            }
        time.sleep(0.2)
    all_wave_valid = all(row.get("wave_valid") is True for row in done_rows)
    participant_contexts = sorted(
        [
            {
                "scene": str(row.get("scene") or ""),
                "round_index": int(row.get("round_index") or 0),
                "batch_kind": str(row.get("batch_kind") or ""),
                "wave_start": int(row.get("wave_start") or 0),
                "wave_valid": row.get("wave_valid") is True,
            }
            for row in done_rows
        ],
        key=lambda row: row["scene"],
    )
    return {
        **cfg,
        "completion_contract": (
            "algorithm_c_four_lane_candidate_completion_cohort_v1"
        ),
        "passed": all_wave_valid,
        "wait_s": time.monotonic() - started,
        "completed_count": completed_count,
        "invalid_files": invalid_files,
        "barrier_stage_sha256": stage_key,
        "wave_sequence": int(wave_sequence),
        "round_index": int(round_index),
        "batch_kind": str(batch_kind),
        "wave_start": int(wave_start),
        "all_wave_valid": all_wave_valid,
        "alignment_contract": (
            "algorithm_c_global_wave_sequence_mixed_local_context_v2"
        ),
        "local_context_is_not_barrier_identity": True,
        "participant_contexts": participant_contexts,
        "participant_round_indices": sorted(
            {row["round_index"] for row in participant_contexts}
        ),
    }


def baseline_execution_domain_audit(
    rows: Sequence[Mapping[str, Any]],
    canonical: Mapping[str, Any],
    *,
    expected_no_mutex: bool,
    cohort: Mapping[str, Any],
    allow_single_lane_no_mutex: bool = False,
) -> Dict[str, Any]:
    """Prove that baseline controls use the formal replay's SSD domain."""
    errors: List[str] = []
    observations: List[Dict[str, Any]] = []
    for label, row in [
        ("canonical", canonical),
        *[
            (str(item.get("label") or f"baseline_{index}"), item)
            for index, item in enumerate(rows, start=1)
        ],
    ]:
        qualification = row.get("ssd_qualification")
        qualification = qualification if isinstance(qualification, Mapping) else {}
        thresholds = qualification.get("thresholds")
        thresholds = thresholds if isinstance(thresholds, Mapping) else {}
        observed_no_mutex = thresholds.get("explicit_user_authorized_no_mutex")
        global_enforced = thresholds.get("global_ssd_stable_window_enforced")
        global_informational = thresholds.get(
            "global_ssd_utilization_is_informational"
        )
        observations.append(
            {
                "label": label,
                "explicit_user_authorized_no_mutex": observed_no_mutex,
                "global_ssd_stable_window_enforced": global_enforced,
                "global_ssd_utilization_is_informational": global_informational,
                "qualification_id": qualification.get("qualification_id"),
            }
        )
        if observed_no_mutex is not expected_no_mutex:
            errors.append(f"{label}:no_mutex_mode_mismatch")
        if global_enforced is not (not expected_no_mutex):
            errors.append(f"{label}:global_ssd_enforcement_mismatch")
        if global_informational is not expected_no_mutex:
            errors.append(f"{label}:global_ssd_information_mode_mismatch")
    cohort_size = int(cohort.get("cohort_size") or 1)
    multi_lane_cohort_ready = bool(
        cohort.get("enabled") is True and cohort_size >= 2
    )
    single_lane_domain_ready = bool(
        allow_single_lane_no_mutex and cohort_size == 1
    )
    if expected_no_mutex and not (
        multi_lane_cohort_ready or single_lane_domain_ready
    ):
        errors.append("no_mutex_baseline_requires_multi_lane_cohort")
    completion_observations: List[Dict[str, Any]] = []
    if cohort.get("enabled") is True:
        for index, row in enumerate(rows, start=1):
            receipt = row.get("baseline_cohort_completion_barrier")
            receipt = receipt if isinstance(receipt, Mapping) else {}
            observation = {
                "label": str(row.get("label") or f"baseline_{index}"),
                "passed": receipt.get("passed") is True,
                "completed_count": int(receipt.get("completed_count") or 0),
                "repetition": receipt.get("repetition"),
                "barrier_stage_sha256": str(
                    receipt.get("barrier_stage_sha256") or ""
                ),
            }
            completion_observations.append(observation)
            if (
                observation["passed"] is not True
                or observation["completed_count"] < cohort_size
            ):
                errors.append(
                    f"{observation['label']}:baseline_completion_cohort_missing"
                )
    result = {
        "contract": "algorithm_c_baseline_execution_domain_v2",
        "passed": not errors,
        "expected_no_mutex": expected_no_mutex,
        "cohort_enabled": cohort.get("enabled") is True,
        "cohort_id_sha256": str(cohort.get("cohort_id_sha256") or ""),
        "cohort_size": cohort_size,
        "single_lane_no_mutex_authorized": single_lane_domain_ready,
        "observations": observations,
        "completion_observations": completion_observations,
        "next_restore_blocked_until_all_lanes_complete": True,
        "errors": errors,
    }
    result["audit_sha256"] = canonical_sha256(result)
    return result


def algorithm_a_single_lane_no_mutex_authorized(
    algorithm_cfg: Mapping[str, Any],
    cohort: Mapping[str, Any],
) -> bool:
    """Recognize the immutable lane-independent Algorithm A execution domain.

    This exception is deliberately narrower than a generic one-lane bypass:
    the user-authorized no-mutex contract, one-lane baseline contract, and
    lane-independent candidate-wave contract must all agree with the runtime
    cohort.  Other algorithms and stale four-lane contracts keep the original
    synchronized multi-lane requirement.
    """

    workload_contract = algorithm_cfg.get("workload_contract")
    workload_contract = (
        workload_contract if isinstance(workload_contract, Mapping) else {}
    )
    shared = workload_contract.get("shared_ssd_coordination")
    shared = shared if isinstance(shared, Mapping) else {}
    baseline = workload_contract.get("baseline_cohort")
    baseline = baseline if isinstance(baseline, Mapping) else {}
    candidate = workload_contract.get("candidate_cohort")
    candidate = candidate if isinstance(candidate, Mapping) else {}
    return bool(
        int(cohort.get("cohort_size") or 1) == 1
        and shared.get("contract") == "pg_algorithm_c_user_authorized_no_mutex_v1"
        and shared.get("enabled") is False
        and shared.get("physical_workload_mode") == "none"
        and shared.get("database_restore_mode") == "none"
        and baseline.get("contract")
        == "algorithm_c_four_lane_no_mutex_baseline_cohort_v1"
        and int(baseline.get("lane_count") or 0) == 1
        and candidate.get("contract")
        == "algorithm_a_lane_independent_candidate_waves_v1"
        and int(candidate.get("lane_count") or 0) == 1
        and candidate.get("synchronization") == "none_between_lanes"
    )


def guarded_c_single_lane_authorized(algorithm_cfg: Mapping[str, Any]) -> bool:
    """Accept only the explicit guarded JOB/XP-I/O isolated C contract."""
    guard = algorithm_cfg.get("isolated_search") or {}
    workload = algorithm_cfg.get("workload_contract") or {}
    candidate = workload.get("candidate_cohort") or {}
    baseline = workload.get("baseline_cohort") or {}
    return bool(
        algorithm_cfg.get("scene") in {"job_memory", "xp_io"}
        and guard.get("contract") in {
            "algorithm_c_guarded_isolated_search_v1",
            "algorithm_c_guarded_isolated_search_v2",
        }
        and guard.get("enabled") is True
        and (
            guard.get("external_workload_guard_required") is True
            or (
                guard.get("external_workload_interference_user_accepted") is True
                and guard.get("external_workload_interference_policy")
                == "user_authorized_uncontrolled_external_workloads_v1"
            )
        )
        and guard.get("post_restore_quiet_required") is True
        and len(str(guard.get("expected_non_search_guc_sha256") or "")) == 64
        and int(baseline.get("lane_count") or 0) == 1
        and candidate.get("contract") == "algorithm_c_guarded_isolated_candidate_waves_v1"
        and int(candidate.get("lane_count") or 0) == 1
    )


def baseline_execution_domain_schema_upgrade_only(
    frozen: Mapping[str, Any],
    current: Mapping[str, Any],
) -> bool:
    """Accept only the v2 audit's newly added explicit single-lane field."""

    if "single_lane_no_mutex_authorized" in frozen:
        return False
    frozen_body = {
        key: copy.deepcopy(value)
        for key, value in frozen.items()
        if key != "audit_sha256"
    }
    current_body = {
        key: copy.deepcopy(value)
        for key, value in current.items()
        if key not in {"audit_sha256", "single_lane_no_mutex_authorized"}
    }
    return bool(
        frozen.get("contract") == "algorithm_c_baseline_execution_domain_v2"
        and frozen.get("passed") is True
        and current.get("passed") is True
        and not (frozen.get("errors") or [])
        and not (current.get("errors") or [])
        and frozen_body == current_body
    )


def record_valid_replay_identity(
    memory: Dict[str, Any],
    *,
    candidate: Mapping[str, Any],
    round_index: int,
    label: str,
) -> Dict[str, Any]:
    """Record one already-valid replay; callers must exclude technical rows."""
    key = valid_replay_identity_key(
        candidate,
        workload_contract_sha256=str(
            memory.get("workload_contract_sha256") or ""
        ),
        target=str(memory.get("target") or ""),
    )
    if not key:
        return {"recorded": False, "reason": "incomplete_candidate_identity"}
    identities = memory.setdefault("identities", {})
    entry = identities.setdefault(
        key,
        {
            "parameter_signature_sha256": canonical_sha256(
                exact_param_signature(candidate["optimized_params"])
            ),
            "full_plan_fingerprint": candidate_full_plan_fingerprint(candidate),
            "rounds": [],
            "labels": [],
        },
    )
    rounds = {int(value) for value in (entry.get("rounds") or [])}
    rounds.add(int(round_index))
    entry["rounds"] = sorted(rounds)
    labels = {str(value) for value in (entry.get("labels") or []) if value}
    if label:
        labels.add(str(label))
    entry["labels"] = sorted(labels)
    return {"recorded": True, "identity_key": key}


def assess_valid_replay_identity(
    memory: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> Dict[str, Any]:
    """Hard-exclude only an exact valid parameter+full-plan replay pair."""
    key = valid_replay_identity_key(
        candidate,
        workload_contract_sha256=str(
            memory.get("workload_contract_sha256") or ""
        ),
        target=str(memory.get("target") or ""),
    )
    identities = (
        memory.get("identities")
        if isinstance(memory.get("identities"), Mapping)
        else {}
    )
    return {
        "identity_key": key,
        "seen_valid_replay": bool(key and key in identities),
        "technical_failures_used": False,
        "candidate_score_modified": False,
    }


def outer_pruning_required_tier(
    cfg: Mapping[str, Any],
    consecutive_missed_outer_rounds: int,
) -> str:
    _ = cfg
    _ = consecutive_missed_outer_rounds
    # The hard floor is always fallback.  Tier ordering is enforced when the
    # whole Top-K batch is sorted, not by rejecting lower tiers before that.
    return "fallback"


def outer_pruning_context(
    cfg: Mapping[str, Any],
    *,
    target: str,
    incumbent_params: Mapping[str, Any],
    required_acceptance_tier: str,
    qps_noise_model_id: Any,
) -> Dict[str, Any]:
    """Build the immutable context in which an Outer label may be reused."""
    algorithm_cfg = cfg.get("algorithm_c") if isinstance(cfg.get("algorithm_c"), Mapping) else {}
    bpt = cfg.get("binary_plan_tuning") if isinstance(cfg.get("binary_plan_tuning"), Mapping) else {}
    search_cfg = bpt.get("binary_search") if isinstance(bpt.get("binary_search"), Mapping) else {}
    nested_cfg = search_cfg.get("nested_loop") if isinstance(search_cfg.get("nested_loop"), Mapping) else {}
    outer_cfg = nested_cfg.get("outer_acceptance") if isinstance(nested_cfg.get("outer_acceptance"), Mapping) else {}
    workload_contract = algorithm_cfg.get("workload_contract") or {}
    # Reusable reject evidence is intentionally limited to failures that are
    # invariant across strict/secondary/fallback (QPS not up, or a new
    # non-target bottleneck).  Therefore the dynamic required tier must not be
    # part of the reusable context identity.  Tier-only misses are excluded by
    # OUTER_PRUNING_LEARNABLE_REJECTS and can never create a pruning rule.
    policy_payload = {
        "target": target,
        "outer_acceptance": outer_cfg,
        "acceptance_policy": acceptance_policy_for_target(dict(cfg), target),
        "qps_noise_gate": algorithm_cfg.get("qps_noise_gate") or {},
    }
    incumbent_payload = {
        name: format(float(incumbent_params[name]), ".17g")
        for name in ACTIVE_PARAMS
    }
    identity_context = {
        "contract": OUTER_PRUNING_CONTRACT,
        "workload_contract_sha256": canonical_sha256(workload_contract),
        "acceptance_policy_sha256": canonical_sha256(policy_payload),
        "incumbent_params_sha256": canonical_sha256(incumbent_payload),
        "qps_noise_model_id": str(qps_noise_model_id or ""),
        "target": target,
    }
    context = dict(identity_context)
    context["context_sha256"] = canonical_sha256(identity_context)
    context["required_acceptance_tier_observed"] = required_acceptance_tier
    context["tier_invariant_reject_evidence_only"] = True
    return context


def empty_outer_pruning_memory() -> Dict[str, Any]:
    return {
        "contract": OUTER_PRUNING_CONTRACT,
        "technical_failures_excluded": True,
        "operator_count_only_rules_forbidden": True,
        "rules": {},
    }


def outer_pruning_rule_key(context: Mapping[str, Any], candidate: Mapping[str, Any]) -> str:
    plan_fingerprint = candidate_full_plan_fingerprint(candidate)
    if not plan_fingerprint:
        return ""
    return canonical_sha256(
        {
            "context_sha256": context.get("context_sha256"),
            "full_plan_fingerprint": plan_fingerprint,
        }
    )


def record_outer_pruning_observation(
    memory: Dict[str, Any],
    *,
    context: Mapping[str, Any],
    candidate: Mapping[str, Any],
    decision: Mapping[str, Any],
    round_index: int,
) -> Dict[str, Any]:
    """Learn only from valid Outer labels; technical or incomplete rows never call this."""
    key = outer_pruning_rule_key(context, candidate)
    if not key:
        return {"recorded": False, "reason": "missing_full_plan_fingerprint_fail_open"}
    accepted = decision.get("accepted") is True
    rejects = sorted({str(value) for value in (decision.get("rejects") or [])})
    if not accepted and (not rejects or not set(rejects).issubset(OUTER_PRUNING_LEARNABLE_REJECTS)):
        return {
            "recorded": False,
            "reason": "reject_reason_not_safe_for_reuse",
            "rejects": rejects,
        }
    if decision.get("qps_noise_gate_required") is True and decision.get("qps_noise_model_ready") is not True:
        return {"recorded": False, "reason": "qps_noise_model_not_ready_fail_open"}
    rules = memory.setdefault("rules", {})
    entry = rules.setdefault(
        key,
        {
            "context_sha256": context.get("context_sha256"),
            "context": copy.deepcopy(dict(context)),
            "full_plan_fingerprint": candidate_full_plan_fingerprint(candidate),
            "accepted_count": 0,
            "rejected_count": 0,
            "rounds": [],
            "parameter_signatures": [],
            "reject_reason_counts": {},
        },
    )
    if accepted:
        entry["accepted_count"] = int(entry.get("accepted_count") or 0) + 1
    else:
        entry["rejected_count"] = int(entry.get("rejected_count") or 0) + 1
        reason_counts = entry.setdefault("reject_reason_counts", {})
        for reason in rejects:
            reason_counts[reason] = int(reason_counts.get(reason) or 0) + 1
    rounds = {int(value) for value in (entry.get("rounds") or [])}
    rounds.add(int(round_index))
    entry["rounds"] = sorted(rounds)
    params = candidate.get("optimized_params")
    if isinstance(params, Mapping) and all(name in params for name in ACTIVE_PARAMS):
        signatures = {str(value) for value in (entry.get("parameter_signatures") or [])}
        signatures.add(canonical_sha256(exact_param_signature(params)))
        entry["parameter_signatures"] = sorted(signatures)
    return {
        "recorded": True,
        "rule_key": key,
        "accepted_count": entry["accepted_count"],
        "rejected_count": entry["rejected_count"],
        "distinct_round_count": len(entry["rounds"]),
        "distinct_parameter_count": len(entry["parameter_signatures"]),
    }


def assess_outer_pruning_candidate(
    memory: Mapping[str, Any],
    *,
    context: Mapping[str, Any],
    candidate: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> Dict[str, Any]:
    """Return hard/soft replay admission without changing the inner candidate score."""
    if policy.get("enabled") is not True:
        return {"enabled": False, "hard_prune": False, "soft_deprioritize": False, "reason": "disabled"}
    key = outer_pruning_rule_key(context, candidate)
    if not key:
        return {
            "enabled": True,
            "hard_prune": False,
            "soft_deprioritize": False,
            "reason": "missing_full_plan_fingerprint_fail_open",
        }
    rules = memory.get("rules") if isinstance(memory.get("rules"), Mapping) else {}
    entry = rules.get(key) if isinstance(rules.get(key), Mapping) else {}
    accepted_count = int(entry.get("accepted_count") or 0)
    rejected_count = int(entry.get("rejected_count") or 0)
    round_count = len(entry.get("rounds") or [])
    parameter_count = len(entry.get("parameter_signatures") or [])
    minimum_rejects = max(1, int(policy.get("minimum_valid_rejects_per_plan") or 2))
    minimum_rounds = max(1, int(policy.get("minimum_distinct_rounds") or 2))
    minimum_params = max(1, int(policy.get("minimum_distinct_parameter_vectors") or 2))
    hard_prune = bool(
        accepted_count == 0
        and rejected_count >= minimum_rejects
        and round_count >= minimum_rounds
        and parameter_count >= minimum_params
    )
    return {
        "enabled": True,
        "hard_prune": hard_prune,
        "soft_deprioritize": bool(not hard_prune and accepted_count == 0 and rejected_count > 0),
        "reason": (
            "zero_accept_full_plan_outer_reject_rule"
            if hard_prune
            else "accepted_counterexample_fail_open"
            if accepted_count > 0
            else "insufficient_independent_outer_reject_support"
            if rejected_count > 0
            else "no_matching_outer_evidence"
        ),
        "rule_key": key,
        "full_plan_fingerprint": candidate_full_plan_fingerprint(candidate),
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "distinct_round_count": round_count,
        "distinct_parameter_count": parameter_count,
        "thresholds": {
            "minimum_valid_rejects_per_plan": minimum_rejects,
            "minimum_distinct_rounds": minimum_rounds,
            "minimum_distinct_parameter_vectors": minimum_params,
        },
        "candidate_score_modified": False,
        "qps_used_for_inner_scoring": False,
    }


def qps_noise_model(
    rows: Sequence[Mapping[str, Any]],
    *,
    static_floor_pct: float,
    confidence_z: float = 1.645,
    maximum_worker_repeat_ratio: float = 1.25,
) -> Dict[str, Any]:
    """Build an auditable run-level QPS diagnostic from repeated controls.

    Each worker must contribute at least two complete workload invocations at
    the same parameter vector.  Within-run sample dispersion is deliberately
    ignored because JOB and TPC-H have strongly phased query sequences.
    """
    grouped: Dict[str, List[float]] = {}
    control_runs: List[Dict[str, Any]] = []
    for row in rows:
        key = str(row.get("worker_key") or "").strip()
        value = float(row.get("db_qps") or 0.0)
        if not key or not math.isfinite(value) or value <= 0.0:
            continue
        grouped.setdefault(key, []).append(math.log(value))
        control_runs.append(
            {
                "worker": key,
                "label": row.get("label"),
                "db_qps": value,
            }
        )
    errors: List[str] = []
    quality_diagnostics: List[str] = []
    worker_sigma_log: Dict[str, float] = {}
    worker_repeat_ratio: Dict[str, float] = {}
    for key in sorted(grouped):
        values = grouped[key]
        if len(values) < 2:
            errors.append(f"worker_control_repeat_count_below_2:{key}")
            continue
        repeat_ratio = math.exp(max(values) - min(values))
        worker_repeat_ratio[key] = repeat_ratio
        if repeat_ratio > float(maximum_worker_repeat_ratio) + 1e-12:
            quality_diagnostics.append(
                "worker_control_qps_repeat_ratio_above_limit:"
                f"{key}:{repeat_ratio:.6f}>{float(maximum_worker_repeat_ratio):.6f}"
            )
        if len(values) == 2:
            sigma = abs(values[0] - values[1]) / math.sqrt(2.0)
        else:
            median = sorted(values)[len(values) // 2]
            deviations = sorted(abs(value - median) for value in values)
            sigma = 1.4826 * deviations[len(deviations) // 2]
        worker_sigma_log[key] = sigma
    if len(worker_sigma_log) < 2:
        errors.append("qps_noise_model_requires_two_workers")
    static_floor_log = math.log1p(max(0.0, float(static_floor_pct)) / 100.0)
    pooled_sigma_log = max(worker_sigma_log.values(), default=math.inf)
    comparison_sigma_log = math.sqrt(2.0) * pooled_sigma_log
    effective_log_gain = static_floor_log + float(confidence_z) * comparison_sigma_log
    effective_min_gain_pct = (
        math.expm1(effective_log_gain) * 100.0
        if math.isfinite(effective_log_gain)
        else math.inf
    )
    result = {
        "contract": "algorithm_c_run_level_qps_noise_v1",
        "ready": not errors,
        "errors": errors,
        "quality_diagnostics": quality_diagnostics,
        "quality_blocks_formal_start": False,
        "formal_role": "outer_acceptance_audit_only",
        "static_floor_pct": float(static_floor_pct),
        "confidence_z": float(confidence_z),
        "worker_sigma_log": worker_sigma_log,
        "worker_repeat_ratio": worker_repeat_ratio,
        "maximum_worker_repeat_ratio": float(maximum_worker_repeat_ratio),
        "pooled_sigma_log": pooled_sigma_log,
        "comparison_sigma_log": comparison_sigma_log,
        "effective_min_gain_pct": effective_min_gain_pct,
        "control_runs": control_runs,
        "within_run_qps_dispersion_used": False,
    }
    result["model_id"] = canonical_sha256(result)
    return result


def executor_telemetry_baseline_model(
    rows: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Freeze execution labels from repeated baseline controls for plan prediction.

    Only resource counters are consumed.  QPS and execution latency are
    intentionally absent so the inner estimator remains independent of outer
    acceptance semantics.
    """
    errors: List[str] = []
    observations: List[Dict[str, Any]] = []
    totals: Dict[str, float] = {}
    for row in rows:
        telemetry = (
            row.get("executor_telemetry")
            if isinstance(row.get("executor_telemetry"), Mapping)
            else {}
        )
        if telemetry.get("available") is not True:
            errors.append(
                f"executor_telemetry_unavailable:{row.get('worker_key') or row.get('label')}"
            )
            continue
        metrics = telemetry.get("metrics") if isinstance(telemetry.get("metrics"), Mapping) else {}
        derived = telemetry.get("derived") if isinstance(telemetry.get("derived"), Mapping) else {}
        observations.append(
            {
                "worker": row.get("worker_key"),
                "label": row.get("label"),
                "source": telemetry.get("source"),
                "shared_cache_hit_rate": derived.get("shared_cache_hit_rate"),
                "shared_block_accesses": derived.get("shared_block_accesses"),
                "temp_blocks": derived.get("temp_blocks"),
            }
        )
        for name, value in metrics.items():
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                totals[str(name)] = totals.get(str(name), 0.0) + float(value)
    if len(observations) != len(rows) or len(observations) < 4:
        errors.append("executor_telemetry_requires_four_repeated_baseline_controls")
    shared_accesses = totals.get("shared_blks_hit", 0.0) + totals.get("shared_blks_read", 0.0)
    if shared_accesses <= 0.0:
        errors.append("executor_telemetry_shared_block_accesses_not_positive")
        cache_hit_rate = None
    else:
        cache_hit_rate = totals.get("shared_blks_hit", 0.0) / shared_accesses
    result = {
        "contract": "algorithm_c_frozen_executor_telemetry_baseline_v1",
        "ready": not errors,
        "errors": errors,
        "control_count": len(observations),
        "observations": observations,
        "aggregate_metrics": totals,
        "derived": {
            "shared_cache_hit_rate": cache_hit_rate,
            "shared_block_accesses": shared_accesses,
            "temp_blocks": totals.get("temp_blks_read", 0.0)
            + totals.get("temp_blks_written", 0.0),
        },
        "qps_used_for_resource_prediction": False,
        "frozen_after_formal_start": True,
    }
    result["model_id"] = canonical_sha256(result)
    return result


def qualify_ssd_wave(
    rows: Sequence[Mapping[str, Any]],
    *,
    target: str,
    phase: str,
    cfg: Mapping[str, Any],
    global_ssd_utilization_is_informational: Optional[bool] = None,
) -> Dict[str, Any]:
    """Validate shared-SSD conditions before a replay can become semantic data."""
    algorithm_cfg = cfg.get("algorithm_c") if isinstance(cfg.get("algorithm_c"), Mapping) else {}
    gate_cfg = (
        algorithm_cfg.get("ssd_replay_qualification")
        if isinstance(algorithm_cfg.get("ssd_replay_qualification"), Mapping)
        else {}
    )
    enabled = gate_cfg.get("enabled") is not False
    expected_devices = sorted(
        str(value) for value in (gate_cfg.get("expected_devices") or ["nvme1n1"])
    )
    minimum_samples = max(1, int(gate_cfg.get("minimum_stable_samples") or 45))
    maximum_avg = float(
        gate_cfg.get(
            "io_target_maximum_ssd_avg_pct" if target == "io" else "non_target_maximum_ssd_avg_pct",
            85.0 if target == "io" else 80.0,
        )
    )
    maximum_p99 = float(
        gate_cfg.get(
            "io_target_maximum_ssd_p99_pct" if target == "io" else "non_target_maximum_ssd_p99_pct",
            100.1 if target == "io" else 95.0,
        )
    )
    minimum_avg = (
        float(gate_cfg.get("io_target_baseline_minimum_ssd_avg_pct") or 65.0)
        if target == "io" and "baseline" in phase
        else None
    )
    maximum_parallel_skew = float(gate_cfg.get("maximum_parallel_avg_skew_pct_points") or 5.0)
    expected_source = str(gate_cfg.get("expected_source") or "host_diskstats_io_busy_ms")
    non_target_policy = str(
        gate_cfg.get("non_target_shared_ssd_policy") or "global_ssd_utilization"
    )
    use_container_io_for_non_target = (
        target != "io" and non_target_policy == "container_io_capacity"
    )
    non_target_candidate_saturation_is_semantic = (
        use_container_io_for_non_target and "baseline" not in phase
    )
    # Every replay wave normally shares one coordinated physical SSD.  A
    # host-busy window above the configured ceiling then means the simultaneous
    # points interfere and cannot become a semantic label.  When the operator
    # explicitly launches the controller with coordination disabled *and*
    # lock mode ``none``, unrelated lanes are intentionally allowed on the
    # same SSD.  In that explicit mode host-wide diskstats are informational;
    # per-container IO and the aggregate traffic of this lane's own replay
    # wave remain enforced below.  Merely omitting the environment variables
    # keeps the conservative coordinated behavior.
    io_target_candidate_saturation_is_semantic = False
    explicit_user_authorized_no_mutex = explicit_user_authorized_no_mutex_from_env()
    if global_ssd_utilization_is_informational is None:
        host_ssd_is_informational = explicit_user_authorized_no_mutex
        host_ssd_policy_authority = "environment_no_mutex_contract"
    else:
        # Multi-lane replay controllers know whether unrelated workloads share
        # the host SSD during this wave.  Let them state that fact explicitly
        # instead of inheriting an ambient process environment.  Passing False
        # is intentional: it keeps the IO-target stable-window gate enforced.
        host_ssd_is_informational = bool(
            global_ssd_utilization_is_informational
        )
        host_ssd_policy_authority = "explicit_caller_contract"
    enforce_global_ssd_stable_window = not host_ssd_is_informational
    capacity_cfg = (
        (((cfg.get("binary_plan_tuning") or {}).get("acceptance_policy") or {}).get(
            "resource_capacity"
        ))
        or {}
    )
    container_io_capacity_mib_s = float(capacity_cfg.get("io_mib_s") or 0.0)
    maximum_container_io_util_pct = float(
        gate_cfg.get("non_target_maximum_container_io_util_pct") or 80.0
    )

    errors: List[str] = []
    observations: List[Dict[str, Any]] = []
    for row in rows:
        base_valid = physical_valid({key: value for key, value in row.items() if key != "ssd_qualification"})
        stats = row.get("resource_stats") if isinstance(row.get("resource_stats"), Mapping) else {}
        ssd_stats = stats.get("ssd_util_percent") if isinstance(stats.get("ssd_util_percent"), Mapping) else {}
        sample_counts = stats.get("stable_sample_count") if isinstance(stats.get("stable_sample_count"), Mapping) else {}
        devices = sorted(str(value) for value in (stats.get("ssd_observer_disk_devices") or []))
        avg = float(ssd_stats["avg"]) if ssd_stats.get("avg") is not None else math.nan
        p99 = float(ssd_stats["p99"]) if ssd_stats.get("p99") is not None else math.nan
        maximum = float(ssd_stats["max"]) if ssd_stats.get("max") is not None else math.nan
        sample_count = int(sample_counts.get("io_util_pct") or 0)
        source = str(stats.get("ssd_util_source") or "")
        resources = row.get("resources") if isinstance(row.get("resources"), Mapping) else {}
        container_io_mib_s = float(resources.get("io") or 0.0)
        container_io_util_pct = (
            100.0 * container_io_mib_s / container_io_capacity_mib_s
            if container_io_capacity_mib_s > 0.0
            else math.nan
        )
        key = str(row.get("worker_key") or "")
        row_errors: List[str] = []
        if not base_valid:
            row_errors.append("base_physical_validation_failed")
        if devices != expected_devices:
            row_errors.append("unexpected_ssd_observer_devices")
        if source != expected_source:
            row_errors.append("unexpected_ssd_utilization_source")
        if sample_count < minimum_samples:
            row_errors.append("ssd_stable_sample_count_below_minimum")
        if not all(math.isfinite(value) and 0.0 <= value <= 100.0 for value in (avg, p99, maximum)):
            row_errors.append("ssd_utilization_evidence_invalid")
        elif enforce_global_ssd_stable_window:
            if avg >= maximum_avg:
                row_errors.append("ssd_average_utilization_above_stable_window")
            elif minimum_avg is not None and avg < minimum_avg:
                row_errors.append("ssd_average_utilization_below_io_target_window")
        if enforce_global_ssd_stable_window:
            if math.isfinite(p99) and p99 >= maximum_p99:
                row_errors.append("ssd_p99_utilization_above_stable_window")
        if use_container_io_for_non_target:
            if not math.isfinite(container_io_util_pct) or container_io_util_pct < 0.0:
                row_errors.append("container_io_utilization_evidence_invalid")
            elif container_io_util_pct >= maximum_container_io_util_pct:
                if not non_target_candidate_saturation_is_semantic:
                    row_errors.append(
                        "container_io_utilization_above_non_target_limit"
                    )
        observations.append(
            {
                "worker": key,
                "window": copy.deepcopy(row.get("window")),
                "devices": devices,
                "source": source,
                "ssd_avg_pct": avg,
                "ssd_p99_pct": p99,
                "ssd_max_pct": maximum,
                "container_io_mib_s": container_io_mib_s,
                "container_io_capacity_mib_s": container_io_capacity_mib_s,
                "container_io_util_pct": container_io_util_pct,
                "semantic_non_target_io_bottleneck": bool(
                    non_target_candidate_saturation_is_semantic
                    and math.isfinite(container_io_util_pct)
                    and container_io_util_pct
                    >= maximum_container_io_util_pct
                ),
                "stable_sample_count": sample_count,
                "errors": row_errors,
            }
        )
        errors.extend(f"{key}:{reason}" for reason in row_errors)

    comparable_parameter_vectors = False
    parameter_vector_sha256: List[str] = []
    for row in rows:
        raw_params = row.get("params") if isinstance(row.get("params"), Mapping) else {}
        normalized_params = {
            name: float(raw_params[name])
            for name in ACTIVE_PARAMS
            if name in raw_params
        }
        parameter_vector_sha256.append(canonical_sha256(normalized_params))
    if len(parameter_vector_sha256) >= 2:
        comparable_parameter_vectors = len(set(parameter_vector_sha256)) == 1

    aggregate_container_io_mib_s = sum(
        float(item["container_io_mib_s"])
        for item in observations
        if math.isfinite(float(item["container_io_mib_s"]))
    )
    aggregate_container_io_util_pct = (
        100.0 * aggregate_container_io_mib_s / container_io_capacity_mib_s
        if container_io_capacity_mib_s > 0.0
        else math.nan
    )
    if use_container_io_for_non_target:
        if not math.isfinite(aggregate_container_io_util_pct):
            errors.append("aggregate_container_io_utilization_evidence_invalid")
        elif aggregate_container_io_util_pct >= maximum_container_io_util_pct:
            # Neither row can be interpreted independently once their combined
            # traffic saturates the one physical device.  Mark the whole wave
            # technical rather than assigning the interference to either
            # candidate or letting it train the resource model.
            errors.append("aggregate_container_io_utilization_above_non_target_limit")

    if len(observations) >= 2:
        windows = [item.get("window") for item in observations]
        valid_windows = bool(
            all(
                isinstance(window, Sequence) and len(window) == 2
                for window in windows
            )
        )
        if valid_windows:
            valid_windows = bool(
                max(float(window[0]) for window in windows)
                < min(float(window[1]) for window in windows)
            )
        if not valid_windows:
            errors.append("parallel_ssd_observation_windows_do_not_overlap")
        averages = [float(item["ssd_avg_pct"]) for item in observations]
        # A parallel baseline repeats the same parameter vector, so large SSD
        # skew means the measurement environment is not comparable.  A
        # candidate wave deliberately runs two *different* parameter vectors;
        # their resource differences are the label being measured and must not
        # be converted into a technical failure.  Each row still has to pass
        # its own device/source/sample/window checks above.
        if (
            enforce_global_ssd_stable_window
            and
            comparable_parameter_vectors
            and all(math.isfinite(value) for value in averages)
            and max(averages) - min(averages) > maximum_parallel_skew
        ):
            errors.append("parallel_ssd_average_skew_above_limit")

    worker_errors = {
        str(observation.get("worker") or ""): [
            str(reason) for reason in (observation.get("errors") or [])
        ]
        for observation in observations
    }
    worker_scoped_errors = {
        f"{key}:{reason}"
        for key, reasons in worker_errors.items()
        for reason in reasons
    }
    wave_errors = [
        str(reason)
        for reason in errors
        if str(reason) not in worker_scoped_errors
    ]

    result = {
        "contract": "algorithm_c_shared_ssd_wave_gate_v5",
        "enabled": enabled,
        "passed": bool(enabled and rows and not errors),
        "target": target,
        "phase": phase,
        "wave_width": len(rows),
        "errors": errors,
        "wave_errors": wave_errors,
        "worker_errors": worker_errors,
        "observations": observations,
        "aggregate_container_io_mib_s": aggregate_container_io_mib_s,
        "aggregate_container_io_util_pct": aggregate_container_io_util_pct,
        "thresholds": {
            "expected_devices": expected_devices,
            "expected_source": expected_source,
            "minimum_stable_samples": minimum_samples,
            "minimum_ssd_avg_pct": minimum_avg,
            "maximum_ssd_avg_pct": maximum_avg,
            "maximum_ssd_p99_pct": maximum_p99,
            "maximum_parallel_avg_skew_pct_points": maximum_parallel_skew,
            "parallel_skew_only_for_identical_parameter_vectors": True,
            "parallel_skew_comparable_parameter_vectors": (
                comparable_parameter_vectors
            ),
            "parameter_vector_sha256": parameter_vector_sha256,
            "non_target_shared_ssd_policy": non_target_policy,
            "non_target_maximum_container_io_util_pct": maximum_container_io_util_pct,
            "container_io_capacity_mib_s": container_io_capacity_mib_s,
            "global_ssd_utilization_is_informational": (
                host_ssd_is_informational
            ),
            "explicit_user_authorized_no_mutex": (
                explicit_user_authorized_no_mutex
            ),
            "host_ssd_policy_authority": host_ssd_policy_authority,
            "io_target_candidate_saturation_is_semantic": (
                io_target_candidate_saturation_is_semantic
            ),
            "non_target_candidate_saturation_is_semantic": (
                non_target_candidate_saturation_is_semantic
            ),
            "global_ssd_stable_window_enforced": enforce_global_ssd_stable_window,
            "container_io_non_target_limit_enforced": use_container_io_for_non_target,
        },
    }
    result["semantic_non_target_bottleneck_resources"] = (
        ["io"]
        if any(
            observation.get("semantic_non_target_io_bottleneck") is True
            for observation in observations
        )
        else []
    )
    result["qualification_id"] = canonical_sha256(result)
    return result


def apply_ssd_wave_qualification(
    rows: Sequence[Dict[str, Any]],
    *,
    target: str,
    phase: str,
    cfg: Mapping[str, Any],
    global_ssd_utilization_is_informational: Optional[bool] = None,
) -> Dict[str, Any]:
    qualification = qualify_ssd_wave(
        rows,
        target=target,
        phase=phase,
        cfg=cfg,
        global_ssd_utilization_is_informational=(
            global_ssd_utilization_is_informational
        ),
    )
    for row in rows:
        worker = str(row.get("worker_key") or "")
        wave_errors = [
            str(reason) for reason in (qualification.get("wave_errors") or [])
        ]
        worker_error_map = qualification.get("worker_errors")
        if isinstance(worker_error_map, Mapping):
            local_errors = [
                str(reason)
                for reason in (worker_error_map.get(worker) or [])
            ]
        else:
            # Fail closed for callers supplying an older/custom qualification
            # shape without explicit worker-error isolation evidence.
            local_errors = [
                str(reason) for reason in (qualification.get("errors") or [])
            ]
        row_qualification = copy.deepcopy(qualification)
        row_qualification["row_isolation_contract"] = (
            "algorithm_c_ssd_wave_worker_failure_isolation_v1"
        )
        row_qualification["wave_passed"] = qualification.get("passed") is True
        row_qualification["worker"] = worker
        row_qualification["errors"] = wave_errors + [
            f"{worker}:{reason}" for reason in local_errors
        ]
        row_qualification["passed"] = bool(
            qualification.get("enabled") is True
            and rows
            and not row_qualification["errors"]
        )
        row_qualification["qualification_id"] = canonical_sha256(
            {
                key: value
                for key, value in row_qualification.items()
                if key != "qualification_id"
            }
        )
        row["ssd_qualification"] = row_qualification
        if row_qualification.get("passed") is not True:
            row["technical_failure"] = True
            row["technical_failure_kind"] = "ssd_qualification_out_of_band"
            row["valid_replay_count"] = 0
            failures = row.setdefault("workload_technical_failures", [])
            if isinstance(failures, list):
                failures.append("ssd_qualification_out_of_band")
    return qualification


def capacity_from_config(cfg: Mapping[str, Any]) -> Dict[str, float]:
    policy = ((cfg.get("binary_plan_tuning") or {}).get("acceptance_policy") or {})
    capacity = policy.get("resource_capacity") or {}
    result = {
        "cpu": float(capacity.get("cpu_cores") or 0.0),
        "memory": float(capacity.get("memory_gib") or 0.0),
        "io": float(capacity.get("io_mib_s") or 0.0),
    }
    if any(not math.isfinite(value) or value <= 0.0 for value in result.values()):
        raise ValueError(f"invalid resource capacity: {result}")
    return result


def configured_static_qps_floor_pct(cfg: Mapping[str, Any]) -> float:
    search_cfg = ((cfg.get("binary_plan_tuning") or {}).get("binary_search") or {})
    nested_cfg = search_cfg.get("nested_loop") if isinstance(search_cfg.get("nested_loop"), Mapping) else {}
    outer_cfg = nested_cfg.get("outer_acceptance") if isinstance(nested_cfg.get("outer_acceptance"), Mapping) else {}
    algorithm_cfg = cfg.get("algorithm_c") if isinstance(cfg.get("algorithm_c"), Mapping) else {}
    noise_cfg = algorithm_cfg.get("qps_noise_gate") if isinstance(algorithm_cfg.get("qps_noise_gate"), Mapping) else {}
    values = [
        float(value)
        for value in (
            outer_cfg.get("min_db_qps_gain_pct"),
            noise_cfg.get("static_floor_pct"),
        )
        if value is not None
    ]
    if len(values) == 2 and not math.isclose(values[0], values[1], rel_tol=0.0, abs_tol=1e-12):
        raise RuntimeError(f"conflicting QPS floor aliases: {values}")
    strict_increase_only = (
        noise_cfg.get("enabled") is False
        and outer_cfg.get("qps_threshold_mode") == "static_floor"
    )
    if (not values or not math.isfinite(values[0]) or values[0] < 0.0
            or (values[0] == 0.0 and not strict_increase_only)):
        raise RuntimeError("formal Algorithm C requires a positive QPS floor or explicit noise-free strict increase")
    return values[0]


def formal_outer_qps_progress_contract(cfg: Mapping[str, Any]) -> Dict[str, Any]:
    """Audit the formal B/C rule that QPS must advance from the incumbent.

    The first formal round has no accepted incumbent and therefore compares
    against the canonical baseline.  After every accept, ``accept()`` replaces
    ``incumbent_validation``; rejected and technical rows never mutate it.
    Requiring ``last_accepted`` here prevents a formal campaign from silently
    falling back to the immutable experiment baseline.
    """

    search_cfg = (
        ((cfg.get("binary_plan_tuning") or {}).get("binary_search") or {})
    )
    nested_cfg = (
        search_cfg.get("nested_loop")
        if isinstance(search_cfg.get("nested_loop"), Mapping)
        else {}
    )
    outer_cfg = (
        nested_cfg.get("outer_acceptance")
        if isinstance(nested_cfg.get("outer_acceptance"), Mapping)
        else {}
    )
    qps_reference_mode = str(
        outer_cfg.get("qps_reference_mode") or ""
    ).strip().lower()
    errors: List[str] = []
    if qps_reference_mode != "last_accepted":
        errors.append("qps_reference_mode_must_be_last_accepted")
    try:
        static_floor_pct = configured_static_qps_floor_pct(cfg)
    except (RuntimeError, TypeError, ValueError) as exc:
        static_floor_pct = None
        errors.append(f"invalid_static_qps_floor:{exc}")
    payload: Dict[str, Any] = {
        "contract": OUTER_QPS_PROGRESS_CONTRACT,
        "qps_reference_mode": qps_reference_mode,
        "first_round_reference": "canonical_baseline",
        "subsequent_round_reference": "previous_accepted_round",
        "rejected_round_updates_reference": False,
        "technical_failure_updates_reference": False,
        "static_min_db_qps_gain_pct": static_floor_pct,
        "passed": not errors,
        "errors": errors,
    }
    payload["audit_sha256"] = canonical_sha256(payload)
    return payload


def should_escape_cached_qps_stagnation(
    *,
    required_tier: str,
    decision_evidence_count: int,
    physical_rows_this_round: int,
) -> bool:
    """Return whether cached misses must reopen full-range plan exploration.

    Valid cached evidence can still be semantically exhausted under the
    last-accepted QPS contract.  Once the current interval has no unseen
    physical candidate at fallback, retaining those lower-QPS rows must not
    suppress the full-range, distinct-plan basin escape.
    """

    return bool(
        str(required_tier) == "fallback"
        and int(decision_evidence_count) > 0
        and int(physical_rows_this_round) == 0
    )


def aggregate_baseline(rows: Sequence[Mapping[str, Any]], capacity: Mapping[str, float]) -> Dict[str, Any]:
    aggregate = aggregate_parallel_baseline_validation(rows)
    if str(aggregate.get("status") or "") != "ok":
        raise RuntimeError(f"parallel baseline failed: {aggregate.get('error')}")
    resources = aggregate.get("resources") or {}
    aggregate["actual_utilization_pct"] = {
        name: float(resources[name]) / float(capacity[name]) * 100.0
        for name in ("cpu", "memory", "io")
    }
    return aggregate


def normalize_to_worker_baseline(
    validation: Mapping[str, Any],
    worker_baseline: Mapping[str, Any],
    canonical_baseline: Mapping[str, Any],
    capacity: Mapping[str, float],
) -> Dict[str, Any]:
    normalized = copy.deepcopy(dict(validation))
    candidate_resources = validation.get("resources") or {}
    worker_resources = worker_baseline.get("resources") or {}
    canonical_resources = canonical_baseline.get("resources") or {}
    resources: Dict[str, float] = {}
    for name in ("cpu", "memory", "io"):
        worker_value = float(worker_resources[name])
        if worker_value <= 0.0:
            raise RuntimeError(f"non-positive same-worker baseline resource: {name}")
        resources[name] = (
            float(canonical_resources[name])
            * float(candidate_resources[name])
            / worker_value
        )
    worker_qps = float(worker_baseline.get("db_qps") or 0.0)
    canonical_qps = float(canonical_baseline.get("db_qps") or 0.0)
    candidate_qps = float(validation.get("db_qps") or 0.0)
    if worker_qps <= 0.0 or canonical_qps <= 0.0:
        raise RuntimeError("same-worker baseline QPS must be positive")
    normalized["resources"] = resources
    normalized["db_qps"] = canonical_qps * candidate_qps / worker_qps
    normalized["actual_utilization_pct"] = {
        name: resources[name] / float(capacity[name]) * 100.0
        for name in resources
    }
    normalized["same_worker_normalization"] = {
        "worker_baseline_label": worker_baseline.get("label"),
        "raw_candidate_resources": dict(candidate_resources),
        "raw_candidate_db_qps": candidate_qps,
        "canonical_baseline_label": canonical_baseline.get("label"),
    }
    return normalized


class RatioScaleEstimator:
    """Plan-derived resource ratios anchored to the real incumbent."""

    def __init__(
        self,
        cfg: Dict[str, Any],
        queries: Sequence[Dict[str, Any]],
        baseline_probe: Mapping[str, Any],
        baseline_real: Mapping[str, float],
        baseline_params: Mapping[str, float],
        prediction_calibration: Mapping[str, Any],
    ) -> None:
        self.probe = PlanFeatureProbe(copy.deepcopy(cfg))
        self.queries = list(queries)
        self.baseline_probe = copy.deepcopy(dict(baseline_probe))
        self.baseline_real = {name: float(value) for name, value in baseline_real.items()}
        self.baseline_params = {name: float(value) for name, value in baseline_params.items()}
        self.prediction_calibration = copy.deepcopy(dict(prediction_calibration))
        if (
            self.prediction_calibration.get("contract")
            != RESOURCE_PREDICTION_CALIBRATION_CONTRACT
        ):
            raise RuntimeError("RatioScaleEstimator prediction calibration mismatch")

    def __call__(self, _sql_set: Any, params: Dict[str, float]) -> Tuple[Dict[str, float], Dict[str, Any], Dict[str, Any]]:
        current = self.probe.probe(self.queries, params)
        if current.get("errors"):
            raise RuntimeError(f"plan probe errors: {current.get('errors')[:3]}")
        exact_metadata = current.get("exact_plan_metadata")
        if not isinstance(exact_metadata, Mapping):
            raise RuntimeError("plan probe exact metadata missing")
        coverage = exact_metadata.get("plan_fingerprint_coverage")
        if not isinstance(coverage, Mapping) or coverage.get("complete") is not True:
            raise RuntimeError(f"incomplete exact plan coverage: {coverage}")
        baseline_estimated = self.baseline_probe.get("resource_avg") or {}
        current_estimated = current.get("resource_avg") or {}
        baseline_breakdown = self.baseline_probe.get("resource_breakdown_summary") or {}
        current_breakdown = current.get("resource_breakdown_summary") or {}
        cpu_shape = (current.get("feature_inputs") or {}).get("cpu") or {}
        baseline_cpu_shape = (
            (self.baseline_probe.get("feature_inputs") or {}).get("cpu") or {}
        )
        relation_stats_coverage = min(
            float(cpu_shape.get("plan_relation_stats_scan_coverage") or 0.0),
            float(
                baseline_cpu_shape.get("plan_relation_stats_scan_coverage") or 0.0
            ),
        )
        stats_modification_fraction = float(
            cpu_shape.get("plan_stats_modification_fraction_max") or 0.0
        )
        physical_model_cfg = (
            (((self.probe.cfg.get("metrics") or {}).get("plan_estimator") or {}).get(
                "physical_model"
            ))
            or {}
        )
        if (
            physical_model_cfg.get("catalog_relation_stats_required_for_confidence")
            is True
            and relation_stats_coverage < 1.0 - 1e-12
        ):
            raise RuntimeError(
                "catalog relation statistics coverage incomplete: "
                f"{relation_stats_coverage:.6f}"
            )
        parameter_log_deltas: Dict[str, float] = {}
        for parameter_name in ACTIVE_PARAMS:
            baseline_parameter = float(self.baseline_params[parameter_name])
            current_parameter = float(params[parameter_name])
            if baseline_parameter <= 0.0 or current_parameter <= 0.0:
                raise RuntimeError(
                    f"non-positive calibrated parameter: {parameter_name}"
                )
            parameter_log_deltas[parameter_name] = math.log(
                current_parameter / baseline_parameter
            )

        calibration_resources = self.prediction_calibration.get("resources") or {}
        ratios: Dict[str, float] = {}
        raw_plan_ratios: Dict[str, float] = {}
        resources: Dict[str, float] = {}
        plan_model_confidence: Dict[str, float] = {}
        calibration_audit: Dict[str, Any] = {}
        for name in ("cpu", "memory", "io"):
            denominator = float(baseline_estimated.get(name) or 0.0)
            numerator = float(current_estimated.get(name) or 0.0)
            if denominator <= 0.0 or numerator <= 0.0 or not math.isfinite(numerator):
                raise RuntimeError(f"invalid plan ratio for {name}: {numerator}/{denominator}")
            raw_plan_ratio = numerator / denominator
            raw_plan_ratios[name] = raw_plan_ratio
            calibration_row = calibration_resources.get(name)
            if not isinstance(calibration_row, Mapping):
                raise RuntimeError(f"prediction calibration missing resource: {name}")
            parameter_coefficients = calibration_row.get("parameter_coefficients") or {}
            parameter_component = sum(
                float(parameter_coefficients[parameter_name])
                * parameter_log_deltas[parameter_name]
                for parameter_name in ACTIVE_PARAMS
            )
            squared_coefficients = (
                calibration_row.get("parameter_squared_coefficients") or {}
            )
            parameter_squared_component = sum(
                float(squared_coefficients.get(parameter_name) or 0.0)
                * parameter_log_deltas[parameter_name] ** 2
                for parameter_name in ACTIVE_PARAMS
            )
            interaction_coefficients = (
                calibration_row.get("parameter_interaction_coefficients")
                or {}
            )
            interaction_component = sum(
                float(
                    interaction_coefficients.get(f"{left}*{right}") or 0.0
                )
                * parameter_log_deltas[left]
                * parameter_log_deltas[right]
                for left, right in parameter_interaction_names(ACTIVE_PARAMS)
            )
            plan_coefficient = float(
                calibration_row.get("plan_log_ratio_coefficient") or 0.0
            )
            raw_plan_log_ratio = math.log(raw_plan_ratio)
            parameter_plan_coefficients = (
                calibration_row.get(
                    "parameter_plan_interaction_coefficients"
                )
                or {}
            )
            parameter_plan_component = sum(
                float(
                    parameter_plan_coefficients.get(parameter_name) or 0.0
                )
                * parameter_log_deltas[parameter_name]
                * raw_plan_log_ratio
                for parameter_name in ACTIVE_PARAMS
            )
            plan_squared_component = float(
                calibration_row.get("plan_log_ratio_squared_coefficient")
                or 0.0
            ) * raw_plan_log_ratio ** 2
            predicted_log_ratio = (
                parameter_component
                + parameter_squared_component
                + interaction_component
                + plan_coefficient * raw_plan_log_ratio
                + parameter_plan_component
                + plan_squared_component
            )
            target_prediction_safety_mode = str(
                calibration_row.get("target_prediction_safety_mode")
                or "direct_resource_response"
            )
            monotone_parameter_l2_scale = float(
                calibration_row.get("monotone_parameter_l2_scale") or 0.0
            )
            monotone_reference_l2 = float(
                calibration_row.get("monotone_reference_l2") or 1.0
            )
            if target_prediction_safety_mode in {
                "flat_target_safe_abstention",
                "irreducible_noise_safe_abstention",
            }:
                predicted_log_ratio = 0.0
            elif target_prediction_safety_mode == "monotone_target_down_distance":
                parameter_l2 = math.sqrt(
                    sum(
                        parameter_log_deltas[parameter_name] ** 2
                        for parameter_name in ACTIVE_PARAMS
                    )
                )
                if (
                    not math.isfinite(monotone_parameter_l2_scale)
                    or monotone_parameter_l2_scale <= 0.0
                    or not math.isfinite(monotone_reference_l2)
                    or monotone_reference_l2 <= 0.0
                ):
                    raise RuntimeError(
                        f"invalid monotone target calibration: {name}"
                    )
                predicted_log_ratio = -monotone_parameter_l2_scale * min(
                    2.0,
                    parameter_l2 / monotone_reference_l2,
                )
            elif target_prediction_safety_mode != "direct_resource_response":
                raise RuntimeError(
                    "unknown target prediction safety mode: "
                    f"{name}={target_prediction_safety_mode}"
                )
            support_domain = calibration_row.get("parameter_support_domain")
            support_assessment: Dict[str, Any] = {
                "enabled": False,
                "supported": True,
                "support_abstained": False,
                "qps_used": False,
            }
            if calibration_row.get("parameter_support_abstention_enabled") is True:
                if not isinstance(support_domain, Mapping):
                    raise RuntimeError(
                        f"prediction support domain missing for {name}"
                    )
                support_assessment = {
                    "enabled": True,
                    **parameter_support_assessment(
                        [parameter_log_deltas[param] for param in ACTIVE_PARAMS],
                        support_domain,
                        ACTIVE_PARAMS,
                    ),
                }
                if support_assessment["support_abstained"]:
                    predicted_log_ratio = 0.0
            maximum_absolute_log_ratio = math.log(1_000_000.0)
            if (
                not math.isfinite(predicted_log_ratio)
                or abs(predicted_log_ratio) > maximum_absolute_log_ratio
            ):
                raise RuntimeError(
                    f"calibrated resource ratio outside safety envelope: "
                    f"{name}={predicted_log_ratio}"
                )
            ratios[name] = math.exp(predicted_log_ratio)
            resources[name] = self.baseline_real[name] * ratios[name]
            calibration_audit[name] = {
                "family": calibration_row.get("family"),
                "parameter_log_component": parameter_component,
                "parameter_squared_component": parameter_squared_component,
                "parameter_interaction_component": interaction_component,
                "raw_plan_log_ratio": raw_plan_log_ratio,
                "plan_log_ratio_coefficient": plan_coefficient,
                "parameter_plan_interaction_component": (
                    parameter_plan_component
                ),
                "plan_log_ratio_squared_component": plan_squared_component,
                "predicted_log_ratio": predicted_log_ratio,
                "target_prediction_safety_mode": (
                    target_prediction_safety_mode
                ),
                "target_prediction_abstained": (
                    target_prediction_safety_mode
                    in {
                        "flat_target_safe_abstention",
                        "irreducible_noise_safe_abstention",
                    }
                ),
                "monotone_parameter_l2_scale": monotone_parameter_l2_scale,
                "monotone_reference_l2": monotone_reference_l2,
                "parameter_support": support_assessment,
                "support_abstained": bool(
                    support_assessment.get("support_abstained")
                ),
            }
            baseline_supported = (
                (baseline_breakdown.get(name) or {}).get(
                    "physical_direction_supported"
                )
                is True
            )
            current_supported = (
                (current_breakdown.get(name) or {}).get(
                    "physical_direction_supported"
                )
                is True
            )
            support_factor = 1.0 if baseline_supported and current_supported else 0.5
            catalog_factor = 0.5 + 0.5 * min(
                1.0, max(0.0, relation_stats_coverage)
            )
            staleness_factor = 1.0 / (1.0 + max(0.0, stats_modification_fraction))
            plan_model_confidence[name] = max(
                0.1,
                min(1.0, support_factor * catalog_factor * staleness_factor),
            )
        stats = {
            name: {
                "min": value,
                "max": value,
                "avg": value,
                "p99": value,
            }
            for name, value in resources.items()
        }
        metadata = {
            **copy.deepcopy(dict(exact_metadata)),
            "resource_estimation_mode": "ratio_scale",
            "cost_parameter_free_features": all(
                str((calibration_resources.get(name) or {}).get("family") or "")
                == "raw_plan"
                for name in ("cpu", "memory", "io")
            ),
            "feature_ratios": ratios,
            "feature_ratios_unclipped": copy.deepcopy(ratios),
            "raw_plan_feature_ratios": raw_plan_ratios,
            "parameter_log_deltas": parameter_log_deltas,
            "resource_prediction_calibration_contract": (
                RESOURCE_PREDICTION_CALIBRATION_CONTRACT
            ),
            "resource_prediction_calibration_audit": calibration_audit,
            "plan_model_confidence": plan_model_confidence,
            "plan_prediction_quality": {
                "physical_model_enabled": current.get("physical_model_enabled") is True,
                "relation_stats_scan_coverage": relation_stats_coverage,
                "stats_modification_fraction_max": stats_modification_fraction,
                "baseline_resource_support": copy.deepcopy(baseline_breakdown),
                "candidate_resource_support": copy.deepcopy(current_breakdown),
                "confidence_is_diagnostic_not_hard_pruning": True,
            },
            "baseline_real": copy.deepcopy(self.baseline_real),
            "baseline_params": copy.deepcopy(self.baseline_params),
            "qps_used_for_inner_scoring": False,
            "current_features": copy.deepcopy(current.get("feature_inputs") or {}),
        }
        return resources, stats, metadata


class LaneController:
    @property
    def replay_parallelism(self) -> int:
        """Return replay width, defaulting to the legacy two-worker domain.

        The fallback keeps lightweight ``__new__``-based semantic tests and
        legacy state migration helpers compatible without weakening runtime
        configuration validation in :meth:`__init__`.
        """

        return int(getattr(self, "_replay_parallelism", REPLAY_PARALLELISM))

    @replay_parallelism.setter
    def replay_parallelism(self, value: int) -> None:
        self._replay_parallelism = int(value)

    def configured_replay_parallelism(self) -> int:
        """Return the physical candidate replay width for this algorithm.

        Algorithm B/C keep their formal two-worker execution domain.  An
        exhaustive derivative may override this hook, but must still provide
        exactly one isolated PostgreSQL worker for every concurrent slot.
        """

        return REPLAY_PARALLELISM

    def physical_wave_guard_event(self, event: str, value: Any) -> None:
        """Optional isolated-run guard; legacy execution keeps its own contract."""
        guard = getattr(self, "physical_wave_guard", None)
        if guard is not None:
            getattr(guard, event)(value)
        elif guarded_c_single_lane_authorized(getattr(self, "algorithm_cfg", {})):
            raise RuntimeError("isolated C configuration requires the guarded entry point")

    def __init__(self, cfg_path: Path, output_dir: Path, target: str) -> None:
        self.cfg_path = cfg_path
        self.cfg = read_json(cfg_path)
        self.output_dir = output_dir
        self.target = target
        self.algorithm_cfg = self.cfg.get("algorithm_c") or {}
        self.native_xp_runtime_contract = native_xp_runtime_contract_audit(
            self.cfg,
            target=self.target,
        )
        if self.native_xp_runtime_contract.get("passed") is not True:
            raise RuntimeError(
                "native XP workload/runtime identity mismatch: "
                f"{self.native_xp_runtime_contract.get('errors')}"
            )
        self.workload_runtime_execution_domain = (
            workload_contract_execution_domain_audit(
                self.algorithm_cfg,
                expected_no_mutex=explicit_user_authorized_no_mutex_from_env(),
                cohort=algorithm_c_cohort_config_from_env(),
                candidate_cohort=algorithm_c_candidate_cohort_config_from_env(),
            )
        )
        if self.workload_runtime_execution_domain.get("passed") is not True:
            raise RuntimeError(
                "workload/runtime execution domain mismatch: "
                f"{self.workload_runtime_execution_domain.get('errors')}"
            )
        self.convergence_contract = resolve_convergence_contract(self.algorithm_cfg)
        self.outer_qps_progress_contract = formal_outer_qps_progress_contract(
            self.cfg
        )
        if self.outer_qps_progress_contract.get("passed") is not True:
            raise RuntimeError(
                "formal Algorithm B/C QPS progress contract failed: "
                f"{self.outer_qps_progress_contract.get('errors')}"
            )
        self.accepted_target = int(
            self.convergence_contract["accepted_target"]
        )
        self.normalized_width_threshold = float(
            self.convergence_contract["normalized_width_threshold"]
        )
        self.stability_confirmation_rounds = int(
            self.convergence_contract["stability_confirmation_rounds"]
        )
        self.stability_confirmation_max_attempts = int(
            self.convergence_contract["stability_confirmation_max_attempts"]
        )
        self.outer_round_limit = int(
            self.convergence_contract["outer_round_limit"]
        )
        self.no_improvement_convergence_rounds = int(
            self.convergence_contract["no_improvement_convergence_rounds"]
        )
        search_cfg = (
            ((self.cfg.get("binary_plan_tuning") or {}).get("binary_search") or {})
        )
        self.max_rounds = int(search_cfg.get("max_rounds") or 100)
        if self.max_rounds < self.accepted_target:
            raise RuntimeError(
                "formal Algorithm C max_rounds must be at least accepted_target: "
                f"{self.max_rounds} < {self.accepted_target}"
            )
        if self.max_rounds < self.outer_round_limit:
            raise RuntimeError(
                "formal Algorithm B/C max_rounds must be at least outer_round_limit: "
                f"{self.max_rounds} < {self.outer_round_limit}"
            )
        pruning_cfg = (
            (((self.cfg.get("binary_plan_tuning") or {}).get("binary_search") or {}).get(
                "replay_candidate_pruning"
            ))
            or {}
        )
        if self.algorithm_cfg.get("operator_resource_pruning_enabled") is not False:
            raise RuntimeError("formal Algorithm C must explicitly forbid operator-resource pruning")
        if isinstance(pruning_cfg, Mapping) and pruning_cfg.get("enabled") is not False:
            raise RuntimeError("replay candidate pruning must be explicitly disabled for Algorithm C")
        self.outer_pruning_policy = outer_pruning_config(self.cfg)
        if self.outer_pruning_policy.get("enabled") is True:
            if self.outer_pruning_policy.get("contract") != OUTER_PRUNING_CONTRACT:
                raise RuntimeError("Outer accept/reject pruning contract mismatch")
            if self.outer_pruning_policy.get("operator_count_only_rules_forbidden") is not True:
                raise RuntimeError("Outer pruning must explicitly forbid operator-count-only hard rules")
            if self.outer_pruning_policy.get("technical_failures_excluded") is not True:
                raise RuntimeError("Outer pruning must explicitly exclude technical failures")
        plan_estimator_cfg = ((self.cfg.get("metrics") or {}).get("plan_estimator") or {})
        physical_model_cfg = plan_estimator_cfg.get("physical_model") or {}
        if physical_model_cfg.get("enabled") is not True:
            raise RuntimeError("formal Algorithm C requires executor-physics resource estimation")
        validation_cfg = ((self.cfg.get("binary_plan_tuning") or {}).get("validation") or {})
        executor_telemetry_cfg = validation_cfg.get("executor_telemetry") or {}
        if executor_telemetry_cfg.get("required") is not True:
            raise RuntimeError("formal Algorithm C requires post-replay executor telemetry")
        self.state_path = output_dir / "algorithm_c_state.json"
        self.log_path = output_dir / "algorithm_c.jsonl"
        self.stop_requested = False
        self.capacity = capacity_from_config(self.cfg)
        self.workers = validation_workers(self.cfg, target)
        self.replay_parallelism = int(self.configured_replay_parallelism())
        if self.replay_parallelism < 1:
            raise RuntimeError("physical replay parallelism must be positive")
        if len(self.workers) != self.replay_parallelism:
            raise RuntimeError(
                "physical replay parallelism requires exactly one worker per slot: "
                f"parallelism={self.replay_parallelism} workers={len(self.workers)}"
            )
        if len({worker_key(worker) for worker in self.workers}) != len(self.workers):
            raise RuntimeError("physical replay workers must have distinct identities")
        self.initial_params = {
            name: float((self.algorithm_cfg.get("initial_params") or {})[name])
            for name in ACTIVE_PARAMS
        }
        self.workload_contract_sha256 = canonical_sha256(
            self.algorithm_cfg.get("workload_contract") or {}
        )
        self.t_artifact_path = Path(str(self.algorithm_cfg.get("experience_table_path") or ""))
        self.experience_table = self.load_experience_table()
        self.ranges = {
            name: ParameterRange(*DEFAULT_RANGES[name]) for name in ACTIVE_PARAMS
        }
        # Valid physical evidence is deliberately kept out of persisted
        # optimizer state.  It is reconstructed from this campaign's
        # authoritative candidate-replay log after every process restart.
        # This keeps the state compact while making exact replay de-duplication
        # independent from tier-dependent Outer accept/reject decisions.
        self.valid_replay_evidence_by_identity: Dict[str, Dict[str, Any]] = {}
        self.state = self.load_or_initialize_state()
        self.recover_inflight_physical_wave()
        self.repair_legacy_completion_domain_pollution()
        self.repair_restart_local_candidate_budget_overrun()
        self.rehydrate_outer_pruning_memory()
        self.rehydrate_valid_replay_identity_memory()

    def log(self, event: str, **extra: Any) -> None:
        payload = {
            "event": event,
            "time": time.time(),
            "scene": self.algorithm_cfg.get("scene"),
            "target": self.target,
            **extra,
        }
        append_jsonl(self.log_path, payload)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)

    def save_state(self) -> None:
        self.state["inner_search_contract"] = (
            PLAN_PORTFOLIO_CONTRACT if plan_portfolio_policy(getattr(self, "algorithm_cfg", {}))
            else "legacy_subset_search"
        )
        self.state["updated_at_epoch_s"] = time.time()
        self.state["ranges"] = {
            name: asdict(value) for name, value in self.ranges.items()
        }
        write_json(self.state_path, self.state)

    def worker_execution_cohort_audit(self) -> Dict[str, Any]:
        """Return the immutable physical worker identity used by this lane."""

        workers = []
        for worker in self.workers:
            workers.append(
                {
                    key: copy.deepcopy(worker.get(key))
                    for key in (
                        "name",
                        "container",
                        "host",
                        "port",
                        "dbname",
                        "data_dir",
                        "container_data_dir",
                        "cpus",
                        "memory",
                        "cpuset_cpus",
                        "disk_root",
                        "disk_devices",
                        "targets",
                    )
                }
            )
        payload = {
            "contract": "pg_optimizer_physical_worker_execution_cohort_v1",
            "replay_parallelism": self.replay_parallelism,
            "global_restore_parallelism": max(
                1,
                safe_int(
                    os.environ.get(
                        "PG_ALGORITHM_C_GLOBAL_RESTORE_PARALLELISM"
                    ),
                    2,
                ),
            ),
            "worker_count": len(workers),
            "workers": workers,
        }
        payload["cohort_sha256"] = canonical_sha256(payload)
        return payload

    def prepare_worker_execution_cohort_transition(
        self,
        *,
        baseline_keys: Set[str],
        configured_keys: Set[str],
    ) -> bool:
        """Authorize an execution-domain transition in an algorithm derivative.

        Algorithm B/C never change their frozen physical worker cohort.  A
        derivative must override this hook and preserve a complete audit trail
        before clearing baseline-dependent fields.
        """

        _ = baseline_keys, configured_keys
        return False

    def continue_after_candidate_terminal_qualification_failure(
        self,
        qualification: Mapping[str, Any],
        *,
        batch_kind: str,
    ) -> bool:
        """Whether a derivative may continue after candidate-local failures.

        Algorithm C keeps the conservative fail-closed behavior.  A
        derivative that requires every physical parameter point to be
        attempted may override this only when the qualification failure is
        entirely due to the candidate workload itself; SSD/execution-domain
        failures must still abort the batch.
        """

        _ = qualification, batch_kind
        return False

    def prepare_valid_replay_identity_context_transition(
        self,
        state: Dict[str, Any],
        identity_memory: Mapping[str, Any],
    ) -> bool:
        """Authorize a workload-contract identity epoch change in a derivative."""

        _ = state, identity_memory
        return False

    def local_candidate_budget_checkpoint(
        self,
        round_index: int,
    ) -> Optional[Dict[str, Any]]:
        """Return the durable local Top-10 completion record for a round."""

        if not self.log_path.is_file():
            return None
        checkpoint: Optional[Dict[str, Any]] = None
        try:
            with self.log_path.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    try:
                        row = json.loads(raw_line)
                    except (TypeError, ValueError):
                        continue
                    if (
                        row.get("event")
                        == "algorithm_c_local_candidate_budget_complete"
                        and int(row.get("round_index") or 0) == int(round_index)
                    ):
                        checkpoint = copy.deepcopy(row)
        except OSError:
            return None
        return checkpoint

    def repair_restart_local_candidate_budget_overrun(self) -> None:
        """Quarantine ordinary-pool rows launched after a durable Top-10.

        A restarted controller used to forget the per-round in-memory local
        replay counter.  It could therefore start a second ordinary Top-5
        after the authoritative Top-10 completion event.  Those invocations
        remain physical facts, but they are outside the formal local budget:
        never turn them into valid decision/cache evidence and never call
        them technical failures.
        """

        if not self.log_path.is_file():
            return
        try:
            round_index = int(self.state.get("round_index") or 0)
        except (TypeError, ValueError):
            return
        checkpoint = self.local_candidate_budget_checkpoint(round_index)
        if not checkpoint:
            return
        checkpoint_time = float(checkpoint.get("time") or 0.0)
        existing = set(
            str(value)
            for value in self.state.get("invalidated_replay_labels") or []
        )
        newly_quarantined: Set[str] = set()
        try:
            with self.log_path.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    try:
                        row = json.loads(raw_line)
                    except (TypeError, ValueError):
                        continue
                    if float(row.get("time") or 0.0) <= checkpoint_time:
                        continue
                    if int(row.get("round_index") or 0) != round_index:
                        continue
                    if row.get("event") != "algorithm_c_candidate_replay":
                        continue
                    batch_kind = str(row.get("batch_kind") or "")
                    if not (
                        batch_kind == "base_top5"
                        or batch_kind.startswith("local_plan_tail_")
                    ):
                        continue
                    validation = row.get("validation")
                    if not isinstance(validation, Mapping) or not physical_valid(
                        validation
                    ):
                        continue
                    label = str(validation.get("label") or "")
                    if label and label not in existing:
                        newly_quarantined.add(label)
        except OSError:
            return
        if not newly_quarantined:
            return

        memory = self.state.get("valid_replay_identity_memory")
        identities = memory.get("identities") if isinstance(memory, Mapping) else None
        removed_identity_keys: List[str] = []
        if isinstance(identities, dict):
            for key, value in list(identities.items()):
                labels = set(
                    str(label)
                    for label in (
                        value.get("labels") if isinstance(value, Mapping) else []
                    )
                )
                if labels and labels.issubset(newly_quarantined):
                    removed_identity_keys.append(str(key))
                    identities.pop(key, None)

        self.state["invalidated_replay_labels"] = sorted(
            existing.union(newly_quarantined)
        )
        excluded_count = len(newly_quarantined)
        self.state["valid_replays"] = max(
            0,
            int(self.state.get("valid_replays") or 0) - excluded_count,
        )
        self.state["restart_local_budget_overrun_quarantined_replays"] = int(
            self.state.get("restart_local_budget_overrun_quarantined_replays") or 0
        ) + excluded_count
        repair = {
            "contract": "algorithm_c_restart_local_budget_overrun_quarantine_v1",
            "passed": True,
            "round_index": round_index,
            "checkpoint_time": checkpoint_time,
            "quarantined_labels": sorted(newly_quarantined),
            "removed_identity_keys": sorted(removed_identity_keys),
            "physical_workload_invocations_retained": True,
            "valid_replays_removed": excluded_count,
            "technical_failures_unchanged": True,
            "accepted_unchanged": True,
            "incumbent_unchanged": True,
            "ranges_unchanged": True,
            "gradient_state_unchanged": True,
            "time": time.time(),
        }
        self.state["restart_local_budget_overrun_quarantine"] = repair
        self.save_state()
        self.log(
            "algorithm_c_restart_local_budget_overrun_quarantined",
            **repair,
        )

    def repair_legacy_completion_domain_pollution(self) -> None:
        """Remove rows that old controllers admitted after a failed cohort barrier."""
        if not self.log_path.is_file():
            return
        repair = self.state.get("completion_domain_pollution_repair")
        prior_repair_time = float(
            repair.get("time")
            if isinstance(repair, Mapping) and repair.get("time") is not None
            else 0.0
        )
        repaired_labels = set(
            str(value)
            for value in (
                repair.get("excluded_labels")
                if isinstance(repair, Mapping)
                else []
            )
        )
        newly_excluded: Set[str] = set()
        with self.log_path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                try:
                    event = json.loads(raw_line)
                except (TypeError, ValueError):
                    continue
                if event.get("event") != "algorithm_c_candidate_replay":
                    continue
                if prior_repair_time and float(event.get("time") or 0.0) >= prior_repair_time:
                    continue
                validation = event.get("validation")
                if not isinstance(validation, Mapping):
                    continue
                if not candidate_completion_out_of_band_invalid(validation):
                    continue
                accounting = validation.get("completion_domain_accounting")
                if (
                    isinstance(accounting, Mapping)
                    and accounting.get("contract")
                    == "algorithm_c_completion_domain_out_of_band_accounting_v1"
                ):
                    continue
                ssd = validation.get("ssd_qualification")
                if not isinstance(ssd, Mapping) or ssd.get("passed") is not True:
                    continue
                label = str(validation.get("label") or "")
                if label and label not in repaired_labels:
                    newly_excluded.add(label)
        if not newly_excluded:
            return

        memory = self.state.get("valid_replay_identity_memory")
        identities = memory.get("identities") if isinstance(memory, Mapping) else None
        removed_identity_keys: List[str] = []
        if isinstance(identities, dict):
            for key, value in list(identities.items()):
                labels = set(
                    str(label)
                    for label in (
                        value.get("labels") if isinstance(value, Mapping) else []
                    )
                )
                if labels and labels.issubset(newly_excluded):
                    removed_identity_keys.append(str(key))
                    identities.pop(key, None)

        def valid_semantic_attempt(attempt: Any) -> bool:
            if not isinstance(attempt, Mapping):
                return False
            confirmations = attempt.get("confirmations")
            labels = {
                str(row.get("label") or "")
                for row in (
                    confirmations if isinstance(confirmations, list) else []
                )
                if isinstance(row, Mapping)
            }
            return not bool(labels.intersection(newly_excluded))

        stability_rounds = self.state.get("stability_rounds")
        removed_semantic_attempts = 0
        if isinstance(stability_rounds, list):
            retained = [row for row in stability_rounds if valid_semantic_attempt(row)]
            removed_semantic_attempts += len(stability_rounds) - len(retained)
            self.state["stability_rounds"] = retained
        active = self.state.get("active_champion_validation")
        if isinstance(active, dict) and isinstance(active.get("attempts"), list):
            active_attempts = active["attempts"]
            active["attempts"] = [
                row for row in active_attempts if valid_semantic_attempt(row)
            ]
        for archive_row in self.state.get("champion_validation_archive") or []:
            result = archive_row.get("result") if isinstance(archive_row, dict) else None
            attempts = result.get("attempts") if isinstance(result, dict) else None
            if isinstance(attempts, list):
                result["attempts"] = [
                    row for row in attempts if valid_semantic_attempt(row)
                ]
                result["semantic_attempts"] = len(result["attempts"])

        self.state["consecutive_stable_rounds"] = 0
        excluded_count = len(newly_excluded)
        self.state["valid_replays"] = max(
            0,
            int(self.state.get("valid_replays") or 0) - excluded_count,
        )
        self.state["out_of_band_invalid_replays"] = int(
            self.state.get("out_of_band_invalid_replays") or 0
        ) + excluded_count
        all_labels = sorted(repaired_labels.union(newly_excluded))
        self.state["invalidated_replay_labels"] = sorted(
            set(str(value) for value in self.state.get("invalidated_replay_labels") or [])
            .union(all_labels)
        )
        self.state["completion_domain_pollution_repair"] = {
            "contract": "algorithm_c_completion_domain_pollution_repair_v1",
            "passed": True,
            "excluded_labels": all_labels,
            "newly_excluded_labels": sorted(newly_excluded),
            "removed_identity_keys": sorted(removed_identity_keys),
            "removed_semantic_attempts": removed_semantic_attempts,
            "accepted_unchanged": True,
            "incumbent_unchanged": True,
            "ranges_unchanged": True,
            "gradient_state_unchanged": True,
            "technical_failures_unchanged": True,
            "time": time.time(),
        }
        self.save_state()
        self.log(
            "algorithm_c_completion_domain_pollution_repaired",
            **self.state["completion_domain_pollution_repair"],
        )

    def verify_baseline_execution_domain(self) -> Dict[str, Any]:
        """Reject reuse when baseline and formal replay use different SSD modes."""
        baseline_rows = self.state.get("baseline_replay_rows")
        canonical = self.state.get("canonical_baseline")
        if not isinstance(baseline_rows, Sequence) or not isinstance(
            canonical,
            Mapping,
        ):
            raise RuntimeError("baseline execution-domain audit lacks baseline rows")
        audit = baseline_execution_domain_audit(
            [row for row in baseline_rows if isinstance(row, Mapping)],
            canonical,
            expected_no_mutex=explicit_user_authorized_no_mutex_from_env(),
            cohort=algorithm_c_cohort_config_from_env(),
            allow_single_lane_no_mutex=(
                algorithm_a_single_lane_no_mutex_authorized(
                    self.algorithm_cfg,
                    algorithm_c_cohort_config_from_env(),
                )
                or guarded_c_single_lane_authorized(self.algorithm_cfg)
            ),
        )
        frozen = self.state.get("baseline_execution_domain")
        if isinstance(frozen, Mapping):
            if str(frozen.get("audit_sha256") or "") != str(
                audit.get("audit_sha256") or ""
            ):
                if baseline_execution_domain_schema_upgrade_only(frozen, audit):
                    prior_sha256 = str(frozen.get("audit_sha256") or "")
                    self.state["baseline_execution_domain"] = copy.deepcopy(audit)
                    self.state.pop("baseline_execution_domain_mismatch", None)
                    self.save_state()
                    self.log(
                        "algorithm_c_baseline_execution_domain_schema_enriched",
                        prior_audit_sha256=prior_sha256,
                        current_audit_sha256=audit.get("audit_sha256"),
                        added_field="single_lane_no_mutex_authorized",
                        physical_workload_invocations=0,
                        semantic_state_mutation=False,
                    )
                else:
                    audit["errors"] = list(audit.get("errors") or []) + [
                        "frozen_baseline_execution_domain_mismatch"
                    ]
                    audit["passed"] = False
                    audit["audit_sha256"] = canonical_sha256(
                        {
                            key: value
                            for key, value in audit.items()
                            if key != "audit_sha256"
                        }
                    )
        if audit.get("passed") is not True:
            self.state["phase"] = "baseline_execution_domain_mismatch_clean"
            self.state["baseline_execution_domain_mismatch"] = audit
            self.save_state()
            self.log(
                "algorithm_c_baseline_execution_domain_mismatch",
                baseline_execution_domain=audit,
                accepted=int(self.state.get("accepted") or 0),
                accepted_unchanged=True,
                incumbent_unchanged=True,
                ranges_unchanged=True,
                gradient_state_unchanged=True,
                physical_workload_invocations=0,
                technical_failure=True,
            )
            self.restore_clean_baseline(
                reason="baseline_execution_domain_mismatch"
            )
            raise RuntimeError(
                "baseline and formal replay execution domains differ: "
                f"{audit.get('errors')}"
            )
        if not isinstance(frozen, Mapping):
            self.state["baseline_execution_domain"] = copy.deepcopy(audit)
            self.save_state()
            self.log(
                "algorithm_c_baseline_execution_domain_rehydrated",
                baseline_execution_domain=audit,
                semantic_state_mutation=False,
                physical_workload_invocations=0,
            )
        return audit

    def load_experience_table(self) -> Dict[str, Dict[str, float]]:
        if not self.t_artifact_path.is_file():
            raise FileNotFoundError(self.t_artifact_path)
        artifact = read_json(self.t_artifact_path)
        validate_experience_artifact(artifact, self.target)
        self.experience_artifact = copy.deepcopy(artifact)
        table = artifact.get("table")
        if not isinstance(table, dict) or set(table) != set(ACTIVE_PARAMS):
            raise RuntimeError("experience table active parameter mismatch")
        if canonical_sha256(table) != str(artifact.get("table_sha256") or ""):
            raise RuntimeError("experience table SHA mismatch")
        return {
            name: {
                resource: float(row.get(resource) or 0.0)
                for resource in (*("cpu", "memory", "io"), "confidence")
            }
            for name, row in table.items()
        }

    def load_or_initialize_state(self) -> Dict[str, Any]:
        if self.state_path.is_file():
            state = read_json(self.state_path)
            expected_inner_contract = (
                PLAN_PORTFOLIO_CONTRACT if plan_portfolio_policy(getattr(self, "algorithm_cfg", {}))
                else "legacy_subset_search"
            )
            if state.get("inner_search_contract", "legacy_subset_search") != expected_inner_contract:
                raise RuntimeError("inner search contract changed; prepare a fresh C campaign")
            if int(state.get("state_version") or 0) != STATE_VERSION:
                raise RuntimeError("state version mismatch")
            if int(state.get("accepted_target") or 0) != self.accepted_target:
                raise RuntimeError("accepted target changed after formal state creation")
            persisted_max_rounds = int(
                state.get("max_rounds") or self.max_rounds
            )
            if persisted_max_rounds != self.max_rounds:
                raise RuntimeError("max rounds changed after formal state creation")
            state["max_rounds"] = self.max_rounds
            persisted_outer_round_limit = int(
                state.get("outer_round_limit") or 0
            )
            if persisted_outer_round_limit != self.outer_round_limit:
                raise RuntimeError(
                    "outer round limit changed after formal state creation"
                )
            persisted_no_improvement_rounds = int(
                state.get("no_improvement_convergence_rounds") or 0
            )
            if (
                persisted_no_improvement_rounds
                != self.no_improvement_convergence_rounds
            ):
                raise RuntimeError(
                    "no-improvement convergence rounds changed after formal state creation"
                )
            state["stability_confirmation_rounds"] = 0
            state["stability_confirmation_max_attempts"] = 0
            state["accepted_improvements"] = int(state.get("accepted") or 0)
            state.setdefault("consecutive_no_improvement_rounds", 0)
            accepted_count = int(state.get("accepted") or 0)
            refinement_count = int(
                state.get("incumbent_centered_stagnation_refinement_count") or 0
            )
            effective_shrink_count = accepted_count + refinement_count
            state["accepted_normalized_width"] = 0.5 ** accepted_count
            state["effective_binary_shrink_count"] = effective_shrink_count
            state["normalized_width"] = 0.5 ** effective_shrink_count
            state["convergence_progress_contract"] = (
                EFFECTIVE_SEARCH_PROGRESS_CONTRACT
            )
            persisted_width_threshold = float(
                state.get("normalized_width_threshold") or 0.0
            )
            if not math.isclose(
                persisted_width_threshold,
                self.normalized_width_threshold,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise RuntimeError(
                    "normalized width threshold changed after formal state creation"
                )
            ranges = state.get("ranges") or {}
            self.ranges = {
                name: ParameterRange(
                    float(ranges[name]["min_value"]),
                    float(ranges[name]["max_value"]),
                )
                for name in ACTIVE_PARAMS
            }
            identity_memory = state.get("valid_replay_identity_memory")
            if not isinstance(identity_memory, dict):
                state["valid_replay_identity_memory"] = (
                    empty_valid_replay_identity_memory(
                        self.workload_contract_sha256,
                        self.target,
                    )
                )
            elif (
                identity_memory.get("contract")
                != VALID_REPLAY_IDENTITY_CONTRACT
                or identity_memory.get("workload_contract_sha256")
                != self.workload_contract_sha256
                or identity_memory.get("target") != self.target
            ):
                if not self.prepare_valid_replay_identity_context_transition(
                    state,
                    identity_memory,
                ):
                    raise RuntimeError("valid replay identity memory context mismatch")
            state.setdefault("accepted_candidate_archive", [])
            state.setdefault("best_feasible_champion", None)
            state.setdefault("final_champion", None)
            persisted_qps_contract = state.get("outer_qps_progress_contract")
            if not isinstance(persisted_qps_contract, Mapping):
                raise RuntimeError(
                    "formal state lacks outer QPS progress contract"
                )
            if (
                persisted_qps_contract.get("audit_sha256")
                != self.outer_qps_progress_contract.get("audit_sha256")
            ):
                raise RuntimeError(
                    "outer QPS progress contract changed after formal state creation"
                )
            return state
        return {
            "state_version": STATE_VERSION,
            "method": "experience_table_all_subset_sgd_algorithm_c_v1",
            "phase": "startup",
            "accepted": 0,
            "accepted_target": self.accepted_target,
            "max_rounds": self.max_rounds,
            "normalized_width_threshold": self.normalized_width_threshold,
            "expected_normalized_width": self.convergence_contract[
                "expected_normalized_width"
            ],
            "stability_confirmation_rounds": self.stability_confirmation_rounds,
            "stability_confirmation_max_attempts": (
                self.stability_confirmation_max_attempts
            ),
            "consecutive_stable_rounds": 0,
            "consecutive_plan_plateau_rounds": 0,
            "round_index": 0,
            "consecutive_missed_outer_rounds": 0,
            "consecutive_no_improvement_rounds": 0,
            "accepted_improvements": 0,
            "accepted_normalized_width": 1.0,
            "effective_binary_shrink_count": 0,
            "normalized_width": 1.0,
            "convergence_progress_contract": (
                EFFECTIVE_SEARCH_PROGRESS_CONTRACT
            ),
            "outer_round_limit": self.outer_round_limit,
            "no_improvement_convergence_rounds": (
                self.no_improvement_convergence_rounds
            ),
            "physical_workload_invocations": 0,
            "valid_replays": 0,
            "technical_failures": 0,
            "restore_technical_failures": 0,
            "incumbent_params": copy.deepcopy(self.initial_params),
            "incumbent_validation": None,
            "accepted_candidate_archive": [],
            "best_feasible_champion": None,
            "final_champion": None,
            "outer_qps_progress_contract": copy.deepcopy(
                self.outer_qps_progress_contract
            ),
            "baseline_by_worker": {},
            "executor_telemetry_baseline_model": None,
            "formal_started": False,
            "manual_mutation_after_formal_start": False,
            "t_artifact_path": str(self.t_artifact_path),
            "outer_accept_reject_pruning": empty_outer_pruning_memory(),
            "valid_replay_identity_memory": empty_valid_replay_identity_memory(
                self.workload_contract_sha256,
                self.target,
            ),
        }

    def required_acceptance_tier(self) -> str:
        return outer_pruning_required_tier(
            self.cfg,
            int(self.state.get("consecutive_missed_outer_rounds") or 0),
        )

    def current_outer_pruning_context(
        self,
        *,
        incumbent_params: Optional[Mapping[str, Any]] = None,
        required_tier: Optional[str] = None,
        qps_noise_model_id: Any = None,
    ) -> Dict[str, Any]:
        params = incumbent_params or self.state.get("incumbent_params") or self.initial_params
        noise_model = self.state.get("qps_noise_model") or {}
        return outer_pruning_context(
            self.cfg,
            target=self.target,
            incumbent_params=params,
            required_acceptance_tier=required_tier or self.required_acceptance_tier(),
            qps_noise_model_id=(
                qps_noise_model_id
                if qps_noise_model_id is not None
                else noise_model.get("model_id")
            ),
        )

    def rehydrate_outer_pruning_memory(self) -> None:
        """Rebuild missing evidence from this lane's valid Outer-decision log."""
        memory = self.state.get("outer_accept_reject_pruning")
        if not isinstance(memory, dict) or memory.get("contract") != OUTER_PRUNING_CONTRACT:
            memory = empty_outer_pruning_memory()
            self.state["outer_accept_reject_pruning"] = memory
        if self.outer_pruning_policy.get("enabled") is not True or memory.get("rules"):
            return
        if not self.log_path.is_file():
            return
        candidates_by_label: Dict[str, Dict[str, Any]] = {}
        decisions: List[Dict[str, Any]] = []
        epoch_start_s = float(
            self.state.get("outer_pruning_epoch_start_s") or 0.0
        )
        try:
            with self.log_path.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    try:
                        row = json.loads(raw_line)
                    except (TypeError, ValueError):
                        continue
                    if float(row.get("time") or 0.0) < epoch_start_s:
                        continue
                    if row.get("event") == "algorithm_c_candidate_replay":
                        validation = row.get("validation")
                        if not isinstance(validation, Mapping) or not physical_valid(validation):
                            continue
                        ranking = validation.get("candidate_ranking")
                        label = str(validation.get("label") or "")
                        if label and isinstance(ranking, Mapping):
                            candidates_by_label[label] = copy.deepcopy(dict(ranking))
                    elif row.get("event") == "algorithm_c_outer_candidate_decision":
                        decisions.append(row)
        except OSError:
            return
        recorded = 0
        for row in decisions:
            label = str(row.get("label") or "")
            candidate = candidates_by_label.get(label)
            decision = row.get("decision")
            if not candidate or not isinstance(decision, Mapping):
                continue
            incumbent_params = candidate.get("initial_params")
            if not isinstance(incumbent_params, Mapping):
                continue
            context = self.current_outer_pruning_context(
                incumbent_params=incumbent_params,
                required_tier=str(decision.get("required_acceptance_tier") or "strict"),
                qps_noise_model_id=decision.get("qps_noise_model_id"),
            )
            update = record_outer_pruning_observation(
                memory,
                context=context,
                candidate=candidate,
                decision=decision,
                round_index=int(row.get("round_index") or 0),
            )
            recorded += int(update.get("recorded") is True)
        if recorded:
            self.state["outer_accept_reject_pruning_rehydrated_count"] = recorded
            self.save_state()

    def rehydrate_valid_replay_identity_memory(self) -> None:
        """Recover exact valid replay identities, including partial waves.

        Unlike Outer labels, this evidence is available as soon as a physical
        replay is valid.  A clean signal stop may occur before batch-level
        accept/reject evaluation, so startup must recover directly from the
        candidate replay records and must ignore every technical failure.
        """
        memory = self.state.get("valid_replay_identity_memory")
        if not isinstance(memory, dict):
            memory = empty_valid_replay_identity_memory(
                self.workload_contract_sha256,
                self.target,
            )
            self.state["valid_replay_identity_memory"] = memory
        if not self.log_path.is_file():
            return
        before = len(memory.get("identities") or {})
        valid_rows = 0
        invalidated_labels = set(
            str(value)
            for value in self.state.get("invalidated_replay_labels") or []
        )
        epoch_start_s = float(
            self.state.get("valid_replay_identity_epoch_start_s") or 0.0
        )
        try:
            with self.log_path.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    try:
                        row = json.loads(raw_line)
                    except (TypeError, ValueError):
                        continue
                    if float(row.get("time") or 0.0) < epoch_start_s:
                        continue
                    if row.get("event") != "algorithm_c_candidate_replay":
                        continue
                    validation = row.get("validation")
                    if not isinstance(validation, Mapping) or not physical_valid(
                        validation
                    ):
                        continue
                    if str(validation.get("label") or "") in invalidated_labels:
                        continue
                    candidate = validation.get("candidate_ranking")
                    if not isinstance(candidate, Mapping):
                        continue
                    update = record_valid_replay_identity(
                        memory,
                        candidate=candidate,
                        round_index=int(row.get("round_index") or 0),
                        label=str(validation.get("label") or ""),
                    )
                    self.cache_valid_replay_evidence(
                        validation,
                        round_index=int(row.get("round_index") or 0),
                    )
                    valid_rows += int(update.get("recorded") is True)
        except OSError:
            return
        after = len(memory.get("identities") or {})
        if after != before:
            self.state["valid_replay_identity_rehydrated_rows"] = valid_rows
            self.state["valid_replay_identity_rehydrated_count"] = after
            self.save_state()

    def cache_valid_replay_evidence(
        self,
        validation: Mapping[str, Any],
        *,
        round_index: int,
    ) -> Dict[str, Any]:
        """Cache one valid same-campaign replay for later Outer re-decision.

        The cache never admits technical failures and never feeds QPS or the
        measured resources back into prediction, gradient, candidate score,
        or ranking.  It exists only to let a later acceptance tier re-evaluate
        an already-paid physical result without replaying the same exact point.
        """
        if not physical_valid(validation):
            return {"cached": False, "reason": "physical_validation_not_valid"}
        candidate = validation.get("candidate_ranking")
        if not isinstance(candidate, Mapping):
            return {"cached": False, "reason": "candidate_ranking_missing"}
        identity_memory = self.state.get("valid_replay_identity_memory")
        if not isinstance(identity_memory, Mapping):
            return {"cached": False, "reason": "identity_memory_missing"}
        key = valid_replay_identity_key(
            candidate,
            workload_contract_sha256=str(
                identity_memory.get("workload_contract_sha256") or ""
            ),
            target=str(identity_memory.get("target") or ""),
        )
        if not key:
            return {"cached": False, "reason": "incomplete_candidate_identity"}
        self.valid_replay_evidence_by_identity[key] = {
            "contract": VALID_REPLAY_EVIDENCE_REDECISION_CONTRACT,
            "round_index": int(round_index),
            "label": str(validation.get("label") or ""),
            "validation": copy.deepcopy(dict(validation)),
            "technical_failure_excluded": True,
            "qps_outer_only": True,
        }
        return {"cached": True, "identity_key": key}

    def import_historical_valid_replay_seed(self) -> Dict[str, Any]:
        """Import audited prior-campaign outcomes after this campaign's baseline.

        The seed is optional and Outer-only.  Its source validation is first
        normalized by the source worker/canonical baseline, then projected
        onto the matching fresh worker baseline.  A subsequent normal replay
        normalization therefore reproduces the source canonical-relative
        ratio under this campaign's canonical scale.  The seed cannot import
        accepted state, ranges, gradients, counters, or technical failures.
        """

        seed_path_raw = self.algorithm_cfg.get("historical_valid_replay_seed_path")
        if not seed_path_raw:
            return {"imported": False, "reason": "not_configured"}
        seed_path = Path(str(seed_path_raw))
        if not seed_path.is_file():
            raise FileNotFoundError(seed_path)
        seed = read_json(seed_path)
        if seed.get("contract") != HISTORICAL_VALID_REPLAY_SEED_CONTRACT:
            raise RuntimeError("historical valid replay seed contract mismatch")
        if str(seed.get("source_algorithm") or "") != "algorithm_c":
            raise RuntimeError(
                "Algorithm C runtime replay seeds must originate from "
                "Algorithm C; A/B evidence is training-only"
            )
        if seed.get("technical_failures_excluded") is not True:
            raise RuntimeError("historical valid replay seed includes technical failures")
        if seed.get("previously_accepted_parameter_vectors_excluded") is not True:
            raise RuntimeError("historical valid replay seed includes accepted parameters")
        if seed.get("qps_outer_only") is not True:
            raise RuntimeError("historical valid replay seed QPS role mismatch")
        declared_payload_sha = str(seed.get("payload_sha256") or "")
        payload_without_sha = copy.deepcopy(seed)
        payload_without_sha.pop("payload_sha256", None)
        if declared_payload_sha != canonical_sha256(payload_without_sha):
            raise RuntimeError("historical valid replay seed payload checksum mismatch")
        if seed.get("workload_contract_sha256") != self.workload_contract_sha256:
            raise RuntimeError("historical valid replay seed workload contract mismatch")
        if seed.get("target") != self.target:
            raise RuntimeError("historical valid replay seed target mismatch")
        source_artifact_sha = str(seed.get("experience_table_sha256") or "")
        current_artifact_sha = hashlib.sha256(
            self.t_artifact_path.read_bytes()
        ).hexdigest()
        if source_artifact_sha != current_artifact_sha:
            raise RuntimeError("historical valid replay seed predictor artifact mismatch")
        rows = seed.get("rows")
        if not isinstance(rows, list):
            raise RuntimeError("historical valid replay seed rows missing")
        baseline_by_worker = self.state.get("baseline_by_worker")
        if not isinstance(baseline_by_worker, Mapping):
            raise RuntimeError("fresh worker baselines missing before seed import")
        identity_memory = self.state.get("valid_replay_identity_memory")
        if not isinstance(identity_memory, dict):
            raise RuntimeError("fresh valid replay identity memory missing")

        imported = 0
        for position, seed_row in enumerate(rows, start=1):
            if not isinstance(seed_row, Mapping):
                raise RuntimeError(f"historical replay seed row {position} is invalid")
            validation = seed_row.get("validation")
            source_worker_baseline = seed_row.get("source_worker_baseline")
            source_canonical_baseline = seed_row.get("source_canonical_baseline")
            if not all(
                isinstance(value, Mapping)
                for value in (
                    validation,
                    source_worker_baseline,
                    source_canonical_baseline,
                )
            ):
                raise RuntimeError(
                    f"historical replay seed row {position} baseline evidence missing"
                )
            if not physical_valid(validation):
                raise RuntimeError(
                    f"historical replay seed row {position} is not physically valid"
                )
            candidate = validation.get("candidate_ranking")
            if not isinstance(candidate, Mapping):
                raise RuntimeError(
                    f"historical replay seed row {position} candidate missing"
                )
            key_worker = str(validation.get("worker_key") or "")
            fresh_worker_baseline = baseline_by_worker.get(key_worker)
            if not isinstance(fresh_worker_baseline, Mapping):
                raise RuntimeError(
                    f"historical replay seed worker is unavailable: {key_worker}"
                )
            source_normalized = normalize_to_worker_baseline(
                validation,
                source_worker_baseline,
                source_canonical_baseline,
                self.capacity,
            )
            projected = copy.deepcopy(dict(validation))
            projected["resources"] = {
                resource: (
                    float(fresh_worker_baseline["resources"][resource])
                    * float(source_normalized["resources"][resource])
                    / float(source_canonical_baseline["resources"][resource])
                )
                for resource in ("cpu", "memory", "io")
            }
            projected["db_qps"] = (
                float(fresh_worker_baseline["db_qps"])
                * float(source_normalized["db_qps"])
                / float(source_canonical_baseline["db_qps"])
            )
            projected["historical_valid_replay_seed"] = {
                "contract": HISTORICAL_VALID_REPLAY_SEED_CONTRACT,
                "source_campaign": seed.get("source_campaign"),
                "source_round_index": int(seed_row.get("source_round_index") or 0),
                "source_label": str(seed_row.get("source_label") or ""),
                "source_normalized_resources": copy.deepcopy(
                    source_normalized.get("resources") or {}
                ),
                "source_normalized_db_qps": source_normalized.get("db_qps"),
                "projection_uses_fresh_worker_baseline": True,
                "qps_outer_only": True,
                "physical_workload_invocations": 0,
                "valid_replays_added": 0,
            }
            update = record_valid_replay_identity(
                identity_memory,
                candidate=candidate,
                round_index=0,
                label=f"historical_seed_{position:03d}",
            )
            cached = self.cache_valid_replay_evidence(
                projected,
                round_index=0,
            )
            if update.get("recorded") is not True or cached.get("cached") is not True:
                raise RuntimeError(
                    f"historical replay seed row {position} identity import failed"
                )
            imported += 1
        receipt = {
            "imported": True,
            "contract": HISTORICAL_VALID_REPLAY_SEED_CONTRACT,
            "path": str(seed_path),
            "sha256": hashlib.sha256(seed_path.read_bytes()).hexdigest(),
            "row_count": len(rows),
            "imported_count": imported,
            "fresh_baseline_required": True,
            "accepted_inherited": False,
            "ranges_inherited": False,
            "gradient_state_inherited": False,
            "physical_workload_invocations": 0,
            "valid_replays_added": 0,
            "technical_failures_excluded": True,
            "qps_outer_only": True,
            "source_algorithm": "algorithm_c",
            "algorithm_a_or_b_runtime_evidence_used": False,
        }
        self.state["historical_valid_replay_seed_receipt"] = copy.deepcopy(receipt)
        self.save_state()
        self.log("algorithm_c_historical_valid_replay_seed_imported", **receipt)
        return receipt

    def cached_valid_replay_rows(
        self,
        candidates: Sequence[Dict[str, Any]],
        *,
        round_index: int,
        exclude_rows: Sequence[Mapping[str, Any]] = (),
        limit: int = BASE_TOP_K,
    ) -> List[Dict[str, Any]]:
        """Return up to Top-5 exact valid rows for tier-only re-decision.

        Candidates must still be present in the current ranking and inside the
        current retained ranges.  Thus cached evidence cannot resurrect an old
        point outside the current Algorithm C search interval.
        """
        identity_memory = self.state.get("valid_replay_identity_memory")
        if not isinstance(identity_memory, Mapping):
            return []
        excluded_keys = {
            valid_replay_identity_key(
                row.get("candidate_ranking") or {},
                workload_contract_sha256=self.workload_contract_sha256,
                target=self.target,
            )
            for row in exclude_rows
            if isinstance(row, Mapping)
        }
        selected: List[Dict[str, Any]] = []
        selected_keys: Set[str] = set()
        for candidate in candidates:
            if len(selected) >= max(0, int(limit)):
                break
            range_audit = parameter_vector_range_audit(
                candidate.get("optimized_params"),
                self.ranges,
            )
            if range_audit.get("passed") is not True:
                continue
            assessment = assess_valid_replay_identity(identity_memory, candidate)
            key = str(assessment.get("identity_key") or "")
            if (
                assessment.get("seen_valid_replay") is not True
                or not key
                or key in excluded_keys
                or key in selected_keys
            ):
                continue
            evidence = self.valid_replay_evidence_by_identity.get(key)
            validation = (
                evidence.get("validation")
                if isinstance(evidence, Mapping)
                else None
            )
            if not isinstance(validation, Mapping) or not physical_valid(validation):
                continue
            cached_row = copy.deepcopy(dict(validation))
            cached_row["candidate_ranking"] = copy.deepcopy(candidate)
            cached_row["cached_valid_replay_redecision"] = {
                "contract": VALID_REPLAY_EVIDENCE_REDECISION_CONTRACT,
                "identity_key": key,
                "source_round_index": int(evidence.get("round_index") or 0),
                "source_label": str(evidence.get("label") or ""),
                "decision_round_index": int(round_index),
                "physical_workload_invocations": 0,
                "valid_replays_added": 0,
                "technical_failures_excluded": True,
                "qps_outer_only": True,
                "inner_candidate_scores_modified": False,
            }
            cached_row["label"] = (
                f"cached_redecision_r{round_index:03d}_rank"
                f"{int(candidate.get('rank') or len(selected) + 1):03d}"
            )
            selected.append(cached_row)
            selected_keys.add(key)
        self.log(
            "algorithm_c_cached_valid_replay_redecision_admission",
            contract=VALID_REPLAY_EVIDENCE_REDECISION_CONTRACT,
            round_index=round_index,
            required_acceptance_tier=self.required_acceptance_tier(),
            current_ranking_count=len(candidates),
            cached_candidate_count=len(selected),
            physical_workload_invocations=0,
            valid_replays_added=0,
            technical_failures_excluded=True,
            qps_outer_only=True,
            inner_candidate_scores_modified=False,
            gradient_state_unchanged=True,
        )
        return selected

    def accepted_parameter_signatures(self) -> Set[Tuple[str, ...]]:
        """Return every parameter vector already used for accepted shrink.

        A historical physical result may become eligible after the incumbent
        or acceptance tier changes.  A point that already performed a shrink
        is different: accepting it again could cycle between old checkpoints
        and fabricate binary-search progress.  Reconstruct this immutable set
        from the authoritative accepted events and include the current
        incumbent defensively.
        """

        signatures: Set[Tuple[str, ...]] = set()
        initial_params = getattr(self, "initial_params", None)
        if isinstance(initial_params, Mapping):
            signatures.add(exact_param_signature(initial_params))
        incumbent = self.state.get("incumbent_params")
        if isinstance(incumbent, Mapping):
            signatures.add(exact_param_signature(incumbent))
        if not self.log_path.is_file():
            return signatures
        try:
            with self.log_path.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    try:
                        row = json.loads(raw_line)
                    except (TypeError, ValueError):
                        continue
                    if row.get("event") != "algorithm_c_round_accepted":
                        continue
                    params = row.get("params")
                    if isinstance(params, Mapping):
                        signatures.add(exact_param_signature(params))
        except OSError:
            return signatures
        return signatures

    def cached_historical_valid_replay_rows(
        self,
        *,
        round_index: int,
        exclude_rows: Sequence[Mapping[str, Any]] = (),
        limit: int = BASE_TOP_K,
    ) -> List[Dict[str, Any]]:
        """Select previously rejected exact points that pass today's Outer gate.

        Unlike :meth:`cached_valid_replay_rows`, this scans the entire valid
        same-campaign evidence pool rather than only candidates regenerated in
        the current ranking.  It remains an Outer-only re-decision: measured
        QPS/resources never enter prediction, gradient, score, or ranking;
        no physical/valid counters are incremented.  Current ranges and every
        historical accepted parameter vector are hard exclusions.
        """

        if max(0, int(limit)) == 0:
            return []
        baseline_by_worker = self.state.get("baseline_by_worker")
        canonical_baseline = self.state.get("canonical_baseline")
        if not isinstance(baseline_by_worker, Mapping) or not isinstance(
            canonical_baseline,
            Mapping,
        ):
            return []
        accepted_signatures = self.accepted_parameter_signatures()
        excluded_keys = {
            valid_replay_identity_key(
                row.get("candidate_ranking") or {},
                workload_contract_sha256=self.workload_contract_sha256,
                target=self.target,
            )
            for row in exclude_rows
            if isinstance(row, Mapping)
        }
        tier_rank = {"strict": 3, "secondary": 2, "fallback": 1}
        eligible: List[Tuple[Tuple[Any, ...], str, Dict[str, Any]]] = []
        exclusion_counts = {
            "technical_or_invalid": 0,
            "already_considered_this_round": 0,
            "already_accepted_parameter_vector": 0,
            "outside_current_range": 0,
            "transition_not_covered_by_active_subset": 0,
            "worker_baseline_missing": 0,
            "current_gate_not_passed": 0,
            "duplicate_parameter_vector": 0,
        }
        selected_signatures: Set[Tuple[str, ...]] = set()
        reference = self.state.get("incumbent_validation") or canonical_baseline
        for key, evidence in sorted(
            self.valid_replay_evidence_by_identity.items(),
            key=lambda item: (
                int((item[1] or {}).get("round_index") or 0),
                str((item[1] or {}).get("label") or ""),
                item[0],
            ),
        ):
            validation = (
                evidence.get("validation")
                if isinstance(evidence, Mapping)
                else None
            )
            if not isinstance(validation, Mapping) or not physical_valid(validation):
                exclusion_counts["technical_or_invalid"] += 1
                continue
            source_candidate = validation.get("candidate_ranking")
            if not isinstance(source_candidate, Mapping):
                exclusion_counts["technical_or_invalid"] += 1
                continue
            candidate = copy.deepcopy(dict(source_candidate))
            if key in excluded_keys:
                exclusion_counts["already_considered_this_round"] += 1
                continue
            signature = exact_param_signature(candidate.get("optimized_params") or {})
            if signature in accepted_signatures:
                exclusion_counts["already_accepted_parameter_vector"] += 1
                continue
            if signature in selected_signatures:
                exclusion_counts["duplicate_parameter_vector"] += 1
                continue
            if parameter_vector_range_audit(
                candidate.get("optimized_params"),
                self.ranges,
            ).get("passed") is not True:
                exclusion_counts["outside_current_range"] += 1
                continue
            candidate_params = candidate.get("optimized_params") or {}
            incumbent_params = self.state.get("incumbent_params") or {}
            active_subset = {
                str(name) for name in (candidate.get("active_subset") or [])
            }
            moved_from_current = {
                name
                for name in ACTIVE_PARAMS
                if not math.isclose(
                    float(candidate_params[name]),
                    float(incumbent_params[name]),
                    rel_tol=1e-12,
                    abs_tol=1e-15,
                )
            }
            is_seeded_checkpoint = isinstance(
                validation.get("historical_valid_replay_seed"),
                Mapping,
            )
            if not moved_from_current:
                exclusion_counts[
                    "transition_not_covered_by_active_subset"
                ] += 1
                continue
            if is_seeded_checkpoint:
                source_active_subset = sorted(active_subset)
                candidate["source_active_subset"] = source_active_subset
                candidate["active_subset"] = sorted(moved_from_current)
                candidate["initial_params"] = copy.deepcopy(dict(incumbent_params))
                candidate["candidate_origin"] = "historical_valid_checkpoint"
                candidate["probe_kind"] = "cached_joint_checkpoint_redecision"
                candidate["used_for_gradient"] = False
                candidate["gradient_state_unchanged"] = True
                candidate["historical_checkpoint_transition"] = {
                    "contract": HISTORICAL_VALID_REPLAY_SEED_CONTRACT,
                    "source_active_subset": source_active_subset,
                    "current_transition_subset": sorted(moved_from_current),
                    "joint_endpoint_has_valid_physical_replay": True,
                    "gradient_evidence_claimed": False,
                    "qps_outer_only": True,
                }
                candidate["delta_param"] = {
                    name: {
                        "before": float(incumbent_params[name]),
                        "after_clip": float(candidate_params[name]),
                        "absolute": (
                            float(candidate_params[name])
                            - float(incumbent_params[name])
                        ),
                        "historical_checkpoint_transition": True,
                    }
                    for name in ACTIVE_PARAMS
                }
            elif not moved_from_current.issubset(active_subset):
                exclusion_counts[
                    "transition_not_covered_by_active_subset"
                ] += 1
                continue
            key_worker = str(validation.get("worker_key") or "")
            worker_baseline = baseline_by_worker.get(key_worker)
            if not isinstance(worker_baseline, Mapping):
                exclusion_counts["worker_baseline_missing"] += 1
                continue
            normalized = normalize_to_worker_baseline(
                validation,
                worker_baseline,
                canonical_baseline,
                self.capacity,
            )
            normalized["qps_noise_model"] = copy.deepcopy(
                self.state.get("qps_noise_model") or {}
            )
            preview = outer_semantic_acceptance_details(
                normalized,
                reference,
                canonical_baseline,
                target=self.target,
                cfg=self.cfg,
                consecutive_missed_outer_rounds=int(
                    self.state.get("consecutive_missed_outer_rounds") or 0
                ),
            )
            if preview.get("accepted") is not True:
                exclusion_counts["current_gate_not_passed"] += 1
                continue
            cached_row = copy.deepcopy(dict(validation))
            cached_row["candidate_ranking"] = copy.deepcopy(dict(candidate))
            cached_row["cached_valid_replay_redecision"] = {
                "contract": VALID_REPLAY_EVIDENCE_REDECISION_CONTRACT,
                "pool_scope": (
                    "cross_campaign_seeded_valid_replays"
                    if is_seeded_checkpoint
                    else "all_same_campaign_valid_replays"
                ),
                "identity_key": key,
                "source_round_index": int(evidence.get("round_index") or 0),
                "source_label": str(evidence.get("label") or ""),
                "decision_round_index": int(round_index),
                "physical_workload_invocations": 0,
                "valid_replays_added": 0,
                "technical_failures_excluded": True,
                "qps_outer_only": True,
                "inner_candidate_scores_modified": False,
                "previously_accepted_parameter_vector": False,
            }
            cached_row["label"] = (
                f"cached_history_r{round_index:03d}_source_"
                f"r{int(evidence.get('round_index') or 0):03d}_"
                f"{len(eligible) + 1:03d}"
            )
            sort_key = (
                -tier_rank.get(str(preview.get("acceptance_tier") or ""), 0),
                -float(preview.get("target_drop_vs_reference") or -math.inf),
                -float(preview.get("qps_gate_gain") or -math.inf),
                int(evidence.get("round_index") or 0),
                str(evidence.get("label") or ""),
            )
            eligible.append((sort_key, key, cached_row))
            selected_signatures.add(signature)
        eligible.sort(key=lambda item: item[0])
        selected = [item[2] for item in eligible[: max(0, int(limit))]]
        self.log(
            "algorithm_c_historical_valid_replay_pool_admission",
            contract=VALID_REPLAY_EVIDENCE_REDECISION_CONTRACT,
            round_index=round_index,
            required_acceptance_tier=self.required_acceptance_tier(),
            evidence_pool_count=len(self.valid_replay_evidence_by_identity),
            currently_acceptable_count=len(eligible),
            selected_count=len(selected),
            selected_source_labels=[
                (row.get("cached_valid_replay_redecision") or {}).get(
                    "source_label"
                )
                for row in selected
            ],
            exclusion_counts=exclusion_counts,
            current_range_required=True,
            previously_accepted_parameter_vectors_excluded=True,
            physical_workload_invocations=0,
            valid_replays_added=0,
            technical_failures_excluded=True,
            qps_outer_only=True,
            inner_candidate_scores_modified=False,
            gradient_state_unchanged=True,
        )
        return selected

    def record_outer_pruning_evidence(
        self,
        *,
        candidate: Mapping[str, Any],
        decision: Mapping[str, Any],
        round_index: int,
    ) -> Dict[str, Any]:
        if self.outer_pruning_policy.get("enabled") is not True:
            return {"recorded": False, "reason": "disabled"}
        memory = self.state.setdefault(
            "outer_accept_reject_pruning",
            empty_outer_pruning_memory(),
        )
        context = self.current_outer_pruning_context(
            incumbent_params=(candidate.get("initial_params") or self.state.get("incumbent_params") or {}),
            required_tier=str(decision.get("required_acceptance_tier") or self.required_acceptance_tier()),
            qps_noise_model_id=decision.get("qps_noise_model_id"),
        )
        return record_outer_pruning_observation(
            memory,
            context=context,
            candidate=candidate,
            decision=decision,
            round_index=round_index,
        )

    def select_outer_admitted_candidates(
        self,
        candidates: Sequence[Dict[str, Any]],
        *,
        round_index: int,
        batch_kind: str,
        limit: int = BASE_TOP_K,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Hard-prune supported repeats and use prediction direction for ranking only.

        Predicted resource direction is model output, not authoritative replay
        evidence.  A predicted target increase may therefore move a candidate
        behind predicted-down candidates, but must never remove it from the
        physical Top-5 when it passes the real hard gates.
        """
        memory = self.state.get("outer_accept_reject_pruning")
        if not isinstance(memory, Mapping):
            memory = empty_outer_pruning_memory()
        context = self.current_outer_pruning_context()
        required_tier = str(context.get("required_acceptance_tier") or "fallback")
        # Target-down prediction remains a ranking preference because strict
        # and secondary outcomes outrank fallback in every batch.  It is not
        # a physical-admission gate: fallback candidates remain eligible.
        require_predicted_target_down = not is_none_target(self.target)
        eligible: List[
            Tuple[bool, bool, int, Dict[str, Any], Dict[str, Any]]
        ] = []
        exact_valid_replay_excluded: List[Dict[str, Any]] = []
        hard_pruned: List[Dict[str, Any]] = []
        numeric_refinement_hard_prune_bypassed: List[Dict[str, Any]] = []
        range_excluded: List[Dict[str, Any]] = []
        target_direction_deprioritized: List[Dict[str, Any]] = []
        identity_memory = self.state.get("valid_replay_identity_memory")
        if not isinstance(identity_memory, Mapping):
            identity_memory = empty_valid_replay_identity_memory(
                self.workload_contract_sha256,
                self.target,
            )
        for position, candidate in enumerate(candidates):
            identity_assessment = assess_valid_replay_identity(
                identity_memory,
                candidate,
            )
            if identity_assessment.get("seen_valid_replay") is True:
                exact_valid_replay_excluded.append(
                    {
                        "rank": candidate.get("rank"),
                        "optimized_params": copy.deepcopy(
                            candidate.get("optimized_params") or {}
                        ),
                        "full_plan_fingerprint": (
                            candidate_full_plan_fingerprint(candidate)
                        ),
                        "identity_assessment": identity_assessment,
                    }
                )
                continue
            global_coverage_rescue = bool(
                candidate.get("global_plan_coverage_rescue") is True
                and candidate.get("global_plan_coverage_contract")
                == GLOBAL_PLAN_COVERAGE_CONTRACT
            )
            range_audit = parameter_vector_range_audit(
                candidate.get("optimized_params"),
                self.ranges,
            )
            if range_audit.get("passed") is not True:
                range_excluded.append(
                    {
                        "rank": candidate.get("rank"),
                        "optimized_params": copy.deepcopy(
                            candidate.get("optimized_params") or {}
                        ),
                        "range_audit": range_audit,
                    }
                )
                continue
            direction_audit = predicted_target_direction_audit(
                candidate,
                self.target,
            )
            direction_deprioritize = (
                require_predicted_target_down
                and direction_audit.get("predicted_target_down") is not True
            )
            if direction_deprioritize:
                target_direction_deprioritized.append(
                    {
                        "rank": candidate.get("rank"),
                        "optimized_params": copy.deepcopy(
                            candidate.get("optimized_params") or {}
                        ),
                        "direction_audit": direction_audit,
                    }
                )
            assessment = assess_outer_pruning_candidate(
                memory,
                context=context,
                candidate=candidate,
                policy=self.outer_pruning_policy,
            )
            numeric_refinement_bypass = bool(
                candidate.get("numeric_refinement") is True
                and candidate.get("plan_space_saturation_required") is True
                and candidate.get("qps_used_for_inner_scoring") is False
            )
            if assessment.get("hard_prune") is True and numeric_refinement_bypass:
                assessment = copy.deepcopy(assessment)
                assessment["original_plan_level_hard_prune"] = True
                assessment["hard_prune"] = False
                assessment["soft_deprioritize"] = True
                assessment["reason"] = (
                    "new_exact_parameter_identity_requires_physical_numeric_refinement"
                )
                assessment[
                    "plan_level_hard_prune_bypassed_for_numeric_refinement"
                ] = True
            audit = {
                "rank": candidate.get("rank"),
                "optimized_params": copy.deepcopy(candidate.get("optimized_params") or {}),
                "assessment": assessment,
                "direction_audit": direction_audit,
                "predicted_target_direction_deprioritized": (
                    direction_deprioritize
                ),
                "global_plan_coverage_rescue": global_coverage_rescue,
                "numeric_refinement_hard_prune_bypass": (
                    numeric_refinement_bypass
                ),
                "admission_range_scope": (
                    "current_formal_interval_global_coverage"
                    if global_coverage_rescue
                    else "current_formal_interval"
                ),
            }
            if assessment.get("hard_prune") is True:
                hard_pruned.append(audit)
                continue
            if assessment.get(
                "plan_level_hard_prune_bypassed_for_numeric_refinement"
            ) is True:
                numeric_refinement_hard_prune_bypassed.append(audit)
            eligible.append(
                (
                    direction_deprioritize,
                    assessment.get("soft_deprioritize") is True,
                    position,
                    candidate,
                    audit,
                )
            )
        eligible.sort(key=lambda value: (value[0], value[1], value[2]))
        portfolio_selection = bool(plan_portfolio_policy(getattr(self, "algorithm_cfg", {})))
        portfolio_abstained = (
            portfolio_selection
            and (self.state.get("plan_portfolio") or {}).get("target_prediction_abstained") is True
        )
        if portfolio_selection:
            # Only candidate scheduling changes. All exclusions above and the
            # real three-tier acceptance below remain authoritative.
            ordered = plan_portfolio_order(
                [value[3] for value in eligible],
                seen_plans=(self.state.get("plan_portfolio") or {}).get("selection_seen_plans") or [],
                target_abstained=portfolio_abstained,
            )
            by_identity = {plan_portfolio_identity(value[3]["optimized_params"]): value for value in eligible}
            eligible = [by_identity[plan_portfolio_identity(row["optimized_params"])] for row in ordered]
        selected = [value[3] for value in eligible[:limit]]
        selected_audit = [value[4] for value in eligible[:limit]]
        audit_summary = {
            "contract": OUTER_PRUNING_CONTRACT,
            "round_index": round_index,
            "batch_kind": batch_kind,
            "context": context,
            "input_candidate_count": len(candidates),
            "exact_valid_replay_excluded_count": len(
                exact_valid_replay_excluded
            ),
            "exact_valid_replay_excluded": exact_valid_replay_excluded,
            "exact_valid_replay_identity_contract": (
                VALID_REPLAY_IDENTITY_CONTRACT
            ),
            "range_excluded_count": len(range_excluded),
            # Compatibility fields remain explicit: prediction direction is no
            # longer a gate and therefore excludes zero physical candidates.
            "target_direction_gate_required": False,
            "target_direction_excluded_count": 0,
            "target_direction_ranking_preference_enabled": (
                require_predicted_target_down
            ),
            "target_direction_deprioritized_count": len(
                target_direction_deprioritized
            ),
            "hard_pruned_count": len(hard_pruned),
            "numeric_refinement_hard_prune_bypassed_count": len(
                numeric_refinement_hard_prune_bypassed
            ),
            "soft_deprioritized_count": sum(value[1] for value in eligible),
            "selected_count": len(selected),
            "hard_pruned": hard_pruned,
            "numeric_refinement_hard_prune_bypassed": (
                numeric_refinement_hard_prune_bypassed
            ),
            "range_excluded": range_excluded,
            "target_direction_excluded": [],
            "target_direction_deprioritized": target_direction_deprioritized,
            "selected": selected_audit,
            "inner_candidate_scores_modified": False,
            "plan_portfolio_3_quality_1_plan_1_coverage": portfolio_selection and not portfolio_abstained,
            "plan_portfolio_1_quality_2_plan_2_coverage": portfolio_abstained,
            "qps_used_for_inner_scoring": False,
            "technical_failures_used_as_rules": False,
            "technical_failures_used_as_valid_replay_identities": False,
        }
        self.log("algorithm_c_outer_replay_admission", **audit_summary)
        return selected, audit_summary

    def prepare_parallel_baseline_workers(
        self,
        repetition: int,
    ) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
        """Return two clean workers, reusing only a verified one-shot handoff."""
        receipt = getattr(self, "prepared_clean_handoff_receipt", None)
        receipt_rows = receipt.get("workers") if isinstance(receipt, Mapping) else None
        if repetition == 1 and isinstance(receipt_rows, Sequence):
            by_worker = {
                str(row.get("worker") or ""): row
                for row in receipt_rows
                if isinstance(row, Mapping)
            }
            prepared: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
            for worker in self.workers:
                key = worker_key(worker)
                row = by_worker.get(key)
                restore = row.get("restore") if isinstance(row, Mapping) else None
                readback = (
                    row.get("baseline_parameter_readback")
                    if isinstance(row, Mapping)
                    else None
                )
                if (
                    not isinstance(restore, Mapping)
                    or restore.get("status") != "ok"
                    or not isinstance(readback, Mapping)
                    or readback.get("passed") is not True
                ):
                    prepared = []
                    break
                prepared.append((worker, copy.deepcopy(dict(restore))))
            # A receipt is one-shot even when malformed; falling back to a
            # fresh restore is safer than trying to interpret it again.
            self.prepared_clean_handoff_receipt = None
            if len(prepared) == len(self.workers):
                self.log(
                    "algorithm_c_formal_baseline_clean_handoff_reused",
                    repetition=repetition,
                    worker_count=len(prepared),
                    source_reason=receipt.get("reason"),
                    source_completed_at_epoch_s=receipt.get(
                        "completed_at_epoch_s"
                    ),
                    physical_workload_invocations=0,
                    no_workload_since_restore=True,
                    no_parameter_mutation_since_restore=True,
                    no_database_mutation_since_restore=True,
                )
                return prepared

        def restore_and_verify(
            worker: Dict[str, Any],
        ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
            restore = restore_worker_from_hybench_template(
                self.cfg,
                worker,
                label=(
                    f"algorithm_c_baseline_rep{repetition}_"
                    f"{worker_key(worker)}"
                ),
            )
            if restore.get("status") != "ok":
                raise RuntimeError(f"baseline restore failed: {restore}")
            readback = apply_pg_params(
                pg_conn_from_worker(self.cfg, worker),
                self.initial_params,
            )
            readback_audit = baseline_parameter_readback_audit(
                self.initial_params,
                readback,
            )
            if readback_audit.get("passed") is not True:
                raise RuntimeError(
                    "baseline restore parameter readback failed: "
                    f"{worker_key(worker)} {readback_audit}"
                )
            return worker, restore

        with ThreadPoolExecutor(max_workers=self.replay_parallelism) as executor:
            futures = [
                executor.submit(restore_and_verify, worker)
                for worker in self.workers
            ]
            prepared = [future.result() for future in futures]
        self.log(
            "algorithm_c_formal_baseline_parallel_restore",
            repetition=repetition,
            worker_count=len(prepared),
            restore_parallelism=self.replay_parallelism,
        )
        return prepared

    def baseline(self) -> None:
        existing_baselines = self.state.get("baseline_by_worker")
        if isinstance(existing_baselines, Mapping) and existing_baselines:
            configured_keys = {worker_key(worker) for worker in self.workers}
            baseline_keys = {str(key) for key in existing_baselines}
            if baseline_keys != configured_keys:
                transitioned = self.prepare_worker_execution_cohort_transition(
                    baseline_keys=baseline_keys,
                    configured_keys=configured_keys,
                )
                existing_baselines = self.state.get("baseline_by_worker")
                if transitioned and not existing_baselines:
                    existing_baselines = None
                elif transitioned and isinstance(existing_baselines, Mapping):
                    transitioned_keys = {str(key) for key in existing_baselines}
                    if transitioned_keys != configured_keys:
                        raise RuntimeError(
                            "audited worker cohort transition produced an "
                            "incomplete baseline map: "
                            f"configured={sorted(configured_keys)} "
                            f"baseline={sorted(transitioned_keys)}"
                        )
                else:
                    raise RuntimeError(
                        "configured worker cohort differs from frozen baseline workers; "
                        "an audited round-boundary execution-domain transition is required: "
                        f"configured={sorted(configured_keys)} "
                        f"baseline={sorted(baseline_keys)}"
                    )
            if not existing_baselines:
                # The derivative archived the prior domain and explicitly
                # requested a fresh baseline below.
                pass
            else:
                cohort = self.worker_execution_cohort_audit()
                frozen_cohort = self.state.get("worker_execution_cohort")
                if isinstance(frozen_cohort, Mapping):
                    if str(frozen_cohort.get("cohort_sha256") or "") != str(
                        cohort["cohort_sha256"]
                    ):
                        legacy_cohort = copy.deepcopy(cohort)
                        legacy_cohort.pop("cohort_sha256", None)
                        legacy_cohort.pop("global_restore_parallelism", None)
                        legacy_cohort["cohort_sha256"] = canonical_sha256(
                            legacy_cohort
                        )
                        baseline_restore_parallelism = {
                            int(
                                ((row.get("restore") or {}).get(
                                    "global_restore_parallelism"
                                ))
                            )
                            for row in (
                                self.state.get("baseline_replay_rows") or []
                            )
                            if isinstance(row, Mapping)
                            and isinstance(row.get("restore"), Mapping)
                            and (row.get("restore") or {}).get(
                                "global_restore_parallelism"
                            )
                            is not None
                        }
                        current_restore_parallelism = int(
                            cohort["global_restore_parallelism"]
                        )
                        if (
                            str(frozen_cohort.get("cohort_sha256") or "")
                            == str(legacy_cohort["cohort_sha256"])
                            and baseline_restore_parallelism
                            == {current_restore_parallelism}
                        ):
                            prior_sha256 = str(
                                frozen_cohort.get("cohort_sha256") or ""
                            )
                            self.state["worker_execution_cohort"] = (
                                copy.deepcopy(cohort)
                            )
                            self.save_state()
                            self.log(
                                "algorithm_c_legacy_restore_parallelism_"
                                "execution_cohort_enriched",
                                prior_cohort_sha256=prior_sha256,
                                worker_execution_cohort=cohort,
                                baseline_restore_parallelism=(
                                    current_restore_parallelism
                                ),
                                physical_workload_invocations=0,
                                semantic_state_mutation=False,
                            )
                        else:
                            raise RuntimeError(
                                "configured worker execution domain differs "
                                "from the frozen cohort"
                            )
                else:
                    self.state["worker_execution_cohort"] = copy.deepcopy(cohort)
                    self.save_state()
                    self.log(
                        "algorithm_c_legacy_worker_execution_cohort_frozen",
                        worker_execution_cohort=cohort,
                        physical_workload_invocations=0,
                        semantic_state_mutation=False,
                    )
                return
        if self.stop_requested:
            return
        repetition_policy = (
            (self.algorithm_cfg.get("budgeted_trial") or {}).get("baseline_repetition_policy") or {}
        )
        repetitions_per_worker = int(repetition_policy.get("repetitions_per_worker") or 2)
        burn_in_repetitions = int(repetition_policy.get("burn_in_repetitions") or 0)
        if repetitions_per_worker not in {2, 3} or burn_in_repetitions != repetitions_per_worker - 2:
            raise ValueError("baseline requires exactly two final measured repetitions")
        pending_context = {
            "contract": "algorithm_c_crash_resumable_parallel_baseline_v1",
            "worker_keys": sorted(worker_key(worker) for worker in self.workers),
            "repetitions_per_worker": repetitions_per_worker,
            "burn_in_repetitions": burn_in_repetitions,
            "workload_contract_sha256": self.workload_contract_sha256,
            "global_restore_parallelism": max(
                1,
                safe_int(
                    os.environ.get("PG_ALGORITHM_C_GLOBAL_RESTORE_PARALLELISM"),
                    2,
                ),
            ),
        }
        stored_pending_rows = self.state.get("baseline_pending_rows")
        stored_pending_context = self.state.get("baseline_pending_context")
        if stored_pending_rows is not None:
            if not isinstance(stored_pending_rows, list):
                raise RuntimeError("baseline_pending_rows must be a list")
            if stored_pending_context != pending_context:
                raise RuntimeError(
                    "pending baseline execution domain differs from configured cohort"
                )
            rows: List[Dict[str, Any]] = copy.deepcopy(stored_pending_rows)
        else:
            rows = []
            self.state["baseline_pending_rows"] = []
            self.state["baseline_pending_context"] = copy.deepcopy(
                pending_context
            )
        expected_worker_keys = set(pending_context["worker_keys"])
        seen_pending_slots: set[Tuple[int, str]] = set()
        for row in rows:
            if not isinstance(row, Mapping):
                raise RuntimeError("pending baseline row is not an object")
            repetition = int(row.get("baseline_repetition") or 0)
            key = str(row.get("worker_key") or "")
            slot = (repetition, key)
            if (
                repetition not in range(1, repetitions_per_worker + 1)
                or key not in expected_worker_keys
                or slot in seen_pending_slots
            ):
                raise RuntimeError(
                    f"invalid or duplicate pending baseline slot: {slot}"
                )
            seen_pending_slots.add(slot)
        self.state["phase"] = "fresh_parallel_baseline"
        self.save_state()
        self.log(
            "algorithm_c_parallel_baseline_start",
            physical_batch_limit=max(BASE_TOP_K, self.replay_parallelism),
            planned_physical_invocations=(repetitions_per_worker * len(self.workers) - len(rows)),
            replay_parallelism=self.replay_parallelism,
            repetitions_per_worker=repetitions_per_worker,
            burn_in_repetitions=burn_in_repetitions,
            resumed_completed_row_count=len(rows),
            crash_resumable_baseline=True,
        )
        # Every pending row was checkpointed atomically with its physical
        # accounting.  Starting the cursor at len(rows) prevents a resumed
        # controller from counting those invocations twice.
        accounted_row_count = len(rows)

        def account_completed_rows() -> Dict[str, int]:
            nonlocal accounted_row_count
            accounted_row_count, counts = account_new_physical_rows(
                self.state,
                rows,
                accounted_row_count,
            )
            return counts

        for repetition in range(1, repetitions_per_worker + 1):
            resumed_wave_rows = [
                row
                for row in rows
                if int(row.get("baseline_repetition") or 0) == repetition
            ]
            if resumed_wave_rows:
                resumed_keys = {
                    str(row.get("worker_key") or "")
                    for row in resumed_wave_rows
                }
                if (
                    len(resumed_wave_rows) != len(self.workers)
                    or resumed_keys != expected_worker_keys
                ):
                    raise RuntimeError(
                        "pending baseline contains an incomplete physical wave"
                    )
                self.log(
                    "algorithm_c_baseline_wave_reused_from_checkpoint",
                    repetition=repetition,
                    completed_row_count=len(resumed_wave_rows),
                    physical_workload_invocations=0,
                    technical_failures=0,
                    formal_state_mutation=False,
                )
                continue
            if self.stop_requested:
                counts = account_completed_rows()
                self.state["phase"] = "baseline_stop_pending_clean_restore"
                self.log(
                    "algorithm_c_baseline_partial_accounted",
                    stop_boundary="before_repetition_restore",
                    completed_row_count=len(rows),
                    newly_accounted=counts,
                )
                self.save_state()
                return
            self.physical_wave_guard_event("before_restore", f"baseline_{repetition:02d}")
            prepared_workers = self.prepare_parallel_baseline_workers(repetition)
            self.physical_wave_guard_event("after_restore", None)
            if self.stop_requested:
                counts = account_completed_rows()
                self.state["phase"] = "baseline_stop_pending_clean_restore"
                self.log(
                    "algorithm_c_baseline_partial_accounted",
                    stop_boundary="after_restore_before_replay",
                    completed_row_count=len(rows),
                    newly_accounted=counts,
                )
                self.save_state()
                return
            cohort_barrier = wait_for_algorithm_c_baseline_cohort(
                scene=str(self.algorithm_cfg.get("scene") or self.target),
                repetition=repetition,
                stop_requested=lambda: self.stop_requested,
            )
            self.log(
                "algorithm_c_baseline_cohort_barrier",
                repetition=repetition,
                cohort_barrier=cohort_barrier,
                physical_workload_invocations=0,
                formal_state_mutation=False,
            )
            wave_rows: List[Dict[str, Any]] = []
            try:
                workload_lease, workload_lease_wait_s = (
                    acquire_configured_shared_ssd_workload_coordination(
                        cancel_requested=lambda: self.stop_requested,
                    )
                )
            except RuntimeError:
                if self.stop_requested:
                    counts = account_completed_rows()
                    self.state["phase"] = "baseline_stop_pending_clean_restore"
                    self.log(
                        "algorithm_c_baseline_stop_while_waiting_workload_lease",
                        repetition=repetition,
                        completed_row_count=len(rows),
                        newly_accounted=counts,
                    )
                    self.save_state()
                    return
                raise
            self.log(
                "algorithm_c_workload_lease_acquired",
                phase="formal_baseline",
                repetition=repetition,
                wait_s=workload_lease_wait_s,
                physical_workload_invocations=0,
                formal_state_mutation=False,
            )
            try:
                with ThreadPoolExecutor(max_workers=self.replay_parallelism) as executor:
                    futures = {}
                    for slot, (worker, restore) in enumerate(prepared_workers, start=1):
                        label = (
                            f"algorithm_c_baseline_w{slot:02d}_rep{repetition:02d}_"
                            f"{worker_key(worker)}"
                        )
                        futures[
                            executor.submit(
                                run_real_validation_safe,
                                self.cfg,
                                self.initial_params,
                                output_dir=self.output_dir / "baseline" / worker_key(worker),
                                label=label,
                                worker=worker_with_prepared_restore(worker),
                                force_new_replay=True,
                            )
                        ] = (worker, restore)
                    for future in as_completed(futures):
                        worker, restore = futures[future]
                        row = future.result()
                        row["event"] = "algorithm_c_baseline_worker_validation"
                        row["worker_key"] = worker_key(worker)
                        row["baseline_repetition"] = repetition
                        row["restore"] = restore
                        wave_rows.append(row)
            finally:
                release_shared_ssd_coordination(workload_lease)
            self.physical_wave_guard_event("after_replay", wave_rows)
            qualification = apply_ssd_wave_qualification(
                wave_rows,
                target=self.target,
                phase="formal_baseline",
                cfg=self.cfg,
            )
            completion_barrier = (
                wait_for_algorithm_c_baseline_cohort_completion(
                    scene=str(self.algorithm_cfg.get("scene") or self.target),
                    repetition=repetition,
                    wave_valid=bool(
                        qualification.get("passed") is True
                        and len(wave_rows) == len(self.workers)
                        and all(physical_valid(row) for row in wave_rows)
                    ),
                    stop_requested=lambda: self.stop_requested,
                )
            )
            for row in wave_rows:
                row["baseline_cohort_completion_barrier"] = copy.deepcopy(
                    completion_barrier
                )
            if completion_barrier.get("passed") is not True:
                qualification["passed"] = False
                qualification["errors"] = list(
                    qualification.get("errors") or []
                ) + ["baseline_cohort_completion_domain_failed"]
                qualification["qualification_id"] = canonical_sha256(
                    {
                        key: value
                        for key, value in qualification.items()
                        if key != "qualification_id"
                    }
                )
            self.log(
                "algorithm_c_baseline_cohort_completion_barrier",
                repetition=repetition,
                completion_barrier=completion_barrier,
                physical_workload_invocations=0,
                formal_state_mutation=False,
                next_restore_blocked_until_all_lanes_complete=True,
            )
            self.log(
                "algorithm_c_ssd_wave_qualification",
                phase="formal_baseline",
                repetition=repetition,
                qualification=qualification,
            )
            for row in wave_rows:
                rows.append(row)
                self.log("algorithm_c_baseline_worker_validation", validation=row)
            counts = account_completed_rows()
            self.state["baseline_pending_rows"] = copy.deepcopy(rows)
            self.state["baseline_pending_context"] = copy.deepcopy(
                pending_context
            )
            self.save_state()
            self.log(
                "algorithm_c_baseline_wave_checkpointed",
                repetition=repetition,
                completed_row_count=len(rows),
                newly_accounted=counts,
                physical_workload_invocations=0,
                formal_state_mutation=False,
            )
            if self.stop_requested:
                counts = account_completed_rows()
                self.state["phase"] = "baseline_stop_pending_clean_restore"
                self.log(
                    "algorithm_c_baseline_partial_accounted",
                    stop_boundary="after_physical_wave",
                    completed_row_count=len(rows),
                    newly_accounted=counts,
                )
                self.save_state()
                return
        account_completed_rows()
        valid_rows = [row for row in rows if physical_valid(row)]
        expected_valid_rows = repetitions_per_worker * len(self.workers)
        if len(valid_rows) != expected_valid_rows:
            self.state["phase"] = "baseline_technical_failure"
            self.save_state()
            self.restore_clean_baseline(reason="baseline_technical_failure")
            raise RuntimeError(
                "fresh parallel baseline did not yield the required workload controls: "
                f"valid={len(valid_rows)} expected={expected_valid_rows}"
            )
        measured_rows, burn_in_rows = partition_baseline_controls(
            valid_rows,
            worker_keys=sorted(worker_key(worker) for worker in self.workers),
            repetitions_per_worker=repetitions_per_worker,
            burn_in_repetitions=burn_in_repetitions,
        )
        worker_rows = {
            key: [row for row in measured_rows if str(row["worker_key"]) == key]
            for key in sorted({str(row["worker_key"]) for row in measured_rows})
        }
        baseline_by_worker = {
            key: aggregate_parallel_baseline_validation(group)
            for key, group in worker_rows.items()
        }
        for key, row in baseline_by_worker.items():
            row["worker_key"] = key
            row["label"] = f"algorithm_c_repeated_baseline_{key}"
        canonical = aggregate_baseline(list(baseline_by_worker.values()), self.capacity)
        canonical["label"] = "algorithm_c_canonical_parallel_baseline"
        cohort_cfg = algorithm_c_cohort_config_from_env()
        all_replay_execution_domain = baseline_execution_domain_audit(
            valid_rows,
            canonical,
            expected_no_mutex=explicit_user_authorized_no_mutex_from_env(),
            cohort=cohort_cfg,
            allow_single_lane_no_mutex=(
                algorithm_a_single_lane_no_mutex_authorized(
                    self.algorithm_cfg,
                    cohort_cfg,
                )
                or guarded_c_single_lane_authorized(self.algorithm_cfg)
            ),
        )
        if all_replay_execution_domain.get("passed") is not True:
            self.state["phase"] = "baseline_execution_domain_failed_clean"
            self.state["baseline_all_replay_execution_domain"] = all_replay_execution_domain
            self.save_state()
            self.restore_clean_baseline(reason="baseline_execution_domain_failed")
            raise RuntimeError(
                "baseline execution domain failed: "
                f"{all_replay_execution_domain.get('errors')}"
            )
        # The frozen domain must cover exactly the scoring rows: later formal
        # verification reads baseline_replay_rows, which excludes burn-in.
        execution_domain = baseline_execution_domain_audit(
            measured_rows,
            canonical,
            expected_no_mutex=explicit_user_authorized_no_mutex_from_env(),
            cohort=cohort_cfg,
            allow_single_lane_no_mutex=(
                algorithm_a_single_lane_no_mutex_authorized(
                    self.algorithm_cfg,
                    cohort_cfg,
                )
                or guarded_c_single_lane_authorized(self.algorithm_cfg)
            ),
        )
        if execution_domain.get("passed") is not True:
            self.state["phase"] = "baseline_execution_domain_failed_clean"
            self.state["baseline_all_replay_execution_domain"] = all_replay_execution_domain
            self.state["baseline_execution_domain"] = execution_domain
            self.save_state()
            self.restore_clean_baseline(reason="baseline_execution_domain_failed")
            raise RuntimeError(
                "scored baseline execution domain failed: "
                f"{execution_domain.get('errors')}"
            )
        static_floor_pct = configured_static_qps_floor_pct(self.cfg)
        noise_cfg = self.algorithm_cfg.get("qps_noise_gate") or {}
        noise_model = qps_noise_model(
            measured_rows,
            static_floor_pct=static_floor_pct,
            confidence_z=float(noise_cfg.get("confidence_z") or 1.645),
            maximum_worker_repeat_ratio=float(
                noise_cfg.get("maximum_worker_repeat_ratio") or 1.25
            ),
        )
        if noise_model.get("ready") is not True and noise_cfg.get("enabled") is not False:
            self.state["phase"] = "qps_noise_model_failed_clean"
            self.state["qps_noise_model"] = noise_model
            self.save_state()
            self.restore_clean_baseline(reason="qps_noise_model_failed")
            raise RuntimeError(f"QPS noise model failed: {noise_model.get('errors')}")
        telemetry_model = executor_telemetry_baseline_model(measured_rows)
        if telemetry_model.get("ready") is not True:
            self.state["phase"] = "executor_telemetry_baseline_failed_clean"
            self.state["executor_telemetry_baseline_model"] = telemetry_model
            self.save_state()
            self.restore_clean_baseline(reason="executor_telemetry_baseline_failed")
            raise RuntimeError(
                f"executor telemetry baseline failed: {telemetry_model.get('errors')}"
            )
        self.state["baseline_replay_rows"] = copy.deepcopy(measured_rows)
        self.state["baseline_burn_in_rows"] = copy.deepcopy(burn_in_rows)
        self.state["baseline_repetition_policy"] = {
            "contract": "algorithm_c_fixed_predeclared_baseline_burn_in_v1",
            "repetitions_per_worker": repetitions_per_worker,
            "burn_in_repetitions": burn_in_repetitions,
            "measured_repetitions": list(range(burn_in_repetitions + 1, repetitions_per_worker + 1)),
            "burn_in_qps_used_for_baseline_or_candidate_scoring": False,
            "selection_based_on_observed_qps": False,
        }
        self.state["baseline_by_worker"] = baseline_by_worker
        self.state["canonical_baseline"] = canonical
        self.state["incumbent_validation"] = copy.deepcopy(canonical)
        self.state["qps_noise_model"] = noise_model
        self.state["executor_telemetry_baseline_model"] = telemetry_model
        self.state["baseline_all_replay_execution_domain"] = all_replay_execution_domain
        self.state["baseline_execution_domain"] = execution_domain
        self.state["worker_execution_cohort"] = self.worker_execution_cohort_audit()
        self.state.pop("baseline_pending_rows", None)
        self.state.pop("baseline_pending_context", None)
        self.state["phase"] = "baseline_ready"
        self.save_state()
        self.log("algorithm_c_qps_noise_model_frozen", qps_noise_model=noise_model)
        self.log(
            "algorithm_c_executor_telemetry_baseline_frozen",
            executor_telemetry_baseline_model=telemetry_model,
            qps_used_for_resource_prediction=False,
        )
        self.log(
            "algorithm_c_baseline_execution_domain_frozen",
            baseline_execution_domain=execution_domain,
            qps_and_resource_comparison_domain_frozen=True,
        )

    def restore_clean_baseline(self, *, reason: str) -> Dict[str, Any]:
        rows: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=self.replay_parallelism) as executor:
            future_map = {
                executor.submit(
                    restore_worker_from_hybench_template,
                    self.cfg,
                    worker,
                    label=f"algorithm_c_restore_{reason}_{worker_key(worker)}",
                ): worker
                for worker in self.workers
            }
            for future in as_completed(future_map):
                worker = future_map[future]
                try:
                    row = future.result()
                except Exception as exc:
                    rows.append(
                        {
                            "worker": worker_key(worker),
                            "restore": {
                                "status": "error",
                                "error_type": type(exc).__name__,
                                "error": str(exc),
                            },
                            "baseline_params_applied": False,
                            "parameter_readback": {
                                "passed": False,
                                "reason": "restore_raised_before_parameter_readback",
                            },
                        }
                    )
                    continue
                if row.get("status") != "ok":
                    rows.append(
                        {
                            "worker": worker_key(worker),
                            "restore": row,
                            "baseline_params_applied": False,
                            "parameter_readback": {
                                "passed": False,
                                "reason": "restore_status_not_ok",
                            },
                        }
                    )
                    continue
                try:
                    readback = apply_pg_params(
                        pg_conn_from_worker(self.cfg, worker),
                        self.initial_params,
                    )
                    readback_audit = baseline_parameter_readback_audit(
                        self.initial_params,
                        readback,
                    )
                except Exception as exc:
                    readback_audit = {
                        "passed": False,
                        "reason": "baseline_parameter_apply_or_readback_failed",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                rows.append(
                    {
                        "worker": worker_key(worker),
                        "restore": row,
                        "baseline_params_applied": (
                            readback_audit.get("passed") is True
                        ),
                        "parameter_readback": readback_audit,
                    }
                )
        passed = bool(
            len(rows) == len(self.workers)
            and all(
                item.get("baseline_params_applied") is True
                and (item.get("restore") or {}).get("status") == "ok"
                and (item.get("parameter_readback") or {}).get("passed") is True
                for item in rows
            )
        )
        self.log(
            "algorithm_c_clean_baseline_restored",
            reason=reason,
            passed=passed,
            workers=rows,
        )
        if not passed:
            self.state["restore_technical_failures"] = int(
                self.state.get("restore_technical_failures") or 0
            ) + 1
            self.state["last_restore_failure"] = {
                "reason": reason,
                "workers": copy.deepcopy(rows),
            }
            self.state["phase"] = "clean_restore_technical_failure"
            self.save_state()
            raise RuntimeError(
                f"clean baseline restore/readback failed: reason={reason} rows={rows}"
            )
        return {
            "reason": reason,
            "completed_at_epoch_s": time.time(),
            "workers": copy.deepcopy(rows),
            "passed": True,
        }

    def finish_signal_stop(self, reason: str) -> None:
        """Persist the stopped-clean terminal state only after restore succeeds."""

        interrupted_phase = str(self.state.get("phase") or "")
        if interrupted_phase in UNFINISHED_OUTER_ROUND_PHASES:
            self.state["signal_stop_interrupted_phase"] = interrupted_phase
            self.state["signal_stop_interrupted_round_index"] = int(
                self.state.get("round_index") or 0
            )
        self.state["phase"] = "signal_stop_pending_clean_restore"
        self.save_state()
        self.log("algorithm_c_signal_stop_restore_start", reason=reason)
        try:
            receipt = self.restore_clean_baseline(reason=reason)
        except Exception as exc:
            self.state["phase"] = "signal_stop_clean_restore_failed"
            self.state["last_signal_stop_restore_failure"] = {
                "reason": reason,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            self.save_state()
            self.log(
                "algorithm_c_signal_stop_restore_failed",
                reason=reason,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise
        self.state["signal_stop_clean_receipt"] = copy.deepcopy(receipt)
        self.state["phase"] = "stopped_by_signal_clean"
        self.save_state()
        self.log(
            "algorithm_c_signal_stop_complete",
            reason=reason,
            clean_receipt=receipt,
        )

    def prediction_config(self) -> Dict[str, Any]:
        """Return the immutable formal predictor config with baseline cache context."""
        cfg = copy.deepcopy(self.cfg)
        telemetry_model = self.state.get("executor_telemetry_baseline_model") or {}
        derived = telemetry_model.get("derived") if isinstance(telemetry_model, Mapping) else {}
        cache_hit_rate = (
            derived.get("shared_cache_hit_rate")
            if isinstance(derived, Mapping)
            else None
        )
        if not isinstance(cache_hit_rate, (int, float)) or not math.isfinite(
            float(cache_hit_rate)
        ):
            raise RuntimeError("frozen baseline cache-hit calibration is unavailable")
        bpt = cfg.setdefault("binary_plan_tuning", {})
        context = bpt.setdefault("workload_context", {})
        context["cache_hit_rate"] = min(1.0, max(0.0, float(cache_hit_rate)))
        context["executor_telemetry_calibration_id"] = telemetry_model.get("model_id")
        context["qps_used_for_resource_prediction"] = False
        return cfg

    def inner_scoring_reference_resources(
        self,
        measured_incumbent_resources: Mapping[str, float],
    ) -> Dict[str, float]:
        """Return the QPS-blind resource reference used only by inner scoring.

        The default controller keeps the measured incumbent resources. Formal
        derivatives may override this hook with a sealed resource-only prior;
        physical replay validation must continue to use the measured values.
        """

        return {
            name: float(measured_incumbent_resources[name])
            for name in ("cpu", "memory", "io")
        }

    def queries_and_probe(
        self,
        incumbent_params: Mapping[str, float],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
        predictor_cfg = self.prediction_config()
        probe = PlanFeatureProbe(copy.deepcopy(predictor_cfg))
        queries, sampling = workload_queries_with_sampling(predictor_cfg, probe)
        expected_count = int(self.algorithm_cfg.get("expected_plan_query_count") or 0)
        if sampling.get("sampling_applied"):
            raise RuntimeError(f"formal Algorithm C forbids plan query sampling: {sampling}")
        if len(queries) != expected_count:
            raise RuntimeError(
                f"plan workload coverage mismatch: expected={expected_count} actual={len(queries)}"
            )
        baseline_probe = probe.probe(queries, dict(incumbent_params))
        coverage = (baseline_probe.get("exact_plan_metadata") or {}).get("plan_fingerprint_coverage") or {}
        if coverage.get("complete") is not True:
            raise RuntimeError(f"baseline exact plan coverage incomplete: {coverage}")
        self.log(
            "algorithm_c_plan_coverage",
            sampling=sampling,
            exact_plan_coverage=coverage,
            plan_fingerprint=(baseline_probe.get("exact_plan_metadata") or {}).get("workload_plan_fingerprint"),
        )
        return queries, baseline_probe, predictor_cfg

    def generate_rankings(
        self,
        round_index: int,
        *,
        parameter_ranges: Optional[Mapping[str, ParameterRange]] = None,
        ranking_scope: str = "incumbent_ranges",
        relative_step_probe_pct: Optional[Sequence[float]] = None,
    ) -> List[Dict[str, Any]]:
        if plan_portfolio_policy(getattr(self, "algorithm_cfg", {})):
            from shell.pg_algorithm_c_portfolio_adapter import generate
            return generate(self, round_index)
        incumbent_params = {
            name: float(value) for name, value in self.state["incumbent_params"].items()
        }
        incumbent_validation = self.state["incumbent_validation"] or self.state["canonical_baseline"]
        queries, baseline_probe, predictor_cfg = self.queries_and_probe(incumbent_params)
        measured_incumbent_resources = {
            name: float((incumbent_validation.get("resources") or {})[name])
            for name in ("cpu", "memory", "io")
        }
        baseline_real = self.inner_scoring_reference_resources(
            measured_incumbent_resources
        )

        def factory(_subset: Tuple[str, ...], _subset_id: str) -> RatioScaleEstimator:
            return RatioScaleEstimator(
                predictor_cfg,
                queries,
                baseline_probe,
                baseline_real,
                incumbent_params,
                self.experience_artifact["resource_prediction_calibration"],
            )

        probe_steps = tuple(
            float(value)
            for value in (
                relative_step_probe_pct
                if relative_step_probe_pct is not None
                else DEFAULT_GRADIENT_PROBE_STEP_PCT
            )
        )
        options = {
            "eta": 0.2,
            "max_iter": len(probe_steps),
            "patience": 3,
            "gamma": 0.5,
            "relative_step_probe_pct": list(probe_steps),
            "boundary_aware_eta_enabled": False,
            "plateau_exploration_enabled": False,
            "target_bottleneck_resources": (
                [] if is_none_target(self.target) else [self.target]
            ),
            "active_bottleneck_underuse_scale": 0.0,
            "overload_threshold": 0.8,
            "verbose": False,
            **formal_inner_scoring_options(self.target),
        }
        if is_none_target(self.target):
            options["directional_scoring_contract_required"] = False
        selected_ranges = parameter_ranges or self.ranges
        summary = optimize_all_experience_table_subsets(
            resource_estimation_factory=factory,
            capability=copy.deepcopy(self.capacity),
            p_real=incumbent_params,
            sensitivity_table=copy.deepcopy(self.experience_table),
            param_ranges=copy.deepcopy(dict(selected_ranges)),
            sql_set={"contract": "read_only_full_workload_plan_set"},
            active_params=list(ACTIVE_PARAMS),
            max_workers=int(self.algorithm_cfg.get("subset_max_workers") or 4),
            round_index=round_index,
            log_dir=str(self.output_dir / "subset_logs" / ranking_scope),
            optimizer_options=options,
            ranking_target_resource=(
                None if is_none_target(self.target) else self.target
            ),
        )
        nonbaseline_rankings = [
            row for row in summary.candidate_rankings
            if exact_param_signature(row["optimized_params"]) != exact_param_signature(incumbent_params)
        ]
        support_abstention_enabled = target_support_abstention_enabled(
            self.experience_artifact,
            self.target,
        )
        support_excluded: List[Dict[str, Any]] = []
        if support_abstention_enabled:
            supported_rankings: List[Dict[str, Any]] = []
            for row in nonbaseline_rankings:
                support = candidate_target_support_assessment(row, self.target)
                if not isinstance(support, Mapping):
                    raise RuntimeError(
                        "formal candidate lacks target parameter support audit"
                    )
                if (
                    support.get("supported") is not True
                    or support.get("support_abstained") is True
                ):
                    support_excluded.append(
                        {
                            "subset_id": row.get("subset_id"),
                            "optimized_params": copy.deepcopy(
                                row.get("optimized_params") or {}
                            ),
                            "parameter_support": copy.deepcopy(dict(support)),
                        }
                    )
                    continue
                supported_rankings.append(row)
            nonbaseline_rankings = supported_rankings
        # Full-range exploration is allowed to discover a usable gradient and
        # additional plan boundaries, but a formal replay candidate must still
        # remain inside the currently retained binary-search interval.  Filter
        # before plan-cluster ranking so later Top-K truncation cannot discard
        # valid in-range backfill rows and then leave fewer than five after the
        # outer admission check.
        nonbaseline_rankings, formal_range_excluded = (
            partition_formal_range_candidates(
                nonbaseline_rankings,
                self.ranges,
            )
        )
        nonbaseline_rankings.sort(
            key=lambda row: (
                (
                    float(row["candidate_score"])
                    if row.get("candidate_score") is not None
                    else -math.inf
                ),
                (
                    float(row["score_improvement"])
                    if row.get("score_improvement") is not None
                    else -math.inf
                ),
                -int(row.get("rank") or 0),
            ),
            reverse=True,
        )

        def incomplete_error(message: str) -> IncompleteSubsetOptimizationError:
            error = IncompleteSubsetOptimizationError(
                message,
                summary.audit_records,
                summary.log_dir,
            )
            # Boundary-refinement fallback needs the already computed frozen
            # gradient audit.  Preserve offline candidates on the exception;
            # this never makes a partial set eligible for physical replay.
            error.candidate_rankings = copy.deepcopy(
                summary.candidate_rankings
            )
            error.all_candidate_rankings = copy.deepcopy(
                summary.all_candidate_rankings
            )
            return error

        try:
            rankings, duplicate_behavior_subset_ids = (
                distinct_validated_behavior_rankings(nonbaseline_rankings)
            )
        except RuntimeError as exc:
            raise incomplete_error(str(exc)) from exc
        if len(rankings) < BASE_TOP_K:
            error = incomplete_error(
                "Algorithm C produced fewer than five distinct non-baseline "
                f"full physical-plan clusters: {len(rankings)}"
            )
            # These rows have already passed the formal-range, target-support,
            # non-baseline, and exact full-plan deduplication gates.  Keep a
            # separate trusted partial result so the caller can exhaust the
            # wider-step/boundary search and then replay a genuine Top-K
            # (K <= 5) without admitting raw optimizer rows.
            error.eligible_candidate_rankings = copy.deepcopy(rankings)
            error.plan_space_saturation_evidence = True
            raise error
        self.validate_top5_audit_records(rankings[:BASE_TOP_K])
        self.log(
            "algorithm_c_subset_ranking",
            round_index=round_index,
            ranking_scope=ranking_scope,
            parameter_ranges={
                name: asdict(selected_ranges[name]) for name in ACTIVE_PARAMS
            },
            relative_step_probe_pct=list(probe_steps),
            gradient_direction_preserved=True,
            random_direction_used=False,
            uncertain_probe_used=False,
            formal_range_state_mutation=False,
            subset_count=summary.subset_count,
            successful_subset_count=summary.successful_subset_count,
            failed_subset_count=summary.failed_subset_count,
            unique_candidate_count=len(rankings),
            formal_range_candidate_count=len(nonbaseline_rankings),
            formal_range_excluded_count=len(formal_range_excluded),
            target_support_abstention_enabled=support_abstention_enabled,
            target_support_excluded_count=len(support_excluded),
            target_support_excluded=support_excluded,
            formal_range_filter_applied_before_plan_cluster_ranking=True,
            base_topk_dedupe_key="validated_full_physical_plan_fingerprint",
            same_plan_different_behavior_retained=False,
            same_behavior_different_parameters_retained=False,
            behavior_cluster_required_for_formal_top5=True,
            defensive_duplicate_behavior_subset_ids=duplicate_behavior_subset_ids,
            qps_used_for_inner_scoring=False,
            top5=copy.deepcopy(rankings[:BASE_TOP_K]),
        )
        return rankings

    def coverage_vectors(self, incumbent_params: Mapping[str, float]) -> List[Dict[str, float]]:
        """Provide the coverage design; budgeted derivatives may bound this pool."""
        return deterministic_global_coverage_vectors(incumbent_params, self.ranges)

    def allow_target_support_expansion_probe(
        self,
        candidate: Mapping[str, Any],
        support: Mapping[str, Any],
    ) -> bool:
        """Keep legacy/formal controllers fail-closed outside model support."""
        _ = candidate
        _ = support
        return False

    def generate_global_plan_coverage_rankings(
        self,
        round_index: int,
        *,
        allow_numeric_refinement: bool = False,
    ) -> List[Dict[str, Any]]:
        """Generate a deterministic, QPS-blind rescue pool in current ranges.

        The ordinary mode keeps one point per physical plan.  Numeric
        refinement is a later recovery tier: after that distinct-plan pool has
        produced no admissible identity, it retains unseen parameter+plan
        identities and orders them plan-round-robin.  Every admitted point is
        still physically replayed and judged only by the outer gate.
        """

        if plan_portfolio_policy(getattr(self, "algorithm_cfg", {})):
            from shell.pg_algorithm_c_portfolio_adapter import generate
            return generate(
                self,
                round_index,
                plan_space_saturation_required=allow_numeric_refinement,
            )

        incumbent_params = {
            name: float(value)
            for name, value in self.state["incumbent_params"].items()
        }
        incumbent_validation = (
            self.state.get("incumbent_validation")
            or self.state.get("canonical_baseline")
        )
        if not isinstance(incumbent_validation, Mapping):
            raise RuntimeError("global coverage rescue requires a valid incumbent")
        measured_incumbent_resources = {
            name: float((incumbent_validation.get("resources") or {})[name])
            for name in ("cpu", "memory", "io")
        }
        baseline_real = self.inner_scoring_reference_resources(
            measured_incumbent_resources
        )
        queries, baseline_probe, predictor_cfg = self.queries_and_probe(
            incumbent_params
        )
        estimator = RatioScaleEstimator(
            predictor_cfg,
            queries,
            baseline_probe,
            baseline_real,
            incumbent_params,
            self.experience_artifact["resource_prediction_calibration"],
        )
        scoring_options = copy.deepcopy(formal_inner_scoring_options(self.target))
        scoring_options.pop("directional_scoring_contract_required", None)
        utility = ResourceUtilityCalculator(**scoring_options)
        initial_score = utility.calculate_utility_score(
            baseline_real,
            self.capacity,
        )
        vectors = self.coverage_vectors(incumbent_params)
        raw_candidates: List[Dict[str, Any]] = []
        support_excluded: List[Dict[str, Any]] = []
        support_expansion_probes: List[Dict[str, Any]] = []
        probe_errors: List[Dict[str, Any]] = []
        incumbent_plan = candidate_full_plan_fingerprint(
            ((self.state.get("incumbent_validation") or {}).get("candidate_ranking") or {})
        )
        for probe_index, params in enumerate(vectors, start=1):
            try:
                resources, _stats, metadata = estimator(
                    {"contract": "read_only_full_workload_plan_set"},
                    copy.deepcopy(params),
                )
                score = float(
                    utility.calculate_utility_score(resources, self.capacity)
                )
                active_subset = [
                    name
                    for name in ACTIVE_PARAMS
                    if not math.isclose(
                        float(params[name]),
                        float(incumbent_params[name]),
                        rel_tol=1e-12,
                        abs_tol=1e-15,
                    )
                ]
                direction: Dict[str, float] = {}
                delta_param: Dict[str, Dict[str, Any]] = {}
                for name in ACTIVE_PARAMS:
                    before = float(incumbent_params[name])
                    after = float(params[name])
                    absolute = after - before
                    if name in active_subset:
                        if resolve_parameter_transform(name) == "log":
                            direction[name] = math.log(after / before)
                        else:
                            current_range = self.ranges[name]
                            low = float(current_range.min_value)
                            high = float(current_range.max_value)
                            direction[name] = absolute / max(high - low, 1e-12)
                    else:
                        direction[name] = 0.0
                    delta_param[name] = {
                        "before": before,
                        "proposed_before_clip": after,
                        "after_clip": after,
                        "absolute": absolute,
                        "relative": absolute / before if before else None,
                        "clipped": False,
                    }
                ranking = attach_three_level_fingerprint(
                    {
                        "subset_id": f"global_coverage_{probe_index:03d}",
                        "active_subset": active_subset,
                        "canonical_subset_order": probe_index,
                        "optimized_params": copy.deepcopy(params),
                        "initial_params": copy.deepcopy(incumbent_params),
                        "initial_resource": copy.deepcopy(baseline_real),
                        "final_resource": copy.deepcopy(resources),
                        "initial_score": initial_score,
                        "final_score": score,
                        "score_improvement": score - initial_score,
                        "iterations": 0,
                        "log_path": str(
                            self.output_dir
                            / "global_coverage_logs"
                            / f"round_{round_index:03d}"
                        ),
                        "final_estimation_metadata": copy.deepcopy(metadata),
                    },
                    self.capacity,
                )
                ranking.update(
                    {
                        "candidate_origin": "deterministic_global_plan_coverage_rescue",
                        "probe_kind": "qps_blind_current_interval_coverage",
                        "used_for_gradient": False,
                        "gradient_state_unchanged": True,
                        "global_plan_coverage_rescue": True,
                        "global_plan_coverage_contract": GLOBAL_PLAN_COVERAGE_CONTRACT,
                        "dScore/dU": {
                            "available": False,
                            "reason": "global_coverage_is_not_gradient_search",
                        },
                        "T": {
                            "available": False,
                            "reason": "frozen_prediction_model_used_directly",
                        },
                        "confidence": {
                            "value": float(
                                min(
                                    (
                                        metadata.get("plan_model_confidence") or {}
                                    ).values()
                                    or [0.0]
                                )
                            ),
                            "classification": "global_coverage_prediction_confidence",
                        },
                        "g_j": direction,
                        "g_j_semantics": "normalized_current_interval_coverage_direction_not_gradient",
                        "eta": 1.0,
                        "eta_semantics": "deterministic_current_interval_design",
                        "eta_applied_to_candidate": False,
                        "delta_param": delta_param,
                        "candidate_score": score,
                        "qps_used_for_inner_scoring": False,
                        "external_algorithm_evidence_used": False,
                    }
                )
                plan_fingerprint = candidate_full_plan_fingerprint(ranking)
                if not plan_fingerprint or (
                    not allow_numeric_refinement
                    and plan_fingerprint == incumbent_plan
                ):
                    continue
                support = candidate_target_support_assessment(ranking, self.target)
                if target_support_abstention_enabled(
                    self.experience_artifact,
                    self.target,
                ) and (
                    not isinstance(support, Mapping)
                    or support.get("supported") is not True
                    or support.get("support_abstained") is True
                ):
                    support_row = {
                        "subset_id": ranking.get("subset_id"),
                        "optimized_params": copy.deepcopy(params),
                        "parameter_support": copy.deepcopy(
                            dict(support) if isinstance(support, Mapping) else {}
                        ),
                    }
                    if (
                        isinstance(support, Mapping)
                        and self.allow_target_support_expansion_probe(
                            ranking,
                            support,
                        )
                    ):
                        ranking.update(
                            {
                                "target_support_expansion_probe": True,
                                "target_support_prediction_used_for_candidate_score": False,
                                "target_support_expansion_real_replay_required": True,
                                "target_support_expansion_qps_used": False,
                                "target_support_expansion_distance_ratio": support.get(
                                    "distance_to_threshold_ratio"
                                ),
                            }
                        )
                        support_expansion_probes.append(support_row)
                    else:
                        support_excluded.append(support_row)
                        continue
                raw_candidates.append(ranking)
            except Exception as exc:
                probe_errors.append(
                    {"probe_index": probe_index, "error": str(exc)}
                )

        ranking_key = lambda row: (
            float(row["candidate_score"]),
            -int(row.get("canonical_subset_order") or 0),
        )
        candidates_by_plan: Dict[str, List[Dict[str, Any]]] = {}
        for candidate in raw_candidates:
            plan = candidate_full_plan_fingerprint(candidate)
            candidates_by_plan.setdefault(plan, []).append(candidate)
        for values in candidates_by_plan.values():
            values.sort(key=ranking_key, reverse=True)

        if allow_numeric_refinement:
            # Round-robin prevents one large plan cluster from monopolizing a
            # two-worker wave.  All rows in a repeated cluster are explicitly
            # marked so the formal Top-K audit can distinguish this deliberate
            # post-saturation refinement from accidental duplicate padding.
            ordered_plans = sorted(
                candidates_by_plan,
                key=lambda plan: ranking_key(candidates_by_plan[plan][0]),
                reverse=True,
            )
            rankings = []
            depth = 0
            while True:
                added = False
                for plan in ordered_plans:
                    values = candidates_by_plan[plan]
                    if depth >= len(values):
                        continue
                    candidate = values[depth]
                    candidate["numeric_refinement"] = True
                    candidate["numeric_refinement_reason"] = (
                        "distinct_plan_coverage_exhausted_unseen_exact_parameter_plan_identity"
                    )
                    candidate["plan_space_saturation_required"] = True
                    candidate["probe_kind"] = (
                        "qps_blind_current_interval_numeric_refinement"
                    )
                    rankings.append(candidate)
                    added = True
                if not added:
                    break
                depth += 1
        else:
            rankings = [values[0] for values in candidates_by_plan.values()]
            rankings.sort(key=ranking_key, reverse=True)
        for rank, ranking in enumerate(rankings, start=1):
            ranking["rank"] = rank
        if rankings:
            self.validate_top5_audit_records(rankings[:BASE_TOP_K])
        self.log(
            "algorithm_c_global_plan_coverage_ranking",
            contract=GLOBAL_PLAN_COVERAGE_CONTRACT,
            round_index=round_index,
            current_formal_ranges={
                name: {
                    "min_value": float(self.ranges[name].min_value),
                    "max_value": float(self.ranges[name].max_value),
                }
                for name in ACTIVE_PARAMS
            },
            default_full_domain_reopened=False,
            deterministic_design=True,
            numeric_refinement_enabled=allow_numeric_refinement,
            numeric_refinement_requires_prior_distinct_plan_coverage=(
                allow_numeric_refinement
            ),
            numeric_refinement_candidate_count=sum(
                1 for row in rankings if row.get("numeric_refinement") is True
            ),
            external_algorithm_evidence_used=False,
            qps_used_for_inner_scoring=False,
            offline_candidate_count=len(vectors),
            supported_candidate_count=len(raw_candidates),
            distinct_full_plan_count=len(rankings),
            candidate_score_ranking_used=True,
            physical_real_replay_limit=(
                GLOBAL_PLAN_COVERAGE_REAL_REPLAY_LIMIT
            ),
            physical_batch_limit=BASE_TOP_K,
            target_support_excluded_count=len(support_excluded),
            target_support_excluded=support_excluded[:20],
            target_support_expansion_probe_count=len(support_expansion_probes),
            target_support_expansion_probes=support_expansion_probes[:20],
            target_support_expansion_uses_qps=False,
            target_support_expansion_requires_real_replay=True,
            offline_probe_error_count=len(probe_errors),
            offline_probe_errors=copy.deepcopy(probe_errors[:20]),
            failed_offline_probes_excluded=True,
            failed_offline_probes_used_for_candidates=False,
            failed_probe_blocks_interval_exhaustion_proof=bool(
                probe_errors and len(rankings) < BASE_TOP_K
            ),
            top5=copy.deepcopy(rankings[:BASE_TOP_K]),
            physical_workload_invocations=0,
            incumbent_unchanged=True,
            ranges_unchanged=True,
            gradient_state_unchanged=True,
        )
        # A failed surplus prediction probe is not a physical technical
        # failure and must not terminate an otherwise complete, plan-diverse
        # rescue Top-5.  The failed row is excluded above.  Conversely, when
        # fewer than five distinct plans survive, the failed probes mean this
        # design cannot prove that the current interval is exhausted, so fail
        # closed instead of admitting a partial or padded formal batch.
        if probe_errors and len(rankings) < BASE_TOP_K:
            raise RuntimeError(
                "global plan coverage has failed offline probes and cannot "
                "prove a complete plan-diverse Top-5: "
                f"{len(probe_errors)}/{len(vectors)} "
                f"distinct_plans={len(rankings)} first={probe_errors[:3]}"
            )
        return rankings

    def resume_current_interval_coverage_after_local_budget(
        self,
        round_index: int,
    ) -> Optional[str]:
        """Resume directly after a durable local Top-10 checkpoint.

        Returns ``None`` when the round has no such checkpoint, ``handled``
        after an accept or clean rejected-round transition, ``terminal`` when
        convergence finalized, and ``stop`` after a signal boundary.
        """

        checkpoint = self.local_candidate_budget_checkpoint(round_index)
        if not checkpoint:
            return None
        checkpoint_time = float(checkpoint.get("time") or 0.0)
        invalidated = set(
            str(value)
            for value in self.state.get("invalidated_replay_labels") or []
        )
        local_rows: List[Dict[str, Any]] = []
        coverage_rows: List[Dict[str, Any]] = []
        if self.log_path.is_file():
            try:
                with self.log_path.open("r", encoding="utf-8") as handle:
                    for raw_line in handle:
                        try:
                            row = json.loads(raw_line)
                        except (TypeError, ValueError):
                            continue
                        if row.get("event") != "algorithm_c_candidate_replay":
                            continue
                        if int(row.get("round_index") or 0) != int(round_index):
                            continue
                        validation = row.get("validation")
                        if not isinstance(validation, Mapping) or not physical_valid(
                            validation
                        ):
                            continue
                        label = str(validation.get("label") or "")
                        if label in invalidated:
                            continue
                        event_time = float(row.get("time") or 0.0)
                        batch_kind = str(row.get("batch_kind") or "")
                        if event_time <= checkpoint_time and (
                            batch_kind == "base_top5"
                            or batch_kind.startswith("local_plan_tail_")
                        ):
                            local_rows.append(copy.deepcopy(dict(validation)))
                        elif event_time > checkpoint_time and batch_kind.startswith(
                            "current_interval_plan_coverage_"
                        ):
                            coverage_rows.append(copy.deepcopy(dict(validation)))
            except OSError:
                return None

        local_replay_count = int(
            checkpoint.get("physical_replay_count") or len(local_rows)
        )
        local_plan_pool_exhausted = bool(
            checkpoint.get("local_plan_pool_exhausted")
        )
        local_replay_budget_exhausted = bool(
            checkpoint.get("local_replay_budget_exhausted")
        )
        self.log(
            "algorithm_c_current_interval_coverage_resumed_from_local_budget",
            contract=GLOBAL_PLAN_COVERAGE_CONTRACT,
            round_index=round_index,
            checkpoint_time=checkpoint_time,
            local_physical_replay_count=local_replay_count,
            recovered_local_valid_row_count=len(local_rows),
            recovered_coverage_valid_row_count=len(coverage_rows),
            invalidated_replay_labels_excluded=True,
            ordinary_candidate_generation_skipped=True,
            ordinary_physical_replay_skipped=True,
            accepted_unchanged=True,
            incumbent_unchanged=True,
            ranges_unchanged=True,
            gradient_state_unchanged=True,
            physical_workload_invocations=0,
        )

        coverage_replayed_rows = list(local_rows) + list(coverage_rows)
        coverage_replay_count = len(coverage_rows)
        if coverage_rows:
            cached_accepted, _cached_count = self.evaluate_batch(
                coverage_rows,
                round_index=round_index,
                batch_kind="current_interval_plan_coverage_resume_cached",
                evidence_source="cached_valid_replay",
            )
            if cached_accepted is not None:
                self.accept(cached_accepted, round_index)
                return "handled"

        self.state["phase"] = "offline_algorithm_c_ranking"
        self.save_state()
        global_rankings = self.generate_global_plan_coverage_rankings(round_index)
        coverage_batch = 0
        coverage_plan_pool_exhausted = False
        coverage_accepted: Optional[Dict[str, Any]] = None
        while coverage_replay_count < GLOBAL_PLAN_COVERAGE_REAL_REPLAY_LIMIT:
            raw_candidates = self.fallback_expansion_candidates(
                global_rankings,
                coverage_replayed_rows,
            )
            candidates, admission = self.select_outer_admitted_candidates(
                raw_candidates,
                round_index=round_index,
                batch_kind="current_interval_plan_coverage_expansion",
                limit=min(
                    BASE_TOP_K,
                    GLOBAL_PLAN_COVERAGE_REAL_REPLAY_LIMIT
                    - coverage_replay_count,
                ),
            )
            self.log(
                "algorithm_c_current_interval_coverage_after_local_exhaustion",
                contract=GLOBAL_PLAN_COVERAGE_CONTRACT,
                round_index=round_index,
                coverage_batch_index=coverage_batch + 1,
                trigger="resumed_local_scored_top10_completed_without_accept",
                normal_top5_valid_replay_count=min(BASE_TOP_K, local_replay_count),
                local_extension_batch_count=max(
                    0,
                    int(checkpoint.get("top5_batches") or 1) - 1,
                ),
                local_physical_replay_count=local_replay_count,
                local_physical_replay_limit=LOCAL_PLAN_CANDIDATE_REAL_REPLAY_LIMIT,
                local_plan_pool_exhausted=local_plan_pool_exhausted,
                local_replay_budget_exhausted=local_replay_budget_exhausted,
                plan_tree_change_required=True,
                unique_plan_fingerprint_required=True,
                current_formal_interval_only=True,
                default_full_domain_reopened=False,
                raw_candidate_count=len(raw_candidates),
                selected_candidate_count=len(candidates),
                coverage_plan_pool_exhausted=not candidates,
                physical_batch_limit=BASE_TOP_K,
                physical_total_limit=GLOBAL_PLAN_COVERAGE_REAL_REPLAY_LIMIT,
                physical_replay_count_before_batch=coverage_replay_count,
                external_algorithm_evidence_used=False,
                qps_used_for_inner_scoring=False,
                outer_replay_admission=admission,
            )
            if not candidates:
                coverage_plan_pool_exhausted = True
                break
            coverage_batch += 1
            self.state["phase"] = (
                "current_interval_plan_coverage_expansion_real_replay"
            )
            self.save_state()
            new_rows = self.replay_batch(
                candidates,
                round_index=round_index,
                batch_kind=f"current_interval_plan_coverage_{coverage_batch:03d}",
            )
            coverage_replay_count += len(new_rows)
            if self.stop_requested:
                return "stop"
            coverage_accepted, valid_count = self.evaluate_batch(
                new_rows,
                round_index=round_index,
                batch_kind=f"current_interval_plan_coverage_{coverage_batch:03d}",
            )
            coverage_replayed_rows.extend(new_rows)
            if valid_count != len(new_rows):
                self.log(
                    "algorithm_c_technical_wave_excluded_continue",
                    round_index=round_index,
                    batch_kind=(
                        f"current_interval_plan_coverage_{coverage_batch:03d}"
                    ),
                    physical_slots_consumed=len(new_rows),
                    valid_replays_added=valid_count,
                    technical_failures_excluded=(len(new_rows) - valid_count),
                    accepted_unchanged=True,
                    incumbent_unchanged=True,
                    ranges_unchanged=True,
                    gradient_state_unchanged=True,
                    continue_within_physical_budget=True,
                )
            if coverage_accepted is not None:
                self.accept(coverage_accepted, round_index)
                return "handled"

        coverage_budget_exhausted = bool(
            coverage_replay_count >= GLOBAL_PLAN_COVERAGE_REAL_REPLAY_LIMIT
            and not coverage_plan_pool_exhausted
        )
        self.log(
            "algorithm_c_current_interval_coverage_budget_complete",
            contract=GLOBAL_PLAN_COVERAGE_CONTRACT,
            round_index=round_index,
            candidate_score_ranking_used=True,
            top5_batches=coverage_batch,
            physical_replay_count=coverage_replay_count,
            physical_replay_limit=GLOBAL_PLAN_COVERAGE_REAL_REPLAY_LIMIT,
            coverage_plan_pool_exhausted=coverage_plan_pool_exhausted,
            coverage_replay_budget_exhausted=coverage_budget_exhausted,
            accepted=False,
            incumbent_unchanged=True,
            ranges_unchanged=True,
            gradient_state_unchanged=True,
        )
        terminal = self.finish_no_improvement_round(
            round_index=round_index,
            reason=(
                "ordinary_local_top10_and_scored_current_interval_top10_"
                "have_no_higher_qps"
            ),
            exhaustive_current_interval=coverage_plan_pool_exhausted,
        )
        return "terminal" if terminal else "handled"

    def expand_behavior_cluster_rankings(
        self,
        round_index: int,
        incumbent_error: IncompleteSubsetOptimizationError,
    ) -> List[Dict[str, Any]]:
        """Expand eta along trustworthy g_j without padding formal Top-5.

        Search only the currently retained formal interval.  Probe
        progressively larger relative steps and then its directional
        boundaries; an already rejected outer interval must never be
        reintroduced merely to fill five slots.  The optimizer continues to
        compute every candidate from dScore/dU * T * confidence, so a zero
        g_j still produces a zero delta and cannot become a non-baseline
        candidate.
        """

        partial_topk_errors: List[IncompleteSubsetOptimizationError] = [
            incumbent_error
        ]
        self.log(
            "algorithm_c_behavior_cluster_gradient_step_expansion",
            round_index=round_index,
            trigger="ordinary_gradient_steps_have_fewer_than_five_distinct_behaviors",
            incumbent_error=str(incumbent_error),
            search_scope="current_formal_interval_only",
            outside_formal_range_expansion_forbidden=True,
            relative_step_probe_pct=list(
                BEHAVIOR_CLUSTER_GRADIENT_PROBE_STEP_PCT
            ),
            plan_tree_change_required=True,
            behavior_cluster_required=True,
            exact_full_plan_deduplication=True,
            gradient_direction_preserved=True,
            random_direction_used=False,
            zero_confidence_parameter_movement_allowed=False,
            uncertain_probe_used=False,
            baseline_padding_allowed=False,
            formal_incumbent_unchanged=True,
            formal_ranges_unchanged=True,
            gradient_state_unchanged=True,
            physical_workload_invocations=0,
        )
        try:
            return self.generate_rankings(
                round_index,
                parameter_ranges=self.ranges,
                ranking_scope="current_range_gradient_step_behavior_cluster_expansion",
                relative_step_probe_pct=(
                    BEHAVIOR_CLUSTER_GRADIENT_PROBE_STEP_PCT
                ),
            )
        except IncompleteSubsetOptimizationError as gradient_step_error:
            partial_topk_errors.append(gradient_step_error)
            try:
                return self.boundary_inward_bisection_rankings(
                    round_index,
                    gradient_step_error,
                )
            except IncompleteSubsetOptimizationError as boundary_error:
                partial_topk_errors.append(boundary_error)
                boundary_error.partial_topk_errors = partial_topk_errors
                raise

    @staticmethod
    def trusted_gradient_lineage_signature(
        candidate: Mapping[str, Any],
    ) -> str:
        """Return the immutable gradient lineage used for eta-only expansion."""

        active_subset = sorted(
            str(name) for name in (candidate.get("active_subset") or [])
        )
        return canonical_sha256(
            {
                "initial_params": candidate.get("initial_params") or {},
                "active_subset": active_subset,
                "dScore/dU": candidate.get("dScore/dU") or {},
                "T": candidate.get("T") or {},
                "confidence": candidate.get("confidence") or {},
                "g_j": candidate.get("g_j") or {},
            }
        )

    def backfill_replayed_identity_top5(
        self,
        rankings: Sequence[Dict[str, Any]],
        admitted: Sequence[Dict[str, Any]],
        admission: Mapping[str, Any],
        *,
        round_index: int,
        required_candidates: int = BASE_TOP_K,
        expanded_rankings: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Fill Top-5 holes with new plans along the same trusted gradient.

        Exact replay identity is deliberately checked after offline ranking.
        When all high-ranked clusters have already been replayed, first rerun
        the deterministic larger-eta gradient probes and admit previously
        unseen, non-incumbent full plans.  If the current formal interval still
        contains fewer than five unseen clusters, deterministically bisect
        from the incumbent toward the bound selected by the same frozen
        gradient.  Only a coordinate already saturated at that bound may move
        inward.  Random, uncertain, baseline-control, and opposite-gradient
        updates can never enter this path.
        """

        if not 1 <= required_candidates <= BASE_TOP_K:
            raise ValueError("backfill target must be between one and BASE_TOP_K")
        initial = list(admitted)
        missing = required_candidates - len(initial)
        if missing <= 0 or int(
            admission.get("exact_valid_replay_excluded_count") or 0
        ) <= 0:
            return initial, {"attempted": False, "reason": "not_applicable"}

        trusted_signatures = {
            self.trusted_gradient_lineage_signature(candidate)
            for candidate in rankings
            if candidate.get("candidate_origin") == "gradient_probe"
            and candidate.get("probe_kind") == "gradient_relative_step_probe"
            and candidate.get("used_for_gradient") is True
            and candidate.get("boundary_plateau") is False
        }
        exclusion_counts = {
            "untrusted_gradient_lineage": 0,
            "baseline_parameter_vector": 0,
            "missing_full_plan_fingerprint": 0,
            "incumbent_full_plan": 0,
            "duplicate_full_plan": 0,
            "unsupported_target_predictor_domain": 0,
        }
        expansion_error = ""
        expanded: List[Dict[str, Any]] = []
        if trusted_signatures and expanded_rankings is not None:
            # The adaptive caller has already computed the wider-step pool.
            expanded = copy.deepcopy(list(expanded_rankings))
        elif trusted_signatures:
            try:
                expanded = self.generate_rankings(
                    round_index,
                    parameter_ranges=self.ranges,
                    ranking_scope="valid_identity_gradient_step_backfill",
                    relative_step_probe_pct=(
                        BEHAVIOR_CLUSTER_GRADIENT_PROBE_STEP_PCT
                    ),
                )
            except IncompleteSubsetOptimizationError as exc:
                if getattr(exc, "technical_candidate_generation_failure", False) is True:
                    raise
                expansion_error = str(exc)
                # ``generate_rankings`` fails closed whenever a standalone
                # expansion has fewer than five plan clusters.  A backfill
                # does not need five standalone clusters: for example, one
                # ordinary admitted candidate plus four new larger-eta
                # clusters is a complete formal Top-5.  Preserve the partial
                # offline rows from the exception and subject every one of
                # them to the same strict lineage, range, replay-identity,
                # incumbent-plan, and full-plan deduplication checks below.
                # This never makes a partial final batch eligible for
                # physical replay.
                expanded = list(
                    getattr(exc, "candidate_rankings", None) or []
                )
                expanded.extend(
                    getattr(exc, "all_candidate_rankings", None) or []
                )
        else:
            # Frozen boundary rows carry a valid non-zero gradient audit but
            # deliberately are not marked ``used_for_gradient``.  A larger-
            # eta rerun cannot pass the canonical lineage filter in this
            # case, so skip that expensive dead branch and proceed directly
            # to bounded in-range bisection with the frozen audit.
            expansion_error = (
                "larger-eta expansion skipped: no canonical used-for-gradient "
                "lineage in ranked candidates"
            )

        incumbent_params = self.state.get("incumbent_params") or {}
        incumbent_validation = self.state.get("incumbent_validation") or {}
        incumbent_ranking = incumbent_validation.get("candidate_ranking") or {}
        incumbent_plan_fingerprint = candidate_full_plan_fingerprint(
            incumbent_ranking
        )
        seen_plans = {
            candidate_full_plan_fingerprint(candidate) for candidate in initial
        }
        seen_plans.discard("")
        support_abstention_enabled = target_support_abstention_enabled(
            getattr(self, "experience_artifact", {}) or {},
            self.target,
        )
        preadmission: List[Dict[str, Any]] = []
        for candidate in expanded:
            trusted = bool(
                candidate.get("candidate_origin") == "gradient_probe"
                and candidate.get("probe_kind")
                == "gradient_relative_step_probe"
                and candidate.get("used_for_gradient") is True
                and candidate.get("boundary_plateau") is False
                and self.trusted_gradient_lineage_signature(candidate)
                in trusted_signatures
            )
            if not trusted:
                exclusion_counts["untrusted_gradient_lineage"] += 1
                continue
            if support_abstention_enabled:
                target_support = candidate_target_support_assessment(
                    candidate,
                    self.target,
                )
                if (
                    not isinstance(target_support, Mapping)
                    or target_support.get("supported") is not True
                    or target_support.get("support_abstained") is True
                ):
                    exclusion_counts[
                        "unsupported_target_predictor_domain"
                    ] += 1
                    continue
            if exact_param_signature(candidate.get("optimized_params") or {}) == (
                exact_param_signature(incumbent_params)
            ):
                exclusion_counts["baseline_parameter_vector"] += 1
                continue
            plan_fingerprint = candidate_full_plan_fingerprint(candidate)
            if not plan_fingerprint:
                exclusion_counts["missing_full_plan_fingerprint"] += 1
                continue
            if (
                incumbent_plan_fingerprint
                and plan_fingerprint == incumbent_plan_fingerprint
            ):
                exclusion_counts["incumbent_full_plan"] += 1
                continue
            if plan_fingerprint in seen_plans:
                exclusion_counts["duplicate_full_plan"] += 1
                continue
            seen_plans.add(plan_fingerprint)
            preadmission.append(candidate)

        backfill, backfill_admission = self.select_outer_admitted_candidates(
            preadmission,
            round_index=round_index,
            batch_kind="base_top5_identity_gradient_backfill",
            limit=missing,
        )
        combined = initial + backfill
        boundary_attempted = False
        boundary_error = ""
        boundary_expanded_count = 0
        boundary_preadmission_count = 0
        boundary_backfill: List[Dict[str, Any]] = []
        boundary_admission: Dict[str, Any] = {}
        if len(combined) < required_candidates:
            boundary_attempted = True
            synthetic_error = IncompleteSubsetOptimizationError(
                "larger-eta identity backfill exhausted inside current formal interval",
                [],
                str(self.output_dir / "offline" / f"round_{round_index:03d}"),
            )
            # The public ranking result intentionally contains only the best
            # behavior clusters.  Keep both the ordinary and larger-eta rows
            # here so the deterministic range fallback can reconstruct the
            # widest available frozen-gradient audit without mutating it.
            synthetic_error.candidate_rankings = copy.deepcopy(
                list(rankings) + list(expanded)
            )
            synthetic_error.all_candidate_rankings = copy.deepcopy(
                list(rankings) + list(expanded)
            )
            boundary_seen_plans = {
                candidate_full_plan_fingerprint(candidate)
                for candidate in combined
            }
            boundary_seen_plans.discard("")
            try:
                boundary_expanded = self.boundary_inward_bisection_rankings(
                    round_index,
                    synthetic_error,
                    required_admissible_plan_count=(
                        required_candidates - len(combined)
                    ),
                    excluded_batch_plan_fingerprints=boundary_seen_plans,
                )
            except IncompleteSubsetOptimizationError as exc:
                if getattr(exc, "technical_candidate_generation_failure", False) is True:
                    raise
                boundary_error = str(exc)
                boundary_expanded = list(
                    getattr(exc, "candidate_rankings", None) or []
                )
                boundary_expanded.extend(
                    getattr(exc, "all_candidate_rankings", None) or []
                )
            boundary_expanded_count = len(boundary_expanded)
            boundary_preadmission: List[Dict[str, Any]] = []
            for candidate in boundary_expanded:
                if candidate.get("candidate_origin") not in {
                    "formal_range_boundary_inward_bisection_probe",
                    "formal_range_gradient_direction_bisection_probe",
                }:
                    continue
                if candidate.get("probe_kind") not in {
                    "boundary_inward_bisection_probe",
                    "gradient_direction_bisection_probe",
                }:
                    continue
                if candidate.get("used_for_gradient") is not False:
                    continue
                if candidate.get("gradient_state_unchanged") is not True:
                    continue
                plan_fingerprint = candidate_full_plan_fingerprint(candidate)
                if not plan_fingerprint:
                    continue
                if (
                    incumbent_plan_fingerprint
                    and plan_fingerprint == incumbent_plan_fingerprint
                ):
                    continue
                if plan_fingerprint in boundary_seen_plans:
                    continue
                boundary_seen_plans.add(plan_fingerprint)
                boundary_preadmission.append(candidate)
            boundary_preadmission_count = len(boundary_preadmission)
            boundary_backfill, boundary_admission = (
                self.select_outer_admitted_candidates(
                    boundary_preadmission,
                    round_index=round_index,
                    batch_kind="base_top5_identity_boundary_inward_backfill",
                    limit=required_candidates - len(combined),
                )
            )
            combined.extend(boundary_backfill)
        if len(combined) == BASE_TOP_K:
            self.validate_top5_audit_records(combined)
        audit = {
            "attempted": True,
            "trigger": "ranked_top5_exhausted_by_exact_valid_replay_identity",
            "initial_admitted_count": len(initial),
            "initial_exact_valid_replay_excluded_count": int(
                admission.get("exact_valid_replay_excluded_count") or 0
            ),
            "missing_count": missing,
            "relative_step_probe_pct": list(
                BEHAVIOR_CLUSTER_GRADIENT_PROBE_STEP_PCT
            ),
            "trusted_gradient_signatures": sorted(trusted_signatures),
            "expanded_candidate_count": len(expanded),
            "preadmission_candidate_count": len(preadmission),
            "backfill_admitted_count": len(backfill),
            "boundary_inward_attempted": boundary_attempted,
            "boundary_inward_error": boundary_error,
            "boundary_inward_expanded_candidate_count": (
                boundary_expanded_count
            ),
            "boundary_inward_preadmission_candidate_count": (
                boundary_preadmission_count
            ),
            "boundary_inward_backfill_admitted_count": len(
                boundary_backfill
            ),
            "boundary_inward_outer_replay_admission": boundary_admission,
            "final_candidate_count": len(combined),
            "incumbent_plan_fingerprint": incumbent_plan_fingerprint,
            "exclusion_counts": exclusion_counts,
            "expansion_error": expansion_error,
            "outer_replay_admission": backfill_admission,
            "exact_full_plan_deduplication": True,
            "same_trusted_gradient_only": True,
            "random_direction_used": False,
            "uncertain_probe_used": False,
            "boundary_inward_probe_used": bool(boundary_backfill),
            "boundary_inward_only_after_outward_saturation": True,
            "boundary_inward_within_current_formal_ranges": True,
            "full_range_directional_bisection_allowed": True,
            "opposite_gradient_movement_allowed": False,
            "baseline_padding_allowed": False,
            "plan_tree_change_required": True,
            "physical_workload_invocations": 0,
            "formal_incumbent_unchanged": True,
            "formal_ranges_unchanged": True,
            "gradient_state_unchanged": True,
            "candidates": copy.deepcopy(combined),
        }
        self.log(
            "algorithm_c_valid_identity_gradient_backfill",
            round_index=round_index,
            **audit,
        )
        return combined, audit

    def boundary_inward_bisection_rankings(
        self,
        round_index: int,
        gradient_step_error: IncompleteSubsetOptimizationError,
        *,
        required_admissible_plan_count: int = 0,
        excluded_batch_plan_fingerprints: Optional[set[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Generate deterministic formal-range probes after Top-5 exhaustion.

        The normal path follows each finite, trusted, non-zero ``g_j`` from
        the incumbent toward its retained range bound.  If the incumbent is
        already saturated at that bound, the legacy inward dyadic path is
        allowed solely to find a different physical plan.  Neither path
        updates the frozen gradient, incumbent, or range, and zero-gradient
        coordinates never move.
        """

        source_rows = list(gradient_step_error.all_candidate_rankings or [])
        source_rows.extend(gradient_step_error.candidate_rankings or [])
        gradient_rows = [
            row
            for row in source_rows
            if isinstance(row.get("g_j"), Mapping)
            and isinstance(row.get("dScore/dU"), Mapping)
            and isinstance(row.get("T"), Mapping)
            and isinstance(row.get("confidence"), Mapping)
        ]
        gradient_rows.sort(
            key=lambda row: (
                -len(row.get("active_subset") or ()),
                int(row.get("trajectory_index") or 10**9),
                int(row.get("pre_dedup_rank") or 10**9),
            )
        )
        if not gradient_rows:
            error = IncompleteSubsetOptimizationError(
                "formal-range bisection lacks a frozen gradient audit",
                gradient_step_error.audit_records,
                gradient_step_error.log_dir,
            )
            error.eligible_candidate_rankings = copy.deepcopy(
                getattr(gradient_step_error, "eligible_candidate_rankings", None)
                or []
            )
            error.plan_space_saturation_evidence = True
            raise error
        gradient_source = gradient_rows[0]
        raw_gradient = {
            name: float((gradient_source.get("g_j") or {}).get(name, 0.0))
            for name in ACTIVE_PARAMS
        }
        raw_confidence = {
            name: float(
                (gradient_source.get("confidence") or {}).get(name, 0.0)
            )
            for name in ACTIVE_PARAMS
        }
        incumbent_params = {
            name: float(value)
            for name, value in self.state["incumbent_params"].items()
        }

        blocked: Dict[str, Dict[str, Any]] = {}
        for name in ACTIVE_PARAMS:
            current = float(incumbent_params[name])
            low = float(self.ranges[name].min_value)
            high = float(self.ranges[name].max_value)
            gradient = float(raw_gradient[name])
            try:
                confidence = float(raw_confidence[name])
            except (KeyError, TypeError, ValueError):
                continue
            if not math.isfinite(confidence) or confidence <= 0.0:
                continue
            tolerance = max(
                1e-15,
                1e-12 * max(abs(current), abs(low), abs(high), 1.0),
            )
            at_upper = abs(current - high) <= tolerance
            at_lower = abs(current - low) <= tolerance
            if not math.isfinite(gradient) or gradient == 0.0 or high <= low:
                continue
            if gradient > 0.0:
                boundary, opposite, side = high, current, "upper"
                mode = "trusted_gradient_toward_boundary"
                if at_upper:
                    opposite = low
                    mode = "outward_saturated_boundary_inward"
            else:
                boundary, opposite, side = low, current, "lower"
                mode = "trusted_gradient_toward_boundary"
                if at_lower:
                    opposite = high
                    mode = "outward_saturated_boundary_inward"
            blocked[name] = {
                "g_j": gradient,
                "confidence": confidence,
                "boundary": boundary,
                "opposite_bound": opposite,
                "blocked_side": side,
                "projection_mode": mode,
                "transform": resolve_parameter_transform(
                    name,
                    getattr(self.ranges[name], "transform", None),
                ),
            }
        if not blocked:
            error = IncompleteSubsetOptimizationError(
                "formal-range bisection has no trusted non-zero gradient",
                gradient_step_error.audit_records,
                gradient_step_error.log_dir,
            )
            error.eligible_candidate_rankings = copy.deepcopy(
                getattr(gradient_step_error, "eligible_candidate_rankings", None)
                or []
            )
            error.plan_space_saturation_evidence = True
            raise error

        incumbent_validation = (
            self.state["incumbent_validation"]
            or self.state["canonical_baseline"]
        )
        measured_incumbent_resources = {
            name: float((incumbent_validation.get("resources") or {})[name])
            for name in ("cpu", "memory", "io")
        }
        baseline_real = self.inner_scoring_reference_resources(
            measured_incumbent_resources
        )
        queries, baseline_probe, predictor_cfg = self.queries_and_probe(
            incumbent_params
        )
        estimator = RatioScaleEstimator(
            predictor_cfg,
            queries,
            baseline_probe,
            baseline_real,
            incumbent_params,
            self.experience_artifact["resource_prediction_calibration"],
        )
        scoring_options = formal_inner_scoring_options(self.target)
        scoring_options.pop("directional_scoring_contract_required", None)
        utility = ResourceUtilityCalculator(**scoring_options)
        initial_score = float(
            utility.calculate_utility_score(baseline_real, self.capacity)
        )
        incumbent_plan_fingerprint = str(
            ((baseline_probe.get("exact_plan_metadata") or {}).get(
                "workload_plan_fingerprint"
            ))
            or ""
        )

        # Breadth-first dyadic fractions in transform space.  Ordinarily the
        # segment starts at the incumbent and follows the trusted gradient to
        # its retained formal bound.  Only an already saturated coordinate
        # uses the legacy inward segment from the opposite bound.  A zero-g_j
        # coordinate is never included.
        fractions = (
            0.5,
            0.75,
            0.25,
            0.875,
            0.625,
            0.375,
            0.125,
            0.9375,
            0.8125,
            0.6875,
            0.5625,
            0.4375,
            0.3125,
            0.1875,
            0.0625,
        )
        blocked_names = tuple(name for name in ACTIVE_PARAMS if name in blocked)
        subsets: List[Tuple[str, ...]] = []
        for subset_size in range(len(blocked_names), 0, -1):
            subsets.extend(itertools.combinations(blocked_names, subset_size))

        def interpolate(row: Mapping[str, Any], fraction: float) -> float:
            boundary = float(row["boundary"])
            opposite = float(row["opposite_bound"])
            if row["transform"] == "log":
                return math.exp(
                    math.log(opposite)
                    + fraction * (math.log(boundary) - math.log(opposite))
                )
            return opposite + fraction * (boundary - opposite)

        raw_candidates: List[Dict[str, Any]] = []
        support_excluded: List[Dict[str, Any]] = []
        seen_parameter_signatures: set[Tuple[Tuple[str, str], ...]] = set()
        probe_errors: List[Dict[str, Any]] = []
        maximum_probe_count = 96
        early_stop_unique_plan_target = BASE_TOP_K + 3
        required_admissible_plan_count = max(
            0,
            int(required_admissible_plan_count),
        )
        excluded_batch_plans = set(
            excluded_batch_plan_fingerprints or set()
        )
        unique_candidate_plans: set[str] = set()
        admissible_candidate_plans: set[str] = set()
        best_candidate_by_plan: Dict[str, Dict[str, Any]] = {}
        early_stop_triggered = False
        probe_index = 0

        def boundary_candidate_order_key(
            candidate: Mapping[str, Any],
        ) -> Tuple[float, float, int]:
            predicted = candidate.get("predicted_resource") or {}
            if is_none_target(self.target):
                resource_tie_break = max(
                    float(predicted[resource]) / float(self.capacity[resource])
                    for resource in ("cpu", "memory", "io")
                )
            else:
                resource_tie_break = (
                    float(predicted[self.target])
                    / float(self.capacity[self.target])
                )
            return (
                -float(candidate["candidate_score"]),
                resource_tie_break,
                int(candidate.get("canonical_subset_order") or 0),
            )

        for fraction in fractions:
            for active_subset in subsets:
                if probe_index >= maximum_probe_count:
                    break
                params = copy.deepcopy(incumbent_params)
                for name in active_subset:
                    params[name] = interpolate(blocked[name], fraction)
                signature = exact_param_signature(params)
                if signature == exact_param_signature(incumbent_params):
                    continue
                if signature in seen_parameter_signatures:
                    continue
                seen_parameter_signatures.add(signature)
                probe_index += 1
                try:
                    resources, _stats, metadata = estimator(
                        {"contract": "read_only_full_workload_plan_set"},
                        params,
                    )
                    score = float(
                        utility.calculate_utility_score(resources, self.capacity)
                    )
                    delta_param: Dict[str, Dict[str, Any]] = {}
                    applied_direction: Dict[str, float] = {}
                    for name in ACTIVE_PARAMS:
                        before = float(incumbent_params[name])
                        after = float(params[name])
                        absolute = after - before
                        delta_param[name] = {
                            "before": before,
                            "proposed_before_clip": after,
                            "after_clip": after,
                            "absolute": absolute,
                            "relative": absolute / before if before else None,
                            "clipped": False,
                            "g_j": raw_gradient[name],
                            "formal_range_bisection_projected": (
                                name in active_subset
                            ),
                        }
                        applied_direction[name] = (
                            math.log(after / before)
                            if before > 0.0 and after > 0.0
                            else absolute / max(abs(before), 1.0)
                        )
                    ranking = attach_three_level_fingerprint(
                        {
                            "subset_id": (
                                "boundary_inward_bisection_"
                                f"{probe_index:03d}"
                            ),
                            "active_subset": list(active_subset),
                            "canonical_subset_order": probe_index,
                            "optimized_params": copy.deepcopy(params),
                            "initial_params": copy.deepcopy(incumbent_params),
                            "initial_resource": copy.deepcopy(baseline_real),
                            "final_resource": copy.deepcopy(resources),
                            "initial_score": initial_score,
                            "final_score": score,
                            "score_improvement": score - initial_score,
                            "iterations": 1,
                            "log_path": gradient_step_error.log_dir,
                            "final_estimation_metadata": copy.deepcopy(metadata),
                        },
                        self.capacity,
                    )
                    ranking.update(
                        {
                            "candidate_origin": (
                                "formal_range_gradient_direction_bisection_probe"
                            ),
                            "probe_kind": "gradient_direction_bisection_probe",
                            "used_for_gradient": False,
                            "used_for_inner_incumbent_update": False,
                            "gradient_state_unchanged": True,
                            "direction_relation": (
                                "trusted_gradient_to_formal_bound_or_"
                                "inward_only_when_already_saturated"
                            ),
                            "dyadic_fraction_on_projection_segment": fraction,
                            "dScore/dU": copy.deepcopy(
                                gradient_source["dScore/dU"]
                            ),
                            "T": copy.deepcopy(gradient_source["T"]),
                            "confidence": copy.deepcopy(raw_confidence),
                            "gradient_components": copy.deepcopy(
                                gradient_source.get("gradient_components") or {}
                            ),
                            "g_j": copy.deepcopy(raw_gradient),
                            "applied_direction": applied_direction,
                            "eta": fraction,
                            "eta_semantics": (
                                "dyadic_fraction_on_trusted_formal_range_"
                                "projection_segment"
                            ),
                            "eta_applied_to_candidate": False,
                            "delta_param": delta_param,
                            "candidate_score": score,
                            "qps_used_for_inner_scoring": False,
                            "formal_range_projection": {
                                name: copy.deepcopy(blocked[name])
                                for name in active_subset
                            },
                        }
                    )
                    plan_fingerprint = candidate_full_plan_fingerprint(ranking)
                    if (
                        not plan_fingerprint
                        or plan_fingerprint == incumbent_plan_fingerprint
                    ):
                        continue
                    target_support = candidate_target_support_assessment(
                        ranking,
                        self.target,
                    )
                    if target_support_abstention_enabled(
                        self.experience_artifact,
                        self.target,
                    ) and (
                        not isinstance(target_support, Mapping)
                        or target_support.get("supported") is not True
                        or target_support.get("support_abstained") is True
                    ):
                        support_excluded.append(
                            {
                                "subset_id": ranking.get("subset_id"),
                                "optimized_params": copy.deepcopy(params),
                                "parameter_support": copy.deepcopy(
                                    dict(target_support)
                                ),
                            }
                        )
                        continue
                    raw_candidates.append(ranking)
                    unique_candidate_plans.add(plan_fingerprint)
                    previous_best = best_candidate_by_plan.get(
                        plan_fingerprint
                    )
                    if (
                        previous_best is None
                        or boundary_candidate_order_key(ranking)
                        < boundary_candidate_order_key(previous_best)
                    ):
                        # Final plan clustering keeps only the best-ranked row
                        # for each full plan.  Count eligibility for that same
                        # representative; otherwise a worse unseen
                        # parameter+plan identity can cause an early stop even
                        # though the retained representative was already
                        # replayed and will be excluded by outer admission.
                        best_candidate_by_plan[plan_fingerprint] = ranking
                        admissible_candidate_plans.discard(plan_fingerprint)
                        identity_assessment = assess_valid_replay_identity(
                            self.state.get("valid_replay_identity_memory") or {},
                            ranking,
                        )
                        if (
                            identity_assessment.get("seen_valid_replay")
                            is not True
                            and plan_fingerprint not in excluded_batch_plans
                        ):
                            admissible_candidate_plans.add(plan_fingerprint)
                    ordinary_early_stop = (
                        required_admissible_plan_count == 0
                        and len(unique_candidate_plans)
                        >= early_stop_unique_plan_target
                    )
                    identity_backfill_early_stop = (
                        required_admissible_plan_count > 0
                        and len(admissible_candidate_plans)
                        >= required_admissible_plan_count
                    )
                    if ordinary_early_stop or identity_backfill_early_stop:
                        early_stop_triggered = True
                        break
                except Exception as exc:
                    probe_errors.append(
                        {
                            "probe_index": probe_index,
                            "active_subset": list(active_subset),
                            "fraction": fraction,
                            "error": str(exc),
                        }
                    )
            if early_stop_triggered:
                break
            if probe_index >= maximum_probe_count:
                break

        raw_candidates.sort(key=boundary_candidate_order_key)
        try:
            rankings, duplicate_subset_ids = distinct_validated_behavior_rankings(
                raw_candidates
            )
        except RuntimeError as exc:
            raise IncompleteSubsetOptimizationError(
                str(exc),
                gradient_step_error.audit_records,
                gradient_step_error.log_dir,
            ) from exc
        for rank, ranking in enumerate(rankings, start=1):
            ranking["rank"] = rank
        self.log(
            "algorithm_c_behavior_cluster_formal_range_directional_bisection",
            round_index=round_index,
            trigger="trusted_nonzero_gradient_top5_identity_exhaustion",
            gradient_step_expansion_error=str(gradient_step_error),
            blocked_parameters=copy.deepcopy(blocked),
            dyadic_fractions=list(fractions),
            offline_probe_count=probe_index,
            early_stop_unique_plan_target=early_stop_unique_plan_target,
            required_admissible_plan_count=required_admissible_plan_count,
            admissible_candidate_plan_count=len(admissible_candidate_plans),
            excluded_batch_plan_fingerprint_count=len(excluded_batch_plans),
            early_stop_mode=(
                "unseen_valid_replay_identity_and_batch_plan"
                if required_admissible_plan_count > 0
                else "unique_full_plan_cluster"
            ),
            early_stop_triggered=early_stop_triggered,
            offline_probe_error_count=len(probe_errors),
            offline_probe_errors=probe_errors[:10],
            nonincumbent_plan_change_candidate_count=len(raw_candidates),
            target_support_excluded_count=len(support_excluded),
            target_support_excluded=support_excluded,
            unique_full_plan_cluster_count=len(rankings),
            duplicate_subset_ids=duplicate_subset_ids,
            plan_tree_change_required=True,
            exact_full_plan_deduplication=True,
            admissible_count_uses_final_plan_representative=True,
            original_g_j_preserved_for_audit=True,
            used_for_gradient=False,
            gradient_state_unchanged=True,
            zero_gradient_parameter_movement_allowed=False,
            random_direction_used=False,
            uncertain_probe_used=False,
            baseline_padding_allowed=False,
            formal_incumbent_unchanged=True,
            formal_ranges_unchanged=True,
            physical_workload_invocations=0,
        )
        # A complete Top-5 does not rely on the failed surplus probes.  A
        # partial result does: without every attempted boundary probe being
        # technically sound, it cannot prove that the interval is saturated.
        if probe_errors and len(rankings) < BASE_TOP_K:
            error = IncompleteSubsetOptimizationError(
                "formal-range directional bisection has failed offline probes: "
                f"{len(probe_errors)}/{probe_index}",
                gradient_step_error.audit_records,
                gradient_step_error.log_dir,
            )
            error.technical_candidate_generation_failure = True
            error.offline_probe_errors = copy.deepcopy(probe_errors)
            raise error
        if len(rankings) < BASE_TOP_K:
            error = IncompleteSubsetOptimizationError(
                "formal-range directional bisection produced fewer than five distinct "
                f"non-baseline full physical-plan clusters: {len(rankings)}",
                gradient_step_error.audit_records,
                gradient_step_error.log_dir,
            )
            error.candidate_rankings = copy.deepcopy(rankings)
            error.all_candidate_rankings = copy.deepcopy(raw_candidates)
            error.eligible_candidate_rankings = copy.deepcopy(rankings)
            error.plan_space_saturation_evidence = True
            raise error
        self.validate_top5_audit_records(rankings[:BASE_TOP_K])
        return rankings

    def merge_exhausted_partial_topk(
        self,
        errors: Sequence[IncompleteSubsetOptimizationError],
        *,
        round_index: int,
    ) -> List[Dict[str, Any]]:
        """Return audited unique plans after every Top-5 expansion is exhausted.

        Top-5 is an upper bound on physical work, not a requirement that the
        current formal interval contain five different PostgreSQL plans.  Only
        the explicitly preserved eligible rows are considered here; raw or
        failed EXPLAIN rows can never become replay candidates.
        """

        eligible: List[Dict[str, Any]] = []
        source_audits: List[Dict[str, Any]] = []
        for error in errors:
            rows = getattr(error, "eligible_candidate_rankings", None) or []
            source_audits.append(
                {
                    "error": str(error),
                    "eligible_candidate_count": len(rows),
                    "log_dir": str(getattr(error, "log_dir", "") or ""),
                }
            )
            eligible.extend(copy.deepcopy(list(rows)))

        incumbent_signature = exact_param_signature(
            self.state.get("incumbent_params") or {}
        )
        identity_memory = self.state.get("valid_replay_identity_memory")
        if not isinstance(identity_memory, Mapping):
            identity_memory = empty_valid_replay_identity_memory(
                getattr(self, "workload_contract_sha256", ""),
                self.target,
            )
        allowed_origins = {
            "gradient_probe",
            "formal_range_boundary_inward_bisection_probe",
            "formal_range_gradient_direction_bisection_probe",
        }
        filtered: List[Dict[str, Any]] = []
        exclusion_counts = {
            "baseline_parameter_vector": 0,
            "outside_formal_range": 0,
            "untrusted_candidate_origin": 0,
            "already_valid_exact_identity": 0,
            "missing_full_plan_fingerprint": 0,
        }
        for row in eligible:
            if exact_param_signature(row.get("optimized_params") or {}) == incumbent_signature:
                exclusion_counts["baseline_parameter_vector"] += 1
                continue
            if parameter_vector_range_audit(
                row.get("optimized_params"), self.ranges
            ).get("passed") is not True:
                exclusion_counts["outside_formal_range"] += 1
                continue
            candidate_origin = str(row.get("candidate_origin") or "")
            # Older preserved eligible rows did not serialize an origin; they
            # remain valid ordinary plan representatives.  An explicitly
            # labelled untrusted origin can never enter either path.
            if candidate_origin and candidate_origin not in allowed_origins:
                exclusion_counts["untrusted_candidate_origin"] += 1
                continue
            if not candidate_full_plan_fingerprint(row):
                exclusion_counts["missing_full_plan_fingerprint"] += 1
                continue
            if assess_valid_replay_identity(
                identity_memory,
                row,
            ).get("seen_valid_replay") is True:
                exclusion_counts["already_valid_exact_identity"] += 1
                continue
            filtered.append(row)
        filtered.sort(
            key=lambda row: (
                -float(row.get("candidate_score") or -math.inf),
                int(row.get("rank") or 10**9),
                str(row.get("subset_id") or ""),
            )
        )
        try:
            rankings, duplicate_subset_ids = (
                distinct_validated_behavior_rankings(filtered)
            )
        except RuntimeError as exc:
            raise RuntimeError(
                "exhausted partial Top-K contains invalid full-plan evidence"
            ) from exc
        rankings = rankings[:BASE_TOP_K]
        distinct_plan_candidate_count = len(rankings)

        # A complete physical plan can remain unchanged while continuous cost
        # parameters still contain useful binary-search points.  Only after
        # the deterministic ordinary/larger-eta/boundary searches have all
        # reported plan-space saturation may unseen exact parameter+plan
        # identities fill the remaining slots.  This is trusted-gradient
        # numeric refinement, never baseline/control/uncertain padding.
        selected_exact_identities = {
            (
                exact_param_signature(row.get("optimized_params") or {}),
                candidate_full_plan_fingerprint(row),
            )
            for row in rankings
        }
        numeric_candidates: List[Dict[str, Any]] = []
        if len(rankings) < BASE_TOP_K:
            for row in filtered:
                if not (
                    row.get("candidate_origin") == "gradient_probe"
                    and row.get("probe_kind") == "gradient_relative_step_probe"
                    and row.get("used_for_gradient") is True
                    and row.get("boundary_plateau") is False
                ):
                    continue
                exact_identity = (
                    exact_param_signature(row.get("optimized_params") or {}),
                    candidate_full_plan_fingerprint(row),
                )
                if exact_identity in selected_exact_identities:
                    continue
                numeric_candidates.append(row)
                selected_exact_identities.add(exact_identity)
                if len(rankings) + len(numeric_candidates) >= BASE_TOP_K:
                    break
        rankings.extend(numeric_candidates)

        plan_counts: Dict[str, int] = {}
        for row in rankings:
            plan_fingerprint = candidate_full_plan_fingerprint(row)
            plan_counts[plan_fingerprint] = plan_counts.get(plan_fingerprint, 0) + 1
        numeric_refinement_count = 0
        for row in rankings:
            plan_fingerprint = candidate_full_plan_fingerprint(row)
            if plan_counts.get(plan_fingerprint, 0) > 1:
                row["numeric_refinement"] = True
                row["numeric_refinement_reason"] = (
                    "new_plan_space_exhausted_unseen_exact_parameter_plan_identity"
                )
                row["plan_space_saturation_required"] = True
                numeric_refinement_count += 1
        for rank, row in enumerate(rankings, start=1):
            row["rank"] = rank
        if rankings:
            self.validate_top5_audit_records(rankings)
        self.log(
            "algorithm_c_exhausted_partial_topk",
            round_index=round_index,
            top_k_limit=BASE_TOP_K,
            admitted_before_outer_pruning=len(rankings),
            top_k_is_upper_bound=True,
            exhaustive_search_completed=True,
            plan_space_saturated=distinct_plan_candidate_count < BASE_TOP_K,
            plan_plateau=len(rankings) == 0,
            distinct_plan_candidate_count=distinct_plan_candidate_count,
            source_audits=source_audits,
            defensive_duplicate_behavior_subset_ids=duplicate_subset_ids,
            exact_full_plan_deduplication=(numeric_refinement_count == 0),
            same_plan_numeric_refinement_enabled=True,
            same_plan_numeric_refinement_count=numeric_refinement_count,
            same_plan_numeric_refinement_requires_plan_space_saturation=True,
            exact_parameter_plan_identity_deduplication=True,
            exclusion_counts=exclusion_counts,
            random_direction_used=False,
            uncertain_probe_used=False,
            baseline_padding_allowed=False,
            accepted_unchanged=True,
            incumbent_unchanged=True,
            ranges_unchanged=True,
            gradient_state_unchanged=True,
            physical_workload_invocations=0,
        )
        return rankings

    def validate_top5_audit_records(
        self,
        candidates: Sequence[Mapping[str, Any]],
    ) -> None:
        """Fail closed when a formal replay candidate lacks required audit data."""
        expected_resources = {"cpu", "memory", "io"}
        seen_plan_fingerprints: Dict[str, List[Tuple[str, bool]]] = {}
        seen_exact_identities: set[Tuple[str, str]] = set()
        for position, candidate in enumerate(candidates, start=1):
            missing = sorted(TOP5_AUDIT_FIELDS - set(candidate))
            if missing:
                raise RuntimeError(
                    f"formal Algorithm C Top-5 rank {position} missing audit fields: {missing}"
                )
            for field in ("feature_ratio", "predicted_resource", "utilization"):
                value = candidate.get(field)
                if not isinstance(value, Mapping) or not expected_resources.issubset(value):
                    raise RuntimeError(
                        f"formal Algorithm C Top-5 rank {position} has incomplete {field}"
                    )
            target_support = candidate_target_support_assessment(
                candidate,
                self.target,
            )
            target_support_enabled = target_support_abstention_enabled(
                self.experience_artifact,
                self.target,
            )
            if target_support_enabled and (
                not isinstance(target_support, Mapping)
                or target_support.get("supported") is not True
                or target_support.get("support_abstained") is True
            ):
                expansion_allowed = bool(
                    isinstance(target_support, Mapping)
                    and self.allow_target_support_expansion_probe(
                        candidate,
                        target_support,
                    )
                )
                expansion_contract = bool(
                    candidate.get("target_support_expansion_probe") is True
                    and candidate.get(
                        "target_support_prediction_used_for_candidate_score"
                    ) is False
                    and candidate.get(
                        "target_support_expansion_real_replay_required"
                    ) is True
                    and candidate.get("target_support_expansion_qps_used") is False
                )
                if not expansion_allowed or not expansion_contract:
                    raise RuntimeError(
                        "formal Algorithm C Top-5 rank "
                        f"{position} is outside the target predictor support domain"
                    )
            try:
                score = float(candidate["candidate_score"])
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"formal Algorithm C Top-5 rank {position} has invalid candidate_score"
                ) from exc
            if not math.isfinite(score):
                raise RuntimeError(
                    f"formal Algorithm C Top-5 rank {position} has non-finite candidate_score"
                )
            validate_zero_gradient_delta(candidate, position)
            plan_fingerprint = candidate_full_plan_fingerprint(candidate)
            if not plan_fingerprint:
                raise RuntimeError(
                    f"formal Algorithm C Top-5 rank {position} lacks a full physical-plan fingerprint"
                )
            parameter_signature = exact_param_signature(
                candidate.get("optimized_params") or {}
            )
            exact_identity = (parameter_signature, plan_fingerprint)
            if exact_identity in seen_exact_identities:
                raise RuntimeError(
                    "formal Algorithm C Top-5 contains duplicate parameter+plan "
                    f"identity at rank {position}: {plan_fingerprint}"
                )
            previous_plan_rows = seen_plan_fingerprints.get(plan_fingerprint, [])
            if previous_plan_rows and (
                candidate.get("numeric_refinement") is not True
                or not all(
                    is_refinement
                    for _signature, is_refinement in previous_plan_rows
                )
            ):
                raise RuntimeError(
                    "formal Algorithm C Top-5 contains duplicate full physical-plan "
                    "fingerprint without explicit numeric_refinement at rank "
                    f"{position}: {plan_fingerprint}"
                )
            seen_exact_identities.add(exact_identity)
            seen_plan_fingerprints.setdefault(plan_fingerprint, []).append(
                (
                    parameter_signature,
                    candidate.get("numeric_refinement") is True,
                )
            )

    def physical_replay_batch_limit(self, batch_kind: str) -> int:
        """Return the physical replay ceiling for one semantic batch.

        Algorithm C and its Top-K derivatives retain the formal five-point
        ceiling.  Exhaustive algorithms may override this hook while reusing
        the same restore, isolation, telemetry, and technical-failure
        accounting implementation.
        """

        _ = batch_kind
        return BASE_TOP_K

    def physical_replay_candidate_slot(
        self,
        candidate: Mapping[str, Any],
        *,
        local_position: int,
        batch_kind: str,
    ) -> int:
        """Return the stable one-based slot used for worker assignment/labels."""

        _ = candidate, batch_kind
        return local_position + 1

    def physical_replay_label_suffix(
        self,
        candidate: Mapping[str, Any],
        *,
        candidate_slot: int,
        batch_kind: str,
    ) -> str:
        """Return an optional suffix that distinguishes repeated invocations."""

        _ = candidate, candidate_slot, batch_kind
        return ""

    def physical_replay_candidate_slot_width(self, batch_kind: str) -> int:
        """Return label width while preserving the historical Top-K format."""

        _ = batch_kind
        return 2

    def account_physical_replay_rows(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        round_index: int,
        batch_kind: str,
    ) -> Dict[str, int]:
        """Commit one completed physical wave before preparing the next wave."""

        identity_memory = self.state.setdefault(
            "valid_replay_identity_memory",
            empty_valid_replay_identity_memory(
                self.workload_contract_sha256,
                self.target,
            ),
        )
        identity_updates: List[Dict[str, Any]] = []
        for row in rows:
            if not physical_valid(row):
                continue
            candidate = row.get("candidate_ranking")
            if not isinstance(candidate, Mapping):
                continue
            identity_updates.append(
                record_valid_replay_identity(
                    identity_memory,
                    candidate=candidate,
                    round_index=round_index,
                    label=str(row.get("label") or ""),
                )
            )
            self.cache_valid_replay_evidence(
                row,
                round_index=round_index,
            )
        accounting = physical_batch_counts(rows)
        for key, value in accounting.items():
            self.state[key] = int(self.state.get(key) or 0) + int(value)
        if identity_updates:
            self.log(
                "algorithm_c_valid_replay_identity_update",
                round_index=round_index,
                batch_kind=batch_kind,
                valid_identity_update_count=sum(
                    update.get("recorded") is True
                    for update in identity_updates
                ),
                valid_identity_count=len(
                    identity_memory.get("identities") or {}
                ),
                technical_failures_excluded=True,
                accepted_unchanged=True,
                incumbent_unchanged=True,
                ranges_unchanged=True,
                gradient_state_unchanged=True,
            )
        return accounting

    def recover_inflight_physical_wave(self) -> None:
        """Resolve a durable physical-wave journal after abrupt termination."""

        journal = self.state.get("inflight_physical_wave")
        if not isinstance(journal, Mapping):
            return
        entries = journal.get("entries")
        if not isinstance(entries, list) or not entries:
            raise RuntimeError("inflight physical-wave journal is malformed")
        recovery_id = str(journal.get("journal_id") or "")
        terminal_by_label: Dict[str, Dict[str, Any]] = {}
        expected_labels = {
            str(entry.get("label") or "")
            for entry in entries
            if isinstance(entry, Mapping)
        }
        if "" in expected_labels or len(expected_labels) != len(entries):
            raise RuntimeError("inflight physical-wave labels are invalid")
        if self.log_path.is_file():
            with self.log_path.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    try:
                        event = json.loads(raw_line)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if event.get("event") != "algorithm_c_candidate_replay":
                        continue
                    validation = event.get("validation")
                    if not isinstance(validation, Mapping):
                        continue
                    label = str(validation.get("label") or "")
                    if label in expected_labels:
                        terminal_by_label.setdefault(
                            label,
                            copy.deepcopy(dict(validation)),
                        )
        recovered_rows: List[Dict[str, Any]] = []
        synthesized_labels: List[str] = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise RuntimeError("inflight physical-wave entry is malformed")
            label = str(entry["label"])
            existing = terminal_by_label.get(label)
            if existing is not None:
                recovered_rows.append(existing)
                continue
            candidate = entry.get("candidate")
            if not isinstance(candidate, Mapping):
                raise RuntimeError("inflight physical-wave candidate is missing")
            synthesized = {
                "status": "failed",
                "technical_failure": True,
                "technical_failure_reason": (
                    "controller_terminated_during_physical_workload"
                ),
                "used_real_replay": True,
                "physical_workload_invocations": 1,
                "valid_replay": False,
                "label": label,
                "worker_key": str(entry.get("worker_key") or ""),
                "candidate_ranking": copy.deepcopy(dict(candidate)),
                "workload_completeness": {
                    "passed": False,
                    "errors": ["controller_terminated_during_physical_workload"],
                },
                "inflight_recovery": {
                    "contract": "algorithm_c_inflight_physical_wave_journal_v1",
                    "journal_id": recovery_id,
                    "excluded_from_valid_and_acceptance": True,
                },
            }
            recovered_rows.append(synthesized)
            synthesized_labels.append(label)
            self.log(
                "algorithm_c_candidate_replay",
                round_index=int(journal.get("round_index") or 0),
                batch_kind=str(journal.get("batch_kind") or ""),
                physical_slot=int(entry.get("physical_slot") or 0),
                validation=synthesized,
                recovered_from_inflight_journal=True,
                journal_id=recovery_id,
            )
        accounting = self.account_physical_replay_rows(
            recovered_rows,
            round_index=int(journal.get("round_index") or 0),
            batch_kind=str(journal.get("batch_kind") or ""),
        )
        self.state.setdefault("inflight_physical_wave_recoveries", []).append(
            {
                "contract": "algorithm_c_inflight_physical_wave_journal_v1",
                "journal_id": recovery_id,
                "terminal_log_rows_reused": len(recovered_rows) - len(synthesized_labels),
                "technical_rows_synthesized": len(synthesized_labels),
                "synthesized_labels": synthesized_labels,
                "accounting": copy.deepcopy(accounting),
                "accepted_unchanged": True,
                "incumbent_unchanged": True,
                "ranges_unchanged": True,
            }
        )
        self.state.pop("inflight_physical_wave", None)
        self.save_state()
        self.log(
            "algorithm_c_inflight_physical_wave_recovered",
            journal_id=recovery_id,
            accounting=accounting,
            synthesized_labels=synthesized_labels,
            accepted_unchanged=True,
            incumbent_unchanged=True,
            ranges_unchanged=True,
        )

    def replay_batch(
        self,
        candidates: Sequence[Dict[str, Any]],
        *,
        round_index: int,
        batch_kind: str,
    ) -> List[Dict[str, Any]]:
        physical_batch_limit = int(self.physical_replay_batch_limit(batch_kind))
        if physical_batch_limit < 1:
            raise ValueError("physical replay batch limit must be positive")
        if len(candidates) > physical_batch_limit:
            raise ValueError(
                "physical replay batch exceeds the configured limit: "
                f"{len(candidates)} > {physical_batch_limit}"
            )
        # Top-5 is a physical budget ceiling.  Zero candidates is a valid
        # plan-plateau control round; one to four candidates are a partial
        # Top-K after exhaustive offline plan search.
        if not candidates or not all(
            candidate.get("confirmation_only") is True
            for candidate in candidates
        ):
            self.validate_top5_audit_records(candidates)
        results: List[Optional[Dict[str, Any]]] = [None] * len(candidates)
        self.log(
            "algorithm_c_physical_batch_start",
            round_index=round_index,
            batch_kind=batch_kind,
            physical_batch_limit=physical_batch_limit,
            candidate_count=len(candidates),
            replay_parallelism=self.replay_parallelism,
            wave_shape=[
                min(self.replay_parallelism, len(candidates) - wave_start)
                for wave_start in range(
                    0,
                    len(candidates),
                    self.replay_parallelism,
                )
            ],
        )
        for wave_start in range(0, len(candidates), self.replay_parallelism):
            self.physical_wave_guard_event(
                "before_restore", f"round_{round_index:03d}_{batch_kind}_{wave_start:03d}",
            )
            wave = list(
                enumerate(
                    candidates[wave_start:wave_start + self.replay_parallelism],
                    start=wave_start,
                )
            )
            prepared_wave: List[Tuple[int, Dict[str, Any], Dict[str, Any], str, Dict[str, Any]]] = []
            # Shared-disk barrier: finish every restore in this wave before
            # either physical workload starts. The workers own disjoint
            # PostgreSQL instances, so their template clones run in parallel.
            restore_requests: List[
                Tuple[int, Dict[str, Any], Dict[str, Any], str]
            ] = []
            for position, candidate in wave:
                candidate_slot = self.physical_replay_candidate_slot(
                    candidate,
                    local_position=position,
                    batch_kind=batch_kind,
                )
                if candidate_slot < 1:
                    raise ValueError(
                        f"physical replay candidate slot must be positive: {candidate_slot}"
                    )
                worker = self.workers[(candidate_slot - 1) % len(self.workers)]
                label_suffix = self.physical_replay_label_suffix(
                    candidate,
                    candidate_slot=candidate_slot,
                    batch_kind=batch_kind,
                )
                candidate_slot_width = self.physical_replay_candidate_slot_width(
                    batch_kind
                )
                artifact_identity = physical_replay_artifact_identity_token(
                    candidate
                )
                label = (
                    f"algorithm_c_r{round_index:03d}_{batch_kind}_"
                    f"c{candidate_slot:0{candidate_slot_width}d}"
                    f"{label_suffix}_i{artifact_identity}_{worker_key(worker)}"
                )
                restore_requests.append((position, candidate, worker, label))
            restore_by_position: Dict[int, Dict[str, Any]] = {}
            with ThreadPoolExecutor(max_workers=self.replay_parallelism) as executor:
                future_map = {
                    executor.submit(
                        restore_worker_from_hybench_template,
                        self.cfg,
                        worker,
                        label=label,
                    ): (position, worker)
                    for position, _candidate, worker, label in restore_requests
                }
                for future in as_completed(future_map):
                    position, worker = future_map[future]
                    restore = future.result()
                    if restore.get("status") != "ok":
                        raise RuntimeError(
                            f"worker restore failed: worker={worker_key(worker)} "
                            f"restore={restore}"
                        )
                    restore_by_position[position] = restore
            for position, candidate, worker, label in restore_requests:
                restore = restore_by_position[position]
                prepared_wave.append((position, candidate, worker, label, restore))
            self.log(
                "algorithm_c_shared_restore_barrier_complete",
                round_index=round_index,
                batch_kind=batch_kind,
                wave_start=wave_start,
                worker_count=len(prepared_wave),
            )
            if self.stop_requested:
                self.log(
                    "algorithm_c_stop_after_restore_before_physical_replay",
                    round_index=round_index,
                    batch_kind=batch_kind,
                    wave_start=wave_start,
                    worker_count=len(prepared_wave),
                    completed_physical_invocations=sum(
                        row is not None for row in results
                    ),
                    formal_state_mutation=False,
                )
                return [row for row in results if row is not None]
            sequence_selection = (
                next_unfinished_algorithm_c_candidate_wave_sequence(
                    int(self.state.get("formal_candidate_wave_sequence") or 0)
                )
            )
            wave_sequence = int(sequence_selection["wave_sequence"])
            skipped_completed_waves = list(
                sequence_selection.get("skipped_completed_waves") or []
            )
            self.state["formal_candidate_wave_sequence"] = wave_sequence
            self.save_state()
            if skipped_completed_waves:
                self.log(
                    "algorithm_c_historical_completed_cohort_waves_skipped",
                    round_index=round_index,
                    batch_kind=batch_kind,
                    wave_start=wave_start,
                    skipped_completed_waves=skipped_completed_waves,
                    selected_wave_sequence=wave_sequence,
                    physical_workload_invocations=0,
                    valid_replays=0,
                    formal_state_mutation=False,
                )
            try:
                candidate_cohort_barrier = wait_for_algorithm_c_candidate_cohort(
                    scene=str(self.algorithm_cfg.get("scene") or self.target),
                    wave_sequence=wave_sequence,
                    round_index=round_index,
                    batch_kind=batch_kind,
                    wave_start=wave_start,
                    stop_requested=lambda: self.stop_requested,
                )
            except RuntimeError:
                if self.stop_requested:
                    self.log(
                        "algorithm_c_stop_while_waiting_candidate_cohort",
                        round_index=round_index,
                        batch_kind=batch_kind,
                        wave_start=wave_start,
                        wave_sequence=wave_sequence,
                        completed_physical_invocations=sum(
                            row is not None for row in results
                        ),
                        formal_state_mutation=False,
                    )
                    return [row for row in results if row is not None]
                raise
            self.log(
                "algorithm_c_candidate_cohort_barrier",
                round_index=round_index,
                batch_kind=batch_kind,
                wave_start=wave_start,
                wave_sequence=wave_sequence,
                candidate_cohort_barrier=candidate_cohort_barrier,
                physical_workload_invocations=0,
                formal_state_mutation=False,
            )
            if self.stop_requested:
                self.log(
                    "algorithm_c_stop_after_candidate_cohort_before_physical_replay",
                    round_index=round_index,
                    batch_kind=batch_kind,
                    wave_start=wave_start,
                    wave_sequence=wave_sequence,
                    completed_physical_invocations=sum(
                        row is not None for row in results
                    ),
                    formal_state_mutation=False,
                )
                return [row for row in results if row is not None]
            try:
                workload_lease, workload_lease_wait_s = (
                    acquire_configured_shared_ssd_workload_coordination(
                        cancel_requested=lambda: self.stop_requested,
                    )
                )
            except RuntimeError:
                if self.stop_requested:
                    self.log(
                        "algorithm_c_stop_while_waiting_workload_lease",
                        round_index=round_index,
                        batch_kind=batch_kind,
                        wave_start=wave_start,
                        completed_physical_invocations=sum(
                            row is not None for row in results
                        ),
                        formal_state_mutation=False,
                    )
                    return [row for row in results if row is not None]
                raise
            self.log(
                "algorithm_c_workload_lease_acquired",
                round_index=round_index,
                batch_kind=batch_kind,
                wave_start=wave_start,
                wait_s=workload_lease_wait_s,
                physical_workload_invocations=0,
                formal_state_mutation=False,
            )
            if self.state.get("inflight_physical_wave") is not None:
                raise RuntimeError(
                    "cannot start a physical wave while an inflight journal exists"
                )
            journal_entries = [
                {
                    "physical_slot": position + 1,
                    "label": label,
                    "worker_key": worker_key(worker),
                    "candidate": copy.deepcopy(candidate),
                }
                for position, candidate, worker, label, _restore in prepared_wave
            ]
            journal = {
                "contract": "algorithm_c_inflight_physical_wave_journal_v1",
                "round_index": round_index,
                "batch_kind": batch_kind,
                "wave_start": wave_start,
                "wave_sequence": wave_sequence,
                "entries": journal_entries,
                "prepared_at_epoch_s": time.time(),
            }
            self.physical_wave_guard_event("after_restore", None)
            journal["journal_id"] = canonical_sha256(journal)
            self.state["inflight_physical_wave"] = copy.deepcopy(journal)
            self.save_state()
            self.log(
                "algorithm_c_inflight_physical_wave_journaled",
                round_index=round_index,
                batch_kind=batch_kind,
                wave_start=wave_start,
                wave_sequence=wave_sequence,
                journal_id=journal["journal_id"],
                labels=[entry["label"] for entry in journal_entries],
                physical_workload_invocations=0,
                formal_state_mutation=False,
            )
            try:
                with ThreadPoolExecutor(max_workers=self.replay_parallelism) as executor:
                    future_map = {}
                    for position, candidate, worker, label, restore in prepared_wave:
                        prepared_worker = worker_with_prepared_restore(worker)
                        future_map[
                            executor.submit(
                                run_real_validation_safe,
                                self.cfg,
                                candidate["optimized_params"],
                                output_dir=self.output_dir / "replays" / f"round_{round_index:03d}" / batch_kind,
                                label=label,
                                worker=prepared_worker,
                                force_new_replay=True,
                            )
                        ] = (position, worker, candidate, restore)
                    for future in as_completed(future_map):
                        position, worker, candidate, restore = future_map[future]
                        raw = future.result()
                        raw["worker_key"] = worker_key(worker)
                        raw["candidate_ranking"] = copy.deepcopy(candidate)
                        raw["restore"] = restore
                        raw["candidate_cohort_barrier"] = copy.deepcopy(
                            candidate_cohort_barrier
                        )
                        results[position] = raw
            finally:
                release_shared_ssd_coordination(workload_lease)
            wave_rows = [
                results[position]
                for position, _candidate in wave
                if results[position] is not None
            ]
            self.physical_wave_guard_event("after_replay", wave_rows)
            qualification = apply_ssd_wave_qualification(
                wave_rows,
                target=self.target,
                phase=f"formal_{batch_kind}",
                cfg=self.cfg,
            )
            baseline_domain = self.state.get("baseline_execution_domain")
            baseline_domain = (
                baseline_domain if isinstance(baseline_domain, Mapping) else {}
            )
            expected_no_mutex = baseline_domain.get("expected_no_mutex")
            current_no_mutex = (
                ((qualification.get("thresholds") or {}).get(
                    "explicit_user_authorized_no_mutex"
                ))
            )
            if (
                not isinstance(expected_no_mutex, bool)
                or current_no_mutex is not expected_no_mutex
            ):
                qualification["passed"] = False
                qualification["errors"] = list(
                    qualification.get("errors") or []
                ) + ["baseline_candidate_execution_domain_mismatch"]
                qualification["baseline_execution_domain"] = copy.deepcopy(
                    dict(baseline_domain)
                )
                qualification["qualification_id"] = canonical_sha256(
                    {
                        key: value
                        for key, value in qualification.items()
                        if key != "qualification_id"
                    }
                )
            runtime_candidate_cohort = (
                algorithm_c_candidate_cohort_config_from_env()
            )
            if runtime_candidate_cohort.get("enabled") is True:
                cohort_receipts = [
                    row.get("candidate_cohort_barrier")
                    for row in wave_rows
                    if isinstance(row, Mapping)
                ]
                if (
                    len(cohort_receipts) != len(wave_rows)
                    or not all(
                        isinstance(receipt, Mapping)
                        and receipt.get("passed") is True
                        and int(receipt.get("ready_count") or 0)
                        >= int(runtime_candidate_cohort["cohort_size"])
                        and int(receipt.get("wave_sequence") or -1)
                        == wave_sequence
                        for receipt in cohort_receipts
                    )
                ):
                    qualification["passed"] = False
                    qualification["errors"] = list(
                        qualification.get("errors") or []
                    ) + ["candidate_cohort_execution_domain_missing"]
                    qualification["qualification_id"] = canonical_sha256(
                        {
                            key: value
                            for key, value in qualification.items()
                            if key != "qualification_id"
                        }
                    )
            completion_barrier = (
                wait_for_algorithm_c_candidate_cohort_completion(
                    scene=str(self.algorithm_cfg.get("scene") or self.target),
                    wave_sequence=wave_sequence,
                    round_index=round_index,
                    batch_kind=batch_kind,
                    wave_start=wave_start,
                    wave_valid=bool(
                        qualification.get("passed") is True
                        and len(wave_rows) == len(wave)
                        and all(physical_valid(row) for row in wave_rows)
                    ),
                    stop_requested=lambda: self.stop_requested,
                )
            )
            for row in wave_rows:
                if isinstance(row, dict):
                    row["candidate_cohort_completion_barrier"] = copy.deepcopy(
                        completion_barrier
                    )
            if runtime_candidate_cohort.get("enabled") is True:
                completion_receipts = [
                    row.get("candidate_cohort_completion_barrier")
                    for row in wave_rows
                    if isinstance(row, Mapping)
                ]
                if (
                    len(completion_receipts) != len(wave_rows)
                    or not all(
                        isinstance(receipt, Mapping)
                        and receipt.get("passed") is True
                        and int(receipt.get("completed_count") or 0)
                        >= int(runtime_candidate_cohort["cohort_size"])
                        and int(receipt.get("wave_sequence") or -1)
                        == wave_sequence
                        for receipt in completion_receipts
                    )
                ):
                    qualification["passed"] = False
                    qualification["errors"] = list(
                        qualification.get("errors") or []
                    ) + ["candidate_cohort_completion_domain_missing"]
                    qualification["qualification_id"] = canonical_sha256(
                        {
                            key: value
                            for key, value in qualification.items()
                            if key != "qualification_id"
                        }
                    )
            if completion_barrier.get("passed") is not True:
                for row in wave_rows:
                    if isinstance(row, dict):
                        row["completion_domain_accounting"] = {
                            "contract": (
                                "algorithm_c_completion_domain_"
                                "out_of_band_accounting_v1"
                            ),
                            "excluded_from_valid_replays": True,
                            "excluded_from_lane_technical_failures": True,
                            "physical_workload_invocation_retained": True,
                            "formal_semantic_state_mutation": False,
                        }
            self.log(
                "algorithm_c_ssd_wave_qualification",
                round_index=round_index,
                batch_kind=batch_kind,
                wave_start=wave_start,
                qualification=qualification,
            )
            self.log(
                "algorithm_c_candidate_cohort_completion_barrier",
                round_index=round_index,
                batch_kind=batch_kind,
                wave_start=wave_start,
                wave_sequence=wave_sequence,
                completion_barrier=completion_barrier,
                physical_workload_invocations=0,
                formal_state_mutation=False,
                next_restore_blocked_until_all_lanes_complete=True,
            )
            for position, _candidate in wave:
                raw = results[position]
                if raw is None:
                    continue
                self.log(
                    "algorithm_c_candidate_replay",
                    round_index=round_index,
                    batch_kind=batch_kind,
                    physical_slot=position + 1,
                    validation=raw,
                )
            wave_accounting = self.account_physical_replay_rows(
                [row for row in wave_rows if isinstance(row, Mapping)],
                round_index=round_index,
                batch_kind=batch_kind,
            )
            self.state.pop("inflight_physical_wave", None)
            self.save_state()
            self.log(
                "algorithm_c_physical_wave_accounted",
                round_index=round_index,
                batch_kind=batch_kind,
                wave_start=wave_start,
                wave_sequence=wave_sequence,
                journal_id=journal["journal_id"],
                accounting=wave_accounting,
                next_wave_may_start=True,
            )
            if qualification.get("passed") is not True:
                if self.continue_after_candidate_terminal_qualification_failure(
                    qualification,
                    batch_kind=batch_kind,
                ):
                    self.log(
                        "algorithm_c_candidate_terminal_failure_wave_continued",
                        round_index=round_index,
                        batch_kind=batch_kind,
                        wave_start=wave_start,
                        qualification=qualification,
                        executed_slots=sum(
                            row is not None for row in results
                        ),
                        remaining_slots=sum(row is None for row in results),
                        failed_candidates_excluded_from_valid_and_acceptance=True,
                        physical_slots_retained=True,
                        semantic_state_mutation=False,
                    )
                else:
                    self.log(
                        "algorithm_c_physical_batch_aborted_out_of_band",
                        round_index=round_index,
                        batch_kind=batch_kind,
                        executed_slots=sum(row is not None for row in results),
                        remaining_slots=sum(row is None for row in results),
                        semantic_state_mutation=False,
                    )
                    break
            if self.stop_requested:
                self.log(
                    "algorithm_c_physical_batch_stopped_at_wave_boundary",
                    round_index=round_index,
                    batch_kind=batch_kind,
                    executed_slots=sum(row is not None for row in results),
                    remaining_slots=sum(row is None for row in results),
                    semantic_state_mutation=False,
                )
                break
        # A partial Top-K still participates in the remaining canonical
        # [2,2,1] cohort waves.  These receipts preserve the four-lane
        # execution domain but intentionally restore/run no workload and do
        # not consume physical slots.  Peers with real candidates therefore
        # cannot deadlock when PostgreSQL exposes fewer than five new plans in
        # one lane's current interval.
        if batch_kind == "base_top5":
            executed_wave_count = math.ceil(
                len(candidates) / self.replay_parallelism
            )
            canonical_wave_starts = (0, 2, 4)
            for wave_start in canonical_wave_starts[executed_wave_count:]:
                if self.stop_requested:
                    break
                sequence_selection = (
                    next_unfinished_algorithm_c_candidate_wave_sequence(
                        int(
                            self.state.get("formal_candidate_wave_sequence") or 0
                        )
                    )
                )
                wave_sequence = int(sequence_selection["wave_sequence"])
                skipped_completed_waves = list(
                    sequence_selection.get("skipped_completed_waves") or []
                )
                self.state["formal_candidate_wave_sequence"] = wave_sequence
                self.save_state()
                if skipped_completed_waves:
                    self.log(
                        "algorithm_c_historical_completed_cohort_waves_skipped",
                        round_index=round_index,
                        batch_kind="base_top5_empty_wave_control",
                        wave_start=wave_start,
                        skipped_completed_waves=skipped_completed_waves,
                        selected_wave_sequence=wave_sequence,
                        physical_workload_invocations=0,
                        valid_replays=0,
                        formal_state_mutation=False,
                    )
                try:
                    barrier = wait_for_algorithm_c_candidate_cohort(
                        scene=str(self.algorithm_cfg.get("scene") or self.target),
                        wave_sequence=wave_sequence,
                        round_index=round_index,
                        batch_kind="base_top5_empty_wave_control",
                        wave_start=wave_start,
                        stop_requested=lambda: self.stop_requested,
                    )
                except RuntimeError:
                    if self.stop_requested:
                        break
                    raise
                completion = wait_for_algorithm_c_candidate_cohort_completion(
                    scene=str(self.algorithm_cfg.get("scene") or self.target),
                    wave_sequence=wave_sequence,
                    round_index=round_index,
                    batch_kind="base_top5_empty_wave_control",
                    wave_start=wave_start,
                    wave_valid=True,
                    stop_requested=lambda: self.stop_requested,
                )
                self.log(
                    "algorithm_c_partial_topk_empty_wave_control",
                    round_index=round_index,
                    batch_kind=batch_kind,
                    candidate_count=len(candidates),
                    wave_start=wave_start,
                    wave_sequence=wave_sequence,
                    candidate_cohort_barrier=barrier,
                    candidate_cohort_completion_barrier=completion,
                    workload_executed=False,
                    physical_workload_invocations=0,
                    valid_replays=0,
                    technical_failures=0,
                    accepted_unchanged=True,
                    incumbent_unchanged=True,
                    ranges_unchanged=True,
                    gradient_state_unchanged=True,
                )
        completed = [row for row in results if row is not None]
        self.restore_clean_baseline(reason=f"round_{round_index:03d}_{batch_kind}")
        return completed

    def observe_valid_evaluation(self, evaluation: Mapping[str, Any], round_index: int) -> None:
        """Optional evidence sink, independent of the search-incumbent selection."""
        _ = evaluation, round_index

    def evaluate_batch(
        self,
        rows: Sequence[Dict[str, Any]],
        *,
        round_index: int,
        batch_kind: str,
        evidence_source: str = "physical_replay",
    ) -> Tuple[Optional[Dict[str, Any]], int]:
        baseline_by_worker = self.state["baseline_by_worker"]
        canonical_baseline = self.state["canonical_baseline"]
        reference = self.state["incumbent_validation"] or canonical_baseline
        valid_evaluations: List[Dict[str, Any]] = []
        for row in rows:
            if not physical_valid(row):
                self.log(
                    "algorithm_c_candidate_technical_failure",
                    round_index=round_index,
                    batch_kind=batch_kind,
                    label=row.get("label"),
                    error=row.get("error"),
                    state_mutation=False,
                )
                continue
            key = str(row["worker_key"])
            normalized = normalize_to_worker_baseline(
                row,
                baseline_by_worker[key],
                canonical_baseline,
                self.capacity,
            )
            normalized["qps_noise_model"] = copy.deepcopy(
                self.state.get("qps_noise_model") or {}
            )
            decision = outer_semantic_acceptance_details(
                normalized,
                reference,
                canonical_baseline,
                target=self.target,
                cfg=self.cfg,
                consecutive_missed_outer_rounds=int(
                    self.state["consecutive_missed_outer_rounds"]
                ),
            )
            evaluation = {
                "raw": row,
                "normalized": normalized,
                "decision": decision,
            }
            self.observe_valid_evaluation(evaluation, round_index)
            valid_evaluations.append(evaluation)
            if evidence_source == "cached_valid_replay":
                # A tier re-decision is not a second independent physical
                # observation.  Re-counting it would fabricate multi-round
                # support for a pruning rule from one measured replay.
                pruning_update = {
                    "recorded": False,
                    "reason": "cached_redecision_is_not_new_physical_evidence",
                }
            else:
                pruning_update = self.record_outer_pruning_evidence(
                    candidate=(row.get("candidate_ranking") or {}),
                    decision=decision,
                    round_index=round_index,
                )
            self.log(
                "algorithm_c_outer_candidate_decision",
                round_index=round_index,
                batch_kind=batch_kind,
                label=row.get("label"),
                evidence_source=evidence_source,
                cached_valid_replay_redecision=(
                    evidence_source == "cached_valid_replay"
                ),
                physical_workload_invocations=(
                    0 if evidence_source == "cached_valid_replay" else 1
                ),
                valid_replays_added=(
                    0 if evidence_source == "cached_valid_replay" else 1
                ),
                qps_outer_only=True,
                inner_candidate_scores_modified=False,
                decision=decision,
                candidate_rank=(row.get("candidate_ranking") or {}).get("rank"),
                outer_pruning_evidence_update=pruning_update,
            )
        if valid_evaluations and self.outer_pruning_policy.get("enabled") is True:
            self.save_state()
        escape_evaluations = [
            item
            for item in valid_evaluations
            if item["decision"].get("accepted_escape") is True
        ]
        if escape_evaluations:
            self.log(
                "algorithm_c_fallback_qps_escape_observed",
                round_index=round_index,
                batch_kind=batch_kind,
                escape_count=len(escape_evaluations),
                labels=[item["raw"].get("label") for item in escape_evaluations],
                accepted_shrink_unchanged=True,
                incumbent_unchanged=True,
                ranges_unchanged=True,
                gradient_state_unchanged=True,
                triggers_plan_change_expansion=True,
            )
        accepted = [item for item in valid_evaluations if item["decision"].get("accepted")]
        accepted.sort(key=accepted_batch_selection_key, reverse=True)
        return (accepted[0] if accepted else None), len(valid_evaluations)

    def fallback_expansion_candidates(
        self,
        rankings: Sequence[Dict[str, Any]],
        base_rows: Sequence[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        replayed_plan_ids = {
            str(
                (((row.get("candidate_ranking") or {}).get("three_level_fingerprint") or {}).get("plan_level"))
                or ""
            )
            for row in base_rows
        }
        incumbent_plan_id = str(
            (((((self.state.get("incumbent_validation") or {}).get("candidate_ranking") or {})
               .get("three_level_fingerprint") or {}).get("plan_level"))
            or "")
        )
        if incumbent_plan_id:
            replayed_plan_ids.add(incumbent_plan_id)
        replayed_signatures = {
            exact_param_signature((row.get("candidate_ranking") or {}).get("optimized_params") or {})
            for row in base_rows
            if (row.get("candidate_ranking") or {}).get("optimized_params")
        }
        selected: List[Dict[str, Any]] = []
        selected_plans: set[str] = set()
        for candidate in rankings:
            signature = exact_param_signature(candidate["optimized_params"])
            plan_id = str(((candidate.get("three_level_fingerprint") or {}).get("plan_level")) or "")
            if signature in replayed_signatures:
                continue
            if not plan_id or plan_id in replayed_plan_ids or plan_id in selected_plans:
                continue
            selected.append(candidate)
            selected_plans.add(plan_id)
        return selected

    def has_shrunk_parameter_ranges(self) -> bool:
        """Return whether accepted updates narrowed any formal parameter range."""
        for name in ACTIVE_PARAMS:
            current = self.ranges[name]
            low, high = DEFAULT_RANGES[name]
            if not math.isclose(current.min_value, low) or not math.isclose(
                current.max_value,
                high,
            ):
                return True
        return False

    def interval_progress_width(self, effective_shrink_count: int) -> float:
        """Legacy progress metric; adaptive controllers override with actual widths."""
        return 0.5 ** effective_shrink_count

    def accept(self, evaluation: Dict[str, Any], round_index: int) -> None:
        ranking = evaluation["raw"]["candidate_ranking"]
        params = {name: float(ranking["optimized_params"][name]) for name in ACTIVE_PARAMS}
        global_coverage_rescue = bool(
            ranking.get("global_plan_coverage_rescue") is True
            and ranking.get("global_plan_coverage_contract")
            == GLOBAL_PLAN_COVERAGE_CONTRACT
        )
        range_audit = parameter_vector_range_audit(params, self.ranges)
        if range_audit.get("passed") is not True:
            raise RuntimeError(
                "accepted candidate is outside the current formal ranges: "
                f"{range_audit.get('violations')}"
            )
        # A subset candidate supplies directional evidence only for the
        # parameters it actually moved.  Shrinking every dimension around an
        # unchanged baseline value discards unexplored space without evidence
        # and can immediately block the next trusted gradient at that artificial
        # boundary.  This previously made XP-CPU stop at accepted=1 even though
        # unsearched plan-changing candidates still existed.
        declared_subset = {
            str(name)
            for name in (ranking.get("active_subset") or [])
            if str(name) in ACTIVE_PARAMS
        }
        moved_subset = {
            name
            for name in ACTIVE_PARAMS
            if not math.isclose(
                float(params[name]),
                float(self.state["incumbent_params"][name]),
                rel_tol=1e-12,
                abs_tol=1e-15,
            )
        }
        shrink_params = [
            name
            for name in ACTIVE_PARAMS
            if name in declared_subset and name in moved_subset
        ]
        if not shrink_params:
            raise RuntimeError(
                "accepted candidate has no moved parameter in its active_subset"
            )
        preserved_inactive_ranges = copy.deepcopy(self.ranges)
        updated_subset_ranges, shrink = binary_shrink_parameter_ranges(
            self.ranges,
            params,
            shrink_params,
        )
        self.ranges = {
            name: (
                updated_subset_ranges[name]
                if name in shrink_params
                else preserved_inactive_ranges[name]
            )
            for name in ACTIVE_PARAMS
        }
        self.state["accepted"] = int(self.state["accepted"]) + 1
        self.state["accepted_improvements"] = int(self.state["accepted"])
        accepted_count = int(self.state["accepted"])
        refinement_count = int(
            self.state.get("incumbent_centered_stagnation_refinement_count") or 0
        )
        effective_shrink_count = accepted_count + refinement_count
        self.state["accepted_normalized_width"] = 0.5 ** accepted_count
        self.state["effective_binary_shrink_count"] = effective_shrink_count
        self.state["normalized_width"] = self.interval_progress_width(effective_shrink_count)
        self.state["convergence_progress_contract"] = (
            EFFECTIVE_SEARCH_PROGRESS_CONTRACT
        )
        self.state["incumbent_params"] = params
        self.state["incumbent_validation"] = evaluation["normalized"]
        canonical_baseline = self.state.get("canonical_baseline")
        if isinstance(canonical_baseline, Mapping):
            final_normalized = copy.deepcopy(evaluation["normalized"])
            final_normalized["qps_noise_model"] = copy.deepcopy(
                self.state.get("qps_noise_model") or {}
            )
            final_decision = outer_semantic_acceptance_details(
                final_normalized,
                canonical_baseline,
                canonical_baseline,
                target=self.target,
                cfg=self.cfg,
                consecutive_missed_outer_rounds=0,
            )
            final_decision_reference = "canonical_baseline"
        else:
            # Minimal unit-test and legacy migration states may predate the
            # canonical baseline field.  Preserve their original decision;
            # formal campaigns always take the branch above.
            final_decision = copy.deepcopy(evaluation["decision"])
            final_decision_reference = "search_reference_compatibility"
        champion = {
            "contract": "best_feasible_champion_tier_qps_resource_v1",
            "accepted_index": int(self.state["accepted"]),
            "round_index": int(round_index),
            "params": copy.deepcopy(params),
            "normalized": copy.deepcopy(evaluation["normalized"]),
            "decision": copy.deepcopy(evaluation["decision"]),
            "final_decision": copy.deepcopy(final_decision),
            "final_decision_reference": final_decision_reference,
            "candidate_rank": ranking.get("rank"),
            "candidate_identity": exact_param_signature(params),
            "source": "valid_real_outer_accepted_candidate",
        }
        champion["sort_key"] = list(feasible_champion_sort_key(champion))
        archive = self.state.setdefault("accepted_candidate_archive", [])
        if not isinstance(archive, list):
            raise RuntimeError("accepted candidate archive is not a list")
        archive.append(champion)
        previous_champion = self.state.get("best_feasible_champion")
        champion_improved = bool(
            not isinstance(previous_champion, Mapping)
            or feasible_champion_sort_key(champion)
            > feasible_champion_sort_key(previous_champion)
        )
        if champion_improved:
            self.state["best_feasible_champion"] = copy.deepcopy(champion)
        self.state["consecutive_missed_outer_rounds"] = 0
        self.state["consecutive_no_improvement_rounds"] = 0
        self.state["consecutive_plan_plateau_rounds"] = 0
        self.state["phase"] = "formal_search"
        self.save_state()
        self.log(
            "algorithm_c_round_accepted",
            round_index=round_index,
            accepted=self.state["accepted"],
            accepted_target=self.accepted_target,
            max_rounds=self.max_rounds,
            normalized_width=self.state["normalized_width"],
            accepted_normalized_width=self.state["accepted_normalized_width"],
            effective_binary_shrink_count=effective_shrink_count,
            convergence_progress_contract=EFFECTIVE_SEARCH_PROGRESS_CONTRACT,
            normalized_width_threshold=self.normalized_width_threshold,
            params=params,
            decision=evaluation["decision"],
            declared_active_subset=sorted(declared_subset),
            moved_parameter_subset=sorted(moved_subset),
            range_shrink_parameter_subset=shrink_params,
            inactive_parameter_ranges_preserved=True,
            global_plan_coverage_rescue=global_coverage_rescue,
            formal_range_reopened_from_default_bounds=False,
            global_coverage_limited_to_current_formal_interval=(
                global_coverage_rescue
            ),
            external_algorithm_evidence_used=False,
            range_shrink=shrink,
            best_feasible_champion_updated=champion_improved,
            best_feasible_champion=copy.deepcopy(
                self.state.get("best_feasible_champion")
            ),
            search_incumbent_separate_from_final_champion=True,
        )

    def finish_plan_space_exhaustion(self, round_index: int) -> None:
        """Persist a clean, non-converged terminal when no new plan exists.

        ``replay_batch([])`` has already completed the cohort control waves
        and restored both workers to the baseline snapshot.  This terminal is
        deliberately not an acceptance: incumbent, ranges, gradient state,
        and the accepted counter remain unchanged.
        """

        self.state["consecutive_missed_outer_rounds"] = (
            int(self.state.get("consecutive_missed_outer_rounds") or 0) + 1
        )
        self.state["consecutive_plan_plateau_rounds"] = (
            int(self.state.get("consecutive_plan_plateau_rounds") or 0) + 1
        )
        exhaustion = {
            "reason": "no_new_distinct_full_plan_in_current_formal_interval",
            "round_index": int(round_index),
            "accepted": int(self.state["accepted"]),
            "accepted_target": self.accepted_target,
            "normalized_width": float(self.state.get("normalized_width") or 1.0),
            "normalized_width_threshold": self.normalized_width_threshold,
            "exhaustive_offline_search_completed": True,
            "ordinary_gradient_search_completed": True,
            "larger_eta_search_completed": True,
            "current_range_boundary_search_completed": True,
            "outside_current_formal_range_reintroduction_allowed": False,
            "baseline_padding_allowed": False,
            "duplicate_plan_padding_allowed": False,
            "technical_failure": False,
            "accepted_unchanged": True,
            "incumbent_unchanged": True,
            "ranges_unchanged": True,
            "gradient_state_unchanged": True,
            "workers_already_restored_by_empty_replay_batch": True,
        }
        self.state["candidate_space_exhaustion"] = exhaustion
        self.state["phase"] = (
            "candidate_space_exhausted_without_accepted_target_clean"
        )
        self.save_state()
        self.log(
            "algorithm_c_candidate_space_exhausted_clean_stop",
            **exhaustion,
            physical_workload_invocations=0,
            valid_replays=0,
            technical_failures=0,
        )

    def record_no_improvement_round(self) -> int:
        """Record one valid outer round that did not replace the incumbent."""

        self.state["consecutive_missed_outer_rounds"] = (
            int(self.state.get("consecutive_missed_outer_rounds") or 0) + 1
        )
        self.state["consecutive_no_improvement_rounds"] = (
            int(self.state.get("consecutive_no_improvement_rounds") or 0) + 1
        )
        return int(self.state["consecutive_no_improvement_rounds"])

    def refine_ranges_around_current_incumbent(
        self,
        *,
        round_index: int,
        reason: str,
    ) -> Dict[str, Any]:
        """Bisect every current interval around the last accepted incumbent.

        This is a stagnation refinement, not acceptance of a rejected point:
        the anchor, QPS reference, accepted counter, incumbent validation and
        gradient state are unchanged.  Only the next inner-loop search domain
        is made finer after two complete no-improvement rounds.
        """

        incumbent_params = {
            name: float(value)
            for name, value in (self.state.get("incumbent_params") or {}).items()
        }
        if set(incumbent_params) != set(ACTIVE_PARAMS):
            raise RuntimeError(
                "stagnation refinement requires a complete incumbent vector"
            )
        accepted_before = int(self.state.get("accepted") or 0)
        prior_refinement_count = int(
            self.state.get("incumbent_centered_stagnation_refinement_count") or 0
        )
        normalized_width_before = float(
            self.state.get("normalized_width")
            or 0.5 ** (accepted_before + prior_refinement_count)
        )
        incumbent_before = copy.deepcopy(incumbent_params)
        validation_before = copy.deepcopy(self.state.get("incumbent_validation"))
        refined_ranges, shrink = binary_shrink_parameter_ranges(
            self.ranges,
            incumbent_params,
            ACTIVE_PARAMS,
        )
        changed = any(
            not math.isclose(
                float(self.ranges[name].min_value),
                float(refined_ranges[name].min_value),
                rel_tol=0.0,
                abs_tol=1e-15,
            )
            or not math.isclose(
                float(self.ranges[name].max_value),
                float(refined_ranges[name].max_value),
                rel_tol=0.0,
                abs_tol=1e-15,
            )
            for name in ACTIVE_PARAMS
        )
        if not changed:
            raise RuntimeError(
                "incumbent-centered stagnation refinement produced no range change"
            )
        self.ranges = refined_ranges
        refinement_index = prior_refinement_count + 1
        effective_shrink_count = accepted_before + refinement_index
        normalized_width_after = self.interval_progress_width(effective_shrink_count)
        receipt = {
            "contract": "incumbent_centered_stagnation_binary_refinement_v1",
            "refinement_index": refinement_index,
            "trigger_round_index": int(round_index),
            "reason": str(reason),
            "accepted": accepted_before,
            "accepted_target": self.accepted_target,
            "anchor_source": "last_qps_monotonic_accepted_incumbent",
            "anchor_params": copy.deepcopy(incumbent_params),
            "range_shrink": copy.deepcopy(shrink),
            "accepted_unchanged": True,
            "incumbent_unchanged": True,
            "incumbent_validation_unchanged": True,
            "gradient_state_unchanged": True,
            "rejected_candidate_used_as_anchor": False,
            "counts_as_accepted_improvement": False,
            "accepted_normalized_width": 0.5 ** accepted_before,
            "effective_binary_shrink_count": effective_shrink_count,
            "normalized_width_before": normalized_width_before,
            "normalized_width_after": normalized_width_after,
            "normalized_width_unchanged": False,
            "convergence_progress_contract": (
                EFFECTIVE_SEARCH_PROGRESS_CONTRACT
            ),
            "next_round_uses_refined_ranges": True,
            "physical_workload_invocations": 0,
        }
        history = self.state.setdefault(
            "incumbent_centered_stagnation_refinements",
            [],
        )
        if not isinstance(history, list):
            raise RuntimeError(
                "incumbent_centered_stagnation_refinements must be a list"
            )
        history.append(copy.deepcopy(receipt))
        self.state["incumbent_centered_stagnation_refinement_count"] = (
            refinement_index
        )
        self.state["accepted_normalized_width"] = 0.5 ** accepted_before
        self.state["effective_binary_shrink_count"] = effective_shrink_count
        self.state["normalized_width"] = normalized_width_after
        self.state["convergence_progress_contract"] = (
            EFFECTIVE_SEARCH_PROGRESS_CONTRACT
        )
        self.state["consecutive_no_improvement_rounds"] = 0
        self.state["phase"] = "formal_search"
        if int(self.state.get("accepted") or 0) != accepted_before:
            raise RuntimeError("stagnation refinement changed accepted")
        if self.state.get("incumbent_params") != incumbent_before:
            raise RuntimeError("stagnation refinement changed incumbent params")
        if self.state.get("incumbent_validation") != validation_before:
            raise RuntimeError("stagnation refinement changed incumbent validation")
        self.save_state()
        self.log(
            "algorithm_c_incumbent_centered_stagnation_shrink",
            **receipt,
        )
        return receipt

    def finish_no_improvement_round(
        self,
        *,
        round_index: int,
        reason: str,
        exhaustive_current_interval: bool,
    ) -> bool:
        """Keep the incumbent and refine after two exhaustive interval misses.

        Two complete ordinary/local/current-interval searches are evidence
        to search more finely around the last accepted point. The incumbent
        remains the QPS reference and no rejected point is promoted to an
        acceptance. No separate post-precision confirmation round is issued.
        """

        streak = self.record_no_improvement_round()
        self.state["phase"] = "formal_search"
        self.save_state()
        self.log(
            "algorithm_c_no_qps_improvement_round_completed",
            round_index=int(round_index),
            reason=str(reason),
            consecutive_no_improvement_rounds=streak,
            required_consecutive_rounds=self.no_improvement_convergence_rounds,
            exhaustive_current_interval=bool(exhaustive_current_interval),
            incumbent_selected_as_round_baseline=True,
            counts_as_accepted_improvement=False,
            accepted_unchanged=True,
            incumbent_unchanged=True,
            ranges_unchanged=True,
            gradient_state_unchanged=True,
        )
        if streak < self.no_improvement_convergence_rounds:
            return False
        accepted_count = int(self.state.get("accepted") or 0)
        refinement_count = int(
            self.state.get("incumbent_centered_stagnation_refinement_count") or 0
        )
        effective_width = float(
            self.state.get("normalized_width")
            or 0.5 ** (accepted_count + refinement_count)
        )
        width_threshold = float(
            getattr(
                self,
                "normalized_width_threshold",
                0.5 ** int(getattr(self, "accepted_target", 1)),
            )
        )
        if effective_width > width_threshold + 1e-12:
            refinement = self.refine_ranges_around_current_incumbent(
                round_index=round_index,
                reason="complete_interval_search_without_higher_qps",
            )
            refined_width = float(refinement["normalized_width_after"])
            if refined_width <= width_threshold + 1e-12:
                self.finalize_on_current_incumbent(
                    reason=(
                        "effective_interval_reached_threshold_without_"
                        "stability_confirmation"
                    ),
                    round_index=round_index,
                    exhaustive_global_coverage=exhaustive_current_interval,
                )
                return True
            return False
        self.finalize_on_current_incumbent(
            reason=(
                "effective_interval_at_or_below_threshold_without_"
                "stability_confirmation"
            ),
            round_index=round_index,
            exhaustive_global_coverage=exhaustive_current_interval,
        )
        return True

    def finalize_precision_terminal_without_stability_confirmation(self) -> bool:
        """Finalize an already-refined one-percent state without more replay."""

        if int(self.state.get("accepted") or 0) >= self.accepted_target:
            return False
        if self.state.get("phase") in {
            "complete",
            "converged_on_incumbent_clean",
            "incumbent_convergence_pending_clean_restore",
        }:
            return False
        effective_width = float(self.state.get("normalized_width") or 1.0)
        if effective_width > self.normalized_width_threshold + 1e-12:
            return False
        refinement_count = int(
            self.state.get("incumbent_centered_stagnation_refinement_count") or 0
        )
        if refinement_count < 1:
            return False
        self.log(
            "algorithm_c_stability_confirmation_removed_precision_terminal",
            accepted=int(self.state.get("accepted") or 0),
            accepted_target=self.accepted_target,
            round_index=int(self.state.get("round_index") or 0),
            normalized_width=effective_width,
            normalized_width_threshold=self.normalized_width_threshold,
            incumbent_centered_stagnation_refinement_count=refinement_count,
            accepted_unchanged=True,
            incumbent_unchanged=True,
            ranges_unchanged=True,
            gradient_state_unchanged=True,
            physical_workload_invocations=0,
        )
        self.finalize_on_current_incumbent(
            reason="precision_already_reached_without_stability_confirmation",
            round_index=int(self.state.get("round_index") or 0),
            exhaustive_global_coverage=False,
        )
        return True

    def reopen_under_target_incumbent_convergence(self) -> bool:
        """Resume a legacy two-miss terminal that stopped below target.

        The superseded state and manifest are copied into append-only audit
        evidence before the incumbent interval is refined.  Existing real
        replay identities and physical/valid accounting are retained.
        """

        if not (
            self.state.get("phase") == "converged_on_incumbent_clean"
            and self.state.get("completion_contract")
            == INCUMBENT_CONVERGENCE_CONTRACT
            and int(self.state.get("accepted") or 0) < self.accepted_target
        ):
            return False
        effective_width = float(
            self.state.get("normalized_width")
            or (self.state.get("incumbent_convergence") or {}).get(
                "effective_normalized_width"
            )
            or 1.0
        )
        # Only legacy under-target terminals that stopped before reaching the
        # configured interval precision need another incumbent-centered
        # refinement.  A terminal at or below the precision threshold has
        # already completed the required two fine-interval miss rounds and
        # must remain authoritative across process restarts.
        normalized_width_threshold = float(
            getattr(
                self,
                "normalized_width_threshold",
                self.state.get("normalized_width_threshold")
                or (self.state.get("incumbent_convergence") or {}).get(
                    "normalized_width_threshold"
                )
                or 0.01,
            )
        )
        if effective_width <= normalized_width_threshold + 1e-12:
            return False
        accepted_before = int(self.state.get("accepted") or 0)
        valid_before = int(self.state.get("valid_replays") or 0)
        physical_before = int(
            self.state.get("physical_workload_invocations") or 0
        )
        authority_path = self.output_dir / "authority_manifest.json"
        archived_authority_path: Optional[Path] = None
        authority_sha256 = ""
        if authority_path.is_file():
            authority_payload = read_json(authority_path)
            authority_sha256 = hashlib.sha256(authority_path.read_bytes()).hexdigest()
            archive_dir = self.output_dir / "superseded_authorities"
            archive_dir.mkdir(parents=True, exist_ok=True)
            archived_authority_path = archive_dir / (
                "authority_manifest_round_"
                f"{int(self.state.get('round_index') or 0):03d}_"
                f"{authority_sha256[:16]}.json"
            )
            if not archived_authority_path.is_file():
                write_json(archived_authority_path, authority_payload)
        superseded = {
            "contract": "under_target_incumbent_convergence_reopen_v1",
            "reopened_at_epoch_s": time.time(),
            "accepted": accepted_before,
            "accepted_target": self.accepted_target,
            "round_index": int(self.state.get("round_index") or 0),
            "valid_replays": valid_before,
            "physical_workload_invocations": physical_before,
            "incumbent_convergence": copy.deepcopy(
                self.state.get("incumbent_convergence")
            ),
            "final_champion": copy.deepcopy(self.state.get("final_champion")),
            "authority_manifest_sha256": authority_sha256,
            "archived_authority_path": (
                str(archived_authority_path)
                if archived_authority_path is not None
                else ""
            ),
            "existing_replays_retained": True,
            "accepted_unchanged": True,
            "incumbent_unchanged": True,
            "rejected_candidate_promoted": False,
        }
        records = self.state.setdefault("superseded_terminal_records", [])
        if not isinstance(records, list):
            raise RuntimeError("superseded_terminal_records must be a list")
        records.append(copy.deepcopy(superseded))
        self.state["completion_contract"] = None
        self.state["incumbent_convergence"] = None
        self.state["final_champion"] = None
        self.state["repeatability"] = {
            "required": False,
            "performed": False,
            "passed": None,
            "reason": "superseded_under_target_incumbent_convergence",
            "physical_workload_invocations": 0,
        }
        self.state["phase"] = "formal_search"
        self.refine_ranges_around_current_incumbent(
            round_index=int(self.state.get("round_index") or 0),
            reason="reopen_under_target_two_miss_terminal",
        )
        if (
            int(self.state.get("accepted") or 0) != accepted_before
            or int(self.state.get("valid_replays") or 0) != valid_before
            or int(self.state.get("physical_workload_invocations") or 0)
            != physical_before
        ):
            raise RuntimeError("under-target convergence reopen changed accounting")
        self.log(
            "algorithm_c_under_target_incumbent_convergence_reopened",
            **superseded,
        )
        return True

    def finalize_on_current_incumbent(
        self,
        *,
        reason: str,
        round_index: int,
        exhaustive_global_coverage: bool,
    ) -> None:
        """Converge without fabricating a repeated acceptance.

        A rejected candidate cannot shrink ranges or advance ``accepted``.
        The last accepted incumbent is already a legal in-range point, so a
        pair of consecutive exhaustive no-improvement rounds terminates on
        that point and records the distinction explicitly.
        """

        archive = self.state.get("accepted_candidate_archive")
        if isinstance(archive, list) and archive:
            champion = copy.deepcopy(archive[-1])
            champion_source = "last_monotonic_qps_accepted_candidate"
        else:
            canonical = copy.deepcopy(self.state.get("canonical_baseline") or {})
            champion = {
                "contract": "canonical_baseline_incumbent_without_improvement_v1",
                "accepted_index": 0,
                "round_index": 0,
                "params": copy.deepcopy(self.state.get("incumbent_params")),
                "normalized": canonical,
                "decision": None,
                "source": "canonical_baseline_no_candidate_accepted",
            }
            champion_source = "canonical_baseline"
        accepted_count = int(self.state.get("accepted") or 0)
        refinement_count = int(
            self.state.get("incumbent_centered_stagnation_refinement_count") or 0
        )
        effective_shrink_count = accepted_count + refinement_count
        final_normalized_width = 0.5 ** effective_shrink_count
        convergence = {
            "contract": INCUMBENT_CONVERGENCE_CONTRACT,
            "reason": str(reason),
            "round_index": int(round_index),
            "outer_round_limit": self.outer_round_limit,
            "accepted_improvements": int(self.state.get("accepted") or 0),
            "incumbent_centered_refinements": refinement_count,
            "effective_binary_shrink_count": effective_shrink_count,
            "effective_normalized_width": final_normalized_width,
            "normalized_width_threshold": self.normalized_width_threshold,
            "convergence_progress_contract": (
                EFFECTIVE_SEARCH_PROGRESS_CONTRACT
            ),
            "accepted_target_is_not_required_for_incumbent_convergence": True,
            "incumbent_selected_as_convergence_baseline": True,
            "counts_as_accepted_improvement": False,
            "accepted_counter_unchanged_by_baseline_selection": True,
            "consecutive_no_improvement_rounds": int(
                self.state.get("consecutive_no_improvement_rounds") or 0
            ),
            "required_no_improvement_rounds": (
                self.no_improvement_convergence_rounds
            ),
            "exhaustive_global_coverage": bool(exhaustive_global_coverage),
            "incumbent_unchanged_by_rejected_rounds": True,
            "ranges_unchanged_by_rejected_rounds": True,
            "gradient_state_unchanged_by_rejected_rounds": True,
            "technical_failures_excluded": True,
            "external_algorithm_evidence_used": False,
            "champion_source": champion_source,
        }
        self.state["accepted_improvements"] = int(
            self.state.get("accepted") or 0
        )
        self.state["normalized_width"] = final_normalized_width
        self.state["best_feasible_champion"] = copy.deepcopy(champion)
        self.state["final_champion"] = copy.deepcopy(champion)
        self.state["post_convergence_stability_required"] = False
        self.state["completion_contract"] = INCUMBENT_CONVERGENCE_CONTRACT
        self.state["consecutive_stable_rounds"] = int(
            self.state.get("consecutive_no_improvement_rounds") or 0
        )
        self.state["repeatability"] = {
            "required": False,
            "performed": False,
            "passed": None,
            "reason": "outer_no_improvement_is_search_convergence_not_engineering_stability",
            "physical_workload_invocations": 0,
        }
        # Keep the local event payload independent from durable state.  The
        # clean-restore receipt is attached to the durable convergence record
        # below; sharing the same dict would also mutate ``convergence`` and
        # pass ``clean_restore_receipt`` twice to ``self.log``.
        self.state["incumbent_convergence"] = copy.deepcopy(convergence)
        self.state["phase"] = "incumbent_convergence_pending_clean_restore"
        self.save_state()
        self.log(
            "algorithm_c_incumbent_convergence_restore_pending",
            **convergence,
            physical_workload_invocations=0,
        )
        restore_receipt = self.restore_clean_baseline(
            reason="formal_incumbent_convergence"
        )
        self.state["incumbent_convergence"]["clean_restore_receipt"] = (
            copy.deepcopy(restore_receipt)
        )
        self.state["phase"] = "converged_on_incumbent_clean"
        self.save_state()
        self.log(
            "algorithm_c_converged_on_current_incumbent",
            **convergence,
            incumbent_params=copy.deepcopy(self.state.get("incumbent_params")),
            incumbent_validation=copy.deepcopy(
                self.state.get("incumbent_validation")
            ),
            final_champion=copy.deepcopy(champion),
            clean_restore_receipt=copy.deepcopy(restore_receipt),
            physical_workload_invocations=0,
        )
        self.write_authority_manifest(final_normalized_width)

    def prepare_stability_resume_epoch(self) -> None:
        """Migrate only confirmation-control state into a fresh cohort epoch.

        The explicit environment variable is required so an ordinary process
        restart can never reset a formal wave sequence.  Semantic optimizer
        state (accepted/incumbent/ranges/gradient) is deliberately untouched.
        """
        epoch = str(
            os.environ.get("BONECT_ALGORITHM_C_STABILITY_RESUME_EPOCH") or ""
        ).strip()
        if not epoch:
            return
        if int(self.state.get("accepted") or 0) != self.accepted_target:
            raise RuntimeError(
                "stability resume epoch is only valid after accepted target"
            )
        if str(self.state.get("stability_confirmation_epoch") or "") == epoch:
            return
        previous = {
            "epoch": self.state.get("stability_confirmation_epoch"),
            "protocol": self.state.get("stability_confirmation_protocol"),
            "phase": self.state.get("phase"),
            "formal_candidate_wave_sequence": int(
                self.state.get("formal_candidate_wave_sequence") or 0
            ),
            "consecutive_stable_rounds": int(
                self.state.get("consecutive_stable_rounds") or 0
            ),
            "stability_rounds": copy.deepcopy(
                self.state.get("stability_rounds") or []
            ),
            "repeatability": copy.deepcopy(self.state.get("repeatability") or {}),
        }
        archive = self.state.setdefault("stability_confirmation_epoch_archive", [])
        if not isinstance(archive, list):
            raise RuntimeError("invalid stability confirmation epoch archive")
        archive.append(previous)
        self.state["stability_confirmation_epoch"] = epoch
        self.state["stability_confirmation_protocol"] = (
            STABILITY_CONFIRMATION_PROTOCOL
        )
        self.state["formal_candidate_wave_sequence"] = 0
        self.state["consecutive_stable_rounds"] = 0
        self.state["stability_rounds"] = []
        self.state["repeatability"] = {}
        self.state["phase"] = "accepted_target_pending_stability"
        self.save_state()
        self.log(
            "algorithm_c_stability_confirmation_epoch_migrated",
            previous=previous,
            new_epoch=epoch,
            protocol=STABILITY_CONFIRMATION_PROTOCOL,
            accepted=int(self.state["accepted"]),
            accepted_unchanged=True,
            incumbent_unchanged=True,
            ranges_unchanged=True,
            gradient_state_unchanged=True,
            physical_workload_invocations=0,
            technical_failures=0,
        )

    def write_authority_manifest(self, final_normalized_width: float) -> None:
        """Write the final authority after the configured convergence gates pass."""
        post_convergence_stability_required = bool(
            self.state.get("post_convergence_stability_required", True)
        )
        write_json(
            self.output_dir / "authority_manifest.json",
            {
                "scene": self.algorithm_cfg.get("scene"),
                "method": self.state["method"],
                "accepted": int(self.state["accepted"]),
                "accepted_improvements": int(
                    self.state.get("accepted_improvements")
                    or self.state.get("accepted")
                    or 0
                ),
                "accepted_target": self.accepted_target,
                "outer_rounds": int(self.state.get("round_index") or 0),
                "outer_round_limit": self.outer_round_limit,
                "search_convergence_contract": self.state.get(
                    "completion_contract"
                ),
                "normalized_width": final_normalized_width,
                "normalized_width_threshold": self.normalized_width_threshold,
                "post_convergence_stability_required": (
                    post_convergence_stability_required
                ),
                "required_consecutive_stable_rounds": (
                    self.stability_confirmation_rounds
                    if post_convergence_stability_required
                    else 0
                ),
                "consecutive_stable_rounds": int(
                    self.state["consecutive_stable_rounds"]
                ),
                "state_path": str(self.state_path),
                "state_sha256": hashlib.sha256(
                    self.state_path.read_bytes()
                ).hexdigest(),
                "history_path": str(self.log_path),
                "history_sha256": hashlib.sha256(
                    self.log_path.read_bytes()
                ).hexdigest(),
                "experience_table_path": str(self.t_artifact_path),
                "experience_table_sha256": hashlib.sha256(
                    self.t_artifact_path.read_bytes()
                ).hexdigest(),
                "manual_mutation_after_formal_start": False,
                "technical_failures_do_not_mutate_state": True,
                "search_incumbent": {
                    "params": copy.deepcopy(self.state.get("incumbent_params")),
                    "validation": copy.deepcopy(
                        self.state.get("incumbent_validation")
                    ),
                },
                "best_feasible_champion": copy.deepcopy(
                    self.state.get("best_feasible_champion")
                ),
                "final_champion": copy.deepcopy(
                    self.state.get("final_champion")
                ),
                "champion_selection_contract": (
                    "strict_then_secondary_then_fallback_qps_resource_v1"
                ),
                "repeatability": self.state["repeatability"],
            },
        )

    def finalize_without_post_convergence_stability(
        self,
        final_normalized_width: float,
    ) -> bool:
        """Finish at convergence without issuing extra stability workloads."""
        champions = self._ordered_feasible_champions()
        if not champions:
            raise RuntimeError("accepted target reached without a champion archive")
        champion = copy.deepcopy(champions[0])
        self.state["best_feasible_champion"] = copy.deepcopy(champion)
        self.state["final_champion"] = copy.deepcopy(champion)
        self.state["normalized_width"] = final_normalized_width
        self.state["post_convergence_stability_required"] = False
        self.state["completion_contract"] = POST_CONVERGENCE_STABILITY_POLICY
        self.state["stability_confirmation_protocol"] = None
        self.state["consecutive_stable_rounds"] = 0
        self.state["repeatability"] = {
            "required": False,
            "performed": False,
            "passed": None,
            "reason": "post_convergence_stability_is_engineering_only",
            "physical_workload_invocations": 0,
        }
        self.state["phase"] = "complete"
        self.save_state()
        self.log(
            "algorithm_c_completed_without_post_convergence_stability",
            completion_contract=POST_CONVERGENCE_STABILITY_POLICY,
            accepted=int(self.state["accepted"]),
            accepted_target=self.accepted_target,
            normalized_width=final_normalized_width,
            normalized_width_threshold=self.normalized_width_threshold,
            final_champion=copy.deepcopy(champion),
            accepted_unchanged=True,
            incumbent_unchanged=True,
            ranges_unchanged=True,
            gradient_state_unchanged=True,
            physical_workload_invocations=0,
        )
        self.restore_clean_baseline(reason="formal_completion_without_stability")
        self.write_authority_manifest(final_normalized_width)
        return True

    def finalize_existing_stability_evidence(self) -> bool:
        """Reassess a completed bounded confirmation without new workload.

        This is intentionally limited to campaigns that already reached the
        accepted target, completed every bounded confirmation attempt, and
        restored the clean baseline.  It changes only the interpretation of
        repeatability evidence; accepted/incumbent/ranges/gradient and physical
        accounting remain untouched.
        """
        if int(self.state.get("accepted") or 0) != self.accepted_target:
            return False
        final_normalized_width = float(
            self.state.get("normalized_width")
            or 0.5 ** int(self.state["accepted"])
        )
        if final_normalized_width > self.normalized_width_threshold + 1e-12:
            return False
        if (
            self.state.get("phase") == "complete"
            and self.state.get("stability_confirmation_protocol")
            == STABILITY_CONFIRMATION_PROTOCOL
            and (self.state.get("repeatability") or {}).get("passed") is True
        ):
            return True
        attempts = self.state.get("stability_rounds")
        if not isinstance(attempts, list) or len(attempts) != (
            self.stability_confirmation_max_attempts
        ):
            return False
        assessment = assess_stability_attempts(
            attempts,
            required_consecutive_rounds=self.stability_confirmation_rounds,
            required_resource_names=stability_required_resource_names(self.target),
        )
        if assessment.get("passed") is not True:
            return False
        selected_attempts = set(assessment["selected_attempts"])
        selected_rows = [
            row
            for row in attempts
            if int(row.get("attempt") or 0) in selected_attempts
        ]
        if len(selected_rows) != self.stability_confirmation_rounds or any(
            row.get("technical_failure") is True for row in selected_rows
        ):
            raise RuntimeError("selected cached stability evidence is not valid")
        archive = self.state.setdefault("stability_redecision_archive", [])
        if not isinstance(archive, list):
            raise RuntimeError("invalid stability redecision archive")
        archive.append(
            {
                "previous_protocol": self.state.get(
                    "stability_confirmation_protocol"
                ),
                "previous_phase": self.state.get("phase"),
                "previous_repeatability": copy.deepcopy(
                    self.state.get("repeatability") or {}
                ),
                "accepted": int(self.state["accepted"]),
                "accepted_unchanged": True,
                "physical_workload_invocations": 0,
            }
        )
        self.state["stability_confirmation_protocol"] = (
            STABILITY_CONFIRMATION_PROTOCOL
        )
        self.state["consecutive_stable_rounds"] = (
            self.stability_confirmation_rounds
        )
        self.state["repeatability"] = {
            "contract": STABILITY_CONFIRMATION_PROTOCOL,
            "passed": True,
            "required_consecutive_rounds": self.stability_confirmation_rounds,
            "maximum_attempts": self.stability_confirmation_max_attempts,
            "attempts_executed": len(attempts),
            "selected_consecutive_attempts": assessment["selected_attempts"],
            "consecutive_qps_relative_gap": assessment[
                "consecutive_qps_relative_gap"
            ],
            "consecutive_resource_relative_gaps": assessment[
                "consecutive_resource_relative_gaps"
            ],
            "same_worker_consecutive_relative_gaps": assessment[
                "same_worker_consecutive_relative_gaps"
            ],
            "required_resource_names": assessment["required_resource_names"],
            "diagnostic_only_resource_names": assessment[
                "diagnostic_only_resource_names"
            ],
            "temporal_metric_diagnostics_passed": assessment[
                "temporal_metric_diagnostics_passed"
            ],
            "qps_temporal_gap_is_diagnostic_only": True,
            "required_resource_temporal_gaps_are_hard": True,
            "within_attempt_cross_worker_dispersion_is_diagnostic_only": True,
            "attempts": copy.deepcopy(attempts),
            "cached_evidence_redecision": True,
            "additional_physical_workload_invocations": 0,
        }
        self.state["phase"] = "complete"
        self.save_state()
        self.log(
            "algorithm_c_stability_cached_evidence_redecided",
            protocol=STABILITY_CONFIRMATION_PROTOCOL,
            selected_consecutive_attempts=assessment["selected_attempts"],
            same_worker_consecutive_relative_gaps=assessment[
                "same_worker_consecutive_relative_gaps"
            ],
            accepted=int(self.state["accepted"]),
            accepted_unchanged=True,
            incumbent_unchanged=True,
            ranges_unchanged=True,
            gradient_state_unchanged=True,
            physical_workload_invocations=0,
            technical_failures_excluded=True,
        )
        self.write_authority_manifest(final_normalized_width)
        return True

    def _ordered_feasible_champions(self) -> List[Dict[str, Any]]:
        """Return unique accepted real points in final-selection order."""
        archive = self.state.get("accepted_candidate_archive")
        if not isinstance(archive, list):
            return []
        best_by_identity: Dict[str, Dict[str, Any]] = {}
        for raw in archive:
            if not isinstance(raw, Mapping):
                continue
            candidate = copy.deepcopy(dict(raw))
            if not isinstance(candidate.get("final_decision"), Mapping):
                normalized = candidate.get("normalized")
                canonical = self.state.get("canonical_baseline")
                if isinstance(normalized, Mapping) and isinstance(
                    canonical, Mapping
                ):
                    final_normalized = copy.deepcopy(dict(normalized))
                    final_normalized["qps_noise_model"] = copy.deepcopy(
                        self.state.get("qps_noise_model") or {}
                    )
                    candidate["final_decision"] = (
                        outer_semantic_acceptance_details(
                            final_normalized,
                            canonical,
                            canonical,
                            target=self.target,
                            cfg=self.cfg,
                            consecutive_missed_outer_rounds=0,
                        )
                    )
                    candidate["final_decision_reference"] = (
                        "canonical_baseline"
                    )
            final_decision = candidate.get("final_decision")
            if (
                isinstance(final_decision, Mapping)
                and final_decision.get("accepted") is not True
            ):
                continue
            identity = str(candidate.get("candidate_identity") or "")
            if not identity:
                continue
            previous = best_by_identity.get(identity)
            if (
                previous is None
                or feasible_champion_sort_key(candidate)
                > feasible_champion_sort_key(previous)
            ):
                best_by_identity[identity] = candidate
        return sorted(
            best_by_identity.values(),
            key=feasible_champion_sort_key,
            reverse=True,
        )

    def _confirm_one_champion(
        self,
        champion: Mapping[str, Any],
        *,
        champion_rank: int,
    ) -> Dict[str, Any]:
        """Replay one champion until two semantic stability waves pass."""
        original_decision = champion.get("final_decision") or champion.get(
            "decision"
        )
        original_decision = (
            original_decision if isinstance(original_decision, Mapping) else {}
        )
        original_tier = str(
            original_decision.get("acceptance_tier") or "fallback"
        )
        stability_required_tier = "fallback"
        required_tier_rank = ACCEPTANCE_TIER_RANK[stability_required_tier]
        params = champion.get("params")
        if not isinstance(params, Mapping):
            raise RuntimeError("champion lacks parameter vector")
        champion_identity = str(champion.get("candidate_identity") or "")
        active = self.state.get("active_champion_validation")
        resumable = bool(
            isinstance(active, Mapping)
            and str(active.get("candidate_identity") or "")
            == champion_identity
            and int(active.get("champion_rank") or 0) == champion_rank
        )
        legacy_final = self.state.get("final_champion")
        legacy_resumable = bool(
            not isinstance(active, Mapping)
            and isinstance(legacy_final, Mapping)
            and str(legacy_final.get("candidate_identity") or "")
            == champion_identity
            and isinstance(self.state.get("stability_rounds"), list)
            and bool(self.state.get("stability_rounds"))
        )
        if resumable or legacy_resumable:
            source_rounds = (
                active.get("attempts")
                if isinstance(active, Mapping)
                else self.state.get("stability_rounds")
            )
            semantic_rounds = canonicalize_stability_attempt_outcomes(
                list(source_rounds or [])
            )
            physical_attempt = max(
                [int(row.get("physical_attempt") or 0) for row in semantic_rounds]
                + [int((active or {}).get("physical_attempt") or 0)]
            )
            consecutive_streak = 0
            for row in semantic_rounds:
                consecutive_streak = (
                    consecutive_streak + 1 if row.get("passed") is True else 0
                )
            self.log(
                "algorithm_c_champion_stability_resumed",
                champion_rank=champion_rank,
                candidate_identity=champion_identity,
                semantic_attempts_reused=len(semantic_rounds),
                next_physical_attempt=physical_attempt + 1,
                accepted_unchanged=True,
                incumbent_unchanged=True,
                ranges_unchanged=True,
                gradient_state_unchanged=True,
                physical_workload_invocations=0,
            )
        else:
            semantic_rounds = []
            validation_archive = self.state.get("champion_validation_archive")
            validation_archive = (
                validation_archive if isinstance(validation_archive, list) else []
            )
            historical_physical_attempts = [
                int((row.get("result") or {}).get("physical_attempts") or 0)
                for row in validation_archive
                if isinstance(row, Mapping)
                and isinstance(row.get("champion"), Mapping)
                and str(row["champion"].get("candidate_identity") or "")
                == champion_identity
                and isinstance(row.get("result"), Mapping)
            ]
            physical_attempt = max(historical_physical_attempts or [0])
            consecutive_streak = 0
            self.state["stability_rounds"] = []
            self.state["consecutive_stable_rounds"] = 0
        self.state["final_champion"] = copy.deepcopy(dict(champion))
        self.state["phase"] = "accepted_target_pending_champion_stability"
        self.state["active_champion_validation"] = {
            "contract": "resumable_champion_stability_v1",
            "champion_rank": champion_rank,
            "candidate_identity": champion_identity,
            "required_acceptance_tier": original_tier,
            "stability_required_acceptance_tier": stability_required_tier,
            "physical_attempt": physical_attempt,
            "attempts": copy.deepcopy(semantic_rounds),
        }
        self.save_state()
        maximum_physical_attempts = self.stability_confirmation_max_attempts * 2
        while (
            len(semantic_rounds) < self.stability_confirmation_max_attempts
            and physical_attempt < maximum_physical_attempts
        ):
            physical_attempt += 1
            self.state["active_champion_validation"]["physical_attempt"] = (
                physical_attempt
            )
            self.save_state()
            confirmation_candidates = [
                {
                    "rank": index + 1,
                    "optimized_params": copy.deepcopy(dict(params)),
                    "confirmation_only": True,
                    "stability_attempt": physical_attempt,
                    "champion_rank": champion_rank,
                    "three_level_fingerprint": {
                        "plan_level": (
                            f"champion_{champion_rank:02d}_stability_"
                            f"attempt_{physical_attempt:02d}_worker_{index + 1}"
                        )
                    },
                }
                for index in range(self.replay_parallelism)
            ]
            confirmations = self.replay_batch(
                confirmation_candidates,
                round_index=int(self.state["round_index"]),
                batch_kind=(
                    f"champion_{champion_rank:02d}_stability_"
                    f"attempt_{physical_attempt:02d}"
                ),
            )
            if self.stop_requested:
                return {"status": "signal_stop", "physical_attempts": physical_attempt}
            if not (
                len(confirmations) == self.replay_parallelism
                and all(physical_valid(row) for row in confirmations)
            ):
                self.log(
                    "algorithm_c_champion_stability_technical_failure",
                    champion_rank=champion_rank,
                    physical_attempt=physical_attempt,
                    valid_confirmation_count=sum(
                        physical_valid(row) for row in confirmations
                    ),
                    semantic_attempts_unchanged=len(semantic_rounds),
                    champion_not_demoted=True,
                )
                continue
            normalized = [
                normalize_to_worker_baseline(
                    row,
                    self.state["baseline_by_worker"][str(row["worker_key"])],
                    self.state["canonical_baseline"],
                    self.capacity,
                )
                for row in confirmations
            ]
            decisions: List[Dict[str, Any]] = []
            for row in normalized:
                row["qps_noise_model"] = copy.deepcopy(
                    self.state.get("qps_noise_model") or {}
                )
                decisions.append(
                    outer_semantic_acceptance_details(
                        row,
                        self.state["canonical_baseline"],
                        self.state["canonical_baseline"],
                        target=self.target,
                        cfg=self.cfg,
                        consecutive_missed_outer_rounds=0,
                    )
                )
            semantic_tier_preserved = all(
                decision.get("accepted") is True
                and ACCEPTANCE_TIER_RANK.get(
                    str(decision.get("acceptance_tier") or ""),
                    0,
                )
                >= required_tier_rank
                for decision in decisions
            )
            qps_values = [float(row["db_qps"]) for row in normalized]
            qps_mean = sum(qps_values) / len(qps_values)
            qps_relative_gap = abs(qps_values[0] - qps_values[1]) / max(
                qps_mean,
                1e-12,
            )
            resource_means = {
                name: sum(float(row["resources"][name]) for row in normalized)
                / len(normalized)
                for name in ("cpu", "memory", "io")
            }
            resource_relative_gaps = {
                name: abs(
                    float(normalized[0]["resources"][name])
                    - float(normalized[1]["resources"][name])
                )
                / max(resource_means[name], 1e-12)
                for name in ("cpu", "memory", "io")
            }
            dispersion_passed = bool(
                qps_relative_gap <= 0.15
                and all(value <= 0.20 for value in resource_relative_gaps.values())
            )
            round_passed = bool(semantic_tier_preserved)
            semantic_attempt = len(semantic_rounds) + 1
            semantic_rounds.append(
                {
                    "attempt": semantic_attempt,
                    "physical_attempt": physical_attempt,
                    "passed": round_passed,
                    "technical_failure": False,
                    "semantic_tier_preserved": semantic_tier_preserved,
                    "within_attempt_worker_dispersion_passed": dispersion_passed,
                    "within_attempt_worker_dispersion_is_diagnostic_only": True,
                    "required_acceptance_tier": original_tier,
                    "original_required_acceptance_tier": original_tier,
                    "stability_required_acceptance_tier": stability_required_tier,
                    "confirmation_decisions": decisions,
                    "qps_mean": qps_mean,
                    "qps_relative_gap_between_workers": qps_relative_gap,
                    "resource_means": resource_means,
                    "resource_relative_gaps_between_workers": resource_relative_gaps,
                    "confirmations": normalized,
                }
            )
            consecutive_streak = consecutive_streak + 1 if round_passed else 0
            self.state["consecutive_stable_rounds"] = consecutive_streak
            self.state["stability_rounds"] = copy.deepcopy(semantic_rounds)
            self.state["active_champion_validation"]["attempts"] = copy.deepcopy(
                semantic_rounds
            )
            self.state["phase"] = "accepted_target_pending_champion_stability"
            self.save_state()
            self.log(
                "algorithm_c_champion_stability_attempt_completed",
                champion_rank=champion_rank,
                physical_attempt=physical_attempt,
                semantic_attempt=semantic_attempt,
                passed=round_passed,
                semantic_tier_preserved=semantic_tier_preserved,
                consecutive_stable_rounds=consecutive_streak,
            )
            if consecutive_streak >= self.stability_confirmation_rounds:
                break
        if not semantic_rounds:
            return {
                "status": "technical_failure",
                "physical_attempts": physical_attempt,
                "semantic_attempts": 0,
            }
        stability_required_resources = stability_required_resource_names(self.target)
        assessment = assess_stability_attempts(
            semantic_rounds,
            required_consecutive_rounds=self.stability_confirmation_rounds,
            required_resource_names=stability_required_resources,
        )
        return {
            "status": "passed" if assessment.get("passed") is True else "semantic_failed",
            "physical_attempts": physical_attempt,
            "semantic_attempts": len(semantic_rounds),
            "attempts": semantic_rounds,
            "assessment": assessment,
        }

    def finalize_best_feasible_champion(self, final_normalized_width: float) -> bool:
        """Validate process-best accepted candidates, falling back by rank."""
        champions = self._ordered_feasible_champions()
        if not champions:
            raise RuntimeError("accepted target reached without a champion archive")
        raw_archive = self.state.get("champion_validation_archive")
        validation_archive: List[Dict[str, Any]] = copy.deepcopy(
            raw_archive if isinstance(raw_archive, list) else []
        )
        completed_identities: Set[str] = set()
        cached_redecisions: Dict[str, Dict[str, Any]] = {}
        required_resources = stability_required_resource_names(self.target)
        for row in validation_archive:
            if not (
                isinstance(row, Mapping)
                and isinstance(row.get("champion"), Mapping)
                and isinstance(row.get("result"), Mapping)
                and row["result"].get("status") == "semantic_failed"
            ):
                continue
            candidate_identity = str(
                row["champion"].get("candidate_identity") or ""
            )
            archived_attempts = row["result"].get("attempts")
            archived_attempts = (
                archived_attempts if isinstance(archived_attempts, list) else []
            )
            archived_attempts = canonicalize_stability_attempt_outcomes(
                archived_attempts
            )
            assessment = assess_stability_attempts(
                archived_attempts,
                required_consecutive_rounds=self.stability_confirmation_rounds,
                required_resource_names=required_resources,
            )
            if assessment.get("passed") is True:
                cached_result = copy.deepcopy(dict(row["result"]))
                cached_result["status"] = "passed"
                cached_result["attempts"] = archived_attempts
                cached_result["assessment"] = assessment
                cached_result["cached_execution_domain_scope_redecision"] = True
                cached_result["additional_physical_workload_invocations"] = 0
                cached_redecisions[candidate_identity] = cached_result
            else:
                completed_identities.add(candidate_identity)
        for champion_rank, champion in enumerate(champions, start=1):
            candidate_identity = str(champion.get("candidate_identity") or "")
            if candidate_identity in completed_identities:
                continue
            if candidate_identity in cached_redecisions:
                result = cached_redecisions[candidate_identity]
                self.log(
                    "algorithm_c_champion_stability_execution_domain_scope_redecided",
                    champion_rank=champion_rank,
                    candidate_identity=candidate_identity,
                    required_resource_names=list(required_resources),
                    diagnostic_only_resource_names=result["assessment"][
                        "diagnostic_only_resource_names"
                    ],
                    selected_consecutive_attempts=result["assessment"][
                        "selected_attempts"
                    ],
                    accepted_unchanged=True,
                    incumbent_unchanged=True,
                    ranges_unchanged=True,
                    gradient_state_unchanged=True,
                    physical_workload_invocations=0,
                )
            else:
                result = self._confirm_one_champion(
                    champion,
                    champion_rank=champion_rank,
                )
            validation_archive.append(
                {
                    "champion_rank": champion_rank,
                    "champion": copy.deepcopy(champion),
                    "result": copy.deepcopy(result),
                }
            )
            self.state["champion_validation_archive"] = copy.deepcopy(
                validation_archive
            )
            self.state["active_champion_validation"] = None
            self.save_state()
            if result.get("status") == "signal_stop":
                self.finish_signal_stop("signal_stop_during_champion_stability")
                return False
            if result.get("status") == "technical_failure":
                self.state["phase"] = "champion_stability_technical_failure_clean"
                self.save_state()
                self.restore_clean_baseline(reason="champion_stability_technical_failure")
                self.log(
                    "algorithm_c_champion_stability_stopped_technically",
                    champion_rank=champion_rank,
                    champion_not_demoted=True,
                    next_champion_not_tried=True,
                )
                return False
            if result.get("status") != "passed":
                self.state["stability_rounds"] = []
                self.state["consecutive_stable_rounds"] = 0
                self.save_state()
                self.log(
                    "algorithm_c_champion_semantic_stability_failed",
                    champion_rank=champion_rank,
                    trying_next_champion=True,
                )
                continue
            assessment = result["assessment"]
            self.state["best_feasible_champion"] = copy.deepcopy(champion)
            self.state["final_champion"] = copy.deepcopy(champion)
            self.state["consecutive_stable_rounds"] = (
                self.stability_confirmation_rounds
            )
            self.state["repeatability"] = {
                "contract": STABILITY_CONFIRMATION_PROTOCOL,
                "passed": True,
                "champion_rank": champion_rank,
                "required_consecutive_rounds": self.stability_confirmation_rounds,
                "maximum_semantic_attempts": self.stability_confirmation_max_attempts,
                "technical_failures_do_not_count_or_demote": True,
                "selected_consecutive_attempts": assessment["selected_attempts"],
                "consecutive_qps_relative_gap": assessment[
                    "consecutive_qps_relative_gap"
                ],
                "consecutive_resource_relative_gaps": assessment[
                    "consecutive_resource_relative_gaps"
                ],
                "same_worker_consecutive_relative_gaps": assessment[
                    "same_worker_consecutive_relative_gaps"
                ],
                "required_resource_names": assessment["required_resource_names"],
                "diagnostic_only_resource_names": assessment[
                    "diagnostic_only_resource_names"
                ],
                "temporal_metric_diagnostics_passed": assessment[
                    "temporal_metric_diagnostics_passed"
                ],
                "qps_temporal_gap_is_diagnostic_only": True,
                "required_resource_temporal_gaps_are_hard": True,
                "within_attempt_cross_worker_dispersion_is_diagnostic_only": True,
                "attempts": copy.deepcopy(result["attempts"]),
            }
            self.state["normalized_width"] = final_normalized_width
            self.state["stability_confirmation_protocol"] = (
                STABILITY_CONFIRMATION_PROTOCOL
            )
            self.state["phase"] = "complete"
            self.save_state()
            self.restore_clean_baseline(reason="formal_champion_completion")
            self.write_authority_manifest(final_normalized_width)
            return True
        self.state["phase"] = "all_champions_semantic_stability_failed_clean"
        self.state["consecutive_stable_rounds"] = 0
        self.save_state()
        self.restore_clean_baseline(reason="all_champions_semantic_stability_failed")
        return False

    def recover_pending_signal_stop_restore(self) -> bool:
        """Finish an interrupted clean stop before resuming its outer round."""

        pending_stop_phase = str(self.state.get("phase") or "")
        if pending_stop_phase not in {
            "signal_stop_pending_clean_restore",
            "signal_stop_clean_restore_failed",
        }:
            return False
        receipt = self.restore_clean_baseline(
            reason="signal_stop_restart_recovery"
        )
        self.state["signal_stop_clean_receipt"] = copy.deepcopy(receipt)
        self.state["phase"] = "stopped_by_signal_clean"
        self.state.pop("last_signal_stop_restore_failure", None)
        self.save_state()
        self.log(
            "algorithm_c_signal_stop_restore_recovered_on_restart",
            prior_phase=pending_stop_phase,
            interrupted_phase=self.state.get("signal_stop_interrupted_phase"),
            interrupted_round_index=self.state.get(
                "signal_stop_interrupted_round_index"
            ),
            clean_receipt=copy.deepcopy(receipt),
            accepted_unchanged=True,
            incumbent_unchanged=True,
            ranges_unchanged=True,
            gradient_state_unchanged=True,
            physical_workload_invocations=0,
        )
        return True

    def effective_outer_round_limit(self) -> int:
        """Return the exploration limit without stability-confirmation grace."""

        return int(self.outer_round_limit)

    def reopen_truncated_fine_interval_convergence(self) -> bool:
        """Resume a clean max-round stop missing its final convergence miss."""

        if self.state.get("phase") != "nonconvergence_max_rounds_clean":
            return False
        if self.state.get("inflight_physical_wave"):
            return False
        configured_limit = int(self.outer_round_limit)
        effective_limit = self.effective_outer_round_limit()
        if effective_limit <= configured_limit:
            return False
        accepted_before = int(self.state.get("accepted") or 0)
        incumbent_before = copy.deepcopy(self.state.get("incumbent_params"))
        ranges_before = copy.deepcopy(self.state.get("ranges"))
        valid_before = int(self.state.get("valid_replays") or 0)
        physical_before = int(
            self.state.get("physical_workload_invocations") or 0
        )
        technical_before = int(self.state.get("technical_failures") or 0)
        archive = self.state.setdefault(
            "superseded_nonconvergence_records",
            [],
        )
        if not isinstance(archive, list):
            raise RuntimeError("superseded_nonconvergence_records must be a list")
        record = {
            "contract": "fine_interval_two_miss_grace_reopen_v1",
            "reopened_at_epoch_s": time.time(),
            "configured_outer_round_limit": configured_limit,
            "effective_outer_round_limit": effective_limit,
            "round_index": int(self.state.get("round_index") or 0),
            "accepted": accepted_before,
            "normalized_width": float(self.state.get("normalized_width") or 0.0),
            "normalized_width_threshold": self.normalized_width_threshold,
            "consecutive_no_improvement_rounds": int(
                self.state.get("consecutive_no_improvement_rounds") or 0
            ),
            "required_no_improvement_rounds": (
                self.no_improvement_convergence_rounds
            ),
            "prior_nonconvergence": copy.deepcopy(
                self.state.get("nonconvergence")
            ),
            "existing_replays_retained": True,
            "accepted_unchanged": True,
            "incumbent_unchanged": True,
            "ranges_unchanged": True,
            "gradient_state_unchanged": True,
            "physical_workload_invocations": 0,
        }
        archive.append(copy.deepcopy(record))
        self.state["phase"] = "formal_search"
        self.state["nonconvergence"] = None
        self.state["outer_round_limit_convergence_grace"] = {
            "contract": "fine_interval_two_miss_grace_v1",
            "configured_outer_round_limit": configured_limit,
            "effective_outer_round_limit": effective_limit,
            "missing_no_improvement_rounds": (
                int(self.no_improvement_convergence_rounds)
                - int(
                    self.state.get("consecutive_no_improvement_rounds") or 0
                )
            ),
        }
        if (
            int(self.state.get("accepted") or 0) != accepted_before
            or self.state.get("incumbent_params") != incumbent_before
            or self.state.get("ranges") != ranges_before
            or int(self.state.get("valid_replays") or 0) != valid_before
            or int(self.state.get("physical_workload_invocations") or 0)
            != physical_before
            or int(self.state.get("technical_failures") or 0)
            != technical_before
        ):
            raise RuntimeError("fine-interval convergence reopen mutated search state")
        self.save_state()
        self.log(
            "algorithm_c_fine_interval_convergence_grace_reopened",
            **record,
        )
        return True

    def run(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.recover_pending_signal_stop_restore()
        self.reopen_truncated_fine_interval_convergence()
        startup_phase = str(self.state.get("phase") or "")
        allow_legacy_stopped_clean_resume = (
            os.environ.get("BONECT_ALGORITHM_C_RESUME_LEGACY_STOPPED_ROUND", "0")
            == "1"
        )
        resume_round_index = unfinished_algorithm_c_round_resume_index(
            self.state,
            allow_legacy_stopped_clean=allow_legacy_stopped_clean_resume,
        )
        if (
            resume_round_index is not None
            and startup_phase == "stopped_by_signal_clean"
            and not self.state.get("signal_stop_interrupted_phase")
        ):
            self.log(
                "algorithm_c_legacy_stopped_clean_round_resume_authorized",
                round_index=int(resume_round_index),
                clean_receipt_passed=True,
                inflight_physical_wave_absent=True,
                accepted_unchanged=True,
                incumbent_unchanged=True,
                ranges_unchanged=True,
                gradient_state_unchanged=True,
            )
        self.log(
            "algorithm_c_lane_start",
            config=str(self.cfg_path),
            t_artifact=str(self.t_artifact_path),
            accepted=int(self.state["accepted"]),
            accepted_target=self.accepted_target,
            normalized_width_threshold=self.normalized_width_threshold,
            expected_normalized_width=self.convergence_contract[
                "expected_normalized_width"
            ],
            stability_confirmation_rounds=self.stability_confirmation_rounds,
            post_convergence_stability_required=False,
            replay_top_k=BASE_TOP_K,
            replay_parallelism=self.replay_parallelism,
            workload_contract=self.algorithm_cfg.get("workload_contract"),
            workload_runtime_execution_domain=(
                self.workload_runtime_execution_domain
            ),
        )
        self.reopen_under_target_incumbent_convergence()
        if self.finalize_precision_terminal_without_stability_confirmation():
            return
        completed_without_stability = bool(
            self.state.get("phase") == "complete"
            and self.state.get("completion_contract")
            == POST_CONVERGENCE_STABILITY_POLICY
            and self.state.get("post_convergence_stability_required") is False
        )
        completed_with_legacy_stability = bool(
            self.state.get("phase") == "complete"
            and self.state.get("stability_confirmation_protocol")
            == STABILITY_CONFIRMATION_PROTOCOL
            and (self.state.get("repeatability") or {}).get("passed") is True
        )
        completed_on_incumbent = bool(
            self.state.get("phase") == "converged_on_incumbent_clean"
            and self.state.get("completion_contract")
            == INCUMBENT_CONVERGENCE_CONTRACT
            and isinstance(self.state.get("final_champion"), Mapping)
            and isinstance(
                (self.state.get("incumbent_convergence") or {}).get(
                    "clean_restore_receipt"
                ),
                Mapping,
            )
            and (self.state.get("incumbent_convergence") or {})
            .get("clean_restore_receipt", {})
            .get("passed")
            is True
        )
        recover_incumbent_terminal = bool(
            self.state.get("phase")
            in {
                "incumbent_convergence_pending_clean_restore",
                "clean_restore_technical_failure",
            }
            and self.state.get("completion_contract")
            == INCUMBENT_CONVERGENCE_CONTRACT
            and isinstance(self.state.get("incumbent_convergence"), Mapping)
            and isinstance(self.state.get("final_champion"), Mapping)
        )
        if recover_incumbent_terminal:
            restore_receipt = self.restore_clean_baseline(
                reason="formal_incumbent_convergence_recovery"
            )
            self.state["incumbent_convergence"]["clean_restore_receipt"] = (
                copy.deepcopy(restore_receipt)
            )
            self.state["phase"] = "converged_on_incumbent_clean"
            self.save_state()
            self.log(
                "algorithm_c_incumbent_convergence_restore_recovered",
                accepted=int(self.state["accepted"]),
                accepted_unchanged=True,
                incumbent_unchanged=True,
                ranges_unchanged=True,
                gradient_state_unchanged=True,
                clean_restore_receipt=copy.deepcopy(restore_receipt),
                physical_workload_invocations=0,
            )
            final_normalized_width = float(
                self.state.get("normalized_width")
                or 0.5 ** int(self.state["accepted"])
            )
            self.write_authority_manifest(final_normalized_width)
            return
        if (
            completed_without_stability
            or completed_with_legacy_stability
            or completed_on_incumbent
        ):
            final_normalized_width = float(
                self.state.get("normalized_width")
                or 0.5 ** int(self.state["accepted"])
            )
            self.log(
                "algorithm_c_completed_authority_reused",
                accepted=int(self.state["accepted"]),
                accepted_unchanged=True,
                incumbent_unchanged=True,
                ranges_unchanged=True,
                gradient_state_unchanged=True,
                physical_workload_invocations=0,
            )
            self.write_authority_manifest(final_normalized_width)
            return
        self.baseline()
        if self.stop_requested:
            self.finish_signal_stop("signal_stop_after_baseline_boundary")
            return
        # Import on every process start. The durable identity memory prevents
        # duplicate physical accounting, while the in-memory evidence cache
        # must be rebuilt after a controller restart.
        self.import_historical_valid_replay_seed()
        self.verify_baseline_execution_domain()
        self.restore_clean_baseline(reason="post_startup_baseline")
        if not self.state.get("formal_started"):
            self.state["formal_started"] = True
            self.state["formal_started_at_epoch_s"] = time.time()
            self.state["phase"] = "formal_search"
            self.save_state()
        while (
            int(self.state["accepted"]) < self.accepted_target
            and int(self.state["round_index"])
            < self.effective_outer_round_limit()
            and not self.stop_requested
        ):
            if resume_round_index is not None:
                round_index = int(resume_round_index)
                resume_round_index = None
                self.log(
                    "algorithm_c_unfinished_round_resumed",
                    startup_phase=startup_phase,
                    round_index=round_index,
                    accepted=int(self.state["accepted"]),
                    accepted_unchanged=True,
                    incumbent_unchanged=True,
                    ranges_unchanged=True,
                    gradient_state_unchanged=True,
                    valid_replays=int(self.state["valid_replays"]),
                    physical_workload_invocations=int(
                        self.state["physical_workload_invocations"]
                    ),
                    technical_failures=int(self.state["technical_failures"]),
                    technical_failure_does_not_advance_round=True,
                )
            else:
                round_index = int(self.state["round_index"]) + 1
            self.state["round_index"] = round_index
            coverage_resume = (
                self.resume_current_interval_coverage_after_local_budget(
                    round_index
                )
            )
            if coverage_resume == "terminal":
                return
            if coverage_resume == "stop":
                break
            if coverage_resume == "handled":
                continue
            self.state["phase"] = "offline_algorithm_c_ranking"
            self.save_state()
            try:
                rankings = self.generate_rankings(round_index)
            except IncompleteSubsetOptimizationError as incumbent_error:
                # Top-5 is the search target and physical ceiling.  Exhaust
                # larger eta values and retained-range boundaries before
                # accepting a genuine partial Top-K; never pad it with a
                # duplicate, baseline, random, or uncertain candidate.
                try:
                    rankings = self.expand_behavior_cluster_rankings(
                        round_index,
                        incumbent_error,
                    )
                except IncompleteSubsetOptimizationError as expansion_error:
                    partial_errors = list(
                        getattr(expansion_error, "partial_topk_errors", None)
                        or [incumbent_error, expansion_error]
                    )
                    if (
                        getattr(
                            expansion_error,
                            "plan_space_saturation_evidence",
                            False,
                        )
                        is not True
                        or getattr(
                            expansion_error,
                            "technical_candidate_generation_failure",
                            False,
                        )
                        is True
                    ):
                        # Missing eligible evidence means the failure was not
                        # merely plan-space saturation (for example malformed
                        # or incomplete EXPLAIN fingerprints).  Preserve the
                        # former fail-closed technical behavior.
                        raise
                    rankings = self.merge_exhausted_partial_topk(
                        partial_errors,
                        round_index=round_index,
                    )
            base_candidates, base_admission = self.select_outer_admitted_candidates(
                rankings,
                round_index=round_index,
                batch_kind="base_top5",
            )
            if (
                0 < len(base_candidates) < BASE_TOP_K
                and int(
                    base_admission.get(
                        "exact_valid_replay_excluded_count"
                    )
                    or 0
                )
                > 0
            ):
                base_candidates, _identity_backfill = (
                    self.backfill_replayed_identity_top5(
                        rankings,
                        base_candidates,
                        base_admission,
                        round_index=round_index,
                    )
                )
            elif (
                len(base_candidates) == 0
                and int(
                    base_admission.get(
                        "exact_valid_replay_excluded_count"
                    )
                    or 0
                )
                > 0
            ):
                self.log(
                    "algorithm_c_exact_top5_cached_redecision_selected",
                    round_index=round_index,
                    reason="all_current_top5_have_valid_physical_evidence",
                    identity_backfill_skipped=True,
                    physical_workload_invocations=0,
                    valid_replays_added=0,
                    qps_outer_only=True,
                    inner_candidate_scores_modified=False,
                    accepted_unchanged=True,
                    incumbent_unchanged=True,
                    ranges_unchanged=True,
                    gradient_state_unchanged=True,
                )
            # Outer hard-pruning can also reduce an otherwise non-empty plan
            # set to zero.  That is a reused-evidence rejection, not a planner
            # plateau.  Only exhaustive offline search with no eligible new
            # full plan earns the plateau label.
            plan_plateau_this_round = len(rankings) == 0
            self.log(
                "algorithm_c_dynamic_topk_admission",
                round_index=round_index,
                top_k_limit=BASE_TOP_K,
                admitted_candidate_count=len(base_candidates),
                partial_topk=0 < len(base_candidates) < BASE_TOP_K,
                plan_plateau=plan_plateau_this_round,
                top_k_is_upper_bound=True,
                duplicate_plan_padding_allowed=False,
                baseline_padding_allowed=False,
                outer_replay_admission=base_admission,
                accepted_unchanged=True,
                incumbent_unchanged=True,
                ranges_unchanged=True,
                gradient_state_unchanged=True,
                physical_workload_invocations=0,
            )

            # Reuse the entire already-paid valid evidence pool before any
            # new workload invocation.  A lane that can accept a historical
            # point must still enter replay_batch with K=0 so it contributes
            # the three empty-wave cohort controls required by peer lanes
            # that may have real [2,2,1] work in this round.
            prephysical_historical_rows = (
                self.cached_historical_valid_replay_rows(
                    round_index=round_index,
                )
            )
            prephysical_historical_accepted: Optional[Dict[str, Any]] = None
            prephysical_historical_decision_count = 0
            if prephysical_historical_rows:
                (
                    prephysical_historical_accepted,
                    prephysical_historical_decision_count,
                ) = self.evaluate_batch(
                    prephysical_historical_rows,
                    round_index=round_index,
                    batch_kind=(
                        "prephysical_cached_historical_valid_replay_redecision"
                    ),
                    evidence_source="cached_valid_replay",
                )
            self.log(
                "algorithm_c_prephysical_historical_redecision_complete",
                contract=VALID_REPLAY_EVIDENCE_REDECISION_CONTRACT,
                round_index=round_index,
                required_acceptance_tier=self.required_acceptance_tier(),
                historical_decision_count=(
                    prephysical_historical_decision_count
                ),
                accepted=prephysical_historical_accepted is not None,
                candidate_physical_replay_avoided=(
                    len(base_candidates)
                    if prephysical_historical_accepted is not None
                    else 0
                ),
                empty_wave_controls_required=True,
                physical_workload_invocations=0,
                valid_replays_added=0,
                technical_failures_excluded=True,
                current_range_required=True,
                previously_accepted_parameter_vectors_excluded=True,
                qps_outer_only=True,
                inner_candidate_scores_modified=False,
            )
            physical_base_candidates = (
                []
                if prephysical_historical_accepted is not None
                else base_candidates
            )
            self.state["phase"] = "base_top5_real_replay"
            self.save_state()
            # Even K=0 must enter replay_batch: it emits the three zero-work
            # cohort controls needed by lanes that still have real candidates.
            # The controls consume no physical slot and mutate no optimizer
            # state, but keep the four-lane [2,2,1] wave protocol aligned.
            base_rows = self.replay_batch(
                physical_base_candidates,
                round_index=round_index,
                batch_kind="base_top5",
            )
            physical_rows_this_round = len(base_rows)
            hard_pruned_this_round = int(base_admission.get("hard_pruned_count") or 0)
            range_excluded_this_round = int(
                base_admission.get("range_excluded_count") or 0
            )
            if self.stop_requested:
                break
            if prephysical_historical_accepted is not None:
                self.accept(prephysical_historical_accepted, round_index)
                continue
            accepted, valid_count = self.evaluate_batch(
                base_rows,
                round_index=round_index,
                batch_kind="base_top5",
            )
            normal_top5_valid_count = valid_count
            if accepted is not None:
                self.accept(accepted, round_index)
                continue

            # Exact valid replay identities are a physical de-duplication
            # rule, not a permanent semantic rejection.  Re-evaluate up to
            # the current Top-5 cached rows under this round's tier after new
            # physical candidates miss.  These rows consume neither physical
            # nor valid-replay accounting and cannot affect inner ranking.
            cached_rows = self.cached_valid_replay_rows(
                rankings,
                round_index=round_index,
                exclude_rows=base_rows,
            )
            cached_accepted: Optional[Dict[str, Any]] = None
            cached_decision_count = 0
            if cached_rows:
                cached_accepted, cached_decision_count = self.evaluate_batch(
                    cached_rows,
                    round_index=round_index,
                    batch_kind="cached_valid_replay_redecision",
                    evidence_source="cached_valid_replay",
                )
                self.log(
                    "algorithm_c_cached_valid_replay_redecision_complete",
                    contract=VALID_REPLAY_EVIDENCE_REDECISION_CONTRACT,
                    round_index=round_index,
                    required_acceptance_tier=self.required_acceptance_tier(),
                    cached_decision_count=cached_decision_count,
                    accepted=cached_accepted is not None,
                    physical_workload_invocations=0,
                    valid_replays_added=0,
                    technical_failures_excluded=True,
                    qps_outer_only=True,
                    inner_candidate_scores_modified=False,
                )
            if cached_accepted is not None:
                self.accept(cached_accepted, round_index)
                continue

            decision_evidence_count = (
                valid_count
                + cached_decision_count
                + prephysical_historical_decision_count
            )

            required_tier = self.required_acceptance_tier()
            global_coverage_attempted = False
            global_coverage_candidates: List[Dict[str, Any]] = []
            global_coverage_admission: Dict[str, Any] = {}
            # Technical rows consume physical slots but are excluded from
            # valid evidence.  Continue within the same bounded candidate
            # budget so one infeasible wave cannot terminate the lane.
            if base_rows and normal_top5_valid_count != len(base_rows):
                self.log(
                    "algorithm_c_technical_wave_excluded_continue",
                    round_index=round_index,
                    batch_kind="base_top5",
                    physical_slots_consumed=len(base_rows),
                    valid_replays_added=normal_top5_valid_count,
                    technical_failures_excluded=(
                        len(base_rows) - normal_top5_valid_count
                    ),
                    accepted_unchanged=True,
                    incumbent_unchanged=True,
                    ranges_unchanged=True,
                    gradient_state_unchanged=True,
                    continue_within_physical_budget=True,
                )

            # First exhaust the tail of the ordinary local candidate pool.
            # Each physical batch contains at most five unseen, distinct full
            # plans.  A real acceptable point ends the outer round at once.
            if base_rows:
                local_replayed_rows = list(base_rows)
                local_extension_batch = 0
                local_replay_count = len(base_rows)
                local_plan_pool_exhausted = False
                local_extension_accepted: Optional[Dict[str, Any]] = None
                while local_replay_count < LOCAL_PLAN_CANDIDATE_REAL_REPLAY_LIMIT:
                    raw_expansion = self.fallback_expansion_candidates(
                        rankings,
                        local_replayed_rows,
                    )
                    expansion, expansion_admission = (
                        self.select_outer_admitted_candidates(
                            raw_expansion,
                            round_index=round_index,
                            batch_kind="local_plan_tail_expansion",
                            limit=min(
                                BASE_TOP_K,
                                LOCAL_PLAN_CANDIDATE_REAL_REPLAY_LIMIT
                                - local_replay_count,
                            ),
                        )
                    )
                    hard_pruned_this_round += int(
                        expansion_admission.get("hard_pruned_count") or 0
                    )
                    range_excluded_this_round += int(
                        expansion_admission.get("range_excluded_count") or 0
                    )
                    self.log(
                        "algorithm_c_local_plan_tail_expansion",
                        round_index=round_index,
                        extension_batch_index=local_extension_batch + 1,
                        trigger="ordinary_top5_valid_but_no_outer_accept",
                        plan_tree_change_required=True,
                        unique_plan_fingerprint_required=True,
                        current_formal_interval_only=True,
                        raw_candidate_count=len(raw_expansion),
                        selected_candidate_count=len(expansion),
                        physical_batch_limit=BASE_TOP_K,
                        physical_total_limit=(
                            LOCAL_PLAN_CANDIDATE_REAL_REPLAY_LIMIT
                        ),
                        physical_replay_count_before_batch=local_replay_count,
                        candidate_score_ranking_used=True,
                        local_candidate_pool_exhausted=not expansion,
                        outer_replay_admission=expansion_admission,
                    )
                    if not expansion:
                        local_plan_pool_exhausted = True
                        break
                    local_extension_batch += 1
                    self.state["phase"] = "local_plan_tail_real_replay"
                    self.save_state()
                    extra_rows = self.replay_batch(
                        expansion,
                        round_index=round_index,
                        batch_kind=(
                            f"local_plan_tail_{local_extension_batch:03d}"
                        ),
                    )
                    physical_rows_this_round += len(extra_rows)
                    local_replay_count += len(extra_rows)
                    if self.stop_requested:
                        break
                    local_extension_accepted, extra_valid = self.evaluate_batch(
                        extra_rows,
                        round_index=round_index,
                        batch_kind=(
                            f"local_plan_tail_{local_extension_batch:03d}"
                        ),
                    )
                    local_replayed_rows.extend(extra_rows)
                    if extra_valid != len(extra_rows):
                        self.log(
                            "algorithm_c_technical_wave_excluded_continue",
                            round_index=round_index,
                            batch_kind=(
                                f"local_plan_tail_{local_extension_batch:03d}"
                            ),
                            physical_slots_consumed=len(extra_rows),
                            valid_replays_added=extra_valid,
                            technical_failures_excluded=(
                                len(extra_rows) - extra_valid
                            ),
                            accepted_unchanged=True,
                            incumbent_unchanged=True,
                            ranges_unchanged=True,
                            gradient_state_unchanged=True,
                            continue_within_physical_budget=True,
                        )
                    valid_count += extra_valid
                    decision_evidence_count += extra_valid
                    if local_extension_accepted is not None:
                        break
                if self.stop_requested:
                    break
                if local_extension_accepted is not None:
                    self.accept(local_extension_accepted, round_index)
                    continue

                local_replay_budget_exhausted = bool(
                    local_replay_count
                    >= LOCAL_PLAN_CANDIDATE_REAL_REPLAY_LIMIT
                    and not local_plan_pool_exhausted
                )
                self.log(
                    "algorithm_c_local_candidate_budget_complete",
                    round_index=round_index,
                    candidate_score_ranking_used=True,
                    top5_batches=(1 + local_extension_batch),
                    physical_replay_count=local_replay_count,
                    physical_replay_limit=(
                        LOCAL_PLAN_CANDIDATE_REAL_REPLAY_LIMIT
                    ),
                    local_plan_pool_exhausted=local_plan_pool_exhausted,
                    local_replay_budget_exhausted=(
                        local_replay_budget_exhausted
                    ),
                    accepted=False,
                    incumbent_unchanged=True,
                    ranges_unchanged=True,
                    gradient_state_unchanged=True,
                )

                # Only after the scored local Top-10 budget is completed (or
                # the local plan pool ends earlier) may the deterministic
                # 256-point design probe the current interval.
                global_coverage_attempted = True
                global_rankings = self.generate_global_plan_coverage_rankings(
                    round_index
                )
                coverage_replayed_rows = list(local_replayed_rows)
                coverage_batch = 0
                coverage_replay_count = 0
                coverage_plan_pool_exhausted = False
                coverage_accepted: Optional[Dict[str, Any]] = None
                while coverage_replay_count < GLOBAL_PLAN_COVERAGE_REAL_REPLAY_LIMIT:
                    raw_global_coverage = self.fallback_expansion_candidates(
                        global_rankings,
                        coverage_replayed_rows,
                    )
                    (
                        global_coverage_candidates,
                        global_coverage_admission,
                    ) = self.select_outer_admitted_candidates(
                        raw_global_coverage,
                        round_index=round_index,
                        batch_kind="current_interval_plan_coverage_expansion",
                        limit=min(
                            BASE_TOP_K,
                            GLOBAL_PLAN_COVERAGE_REAL_REPLAY_LIMIT
                            - coverage_replay_count,
                        ),
                    )
                    self.log(
                        "algorithm_c_current_interval_coverage_after_local_exhaustion",
                        contract=GLOBAL_PLAN_COVERAGE_CONTRACT,
                        round_index=round_index,
                        coverage_batch_index=coverage_batch + 1,
                        trigger=(
                            "ordinary_local_scored_top10_completed_without_"
                            "accept"
                        ),
                        normal_top5_valid_replay_count=normal_top5_valid_count,
                        local_extension_batch_count=local_extension_batch,
                        local_physical_replay_count=local_replay_count,
                        local_physical_replay_limit=(
                            LOCAL_PLAN_CANDIDATE_REAL_REPLAY_LIMIT
                        ),
                        local_plan_pool_exhausted=local_plan_pool_exhausted,
                        local_replay_budget_exhausted=(
                            local_replay_budget_exhausted
                        ),
                        plan_tree_change_required=True,
                        unique_plan_fingerprint_required=True,
                        current_formal_interval_only=True,
                        default_full_domain_reopened=False,
                        raw_candidate_count=len(raw_global_coverage),
                        selected_candidate_count=len(global_coverage_candidates),
                        coverage_plan_pool_exhausted=(
                            not global_coverage_candidates
                        ),
                        physical_batch_limit=BASE_TOP_K,
                        physical_total_limit=(
                            GLOBAL_PLAN_COVERAGE_REAL_REPLAY_LIMIT
                        ),
                        physical_replay_count_before_batch=(
                            coverage_replay_count
                        ),
                        external_algorithm_evidence_used=False,
                        qps_used_for_inner_scoring=False,
                        outer_replay_admission=global_coverage_admission,
                    )
                    if not global_coverage_candidates:
                        coverage_plan_pool_exhausted = True
                        break
                    coverage_batch += 1
                    self.state["phase"] = (
                        "current_interval_plan_coverage_expansion_real_replay"
                    )
                    self.save_state()
                    global_rows = self.replay_batch(
                        global_coverage_candidates,
                        round_index=round_index,
                        batch_kind=(
                            f"current_interval_plan_coverage_{coverage_batch:03d}"
                        ),
                    )
                    physical_rows_this_round += len(global_rows)
                    coverage_replay_count += len(global_rows)
                    if self.stop_requested:
                        break
                    coverage_accepted, global_valid = self.evaluate_batch(
                        global_rows,
                        round_index=round_index,
                        batch_kind=(
                            f"current_interval_plan_coverage_{coverage_batch:03d}"
                        ),
                    )
                    decision_evidence_count += global_valid
                    coverage_replayed_rows.extend(global_rows)
                    if global_valid != len(global_rows):
                        self.log(
                            "algorithm_c_technical_wave_excluded_continue",
                            round_index=round_index,
                            batch_kind=(
                                "current_interval_plan_coverage_"
                                f"{coverage_batch:03d}"
                            ),
                            physical_slots_consumed=len(global_rows),
                            valid_replays_added=global_valid,
                            technical_failures_excluded=(
                                len(global_rows) - global_valid
                            ),
                            accepted_unchanged=True,
                            incumbent_unchanged=True,
                            ranges_unchanged=True,
                            gradient_state_unchanged=True,
                            continue_within_physical_budget=True,
                        )
                    if coverage_accepted is not None:
                        break
                if self.stop_requested:
                    break
                if coverage_accepted is not None:
                    self.accept(coverage_accepted, round_index)
                    continue
                coverage_budget_exhausted = bool(
                    coverage_replay_count
                    >= GLOBAL_PLAN_COVERAGE_REAL_REPLAY_LIMIT
                    and not coverage_plan_pool_exhausted
                )
                self.log(
                    "algorithm_c_current_interval_coverage_budget_complete",
                    contract=GLOBAL_PLAN_COVERAGE_CONTRACT,
                    round_index=round_index,
                    candidate_score_ranking_used=True,
                    top5_batches=coverage_batch,
                    physical_replay_count=coverage_replay_count,
                    physical_replay_limit=(
                        GLOBAL_PLAN_COVERAGE_REAL_REPLAY_LIMIT
                    ),
                    coverage_plan_pool_exhausted=coverage_plan_pool_exhausted,
                    coverage_replay_budget_exhausted=coverage_budget_exhausted,
                    accepted=False,
                    incumbent_unchanged=True,
                    ranges_unchanged=True,
                    gradient_state_unchanged=True,
                )
                if self.finish_no_improvement_round(
                    round_index=round_index,
                    reason=(
                        "ordinary_local_top10_and_scored_current_interval_"
                        "top10_have_no_higher_qps"
                    ),
                    exhaustive_current_interval=coverage_plan_pool_exhausted,
                ):
                    return
                continue
            # A non-empty cached evidence pool is not proof that the search
            # can still make QPS progress.  With last-accepted semantics it
            # may consist entirely of previously measured points whose QPS
            # is below the current incumbent.  The former condition entered
            # the full-range basin escape only when there was *no* decision
            # evidence, so these lower-QPS rows caused identical cached
            # re-decisions until max_rounds.  When the current interval has
            # no new physical point at fallback, also search the original
            # formal bounds for an unseen full-plan fingerprint.  This is an
            # exploration envelope only: rejected rounds still leave the
            # formal range, incumbent, and gradient state unchanged.  Every
            # rescue remains inside the current formal interval.
            cached_evidence_without_new_physical_candidate = (
                should_escape_cached_qps_stagnation(
                    required_tier=required_tier,
                    decision_evidence_count=decision_evidence_count,
                    physical_rows_this_round=physical_rows_this_round,
                )
            )
            if (
                decision_evidence_count <= 0
                or cached_evidence_without_new_physical_candidate
            ):
                if (
                    decision_evidence_count <= 0
                    and plan_plateau_this_round
                    and physical_rows_this_round == 0
                ):
                    self.log(
                        "algorithm_c_local_plan_space_exhausted_before_global_coverage",
                        round_index=round_index,
                        local_plan_space_exhausted=True,
                        global_coverage_required_before_terminal=True,
                        accepted_unchanged=True,
                        incumbent_unchanged=True,
                        ranges_unchanged=True,
                        gradient_state_unchanged=True,
                        physical_workload_invocations=0,
                    )
                if physical_rows_this_round == 0 and (
                    hard_pruned_this_round > 0
                    or range_excluded_this_round > 0
                    or cached_evidence_without_new_physical_candidate
                    or plan_plateau_this_round
                ):
                    if required_tier == "fallback":
                        basin_candidates: List[Dict[str, Any]] = []
                        basin_admission: Dict[str, Any] = {}
                        if self.has_shrunk_parameter_ranges():
                            basin_partial_topk = False
                            basin_ranking_error = ""
                            try:
                                full_rankings = self.generate_rankings(
                                    round_index,
                                    parameter_ranges=self.ranges,
                                    ranking_scope=(
                                        "current_interval_plan_basin_escape"
                                    ),
                                )
                            except IncompleteSubsetOptimizationError as exc:
                                full_rankings = (
                                    saturated_plan_space_partial_rankings(exc)
                                )
                                basin_partial_topk = True
                                basin_ranking_error = str(exc)
                                self.log(
                                    "algorithm_c_current_interval_plan_basin_"
                                    "partial_topk",
                                    round_index=round_index,
                                    reason="distinct_plan_space_saturation",
                                    trusted_eligible_candidate_count=len(
                                        full_rankings
                                    ),
                                    top_k_is_upper_bound=True,
                                    candidate_replication_allowed=False,
                                    baseline_padding_allowed=False,
                                    uncertain_probe_padding_allowed=False,
                                    technical_candidate_generation_failure=False,
                                    current_formal_interval_only=True,
                                    ranking_error=basin_ranking_error,
                                    physical_workload_invocations=0,
                                    accepted_unchanged=True,
                                    incumbent_unchanged=True,
                                    ranges_unchanged=True,
                                    gradient_state_unchanged=True,
                                )
                            raw_basin = self.fallback_expansion_candidates(
                                full_rankings,
                                [],
                            )
                            basin_candidates, basin_admission = (
                                self.select_outer_admitted_candidates(
                                    raw_basin,
                                    round_index=round_index,
                                    batch_kind=(
                                        "current_interval_plan_basin_escape"
                                    ),
                                )
                            )
                            hard_pruned_this_round += int(
                                basin_admission.get("hard_pruned_count") or 0
                            )
                            range_excluded_this_round += int(
                                basin_admission.get("range_excluded_count") or 0
                            )
                            self.log(
                                "algorithm_c_current_interval_plan_basin_escape",
                                round_index=round_index,
                                trigger=(
                                    "current_interval_plan_clusters_"
                                    "exhausted_at_fallback"
                                ),
                                current_formal_interval_only=True,
                                default_full_domain_reopened=False,
                                plan_tree_change_required=True,
                                incumbent_plan_fingerprint_excluded=True,
                                unique_plan_fingerprint_required=True,
                                raw_candidate_count=len(raw_basin),
                                selected_candidate_count=len(basin_candidates),
                                partial_topk_due_plan_space_saturation=(
                                    basin_partial_topk
                                ),
                                ranking_error=basin_ranking_error,
                                physical_batch_limit=5,
                                cached_decision_evidence_count=(
                                    decision_evidence_count
                                ),
                                cached_evidence_without_new_physical_candidate=(
                                    cached_evidence_without_new_physical_candidate
                                ),
                                formal_incumbent_unchanged=True,
                                formal_ranges_unchanged=True,
                                gradient_state_unchanged=True,
                                outer_replay_admission=basin_admission,
                            )
                        if basin_candidates:
                            self.state["phase"] = (
                                "current_interval_plan_basin_escape_real_replay"
                            )
                            self.save_state()
                            basin_rows = self.replay_batch(
                                basin_candidates,
                                round_index=round_index,
                                batch_kind="current_interval_plan_basin_escape",
                            )
                            physical_rows_this_round += len(basin_rows)
                            if self.stop_requested:
                                break
                            accepted, basin_valid = self.evaluate_batch(
                                basin_rows,
                                round_index=round_index,
                                batch_kind="current_interval_plan_basin_escape",
                            )
                            valid_count += basin_valid
                            decision_evidence_count += basin_valid
                            if accepted is not None:
                                self.accept(accepted, round_index)
                                continue
                            if basin_valid > 0:
                                self.log(
                                (
                                    "algorithm_c_round_rejected_after_"
                                    "current_interval_basin_escape"
                                ),
                                    round_index=round_index,
                                    required_acceptance_tier=required_tier,
                                    physical_valid_replay_count=valid_count,
                                    cached_valid_replay_redecision_count=(
                                        cached_decision_count
                                    ),
                                    decision_evidence_count=(
                                        decision_evidence_count
                                    ),
                                    accepted=int(self.state["accepted"]),
                                    incumbent_unchanged=True,
                                    ranges_unchanged=True,
                                    gradient_state_unchanged=True,
                                )
                                if self.finish_no_improvement_round(
                                    round_index=round_index,
                                    reason=(
                                        "current_interval_basin_top5_has_no_"
                                        "higher_qps"
                                    ),
                                    exhaustive_current_interval=False,
                                ):
                                    return
                                continue
                        # Local gradients and their interval projections can
                        # cover only a few trajectories.  Before declaring the
                        # incumbent stable, run an independent deterministic
                        # current-interval EXPLAIN/prediction pool.  This pool uses
                        # no QPS and no evidence from another algorithm; only
                        # its plan-diverse Top-5 reaches physical replay.
                        global_coverage_attempted = True
                        global_rankings = (
                            self.generate_global_plan_coverage_rankings(
                                round_index
                            )
                        )
                        raw_global_coverage = self.fallback_expansion_candidates(
                            global_rankings,
                            [],
                        )
                        (
                            global_coverage_candidates,
                            global_coverage_admission,
                        ) = self.select_outer_admitted_candidates(
                            raw_global_coverage,
                            round_index=round_index,
                            batch_kind="global_plan_coverage_rescue",
                        )
                        hard_pruned_this_round += int(
                            global_coverage_admission.get("hard_pruned_count") or 0
                        )
                        range_excluded_this_round += int(
                            global_coverage_admission.get("range_excluded_count") or 0
                        )
                        self.log(
                            "algorithm_c_global_plan_coverage_admission",
                            contract=GLOBAL_PLAN_COVERAGE_CONTRACT,
                            round_index=round_index,
                            raw_candidate_count=len(raw_global_coverage),
                            selected_candidate_count=len(
                                global_coverage_candidates
                            ),
                            physical_batch_limit=BASE_TOP_K,
                            external_algorithm_evidence_used=False,
                            qps_used_for_inner_scoring=False,
                            formal_incumbent_unchanged=True,
                            formal_ranges_unchanged=True,
                            gradient_state_unchanged=True,
                            outer_replay_admission=global_coverage_admission,
                        )
                        if global_coverage_candidates:
                            self.state["phase"] = (
                                "global_plan_coverage_rescue_real_replay"
                            )
                            self.save_state()
                            global_rows = self.replay_batch(
                                global_coverage_candidates,
                                round_index=round_index,
                                batch_kind="global_plan_coverage_rescue",
                            )
                            physical_rows_this_round += len(global_rows)
                            if self.stop_requested:
                                break
                            accepted, global_valid = self.evaluate_batch(
                                global_rows,
                                round_index=round_index,
                                batch_kind="global_plan_coverage_rescue",
                            )
                            valid_count += global_valid
                            decision_evidence_count += global_valid
                            if accepted is not None:
                                self.accept(accepted, round_index)
                                continue
                            if global_valid > 0:
                                self.log(
                                    "algorithm_c_round_rejected_after_global_coverage",
                                    round_index=round_index,
                                    physical_valid_replay_count=valid_count,
                                    accepted=int(self.state["accepted"]),
                                    incumbent_unchanged=True,
                                    ranges_unchanged=True,
                                    gradient_state_unchanged=True,
                                    external_algorithm_evidence_used=False,
                                )
                                if self.finish_no_improvement_round(
                                    round_index=round_index,
                                    reason=(
                                        "current_interval_coverage_top5_has_"
                                        "no_higher_qps"
                                    ),
                                    exhaustive_current_interval=True,
                                ):
                                    return
                                continue
                        if physical_rows_this_round == 0:
                            exhaustion_reason = (
                                "cached_valid_replay_has_no_qps_progress_and_"
                                "current_interval_has_no_new_plan"
                                if cached_evidence_without_new_physical_candidate
                                else "outer_pruning_removed_all_candidates"
                            )
                            self.state["phase"] = (
                                "qps_progress_candidate_space_exhausted_clean"
                                if cached_evidence_without_new_physical_candidate
                                else "outer_pruning_candidate_space_exhausted_clean"
                            )
                            if cached_evidence_without_new_physical_candidate:
                                self.state["qps_progress_exhaustion"] = {
                                    "contract": (
                                        "cached_valid_replay_qps_stagnation_"
                                        "current_interval_plan_escape_v2"
                                    ),
                                    "reason": exhaustion_reason,
                                    "round_index": int(round_index),
                                    "accepted": int(self.state["accepted"]),
                                    "accepted_target": self.accepted_target,
                                    "decision_evidence_count": int(
                                        decision_evidence_count
                                    ),
                                    "current_interval_plan_escape_attempted": (
                                        self.has_shrunk_parameter_ranges()
                                    ),
                                    "current_interval_new_plan_count": len(
                                        basin_candidates
                                    ),
                                    "global_coverage_attempted": (
                                        global_coverage_attempted
                                    ),
                                    "global_coverage_new_plan_count": len(
                                        global_coverage_candidates
                                    ),
                                    "external_algorithm_evidence_used": False,
                                    "technical_failure": False,
                                    "accepted_unchanged": True,
                                    "incumbent_unchanged": True,
                                    "ranges_unchanged": True,
                                    "gradient_state_unchanged": True,
                                }
                            self.save_state()
                            self.log(
                                (
                                    "algorithm_c_outer_pruning_"
                                    "candidate_space_exhausted"
                                ),
                                round_index=round_index,
                                exhaustion_reason=exhaustion_reason,
                                required_acceptance_tier=required_tier,
                                decision_evidence_count=decision_evidence_count,
                                cached_evidence_without_new_physical_candidate=(
                                    cached_evidence_without_new_physical_candidate
                                ),
                                hard_pruned_count=hard_pruned_this_round,
                                range_excluded_count=range_excluded_this_round,
                                current_interval_basin_escape_attempted=(
                                    self.has_shrunk_parameter_ranges()
                                ),
                                current_interval_basin_escape_selected=len(
                                    basin_candidates
                                ),
                                accepted=int(self.state["accepted"]),
                                accepted_unchanged=True,
                                incumbent_unchanged=True,
                                ranges_unchanged=True,
                                gradient_state_unchanged=True,
                                technical_failure=False,
                            )
                            if self.finish_no_improvement_round(
                                round_index=round_index,
                                reason=exhaustion_reason,
                                exhaustive_current_interval=True,
                            ):
                                return
                            continue
                    self.log(
                        "algorithm_c_round_rejected_by_reused_outer_evidence",
                        round_index=round_index,
                        required_acceptance_tier=required_tier,
                        hard_pruned_count=hard_pruned_this_round,
                        range_excluded_count=range_excluded_this_round,
                        next_required_acceptance_tier=self.required_acceptance_tier(),
                        accepted=int(self.state["accepted"]),
                        incumbent_unchanged=True,
                        ranges_unchanged=True,
                        gradient_state_unchanged=True,
                        physical_workload_invocations=0,
                    )
                    if self.finish_no_improvement_round(
                        round_index=round_index,
                        reason="reused_current_interval_evidence_has_no_higher_qps",
                        exhaustive_current_interval=True,
                    ):
                        return
                    continue
                self.state["phase"] = "technical_failure_stopped_clean"
                self.save_state()
                raise RuntimeError("round has no valid replay; state left unchanged")
            self.state["consecutive_plan_plateau_rounds"] = 0
            self.log(
                "algorithm_c_round_rejected",
                round_index=round_index,
                accepted=int(self.state["accepted"]),
                physical_valid_replay_count=valid_count,
                cached_valid_replay_redecision_count=cached_decision_count,
                decision_evidence_count=decision_evidence_count,
                consecutive_missed_outer_rounds=int(
                    self.state["consecutive_missed_outer_rounds"]
                ) + 1,
                incumbent_unchanged=True,
                ranges_unchanged=True,
                gradient_state_unchanged=True,
                hard_pruned_candidate_count=hard_pruned_this_round,
                range_excluded_candidate_count=range_excluded_this_round,
            )
            if self.finish_no_improvement_round(
                round_index=round_index,
                reason="valid_outer_candidates_have_no_higher_qps",
                exhaustive_current_interval=global_coverage_attempted,
            ):
                return
        if self.stop_requested:
            self.finish_signal_stop("signal_stop")
            return
        if int(self.state["accepted"]) < self.accepted_target:
            self.state["phase"] = "nonconvergence_max_rounds_clean"
            self.state["nonconvergence"] = {
                "reason": (
                    "outer_round_limit_exhausted_before_two_consecutive_"
                    "no_improvement_rounds"
                ),
                "round_index": int(self.state["round_index"]),
                "max_rounds": self.max_rounds,
                "outer_round_limit": self.outer_round_limit,
                "effective_outer_round_limit": (
                    self.effective_outer_round_limit()
                ),
                "accepted": int(self.state["accepted"]),
                "accepted_improvements": int(self.state["accepted"]),
                "accepted_target": self.accepted_target,
                "consecutive_no_improvement_rounds": int(
                    self.state.get("consecutive_no_improvement_rounds") or 0
                ),
                "incumbent_baseline_not_selected_without_two_misses": True,
                "incumbent_unchanged_by_stop": True,
                "ranges_unchanged_by_stop": True,
                "gradient_state_unchanged_by_stop": True,
            }
            self.save_state()
            self.restore_clean_baseline(
                reason="formal_outer_round_limit_nonconvergence"
            )
            self.log(
                "algorithm_c_nonconvergence_clean_stop",
                **self.state["nonconvergence"],
            )
            return
        final_normalized_width = float(
            self.state.get("normalized_width")
            or 0.5
            ** (
                int(self.state["accepted"])
                + int(
                    self.state.get(
                        "incumbent_centered_stagnation_refinement_count"
                    )
                    or 0
                )
            )
        )
        if final_normalized_width > self.normalized_width_threshold + 1e-12:
            self.state["phase"] = "normalized_width_threshold_not_reached_clean"
            self.save_state()
            self.restore_clean_baseline(reason="normalized_width_threshold_not_reached")
            raise RuntimeError(
                "accepted target reached without satisfying normalized width threshold"
            )
        self.finalize_without_post_convergence_stability(final_normalized_width)
        return

        # Legacy incumbent-only confirmation remains below for state-file
        # compatibility documentation. New formal campaigns return through
        # ``finalize_best_feasible_champion`` above.
        stability_rounds: List[Dict[str, Any]] = []
        consecutive_streak = 0
        for confirmation_attempt in range(
            1,
            self.stability_confirmation_max_attempts + 1,
        ):
            confirmation_candidates = [
                {
                    "rank": index + 1,
                    "optimized_params": copy.deepcopy(
                        self.state["incumbent_params"]
                    ),
                    "confirmation_only": True,
                    "stability_attempt": confirmation_attempt,
                    "three_level_fingerprint": {
                        "plan_level": (
                            f"stability_attempt_{confirmation_attempt:02d}_"
                            f"worker_{index + 1}"
                        )
                    },
                }
                for index in range(self.replay_parallelism)
            ]
            confirmations = self.replay_batch(
                confirmation_candidates,
                round_index=int(self.state["round_index"]),
                batch_kind=(
                    f"stability_confirmation_attempt_{confirmation_attempt:02d}"
                ),
            )
            if self.stop_requested:
                self.finish_signal_stop(
                    f"signal_stop_during_stability_attempt_{confirmation_attempt:02d}"
                )
                return
            attempt_technically_valid = bool(
                len(confirmations) == self.replay_parallelism and all(
                physical_valid(row) for row in confirmations
                )
            )
            if not attempt_technically_valid:
                stability_rounds.append(
                    {
                        "attempt": confirmation_attempt,
                        "passed": False,
                        "technical_failure": True,
                        "valid_confirmation_count": sum(
                            physical_valid(row) for row in confirmations
                        ),
                        "confirmations": copy.deepcopy(confirmations),
                    }
                )
                consecutive_streak = 0
                self.state["consecutive_stable_rounds"] = 0
                self.state["stability_rounds"] = copy.deepcopy(stability_rounds)
                self.state["phase"] = "accepted_target_pending_stability"
                self.save_state()
                self.log(
                    "algorithm_c_stability_attempt_failed_technically",
                    stability_attempt=confirmation_attempt,
                    valid_confirmation_count=sum(
                        physical_valid(row) for row in confirmations
                    ),
                    accepted=int(self.state["accepted"]),
                    semantic_state_mutation=False,
                    continue_bounded_attempts=True,
                )
                continue
            normalized = [
                normalize_to_worker_baseline(
                    row,
                    self.state["baseline_by_worker"][str(row["worker_key"])],
                    self.state["canonical_baseline"],
                    self.capacity,
                )
                for row in confirmations
            ]
            qps_values = [float(row["db_qps"]) for row in normalized]
            qps_mean = sum(qps_values) / len(qps_values)
            qps_relative_gap = abs(qps_values[0] - qps_values[1]) / max(
                qps_mean,
                1e-12,
            )
            resource_means = {
                name: sum(
                    float(row["resources"][name]) for row in normalized
                ) / len(normalized)
                for name in ("cpu", "memory", "io")
            }
            resource_relative_gaps = {
                name: abs(
                    float(normalized[0]["resources"][name])
                    - float(normalized[1]["resources"][name])
                )
                / max(resource_means[name], 1e-12)
                for name in ("cpu", "memory", "io")
            }
            dispersion_passed = bool(
                qps_relative_gap <= 0.15
                and all(
                    value <= 0.20
                    for value in resource_relative_gaps.values()
                )
            )
            round_passed = True
            stability_rounds.append(
                {
                    "attempt": confirmation_attempt,
                    "passed": round_passed,
                    "within_attempt_worker_dispersion_passed": dispersion_passed,
                    "within_attempt_worker_dispersion_is_diagnostic_only": True,
                    "technical_failure": False,
                    "qps_mean": qps_mean,
                    "qps_relative_gap_between_workers": qps_relative_gap,
                    "resource_means": resource_means,
                    "resource_relative_gaps_between_workers": (
                        resource_relative_gaps
                    ),
                    "confirmations": normalized,
                }
            )
            consecutive_streak = consecutive_streak + 1 if round_passed else 0
            self.state["consecutive_stable_rounds"] = consecutive_streak
            self.state["stability_rounds"] = copy.deepcopy(stability_rounds)
            self.state["phase"] = "accepted_target_pending_stability"
            self.save_state()
            self.log(
                "algorithm_c_stability_attempt_completed",
                stability_attempt=confirmation_attempt,
                passed=round_passed,
                consecutive_stable_rounds=consecutive_streak,
                required_consecutive_rounds=self.stability_confirmation_rounds,
                continue_to_fixed_cohort_attempt_count=True,
            )
        assessment = assess_stability_attempts(
            stability_rounds,
            required_consecutive_rounds=self.stability_confirmation_rounds,
        )
        stability_passed = assessment["passed"] is True
        self.state["consecutive_stable_rounds"] = (
            self.stability_confirmation_rounds if stability_passed else 0
        )
        self.state["repeatability"] = {
            "contract": STABILITY_CONFIRMATION_PROTOCOL,
            "passed": stability_passed,
            "required_consecutive_rounds": self.stability_confirmation_rounds,
            "maximum_attempts": self.stability_confirmation_max_attempts,
            "attempts_executed": len(stability_rounds),
            "selected_consecutive_attempts": assessment["selected_attempts"],
            "consecutive_qps_relative_gap": assessment[
                "consecutive_qps_relative_gap"
            ],
            "consecutive_resource_relative_gaps": assessment[
                "consecutive_resource_relative_gaps"
            ],
            "same_worker_consecutive_relative_gaps": assessment[
                "same_worker_consecutive_relative_gaps"
            ],
            "temporal_metric_diagnostics_passed": assessment[
                "temporal_metric_diagnostics_passed"
            ],
            "qps_temporal_gap_is_diagnostic_only": True,
            "required_resource_temporal_gaps_are_hard": True,
            "within_attempt_cross_worker_dispersion_is_diagnostic_only": True,
            "attempts": stability_rounds,
        }
        self.state["phase"] = (
            "complete" if stability_passed else "stability_confirmation_failed"
        )
        self.save_state()
        self.restore_clean_baseline(reason="formal_completion")
        if not stability_passed:
            raise RuntimeError(
                "two consecutive stability rounds failed within bounded attempts"
            )
        self.write_authority_manifest(final_normalized_width)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--target", required=True, choices=("cpu", "memory", "io", "none")
    )
    parser.add_argument(
        "--finalize-existing-stability",
        action="store_true",
        help="reassess a completed bounded stability batch without new workload",
    )
    args = parser.parse_args()
    controller = LaneController(args.config.resolve(), args.output_dir.resolve(), args.target)

    def request_stop(signum: int, _frame: Any) -> None:
        controller.stop_requested = True
        controller.log("algorithm_c_stop_requested", signal=signal.Signals(signum).name)

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, request_stop)
    if args.finalize_existing_stability:
        if not controller.finalize_existing_stability_evidence():
            raise RuntimeError("existing stability evidence does not pass")
    else:
        controller.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

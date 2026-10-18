#!/usr/bin/env python3
"""Generate XP round-10 subset/corner candidates from sealed local evidence.

This entry point is deliberately separate from the workload optimizer. It
imports only the frozen subset/gradient implementation and consumes a JSON
sealed model. It never opens sockets, starts subprocesses, invokes a shell, or
executes warmup/baseline/benchmark work.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from experience_table_subset_optimizer_current import optimize_all_experience_table_subsets
from gradient_descent_optimizer_current import ParameterRange


RESOURCES = ("cpu", "io", "memory")
OFFLINE_CONTRACT = "xp_round10_sealed_ratio_scale_offline_subset_generation_v1"


def _finite(value: Any, name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _load_object(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _first_candidate(sealed: Mapping[str, Any]) -> Mapping[str, Any]:
    candidates = sealed.get("top5") or sealed.get("candidate_rankings") or []
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], Mapping):
        raise ValueError("sealed round9 input must contain at least one candidate")
    return candidates[0]


def _common_sealed_value(sealed: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]], key: str) -> Dict[str, float]:
    direct = sealed.get(key)
    if isinstance(direct, Mapping):
        return {str(name): _finite(value, f"{key}.{name}") for name, value in direct.items()}
    values = [candidate.get(key) for candidate in candidates]
    if not values or not all(isinstance(value, Mapping) for value in values):
        raise ValueError(f"sealed input lacks {key}")
    first = {str(name): _finite(value, f"{key}.{name}") for name, value in values[0].items()}
    for other in values[1:]:
        normalized = {str(name): _finite(value, f"{key}.{name}") for name, value in other.items()}
        if normalized != first:
            raise ValueError(f"sealed {key} differs between round9 candidates")
    return first


class OfflineGenerationBlocked(RuntimeError):
    """The sealed evidence is insufficient for a trustworthy offline run."""


def _real_query_features(metadata: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = metadata.get("query_features")
    if not isinstance(raw, list) or not raw:
        raw = (metadata.get("current_features") or {}).get("query_features")
    if not isinstance(raw, list) or not raw:
        raise OfflineGenerationBlocked("blocked_missing_sealed_query_features")
    result = []
    for item in raw:
        if not isinstance(item, Mapping) or item.get("queryid") is None:
            raise OfflineGenerationBlocked("blocked_missing_sealed_query_features")
        fingerprint = str(item.get("plan_fingerprint") or "")
        if not re.fullmatch(r"exact-structure-sha256:[0-9a-f]{64}", fingerprint):
            raise OfflineGenerationBlocked("blocked_missing_sealed_query_features")
        result.append(copy.deepcopy(dict(item)))
    return result


def load_formal_estimator_factory(
    factory_path: Optional[Path],
    *,
    sealed: Mapping[str, Any],
    inputs: Mapping[str, Any],
) -> Any:
    """Load only an explicitly supplied formal, local estimator factory.

    The module contract is intentionally narrow. It must export
    ``RatioScaleResourceEstimator``, set ``COST_INDEPENDENT_FEATURE_EXTRACTOR``
    to true, and expose ``build_resource_estimation_factory``. The builder is
    given no sensitivity table, so T cannot leak into resource prediction.
    """
    if factory_path is None:
        raise OfflineGenerationBlocked("blocked_missing_sealed_estimator_factory")
    source = factory_path.read_text(encoding="utf-8", errors="replace")
    forbidden = ("subprocess", "socket", "requests", "urllib", "paramiko", "ssh", "scp", "sftp")
    if any(token in source.lower() for token in forbidden):
        raise OfflineGenerationBlocked("blocked_nonlocal_or_network_estimator_factory")
    spec = importlib.util.spec_from_file_location("xp_round10_formal_estimator_factory", str(factory_path))
    if spec is None or spec.loader is None:
        raise OfflineGenerationBlocked("blocked_missing_sealed_estimator_factory")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "RatioScaleResourceEstimator"):
        raise OfflineGenerationBlocked("blocked_missing_formal_ratio_scale_resource_estimator")
    if getattr(module, "COST_INDEPENDENT_FEATURE_EXTRACTOR", False) is not True:
        raise OfflineGenerationBlocked("blocked_missing_formal_cost_independent_feature_extractor")
    builder = getattr(module, "build_resource_estimation_factory", None)
    if not callable(builder):
        raise OfflineGenerationBlocked("blocked_missing_sealed_estimator_factory")
    query_features = _real_query_features(inputs["metadata"])
    factory = builder(
        baseline_params=copy.deepcopy(inputs["initial_params"]),
        baseline_resource=copy.deepcopy(inputs["initial_resource"]),
        baseline_features=copy.deepcopy(inputs["metadata"].get("baseline_features") or {}),
        query_features=query_features,
        sealed_estimator_model=copy.deepcopy(sealed.get("ratio_scale_estimator")),
        sql_set=copy.deepcopy(query_features),
    )
    if not callable(factory):
        raise OfflineGenerationBlocked("blocked_invalid_sealed_estimator_factory")
    return factory


def _sealed_inputs(sealed: Mapping[str, Any], config: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    candidates = [item for item in (sealed.get("top5") or sealed.get("candidate_rankings") or []) if isinstance(item, Mapping)]
    seed = _first_candidate(sealed)
    metadata = seed.get("final_estimation_metadata") or {}
    if not isinstance(metadata, Mapping):
        raise ValueError("seed candidate lacks final_estimation_metadata")
    source = dict(config or {})
    initial_params = sealed.get("initial_params") or seed.get("initial_params")
    initial_resource = sealed.get("initial_resource") or seed.get("initial_resource")
    if not isinstance(initial_params, Mapping) or not isinstance(initial_resource, Mapping):
        raise ValueError("sealed input must provide initial_params and initial_resource")
    capability = sealed.get("capability") or source.get("capability")
    sensitivity = sealed.get("sensitivity_table") or source.get("sensitivity_table") or seed.get("T")
    ranges = sealed.get("param_ranges") or source.get("param_ranges")
    if not isinstance(capability, Mapping) or not isinstance(sensitivity, Mapping) or not isinstance(ranges, Mapping):
        raise ValueError("sealed input/config must provide capability, sensitivity_table, and param_ranges")
    active = sealed.get("active_params") or source.get("active_params")
    if not active:
        active = [name for name in sensitivity if name in initial_params and float((ranges.get(name) or {}).get("min_value", (ranges.get(name) or {}).get("min", initial_params[name]))) != float((ranges.get(name) or {}).get("max_value", (ranges.get(name) or {}).get("max", initial_params[name])))]
    if not active:
        active = [name for name in initial_params if name in sensitivity]
    return {
        "initial_params": dict(initial_params),
        "initial_resource": dict(initial_resource),
        "capability": {str(k): _finite(v, f"capability.{k}") for k, v in capability.items()},
        "sensitivity_table": copy.deepcopy(dict(sensitivity)),
        "param_ranges": copy.deepcopy(dict(ranges)),
        "active_params": [str(name) for name in active],
        "metadata": metadata,
        "candidates": candidates,
    }


def generate_offline_candidates(
    *,
    sealed: Mapping[str, Any],
    config: Optional[Mapping[str, Any]],
    output_dir: Path,
    max_workers: int = 1,
    estimator_factory: Any = None,
    estimator_factory_path: Optional[Path] = None,
    require_min_novel_non_corner: bool = False,
) -> Dict[str, Any]:
    inputs = _sealed_inputs(sealed, config)
    query_features = _real_query_features(inputs["metadata"])
    ranges = {}
    for name, raw in inputs["param_ranges"].items():
        if not isinstance(raw, Mapping):
            raise ValueError(f"param_ranges.{name} must be an object")
        minimum = raw.get("min_value", raw.get("min"))
        maximum = raw.get("max_value", raw.get("max"))
        ranges[str(name)] = ParameterRange(_finite(minimum, f"param_ranges.{name}.min"), _finite(maximum, f"param_ranges.{name}.max"))
    if estimator_factory is None:
        estimator_factory = load_formal_estimator_factory(
            estimator_factory_path,
            sealed=sealed,
            inputs=inputs,
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = optimize_all_experience_table_subsets(
        resource_estimation_factory=estimator_factory,
        capability=copy.deepcopy(inputs["capability"]),
        p_real=copy.deepcopy(inputs["initial_params"]),
        sensitivity_table=copy.deepcopy(inputs["sensitivity_table"]),
        param_ranges=ranges,
        sql_set=copy.deepcopy(query_features),
        active_params=inputs["active_params"],
        max_workers=max_workers,
        round_index="round10-offline",
        log_dir=str(output_dir / "subset_logs"),
        optimizer_options={
            "full_subset_boundary_corner_scan_enabled": True,
            "directional_scoring_contract_required": True,
            "eta": 0.1,
            "max_iter": 4,
            "patience": 2,
            "gamma": 0.5,
            "min_param_delta_rel_pct": 0.0,
            "max_param_delta_rel_pct": 100.0,
            "adaptive_plan_change_expansion_enabled": True,
            "adaptive_plan_change_growth_factor": 2.0,
            "adaptive_plan_change_max_probes": 5,
            "adaptive_plan_change_max_relative_step_pct": 80.0,
            "adaptive_plan_change_reverse_fallback_enabled": True,
            "verbose": False,
            "target_bottleneck_resources": ["io"],
            "score_mode": "band_target",
            "resource_weights": {"cpu": 1.0, "io": 3.0, "memory": 1.0},
            "resource_target_ranges": {
                "cpu": {"min": 0.30, "max": 0.50},
                "io": {"min": 0.85, "max": 0.90},
                "memory": {"min": 0.30, "max": 0.50},
            },
        },
    )
    candidate_rankings = copy.deepcopy(summary.candidate_rankings)
    all_rankings = copy.deepcopy(summary.all_candidate_rankings)
    if require_min_novel_non_corner:
        non_corner_ids = {
            str((item.get("three_level_fingerprint") or {}).get("id"))
            for item in all_rankings
            if item.get("probe_kind") != "parameter_boundary_corner"
            and "boundary_corner" not in str(item.get("candidate_origin") or "")
            and (item.get("three_level_fingerprint") or {}).get("id")
        }
        if len(non_corner_ids) < 5:
            raise OfflineGenerationBlocked(
                "need at least 5 novel non-corner validated three-level fingerprints; "
                f"got {len(non_corner_ids)}"
            )
    for item in candidate_rankings + all_rankings:
        origin = str(item.get("candidate_origin") or "")
        is_corner = item.get("probe_kind") == "parameter_boundary_corner" or "boundary_corner" in origin
        is_probe = item.get("probe_kind") in {"uncertain_probe", "maximin_uncertain_probe"} or "uncertain_probe" in origin
        if is_corner or is_probe:
            item["used_for_gradient"] = False
            item["used_for_inner_incumbent_update"] = False
            item["incumbent_update_eligible"] = False
    result = {
        "contract": OFFLINE_CONTRACT,
        "offline_only": True,
        "workload_executed": False,
        "warmup_executed": False,
        "baseline_workload_executed": False,
        "network_used": False,
        "full_subset_boundary_corner_scan_enabled": True,
        "initial_params_digest": _digest(inputs["initial_params"]),
        "initial_resource_digest": _digest(inputs["initial_resource"]),
        "estimator_factory": str(estimator_factory_path) if estimator_factory_path else "injected_formal_factory",
        "active_params": inputs["active_params"],
        "subset_count": summary.subset_count,
        "successful_subset_count": summary.successful_subset_count,
        "unique_candidate_count": summary.unique_candidate_count,
        "candidate_rankings": candidate_rankings,
        "all_candidate_rankings": all_rankings,
        "audit_records": copy.deepcopy(summary.audit_records),
        "corner_probe_invariant": {
            "corners_used_for_gradient": False,
            "corners_used_for_inner_incumbent_update": False,
            "probes_used_for_gradient": False,
            "probes_used_for_inner_incumbent_update": False,
            "incumbent_mutated": False,
        },
        "adaptive_plan_change_expansion": {
            "enabled": True,
            "growth_factor": 2.0,
            "max_probes": 5,
            "max_relative_step_pct": 80.0,
            "reverse_fallback_enabled": True,
            "reverse_fallback_only_when_forward_has_no_plan_change": True,
            "direction_source": "original_dScore_dU_times_T_times_confidence",
            "score_source": "ratio_scale_predicted_resource",
            "workload_executed": False,
            "analyze_executed": False,
            "qps_used_for_inner_scoring": False,
            "incumbent_mutated": False,
        },
    }
    (output_dir / "offline_generation.json").write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return result


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sealed-round9", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--estimator-factory", type=Path)
    parser.add_argument("--max-workers", type=int, default=1)
    args = parser.parse_args()
    if args.max_workers <= 0:
        raise SystemExit("--max-workers must be positive")
    sealed = _load_object(args.sealed_round9)
    config = _load_object(args.config) if args.config else None
    try:
        result = generate_offline_candidates(
            sealed=sealed,
            config=config,
            output_dir=args.output_dir,
            max_workers=args.max_workers,
            estimator_factory_path=args.estimator_factory,
        )
    except OfflineGenerationBlocked as exc:
        print(json.dumps({"status": "blocked_missing_input", "input_gaps": [str(exc)]}, sort_keys=True))
        return 2
    print(json.dumps({"status": "ready", "candidate_count": len(result["all_candidate_rankings"]), "network_used": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

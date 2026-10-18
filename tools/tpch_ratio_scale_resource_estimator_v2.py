"""Cost-parameter-independent, baseline-anchored plan resource estimator."""

from __future__ import annotations

from collections import Counter
import copy
import hashlib
import json
import math
import re
import threading
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from plan_resource_estimator import PlanResourceEstimator


RESOURCE_NAMES = ("cpu", "memory", "io")


def _finite_positive(value: Any, *, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} must be positive and finite")
    return number


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _resource_ratio_bounds(
    value: Any,
    *,
    default: float,
    name: str,
) -> Dict[str, float]:
    if value is None:
        return {resource: default for resource in RESOURCE_NAMES}
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    unknown = sorted(set(value) - set(RESOURCE_NAMES))
    if unknown:
        raise ValueError(f"{name} contains unknown resources: {unknown}")
    return {
        resource: _finite_positive(
            value.get(resource, default),
            name=f"{name}.{resource}",
        )
        for resource in RESOURCE_NAMES
    }


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


class RatioScaleResourceEstimator:
    """Scale real baseline resources by cost-free structural plan features.

    The output unit for each resource is exactly the unit supplied to
    :meth:`calibrate`.  The PostgreSQL optimizer integration supplies CPU cores,
    GiB and MiB/s so the result can be compared directly with its capabilities.
    """

    def __init__(
        self,
        *,
        pg_host: str,
        pg_port: str,
        pg_user: str,
        pg_dbname: str,
        estimator_cfg: Optional[Dict[str, Any]] = None,
        plan_capture_modes: Optional[Sequence[str]] = None,
        custom_plan_params: Optional[Dict[str, List[Any]]] = None,
        psql_query_func: Optional[Callable[[str], str]] = None,
        cache_hit_rate: Optional[float] = None,
        verbose: bool = True,
        inner_estimator: Optional[PlanResourceEstimator] = None,
    ) -> None:
        self.estimator_cfg = copy.deepcopy(estimator_cfg or {})
        ratio_cfg = self.estimator_cfg.get("ratio_scale") or {}
        configured_cache = (
            cache_hit_rate
            if cache_hit_rate is not None
            else ratio_cfg.get("cache_hit_rate", 0.85)
        )
        self.cache_hit_rate = min(1.0, max(0.0, _safe_float(configured_cache, 0.85)))
        self.ratio_min = _finite_positive(
            ratio_cfg.get("ratio_min", 0.05), name="ratio_scale.ratio_min"
        )
        self.ratio_max = _finite_positive(
            ratio_cfg.get("ratio_max", 20.0), name="ratio_scale.ratio_max"
        )
        if self.ratio_max < self.ratio_min:
            raise ValueError("ratio_scale.ratio_max must be >= ratio_min")
        self.ratio_min_by_resource = _resource_ratio_bounds(
            ratio_cfg.get("ratio_min_by_resource"),
            default=self.ratio_min,
            name="ratio_scale.ratio_min_by_resource",
        )
        self.ratio_max_by_resource = _resource_ratio_bounds(
            ratio_cfg.get("ratio_max_by_resource"),
            default=self.ratio_max,
            name="ratio_scale.ratio_max_by_resource",
        )
        for resource in RESOURCE_NAMES:
            if (
                self.ratio_max_by_resource[resource]
                < self.ratio_min_by_resource[resource]
            ):
                raise ValueError(
                    "ratio_scale.ratio_max_by_resource."
                    f"{resource} must be >= ratio_min_by_resource.{resource}"
                )
        self.explain_workers = max(
            1,
            int(
                ratio_cfg.get(
                    "explain_workers",
                    (self.estimator_cfg.get("shared") or {}).get("explain_workers", 8),
                )
            ),
        )
        self.require_complete_explain = bool(
            ratio_cfg.get("require_complete_explain", True)
        )
        self.require_memory_floor = bool(
            ratio_cfg.get("require_memory_floor", False)
        )
        self.require_query_latency_for_memory = bool(
            ratio_cfg.get("require_query_latency_for_memory", False)
        )
        # Query latency is useful for occupancy weighting, but it is not a
        # resource objective. When it is unavailable, retain the validated
        # resource-only replay-weighted Memory path instead of blocking CPU/IO
        # candidate generation.
        self.allow_resource_only_memory_fallback = bool(
            ratio_cfg.get("allow_resource_only_memory_fallback", False)
        )
        self.memory_concurrency = _finite_positive(
            ratio_cfg.get("memory_concurrency", 1.0),
            name="ratio_scale.memory_concurrency",
        )
        self.configured_memory_fixed_baseline = ratio_cfg.get(
            "memory_fixed_baseline"
        )
        self.historical_error_bounds = copy.deepcopy(
            ratio_cfg.get("historical_error_bounds") or {}
        )
        self.default_work_mem_bytes = _finite_positive(
            ratio_cfg.get("work_mem_bytes", 4 * 1024 * 1024),
            name="ratio_scale.work_mem_bytes",
        )
        self.verbose = verbose
        self._inner = inner_estimator or PlanResourceEstimator(
            pg_host=pg_host,
            pg_port=pg_port,
            pg_user=pg_user,
            pg_dbname=pg_dbname,
            estimator_cfg=self.estimator_cfg,
            plan_capture_modes=plan_capture_modes,
            custom_plan_params=custom_plan_params,
            psql_query_func=psql_query_func,
        )
        self._state_lock = threading.RLock()
        self._baseline_features: Optional[Dict[str, Any]] = None
        self._baseline_real: Optional[Dict[str, float]] = None
        self._baseline_memory_fixed: Optional[float] = None
        self._baseline_memory_dynamic: Optional[float] = None
        self._baseline_params: Optional[Dict[str, float]] = None
        self._calibrated = False
        self.last_stats: Dict[str, Any] = {}
        self.last_metadata: Dict[str, Any] = {}

    @property
    def calibrated(self) -> bool:
        with self._state_lock:
            return self._calibrated

    def calibrate(
        self,
        sql_set: Any,
        baseline_params: Mapping[str, Any],
        runtime_resource_real: Mapping[str, Any],
    ) -> Dict[str, Any]:
        queries = self._normalize_queries(sql_set)
        baseline_real = {
            name: _finite_positive(
                runtime_resource_real.get(name), name=f"baseline_real.{name}"
            )
            for name in RESOURCE_NAMES
        }
        params = self._normalize_params(baseline_params)
        features = self._compute_structural_features(queries, params)
        memory_fixed = self._resolve_memory_fixed_baseline(
            runtime_resource_real,
            baseline_real["memory"],
        )
        memory_dynamic = baseline_real["memory"] - memory_fixed
        with self._state_lock:
            self._baseline_features = copy.deepcopy(features)
            self._baseline_real = baseline_real
            self._baseline_params = params
            self._baseline_memory_fixed = memory_fixed
            self._baseline_memory_dynamic = memory_dynamic
            self._calibrated = True
        return {
            "baseline_real": copy.deepcopy(baseline_real),
            "baseline_features": copy.deepcopy(features),
            "baseline_params": copy.deepcopy(params),
            "memory_ratio_scale_skipped_missing_latency": copy.deepcopy(
                features.get("memory_ratio_scale_skipped_missing_latency")
            ),
            "memory_baseline_breakdown": {
                "fixed": memory_fixed,
                "dynamic": memory_dynamic,
                "total": baseline_real["memory"],
            },
        }

    def __call__(self, sql_set: Any, params: Mapping[str, Any]) -> Dict[str, float]:
        resource, stats, metadata = self.estimate_with_metadata(sql_set, params)
        self.last_stats = stats
        self.last_metadata = metadata
        return resource

    def estimate_with_metadata(
        self,
        sql_set: Any,
        params: Mapping[str, Any],
    ) -> Tuple[Dict[str, float], Dict[str, Any], Dict[str, Any]]:
        with self._state_lock:
            if not self._calibrated or self._baseline_features is None or self._baseline_real is None:
                raise RuntimeError(
                    "RatioScaleResourceEstimator.calibrate() must succeed before estimation"
                )
            baseline_features = copy.deepcopy(self._baseline_features)
            baseline_real = copy.deepcopy(self._baseline_real)
            baseline_params = copy.deepcopy(self._baseline_params)
            baseline_memory_fixed = float(self._baseline_memory_fixed or 0.0)
            baseline_memory_dynamic = float(
                self._baseline_memory_dynamic
                if self._baseline_memory_dynamic is not None
                else baseline_real["memory"]
            )
        normalized_params = self._normalize_params(params)
        current_features = self._compute_structural_features(
            self._normalize_queries(sql_set), normalized_params
        )
        resource: Dict[str, float] = {}
        ratios: Dict[str, float] = {}
        unclipped_ratios: Dict[str, float] = {}
        structural_ratios_unclipped: Dict[str, float] = {}
        for name in RESOURCE_NAMES:
            key = f"{name}_total"
            baseline_value = _safe_float(baseline_features.get(key), 0.0)
            current_value = _safe_float(current_features.get(key), 0.0)
            structural_ratio = (
                current_value / baseline_value if baseline_value > 1e-12 else 1.0
            )
            if not math.isfinite(structural_ratio) or structural_ratio <= 0.0:
                structural_ratio = 1.0
            structural_ratios_unclipped[name] = structural_ratio
            raw_ratio = structural_ratio
            if name == "memory":
                raw_ratio = (
                    baseline_memory_fixed
                    + baseline_memory_dynamic * structural_ratio
                ) / baseline_real["memory"]
            ratio = min(
                self.ratio_max_by_resource[name],
                max(self.ratio_min_by_resource[name], raw_ratio),
            )
            unclipped_ratios[name] = raw_ratio
            ratios[name] = ratio
            resource[name] = baseline_real[name] * ratio
        resource_error_bounds = self._resolved_historical_error_bounds(
            baseline_real
        )
        stats: Dict[str, Any] = {
            name: {
                "min": value,
                "max": value,
                "avg": value,
                "p99": value,
            }
            for name, value in resource.items()
        }
        stats["memory_source"] = {"value": "ratio-scale-plan-estimator"}
        metadata = {
            "resource_estimation_source": "ratio-scale-plan-estimator",
            "resource_estimation_mode": "ratio_scale",
            "cost_parameter_free_features": True,
            "baseline_real": baseline_real,
            "baseline_features": baseline_features,
            "current_features": current_features,
            "structural_feature_ratios_unclipped": structural_ratios_unclipped,
            "feature_ratios_unclipped": unclipped_ratios,
            "feature_ratios": ratios,
            "resource_error_bounds": resource_error_bounds,
            "ratio_clip": [self.ratio_min, self.ratio_max],
            "ratio_clip_by_resource": {
                name: [
                    self.ratio_min_by_resource[name],
                    self.ratio_max_by_resource[name],
                ]
                for name in RESOURCE_NAMES
            },
            "baseline_params": baseline_params,
            "current_params": normalized_params,
            "workload_plan_fingerprint": current_features.get(
                "workload_plan_fingerprint"
            ),
            "baseline_plan_fingerprint": baseline_features.get(
                "workload_plan_fingerprint"
            ),
            "query_features": current_features.get("query_features"),
            "optimistic_interval_pruning_allowed": False,
            "qps_used_for_inner_scoring": False,
            "memory_prediction": {
                "fixed": baseline_memory_fixed,
                "baseline_dynamic": baseline_memory_dynamic,
                "structural_dynamic_ratio_unclipped": (
                    structural_ratios_unclipped["memory"]
                ),
                "effective_total_ratio": ratios["memory"],
                "predicted_dynamic": (
                    resource["memory"] - baseline_memory_fixed
                ),
                "predicted_total": resource["memory"],
            },
            "memory_ratio_scale_skipped_missing_latency": copy.deepcopy(
                current_features.get("memory_ratio_scale_skipped_missing_latency")
            ),
        }
        self.last_stats = copy.deepcopy(stats)
        self.last_metadata = copy.deepcopy(metadata)
        return resource, stats, metadata

    def _compute_structural_features(
        self,
        queries: Sequence[Dict[str, Any]],
        params: Mapping[str, float],
    ) -> Dict[str, Any]:
        explained = self._inner._collect_query_plans(
            queries,
            explain_workers=self.explain_workers,
            params=dict(params),
        )
        errors = [item for item in explained if item.get("error")]
        if errors and self.require_complete_explain:
            raise RuntimeError(
                "ratio-scale estimator requires complete EXPLAIN coverage: "
                f"failed={len(errors)}/{len(queries)}, errors={errors[:3]}"
            )
        usable = [item for item in explained if not item.get("error")]
        if not usable:
            raise RuntimeError("ratio-scale estimator received no usable EXPLAIN plans")
        load_relation_stats = getattr(
            self._inner,
            "_load_relation_stats",
            None,
        )
        relation_stats = (
            load_relation_stats()
            if callable(load_relation_stats)
            else None
        )
        work_mem_bytes = self._work_mem_bytes(params)
        aggregate: Counter[str] = Counter()
        memory_records: List[Tuple[Counter[str], float, bool, int]] = []
        query_features: List[Dict[str, Any]] = []
        fingerprints = []
        for item in usable:
            plan_root = item.get("plan_root")
            if relation_stats is None:
                nodes = self._inner._extract_plan_node_features(plan_root)
            else:
                nodes = self._inner._extract_plan_node_features(
                    plan_root,
                    relation_stats=relation_stats,
                )
            replay_count = max(
                1.0,
                _safe_float(item.get("replay_count", item.get("calls", 1)), 1.0),
            )
            cpu = self._cpu_features(nodes)
            io = self._io_features(nodes, work_mem_bytes)
            memory = self._memory_features(nodes)
            for feature_set in (cpu, io):
                for key, value in feature_set.items():
                    aggregate[key] += float(value) * replay_count
            memory_weight, has_latency = self._query_memory_occupancy_weight(
                item,
                replay_count,
            )
            memory_records.append(
                (memory, memory_weight, has_latency, len(query_features))
            )
            fingerprint = self._plan_fingerprint(plan_root)
            fingerprints.append(
                {"queryid": item.get("queryid"), "fingerprint": fingerprint}
            )
            query_features.append(
                {
                    "queryid": item.get("queryid"),
                    "replay_count": replay_count,
                    "plan_fingerprint": fingerprint,
                    "cpu": cpu,
                    "io": io,
                    "memory": memory,
                    "memory_occupancy_weight_raw": memory_weight,
                    "memory_latency_evidence": has_latency,
                }
            )
        missing_latency_count = sum(
            1 for _memory, _weight, has_latency, _index in memory_records
            if not has_latency
        )
        missing_latency_fallback: Optional[Dict[str, Any]] = None
        if self.require_query_latency_for_memory and missing_latency_count:
            missing_summary = {
                "missing": missing_latency_count,
                "total": len(memory_records),
                "source": "resource_only_replay_count_weight",
                "qps_used": False,
                "latency_used": False,
            }
            if not self.allow_resource_only_memory_fallback:
                raise RuntimeError(
                    "ratio-scale memory model requires query-level latency for all "
                    f"queries: missing={missing_latency_count}/{len(memory_records)}"
                )
            missing_latency_fallback = missing_summary
        total_memory_weight = sum(record[1] for record in memory_records)
        if total_memory_weight <= 0.0:
            raise RuntimeError("ratio-scale memory occupancy weights are empty")
        for memory, weight, _has_latency, query_index in memory_records:
            share = weight / total_memory_weight
            query_features[query_index]["memory_time_share"] = share
            for key, value in memory.items():
                aggregate[key] += (
                    float(value) * share * self.memory_concurrency
                )
        result: Dict[str, Any] = dict(aggregate)
        result["cpu_total"] = max(1.0, sum(
            result.get(key, 0.0) * weight
            for key, weight in {
                "cpu_scan_tuples": 1.0,
                "cpu_sort_cmps": 3.0,
                "cpu_hash_build": 1.5,
                "cpu_hash_probe": 1.0,
                "cpu_nl_inner": 2.0,
                "cpu_agg_tuples": 1.3,
                "cpu_join_tuples": 0.8,
                "cpu_proj_tuples": 0.25,
            }.items()
        ))
        result["io_total"] = max(1.0, (
            result.get("io_seq_pages", 0.0)
            + result.get("io_rand_pages", 0.0) * 4.0
            + result.get("io_bitmap_pages", 0.0) * 2.0
            + result.get("io_spill_pages", 0.0) * 2.0
            + result.get("io_wal_writes", 0.0)
        ) * (1.0 - self.cache_hit_rate))
        result["memory_total"] = max(
            1.0,
            result.get("mem_hash_build_bytes", 0.0)
            + result.get("mem_hashagg_bytes", 0.0)
            + result.get("mem_sort_bytes", 0.0)
            + result.get("mem_materialize_bytes", 0.0),
        )
        result["query_features"] = query_features
        result["workload_plan_fingerprint"] = (
            "ratio-plan-sha256:" + _canonical_sha256(fingerprints)
        )
        result["work_mem_bytes"] = work_mem_bytes
        result["memory_concurrency"] = self.memory_concurrency
        result["memory_latency_evidence_complete"] = (
            missing_latency_count == 0
        )
        result["memory_ratio_scale_skipped_missing_latency"] = (
            missing_latency_fallback
        )
        return result

    def _cpu_features(self, nodes: Sequence[Mapping[str, Any]]) -> Counter[str]:
        totals: Counter[str] = Counter()
        node_by_id = {str(node.get("node_id")): node for node in nodes}
        for node in nodes:
            node_type = str(node.get("node_type") or "").lower()
            rows = max(0.0, _safe_float(node.get("rows"), 0.0))
            loops = max(1.0, _safe_float(node.get("execution_multiplier"), 1.0))
            if "scan" in node_type:
                totals["cpu_scan_tuples"] += rows * loops
            if "sort" in node_type:
                sort_key = node.get("sort_key") or []
                key_count = len(sort_key) if isinstance(sort_key, list) else len(str(sort_key).split(","))
                totals["cpu_sort_cmps"] += rows * math.log2(max(rows, 2.0)) * max(1, key_count) * loops
            if node_type == "hash join":
                inner, outer = self._join_children(node, node_by_id)
                totals["cpu_hash_build"] += self._node_rows(inner, rows) * loops
                totals["cpu_hash_probe"] += self._node_rows(outer, rows) * loops
            elif node_type == "nested loop":
                inner, outer = self._join_children(node, node_by_id)
                totals["cpu_nl_inner"] += self._node_rows(inner, rows) * self._node_rows(outer, 1.0) * loops
            if "aggregate" in node_type or node_type == "group":
                totals["cpu_agg_tuples"] += max(rows, _safe_float(node.get("input_rows"), rows)) * loops
            if "join" in node_type or node_type == "nested loop":
                totals["cpu_join_tuples"] += rows * loops
            if node_type in {"result", "projectset"}:
                totals["cpu_proj_tuples"] += rows * loops
        return totals

    def _io_features(
        self,
        nodes: Sequence[Mapping[str, Any]],
        work_mem_bytes: float,
    ) -> Counter[str]:
        totals: Counter[str] = Counter()
        block_size = 8192.0
        for node in nodes:
            node_type = str(node.get("node_type") or "").lower()
            rows = max(0.0, _safe_float(node.get("rows"), 0.0))
            width = max(1.0, _safe_float(node.get("width"), 8.0))
            loops = max(1.0, _safe_float(node.get("execution_multiplier"), 1.0))
            bytes_est = rows * width
            relation = node.get("relation_stats") or {}
            relpages = max(0.0, _safe_float(relation.get("relpages"), 0.0))
            reltuples = max(0.0, _safe_float(relation.get("reltuples"), 0.0))
            if node_type in {"seq scan", "parallel seq scan"}:
                pages = relpages if relpages > 0.0 else math.ceil(bytes_est / block_size)
                totals["io_seq_pages"] += pages * loops
            elif node_type in {"index scan", "index only scan"}:
                heap_pages = self._distinct_heap_pages(rows, relpages, reltuples)
                index = node.get("index_stats") or {}
                index_pages = max(0.0, _safe_float(index.get("relpages"), 0.0))
                if index_pages <= 0.0:
                    index_pages = math.ceil(bytes_est / block_size / 10.0)
                totals["io_rand_pages"] += (heap_pages + index_pages * 0.3) * loops
            elif node_type == "bitmap heap scan":
                totals["io_bitmap_pages"] += self._distinct_heap_pages(
                    rows, relpages, reltuples
                ) * loops
            if "sort" in node_type and bytes_est > work_mem_bytes:
                totals["io_spill_pages"] += math.ceil(bytes_est / block_size) * loops
            if node_type in {"hash", "hash join"}:
                batches = max(1.0, _safe_float(node.get("hash_batches"), 1.0))
                if batches > 1.0 or bytes_est > work_mem_bytes:
                    totals["io_spill_pages"] += math.ceil(bytes_est / block_size) * loops
            operation = str(node.get("operation") or "").lower()
            if operation in {"insert", "update", "delete"}:
                totals["io_wal_writes"] += math.ceil(bytes_est / block_size) * loops
        return totals

    def _memory_features(self, nodes: Sequence[Mapping[str, Any]]) -> Counter[str]:
        totals: Counter[str] = Counter()
        node_by_id = {str(node.get("node_id")): node for node in nodes}
        for node in nodes:
            node_type = str(node.get("node_type") or "").lower()
            rows = max(0.0, _safe_float(node.get("rows"), 0.0))
            width = max(1.0, _safe_float(node.get("width"), 8.0))
            query_parallel_factor = max(
                1.0,
                _safe_float(node.get("query_parallel_factor"), 1.0),
            )
            parallel_factor = 1.0 + (query_parallel_factor - 1.0) * 0.8
            bytes_est = rows * width * parallel_factor
            if node_type == "hash join":
                inner, _outer = self._join_children(node, node_by_id)
                totals["mem_hash_build_bytes"] += (
                    self._node_rows(inner, rows)
                    * max(1.0, _safe_float((inner or {}).get("width"), width))
                    * parallel_factor
                )
            elif node_type in {"hashaggregate", "hash aggregate"} or (
                node_type == "aggregate" and str(node.get("strategy") or "").lower() == "hashed"
            ):
                totals["mem_hashagg_bytes"] += bytes_est
            elif "sort" in node_type:
                totals["mem_sort_bytes"] += bytes_est
            elif node_type in {"materialize", "memoize"}:
                totals["mem_materialize_bytes"] += bytes_est
        return totals

    @staticmethod
    def _query_memory_occupancy_weight(
        item: Mapping[str, Any],
        replay_count: float,
    ) -> Tuple[float, bool]:
        total_time_ms = _safe_float(
            item.get("total_time_ms", item.get("total_exec_time_ms")),
            0.0,
        )
        if total_time_ms > 0.0:
            return total_time_ms, True
        for key in (
            "mean_time_ms",
            "mean_exec_time_ms",
            "mean_latency_ms",
            "latency_ms",
        ):
            mean_time_ms = _safe_float(item.get(key), 0.0)
            if mean_time_ms > 0.0:
                return mean_time_ms * replay_count, True
        return replay_count, False

    def _resolve_memory_fixed_baseline(
        self,
        runtime_resource_real: Mapping[str, Any],
        baseline_memory: float,
    ) -> float:
        raw_value = runtime_resource_real.get(
            "memory_fixed",
            self.configured_memory_fixed_baseline,
        )
        if raw_value in (None, ""):
            if self.require_memory_floor:
                raise ValueError(
                    "ratio-scale memory_fixed baseline is required; measure idle "
                    "PostgreSQL/cgroup resident memory before calibration"
                )
            return 0.0
        fixed = _safe_float(raw_value, -1.0)
        if not math.isfinite(fixed) or fixed < 0.0 or fixed >= baseline_memory:
            raise ValueError(
                "baseline memory_fixed must be finite, non-negative, and lower "
                "than baseline_real.memory"
            )
        return fixed

    def _resolved_historical_error_bounds(
        self,
        baseline_real: Mapping[str, float],
    ) -> Dict[str, Dict[str, float]]:
        resolved: Dict[str, Dict[str, float]] = {}
        for resource_name in RESOURCE_NAMES:
            raw = self.historical_error_bounds.get(resource_name)
            if not isinstance(raw, Mapping):
                continue
            absolute = max(0.0, _safe_float(raw.get("absolute"), 0.0))
            relative_pct = max(
                0.0,
                _safe_float(raw.get("relative_pct"), 0.0),
            )
            relative_absolute = (
                float(baseline_real[resource_name]) * relative_pct / 100.0
            )
            bound = max(absolute, relative_absolute)
            if bound <= 0.0:
                continue
            resolved[resource_name] = {
                "absolute": bound,
                "configured_absolute": absolute,
                "relative_pct": relative_pct,
                "source": "historical_real_replay_error_bound",
            }
        return resolved

    @staticmethod
    def _join_children(
        node: Mapping[str, Any],
        node_by_id: Mapping[str, Mapping[str, Any]],
    ) -> Tuple[Optional[Mapping[str, Any]], Optional[Mapping[str, Any]]]:
        children = [node_by_id.get(str(child_id)) for child_id in (node.get("children") or [])]
        children = [child for child in children if child is not None]
        inner = next(
            (child for child in children if str(child.get("parent_relationship") or "").lower() == "inner"),
            children[-1] if children else None,
        )
        outer = next(
            (child for child in children if str(child.get("parent_relationship") or "").lower() == "outer"),
            children[0] if children else None,
        )
        return inner, outer

    @staticmethod
    def _node_rows(node: Optional[Mapping[str, Any]], default: float) -> float:
        return max(0.0, _safe_float((node or {}).get("rows"), default))

    @staticmethod
    def _distinct_heap_pages(rows: float, relation_pages: float, relation_tuples: float) -> float:
        if relation_tuples <= 0.0 or relation_pages <= 0.0:
            return max(1.0, min(rows, max(relation_pages, 1.0)))
        selectivity = min(max(rows / relation_tuples, 0.0), 1.0)
        distinct = relation_pages * (
            1.0 - (1.0 - selectivity) ** (relation_tuples / max(relation_pages, 1.0))
        )
        return max(1.0, min(relation_pages, distinct))

    def _work_mem_bytes(self, params: Mapping[str, Any]) -> float:
        if "work_mem_bytes" in params:
            return _finite_positive(params["work_mem_bytes"], name="work_mem_bytes")
        value = params.get("work_mem")
        if value in (None, ""):
            return self.default_work_mem_bytes
        if isinstance(value, (int, float)):
            return _finite_positive(value, name="work_mem") * 1024.0
        match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([kmgt]?b)?\s*", str(value), re.I)
        if not match:
            raise ValueError(f"unsupported work_mem value: {value!r}")
        number = float(match.group(1))
        unit = (match.group(2) or "kb").lower()
        scale = {"b": 1.0, "kb": 1024.0, "mb": 1024.0 ** 2, "gb": 1024.0 ** 3, "tb": 1024.0 ** 4}[unit]
        return _finite_positive(number * scale, name="work_mem")

    @staticmethod
    def _normalize_queries(sql_set: Any) -> List[Dict[str, Any]]:
        raw = sql_set.queries if hasattr(sql_set, "queries") else sql_set
        if isinstance(raw, (str, bytes, Mapping)):
            raw = [raw]
        if not isinstance(raw, (list, tuple)):
            raise TypeError("sql_set must be a query, query mapping, or sequence")
        queries: List[Dict[str, Any]] = []
        for index, item in enumerate(raw):
            if isinstance(item, Mapping):
                record = dict(item)
                query = str(record.get("query") or record.get("sql") or "").strip()
            else:
                record = {}
                query = str(item).strip()
            if not query:
                raise ValueError(f"query at index {index} is empty")
            record["query"] = query
            record.setdefault("queryid", f"ratio-query-{index + 1}")
            record.setdefault("replay_count", record.get("calls", 1))
            queries.append(record)
        if not queries:
            raise ValueError("ratio-scale estimator requires at least one query")
        return queries

    @staticmethod
    def _normalize_params(params: Mapping[str, Any]) -> Dict[str, Any]:
        if not isinstance(params, Mapping) or not params:
            raise ValueError("params must be a non-empty mapping")
        normalized: Dict[str, Any] = {}
        for name, value in params.items():
            if name in {"work_mem", "work_mem_bytes"}:
                normalized[str(name)] = value
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                normalized[str(name)] = number
        if not normalized:
            raise ValueError("params contain no finite numeric PostgreSQL settings")
        return normalized

    @staticmethod
    def _plan_fingerprint(plan_root: Any) -> str:
        # A plan regime represents execution topology, access paths,
        # predicates, and parallel structure.  PostgreSQL's row-count and
        # row-width estimates can drift after statistics refreshes without
        # changing any of those decisions.  Including them made identical
        # plans appear to enter a new regime between consecutive replays.
        volatile_estimate_keys = {
            "Startup Cost",
            "Total Cost",
            "Plan Rows",
            "Plan Width",
        }

        def strip_volatile_estimates(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    key: strip_volatile_estimates(item)
                    for key, item in sorted(value.items())
                    if key not in volatile_estimate_keys
                }
            if isinstance(value, list):
                return [strip_volatile_estimates(item) for item in value]
            return value

        identity = {
            "contract": "plan_topology_without_cost_or_row_estimates_v2",
            "plan": strip_volatile_estimates(plan_root),
        }
        return "exact-structure-sha256:" + _canonical_sha256(identity)

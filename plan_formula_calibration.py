from __future__ import annotations

import json
import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

from plan_resource_estimator import PlanResourceEstimator, _safe_float, _safe_int


def _block_count(node: Dict[str, Any], key: str) -> float:
    return _safe_float(node.get(key), 0.0)


def _percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(float(v) for v in values)
    idx = min(len(sorted_vals) - 1, max(0, int(round((len(sorted_vals) - 1) * q))))
    return float(sorted_vals[idx])


def _root_actual_io(plan_root: Dict[str, Any], statement_kind: str) -> Dict[str, float]:
    shared_read = _block_count(plan_root, "Shared Read Blocks")
    local_read = _block_count(plan_root, "Local Read Blocks")
    temp_read = _block_count(plan_root, "Temp Read Blocks")
    temp_write = _block_count(plan_root, "Temp Written Blocks")
    wal_records = _block_count(plan_root, "WAL Records")
    wal_fpi = _block_count(plan_root, "WAL FPI")
    wal_bytes = _block_count(plan_root, "WAL Bytes")
    wal_write_blocks = 0.0
    if wal_bytes > 0:
        wal_write_blocks = wal_bytes / 8192.0
    elif statement_kind in ("insert", "update", "delete") and (wal_records > 0 or wal_fpi > 0):
        wal_write_blocks = max(1.0, wal_records + wal_fpi)
    return {
        "data_read_blocks": shared_read + local_read,
        "temp_read_blocks": temp_read,
        "temp_write_blocks": temp_write,
        "wal_write_blocks": wal_write_blocks,
        "read_blocks": shared_read + local_read + temp_read,
        "write_blocks": temp_write + wal_write_blocks,
    }


def _dominant_scan_category(io_operator_breakdown: Sequence[Dict[str, Any]]) -> str:
    scan_map = {
        "seq scan": "seq_scan",
        "index scan": "index_scan",
        "index only scan": "index_only",
        "bitmap index scan": "bitmap_index",
        "bitmap heap scan": "bitmap_heap",
    }
    totals: Dict[str, float] = {}
    for item in io_operator_breakdown:
        category = scan_map.get(str(item.get("node_type") or "").lower())
        if not category:
            continue
        totals[category] = totals.get(category, 0.0) + _safe_float(item.get("logic_read_blocks"), 0.0)
    if not totals:
        return "other"
    return max(totals.items(), key=lambda kv: kv[1])[0]


def _walk_plan_nodes(plan_root: Any) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []

    def visit(node: Any) -> None:
        if not isinstance(node, dict):
            return
        nodes.append(node)
        for child in node.get("Plans", []) or []:
            visit(child)

    visit(plan_root)
    return nodes


def _operator_family(node_type: str) -> Optional[str]:
    normalized = str(node_type or "").lower()
    if normalized in ("sort", "incremental sort"):
        return "sort"
    if normalized in ("hash", "hash join"):
        return "hash"
    if normalized in ("hashaggregate", "hash aggregate"):
        return "hashagg"
    if normalized == "materialize":
        return "materialize"
    if normalized == "memoize":
        return "memoize"
    return None


def _actual_memory_bytes_by_family(
    plan_root: Dict[str, Any],
    *,
    work_mem_bytes: float,
    hash_mem_bytes: float,
) -> Dict[str, float]:
    totals = {
        "sort": 0.0,
        "hash": 0.0,
        "hashagg": 0.0,
        "materialize": 0.0,
        "memoize": 0.0,
    }
    for node in _walk_plan_nodes(plan_root):
        family = _operator_family(str(node.get("Node Type") or ""))
        if family is None:
            continue
        actual_bytes = 0.0
        if family == "sort":
            space_used_kb = _safe_float(node.get("Sort Space Used"), 0.0)
            space_type = str(node.get("Sort Space Type") or "").lower()
            if space_used_kb > 0:
                actual_bytes = space_used_kb * 1024.0
                if space_type == "disk":
                    actual_bytes = max(actual_bytes, work_mem_bytes)
        elif family in ("hash", "hashagg"):
            peak_memory_kb = _safe_float(node.get("Peak Memory Usage"), 0.0)
            if peak_memory_kb > 0:
                actual_bytes = peak_memory_kb * 1024.0
            elif _safe_float(node.get("Hash Batches"), 0.0) > 1:
                actual_bytes = hash_mem_bytes
        elif family == "materialize":
            if _safe_float(node.get("Temp Written Blocks"), 0.0) > 0:
                actual_bytes = work_mem_bytes
        elif family == "memoize":
            peak_memory_kb = _safe_float(node.get("Peak Memory Usage"), 0.0)
            if peak_memory_kb > 0:
                actual_bytes = peak_memory_kb * 1024.0
        totals[family] += actual_bytes
    return totals


def _estimated_memory_bytes_by_family(operator_breakdown: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    totals = {
        "sort": 0.0,
        "hash": 0.0,
        "hashagg": 0.0,
        "materialize": 0.0,
        "memoize": 0.0,
    }
    for item in operator_breakdown:
        family = _operator_family(str(item.get("node_type") or ""))
        if family is None:
            continue
        totals[family] += _safe_float(item.get("memory_bytes"), 0.0)
    return totals


class PlanFormulaCalibrator:
    """Use EXPLAIN ANALYZE actual plan-tree values to calibrate per-resource formulas."""

    def __init__(
        self,
        config: Dict[str, Any],
        *,
        pg_host: Optional[str] = None,
        pg_port: Optional[str] = None,
        pg_user: Optional[str] = None,
        pg_dbname: Optional[str] = None,
    ) -> None:
        metrics_cfg = config.get("metrics") or {}
        estimator_cfg = (metrics_cfg.get("plan_estimator") or {})
        pg_cfg = config.get("postgres") or {}
        self.estimator = PlanResourceEstimator(
            pg_host=str(pg_host or pg_cfg.get("host") or "127.0.0.1"),
            pg_port=str(pg_port or pg_cfg.get("port") or "25432"),
            pg_user=str(pg_user or pg_cfg.get("user") or "postgres"),
            pg_dbname=str(pg_dbname or pg_cfg.get("dbname") or "postgres"),
            estimator_cfg=estimator_cfg,
            plan_capture_modes=(config.get("plan_capture") or {}).get("modes") or ["generic"],
            custom_plan_params=(config.get("plan_capture") or {}).get("custom_plan_params") or {},
        )
        self.config = config

    def _build_analyze_sql(self, query_text: str, queryid: Any, mode: str = "generic") -> Tuple[str, str]:
        has_params = "$" in query_text
        explain_mode = str(mode or "generic").strip().lower()
        if explain_mode == "generic":
            if not has_params:
                return (
                    "EXPLAIN (ANALYZE true, BUFFERS true, WAL true, TIMING false, SUMMARY true, FORMAT JSON) "
                    + query_text,
                    "generic",
                )
            return (
                "EXPLAIN (ANALYZE true, BUFFERS true, WAL true, TIMING false, SUMMARY true, FORMAT JSON, GENERIC_PLAN true) "
                + query_text,
                "generic",
            )
        custom_query = (
            self.estimator._materialize_query_for_custom_plan(query_text, queryid)
            if has_params
            else query_text
        )
        return (
            "EXPLAIN (ANALYZE true, BUFFERS true, WAL true, TIMING false, SUMMARY true, FORMAT JSON) "
            + custom_query,
            explain_mode,
        )

    def _explain_analyze(self, query_text: str, queryid: Any) -> Tuple[Dict[str, Any], str]:
        modes = self.estimator.plan_capture_modes or ["generic"]
        last_error: Optional[Exception] = None
        for mode in modes:
            try:
                explain_sql, explain_mode = self._build_analyze_sql(query_text, queryid, mode=mode)
                raw = self.estimator._psql_query(explain_sql)
                payload = json.loads(raw)
                plan_doc = payload[0] if isinstance(payload, list) and payload else payload
                if not isinstance(plan_doc, dict):
                    raise RuntimeError("unexpected ANALYZE plan document")
                return plan_doc, explain_mode
            except Exception as exc:
                last_error = exc
        raise RuntimeError(str(last_error) if last_error else "ANALYZE calibration failed")

    def calibrate(
        self,
        queries: Sequence[Dict[str, Any]],
        *,
        workload_context: Dict[str, Any],
        topn: int = 20,
    ) -> Dict[str, Any]:
        ranked = sorted(
            list(queries),
            key=lambda item: max(1, _safe_int(item.get("replay_count", item.get("calls", 1)), 1)),
            reverse=True,
        )
        selected = ranked[: max(1, int(topn))]
        explained = self.estimator._collect_query_plans(selected, explain_workers=1)
        estimated = self.estimator._build_query_estimates(
            explained_queries=explained,
            workload_context=workload_context,
        )

        estimator_cfg = ((self.config.get("metrics") or {}).get("plan_estimator") or {})
        io_cfg = estimator_cfg.get("io") or {}
        cpu_cfg = estimator_cfg.get("cpu") or {}
        memory_cfg = estimator_cfg.get("memory") or {}
        work_mem_bytes = self.estimator._work_mem_bytes()
        hash_mem_bytes = self.estimator._hash_mem_bytes()

        per_query_results: List[Dict[str, Any]] = []
        io_category_ratios: Dict[str, List[float]] = {}
        cpu_ratios: List[float] = []
        memory_family_ratios: Dict[str, List[float]] = {}
        total_est_read = 0.0
        total_actual_read = 0.0
        total_est_write = 0.0
        total_actual_write = 0.0

        for item in estimated:
            if item.get("error"):
                per_query_results.append(
                    {
                        "queryid": item.get("queryid"),
                        "query": item.get("query"),
                        "replay_count": item.get("replay_count"),
                        "error": item.get("error"),
                    }
                )
                continue

            query_text = str(item.get("query") or "").strip().rstrip(";")
            queryid = item.get("queryid")
            statement_kind = str(item.get("statement_kind") or "unknown")
            query_resources = item.get("query_resources") or {}
            io_est = query_resources.get("io") or {}
            cpu_est = query_resources.get("cpu") or {}
            memory_est = query_resources.get("memory") or {}

            try:
                plan_doc, explain_mode = self._explain_analyze(query_text, queryid)
                plan_root = plan_doc.get("Plan") if isinstance(plan_doc, dict) else plan_doc
            except Exception as exc:
                per_query_results.append(
                    {
                        "queryid": queryid,
                        "query": query_text,
                        "replay_count": item.get("replay_count"),
                        "estimated": query_resources,
                        "error": str(exc),
                    }
                )
                continue

            actual_io = _root_actual_io(plan_root or {}, statement_kind)
            est_read = _safe_float(io_est.get("read_blocks_per_query"), 0.0)
            est_write = _safe_float(io_est.get("write_blocks_per_query"), 0.0)
            actual_read = _safe_float(actual_io.get("read_blocks"), 0.0)
            actual_write = _safe_float(actual_io.get("write_blocks"), 0.0)
            dominant_scan_category = _dominant_scan_category(io_est.get("operator_breakdown") or [])
            if est_read > 0 and actual_read > 0:
                io_category_ratios.setdefault(dominant_scan_category, []).append(actual_read / est_read)
            total_est_read += est_read
            total_actual_read += actual_read
            total_est_write += est_write
            total_actual_write += actual_write

            actual_execution_ms = _safe_float(
                plan_doc.get("Execution Time"),
                _safe_float((plan_root or {}).get("Actual Total Time"), 0.0),
            )
            est_cpu_work = _safe_float(cpu_est.get("cpu_work_per_query"), 0.0)
            actual_cpu_seconds = actual_execution_ms / 1000.0
            if est_cpu_work > 0 and actual_cpu_seconds > 0:
                cpu_ratios.append(actual_cpu_seconds / est_cpu_work)

            estimated_memory_by_family = _estimated_memory_bytes_by_family(
                memory_est.get("operator_breakdown") or []
            )
            actual_memory_by_family = _actual_memory_bytes_by_family(
                plan_root or {},
                work_mem_bytes=work_mem_bytes,
                hash_mem_bytes=hash_mem_bytes,
            )
            for family, estimated_bytes in estimated_memory_by_family.items():
                actual_bytes = _safe_float(actual_memory_by_family.get(family), 0.0)
                if estimated_bytes > 0 and actual_bytes > 0:
                    memory_family_ratios.setdefault(family, []).append(actual_bytes / estimated_bytes)

            per_query_results.append(
                {
                    "queryid": queryid,
                    "query": query_text,
                    "replay_count": item.get("replay_count"),
                    "plan_mode": explain_mode,
                    "estimated": {
                        "io": {
                            "read_blocks": est_read,
                            "write_blocks": est_write,
                        },
                        "cpu": {
                            "cpu_work_per_query": est_cpu_work,
                            "cpu_seconds_per_query": _safe_float(cpu_est.get("cpu_seconds_per_query"), 0.0),
                        },
                        "memory": {
                            "by_family": estimated_memory_by_family,
                            "mem_query_parallel_bytes": _safe_float(memory_est.get("mem_query_parallel_bytes"), 0.0),
                        },
                    },
                    "actual": {
                        "io": actual_io,
                        "cpu": {
                            "execution_time_ms": actual_execution_ms,
                            "actual_cpu_seconds": actual_cpu_seconds,
                        },
                        "memory": {
                            "by_family": actual_memory_by_family,
                        },
                    },
                }
            )

        memory_beta_field = {
            "sort": "beta_sort",
            "hash": "beta_hash",
            "hashagg": "beta_hashagg",
            "materialize": "beta_materialize",
            "memoize": "beta_memoize",
        }

        recommendations: Dict[str, Any] = {
            "io": {
                "global": {
                    "read_ratio_actual_over_estimated": (
                        total_actual_read / total_est_read if total_est_read > 0 else None
                    ),
                    "write_ratio_actual_over_estimated": (
                        total_actual_write / total_est_write if total_est_write > 0 else None
                    ),
                },
                "cache_miss_factors": {},
            },
            "cpu": {},
            "memory": {},
        }

        for category, ratios in io_category_ratios.items():
            current = _safe_float(
                (io_cfg.get("cache_miss_factors") or {}).get(category),
                {
                    "seq_scan": 1.5,
                    "index_scan": 0.5,
                    "index_only": 0.3,
                    "bitmap_index": 0.4,
                    "bitmap_heap": 1.0,
                }.get(category, 1.0),
            )
            median_ratio = statistics.median(ratios) if ratios else 1.0
            recommendations["io"]["cache_miss_factors"][category] = {
                "current": current,
                "median_actual_over_estimated": median_ratio,
                "p90_actual_over_estimated": _percentile(ratios, 0.90),
                "sample_count": len(ratios),
                "suggested": max(0.01, current * median_ratio),
            }

        if cpu_ratios:
            current_cpu_seconds_per_work = _safe_float(cpu_cfg.get("cpu_seconds_per_work"), 1e-6)
            median_ratio = statistics.median(cpu_ratios)
            recommendations["cpu"]["cpu_seconds_per_work"] = {
                "current": current_cpu_seconds_per_work,
                "median_actual_over_estimated": median_ratio,
                "p90_actual_over_estimated": _percentile(cpu_ratios, 0.90),
                "sample_count": len(cpu_ratios),
                "suggested": max(1e-9, current_cpu_seconds_per_work * median_ratio),
            }

        for family, ratios in memory_family_ratios.items():
            field_name = memory_beta_field.get(family)
            if not field_name or not ratios:
                continue
            current = _safe_float(memory_cfg.get(field_name), 1.0)
            median_ratio = statistics.median(ratios)
            recommendations["memory"][field_name] = {
                "current": current,
                "median_actual_over_estimated": median_ratio,
                "p90_actual_over_estimated": _percentile(ratios, 0.90),
                "sample_count": len(ratios),
                "suggested": max(0.0, current * median_ratio),
            }

        return {
            "sample_query_count": len(selected),
            "successful_query_count": sum(1 for item in per_query_results if not item.get("error")),
            "failed_query_count": sum(1 for item in per_query_results if item.get("error")),
            "workload_context": workload_context,
            "per_query": per_query_results,
            "recommendations": recommendations,
        }

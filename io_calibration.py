from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from plan_resource_estimator import PlanResourceEstimator, _safe_float, _safe_int


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _block_count(node: Dict[str, Any], key: str) -> float:
    return _safe_float(node.get(key), 0.0)


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
        "shared_read_blocks": shared_read,
        "local_read_blocks": local_read,
        "wal_bytes": wal_bytes,
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
        node_type = str(item.get("node_type") or "").lower()
        category = scan_map.get(node_type)
        if not category:
            continue
        totals[category] = totals.get(category, 0.0) + _safe_float(item.get("logic_read_blocks"), 0.0)
    if not totals:
        return "other"
    return max(totals.items(), key=lambda kv: kv[1])[0]


def _percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(float(v) for v in values)
    idx = min(len(sorted_vals) - 1, max(0, int(round((len(sorted_vals) - 1) * q))))
    return float(sorted_vals[idx])


class IOCalibrator:
    """IOCalibrator."""

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
        estimator_cfg = ((metrics_cfg.get("plan_estimator") or {}))
        pg_cfg = config.get("postgresql") or {}
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

    def _explain_analyze_buffers(self, query_text: str, queryid: Any) -> Tuple[Dict[str, Any], str]:
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
        raise RuntimeError(str(last_error) if last_error else "ANALYZE+BUFFERS failed")

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

        per_query_results: List[Dict[str, Any]] = []
        category_ratios: Dict[str, List[float]] = {}
        total_est_read = 0.0
        total_est_write = 0.0
        total_actual_read = 0.0
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
            try:
                plan_doc, explain_mode = self._explain_analyze_buffers(query_text, queryid)
                plan_root = plan_doc.get("Plan") if isinstance(plan_doc, dict) else plan_doc
                actual = _root_actual_io(plan_root or {}, statement_kind)
            except Exception as exc:
                per_query_results.append(
                    {
                        "queryid": queryid,
                        "query": query_text,
                        "replay_count": item.get("replay_count"),
                        "estimated": io_est,
                        "error": str(exc),
                    }
                )
                continue

            est_read = _safe_float(io_est.get("read_blocks_per_query"), 0.0)
            est_write = _safe_float(io_est.get("write_blocks_per_query"), 0.0)
            actual_read = _safe_float(actual.get("read_blocks"), 0.0)
            actual_write = _safe_float(actual.get("write_blocks"), 0.0)
            read_ratio = (actual_read / est_read) if est_read > 0 else None
            write_ratio = (actual_write / est_write) if est_write > 0 else None
            category = _dominant_scan_category(io_est.get("operator_breakdown") or [])
            if read_ratio is not None:
                category_ratios.setdefault(category, []).append(read_ratio)

            total_est_read += est_read
            total_est_write += est_write
            total_actual_read += actual_read
            total_actual_write += actual_write

            per_query_results.append(
                {
                    "queryid": queryid,
                    "query": query_text,
                    "replay_count": item.get("replay_count"),
                    "dominant_scan_category": category,
                    "plan_mode": explain_mode,
                    "estimated": {
                        "logical_read_blocks": io_est.get("logical_read_blocks_per_query"),
                        "data_read_blocks": io_est.get("data_read_blocks_per_query"),
                        "temp_read_blocks": io_est.get("temp_read_blocks_per_query"),
                        "temp_write_blocks": io_est.get("temp_write_blocks_per_query"),
                        "wal_write_blocks": io_est.get("wal_write_blocks_per_query"),
                        "read_blocks": est_read,
                        "write_blocks": est_write,
                    },
                    "actual": actual,
                    "ratios": {
                        "read_ratio_actual_over_estimated": read_ratio,
                        "write_ratio_actual_over_estimated": write_ratio,
                    },
                }
            )

        io_cfg = ((self.config.get("metrics") or {}).get("plan_estimator") or {}).get("io") or {}
        miss_cfg = io_cfg.get("cache_miss_factors") or {}
        recommendations: Dict[str, Any] = {
            "global": {
                "total_estimated_read_blocks": total_est_read,
                "total_actual_read_blocks": total_actual_read,
                "total_estimated_write_blocks": total_est_write,
                "total_actual_write_blocks": total_actual_write,
                "read_ratio_actual_over_estimated": (
                    total_actual_read / total_est_read if total_est_read > 0 else None
                ),
                "write_ratio_actual_over_estimated": (
                    total_actual_write / total_est_write if total_est_write > 0 else None
                ),
            },
            "cache_miss_factors": {},
        }

        for category, ratios in category_ratios.items():
            current = _safe_float(miss_cfg.get(category), {
                "seq_scan": 1.5,
                "index_scan": 0.5,
                "index_only": 0.3,
                "bitmap_index": 0.4,
                "bitmap_heap": 1.0,
            }.get(category, 1.0))
            median_ratio = statistics.median(ratios) if ratios else 1.0
            p90_ratio = _percentile(ratios, 0.90)
            recommendations["cache_miss_factors"][category] = {
                "current": current,
                "median_actual_over_estimated": median_ratio,
                "p90_actual_over_estimated": p90_ratio,
                "sample_count": len(ratios),
                "suggested": max(0.01, current * median_ratio),
            }

        return {
            "sample_query_count": len(selected),
            "successful_query_count": sum(1 for item in per_query_results if not item.get("error")),
            "failed_query_count": sum(1 for item in per_query_results if item.get("error")),
            "workload_context": workload_context,
            "per_query": per_query_results,
            "recommendations": recommendations,
        }


def _load_workload_context(history_jsonl: Path) -> Dict[str, Any]:
    with history_jsonl.open("r", encoding="utf-8") as fh:
        for line in fh:
            item = json.loads(line)
            if item.get("event") == "baseline_top_sql":
                return dict(item.get("plan_estimator_workload_profile") or {})
    raise RuntimeError(f"baseline_top_sql not found in {history_jsonl}")


def _default_output_dir() -> Path:
    return Path("history") / "io_calibration"


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate plan-estimator IO with EXPLAIN ANALYZE BUFFERS")
    parser.add_argument(
        "--config",
        default="config/optimizer_config.json",
        help="Optimizer config path",
    )
    parser.add_argument(
        "--replay-queries",
        required=True,
        help="Path to replay_queries.json",
    )
    parser.add_argument(
        "--history-jsonl",
        required=True,
        help="Path to optimization_history_*.jsonl for baseline workload context",
    )
    parser.add_argument(
        "--topn",
        type=int,
        default=20,
        help="Top replay_count queries to calibrate",
    )
    parser.add_argument(
        "--output-dir",
        default=str(_default_output_dir()),
        help="Output directory",
    )
    parser.add_argument("--pg-host", default=None, help="PostgreSQL host override")
    parser.add_argument("--pg-port", default=None, help="PostgreSQL port override")
    parser.add_argument("--pg-user", default=None, help="PostgreSQL user override")
    parser.add_argument("--pg-dbname", default=None, help="PostgreSQL dbname override")
    args = parser.parse_args()

    config = _load_json(Path(args.config))
    queries = _load_json(Path(args.replay_queries))
    if not isinstance(queries, list):
        raise RuntimeError("replay_queries must be a JSON array")
    workload_context = _load_workload_context(Path(args.history_jsonl))
    calibrator = IOCalibrator(
        config,
        pg_host=args.pg_host,
        pg_port=args.pg_port,
        pg_user=args.pg_user,
        pg_dbname=args.pg_dbname,
    )
    result = calibrator.calibrate(
        queries,
        workload_context=workload_context,
        topn=args.topn,
    )

    output_dir = Path(args.output_dir)
    _ensure_dir(output_dir)
    stem = Path(args.history_jsonl).stem.replace("optimization_history_", "")
    result_path = output_dir / f"io_calibration_{stem}.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print(f"Calibration report written to: {result_path}", flush=True)
    global_rec = (result.get("recommendations") or {}).get("global") or {}
    print("Global ratios:", flush=True)
    print(
        json.dumps(
            {
                "read_ratio_actual_over_estimated": global_rec.get("read_ratio_actual_over_estimated"),
                "write_ratio_actual_over_estimated": global_rec.get("write_ratio_actual_over_estimated"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    print("Suggested cache_miss_factors:", flush=True)
    print(
        json.dumps(
            (result.get("recommendations") or {}).get("cache_miss_factors") or {},
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()

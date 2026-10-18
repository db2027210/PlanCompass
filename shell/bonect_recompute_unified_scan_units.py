#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Dict, Iterable, List, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from resource_utility import ResourceUtilityCalculator


COST_PARAMS = [
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
]


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def numeric_stats(values: Sequence[float]) -> Dict[str, Any]:
    vals = [float(v) for v in values if v == v]
    if not vals:
        return {"count": 0, "min": None, "max": None, "mean": None}
    return {
        "count": len(vals),
        "min": min(vals),
        "max": max(vals),
        "mean": sum(vals) / len(vals),
    }


def normalize_raw_resource(raw: Dict[str, Any]) -> Dict[str, float]:
    return {
        "cpu": float(raw.get("cpu", 0.0) or 0.0),
        "memory": float(raw.get("memory", 0.0) or 0.0) / (1024.0 ** 3),
        "io": float(raw.get("io", 0.0) or 0.0) / (1024.0 ** 2),
    }


def resource_signature(resource: Dict[str, float]) -> str:
    return "|".join(f"{name}={float(resource.get(name, 0.0)):.9g}" for name in ["cpu", "memory", "io"])


def score_signature(score: float) -> str:
    return f"{float(score):.12g}"


def build_calculator(config: Dict[str, Any]) -> ResourceUtilityCalculator:
    scoring_cfg = config.get("scoring") or {}
    return ResourceUtilityCalculator(
        reward_type="sigmoid_linear",
        penalty_type="sigmoid_linear",
        overload_threshold=float(scoring_cfg.get("overload_threshold", 0.80)),
        score_mode=str(scoring_cfg.get("score_mode", "band_target")),
        resource_weights=scoring_cfg.get("resource_weights") if isinstance(scoring_cfg.get("resource_weights"), dict) else None,
        resource_reward_weights=(
            scoring_cfg.get("resource_reward_weights")
            if isinstance(scoring_cfg.get("resource_reward_weights"), dict)
            else None
        ),
        resource_penalty_weights=(
            scoring_cfg.get("resource_penalty_weights")
            if isinstance(scoring_cfg.get("resource_penalty_weights"), dict)
            else None
        ),
        resource_target_ranges=(
            scoring_cfg.get("resource_target_ranges")
            if isinstance(scoring_cfg.get("resource_target_ranges"), dict)
            else None
        ),
    )


def enrich_record(row: Dict[str, Any], calculator: ResourceUtilityCalculator, capability: Dict[str, float]) -> Dict[str, Any]:
    raw = row.get("estimated_resource") or {}
    resource = normalize_raw_resource(raw)
    utilization = {
        name: (float(resource.get(name, 0.0)) / float(capability[name]) * 100.0)
        for name in capability
        if name in resource and float(capability[name]) != 0.0
    }
    score = float(calculator.calculate_utility_score(resource, capability))
    out = dict(row)
    out["estimated_score_raw_units"] = row.get("score")
    out["estimated_resource_raw"] = raw
    out["estimated_resource_raw_units"] = {"cpu": "cores", "memory": "bytes", "io": "bytes/s"}
    out["estimated_resource"] = resource
    out["estimated_resource_units"] = {"cpu": "cores", "memory": "GB", "io": "MB/s"}
    out["utilization_pct"] = utilization
    out["score"] = score
    out["score_signature"] = score_signature(score)
    out["resource_signature"] = resource_signature(resource)
    return out


def distinct_top(records: Sequence[Dict[str, Any]], key: str, limit: int = 20) -> List[Dict[str, Any]]:
    seen = set()
    rows: List[Dict[str, Any]] = []
    for row in sorted(records, key=lambda item: float(item.get("score", float("-inf"))), reverse=True):
        marker = str(row.get(key))
        if marker in seen:
            continue
        seen.add(marker)
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def write_top_markdown(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    headers = [
        "rank",
        "score",
        "cpu_cores",
        "memory_GB",
        "io_MBps",
        "cpu_util_pct",
        "memory_util_pct",
        "io_util_pct",
        *COST_PARAMS,
        "failed_query_count",
        "plan_hash",
    ]
    lines = [
        "# Top20 Distinct Scores (Unified Units)",
        "",
        "- estimated_resource CPU=coresmemory=GBIO=MB/s",
        "",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for rank, row in enumerate(rows, start=1):
        params = row.get("params") or {}
        res = row.get("estimated_resource") or {}
        util = row.get("utilization_pct") or {}
        values: List[Any] = [
            rank,
            f"{float(row.get('score', 0.0)):.6f}",
            f"{float(res.get('cpu', 0.0)):.6f}",
            f"{float(res.get('memory', 0.0)):.6f}",
            f"{float(res.get('io', 0.0)):.6f}",
            f"{float(util.get('cpu', 0.0)):.3f}",
            f"{float(util.get('memory', 0.0)):.3f}",
            f"{float(util.get('io', 0.0)):.3f}",
        ]
        values.extend(f"{float(params.get(name, 0.0)):.9g}" for name in COST_PARAMS)
        values.append(int(row.get("failed_query_count", 0) or 0))
        values.append(str(row.get("plan_hash", ""))[:12])
        lines.append("| " + " | ".join(str(value) for value in values) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def bounds(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "candidate_count": len(records),
        "score": numeric_stats([float(row.get("score", float("nan"))) for row in records]),
        "estimated_resource": {},
        "utilization_pct": {},
        "estimated_resource_units": {"cpu": "cores", "memory": "GB", "io": "MB/s"},
    }
    for name in ["cpu", "memory", "io"]:
        payload["estimated_resource"][name] = numeric_stats(
            [float((row.get("estimated_resource") or {}).get(name, float("nan"))) for row in records]
        )
        payload["utilization_pct"][name] = numeric_stats(
            [float((row.get("utilization_pct") or {}).get(name, float("nan"))) for row in records]
        )
    return payload


def write_bounds_markdown(path: Path, data: Dict[str, Any]) -> None:
    lines = [
        "# Score and Resource Bounds (Unified Units)",
        "",
        "- estimated_resource CPU=coresmemory=GBIO=MB/s",
        f"- candidate_count: `{data.get('candidate_count', 0)}`",
        "",
        "| metric | count | min | max | mean |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]

    def add_row(name: str, stats: Dict[str, Any]) -> None:
        def fmt(value: Any) -> str:
            return "" if value is None else f"{float(value):.9g}"

        lines.append(
            f"| {name} | {int(stats.get('count', 0) or 0)} | {fmt(stats.get('min'))} | "
            f"{fmt(stats.get('max'))} | {fmt(stats.get('mean'))} |"
        )

    add_row("score", data.get("score") or {})
    for name, stats in (data.get("estimated_resource") or {}).items():
        add_row(f"estimated_resource.{name}", stats)
    for name, stats in (data.get("utilization_pct") or {}).items():
        add_row(f"utilization_pct.{name}", stats)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def process_scan_dir(scan_dir: Path) -> Dict[str, Any]:
    config_path = scan_dir / "scan_config.json"
    candidates_path = scan_dir / "candidates.jsonl"
    if not config_path.exists() or not candidates_path.exists():
        raise FileNotFoundError(f"missing scan_config.json or candidates.jsonl in {scan_dir}")
    scan_config = read_json(config_path)
    source_config = Path(str(scan_config.get("config_path") or ""))
    if not source_config.is_absolute():
        source_config = ROOT / source_config
    config = read_json(source_config)
    capability = {str(k): float(v) for k, v in (config.get("capability") or {}).items()}
    calculator = build_calculator(config)
    records = [enrich_record(row, calculator, capability) for row in iter_jsonl(candidates_path)]

    top_scores = distinct_top(records, "score_signature")
    top_resources = distinct_top(records, "resource_signature")
    top_plans = distinct_top(records, "plan_hash")
    bound_data = bounds(records)
    distinct_scores = {row.get("score_signature") for row in records}
    distinct_resources = {row.get("resource_signature") for row in records}
    distinct_plans = {row.get("plan_hash") for row in records}
    summary = {
        "scan_dir": str(scan_dir),
        "config_path": str(source_config),
        "candidate_count": len(records),
        "estimated_resource_units": {"cpu": "cores", "memory": "GB", "io": "MB/s"},
        "score_bounds": bound_data.get("score"),
        "estimated_resource_bounds": bound_data.get("estimated_resource"),
        "utilization_pct_bounds": bound_data.get("utilization_pct"),
        "distinct_score_count": len(distinct_scores),
        "distinct_resource_count": len(distinct_resources),
        "distinct_plan_count": len(distinct_plans),
        "best_score": max((float(row.get("score", float("-inf"))) for row in records), default=None),
        "top20_distinct_scores_path": str(scan_dir / "top20_distinct_scores_unified_units.json"),
        "score_resource_bounds_path": str(scan_dir / "score_resource_bounds_unified_units.json"),
    }

    write_json(scan_dir / "top20_distinct_scores_unified_units.json", top_scores)
    write_json(scan_dir / "top20_distinct_resources_unified_units.json", top_resources)
    write_json(scan_dir / "top20_distinct_plans_unified_units.json", top_plans)
    write_top_markdown(scan_dir / "top20_distinct_scores_unified_units.md", top_scores)
    write_json(scan_dir / "score_resource_bounds_unified_units.json", bound_data)
    write_bounds_markdown(scan_dir / "score_resource_bounds_unified_units.md", bound_data)
    write_json(scan_dir / "scan_summary_unified_units.json", summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recompute Bonect estimator scan scores with unified resource units.")
    parser.add_argument("scan_dirs", nargs="+", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summaries = [process_scan_dir(path) for path in args.scan_dirs]
    for summary in summaries:
        print(
            f"{summary['scan_dir']}: candidates={summary['candidate_count']} "
            f"distinct_score={summary['distinct_score_count']} "
            f"distinct_resource={summary['distinct_resource_count']} "
            f"best_score={summary['best_score']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

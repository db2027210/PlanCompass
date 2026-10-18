#!/usr/bin/env python3
"""Write a Bonect rough-tuning reference summary into a run directory."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DOC = PROJECT_ROOT / ".agents" / "bonect-result-reference.md"
RESULT_SOURCES = [
    PROJECT_ROOT / "result" / "bonect" / "cpu.md",
    PROJECT_ROOT / "result" / "bonect" / "mem.md",
    PROJECT_ROOT / "result" / "bonect" / "io.md",
    PROJECT_ROOT / "result" / "bonect" / "experience_table.md",
]
DEFAULT_HISTORY_ROOT = PROJECT_ROOT / "history" / "pg_benchmark_bottleneck_matrix"
RESOURCE_COLUMNS = ("cpu", "memory", "io")
LATENCY_SEVERE_DEGRADATION_PCT = 10000.0
MAX_LATENCY_RATIO = 1.0 + LATENCY_SEVERE_DEGRADATION_PCT / 100.0
FALLBACK_BOTTLENECK_MAX_INCREASE_RATIO = 0.02
ROUGH_TABLE_SOURCES: List[Tuple[str, str, Path]] = [
    ("cpu", "hybench_xp", PROJECT_ROOT / "result" / "bonect" / "cpu.md"),
    ("memory", "hybench_ap", PROJECT_ROOT / "result" / "bonect" / "mem.md"),
    ("io", "hybench_xp", PROJECT_ROOT / "result" / "bonect" / "io.md"),
]


def source_meta(path: Path) -> Dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path),
        "size_bytes": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
    }


def validate_sources() -> List[Path]:
    missing = [path for path in [REFERENCE_DOC, *RESULT_SOURCES] if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing Bonect reference files: " + ", ".join(str(path) for path in missing))
    return [REFERENCE_DOC, *RESULT_SOURCES]


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_bool(value: Any) -> Optional[bool]:
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _coarse_text(text: str) -> str:
    match = re.search(r"\n#{1,6}\s*(?:\*\*)?", text)
    return text[: match.start()] if match else text


def _split_table_line(line: str) -> List[str]:
    return [part.strip() for part in line.strip().strip("|").split("|")]


def _parse_markdown_iteration_tables(text: str) -> List[List[Dict[str, Any]]]:
    lines = text.splitlines()
    tables: List[List[Dict[str, Any]]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.lstrip().startswith("|") or "iteration" not in line:
            i += 1
            continue
        headers = _split_table_line(line)
        if "iteration" not in headers:
            i += 1
            continue
        if i + 1 >= len(lines) or "---" not in lines[i + 1]:
            i += 1
            continue
        rows: List[Dict[str, Any]] = []
        i += 2
        while i < len(lines) and lines[i].lstrip().startswith("|"):
            cells = _split_table_line(lines[i])
            if len(cells) == len(headers):
                row = {headers[idx]: cells[idx] for idx in range(len(headers))}
                row["_numeric"] = {
                    key: _to_float(value)
                    for key, value in row.items()
                    if not key.startswith("_")
                }
                row["_accepted_bool"] = _to_bool(row.get("accepted"))
                rows.append(row)
            i += 1
        if rows:
            tables.append(rows)
        continue
    return tables


def _resource_value(row: Dict[str, Any], resource: str) -> Optional[float]:
    numeric = row.get("_numeric") or {}
    if resource == "io":
        direct = numeric.get("io")
        if direct is not None:
            return direct
        read = numeric.get("io_read")
        write = numeric.get("io_write")
        if read is None and write is None:
            return None
        return float(read or 0.0) + float(write or 0.0)
    return numeric.get(resource)


def _latency_ratio(row: Dict[str, Any], baseline: Dict[str, Any], metric: str) -> Optional[float]:
    numeric = row.get("_numeric") or {}
    base_numeric = baseline.get("_numeric") or {}
    value = numeric.get(metric)
    base = base_numeric.get(metric)
    if value is None or base is None or base <= 0.0:
        return None
    return float(value) / float(base)


def _evaluate_good_point(row: Dict[str, Any], baseline: Dict[str, Any], bottleneck: str) -> Dict[str, Any]:
    numeric = row.get("_numeric") or {}
    base_numeric = baseline.get("_numeric") or {}
    db_qps = numeric.get("db_qps")
    base_db_qps = base_numeric.get("db_qps")
    db_qps_increased = db_qps is not None and base_db_qps is not None and db_qps > base_db_qps
    db_qps_ratio = (float(db_qps) / float(base_db_qps)) if db_qps is not None and base_db_qps else None
    db_tps = numeric.get("db_xact_tps", numeric.get("db_tps"))
    base_db_tps = base_numeric.get("db_xact_tps", base_numeric.get("db_tps"))
    db_tps_increased = db_tps is not None and base_db_tps is not None and db_tps > base_db_tps
    db_tps_ratio = (float(db_tps) / float(base_db_tps)) if db_tps is not None and base_db_tps else None

    latency_details: List[Dict[str, Any]] = []
    latency_ok = True
    for metric in ("latency_p90", "latency_p95", "latency_p99"):
        ratio = _latency_ratio(row, baseline, metric)
        if ratio is None:
            latency_details.append({"metric": metric, "checked": False, "reason": "missing"})
            continue
        degradation_pct = (ratio - 1.0) * 100.0
        passed = degradation_pct <= LATENCY_SEVERE_DEGRADATION_PCT
        latency_details.append(
            {
                "metric": metric,
                "checked": True,
                "ratio": ratio,
                "degradation_pct": degradation_pct,
                "passed": passed,
            }
        )
        if not passed:
            latency_ok = False

    resource_details: List[Dict[str, Any]] = []
    resource_direction_ok = True
    bottleneck_passed = True
    non_target_passed_count = 0
    non_target_checked_count = 0
    none_checked_count = 0
    none_increased_count = 0
    none_base_total = 0.0
    none_candidate_total = 0.0
    if bottleneck == "none":
        expected = {name: "increase_contributes_to_overall" for name in RESOURCE_COLUMNS}
    else:
        expected = {
            name: ("decrease" if name == bottleneck else "increase")
            for name in RESOURCE_COLUMNS
        }
    for resource, direction in expected.items():
        value = _resource_value(row, resource)
        base = _resource_value(baseline, resource)
        if value is None or base is None:
            passed = False
            delta = None
            reason = "missing"
        else:
            delta = float(value) - float(base)
            passed = delta < 0.0 if direction == "decrease" else delta > 0.0
            reason = "ok" if passed else "wrong_direction"
            if bottleneck == "none":
                none_checked_count += 1
                none_base_total += float(base)
                none_candidate_total += float(value)
                if passed:
                    none_increased_count += 1
        resource_details.append(
            {
                "resource": resource,
                "baseline": base,
                "candidate": value,
                "delta": delta,
                "expected": direction,
                "passed": passed,
                "reason": reason,
            }
        )
        if bottleneck == "none":
            continue
        elif resource == bottleneck:
            bottleneck_passed = bool(passed)
        else:
            non_target_checked_count += 1
            if passed:
                non_target_passed_count += 1

    if bottleneck == "none":
        required_increase_count = max(1, (none_checked_count // 2) + 1)
        total_delta = none_candidate_total - none_base_total
        aggregate_passed = none_checked_count == len(RESOURCE_COLUMNS) and total_delta > 0.0
        majority_passed = none_increased_count >= required_increase_count
        resource_direction_ok = bool(aggregate_passed and majority_passed)
        resource_details.append(
            {
                "resource": "__overall_group__",
                "baseline": none_base_total if none_checked_count else None,
                "candidate": none_candidate_total if none_checked_count else None,
                "delta": total_delta if none_checked_count else None,
                "expected": "aggregate_increase_and_resource_majority_increase",
                "passed": resource_direction_ok,
                "reason": "none_resources_must_trend_up_overall",
                "checked_count": none_checked_count,
                "increased_count": none_increased_count,
                "required_increase_count": required_increase_count,
                "aggregate_passed": aggregate_passed,
                "majority_passed": majority_passed,
            }
        )
    else:
        resource_direction_ok = bool(bottleneck_passed and non_target_passed_count >= 1)
        resource_details.append(
            {
                "resource": "__non_target_group__",
                "baseline": None,
                "candidate": None,
                "delta": None,
                "expected": "at_least_one_increase",
                "passed": non_target_passed_count >= 1,
                "reason": "non_target_resource_must_have_at_least_one_increase",
                "checked_count": non_target_checked_count,
                "passed_count": non_target_passed_count,
            }
        )

    fallback_resource_details: List[Dict[str, Any]] = []
    fallback_resource_direction_ok = False
    if bottleneck != "none":
        fallback_ok = True
        for resource in RESOURCE_COLUMNS:
            value = _resource_value(row, resource)
            base = _resource_value(baseline, resource)
            if value is None or base is None:
                passed = False
                delta = None
                max_delta = None
                expected = "not_decrease_or_mild_increase" if resource == bottleneck else "increase"
                reason = "missing"
            else:
                delta = float(value) - float(base)
                if resource == bottleneck:
                    max_delta = abs(float(base)) * FALLBACK_BOTTLENECK_MAX_INCREASE_RATIO
                    passed = delta >= 0.0 and delta <= max_delta
                    expected = "not_decrease_or_mild_increase"
                    reason = "ok" if passed else "bottleneck_not_in_fallback_band"
                else:
                    max_delta = None
                    passed = delta > 0.0
                    expected = "increase"
                    reason = "ok" if passed else "non_target_must_increase"
            fallback_resource_details.append(
                {
                    "resource": resource,
                    "baseline": base,
                    "candidate": value,
                    "delta": delta,
                    "max_delta": max_delta,
                    "expected": expected,
                    "passed": passed,
                    "reason": reason,
                }
            )
            if not passed:
                fallback_ok = False
        fallback_resource_direction_ok = fallback_ok

    strict_good = bool(db_qps_increased and db_tps_increased and latency_ok and resource_direction_ok)
    fallback_good = bool(db_qps_increased and latency_ok and fallback_resource_direction_ok)
    return {
        "iteration": int(numeric.get("iteration") or -1),
        "accepted": row.get("_accepted_bool"),
        "score": numeric.get("score"),
        "best_score": numeric.get("best_score"),
        "db_qps": db_qps,
        "baseline_db_qps": base_db_qps,
        "db_qps_ratio": db_qps_ratio,
        "db_qps_increased": db_qps_increased,
        "db_tps": db_tps,
        "baseline_db_tps": base_db_tps,
        "db_tps_ratio": db_tps_ratio,
        "db_tps_increased": db_tps_increased,
        "latency_not_severely_degraded": latency_ok,
        "latency_details": latency_details,
        "resource_direction_ok": resource_direction_ok,
        "resource_direction_details": resource_details,
        "strict_good": strict_good,
        "fallback_resource_direction_ok": fallback_resource_direction_ok,
        "fallback_resource_direction_details": fallback_resource_details,
        "fallback_good": fallback_good,
        "params": {
            key: numeric.get(key)
            for key in (
                "seq_page_cost",
                "random_page_cost",
                "cpu_tuple_cost",
                "cpu_index_tuple_cost",
                "cpu_operator_cost",
                "parallel_setup_cost",
                "parallel_tuple_cost",
            )
            if numeric.get(key) is not None
        },
    }


def build_rough_good_points() -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "definition": {
            "db_qps": "candidate db_qps must be greater than the same coarse table's iteration-0 db_qps",
            "db_tps": "candidate db_xact_tps/db_tps must be greater than the same coarse table's iteration-0 DB TPS",
            "latency": (
                "latency_p90/p95/p99 must not degrade by more than "
                f"{LATENCY_SEVERE_DEGRADATION_PCT:.0f}% when present"
            ),
            "bottleneck_resource_direction": (
                "for cpu/memory/io bottleneck scenarios, the bottleneck resource must decrease "
                "and at least one non-bottleneck resource must increase relative to iteration 0"
            ),
            "fallback_direction": (
                "only after workload construction cannot produce an explainable point, choose the best "
                "DB-QPS-increasing fallback point whose bottleneck resource does not decrease and rises by at most "
                f"{FALLBACK_BOTTLENECK_MAX_INCREASE_RATIO * 100:.1f}% while all non-bottleneck resources increase"
            ),
            "no_bottleneck_resource_direction": (
                "for the no-bottleneck scenario, aggregate cpu+memory+io utilization must increase "
                "and at least two resource dimensions must increase"
            ),
            "io_resource": "io uses the io column when present, otherwise io_read + io_write",
        },
        "scenarios": {},
    }
    for bottleneck, benchmark, path in ROUGH_TABLE_SOURCES:
        text = _coarse_text(path.read_text(encoding="utf-8"))
        tables = _parse_markdown_iteration_tables(text)
        rows = tables[0] if tables else []
        baseline = next(
            (row for row in rows if (row.get("_numeric") or {}).get("iteration") == 0),
            rows[0] if rows else {},
        )
        evaluated = [
            _evaluate_good_point(row, baseline, bottleneck)
            for row in rows
            if row is not baseline and (row.get("_numeric") or {}).get("iteration") is not None
        ]
        strict_good = [item for item in evaluated if item.get("strict_good")]
        strict_good.sort(
            key=lambda item: (
                float(item.get("score") or float("-inf")),
                float(item.get("db_qps_ratio") or 0.0),
            ),
            reverse=True,
        )
        fallback_good = [item for item in evaluated if item.get("fallback_good")]
        fallback_good.sort(
            key=lambda item: (
                float(item.get("db_qps_ratio") or 0.0),
                float(item.get("score") or float("-inf")),
            ),
            reverse=True,
        )
        result["scenarios"][bottleneck] = {
            "benchmark": benchmark,
            "source": str(path),
            "resource_direction_policy": "bottleneck_down_any_other_up",
            "bottleneck_resource": bottleneck,
            "baseline": {
                "iteration": int((baseline.get("_numeric") or {}).get("iteration") or 0),
                "db_qps": (baseline.get("_numeric") or {}).get("db_qps"),
                "resources": {
                    name: _resource_value(baseline, name)
                    for name in RESOURCE_COLUMNS
                },
                "latency": {
                    key: (baseline.get("_numeric") or {}).get(key)
                    for key in ("latency_p90", "latency_p95", "latency_p99")
                },
            },
            "strict_good_count": len(strict_good),
            "strict_good_points": strict_good,
            "fallback_good_count": len(fallback_good),
            "fallback_good_points": fallback_good,
            "evaluated_points": evaluated,
        }
    result["scenarios"]["none"] = {
        "benchmark": "hybench_xp",
        "source": None,
        "resource_direction_policy": "overall_up",
        "bottleneck_resource": None,
        "strict_good_count": 0,
        "strict_good_points": [],
        "fallback_good_count": 0,
        "fallback_good_points": [],
        "note": "No result/bonect no-bottleneck rough table exists yet; this scenario must be established by the new run.",
    }
    return result


def _good_points_markdown(rough_good_points: Dict[str, Any]) -> List[str]:
    lines = [
        "## Structured Rough Good-Point Selection",
        "",
        (
            f"- latency severe-degradation threshold: `{LATENCY_SEVERE_DEGRADATION_PCT:.0f}%` "
            f"(ratio <= `{MAX_LATENCY_RATIO:.1f}`)"
        ),
        "- DB QPS and DB TPS must increase relative to the same coarse table's iteration 0.",
        "- Bottleneck scenarios require target resource down and at least one non-target resource up.",
        (
            "- Fallback is allowed only after workload-construction attempts fail: choose the best DB-QPS-increasing "
            "point with bottleneck resource not decreasing or rising only mildly, and all non-target resources up."
        ),
        "- No-bottleneck scenario requires aggregate CPU/Memory/IO utilization up and at least two resource dimensions up; no rough table exists yet.",
        "",
    ]
    scenarios = rough_good_points.get("scenarios") or {}
    for name in ("cpu", "memory", "io", "none"):
        scenario = scenarios.get(name) or {}
        lines.append(f"### {name}")
        lines.append("")
        lines.append(f"- benchmark: `{scenario.get('benchmark')}`")
        lines.append(f"- resource_direction_policy: `{scenario.get('resource_direction_policy')}`")
        if scenario.get("source"):
            lines.append(f"- source: `{scenario.get('source')}`")
        if scenario.get("note"):
            lines.append(f"- note: {scenario.get('note')}")
        lines.append(f"- strict_good_count: `{scenario.get('strict_good_count', 0)}`")
        for point in (scenario.get("strict_good_points") or [])[:5]:
            params = point.get("params") or {}
            lines.append(
                "- good point: "
                f"iteration `{point.get('iteration')}`, "
                f"score `{point.get('score')}`, "
                f"db_qps `{point.get('db_qps')}`, "
                f"db_tps `{point.get('db_tps')}`, "
                f"params `{json.dumps(params, ensure_ascii=False, sort_keys=True)}`"
            )
        lines.append(f"- fallback_good_count: `{scenario.get('fallback_good_count', 0)}`")
        for point in (scenario.get("fallback_good_points") or [])[:3]:
            params = point.get("params") or {}
            lines.append(
                "- fallback point: "
                f"iteration `{point.get('iteration')}`, "
                f"score `{point.get('score')}`, "
                f"db_qps `{point.get('db_qps')}`, "
                f"db_tps `{point.get('db_tps')}`, "
                f"params `{json.dumps(params, ensure_ascii=False, sort_keys=True)}`"
            )
        lines.append("")
    return lines


def write_reference(run_id: str, history_root: Path) -> Dict[str, Any]:
    sources = validate_sources()
    rough_good_points = build_rough_good_points()
    run_dir = history_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    out_md = run_dir / "bonect_reference_summary.md"
    out_json = run_dir / "bonect_reference_summary.json"

    reference_text = REFERENCE_DOC.read_text(encoding="utf-8")
    raw_dir = run_dir / "bonect_reference_sources"
    raw_dir.mkdir(parents=True, exist_ok=True)
    copied: List[str] = []
    for src in RESULT_SOURCES:
        dst = raw_dir / src.name
        shutil.copyfile(src, dst)
        copied.append(str(dst))

    header = [
        "# Bonect Reference Summary For Run",
        "",
        f"- run_id: `{run_id}`",
        f"- generated_at: `{datetime.now().isoformat(timespec='seconds')}`",
        f"- reference_doc: `{REFERENCE_DOC}`",
        "- copied_sources:",
    ]
    header.extend(f"  - `{path}`" for path in copied)
    header.extend(["", "---", ""])
    header.extend(_good_points_markdown(rough_good_points))
    header.extend(["", "---", ""])
    out_md.write_text("\n".join(header) + reference_text + "\n", encoding="utf-8")

    metadata = {
        "run_id": run_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "reference_doc": str(REFERENCE_DOC),
        "required": True,
        "sources": [source_meta(path) for path in sources],
        "copied_sources": copied,
        "rough_good_points": rough_good_points,
        "summary_md": str(out_md),
        "summary_json": str(out_json),
        "gate": {
            "must_exist_before_run": True,
            "supervisor_action_if_missing": "stop_and_build_bonect_reference",
        },
    }
    out_json.write_text(json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return metadata


def check_reference(run_id: str, history_root: Path) -> int:
    run_dir = history_root / run_id
    md = run_dir / "bonect_reference_summary.md"
    js = run_dir / "bonect_reference_summary.json"
    if md.exists() and js.exists():
        print(f"OK bonect reference exists: {md}", flush=True)
        return 0
    print(f"MISSING bonect reference for run_id={run_id}: {md} {js}", flush=True)
    return 2


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare result/bonect reference summary for one tuning run.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--history-root", default=str(DEFAULT_HISTORY_ROOT))
    parser.add_argument("--check", action="store_true", help="Only check whether the reference summary exists.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    history_root = Path(args.history_root)
    if args.check:
        return check_reference(args.run_id, history_root)
    metadata = write_reference(args.run_id, history_root)
    print(metadata["summary_md"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

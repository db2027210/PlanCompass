#!/usr/bin/env python3
"""Summarize pg_benchmark_bottleneck_matrix optimizer results."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


PARAM_KEYS = [
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
]
RESOURCE_KEYS = ["cpu", "memory", "io", "io_read", "io_write", "iops_read", "iops_write"]
BOTTLENECK_ORDER = {"cpu": 0, "memory": 1, "io": 2, "none": 3}


def to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_final_summary(path: Path) -> Optional[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("event") == "final_summary":
                return obj
    return None


def resource_names(values: Any) -> str:
    names: List[str] = []
    for item in values or []:
        if isinstance(item, dict):
            name = item.get("name") or item.get("resource") or item.get("key")
            if name is None:
                name = json.dumps(item, ensure_ascii=False, sort_keys=True)
        else:
            name = item
        text = str(name).strip()
        if text:
            names.append(text)
    return ",".join(names)


def parse_history_path(run_dir: Path, path: Path) -> Dict[str, str]:
    rel = path.relative_to(run_dir)
    bottleneck = rel.parts[0] if len(rel.parts) > 1 else ""
    name = path.name
    benchmark = name.split("_optimizer_history_", 1)[0]
    return {"bottleneck": bottleneck, "benchmark": benchmark}


def flatten(run_dir: Path, path: Path, summary: Dict[str, Any]) -> Dict[str, Any]:
    labels = parse_history_path(run_dir, path)
    initial_score = to_float(summary.get("initial_score"))
    final_score = to_float(summary.get("final_score"))
    row: Dict[str, Any] = {
        "run_id": run_dir.name,
        "bottleneck": labels["bottleneck"],
        "benchmark": labels["benchmark"],
        "source_file": str(path),
        "timestamp": summary.get("timestamp"),
        "iterations": summary.get("iterations"),
        "initial_score": initial_score,
        "final_score": final_score,
        "score_improvement": to_float(summary.get("score_improvement")),
        "score_improvement_pct": to_float(summary.get("score_improvement_pct")),
        "score_ratio": (final_score / initial_score) if initial_score not in (None, 0) and final_score is not None else None,
    }
    effect = summary.get("final_effect_evaluation") or {}
    row.update(
        {
            "target_achieved": effect.get("target_achieved"),
            "score_ok": effect.get("score_ok"),
            "degradation_ok": effect.get("degradation_ok"),
            "overloaded_resources": resource_names(effect.get("overloaded_resources")),
            "high_resources": resource_names(effect.get("high_resources")),
        }
    )
    degradation = summary.get("degradation_summary") or {}
    row.update(
        {
            "degraded_sql_count": degradation.get("degraded_count"),
            "max_degradation_pct": to_float(degradation.get("max_degradation_pct")),
            "avg_degradation_pct": to_float(degradation.get("avg_degradation_pct")),
        }
    )
    for prefix in ["initial_resource", "final_resource", "initial_utilization", "final_utilization"]:
        values = summary.get(prefix) or {}
        for key in RESOURCE_KEYS:
            row[f"{prefix}_{key}"] = to_float(values.get(key))
    initial_params = summary.get("initial_params") or {}
    final_params = summary.get("optimized_params") or {}
    changes = summary.get("param_changes") or {}
    for key in PARAM_KEYS:
        row[f"{key}_initial"] = to_float(initial_params.get(key))
        row[f"{key}_final"] = to_float(final_params.get(key))
        row[f"{key}_change_pct"] = to_float((changes.get(key) or {}).get("change_pct"))
    return row


def mean(values: Iterable[Optional[float]]) -> Optional[float]:
    nums = [value for value in values if value is not None]
    if not nums:
        return None
    return statistics.fmean(nums)


def summarize_group(rows: List[Dict[str, Any]], key: str) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key, ""))].append(row)
    out: Dict[str, Dict[str, Any]] = {}
    for name, group_rows in sorted(grouped.items()):
        out[name] = {
            "count": len(group_rows),
            "avg_initial_score": mean(to_float(row.get("initial_score")) for row in group_rows),
            "avg_final_score": mean(to_float(row.get("final_score")) for row in group_rows),
            "avg_score_improvement_pct": mean(to_float(row.get("score_improvement_pct")) for row in group_rows),
            "target_achieved_count": sum(1 for row in group_rows if str(row.get("target_achieved")).lower() == "true"),
            "avg_final_cpu_util": mean(to_float(row.get("final_utilization_cpu")) for row in group_rows),
            "avg_final_memory_util": mean(to_float(row.get("final_utilization_memory")) for row in group_rows),
            "avg_final_io_util": mean(to_float(row.get("final_utilization_io")) for row in group_rows),
        }
    return out


def fmt(value: Any, digits: int = 4) -> str:
    number = to_float(value)
    if number is None:
        return ""
    if abs(number) >= 100:
        return f"{number:.2f}"
    if abs(number) >= 1:
        return f"{number:.4f}"
    return f"{number:.6g}"


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    fieldnames: List[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(path: Path, rows: List[Dict[str, Any]], aggregate: Dict[str, Any]) -> None:
    lines = [
        f"# PostgreSQL Benchmark Bottleneck Matrix Summary",
        "",
        f"- run_id: `{aggregate['run_id']}`",
        f"- completed_specs: `{len(rows)}`",
        f"- output_csv: `{aggregate['matrix_csv']}`",
        "",
        "## Matrix Results",
        "",
        "| bottleneck | benchmark | initial_score | final_score | improvement_pct | iterations | target_achieved | final_cpu | final_mem | final_io |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in sorted(rows, key=lambda item: (str(item["bottleneck"]), str(item["benchmark"]))):
        lines.append(
            "| {bottleneck} | {benchmark} | {initial_score} | {final_score} | {improvement} | {iterations} | {target} | {cpu} | {mem} | {io} |".format(
                bottleneck=row.get("bottleneck", ""),
                benchmark=row.get("benchmark", ""),
                initial_score=fmt(row.get("initial_score")),
                final_score=fmt(row.get("final_score")),
                improvement=fmt(row.get("score_improvement_pct")),
                iterations=row.get("iterations", ""),
                target=row.get("target_achieved", ""),
                cpu=fmt(row.get("final_utilization_cpu")),
                mem=fmt(row.get("final_utilization_memory")),
                io=fmt(row.get("final_utilization_io")),
            )
        )
    lines.extend(["", "## Aggregate By Bottleneck", ""])
    lines.append("| bottleneck | count | avg_improvement_pct | target_achieved | avg_final_cpu | avg_final_mem | avg_final_io |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for name, item in aggregate["by_bottleneck"].items():
        lines.append(
            f"| {name} | {item['count']} | {fmt(item['avg_score_improvement_pct'])} | "
            f"{item['target_achieved_count']} | {fmt(item['avg_final_cpu_util'])} | "
            f"{fmt(item['avg_final_memory_util'])} | {fmt(item['avg_final_io_util'])} |"
        )
    lines.extend(["", "## Aggregate By Benchmark", ""])
    lines.append("| benchmark | count | avg_improvement_pct | target_achieved | avg_final_cpu | avg_final_mem | avg_final_io |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for name, item in aggregate["by_benchmark"].items():
        lines.append(
            f"| {name} | {item['count']} | {fmt(item['avg_score_improvement_pct'])} | "
            f"{item['target_achieved_count']} | {fmt(item['avg_final_cpu_util'])} | "
            f"{fmt(item['avg_final_memory_util'])} | {fmt(item['avg_final_io_util'])} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def best_row(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    ranked = [
        row for row in rows
        if to_float(row.get("score_improvement_pct")) is not None
    ]
    if not ranked:
        return None
    return max(ranked, key=lambda row: float(row["score_improvement_pct"]))


def sort_bottleneck_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(rows, key=lambda row: (BOTTLENECK_ORDER.get(str(row.get("bottleneck")), 99), str(row.get("bottleneck"))))


def write_benchmark_markdown(path: Path, benchmark: str, rows: List[Dict[str, Any]], run_id: str) -> None:
    ordered_rows = sort_bottleneck_rows(rows)
    winner = best_row(ordered_rows)
    lines = [
        f"# {benchmark} Summary",
        "",
        "## Overall",
        "",
        f"- run_id: `{run_id}`",
        f"- benchmark: `{benchmark}`",
        f"- bottleneck_specs: `{len(ordered_rows)}`",
        f"- best_bottleneck_by_score: `{winner.get('bottleneck') if winner else ''}`",
        f"- best_score_improvement_pct: `{fmt(winner.get('score_improvement_pct')) if winner else ''}`",
        "",
        "## Bottleneck Results",
        "",
        "| bottleneck | initial_score | final_score | improvement_pct | iterations | target_achieved | final_cpu | final_memory | final_io |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in ordered_rows:
        lines.append(
            f"| {row.get('bottleneck', '')} | {fmt(row.get('initial_score'))} | "
            f"{fmt(row.get('final_score'))} | {fmt(row.get('score_improvement_pct'))} | "
            f"{row.get('iterations', '')} | {row.get('target_achieved', '')} | "
            f"{fmt(row.get('final_utilization_cpu'))} | {fmt(row.get('final_utilization_memory'))} | "
            f"{fmt(row.get('final_utilization_io'))} |"
        )

    lines.extend(["", "## Final Parameters", ""])
    lines.append("| bottleneck | seq_page_cost | random_page_cost | cpu_tuple_cost | cpu_index_tuple_cost | cpu_operator_cost | parallel_setup_cost | parallel_tuple_cost |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for row in ordered_rows:
        lines.append(
            f"| {row.get('bottleneck', '')} | {fmt(row.get('seq_page_cost_final'))} | "
            f"{fmt(row.get('random_page_cost_final'))} | {fmt(row.get('cpu_tuple_cost_final'))} | "
            f"{fmt(row.get('cpu_index_tuple_cost_final'))} | {fmt(row.get('cpu_operator_cost_final'))} | "
            f"{fmt(row.get('parallel_setup_cost_final'))} | {fmt(row.get('parallel_tuple_cost_final'))} |"
        )

    lines.extend(["", "## Parameter Change Pct", ""])
    lines.append("| bottleneck | seq_page_cost | random_page_cost | cpu_tuple_cost | cpu_index_tuple_cost | cpu_operator_cost | parallel_setup_cost | parallel_tuple_cost |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for row in ordered_rows:
        lines.append(
            f"| {row.get('bottleneck', '')} | {fmt(row.get('seq_page_cost_change_pct'))} | "
            f"{fmt(row.get('random_page_cost_change_pct'))} | {fmt(row.get('cpu_tuple_cost_change_pct'))} | "
            f"{fmt(row.get('cpu_index_tuple_cost_change_pct'))} | {fmt(row.get('cpu_operator_cost_change_pct'))} | "
            f"{fmt(row.get('parallel_setup_cost_change_pct'))} | {fmt(row.get('parallel_tuple_cost_change_pct'))} |"
        )

    lines.extend(["", "## Notes", ""])
    if all(to_float(row.get("final_utilization_memory")) in (None, 0.0) for row in ordered_rows):
        lines.append("- Memory utilization is zero in all rows; interpret this as estimator output, not proof that memory was unused at runtime.")
    zero_improvements = [str(row.get("bottleneck")) for row in ordered_rows if (to_float(row.get("score_improvement_pct")) or 0.0) == 0.0]
    if zero_improvements:
        lines.append(f"- Zero score improvement for: `{', '.join(zero_improvements)}`.")
    if winner:
        lines.append(
            f"- The largest score movement is under `{winner.get('bottleneck')}` with improvement `{fmt(winner.get('score_improvement_pct'))}%`."
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_benchmark_artifacts(output_dir: Path, rows: List[Dict[str, Any]], run_id: str) -> Dict[str, Dict[str, str]]:
    benchmark_dir = output_dir / "benchmarks"
    benchmark_dir.mkdir(parents=True, exist_ok=True)
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("benchmark", ""))].append(row)

    artifacts: Dict[str, Dict[str, str]] = {}
    for benchmark, benchmark_rows in sorted(grouped.items()):
        ordered_rows = sort_bottleneck_rows(benchmark_rows)
        csv_path = benchmark_dir / f"{benchmark}.csv"
        json_path = benchmark_dir / f"{benchmark}.json"
        md_path = benchmark_dir / f"{benchmark}.md"
        write_csv(csv_path, ordered_rows)
        json_path.write_text(json.dumps({"benchmark": benchmark, "rows": ordered_rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_benchmark_markdown(md_path, benchmark, ordered_rows, run_id)
        artifacts[benchmark] = {
            "csv": str(csv_path),
            "json": str(json_path),
            "markdown": str(md_path),
        }
    return artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize pg_benchmark_bottleneck_matrix optimizer histories.")
    parser.add_argument("run_dir", help="Run directory under history/pg_benchmark_bottleneck_matrix")
    parser.add_argument("--output-dir", default="", help="Output directory. Default: <run_dir>/summary")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    output_dir = Path(args.output_dir).resolve() if args.output_dir else run_dir / "summary"
    output_dir.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, Any]] = []
    missing: List[str] = []
    for path in sorted(run_dir.glob("*/*_optimizer_history_*.jsonl")):
        summary = load_final_summary(path)
        if summary is None:
            missing.append(str(path))
            continue
        rows.append(flatten(run_dir, path, summary))

    csv_path = output_dir / "matrix_summary.csv"
    json_path = output_dir / "matrix_summary.json"
    md_path = output_dir / "matrix_summary.md"
    benchmark_artifacts = write_benchmark_artifacts(output_dir, rows, run_dir.name)
    write_csv(csv_path, rows)
    aggregate = {
        "run_id": run_dir.name,
        "run_dir": str(run_dir),
        "matrix_csv": str(csv_path),
        "matrix_markdown": str(md_path),
        "completed_specs": len(rows),
        "missing_final_summary": missing,
        "benchmark_artifacts": benchmark_artifacts,
        "by_bottleneck": summarize_group(rows, "bottleneck"),
        "by_benchmark": summarize_group(rows, "benchmark"),
    }
    json_path.write_text(json.dumps({"rows": rows, "aggregate": aggregate}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(md_path, rows, aggregate)
    print(f"completed_specs={len(rows)}")
    print(f"matrix_csv={csv_path}")
    print(f"matrix_json={json_path}")
    print(f"matrix_markdown={md_path}")
    print(f"benchmark_summary_dir={output_dir / 'benchmarks'}")
    if missing:
        print(f"missing_final_summary={len(missing)}")


if __name__ == "__main__":
    main()

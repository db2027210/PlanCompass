#!/usr/bin/env python3

from __future__ import annotations

import argparse
import ast
import csv
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


PARAM_KEYS = [
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
]

RESOURCE_KEYS = ["cpu", "memory", "io_read", "io_write", "iops_read", "iops_write"]
SUMMARY_TITLE = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=" summary optimization_history JSONL  run ")
    parser.add_argument("input_file", help=" history/optimization_history_*.jsonl  history/run_*.log")
    parser.add_argument("--output-json", default="", help=" JSON ")
    parser.add_argument("--output-csv", default="", help=" CSV ")
    return parser.parse_args()


def to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_final_summary_from_jsonl(path: Path) -> Optional[Dict[str, Any]]:
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


def extract_block_after_title(text: str) -> str:
    idx = text.find(SUMMARY_TITLE)
    if idx < 0:
        return ""
    return text[idx:]


def parse_dict_line(block: str, label: str) -> Optional[Dict[str, Any]]:
    pattern = re.compile(rf"{re.escape(label)}:\s*(\{{.*?\}})")
    match = pattern.search(block)
    if not match:
        return None
    try:
        value = ast.literal_eval(match.group(1))
    except (SyntaxError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def parse_scalar_line(block: str, label: str) -> Optional[float]:
    pattern = re.compile(rf"{re.escape(label)}:\s*([+\-]?[0-9.]+)")
    match = pattern.search(block)
    if not match:
        return None
    return to_float(match.group(1))


def parse_int_line(block: str, label: str) -> Optional[int]:
    pattern = re.compile(rf"{re.escape(label)}:\s*([0-9]+)")
    match = pattern.search(block)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def parse_degradation_summary_from_block(block: str) -> Optional[Dict[str, Any]]:
    if "Top SQL regression statistics" not in block:
        return None

    summary: Dict[str, Any] = {
        "degraded_count": parse_int_line(block, "SQL"),
        "new_in_final_top_count": parse_int_line(block, "TopSQL"),
        "max_degradation_pct": parse_scalar_line(block, ""),
        "avg_degradation_pct": parse_scalar_line(block, ""),
        "degraded_sql": [],
    }

    sql_lines = block.splitlines()
    degraded_sql: List[Dict[str, Any]] = []
    i = 0
    while i < len(sql_lines):
        line = sql_lines[i]
        item_match = re.match(
            r"\s*\d+\.\s+queryid=(?P<queryid>\S+)\s+\|\s+=(?P<pct>[+\-]?[0-9.]+)%\s+\|\s+baseline=(?P<baseline>[0-9.]+)ms\s+\|\s+final=(?P<final>[0-9.]+)ms",
            line,
        )
        if not item_match:
            i += 1
            continue
        item = {
            "queryid": item_match.group("queryid"),
            "degradation_pct": to_float(item_match.group("pct")),
            "baseline_mean_ms": to_float(item_match.group("baseline")),
            "final_mean_ms": to_float(item_match.group("final")),
            "query": "",
        }
        if i + 1 < len(sql_lines):
            sql_match = re.match(r"\s*SQL:\s*(.*)", sql_lines[i + 1].strip())
            if sql_match:
                item["query"] = sql_match.group(1).strip()
                i += 1
        degraded_sql.append(item)
        i += 1

    summary["degraded_sql"] = degraded_sql
    return summary


def build_param_change_summary(
    initial_params: Optional[Dict[str, Any]],
    optimized_params: Optional[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(initial_params, dict) or not isinstance(optimized_params, dict):
        return out
    for key in PARAM_KEYS:
        initial = to_float(initial_params.get(key))
        final = to_float(optimized_params.get(key))
        if initial is None or final is None:
            continue
        change = final - initial
        change_pct = (change / initial * 100.0) if initial != 0 else None
        out[key] = {
            "initial": initial,
            "final": final,
            "change": change,
            "change_pct": change_pct,
        }
    return out


def parse_summary_from_run_log(path: Path) -> Optional[Dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    block = extract_block_after_title(text)
    if not block:
        return None

    initial_params = parse_dict_line(block, "")
    optimized_params = parse_dict_line(block, "")
    initial_resource = parse_dict_line(block, "")
    final_resource = parse_dict_line(block, "")
    initial_utilization = parse_dict_line(block, "")
    final_utilization = parse_dict_line(block, "")

    summary: Dict[str, Any] = {
        "event": "final_summary",
        "initial_params": initial_params,
        "optimized_params": optimized_params,
        "param_changes": build_param_change_summary(initial_params, optimized_params),
        "initial_resource": initial_resource,
        "final_resource": final_resource,
        "initial_utilization": initial_utilization,
        "final_utilization": final_utilization,
        "degradation_summary": parse_degradation_summary_from_block(block),
        "initial_score": parse_scalar_line(block, ""),
        "final_score": parse_scalar_line(block, ""),
        "score_improvement": parse_scalar_line(block, ""),
        "score_improvement_pct": parse_scalar_line(block, ""),
        "iterations": parse_int_line(block, ""),
    }
    return summary


def flatten_summary(summary: Dict[str, Any], source_file: Path) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "source_file": str(source_file),
        "initial_score": to_float(summary.get("initial_score")),
        "final_score": to_float(summary.get("final_score")),
        "score_improvement": to_float(summary.get("score_improvement")),
        "score_improvement_pct": to_float(summary.get("score_improvement_pct")),
        "iterations": summary.get("iterations"),
    }

    degradation_summary = summary.get("degradation_summary") or {}
    row["degraded_sql_count"] = degradation_summary.get("degraded_count")
    row["new_in_final_top_count"] = degradation_summary.get("new_in_final_top_count")
    row["max_degradation_pct"] = to_float(degradation_summary.get("max_degradation_pct"))
    row["avg_degradation_pct"] = to_float(degradation_summary.get("avg_degradation_pct"))

    initial_params = summary.get("initial_params") or {}
    optimized_params = summary.get("optimized_params") or {}
    param_changes = summary.get("param_changes") or {}
    for key in PARAM_KEYS:
        row[f"{key}_initial"] = to_float(initial_params.get(key))
        row[f"{key}_final"] = to_float(optimized_params.get(key))
        change_info = param_changes.get(key) or {}
        row[f"{key}_change"] = to_float(change_info.get("change"))
        row[f"{key}_change_pct"] = to_float(change_info.get("change_pct"))

    for prefix in ["initial_resource", "final_resource", "initial_utilization", "final_utilization"]:
        values = summary.get(prefix) or {}
        for key in RESOURCE_KEYS:
            row[f"{prefix}_{key}"] = to_float(values.get(key))

    degraded_sql = degradation_summary.get("degraded_sql") or []
    for idx, item in enumerate(degraded_sql[:10], start=1):
        row[f"degraded_sql_{idx}_queryid"] = item.get("queryid")
        row[f"degraded_sql_{idx}_degradation_pct"] = to_float(item.get("degradation_pct"))
        row[f"degraded_sql_{idx}_baseline_mean_ms"] = to_float(item.get("baseline_mean_ms"))
        row[f"degraded_sql_{idx}_final_mean_ms"] = to_float(item.get("final_mean_ms"))
        row[f"degraded_sql_{idx}_query"] = item.get("query")
    return row


def write_json(path: Path, summary: Dict[str, Any]) -> None:
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, row: Dict[str, Any]) -> None:
    fieldnames = list(row.keys())
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(row)


def print_summary(summary: Dict[str, Any]) -> None:
    initial_score = to_float(summary.get("initial_score"))
    final_score = to_float(summary.get("final_score"))
    score_improvement_pct = to_float(summary.get("score_improvement_pct"))
    iterations = summary.get("iterations")
    degradation_summary = summary.get("degradation_summary") or {}
    degraded_count = degradation_summary.get("degraded_count")
    max_deg = to_float(degradation_summary.get("max_degradation_pct"))

    print(f": {initial_score}")
    print(f": {final_score}")
    print(f": {score_improvement_pct}")
    print(f": {iterations}")
    print(f"SQL: {degraded_count}")
    print(f": {max_deg}")


def main() -> None:
    args = parse_args()
    input_path = Path(args.input_file)

    summary = load_final_summary_from_jsonl(input_path)
    if summary is None:
        summary = parse_summary_from_run_log(input_path)
    if summary is None:
        raise SystemExit(f" {input_path}  final summary")

    row = flatten_summary(summary, input_path)

    if args.output_json:
        write_json(Path(args.output_json), summary)
    if args.output_csv:
        write_csv(Path(args.output_csv), row)

    print_summary(summary)


if __name__ == "__main__":
    main()

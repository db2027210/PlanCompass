#!/usr/bin/env python3
"""Export per-case result artifacts for PostgreSQL benchmark matrix runs."""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT_DIR = Path(__file__).resolve().parents[1]
SHELL_DIR = ROOT_DIR / "shell"
if str(SHELL_DIR) not in sys.path:
    sys.path.insert(0, str(SHELL_DIR))

from extract_final_summary import (  # noqa: E402
    flatten_summary,
    load_final_summary_from_jsonl,
    write_csv as write_final_csv,
    write_json as write_final_json,
)
from extract_iteration_metrics import (  # noqa: E402
    build_rows,
    write_csv as write_iter_csv,
    write_json as write_iter_json,
)


BOTTLENECK_ORDER = {"cpu": 0, "memory": 1, "io": 2, "none": 3}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export final/iteration/plan summaries for every matrix case.")
    parser.add_argument("run_dir", help="Run directory, e.g. history/pg_benchmark_bottleneck_matrix/<run_id>")
    parser.add_argument(
        "--output-dir",
        default="",
        help="Output directory. Default: result/pg_benchmark_bottleneck_matrix/<run_id>/case_files",
    )
    parser.add_argument(
        "--flat",
        action="store_true",
        help="Write all case files directly under output-dir instead of output-dir/<bottleneck>/<benchmark>.",
    )
    parser.add_argument(
        "--fail-on-incomplete",
        action="store_true",
        help="Fail when an optimizer history has no final_summary. Default skips incomplete running cases.",
    )
    return parser.parse_args()


def case_labels(run_dir: Path, history_path: Path) -> Dict[str, str]:
    rel = history_path.relative_to(run_dir)
    bottleneck = rel.parts[0] if len(rel.parts) > 1 else "unknown"
    benchmark = history_path.name.split("_optimizer_history_", 1)[0]
    return {"bottleneck": bottleneck, "benchmark": benchmark}


def sanitize(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9_]+", "_", value)
    return value.strip("_") or "unknown"


def iter_history_files(run_dir: Path) -> List[Path]:
    return sorted(
        run_dir.glob("*/*_optimizer_history_*.jsonl"),
        key=lambda path: (
            BOTTLENECK_ORDER.get(case_labels(run_dir, path)["bottleneck"], 99),
            case_labels(run_dir, path)["benchmark"],
            path.name,
        ),
    )


def collect_plan_capture_diagnostics(history_path: Path) -> Dict[str, Any]:
    capture_dir = history_path.parent / f"{history_path.name.split('_optimizer_history_', 1)[0]}_plan_captures"
    files = sorted(capture_dir.glob("*.json")) if capture_dir.exists() else []
    item_counts: List[int] = []
    plan_hashes = set()
    query_samples: List[str] = []
    for path in files:
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        items = obj.get("items") or []
        if not isinstance(items, list):
            continue
        item_counts.append(len(items))
        for item in items:
            if not isinstance(item, dict):
                continue
            plan_hash = item.get("plan_hash") or item.get("normalized_plan_hash")
            if plan_hash:
                plan_hashes.add(str(plan_hash))
            query = str(item.get("query") or "").strip().replace("\n", " ")
            if query and query not in query_samples:
                query_samples.append(query[:180])

    return {
        "plan_capture_dir": str(capture_dir),
        "plan_capture_files": len(files),
        "plan_capture_item_counts": sorted(set(item_counts)),
        "plan_capture_total_items": sum(item_counts),
        "plan_capture_hash_count": len(plan_hashes),
        "plan_capture_query_samples": query_samples[:5],
    }


def is_empty_plan_summary(path: Path) -> bool:
    if not path.exists():
        return True
    text = path.read_text(encoding="utf-8", errors="replace")
    return "node_type_transition_summary" in text and "structural_node_type_change_summary" in text and text.count("(empty)") >= 2


def prepend_plan_diagnostics(output_path: Path, diagnostics: Dict[str, Any]) -> None:
    existing = output_path.read_text(encoding="utf-8", errors="replace") if output_path.exists() else ""
    query_samples = diagnostics.get("plan_capture_query_samples") or []
    lines = [
        "plan_summary_diagnostic",
        "status: empty plan-change summary",
        "reason: no node-type or structural plan changes were recorded in optimizer history.",
        f"plan_capture_files: {diagnostics.get('plan_capture_files')}",
        f"plan_capture_item_counts: {diagnostics.get('plan_capture_item_counts')}",
        f"plan_capture_total_items: {diagnostics.get('plan_capture_total_items')}",
        f"plan_capture_hash_count: {diagnostics.get('plan_capture_hash_count')}",
    ]
    if query_samples:
        lines.append("plan_capture_query_samples:")
        lines.extend(f"- {query}" for query in query_samples)
    lines.extend(["", existing])
    output_path.write_text("\n".join(lines), encoding="utf-8")


def write_plan_summary(log_path: Path, output_path: Path, diagnostics: Dict[str, Any]) -> bool:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(SHELL_DIR / "find_changed_sql_plan_diff.py"),
        str(log_path),
        "--node-type-only",
        "--summary-output-text",
        str(output_path),
    ]
    completed = subprocess.run(command, cwd=str(ROOT_DIR), text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        output_path.write_text(
            "plan_summary_error\n"
            f"returncode: {completed.returncode}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}\n",
            encoding="utf-8",
        )
        return False
    if not output_path.exists():
        output_path.write_text(completed.stdout or "No matching plan-change SQL found.\n", encoding="utf-8")
    if is_empty_plan_summary(output_path):
        prepend_plan_diagnostics(output_path, diagnostics)
    return True


def write_manifest(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    json_path = path.with_suffix(".json")
    csv_path = path.with_suffix(".csv")
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fieldnames: List[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with csv_path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def output_base(output_dir: Path, labels: Dict[str, str], flat: bool) -> Path:
    if flat:
        return output_dir
    return output_dir / labels["bottleneck"] / labels["benchmark"]


def export_case(run_dir: Path, history_path: Path, output_dir: Path, flat: bool) -> Dict[str, Any]:
    labels = case_labels(run_dir, history_path)
    case_id = f"{sanitize(labels['bottleneck'])}_{sanitize(labels['benchmark'])}"
    case_dir = output_base(output_dir, labels, flat)
    case_dir.mkdir(parents=True, exist_ok=True)

    final_json = case_dir / f"final_summary_{case_id}.json"
    final_csv = case_dir / f"final_summary_{case_id}.csv"
    iter_json = case_dir / f"iter_metrics_{case_id}.json"
    iter_csv = case_dir / f"iter_metrics_{case_id}.csv"
    plan_txt = case_dir / f"plan_summary_only_{case_id}.txt"

    summary = load_final_summary_from_jsonl(history_path)
    if summary is None:
        raise RuntimeError(f"final_summary not found: {history_path}")
    write_final_json(final_json, summary)
    write_final_csv(final_csv, flatten_summary(summary, history_path))

    iter_rows = build_rows(history_path, query_log_csv=None, run_log_path=None, db_monitor_csv=None)
    write_iter_json(iter_json, iter_rows)
    write_iter_csv(iter_csv, iter_rows)
    plan_diagnostics = collect_plan_capture_diagnostics(history_path)
    plan_ok = write_plan_summary(history_path, plan_txt, plan_diagnostics)

    return {
        "case_id": case_id,
        "bottleneck": labels["bottleneck"],
        "benchmark": labels["benchmark"],
        "source_file": str(history_path),
        "final_summary_json": str(final_json),
        "final_summary_csv": str(final_csv),
        "iter_metrics_json": str(iter_json),
        "iter_metrics_csv": str(iter_csv),
        "plan_summary_only_txt": str(plan_txt),
        "iteration_rows": len(iter_rows),
        "plan_summary_ok": plan_ok,
        **plan_diagnostics,
    }


def main() -> None:
    args = parse_args()
    run_dir = Path(args.run_dir).resolve()
    if not run_dir.exists():
        raise FileNotFoundError(f"run_dir not found: {run_dir}")
    output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else ROOT_DIR / "result" / "pg_benchmark_bottleneck_matrix" / run_dir.name / "case_files"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    for history_path in iter_history_files(run_dir):
        try:
            row = export_case(run_dir, history_path, output_dir, args.flat)
        except RuntimeError as exc:
            if args.fail_on_incomplete or "final_summary not found" not in str(exc):
                raise
            labels = case_labels(run_dir, history_path)
            skipped_row = {
                "bottleneck": labels["bottleneck"],
                "benchmark": labels["benchmark"],
                "source_file": str(history_path),
                "reason": str(exc),
            }
            skipped.append(skipped_row)
            print(
                f"skip incomplete {labels['bottleneck']}/{labels['benchmark']}: {exc}",
                flush=True,
            )
            continue
        rows.append(row)
        print(f"exported {row['case_id']} -> {Path(row['final_summary_csv']).parent}", flush=True)

    write_manifest(output_dir / "case_manifest", rows)
    if skipped:
        write_manifest(output_dir / "case_manifest_skipped", skipped)
    print(f"exported_cases={len(rows)}", flush=True)
    print(f"skipped_incomplete={len(skipped)}", flush=True)
    print(f"output_dir={output_dir}", flush=True)
    print(f"manifest_csv={output_dir / 'case_manifest.csv'}", flush=True)
    print(f"manifest_json={output_dir / 'case_manifest.json'}", flush=True)


if __name__ == "__main__":
    main()

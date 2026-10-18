#!/usr/bin/env python3
"""Extract accepted optimizer iterations from PostgreSQL matrix JSONL logs."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


BOTTLENECK_ORDER = {"cpu": 0, "memory": 1, "io": 2, "none": 3}
PARAM_KEYS = [
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract accepted/rejected parameter-iteration rounds from optimizer JSONL files. "
            "Input can be a run directory or one or more *_optimizer_history_*.jsonl files."
        )
    )
    parser.add_argument("inputs", nargs="+", help="Run directories or optimizer_history JSONL files.")
    parser.add_argument(
        "--output-dir",
        default="",
        help=(
            "Output directory. Default: result/pg_benchmark_bottleneck_matrix/<run_id>/accepted_iterations "
            "when input is a run dir, otherwise ./accepted_iterations"
        ),
    )
    parser.add_argument("--stdout", action="store_true", help="Also print the Markdown summary to stdout.")
    parser.add_argument(
        "--fail-on-empty",
        action="store_true",
        help="Exit non-zero if no optimizer iteration rows are found.",
    )
    return parser.parse_args()


def to_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def discover_history_files(inputs: List[str]) -> Tuple[List[Path], Optional[Path]]:
    files: List[Path] = []
    run_dir: Optional[Path] = None
    for raw in inputs:
        path = Path(raw).expanduser().resolve()
        if path.is_dir():
            if run_dir is None:
                run_dir = path
            files.extend(sorted(path.glob("*/*_optimizer_history_*.jsonl")))
        elif path.is_file():
            files.append(path)
        else:
            raise FileNotFoundError(str(path))
    unique = sorted(
        {path.resolve() for path in files},
        key=lambda path: (
            case_labels(run_dir, path).get("bottleneck_order", 99),
            case_labels(run_dir, path).get("benchmark", ""),
            str(path),
        ),
    )
    return unique, run_dir


def case_labels(run_dir: Optional[Path], history_path: Path) -> Dict[str, Any]:
    bottleneck = "unknown"
    benchmark = history_path.name.split("_optimizer_history_", 1)[0]
    if run_dir is not None:
        try:
            rel = history_path.relative_to(run_dir)
            if len(rel.parts) > 1:
                bottleneck = rel.parts[0]
        except ValueError:
            pass
    else:
        parent = history_path.parent.name
        if parent in BOTTLENECK_ORDER:
            bottleneck = parent
    return {
        "run_id": run_dir.name if run_dir is not None else "",
        "bottleneck": bottleneck,
        "benchmark": benchmark,
        "case_id": f"{bottleneck}_{benchmark}",
        "bottleneck_order": BOTTLENECK_ORDER.get(bottleneck, 99),
    }


def read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fp:
        for line_no, line in enumerate(fp, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                obj["_line_no"] = line_no
                yield obj


def compact_ranges(values: List[int]) -> str:
    if not values:
        return ""
    ordered = sorted(set(values))
    ranges: List[str] = []
    start = prev = ordered[0]
    for value in ordered[1:]:
        if value == prev + 1:
            prev = value
            continue
        ranges.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = value
    ranges.append(str(start) if start == prev else f"{start}-{prev}")
    return ",".join(ranges)


def final_param_changes(start_params: Dict[str, Any], final_params: Dict[str, Any]) -> Dict[str, Any]:
    changes: Dict[str, Any] = {}
    for key in PARAM_KEYS:
        before = to_float(start_params.get(key))
        after = to_float(final_params.get(key))
        if before is None or after is None:
            continue
        changes[key] = {
            "initial": before,
            "final": after,
            "delta": after - before,
            "changed": abs(after - before) > 1e-12,
        }
    return changes


def extract_file(path: Path, run_dir: Optional[Path]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    labels = case_labels(run_dir, path)
    detail_rows: List[Dict[str, Any]] = []
    loop_meta: Dict[int, Dict[str, Any]] = {}
    current_loop: Optional[int] = None
    fallback_loop = -1
    previous_iteration: Optional[int] = None

    for obj in read_jsonl(path):
        event = obj.get("event")
        if event == "gradient_optimization_round_start":
            raw_round = obj.get("round_index")
            current_loop = int(raw_round) if isinstance(raw_round, int) else (current_loop or 0)
            loop_meta[current_loop] = {
                "round_index": current_loop,
                "timestamp": obj.get("timestamp"),
                "capability": obj.get("capability") or {},
                "target_bottleneck_resources": obj.get("target_bottleneck_resources") or [],
                "start_params": obj.get("start_params") or {},
                "candidate_params": obj.get("candidate_params") or {},
            }
            previous_iteration = None
            continue

        iteration = obj.get("iteration")
        if not isinstance(iteration, int) or "accepted" not in obj:
            continue
        if current_loop is None:
            current_loop = 0
            loop_meta.setdefault(current_loop, {"round_index": current_loop})
        elif previous_iteration is not None and iteration <= previous_iteration:
            # Compatibility with older logs that lack gradient_optimization_round_start.
            fallback_loop = max(fallback_loop, current_loop) + 1
            current_loop = fallback_loop
            loop_meta.setdefault(current_loop, {"round_index": current_loop})
        previous_iteration = iteration

        params = obj.get("params") or {}
        candidate_params = obj.get("candidate_params") or {}
        row = {
            "run_id": labels["run_id"],
            "case_id": labels["case_id"],
            "bottleneck": labels["bottleneck"],
            "benchmark": labels["benchmark"],
            "source_file": str(path),
            "line_no": obj.get("_line_no"),
            "loop_index": current_loop,
            "iteration": iteration,
            "accepted": bool(obj.get("accepted")),
            "accept_reason": obj.get("accept_reason"),
            "score": to_float(obj.get("score")),
            "candidate_score": to_float(obj.get("candidate_score")),
            "best_score": to_float(obj.get("best_score")),
            "eta": to_float(obj.get("eta")),
            "changed_param_count": obj.get("changed_param_count"),
            "clipped_param_count": obj.get("clipped_param_count"),
            "no_improve_cnt": obj.get("no_improve_cnt"),
            "consecutive_reject_streak": obj.get("consecutive_reject_streak"),
            "no_plan_change_streak": obj.get("no_plan_change_streak"),
        }
        for key in PARAM_KEYS:
            row[f"param_{key}"] = to_float(params.get(key))
            row[f"candidate_{key}"] = to_float(candidate_params.get(key))
        detail_rows.append(row)

    summary_rows: List[Dict[str, Any]] = []
    grouped: Dict[int, List[Dict[str, Any]]] = {}
    for row in detail_rows:
        grouped.setdefault(int(row["loop_index"]), []).append(row)
    for loop_index, rows in sorted(grouped.items()):
        accepted = [int(row["iteration"]) for row in rows if row.get("accepted")]
        rejected = [int(row["iteration"]) for row in rows if not row.get("accepted")]
        first = rows[0]
        last = rows[-1]
        meta = loop_meta.get(loop_index, {})
        start_params = meta.get("start_params") or {}
        accepted_rows = [row for row in rows if row.get("accepted")]
        final_param_row = accepted_rows[-1] if accepted_rows else last
        final_params = {
            key: final_param_row.get(f"param_{key}")
            for key in PARAM_KEYS
            if final_param_row.get(f"param_{key}") is not None
        }
        changes = final_param_changes(start_params, final_params)
        changed_params = [key for key, change in changes.items() if change.get("changed")]
        summary_rows.append(
            {
                "run_id": labels["run_id"],
                "case_id": labels["case_id"],
                "bottleneck": labels["bottleneck"],
                "benchmark": labels["benchmark"],
                "source_file": str(path),
                "loop_index": loop_index,
                "loop_timestamp": meta.get("timestamp"),
                "target_bottleneck_resources": ",".join(
                    str(item) for item in (meta.get("target_bottleneck_resources") or [])
                ),
                "iteration_count": len(rows),
                "accepted_count": len(accepted),
                "rejected_count": len(rejected),
                "accepted_iterations": compact_ranges(accepted),
                "rejected_iterations": compact_ranges(rejected),
                "first_accepted_iteration": min(accepted) if accepted else None,
                "last_accepted_iteration": max(accepted) if accepted else None,
                "final_adopted_iteration": max(accepted) if accepted else None,
                "final_adopted_source": "accepted_iteration" if accepted else "start_or_unchanged_params",
                "initial_score": first.get("score"),
                "final_score": last.get("score"),
                "best_score": last.get("best_score"),
                "changed_params": ",".join(changed_params),
                "changed_param_count": len(changed_params),
            }
        )
    return summary_rows, detail_rows


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: List[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def markdown_table(rows: List[Dict[str, Any]]) -> str:
    lines = [
        "# Accepted Optimizer Iterations",
        "",
        "| case | loop | target resources | iterations | accepted count | accepted iterations | final adopted | changed params |",
        "|---|---:|---|---:|---:|---|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| {case_id} | {loop_index} | {target_bottleneck_resources} | {iteration_count} | "
            "{accepted_count} | {accepted_iterations} | {final_adopted_iteration} | {changed_params} |".format(
                **{key: ("" if value is None else value) for key, value in row.items()}
            )
        )
    return "\n".join(lines) + "\n"


def default_output_dir(run_dir: Optional[Path]) -> Path:
    if run_dir is not None:
        return Path("result") / "pg_benchmark_bottleneck_matrix" / run_dir.name / "accepted_iterations"
    return Path("accepted_iterations")


def main() -> None:
    args = parse_args()
    history_files, run_dir = discover_history_files(args.inputs)
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir(run_dir)

    summary_rows: List[Dict[str, Any]] = []
    detail_rows: List[Dict[str, Any]] = []
    for path in history_files:
        case_summary, case_detail = extract_file(path, run_dir)
        summary_rows.extend(case_summary)
        detail_rows.extend(case_detail)

    if args.fail_on_empty and not detail_rows:
        raise SystemExit("no optimizer iteration rows found")

    payload = {
        "run_dir": str(run_dir) if run_dir is not None else "",
        "history_files": [str(path) for path in history_files],
        "summary": summary_rows,
        "detail_count": len(detail_rows),
    }
    write_csv(output_dir / "accepted_iteration_summary.csv", summary_rows)
    write_json(output_dir / "accepted_iteration_summary.json", payload)
    write_csv(output_dir / "accepted_iteration_detail.csv", detail_rows)
    write_json(output_dir / "accepted_iteration_detail.json", detail_rows)
    md = markdown_table(summary_rows)
    (output_dir / "accepted_iteration_summary.md").write_text(md, encoding="utf-8")

    print(f"history_files={len(history_files)}")
    print(f"loop_rows={len(summary_rows)}")
    print(f"iteration_rows={len(detail_rows)}")
    print(f"output_dir={output_dir.resolve()}")
    print(f"summary_csv={(output_dir / 'accepted_iteration_summary.csv').resolve()}")
    print(f"summary_md={(output_dir / 'accepted_iteration_summary.md').resolve()}")
    if args.stdout:
        print()
        print(md)


if __name__ == "__main__":
    main()

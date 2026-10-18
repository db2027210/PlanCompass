#!/usr/bin/env python3
"""Summarize optimizer feedback-loop and accepted iteration details to Markdown."""

from __future__ import annotations

import argparse
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
            "Generate Markdown reports for PostgreSQL optimizer large feedback loops "
            "and accepted small-loop parameter iterations."
        )
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help="Run directories or *_optimizer_history_*.jsonl files.",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help=(
            "Output directory. Default: result/pg_benchmark_bottleneck_matrix/<run_id>/"
            "iteration_details for a run dir, otherwise ./iteration_details."
        ),
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=20,
        help="Maximum accepted small-loop rows to display in the detailed sample table.",
    )
    parser.add_argument("--stdout", action="store_true", help="Print generated Markdown to stdout.")
    return parser.parse_args()


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


def to_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def fmt(value: Any, digits: int = 4) -> str:
    number = to_float(value)
    if number is None:
        return ""
    if abs(number) >= 1000:
        return f"{number:.2f}"
    if 0 < abs(number) < 0.0001:
        return f"{number:.2e}"
    return f"{number:.{digits}f}"


def fmt_pct(value: Any) -> str:
    number = to_float(value)
    if number is None:
        return ""
    return f"{number:.2f}%"


def util_pct_from_fraction(value: Any) -> str:
    number = to_float(value)
    if number is None:
        return ""
    return f"{number * 100.0:.2f}%"


def md_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


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


def sample_evenly(rows: List[Dict[str, Any]], sample_size: int) -> List[Dict[str, Any]]:
    if sample_size <= 0 or len(rows) <= sample_size:
        return rows
    if sample_size == 1:
        return [rows[-1]]
    indexes = [
        round(i * (len(rows) - 1) / (sample_size - 1))
        for i in range(sample_size)
    ]
    sampled: List[Dict[str, Any]] = []
    seen = set()
    for idx in indexes:
        if idx in seen:
            continue
        seen.add(idx)
        sampled.append(rows[idx])
    return sampled


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
    case_id = f"{bottleneck}_{benchmark}"
    return {
        "run_id": run_dir.name if run_dir is not None else infer_run_id(history_path),
        "bottleneck": bottleneck,
        "benchmark": benchmark,
        "case_id": case_id,
        "bottleneck_order": BOTTLENECK_ORDER.get(bottleneck, 99),
    }


def infer_run_id(history_path: Path) -> str:
    parts = history_path.parts
    if "pg_benchmark_bottleneck_matrix" in parts:
        idx = parts.index("pg_benchmark_bottleneck_matrix")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return history_path.parent.parent.name


def default_output_dir(run_dir: Optional[Path], files: List[Path]) -> Path:
    if run_dir is not None:
        return Path("result/pg_benchmark_bottleneck_matrix") / run_dir.name / "iteration_details"
    run_id = infer_run_id(files[0]) if files else "unknown_run"
    return Path("result/pg_benchmark_bottleneck_matrix") / run_id / "iteration_details"


def collect_history(path: Path, run_dir: Optional[Path]) -> Dict[str, Any]:
    labels = case_labels(run_dir, path)
    loops: List[Dict[str, Any]] = []
    loop_by_index: Dict[int, Dict[str, Any]] = {}
    accepted_rows: List[Dict[str, Any]] = []
    all_iteration_rows: List[Dict[str, Any]] = []
    feedback_rounds: List[Dict[str, Any]] = []
    final_summary: Dict[str, Any] = {}
    current_loop: Optional[int] = None
    fallback_loop = -1
    previous_iteration: Optional[int] = None

    for obj in read_jsonl(path):
        event = obj.get("event")
        if event == "gradient_optimization_round_start":
            raw_round = obj.get("round_index")
            current_loop = int(raw_round) if isinstance(raw_round, int) else (current_loop or 0)
            loop = {
                "loop_index": current_loop,
                "timestamp": obj.get("timestamp"),
                "capability": obj.get("capability") or {},
                "target_bottleneck_resources": obj.get("target_bottleneck_resources") or [],
                "start_params": obj.get("start_params") or {},
                "candidate_params": obj.get("candidate_params") or {},
                "iterations": [],
            }
            loops.append(loop)
            loop_by_index[current_loop] = loop
            previous_iteration = None
            continue

        if event == "final_feedback_round_complete":
            feedback_rounds.append(obj)
            continue

        if event == "final_summary":
            final_summary = obj
            continue

        iteration = obj.get("iteration")
        if not isinstance(iteration, int) or "accepted" not in obj:
            continue
        if current_loop is None:
            current_loop = 0
            loop = {
                "loop_index": current_loop,
                "timestamp": obj.get("timestamp"),
                "capability": {},
                "target_bottleneck_resources": [],
                "start_params": {},
                "candidate_params": {},
                "iterations": [],
            }
            loops.append(loop)
            loop_by_index[current_loop] = loop
        elif previous_iteration is not None and iteration <= previous_iteration:
            fallback_loop = max(fallback_loop, current_loop) + 1
            current_loop = fallback_loop
            loop = {
                "loop_index": current_loop,
                "timestamp": obj.get("timestamp"),
                "capability": {},
                "target_bottleneck_resources": [],
                "start_params": {},
                "candidate_params": {},
                "iterations": [],
            }
            loops.append(loop)
            loop_by_index[current_loop] = loop
        previous_iteration = iteration

        row = dict(obj)
        row["loop_index"] = current_loop
        loop_by_index[current_loop]["iterations"].append(row)
        all_iteration_rows.append(row)
        if bool(obj.get("accepted")):
            accepted_rows.append(row)

    return {
        "labels": labels,
        "path": path,
        "loops": loops,
        "accepted_rows": accepted_rows,
        "all_iteration_rows": all_iteration_rows,
        "feedback_rounds": feedback_rounds,
        "final_summary": final_summary,
    }


def param_summary(params: Dict[str, Any]) -> str:
    if not isinstance(params, dict):
        return ""
    parts = []
    for key in ["seq_page_cost", "random_page_cost", "cpu_tuple_cost", "cpu_operator_cost", "parallel_tuple_cost"]:
        if key in params:
            parts.append(f"{key}={fmt(params.get(key), 4)}")
    return "<br>".join(parts)


def resource_util_triplet(util: Dict[str, Any], *, percent_values: bool = False) -> Tuple[str, str, str]:
    if not isinstance(util, dict):
        return "", "", ""
    if percent_values:
        return fmt_pct(util.get("cpu")), fmt_pct(util.get("memory")), fmt_pct(util.get("io"))
    return (
        util_pct_from_fraction(util.get("cpu")),
        util_pct_from_fraction(util.get("memory")),
        util_pct_from_fraction(util.get("io")),
    )


def resource_value_triplet(resource: Dict[str, Any]) -> Tuple[str, str, str]:
    if not isinstance(resource, dict):
        return "", "", ""
    return fmt(resource.get("cpu")), fmt(resource.get("memory")), fmt(resource.get("io"))


def feedback_resources(items: List[Dict[str, Any]]) -> str:
    parts = []
    for item in items or []:
        resource = item.get("resource")
        util = fmt_pct(item.get("utilization_pct"))
        parts.append(f"{resource}:{util}")
    return ", ".join(parts)


def capability_updates_text(updates: Dict[str, Any]) -> str:
    if not isinstance(updates, dict) or not updates:
        return ""
    parts = []
    for name, update in updates.items():
        old_cap = fmt(update.get("old_capability"))
        new_cap = fmt(update.get("new_capability"))
        parts.append(f"{name}:{old_cap}->{new_cap}")
    return "<br>".join(parts)


def validation_metric(validation: Dict[str, Any], key: str) -> Any:
    if not isinstance(validation, dict):
        return None
    if validation.get(key) is not None:
        return validation.get(key)
    benchmark = validation.get("benchmark") or {}
    latency = validation.get("benchmark_latency") or {}
    aliases = {
        "qps": ["xp_qps", "ap_qps", "throughput"],
        "tps": ["xp_tps", "tp_tps", "goodput"],
        "p90": ["latency_p90", "all_p90_avg", "p90"],
        "p95": ["latency_p95", "all_p95_avg", "p95"],
        "p99": ["latency_p99", "all_p99_avg", "p99"],
    }
    for alias in aliases.get(key, []):
        if benchmark.get(alias) is not None:
            return benchmark.get(alias)
        if latency.get(alias) is not None:
            return latency.get(alias)
    return None


def validation_db_metric(validation: Dict[str, Any], key: str) -> Any:
    if not isinstance(validation, dict):
        return None
    if validation.get(key) is not None:
        return validation.get(key)
    db_view_monitor = validation.get("db_view_monitor") or {}
    if isinstance(db_view_monitor, dict):
        return db_view_monitor.get(key)
    return None


def build_markdown(data: Dict[str, Any], sample_size: int) -> str:
    labels = data["labels"]
    final_summary = data["final_summary"] or {}
    loops = data["loops"]
    accepted_rows = data["accepted_rows"]
    feedback_rounds = data["feedback_rounds"]

    lines: List[str] = []
    lines.append(f"# Iteration Detail Summary: {labels['case_id']}")
    lines.append("")
    lines.append(f"- Run ID: `{labels['run_id']}`")
    lines.append(f"- Case: `{labels['bottleneck']}/{labels['benchmark']}`")
    lines.append(f"- Source: `{data['path']}`")
    lines.append(f"- Feedback rounds captured: `{len(feedback_rounds)}`")
    lines.append(f"- Optimizer small loops captured: `{len(loops)}`")
    lines.append(f"- Accepted small-loop iterations: `{len(accepted_rows)}`")
    lines.append("")

    if final_summary:
        initial_resource = final_summary.get("initial_resource") or {}
        final_resource = final_summary.get("final_resource") or {}
        initial_util = final_summary.get("initial_utilization") or {}
        final_util = final_summary.get("final_utilization") or {}
        init_cpu, init_mem, init_io = resource_value_triplet(initial_resource)
        final_cpu, final_mem, final_io = resource_value_triplet(final_resource)
        init_cpu_u, init_mem_u, init_io_u = resource_util_triplet(initial_util)
        final_cpu_u, final_mem_u, final_io_u = resource_util_triplet(final_util)
        lines.append("## Final Runtime Summary")
        lines.append("")
        lines.append("| Metric | CPU | Memory | IO |")
        lines.append("|---|---:|---:|---:|")
        lines.append(f"| Initial value | {init_cpu} | {init_mem} | {init_io} |")
        lines.append(f"| Initial utilization | {init_cpu_u} | {init_mem_u} | {init_io_u} |")
        lines.append(f"| Final value | {final_cpu} | {final_mem} | {final_io} |")
        lines.append(f"| Final utilization | {final_cpu_u} | {final_mem_u} | {final_io_u} |")
        lines.append("")
        lines.append("| Score | Value |")
        lines.append("|---|---:|")
        lines.append(f"| Initial score | {fmt(final_summary.get('initial_score'))} |")
        lines.append(f"| Final score | {fmt(final_summary.get('final_score'))} |")
        lines.append(f"| Score delta | {fmt(final_summary.get('score_improvement'))} |")
        lines.append("")
        final_eval = final_summary.get("final_effect_evaluation") or {}
        final_validation = final_eval.get("final_validation") or {}
        plan_change_summary = final_validation.get("plan_change_summary") or {}
        degradation_summary = final_summary.get("degradation_summary") or {}
        best_actual = final_eval.get("final_feedback_best_actual") or final_summary.get("final_feedback_best_actual") or {}
        if final_validation or degradation_summary or plan_change_summary:
            lines.append("## Final Workload Metrics")
            lines.append("")
            lines.append("| Metric | Value |")
            lines.append("|---|---:|")
            lines.append(f"| Workload QPS | {fmt(validation_metric(final_validation, 'qps'))} |")
            lines.append(f"| Workload TPS | {fmt(validation_metric(final_validation, 'tps'))} |")
            lines.append(f"| DB QPS | {fmt(validation_db_metric(final_validation, 'db_qps'))} |")
            lines.append(f"| DB xact TPS | {fmt(validation_db_metric(final_validation, 'db_xact_tps'))} |")
            lines.append(f"| DB commit TPS | {fmt(validation_db_metric(final_validation, 'db_commit_tps'))} |")
            lines.append(f"| DB rollback TPS | {fmt(validation_db_metric(final_validation, 'db_rollback_tps'))} |")
            lines.append(f"| DB active sessions | {fmt(validation_db_metric(final_validation, 'db_active_sessions'))} |")
            lines.append(f"| P90 ms | {fmt(validation_metric(final_validation, 'p90'))} |")
            lines.append(f"| P95 ms | {fmt(validation_metric(final_validation, 'p95'))} |")
            lines.append(f"| P99 ms | {fmt(validation_metric(final_validation, 'p99'))} |")
            lines.append(f"| Top SQL degraded count | {degradation_summary.get('degraded_count', '')} |")
            lines.append(f"| Top SQL max degradation % | {fmt(degradation_summary.get('max_degradation_pct'))} |")
            lines.append(f"| Top SQL avg degradation % | {fmt(degradation_summary.get('avg_degradation_pct'))} |")
            lines.append(f"| Plan changes | {plan_change_summary.get('changed_plan_count', '')} |")
            cumulative_plan_changes = plan_change_summary.get("cumulative_changed_plan_count")
            if cumulative_plan_changes not in (None, ""):
                lines.append(f"| Cumulative plan changes | {cumulative_plan_changes} |")
            lines.append(f"| Captured final plans | {plan_change_summary.get('final_plan_capture_count', '')} |")
            if best_actual:
                lines.append(f"| Selected best actual round | {best_actual.get('round_index', '')} |")
            lines.append("")
        if final_eval:
            lines.append(
                f"Target achieved flag: `{final_eval.get('target_achieved')}`; "
                f"final validation ok: `{final_eval.get('final_validation_ok')}`; "
                f"top SQL degraded count: `{final_eval.get('top_sql_degraded_count')}`."
            )
            lines.append("")
        lines.append("Final parameters:")
        lines.append("")
        lines.append("| Parameter | Value |")
        lines.append("|---|---:|")
        for key in PARAM_KEYS:
            lines.append(f"| `{key}` | {fmt((final_summary.get('optimized_params') or {}).get(key), 6)} |")
        lines.append("")

    lines.append("## Large Feedback Loop")
    lines.append("")
    lines.append("Every final feedback round is listed. Utilization columns are from actual runtime validation.")
    lines.append("")
    lines.append(
        "| Round | Converged Before | Retarget Skipped | Status Before Off-Target | "
        "Status Before Overload | Capability Updates | Final CPU | Final Mem | Final IO | Workload QPS | "
        "Workload TPS | DB QPS | DB xact TPS | P95 | P99 | Parameter Snapshot |"
    )
    lines.append("|---:|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for item in feedback_rounds:
        status = item.get("status_before") or {}
        validation = item.get("final_validation") or {}
        util = validation.get("utilization_pct") or {}
        params = item.get("optimized_params") or {}
        qps = validation_metric(validation, "qps")
        tps = validation_metric(validation, "tps")
        db_qps = validation_db_metric(validation, "db_qps")
        db_xact_tps = validation_db_metric(validation, "db_xact_tps")
        p95 = validation_metric(validation, "p95")
        p99 = validation_metric(validation, "p99")
        lines.append(
            "| {round_idx} | {converged} | {skipped} | {off_target} | {overloaded} | {updates} | "
            "{cpu} | {mem} | {io} | {qps} | {tps} | {db_qps} | {db_xact_tps} | {p95} | {p99} | {params} |".format(
                round_idx=item.get("round_index"),
                converged=status.get("converged"),
                skipped=item.get("capability_retarget_skipped"),
                off_target=md_escape(feedback_resources(status.get("off_target") or [])),
                overloaded=md_escape(feedback_resources(status.get("overloaded") or [])),
                updates=md_escape(capability_updates_text(item.get("capability_updates") or {})),
                cpu=fmt_pct(util.get("cpu")),
                mem=fmt_pct(util.get("memory")),
                io=fmt_pct(util.get("io")),
                qps=fmt(qps),
                tps=fmt(tps),
                db_qps=fmt(db_qps),
                db_xact_tps=fmt(db_xact_tps),
                p95=fmt(p95),
                p99=fmt(p99),
                params=md_escape(param_summary(params)),
            )
        )
    lines.append("")

    lines.append("## Small Loop Overview")
    lines.append("")
    lines.append("Each small loop is one gradient-optimizer invocation. Only accepted iteration ranges are shown.")
    lines.append("")
    lines.append(
        "| Loop | Accepted | Rejected | Last Iter | Accepted Iterations | Final Accepted Score | "
        "Final Accepted CPU | Final Accepted Mem | Final Accepted IO | Final Accepted Params |"
    )
    lines.append("|---:|---:|---:|---:|---|---:|---:|---:|---:|---|")
    for loop in loops:
        rows = loop.get("iterations") or []
        accepted = [row for row in rows if row.get("accepted")]
        rejected = [row for row in rows if not row.get("accepted")]
        last_iter = max((int(row.get("iteration") or 0) for row in rows), default=0)
        accepted_iters = compact_ranges([int(row.get("iteration") or 0) for row in accepted])
        final_accepted = accepted[-1] if accepted else {}
        util = final_accepted.get("utilization") or {}
        cpu_u, mem_u, io_u = resource_util_triplet(util)
        lines.append(
            "| {loop_idx} | {accepted_count} | {rejected_count} | {last_iter} | {accepted_iters} | "
            "{score} | {cpu} | {mem} | {io} | {params} |".format(
                loop_idx=loop.get("loop_index"),
                accepted_count=len(accepted),
                rejected_count=len(rejected),
                last_iter=last_iter,
                accepted_iters=accepted_iters,
                score=fmt(final_accepted.get("score")),
                cpu=cpu_u,
                mem=mem_u,
                io=io_u,
                params=md_escape(param_summary(final_accepted.get("params") or {})),
            )
        )
    lines.append("")

    sampled = sample_evenly(accepted_rows, sample_size)
    lines.append(f"## Accepted Small-Loop Iteration Sample ({len(sampled)} of {len(accepted_rows)})")
    lines.append("")
    lines.append(
        "| Loop | Iter | Reason | Score | CPU | Mem | IO | CPU Val | Mem Val | IO Val | "
        "seq | random | cpu_tuple | cpu_operator | parallel_setup | parallel_tuple |"
    )
    lines.append("|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for row in sampled:
        util = row.get("utilization") or {}
        resource = row.get("resource") or {}
        cpu_u, mem_u, io_u = resource_util_triplet(util)
        cpu_v, mem_v, io_v = resource_value_triplet(resource)
        params = row.get("params") or {}
        lines.append(
            "| {loop_idx} | {iteration} | {reason} | {score} | {cpu_u} | {mem_u} | {io_u} | "
            "{cpu_v} | {mem_v} | {io_v} | {seq} | {random} | {cpu_tuple} | {cpu_op} | "
            "{parallel_setup} | {parallel_tuple} |".format(
                loop_idx=row.get("loop_index"),
                iteration=row.get("iteration"),
                reason=md_escape(row.get("accept_reason")),
                score=fmt(row.get("score")),
                cpu_u=cpu_u,
                mem_u=mem_u,
                io_u=io_u,
                cpu_v=cpu_v,
                mem_v=mem_v,
                io_v=io_v,
                seq=fmt(params.get("seq_page_cost"), 6),
                random=fmt(params.get("random_page_cost"), 6),
                cpu_tuple=fmt(params.get("cpu_tuple_cost"), 6),
                cpu_op=fmt(params.get("cpu_operator_cost"), 6),
                parallel_setup=fmt(params.get("parallel_setup_cost"), 4),
                parallel_tuple=fmt(params.get("parallel_tuple_cost"), 6),
            )
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    files, run_dir = discover_history_files(args.inputs)
    if not files:
        raise SystemExit("No optimizer history JSONL files found.")
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir(run_dir, files)
    output_dir.mkdir(parents=True, exist_ok=True)

    written: List[Path] = []
    for path in files:
        data = collect_history(path, run_dir)
        markdown = build_markdown(data, sample_size=args.sample_size)
        labels = data["labels"]
        output_path = output_dir / f"iteration_detail_{labels['case_id']}.md"
        output_path.write_text(markdown + "\n", encoding="utf-8")
        written.append(output_path)
        if args.stdout:
            print(markdown)
            print()

    print("Wrote iteration detail reports:", flush=True)
    for path in written:
        print(f"  {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

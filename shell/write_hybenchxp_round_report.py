#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import math
import statistics
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


PARAM_ORDER = [
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
]


def load_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            yield json.loads(line)


def fmt(value: Any, digits: int = 9) -> str:
    if value is None:
        return "null"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    text = f"{number:.{digits}f}"
    if "." in text:
        return text.rstrip("0").rstrip(".")
    return text


def candidate_metric(row: Dict[str, Any], key: str) -> Optional[Any]:
    if key in ("cpu", "memory", "io", "io_read", "io_write"):
        return (row.get("resources") or {}).get(key)
    metrics = row.get("metrics") or {}
    if key in metrics:
        return metrics.get(key)
    decision = row.get("decision") or {}
    aliases = {
        "db_qps": "candidate_db_qps",
        "xp_qps": "candidate_hybench_xp_qps",
        "xp_tps": "candidate_hybench_xp_tps",
        "hybench_xp_qps": "candidate_hybench_xp_qps",
        "hybench_xp_tps": "candidate_hybench_xp_tps",
    }
    return decision.get(aliases.get(key, f"candidate_{key}"))


def best_candidate_metric(row: Dict[str, Any], key: str) -> Optional[Any]:
    if key in ("cpu", "memory", "io", "io_read", "io_write"):
        return (row.get("resources") or {}).get(key)
    if key == "db_qps":
        return row.get("db_qps")
    benchmark = (row.get("hybench") or {}).get("benchmark") or {}
    if key == "xp_qps":
        return benchmark.get("xp_qps")
    if key == "xp_tps":
        return benchmark.get("xp_tps")
    return row.get(key)


def select_by(rows: List[Dict[str, Any]], key: str, reverse: bool) -> Optional[Dict[str, Any]]:
    candidates = [row for row in rows if candidate_metric(row, key) is not None]
    if not candidates:
        return None
    return sorted(candidates, key=lambda row: float(candidate_metric(row, key)), reverse=reverse)[0]


def point_line(title: str, row: Optional[Dict[str, Any]]) -> str:
    if row is None:
        return f"- {title}: none"
    decision = row.get("decision") or {}
    other_up = ",".join(decision.get("other_up_resources") or []) or "none"
    return (
        f"- {title}: `{row.get('label')}`, CPU `{fmt(candidate_metric(row, 'cpu'))}`, "
        f"DB-QPS `{fmt(candidate_metric(row, 'db_qps'))}`, XP-QPS / XP-TPS "
        f"`{fmt(candidate_metric(row, 'xp_qps'), 2)} / {fmt(candidate_metric(row, 'xp_tps'), 2)}`; "
        f"reject_reason=`{row.get('reject_reason') or ''}`; "
        f"target_delta_vs_worker_baseline=`{fmt(decision.get('target_delta_pct'), 4)}%`, "
        f"other_up=`{other_up}`"
    )


def min_resource_samples(rows: List[Dict[str, Any]]) -> List[int]:
    samples: List[int] = []
    for row in rows:
        values: List[int] = []
        for value in (row.get("resource_samples") or {}).values():
            try:
                values.append(int(value))
            except (TypeError, ValueError):
                pass
        if values:
            samples.append(min(values))
    return samples


def resource_stats(rows: List[Dict[str, Any]], key: str) -> Optional[tuple]:
    values = []
    for row in rows:
        value = candidate_metric(row, key)
        if value is None:
            continue
        values.append(float(value))
    if not values:
        return None
    return min(values), statistics.median(values), max(values)


def elapsed_between(start: Any, end: Any) -> Optional[float]:
    try:
        start_dt = dt.datetime.strptime(str(start), "%Y-%m-%d %H:%M:%S")
        end_dt = dt.datetime.strptime(str(end), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None
    return max(0.0, (end_dt - start_dt).total_seconds())


def main() -> int:
    parser = argparse.ArgumentParser(description="Write HyBench XP binary-search round report from JSONL logs.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--target", default="cpu", choices=["cpu", "memory", "io", "none"])
    parser.add_argument("--round", type=int, required=True)
    parser.add_argument("--report-dir", default="report/bonect")
    parser.add_argument("--timestamp", default="")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    log_path = run_dir / f"{args.target}_binary_search_hybenchxp.jsonl"
    sanity_path = run_dir / "resource_monitor_sanity_hybenchxp.jsonl"
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    round_no = args.round
    rows: List[Dict[str, Any]] = []
    round_starts: List[Dict[str, Any]] = []
    parallel_starts: List[Dict[str, Any]] = []
    summary: Optional[Dict[str, Any]] = None
    next_start: Optional[Dict[str, Any]] = None
    for item in load_jsonl(log_path):
        event = item.get("event")
        item_round = int(item.get("round") or 0) if item.get("round") is not None else None
        if event == "round_start" and item_round == round_no:
            round_starts.append(item)
        elif event == "round_parallel_validation_start" and item_round == round_no:
            parallel_starts.append(item)
        elif event == "candidate_validation" and item_round == round_no:
            rows.append(item)
        elif event == "round_summary" and item_round == round_no:
            summary = item
        elif event == "round_start" and item_round == round_no + 1:
            next_start = item

    if not round_starts or summary is None:
        raise RuntimeError(f"round {round_no} is incomplete in {log_path}")

    start = round_starts[0]

    status_counts = collections.Counter(row.get("status") for row in rows)
    reject_counts = collections.Counter(row.get("reject_reason", "") for row in rows)
    candidate_failures = sum(1 for row in rows if row.get("status") != "ok" or row.get("monitor_error"))
    samples = min_resource_samples(rows)

    sanity_rows = list(load_jsonl(sanity_path)) if sanity_path.exists() else []
    latest_sanity = sanity_rows[-1] if sanity_rows else None
    warnings = []
    start_ts = start.get("timestamp") or ""
    end_ts = summary.get("timestamp") or ""
    for item in sanity_rows:
        ts = item.get("timestamp") or ""
        if start_ts <= ts <= end_ts and (item.get("anomaly_count") or 0):
            warnings.append(item)
    warning_types = collections.Counter()
    for item in warnings:
        for anomaly in item.get("anomalies") or []:
            warning_types[(anomaly.get("type"), anomaly.get("severity"), anomaly.get("consecutive"))] += 1

    lowest_target = select_by(rows, args.target if args.target != "none" else "cpu", False)
    lowest_cpu = select_by(rows, "cpu", False)
    highest_db_qps = select_by(rows, "db_qps", True)
    highest_xp_qps = select_by(rows, "xp_qps", True)
    highest_xp_tps = select_by(rows, "xp_tps", True)
    incumbent = summary.get("best_candidate") or {}
    updated_ranges = summary.get("updated_ranges") or {}
    max_parallelism = max(
        [int(item.get("validation_parallelism") or 0) for item in parallel_starts]
        or [int(summary.get("validation_parallelism") or 0)]
    )
    validation_count = int(summary.get("validation_count") or len(rows))
    unique_point_batches = int(math.ceil(validation_count / max(1, max_parallelism))) if validation_count else 0
    attempt_planned_batches = sum(int(item.get("validation_batch_count") or 0) for item in parallel_starts)
    attempt_new_validations = [int(item.get("new_validation_count") or 0) for item in parallel_starts]
    wall_elapsed = elapsed_between(start_ts, end_ts)
    replay_seconds = sum(float(row.get("elapsed_s") or 0.0) for row in rows)
    replay_parallel_lower_bound = replay_seconds / max(1, max_parallelism)

    report_stamp = args.timestamp or (summary.get("timestamp") or "").replace("-", "").replace(":", "").replace(" ", "_")[:13]
    report_path = report_dir / (
        f"binary_plan_resource_tuning_hybenchxp_{args.target}_round{round_no}_complete_{report_stamp}.md"
    )

    lines: List[str] = []
    title_stamp = summary.get("timestamp") or report_stamp
    lines.append(f"# HyBench XP {args.target.upper()} Binary Search Round {round_no} Snapshot {title_stamp}")
    lines.append("")
    lines.append("## Status")
    lines.append("")
    lines.append(f"- Run dir: `{run_dir}`")
    lines.append("- Workload: `hybenchxp`")
    lines.append(f"- Stage: {args.target.upper()} binary search")
    lines.append(f"- Round {round_no} completed at `{summary.get('timestamp')}`")
    if next_start is not None:
        lines.append(f"- Round {round_no + 1} started automatically at `{next_start.get('timestamp')}`")
    else:
        lines.append(f"- Round {round_no + 1} has not started in this log yet")
    lines.append("")
    lines.append(f"## Round {round_no} Audit")
    lines.append("")
    lines.append(f"- Search points: `{start.get('raw_search_point_count')}`")
    lines.append(f"- Real validations: `{summary.get('validation_count')}`")
    lines.append(f"- Replay pruning: `{summary.get('replay_pruned_count')}`")
    lines.append(
        f"- Legacy topk fields: `topk_limit={summary.get('topk_limit')}`, "
        f"`topk_candidate_count={summary.get('topk_candidate_count')}`; no top20 pruning occurred."
    )
    lines.append(f"- Validation parallelism: `{max_parallelism}`")
    lines.append(f"- Unique-point batches at that parallelism: `{unique_point_batches}` (`{validation_count}/{max_parallelism}`)")
    lines.append(f"- Execution attempts: `{len(parallel_starts)}`; new validations planned per attempt: `{attempt_new_validations}`")
    lines.append(f"- Attempt-planned batches total: `{attempt_planned_batches}`")
    lines.append(
        f"- Final-attempt summary batches / elapsed: `{summary.get('validation_batch_count')}` / "
        f"`{fmt(summary.get('validation_elapsed_s'), 3)}s`"
    )
    lines.append(f"- Candidate replay elapsed sum / parallelism lower bound: `{fmt(replay_parallel_lower_bound, 3)}s`")
    lines.append(f"- End-to-end round wall elapsed: `{fmt(wall_elapsed, 3)}s` (`{start_ts}` to `{end_ts}`)")
    lines.append(f"- Candidate failures: `{candidate_failures}`")
    lines.append(f"- Status counts: `{dict(status_counts)}`")
    lines.append(f"- Accepted: `{summary.get('accepted_count')}`")
    lines.append(f"- Rejected: `{sum(value for key, value in reject_counts.items() if key)}`")
    reject_summary = ", ".join(f"{key}={value}" for key, value in sorted((summary.get("reject_reasons") or {}).items()))
    lines.append(f"- Reject reasons: `{reject_summary or 'none'}`")
    lines.append("")
    lines.append("## Resource Stability")
    lines.append("")
    if samples:
        lines.append(
            f"- Stable resource sample count min/median/max: `{min(samples)} / "
            f"{fmt(statistics.median(samples), 1)} / {max(samples)}`"
        )
    else:
        lines.append("- Stable resource sample count min/median/max: `missing`")
    lines.append("- Candidate-level `monitor_error`: none" if candidate_failures == 0 else "- Candidate-level `monitor_error`: check JSONL")
    for key in ("cpu", "memory", "io"):
        stats = resource_stats(rows, key)
        if stats:
            lines.append(
                f"- Candidate {key.upper()} min/median/max: `{fmt(stats[0])} / {fmt(stats[1])} / {fmt(stats[2])}`"
            )
    if latest_sanity is not None:
        lines.append(
            f"- Latest sanity sample after report generation (`{latest_sanity.get('timestamp')}`): "
            f"`anomaly_count={latest_sanity.get('anomaly_count')}`"
        )
    if warnings:
        parts = [
            f"{key[0]}/{key[1]}/consecutive={key[2]}: {value}"
            for key, value in sorted(warning_types.items(), key=lambda item: tuple(str(value) for value in item[0]))
        ]
        lines.append(
            f"- Sanity warnings during round: `{len(warnings)}` samples; summary `{'; '.join(parts)}`. "
            "Validation-prefixed warnings are JSONL resource audits; docker-stats warnings describe live containers."
        )
    else:
        lines.append("- Sanity warnings during round: none")
    lines.append("")
    lines.append("## Best / Anchor")
    lines.append("")
    lines.append(f"- Range anchor after round {round_no}: `{summary.get('range_anchor_label')}`")
    lines.append(f"- Anchor source: `{summary.get('range_update_anchor')}`")
    lines.append("- Interpretation: continue from the logged updated ranges; do not stop before epsilon/max-round convergence.")
    lines.append("")
    lines.append(f"Current incumbent `{incumbent.get('label')}`:")
    lines.append("")
    lines.append(f"- CPU: `{fmt(best_candidate_metric(incumbent, 'cpu'))}`")
    lines.append(f"- DB-QPS: `{fmt(best_candidate_metric(incumbent, 'db_qps'))}`")
    lines.append(
        f"- XP-QPS / XP-TPS: `{fmt(best_candidate_metric(incumbent, 'xp_qps'), 2)} / "
        f"{fmt(best_candidate_metric(incumbent, 'xp_tps'), 2)}`"
    )
    lines.append(
        f"- Memory / IO: `{fmt(best_candidate_metric(incumbent, 'memory'))} / "
        f"{fmt(best_candidate_metric(incumbent, 'io'))}`"
    )
    lines.append("")
    lines.append(f"Notable round-{round_no} points:")
    lines.append("")
    if args.target != "cpu":
        lines.append(point_line(f"Lowest {args.target.upper()} target", lowest_target))
    lines.append(point_line("Lowest CPU", lowest_cpu))
    lines.append(point_line("Highest DB-QPS", highest_db_qps))
    lines.append(point_line("Highest XP-QPS", highest_xp_qps))
    if highest_xp_tps is not highest_db_qps:
        lines.append(point_line("Highest XP-TPS", highest_xp_tps))
    lines.append("")
    lines.append(f"## Updated Ranges For Round {round_no + 1}")
    lines.append("")
    for key in PARAM_ORDER:
        lines.append(f"- `{key}`: `{updated_ranges.get(key)}`")
    lines.append("")
    lines.append("## Handoff")
    lines.append("")
    lines.append(f"- Auto handoff JSON expected: `handoff_snapshots/{args.target}_round_{round_no:03d}_snapshot.json`")
    lines.append(f"- Auto handoff Markdown expected: `handoff_snapshots/{args.target}_round_{round_no:03d}_handoff.md`")
    lines.append(f"- Continue monitoring round {round_no + 1} until `128/128` real validations complete.")
    lines.append("- Do not stop on no improvement; current config has `stop_only_on_epsilon=true` and `max_rounds=17`.")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

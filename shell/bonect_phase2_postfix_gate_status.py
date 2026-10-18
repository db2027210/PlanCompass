#!/usr/bin/env python3
"""Check Bonect phase-2 post-fix top-k and best-primary gates from JSONL history."""

from __future__ import annotations

import argparse
import glob
import json
import math
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


INTERESTING_EVENTS = {
    "candidate_param_feature_filter",
    "bruteforce_replay_candidate_pruning_checked",
    "small_loop_ranking_goal_summary",
    "small_loop_first_stage_ranking_gate_stop",
    "final_feedback_best_primary_by_db_qps_updated",
    "final_feedback_best_primary_by_db_qps_selected",
}


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def iter_jsonl(path: Path) -> Iterable[Tuple[int, Dict[str, Any]]]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_no, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict):
                yield line_no, row


def to_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except Exception:
        return None
    return parsed if math.isfinite(parsed) else None


def to_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def find_histories(args: argparse.Namespace) -> List[Path]:
    paths: List[Path] = []
    for raw in args.history:
        matches = [Path(item) for item in glob.glob(str(raw))]
        if matches:
            paths.extend(matches)
        else:
            paths.append(Path(raw))
    if args.run_dir:
        pattern = (
            Path(args.run_dir)
            / args.bottleneck
            / f"{args.benchmark}_optimizer_history_*.jsonl"
        )
        paths.extend(Path(item) for item in glob.glob(str(pattern)))
    seen = set()
    existing: List[Path] = []
    for path in paths:
        key = str(path)
        if key in seen or not path.exists():
            continue
        seen.add(key)
        existing.append(path)
    existing.sort(key=lambda item: (item.stat().st_mtime, str(item)), reverse=True)
    return existing


def summarize_history(path: Path, args: argparse.Namespace) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    latest: Dict[str, Dict[str, Any]] = {}
    latest_line: Dict[str, int] = {}
    line_count = 0
    for line_no, row in iter_jsonl(path):
        line_count = max(line_count, line_no)
        event = str(row.get("event") or "")
        counts[event] = counts.get(event, 0) + 1
        if event in INTERESTING_EVENTS:
            latest[event] = row
            latest_line[event] = line_no

    small = latest.get("small_loop_ranking_goal_summary") or {}
    validated = to_int(small.get("validated_candidate_count"))
    required_validated = (
        to_int(small.get("required_validated_candidate_count"))
        or args.required_validated
    )
    # When adaptive replay controller is enabled, the required_validated count
    # is decoupled from candidate_replay_top_k and set to min_evidence
    # (override §520-527). If the summary carries lane_aware_replay_validation,
    # prefer the lane_quota_config.min_evidence as the required count.
    lane_aware_active = bool(small.get("lane_aware_replay_validation"))
    if lane_aware_active:
        lane_quota_cfg = small.get("lane_quota_config") or {}
        lane_min_evidence = to_int(lane_quota_cfg.get("min_evidence"))
        if lane_min_evidence:
            required_validated = lane_min_evidence
    inversion = to_float(small.get("pairwise_inversion_rate"))
    inversion_threshold = (
        to_float(small.get("pairwise_inversion_threshold"))
        if small.get("pairwise_inversion_threshold") is not None
        else args.inversion_threshold
    )
    long_hits = to_int(small.get("long_term_goal_hit_count_in_validated_prefix"))
    required_hits = (
        to_int(small.get("min_long_term_goal_hits"))
        or to_int(small.get("required_long_term_goal_hit_count"))
        or args.required_long_term_hits
    )
    topk_ready = bool(small.get("topk_validation_ready", False))
    ranking_close = bool(small.get("ranking_close", False))
    long_hits_met = bool(small.get("long_term_goal_hit_count_met", False))

    small_failures: List[str] = []
    if not small:
        small_failures.append("missing_small_loop_ranking_goal_summary")
    if validated is None or validated < required_validated:
        small_failures.append("validated_candidate_count_below_required")
    if not topk_ready:
        small_failures.append("topk_validation_not_ready")
    if inversion is None or inversion_threshold is None or inversion > inversion_threshold:
        small_failures.append("pairwise_inversion_rate_above_threshold")
    if long_hits is None or long_hits < required_hits:
        small_failures.append("long_term_goal_hit_count_below_required")
    if not ranking_close:
        small_failures.append("ranking_close_false")
    if not long_hits_met:
        small_failures.append("long_term_goal_hit_count_met_false")

    best = (
        latest.get("final_feedback_best_primary_by_db_qps_selected")
        or latest.get("final_feedback_best_primary_by_db_qps_updated")
        or {}
    )
    status = best.get("primary_target_status") or {}
    db_delta = to_float(status.get("db_qps_delta_pct"))
    memory_improvement = to_float(status.get("bottleneck_resource_improvement_pp"))
    target_direction_ok = bool(best.get("target_direction_ok", False))
    performance_guard_ok = bool(best.get("performance_guard_ok", False))
    details = best.get("target_direction_details") or []
    other_up_details: List[Dict[str, Any]] = []
    ignored = {item.strip().lower() for item in args.other_up_ignored if item.strip()}
    target_resource = args.target_resource.strip().lower()
    if isinstance(details, list):
        for detail in details:
            if not isinstance(detail, dict):
                continue
            name = str(detail.get("resource") or "").strip().lower()
            if not name or name == target_resource or name in ignored:
                continue
            delta = to_float(detail.get("delta_pct", detail.get("delta")))
            if delta is not None and delta > 0.0:
                other_up_details.append(detail)

    best_failures: List[str] = []
    if not best:
        best_failures.append("missing_best_primary_by_db_qps_event")
    if db_delta is None or db_delta <= 0.0:
        best_failures.append("db_qps_not_positive")
    if memory_improvement is None or memory_improvement <= 0.0:
        best_failures.append("target_resource_not_improved")
    if not target_direction_ok:
        best_failures.append("target_direction_ok_false")
    if not performance_guard_ok:
        best_failures.append("performance_guard_ok_false")
    if best and not other_up_details and args.require_other_up_detail:
        best_failures.append("missing_non_ignored_other_resource_up_detail")

    preferred_target_met = bool(
        db_delta is not None
        and db_delta >= args.preferred_db_qps_delta
        and memory_improvement is not None
        and memory_improvement >= args.preferred_target_improvement
    )

    post_fix_markers = {
        "candidate_param_feature_filter": counts.get("candidate_param_feature_filter", 0),
        "bruteforce_replay_candidate_pruning_checked": counts.get(
            "bruteforce_replay_candidate_pruning_checked",
            0,
        ),
    }
    post_fix_seen = bool(
        post_fix_markers["candidate_param_feature_filter"] > 0
        or post_fix_markers["bruteforce_replay_candidate_pruning_checked"] > 0
    )

    return {
        "history_path": str(path),
        "history_mtime": time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.localtime(path.stat().st_mtime),
        ),
        "line_count": line_count,
        "event_counts": {
            name: counts.get(name, 0)
            for name in sorted(INTERESTING_EVENTS)
        },
        "latest_event_lines": latest_line,
        "post_fix_marker_counts": post_fix_markers,
        "post_fix_seen": post_fix_seen,
        "small_loop": {
            "present": bool(small),
            "pass": not small_failures,
            "failures": small_failures,
            "validated_candidate_count": validated,
            "required_validated_candidate_count": required_validated,
            "topk_validation_ready": topk_ready,
            "pairwise_inversion_rate": inversion,
            "pairwise_inversion_threshold": inversion_threshold,
            "long_term_goal_hit_count_in_validated_prefix": long_hits,
            "required_long_term_goal_hit_count": required_hits,
            "ranking_close": ranking_close,
            "long_term_goal_hit_count_met": long_hits_met,
            "summary": small,
        },
        "best_primary": {
            "present": bool(best),
            "pass": not best_failures,
            "failures": best_failures,
            "db_qps_delta_pct": db_delta,
            "target_resource_improvement_pp": memory_improvement,
            "target_direction_ok": target_direction_ok,
            "performance_guard_ok": performance_guard_ok,
            "preferred_target_met": preferred_target_met,
            "preferred_db_qps_delta_pct": args.preferred_db_qps_delta,
            "preferred_target_resource_improvement_pp": args.preferred_target_improvement,
            "other_up_details": other_up_details,
            "event": best,
        },
        "overall": {
            "post_fix_validation_ready": post_fix_seen and not small_failures,
            "best_primary_ready": not best_failures,
            "preferred_goal_met": preferred_target_met and not best_failures,
        },
    }


def write_markdown(path: Path, payload: Dict[str, Any]) -> None:
    rows = payload.get("histories") or []
    latest = payload.get("latest") or {}
    small = latest.get("small_loop") or {}
    best = latest.get("best_primary") or {}
    lines = [
        "# Bonect phase-2 post-fix gate status",
        "",
        f"- generated_at: `{payload.get('generated_at')}`",
        f"- selected_history: `{latest.get('history_path')}`",
        f"- post_fix_seen: `{latest.get('post_fix_seen')}`",
        f"- small_loop_pass: `{small.get('pass')}`",
        f"- best_primary_pass: `{best.get('pass')}`",
        f"- preferred_goal_met: `{(latest.get('overall') or {}).get('preferred_goal_met')}`",
        "",
        "## Small Loop",
        "",
        f"- validated_candidate_count: `{small.get('validated_candidate_count')}`",
        f"- required_validated_candidate_count: `{small.get('required_validated_candidate_count')}`",
        f"- pairwise_inversion_rate: `{small.get('pairwise_inversion_rate')}`",
        f"- pairwise_inversion_threshold: `{small.get('pairwise_inversion_threshold')}`",
        f"- long_term_goal_hit_count: `{small.get('long_term_goal_hit_count_in_validated_prefix')}`",
        f"- required_long_term_goal_hit_count: `{small.get('required_long_term_goal_hit_count')}`",
        f"- failures: `{small.get('failures')}`",
        "",
        "## Best Primary",
        "",
        f"- db_qps_delta_pct: `{best.get('db_qps_delta_pct')}`",
        f"- target_resource_improvement_pp: `{best.get('target_resource_improvement_pp')}`",
        f"- target_direction_ok: `{best.get('target_direction_ok')}`",
        f"- performance_guard_ok: `{best.get('performance_guard_ok')}`",
        f"- failures: `{best.get('failures')}`",
        "",
        "## Histories Considered",
        "",
        "| mtime | post-fix | small-loop pass | best pass | path |",
        "|---|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            f"`{row.get('history_mtime')}` | "
            f"`{row.get('post_fix_seen')}` | "
            f"`{(row.get('small_loop') or {}).get('pass')}` | "
            f"`{(row.get('best_primary') or {}).get('pass')}` | "
            f"`{row.get('history_path')}` |"
        )
    atomic_write_text(path, "\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="append", default=[])
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--bottleneck", default="memory")
    parser.add_argument("--benchmark", default="hybench_ap")
    parser.add_argument("--required-validated", type=int, default=20)
    parser.add_argument("--inversion-threshold", type=float, default=0.10)
    parser.add_argument("--required-long-term-hits", type=int, default=3)
    parser.add_argument("--target-resource", default="memory")
    parser.add_argument("--other-up-ignored", action="append", default=["io"])
    parser.add_argument("--require-other-up-detail", action="store_true")
    parser.add_argument("--preferred-db-qps-delta", type=float, default=5.0)
    parser.add_argument("--preferred-target-improvement", type=float, default=5.0)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--md-output", type=Path)
    parser.add_argument(
        "--require-pass",
        action="store_true",
        help="Exit non-zero unless latest history passes small-loop and best-primary gates.",
    )
    args = parser.parse_args()

    histories = find_histories(args)
    summaries = [summarize_history(path, args) for path in histories]
    latest = summaries[0] if summaries else {}
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "history_count": len(summaries),
        "latest": latest,
        "histories": summaries,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.json_output:
        atomic_write_text(args.json_output, text + "\n")
    if args.md_output:
        write_markdown(args.md_output, payload)
    print(text)
    if args.require_pass:
        overall = latest.get("overall") or {}
        if not (
            overall.get("post_fix_validation_ready")
            and overall.get("best_primary_ready")
        ):
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

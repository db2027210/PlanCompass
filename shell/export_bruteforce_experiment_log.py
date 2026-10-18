#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


JsonDict = Dict[str, Any]


def _float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _read_jsonl(path: Path) -> List[JsonDict]:
    rows: List[JsonDict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _round_number(row: JsonDict) -> Optional[int]:
    value = row.get("round")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _resource_fraction(
    resources: JsonDict,
    *,
    cpu_cores: float,
    mem_gb: float,
    disk_bw_mb: float,
) -> JsonDict:
    cpu = _float(resources.get("cpu"))
    memory = _float(resources.get("memory"))
    io_value = _float(resources.get("io"))
    return {
        "cpu": cpu / cpu_cores if cpu is not None and cpu_cores > 0 else None,
        "mem": memory / mem_gb if memory is not None and mem_gb > 0 else None,
        "io": io_value / disk_bw_mb if io_value is not None and disk_bw_mb > 0 else None,
    }


def _utility(row: JsonDict) -> Optional[float]:
    real_score = row.get("real_score_b")
    if isinstance(real_score, dict):
        score = _float(real_score.get("score_b"))
        if score is not None:
            return score
    for key in ("utility", "actual_utility", "score"):
        score = _float(row.get(key))
        if score is not None:
            return score
    return None


def _estimated_utility(row: JsonDict) -> Optional[float]:
    for key in ("estimated_utility", "plan_estimator_score", "estimated_score"):
        score = _float(row.get(key))
        if score is not None:
            return score
    return None


def _explain_samples(row: JsonDict) -> List[str]:
    samples: List[str] = []
    raw = row.get("explain_samples")
    if isinstance(raw, list):
        samples.extend(str(value) for value in raw if value)
    elif isinstance(raw, str) and raw.strip():
        samples.append(raw)
    for key in ("explain_analyze", "explain_output"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            samples.append(value)
    return samples


def _candidate_key(row: JsonDict) -> Tuple[int, int]:
    round_value = _round_number(row) or -1
    try:
        candidate_index = int(row.get("candidate_index", -1))
    except (TypeError, ValueError):
        candidate_index = -1
    return round_value, candidate_index


def _strict_real_candidate(row: JsonDict, validation_backend: str) -> bool:
    resource_stats = row.get("resource_stats")
    qps = _float(row.get("db_qps"))
    return (
        row.get("status") == "ok"
        and row.get("returncode") == 0
        and row.get("validation_backend") == validation_backend
        and qps is not None
        and qps > 0.0
        and isinstance(resource_stats, dict)
        and resource_stats.get("resource_monitor_ok") is True
    )


def _best_candidate(candidates: Iterable[JsonDict]) -> Optional[JsonDict]:
    accepted = [row for row in candidates if row.get("accepted") is True]
    pool = accepted or list(candidates)
    usable = [row for row in pool if _float(row.get("db_qps")) is not None]
    return max(usable, key=lambda row: float(row["db_qps"])) if usable else None


def _find_anchor_candidate(
    summary: JsonDict,
    candidates: List[JsonDict],
    candidates_by_label: Dict[str, JsonDict],
) -> Optional[JsonDict]:
    label = summary.get("range_anchor_label")
    if isinstance(label, str) and label in candidates_by_label:
        return candidates_by_label[label]
    round_value = _round_number(summary)
    same_round = [row for row in candidates if _round_number(row) == round_value]
    return _best_candidate(same_round)


def _window_bounds(rows: Iterable[JsonDict]) -> Tuple[Optional[float], Optional[float]]:
    starts: List[float] = []
    ends: List[float] = []
    for row in rows:
        window = row.get("window")
        if not isinstance(window, list) or len(window) < 2:
            continue
        start = _float(window[0])
        end = _float(window[1])
        if start is not None:
            starts.append(start)
        if end is not None:
            ends.append(end)
    return (min(starts) if starts else None, max(ends) if ends else None)


def _inner_loop_wall_time_s(candidates: Iterable[JsonDict]) -> float:
    """ inner loop wall time s."""
    rows_by_round: Dict[int, List[JsonDict]] = {}
    for row in candidates:
        round_value = _round_number(row)
        if round_value is not None:
            rows_by_round.setdefault(round_value, []).append(row)
    total = 0.0
    for round_rows in rows_by_round.values():
        start, end = _window_bounds(round_rows)
        if start is not None and end is not None and end >= start:
            total += end - start
        else:
            total += sum(_float(row.get("elapsed_s")) or 0.0 for row in round_rows)
    return total


def _baseline(rows: List[JsonDict]) -> Optional[JsonDict]:
    baselines = [
        row
        for row in rows
        if row.get("event") == "baseline_validation"
        and row.get("status") == "ok"
        and row.get("validation_backend")
    ]
    aggregate = [row for row in baselines if row.get("worker_key") == "parallel_baseline_aggregate"]
    return (aggregate or baselines)[-1] if baselines else None


def _worker_key(row: JsonDict) -> str:
    for key in ("comparison_baseline_key", "worker_key"):
        value = row.get(key)
        if isinstance(value, str) and value and value != "parallel_baseline_aggregate":
            return value
    worker = row.get("worker")
    if isinstance(worker, dict):
        for key in ("name", "container"):
            value = worker.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _baseline_for_candidate(
    rows: List[JsonDict],
    candidate: Optional[JsonDict],
    fallback: Optional[JsonDict],
) -> Optional[JsonDict]:
    if candidate is None:
        return fallback
    key = _worker_key(candidate)
    if not key:
        return fallback
    matching = [
        row
        for row in rows
        if row.get("event") == "baseline_worker_validation"
        and row.get("status") == "ok"
        and row.get("validation_backend")
        and _worker_key(row) == key
    ]
    return matching[-1] if matching else fallback


def build_document(args: argparse.Namespace, rows: List[JsonDict]) -> JsonDict:
    all_candidate_rows = [row for row in rows if row.get("event") == "candidate_validation"]
    latest_candidates_by_key: Dict[Tuple[int, int], JsonDict] = {}
    for row in all_candidate_rows:
        round_value = _round_number(row)
        candidate_index = _int(row.get("candidate_index"))
        if round_value is None or candidate_index is None:
            continue
        latest_candidates_by_key[(round_value, candidate_index)] = row
    authoritative_candidate_attempts = list(latest_candidates_by_key.values())
    candidates = sorted(
        [
            row
            for row in authoritative_candidate_attempts
            if _strict_real_candidate(row, args.validation_backend)
        ],
        key=_candidate_key,
    )
    latest_summaries_by_round: Dict[int, JsonDict] = {}
    for row in rows:
        if row.get("event") != "round_summary":
            continue
        round_value = _round_number(row)
        if round_value is not None:
            latest_summaries_by_round[round_value] = row
    summaries = [latest_summaries_by_round[key] for key in sorted(latest_summaries_by_round)]
    explain_backfills = {
        round_value: _explain_samples(row)
        for row in rows
        if row.get("event") == "round_explain_backfill"
        for round_value in [_round_number(row)]
        if round_value is not None and _explain_samples(row)
    }
    complete_events = [row for row in rows if row.get("event") == "search_complete"]
    complete = bool(complete_events)
    if not complete and not args.allow_incomplete:
        raise ValueError("search_complete is missing; use --allow-incomplete only for a preview")

    candidates_by_label = {
        str(row["label"]): row for row in candidates if isinstance(row.get("label"), str)
    }
    best = _best_candidate(candidates)
    aggregate_baseline = _baseline(rows)
    baseline = _baseline_for_candidate(rows, best, aggregate_baseline)
    start_ts, end_ts = _window_bounds([row for row in rows if row.get("event") in {
        "baseline_validation", "candidate_validation"
    }])

    outer_loop: List[JsonDict] = []
    for summary in summaries:
        anchor = _find_anchor_candidate(summary, candidates, candidates_by_label)
        if anchor is None:
            continue
        round_value = _round_number(summary)
        explain_samples = (
            _explain_samples(summary)
            or explain_backfills.get(round_value, [])
            or _explain_samples(anchor)
        )
        outer_loop.append({
            "iter": _round_number(summary),
            "params": anchor.get("params") or {},
            "qps": _float(anchor.get("db_qps")),
            "latency_p99_ms": _float(anchor.get("p99_ms")),
            "latency_p95_ms": _float(anchor.get("p95_ms")),
            "resource": _resource_fraction(
                anchor.get("resources") if isinstance(anchor.get("resources"), dict) else {},
                cpu_cores=args.cpu_cores,
                mem_gb=args.mem_gb,
                disk_bw_mb=args.disk_bw_mb,
            ),
            "utility": _utility(anchor),
            "explain_samples": explain_samples,
        })

    estimated_scores = [score for score in (_estimated_utility(row) for row in candidates) if score is not None]
    estimated_best = max(estimated_scores) if estimated_scores else None
    candidate_elapsed_s = _inner_loop_wall_time_s(candidates)
    best_utility = _utility(best) if best is not None else None
    qps_before = _float(baseline.get("db_qps")) if baseline is not None else None
    qps_after = _float(best.get("db_qps")) if best is not None else None
    p99_before = _float(baseline.get("p99_ms")) if baseline is not None else None
    p99_after = _float(best.get("p99_ms")) if best is not None else None
    validation_errors = len(authoritative_candidate_attempts) - len(candidates)
    failed_attempt_rows = [row for row in all_candidate_rows if row.get("status") != "ok"]
    round_counts: Dict[int, int] = {}
    candidate_keys: List[Tuple[int, int]] = []
    for row in candidates:
        round_value = _round_number(row)
        if round_value is not None:
            round_counts[round_value] = round_counts.get(round_value, 0) + 1
            candidate_keys.append(_candidate_key(row))
    expected_per_round = args.expected_candidates_per_round
    complete_rounds = [round_value for round_value, count in round_counts.items() if count == expected_per_round]
    duplicate_candidate_keys = len(candidate_keys) - len(set(candidate_keys))
    explain_sample_rounds = sorted({
        round_value
        for round_value in (
            [
                _round_number(summary)
                for summary in summaries
                if _explain_samples(summary)
            ]
            + list(explain_backfills)
        )
        if round_value is not None
    })

    total_wall_time_s = end_ts - start_ts if start_ts is not None and end_ts is not None else None
    delta_u_pct = best_utility * 100.0 if best_utility is not None else None
    baseline_resources = (
        baseline.get("resources")
        if baseline is not None and isinstance(baseline.get("resources"), dict)
        else {}
    )
    best_resources = (
        best.get("resources")
        if best is not None and isinstance(best.get("resources"), dict)
        else {}
    )
    return {
        "meta": {
            "exp_id": args.exp_id,
            "benchmark": args.benchmark,
            "db": args.db,
            "method": args.method,
            "bottleneck": args.bottleneck,
            "bottleneck_target": args.bottleneck_target,
            "repeat_id": args.repeat_id,
            "machine_spec": {
                "cpu_cores": args.cpu_cores,
                "mem_gb": args.mem_gb,
                "disk_bw_mb": args.disk_bw_mb,
            },
        },
        "outer_loop": outer_loop,
        "inner_loop_summary": {
            "total_iterations": len(candidates),
            "wall_time_ms": round(candidate_elapsed_s * 1000.0),
            "best_estimated_utility": estimated_best,
            "best_params": best.get("params") if best is not None else {},
        },
        "result": {
            "final_params": best.get("params") if best is not None else {},
            "delta_u_pct": delta_u_pct,
            "qps_before": qps_before,
            "qps_after": qps_after,
            "p99_before_ms": p99_before,
            "p99_after_ms": p99_after,
            "p95_before_ms": _float(baseline.get("p95_ms")) if baseline is not None else None,
            "p95_after_ms": _float(best.get("p95_ms")) if best is not None else None,
            "resource_before": _resource_fraction(
                baseline_resources,
                cpu_cores=args.cpu_cores,
                mem_gb=args.mem_gb,
                disk_bw_mb=args.disk_bw_mb,
            ),
            "resource_after": _resource_fraction(
                best_resources,
                cpu_cores=args.cpu_cores,
                mem_gb=args.mem_gb,
                disk_bw_mb=args.disk_bw_mb,
            ),
            "bad_config": (
                (not complete)
                or validation_errors > 0
                or duplicate_candidate_keys > 0
                or best is None
            ),
            "converge_outer_iters": len(summaries),
            "total_wall_time_s": total_wall_time_s,
        },
        "audit": {
            "source_jsonl": str(args.input.resolve()),
            "validation_backend": args.validation_backend,
            "search_complete": complete,
            "candidate_rows": len(all_candidate_rows),
            "candidate_attempt_rows": len(all_candidate_rows),
            "authoritative_candidate_rows": len(candidates),
            "validation_errors": validation_errors,
            "failed_attempt_rows": len(failed_attempt_rows),
            "superseded_attempt_rows": len(all_candidate_rows) - len(authoritative_candidate_attempts),
            "best_candidate_label": best.get("label") if best is not None else None,
            "best_candidate_worker_key": _worker_key(best) if best is not None else "",
            "matched_baseline_label": baseline.get("label") if baseline is not None else None,
            "matched_baseline_worker_key": _worker_key(baseline) if baseline is not None else "",
            "duplicate_candidate_keys": duplicate_candidate_keys,
            "expected_candidates_per_round": expected_per_round,
            "round_candidate_counts": {str(key): value for key, value in sorted(round_counts.items())},
            "complete_rounds": sorted(complete_rounds),
            "explain_sample_rounds": [value for value in explain_sample_rounds if value is not None],
            "delta_u_basis": "real_score_b_relative_to_zero_baseline",
            "resource_units": "fractions_of_machine_spec",
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--exp-id", required=True)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--db", default="pg17")
    parser.add_argument("--method", default="brute_force_grid_search")
    parser.add_argument("--bottleneck", required=True)
    parser.add_argument(
        "--bottleneck-target",
        type=float,
        default=None,
        help="0-1 null",
    )
    parser.add_argument("--repeat-id", type=int, required=True)
    parser.add_argument("--cpu-cores", type=float, required=True)
    parser.add_argument("--mem-gb", type=float, required=True)
    parser.add_argument("--disk-bw-mb", type=float, required=True)
    parser.add_argument("--validation-backend", required=True)
    parser.add_argument("--expected-candidates-per-round", type=int, default=128)
    parser.add_argument("--allow-incomplete", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = _read_jsonl(args.input)
    document = build_document(args, rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(str(args.output), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

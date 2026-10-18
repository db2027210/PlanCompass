#!/usr/bin/env python3
"""Fail closed unless one exported full-real grid-search artifact is complete."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List


COST_PARAMS = {
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
}
REQUIRED = {
    "meta": {
        "exp_id", "benchmark", "db", "method", "bottleneck",
        "bottleneck_target", "repeat_id", "machine_spec",
    },
    "inner_loop_summary": {
        "total_iterations", "wall_time_ms", "best_estimated_utility", "best_params",
    },
    "result": {
        "final_params", "delta_u_pct", "qps_before", "qps_after",
        "p99_before_ms", "p99_after_ms", "bad_config",
        "converge_outer_iters", "total_wall_time_s",
    },
}
OUTER_FIELDS = {
    "iter", "params", "qps", "latency_p99_ms", "latency_p95_ms",
    "resource", "utility", "explain_samples",
}


def finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--expected-candidates-per-round", type=int, default=128)
    parser.add_argument("--min-rounds", type=int, default=2)
    parser.add_argument("--max-bottleneck-drop-pct", type=float, default=30.0)
    parser.add_argument("--allow-empty-explain", action="store_true")
    parser.add_argument("--require-none-all-resources-up", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data = json.loads(args.artifact.read_text(encoding="utf-8"))
    errors: List[str] = []
    for section, fields in REQUIRED.items():
        value = data.get(section)
        if not isinstance(value, dict):
            errors.append(f"missing_object:{section}")
            continue
        missing = sorted(fields - set(value))
        errors.extend(f"missing_field:{section}.{field}" for field in missing)

    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    inner = data.get("inner_loop_summary") if isinstance(data.get("inner_loop_summary"), dict) else {}
    audit = data.get("audit") if isinstance(data.get("audit"), dict) else {}
    outer = data.get("outer_loop") if isinstance(data.get("outer_loop"), list) else []

    if meta.get("db") != "pg17":
        errors.append("meta.db_not_pg17")
    if meta.get("method") != "brute_force_grid_search":
        errors.append("meta.method_not_brute_force_grid_search")
    if not isinstance(meta.get("machine_spec"), dict):
        errors.append("meta.machine_spec_missing")
    if audit.get("search_complete") is not True:
        errors.append("audit.search_complete_false")
    if int(audit.get("validation_errors") or 0) != 0:
        errors.append("audit.validation_errors_nonzero")
    if int(audit.get("duplicate_candidate_keys") or 0) != 0:
        errors.append("audit.duplicate_candidate_keys_nonzero")
    if result.get("bad_config") is not False:
        errors.append("result.bad_config_not_false")

    round_counts_raw = audit.get("round_candidate_counts")
    round_counts: Dict[str, int] = round_counts_raw if isinstance(round_counts_raw, dict) else {}
    if len(round_counts) < args.min_rounds:
        errors.append(f"complete_round_count_below_{args.min_rounds}")
    for round_number, count in sorted(round_counts.items()):
        if int(count) != args.expected_candidates_per_round:
            errors.append(f"round_{round_number}_candidate_count_{count}")
    total_candidates = sum(int(value) for value in round_counts.values())
    if int(inner.get("total_iterations") or -1) != total_candidates:
        errors.append("inner_loop_summary.total_iterations_mismatch")
    if int(result.get("converge_outer_iters") or -1) != len(outer):
        errors.append("result.converge_outer_iters_mismatch")

    if len(outer) < args.min_rounds:
        errors.append(f"outer_loop_count_below_{args.min_rounds}")
    outer_iters: List[int] = []
    for index, row in enumerate(outer, start=1):
        if not isinstance(row, dict):
            errors.append(f"outer_loop_{index}_not_object")
            continue
        missing = sorted(OUTER_FIELDS - set(row))
        errors.extend(f"missing_field:outer_loop[{index}].{field}" for field in missing)
        try:
            outer_iters.append(int(row.get("iter")))
        except (TypeError, ValueError):
            errors.append(f"outer_loop_{index}_iter_invalid")
        if not args.allow_empty_explain and not row.get("explain_samples"):
            errors.append(f"outer_loop_{index}_explain_samples_empty")
    if len(outer_iters) != len(set(outer_iters)):
        errors.append("outer_loop_duplicate_iters")
    expected_outer_iters = {int(value) for value in round_counts}
    if set(outer_iters) != expected_outer_iters:
        errors.append("outer_loop_iters_do_not_match_round_counts")

    final_params = result.get("final_params") if isinstance(result.get("final_params"), dict) else {}
    if set(final_params) != COST_PARAMS:
        errors.append("result.final_params_not_exactly_seven_cost_params")
    qps_before = result.get("qps_before")
    qps_after = result.get("qps_after")
    if not finite(qps_before) or not finite(qps_after) or float(qps_after) <= float(qps_before):
        errors.append("result.db_qps_did_not_increase")

    before = result.get("resource_before") if isinstance(result.get("resource_before"), dict) else {}
    after = result.get("resource_after") if isinstance(result.get("resource_after"), dict) else {}
    bottleneck = str(meta.get("bottleneck") or "")
    if bottleneck == "none":
        up = [
            key for key in ("cpu", "mem", "io")
            if finite(before.get(key)) and finite(after.get(key)) and float(after[key]) > float(before[key])
        ]
        if args.require_none_all_resources_up and len(up) != 3:
            errors.append("result.none_not_all_three_resources_up")
        elif not up:
            errors.append("result.none_no_resource_increased")
    else:
        key = "mem" if bottleneck in {"memory", "mem"} else bottleneck
        if not finite(before.get(key)) or not finite(after.get(key)):
            errors.append(f"result.{key}_resource_missing")
        elif float(after[key]) >= float(before[key]):
            errors.append(f"result.{key}_bottleneck_did_not_drop")
        elif float(before[key]) > 0.0:
            drop_pct = (float(before[key]) - float(after[key])) / float(before[key]) * 100.0
            if drop_pct > args.max_bottleneck_drop_pct:
                errors.append(f"result.{key}_drop_too_extreme_{drop_pct:.6f}")

    payload = {
        "artifact": str(args.artifact.resolve()),
        "ok": not errors,
        "errors": errors,
        "exp_id": meta.get("exp_id"),
        "benchmark": meta.get("benchmark"),
        "bottleneck": bottleneck,
        "rounds": len(round_counts),
        "candidates": total_candidates,
        "qps_before": qps_before,
        "qps_after": qps_after,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())

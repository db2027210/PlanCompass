#!/usr/bin/env python3
"""Audit Bonect phase-2 search-space invariants without launching benchmarks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "shell") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "shell"))

import run_pg_benchmark_bottleneck_matrix as matrix  # noqa: E402


SCENARIOS: List[Tuple[str, str]] = [
    ("cpu", "hybench_xp"),
    ("memory", "hybench_ap"),
    ("io", "hybench_xp"),
    ("none", "hybench_xp"),
]

REQUIRED_ANCHOR_PATTERNS: Dict[str, List[str]] = {
    "cpu": [
        "postgres_default",
        "phase1_cpu_primary_accepted_default_anchor",
        "phase2_v44_cpu_dbqps_amp_goal_hit",
        "phase2_v80_cpu_dbqps_amp_goal_hit",
        "external_binary_hyxp_cpu_r01_c118_high_dbqps_weak_relief_anchor",
        "external_binary_hyxp_cpu_r02_c024_high_dbqps_anchor",
        "external_binary_hyxp_cpu_r02_c079_resource_drop_anchor",
        "external_binary_hyxp_cpu_r02_c080_balanced_anchor",
        "external_binary_hyxp_cpu_r03_c112_dbqps_anchor",
        "external_binary_hyxp_cpu_r03_c120_balanced_near_target_anchor",
        "external_binary_hyxp_cpu_r02_c123_resource_drop_anchor",
        "external_binary_hyxp_cpu_r04_c025_high_dbqps_weak_relief_anchor",
        "external_binary_hyxp_cpu_r04_c026_high_dbqps_anchor",
        "external_binary_hyxp_cpu_r04_c027_current_best_dbqps_anchor",
        "external_binary_hyxp_cpu_r04_c029_balanced_boundary_anchor",
        "external_binary_hyxp_cpu_r04_c037_weak_relief_boundary_anchor",
        "external_binary_hyxp_cpu_r04_c043_weak_relief_boundary_anchor",
        "external_binary_hyxp_cpu_r04_c120_high_dbqps_weak_relief_anchor",
        "result_bonect_cpu_iter11_safe_operator_projection",
    ],
    "memory": [
        "postgres_default",
        "phase1_memory_primary_accepted_default_anchor",
        "phase2_v105_memory_dbqps_amp_goal_hit",
        "phase2_v111_memory_dbqps_amp_goal_hit",
        "phase2_v109_memory_resource_stronger_goal_hit",
        "result_bonect_mem_iter13_strict_good",
    ],
    "io": [
        "postgres_default",
        "phase1_io_primary_accepted_default_anchor",
        "result_bonect_io_iter16_strict_good",
        "result_bonect_io_finetune_iter04_dbqps_io_goal_hit",
    ],
    "none": [
        "postgres_default",
        "phase1_none_primary_accepted_default_anchor",
        "robust_v5_none_low_random_extra_label_rank1",
        "robust_v5_none_estimator_top1",
        "robust_v5_none_estimator_top2_path_anchor",
    ],
}


def _dummy_grid_args(discrete_count: int) -> SimpleNamespace:
    return SimpleNamespace(
        bruteforce_param_discrete_count=discrete_count,
        bruteforce_param_step=[],
        bruteforce_param_values=[],
        bonect_param_range=[],
    )


def _name_set(anchors: List[Dict[str, Any]]) -> set[str]:
    return {str(item.get("name") or "") for item in anchors if isinstance(item, dict)}


def _target_policy(bottleneck: str) -> str:
    return "overall_up" if bottleneck == "none" else "bottleneck_down_any_other_up"


def audit_scenario(bottleneck: str, benchmark: str, discrete_count: int) -> Dict[str, Any]:
    active_params = (
        matrix.bonect_active_params(bottleneck, benchmark)
        or list(matrix.COST_PARAM_KEYS)
    )
    anchors = matrix.bonect_reference_path_anchors(bottleneck, benchmark)
    names = _name_set(anchors)
    known_real = [
        item for item in anchors
        if isinstance(item, dict) and bool(item.get("phase2_known_real_direction_seed"))
    ]
    known_real_names = [str(item.get("name") or "") for item in known_real]
    ranges = matrix.apply_bruteforce_grid_overrides(
        matrix.expand_bonect_param_ranges_with_references(
            bottleneck,
            benchmark,
            matrix.bonect_param_ranges(bottleneck, benchmark),
        ),
        _dummy_grid_args(discrete_count),
    )
    ranges = ranges or {}
    range_keys = sorted(str(key) for key in ranges.keys())
    required_names = REQUIRED_ANCHOR_PATTERNS.get(bottleneck, [])
    missing_required = [name for name in required_names if name not in names]
    wrong_known_target = [
        {
            "name": item.get("name"),
            "target": item.get("phase2_known_real_target_resource"),
        }
        for item in known_real
        if str(item.get("phase2_known_real_target_resource") or "") != bottleneck
    ]
    range_summary = {
        name: {
            "min_value": ranges.get(name, {}).get("min_value"),
            "max_value": ranges.get(name, {}).get("max_value"),
            "discrete_count": ranges.get(name, {}).get("discrete_count"),
        }
        for name in matrix.COST_PARAM_KEYS
    }
    checks = {
        "active_exactly_7_cost_params": (
            len(active_params) == 7 and set(active_params) == set(matrix.COST_PARAM_KEYS)
        ),
        "range_exactly_7_cost_params": (
            len(range_keys) == 7 and set(range_keys) == set(matrix.COST_PARAM_KEYS)
        ),
        "all_final_discrete_count": all(
            ranges.get(name, {}).get("discrete_count") == discrete_count
            for name in matrix.COST_PARAM_KEYS
        ),
        "postgres_default_anchor_present": "postgres_default" in names,
        "required_anchor_patterns_present": not missing_required,
        "known_real_targets_match_scenario": not wrong_known_target,
        "has_known_real_seed": len(known_real) >= 1,
    }
    return {
        "scenario": bottleneck,
        "benchmark": benchmark,
        "resource_direction_policy": _target_policy(bottleneck),
        "active_params": active_params,
        "anchor_count": len(anchors),
        "known_real_count": len(known_real),
        "known_real_names": known_real_names,
        "required_anchor_names": required_names,
        "missing_required_anchor_names": missing_required,
        "wrong_known_real_targets": wrong_known_target,
        "range_keys": range_keys,
        "range_summary": range_summary,
        "checks": checks,
        "pass": all(checks.values()),
    }


def build_report(discrete_count: int) -> Tuple[Dict[str, Any], str]:
    scenario_reports = [
        audit_scenario(bottleneck, benchmark, discrete_count)
        for bottleneck, benchmark in SCENARIOS
    ]
    required_checks = {
        "all_scenarios_present": len(scenario_reports) == len(SCENARIOS),
        "all_scenarios_pass_static_search_space_audit": all(
            item.get("pass") for item in scenario_reports
        ),
        "all_use_exactly_7_cost_params": all(
            item["checks"]["active_exactly_7_cost_params"]
            and item["checks"]["range_exactly_7_cost_params"]
            for item in scenario_reports
        ),
        "all_final_discrete_count_matches": all(
            item["checks"]["all_final_discrete_count"]
            for item in scenario_reports
        ),
    }
    result = {
        "discrete_count": discrete_count,
        "candidate_top_k_expected": 20,
        "candidate_replay_top_k_expected": 20,
        "scenarios": scenario_reports,
        "required_checks": required_checks,
        "pass": all(required_checks.values()),
    }
    lines = [
        "# Bonect Phase-2 Search-Space Audit",
        "",
        f"- expected candidate_top_k: `20`",
        f"- expected candidate_replay_top_k: `20`",
        f"- expected per-parameter discrete_count: `{discrete_count}`",
        f"- overall pass: `{result['pass']}`",
        "",
        "## Required Checks",
    ]
    for name, ok in required_checks.items():
        lines.append(f"- `{name}`: `{ok}`")
    lines.extend(
        [
            "",
            "## Scenario Summary",
            "",
            "| Scenario | Benchmark | Pass | Anchors | Known-real | Policy | Missing required anchors |",
            "|---|---|---:|---:|---:|---|---|",
        ]
    )
    for item in scenario_reports:
        missing = ", ".join(item["missing_required_anchor_names"]) or ""
        lines.append(
            "| {scenario} | {benchmark} | `{passed}` | {anchors} | {known} | `{policy}` | {missing} |".format(
                scenario=item["scenario"],
                benchmark=item["benchmark"],
                passed=item["pass"],
                anchors=item["anchor_count"],
                known=item["known_real_count"],
                policy=item["resource_direction_policy"],
                missing=missing,
            )
        )
    for item in scenario_reports:
        lines.extend(
            [
                "",
                f"## {item['scenario']} / {item['benchmark']}",
                "",
                f"- pass: `{item['pass']}`",
                f"- active_params: `{', '.join(item['active_params'])}`",
                f"- known_real_names: `{', '.join(item['known_real_names'])}`",
                f"- missing_required_anchor_names: `{', '.join(item['missing_required_anchor_names'])}`",
                "",
                "| Parameter | min | max | discrete_count |",
                "|---|---:|---:|---:|",
            ]
        )
        for name in matrix.COST_PARAM_KEYS:
            data = item["range_summary"].get(name) or {}
            lines.append(
                f"| `{name}` | {data.get('min_value')} | {data.get('max_value')} | {data.get('discrete_count')} |"
            )
    lines.append("")
    return result, "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--discrete-count", type=int, default=20)
    parser.add_argument("--json-output", default="")
    parser.add_argument("--md-output", default="")
    args = parser.parse_args()
    result, markdown = build_report(max(2, int(args.discrete_count)))
    if args.json_output:
        path = Path(args.json_output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.md_output:
        path = Path(args.md_output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("pass") else 2


if __name__ == "__main__":
    raise SystemExit(main())

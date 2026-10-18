#!/usr/bin/env python3
"""Generate a Bonect phase-2 scenario handoff plan.

The plan is intentionally non-executing. It records the commands and evidence
checks needed to continue CPU/Memory/IO/None phase-2 runs after the current
benchmark slot becomes available.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_DIR = PROJECT_ROOT / "report" / "bonect"
SCENARIO_BENCHMARKS: Dict[str, str] = {
    "cpu": "hybench_xp",
    "memory": "hybench_ap",
    "io": "hybench_xp",
    "none": "hybench_xp",
}


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_error": str(exc)}


def _scenario_targets(scenario: str) -> Dict[str, Any]:
    if scenario in {"cpu", "none"}:
        db_target = 10.0
    else:
        db_target = 5.0
    if scenario == "none":
        direction = "DB QPS up and CPU/Memory/IO overall up"
        resource_target = "overall resource utilization up"
    else:
        direction = f"{scenario.upper()} down and at least one non-target resource up"
        resource_target = "target resource down >=5pp preferred, >=2pp secondary"
    return {
        "db_qps_delta_target_pct": db_target,
        "resource_direction": direction,
        "resource_improvement_target": resource_target,
    }


def _run_id(prefix: str, scenario: str, ts: str) -> str:
    return f"{prefix}_{scenario}_{ts}"


def _runner_command(run_id: str, scenario: str) -> List[str]:
    cmd = [
        "python3",
        "shell/run_pg_benchmark_bottleneck_matrix.py",
        "--scenario-set",
        "bonect",
        "--bonect-bottlenecks",
        scenario,
        "--run-id",
        run_id,
        "--run",
        "--use-resource-estimator",
        "--optimizer-mode",
        "bruteforce",
        "--bruteforce-candidate-top-k",
        "20",
        "--bruteforce-candidate-replay-top-k",
        "20",
        "--bruteforce-param-discrete-count",
        "20",
        "--bruteforce-range-update-strategy",
        "binary_interval",
        "--final-validation",
        "--fail-fast",
    ]
    if scenario == "memory":
        cmd.extend(["--vm-memory-mem", "3G", "--bonect-memory-ap-clients", "8"])
    return cmd


def _command_text(cmd: List[str]) -> str:
    return " ".join(cmd)


def _scenario_plan(
    scenario: str,
    run_id: str,
    search_space: Dict[str, Any],
) -> Dict[str, Any]:
    benchmark = SCENARIO_BENCHMARKS[scenario]
    scenario_audit = {}
    for item in search_space.get("scenarios", []):
        if item.get("scenario") == scenario:
            scenario_audit = item
            break
    runner_cmd = _runner_command(run_id, scenario)
    return {
        "scenario": scenario,
        "benchmark": benchmark,
        "run_id": run_id,
        "targets": _scenario_targets(scenario),
        "search_space_static_audit": {
            "pass": scenario_audit.get("pass"),
            "anchor_count": scenario_audit.get("anchor_count"),
            "known_real_count": scenario_audit.get("known_real_count"),
            "known_real_names": scenario_audit.get("known_real_names"),
            "checks": scenario_audit.get("checks"),
        },
        "commands": {
            "prepare_reference": [
                "python3",
                "shell/prepare_bonect_reference.py",
                "--run-id",
                run_id,
            ],
            "runner": runner_cmd,
            "supervisor": [
                "python3",
                "shell/bonect_supervisor.py",
                "--run-id",
                run_id,
                "--poll-sec",
                "60",
                "--stop-on-trigger",
            ],
            "verify": [
                "python3",
                "shell/verify_bonect_phase2_run.py",
                "--run-id",
                run_id,
                "--bottleneck",
                scenario,
                "--benchmark",
                benchmark,
            ],
        },
        "runtime_evidence_required": [
            "bonect_reference_summary.md/json exists after prepare_reference",
            "runner.pid and supervisor.pid exist and are alive after launch",
            "baseline_runtime_resource_summary and baseline_top_sql events exist",
            "candidate_surrogate_probe_start/end events exist",
            "surrogate selected_backend is torch_adam_autograd",
            "bruteforce_initial_topk_replay_pruned has prefix_count=20 and no pre-real label pruning",
            "small_loop_ranking_goal_summary shows pairwise inversion <=0.1",
            "small_loop_ranking_goal_summary shows at least 3 long-term-goal hits when feasible",
            "final_feedback_best_primary_by_db_qps_updated or round report records best-so-far",
            "phase2 round report records DB QPS/resource deltas and continue/stop decision",
        ],
    }


def build_plan(args: argparse.Namespace) -> Dict[str, Any]:
    search_space = _read_json(Path(args.search_space_audit))
    current_status = _read_json(Path(args.current_status_json))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    scenarios = [item.strip() for item in args.scenarios.split(",") if item.strip()]
    plans = []
    for scenario in scenarios:
        if scenario not in SCENARIO_BENCHMARKS:
            raise ValueError(f"unknown scenario {scenario!r}")
        run_id = _run_id(args.run_id_prefix, scenario, ts)
        plans.append(_scenario_plan(scenario, run_id, search_space))
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "purpose": "Non-executing handoff plan for phase-2 scenario continuation.",
        "current_status_summary": {
            "current_bonect": current_status.get("current_bonect"),
            "effective_wait_state": current_status.get("effective_wait_state"),
            "external_current_round": (current_status.get("external") or {}).get("current_round"),
            "external_current_round_validated": (current_status.get("external") or {}).get(
                "current_round_validated"
            ),
            "external_started_rounds": (current_status.get("external") or {}).get(
                "started_rounds"
            ),
            "external_completed_rounds": (current_status.get("external") or {}).get(
                "completed_rounds"
            ),
            "external_accepted_count": (current_status.get("external") or {}).get("accepted_count"),
        },
        "global_preconditions": [
            "Do not launch a new scenario while the current Bonect runner is alive.",
            "Do not launch while external /disk2 binary_plan_tuning_hybenchxp parent/runxp jobs exist.",
            "For any new workload/profile, retrain/check CPU/Memory/IO resource models before trusting ranking.",
            "Run prepare_bonect_reference.py before runner launch.",
            "Start bonect_supervisor.py immediately after runner launch.",
            "Read and act on any control_decision_*.json/md before continuing.",
        ],
        "scenario_order": scenarios,
        "plans": plans,
    }


def markdown(plan: Dict[str, Any]) -> str:
    lines = [
        "# Bonect Phase-2 Handoff Plan",
        "",
        f"- generated_at: `{plan['generated_at']}`",
        "- type: non-executing plan",
        "",
        "## Current Status",
    ]
    current = plan.get("current_status_summary") or {}
    for key, value in current.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Global Preconditions"])
    for item in plan.get("global_preconditions", []):
        lines.append(f"- {item}")
    for item in plan.get("plans", []):
        lines.extend(
            [
                "",
                f"## {item['scenario']} / {item['benchmark']}",
                "",
                f"- run_id: `{item['run_id']}`",
                f"- DB QPS target: `{item['targets']['db_qps_delta_target_pct']}%`",
                f"- resource direction: `{item['targets']['resource_direction']}`",
                f"- resource target: `{item['targets']['resource_improvement_target']}`",
                f"- static search-space pass: `{item['search_space_static_audit'].get('pass')}`",
                f"- anchor_count: `{item['search_space_static_audit'].get('anchor_count')}`",
                f"- known_real_count: `{item['search_space_static_audit'].get('known_real_count')}`",
                f"- known_real_names: `{', '.join(item['search_space_static_audit'].get('known_real_names') or [])}`",
                "",
                "### Commands",
                "",
                "```bash",
                _command_text(item["commands"]["prepare_reference"]),
                _command_text(item["commands"]["runner"]),
                _command_text(item["commands"]["supervisor"]),
                _command_text(item["commands"]["verify"]),
                "```",
                "",
                "### Runtime Evidence Required",
            ]
        )
        for evidence in item.get("runtime_evidence_required", []):
            lines.append(f"- {evidence}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenarios", default="cpu,io,none")
    parser.add_argument("--run-id-prefix", default="pg_bonect_phase2_dbqps_amp_binary_top20_prune")
    parser.add_argument(
        "--search-space-audit",
        default=str(DEFAULT_REPORT_DIR / "phase2_anchor_search_space_audit_latest.json"),
    )
    parser.add_argument(
        "--current-status-json",
        default=str(DEFAULT_REPORT_DIR / "phase2_external_hyxp_status_latest.json"),
    )
    parser.add_argument(
        "--json-output",
        default=str(DEFAULT_REPORT_DIR / "phase2_handoff_plan_latest.json"),
    )
    parser.add_argument(
        "--md-output",
        default=str(DEFAULT_REPORT_DIR / "phase2_handoff_plan_latest.md"),
    )
    args = parser.parse_args()
    plan = build_plan(args)
    json_path = Path(args.json_output)
    md_path = Path(args.md_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(markdown(plan), encoding="utf-8")
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

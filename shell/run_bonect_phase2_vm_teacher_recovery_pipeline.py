#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
SHELL_DIR = ROOT / "shell"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SHELL_DIR) not in sys.path:
    sys.path.insert(0, str(SHELL_DIR))

from phase2_teacher_recovery import normalized_param_distance  # noqa: E402
from run_bonect_phase2_vm_teacher_validation import (  # noqa: E402
    COST_PARAM_KEYS,
    DEFAULT_COST_PARAMS,
    HISTORY_ROOT,
    REPORT_ROOT,
    SCENARIO_ORDER,
    append_jsonl,
    find_control_decisions,
    fresh_run_id,
    now_text,
    read_json,
    resolve_project_path,
    validate_manifest,
    wait_for_current_worktree_idle,
)


RUNNER = ROOT / "shell" / "run_pg_benchmark_bottleneck_matrix.py"
SUPERVISOR = ROOT / "shell" / "bonect_supervisor.py"
DEFAULT_TEACHER_MANIFEST = (
    ROOT / "config" / "bonect_phase2_vm_teacher_validation_20260711.json"
)
DEFAULT_TEACHER_RECOVERY_CONFIG = (
    ROOT / "config" / "bonect_phase2_vm_teacher_recovery_20260711.json"
)
DEFAULT_ESTIMATOR_DIR = (
    REPORT_ROOT / "phase2_vm_teacher_neighborhood_v80_20260711"
)
DEFAULT_CAMPAIGN_DIR = (
    REPORT_ROOT / "phase2_vm_teacher_recovery_pipeline_v82_20260711"
)
BROAD_RANGES = {
    "seq_page_cost": {"min_value": 0.0001, "max_value": 100.0},
    "random_page_cost": {"min_value": 0.0001, "max_value": 20.0},
    "cpu_tuple_cost": {"min_value": 0.000001, "max_value": 1.0},
    "cpu_index_tuple_cost": {"min_value": 0.000001, "max_value": 1.0},
    "cpu_operator_cost": {"min_value": 0.000001, "max_value": 1.0},
    "parallel_setup_cost": {"min_value": 0.0, "max_value": 10000.0},
    "parallel_tuple_cost": {"min_value": 0.000001, "max_value": 10.0},
}


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


def latest_event(path: Path, event_name: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for record in iter_jsonl(path):
        if record.get("event") == event_name:
            result = record
    return result


def latest_history_path(run_id: str, scenario: str) -> Path:
    paths = sorted(
        (HISTORY_ROOT / run_id / scenario).glob("*_optimizer_history*.jsonl"),
        key=lambda path: (path.stat().st_mtime, path.name),
    )
    if not paths:
        raise RuntimeError(f"optimizer history missing for {run_id}/{scenario}")
    return paths[-1]


def generated_config_path(run_id: str, scenario: str) -> Path:
    return (
        ROOT
        / "config"
        / "generated"
        / "pg_benchmark_bottleneck_matrix"
        / run_id
        / scenario
        / "hybench_xp.json"
    )


def scenario_command(
    *,
    run_id: str,
    scenario: str,
    scenario_cfg: Mapping[str, Any],
    delta_artifact: Path,
    teacher_recovery_config: Optional[Path],
    answer_blind: bool,
    execute: bool,
) -> List[str]:
    if bool(teacher_recovery_config) == bool(answer_blind):
        raise RuntimeError(
            "exactly one recovery mode is required: teacher diagnostic or answer-blind"
        )
    vm = scenario_cfg.get("vm_profile") or {}
    capability = scenario_cfg.get("capability") or {}
    run_mins = max(1, int(scenario_cfg.get("run_mins", 2)))
    warmup_sec = max(0, int(scenario_cfg.get("validation_warmup_sec", 60)))
    monitor_warmup_sec = max(
        0,
        int(scenario_cfg.get("runtime_monitor_warmup_sec", 30)),
    )
    tp_clients = max(0, int(scenario_cfg.get("tp_clients", 1)))
    ap_clients = max(0, int(scenario_cfg.get("ap_clients", 1)))
    cmd = [
        sys.executable,
        str(RUNNER),
        "--scenario-set",
        "bonect",
        "--bonect-bottlenecks",
        scenario,
        "--run-id",
        run_id,
        "--python-executable",
        sys.executable,
        "--use-resource-estimator",
        "--apply-vm-profile",
        "--no-warmup",
        "--no-adaptive-bottleneck-calibration",
        "--hybench-run-mins",
        str(run_mins),
        "--hybench-tp-clients",
        str(tp_clients),
        "--hybench-ap-clients",
        str(ap_clients),
        "--hybench-ap-query-ids",
        str(scenario_cfg.get("ap_query_ids") or "6"),
        "--hybench-ap-query-weights",
        str(scenario_cfg.get("ap_query_weights") or "100"),
        "--hybench-ap-query-sequence",
        str(scenario_cfg.get("ap_query_sequence") or "6"),
        "--hybench-progress-windows",
        "10",
        "--hybench-statement-timeout-ms",
        str(int(scenario_cfg.get("statement_timeout_ms", 300000))),
        "--validation-warmup-timeout-sec",
        str(warmup_sec),
        "--runtime-monitor-warmup-sec",
        str(monitor_warmup_sec),
        "--runtime-monitor-interval-sec",
        "1",
        "--measured-workload-timeout-sec",
        str(run_mins * 60 + 180),
        f"--vm-{scenario}-cpu",
        str(int(vm["cpu"])),
        f"--vm-{scenario}-mem",
        str(vm["memory"]),
        f"--vm-{scenario}-read-mb",
        str(int(vm["read_mb"])),
        f"--vm-{scenario}-write-mb",
        str(int(vm["write_mb"])),
        "--vm-disk-dev",
        str(vm.get("disk_dev") or "vda"),
        "--bonect-active-params",
        ",".join(COST_PARAM_KEYS),
        "--bruteforce-max-iter",
        "24",
        "--bruteforce-early-stop-threshold",
        "24",
        "--bruteforce-candidate-top-k",
        "60",
        "--bruteforce-candidate-replay-top-k",
        "24" if answer_blind else "15",
        "--bruteforce-param-discrete-count",
        "5",
        "--bruteforce-range-shrink-factor",
        "0.5",
        "--bruteforce-range-update-strategy",
        "binary_interval",
        "--bruteforce-range-min-width-pct",
        "1.0",
        "--bonect-pair-probe-mode",
        "full-factorial",
        "--bonect-pair-probe-max-combination-size",
        "7",
        "--bonect-pair-probe-max-candidates",
        "78125",
        "--final-feedback-max-rounds",
        "24",
        "--phase2-real-delta-calibration-artifact",
        str(delta_artifact.resolve()),
        "--phase2-real-delta-min-plan-feature-coverage",
        "0.80",
    ]
    for resource in ("cpu", "memory", "io"):
        cmd.extend(
            ["--bonect-capability", f"{resource}={float(capability[resource]):.17g}"]
        )
    if scenario == "cpu":
        cmd.extend(
            [
                "--bonect-cpu-xp-tp-clients",
                str(tp_clients),
                "--bonect-cpu-xp-ap-clients",
                str(ap_clients),
                "--bonect-cpu-xp-ap-query-ids",
                str(scenario_cfg.get("ap_query_ids") or "6"),
                "--bonect-cpu-xp-ap-query-weights",
                str(scenario_cfg.get("ap_query_weights") or "100"),
                "--bonect-cpu-xp-ap-query-sequence",
                str(scenario_cfg.get("ap_query_sequence") or "6"),
                "--bonect-cpu-xp-progress-windows",
                "10",
            ]
        )
    if scenario == "memory" and bool(scenario_cfg.get("bonect_memory_use_xp")):
        cmd.append("--bonect-memory-use-xp")
    if scenario == "none":
        cmd.append("--allow-noncanonical-none-profile")
    for section, raw_path in sorted(
        (scenario_cfg.get("statement_sql_files") or {}).items()
    ):
        cmd.extend(
            [
                "--hybench-statement-sql-file",
                f"{section}={resolve_project_path(str(raw_path))}",
            ]
        )
    for raw_path in scenario_cfg.get("setup_sql_files") or []:
        cmd.extend(
            ["--hybench-setup-sql-file", str(resolve_project_path(str(raw_path)))]
        )
    if answer_blind:
        cmd.extend(
            [
                "--phase2-blind-recovery",
                "--adaptive-replay-controller-enabled",
                "--adaptive-replay-controller-max-budget",
                "24",
                "--adaptive-replay-controller-min-evidence",
                "12",
                "--adaptive-replay-controller-batch-size",
                "6",
                "--adaptive-replay-controller-max-expansions",
                "2",
            ]
        )
    else:
        cmd.extend(
            ["--phase2-teacher-recovery-config", str(teacher_recovery_config)]
        )
    if execute:
        cmd.append("--run")
    forbidden_coordinate_flags = {
        "--bonect-initial-param",
        "--bonect-param-range",
        "--bruteforce-param-values",
        "--bruteforce-param-step",
    }
    leaked_flags = sorted(forbidden_coordinate_flags.intersection(cmd))
    if leaked_flags:
        raise RuntimeError(f"recovery command leaks coordinate overrides: {leaked_flags}")
    return cmd


def completed_scenarios(state_path: Path, event_name: str) -> set[str]:
    completed = set()
    for record in iter_jsonl(state_path):
        if record.get("event") == event_name and record.get("passed") is True:
            completed.add(str(record.get("scenario") or ""))
    return completed


def wait_for_estimator_gate(
    estimator_dir: Path,
    *,
    state_path: Path,
    poll_sec: float,
    timeout_sec: float,
) -> Dict[str, Any]:
    summary_path = estimator_dir / "estimator_training_summary.json"
    gate_state_path = estimator_dir / "estimator_gate_state.jsonl"
    started = time.monotonic()
    next_log = 0.0
    while True:
        if summary_path.is_file():
            summary = read_json(summary_path)
            if summary.get("all_passed") is True:
                return summary
            raise RuntimeError(
                "estimator training completed but did not pass all four scenarios: "
                + json.dumps(summary.get("training_gates") or {}, ensure_ascii=False)
            )
        gate_failure = latest_event(gate_state_path, "estimator_gate_failed")
        if gate_failure:
            raise RuntimeError(
                "estimator gate failed: "
                + json.dumps(gate_failure, ensure_ascii=False, sort_keys=True)
            )
        elapsed = time.monotonic() - started
        if timeout_sec > 0 and elapsed >= timeout_sec:
            raise RuntimeError("timed out waiting for estimator training gate")
        if time.monotonic() >= next_log:
            record = {
                "event": "waiting_for_estimator_training_gate",
                "timestamp": now_text(),
                "elapsed_sec": elapsed,
                "summary_path": str(summary_path),
            }
            append_jsonl(state_path, record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
            next_log = time.monotonic() + 300.0
        time.sleep(max(1.0, poll_sec))


def verify_blind_artifact(path: Path) -> Dict[str, Any]:
    artifact = read_json(path)
    deployment = artifact.get("blind_deployment") or {}
    coordinate_points = [
        point.get("point_name")
        for point in artifact.get("points") or []
        if isinstance(point, Mapping) and "params" in point
    ]
    passed = bool(
        (artifact.get("training_gate") or {}).get("passed")
        and deployment.get("selection_blind") is True
        and deployment.get("exact_teacher_coordinates_present") is False
        and "teacher_holdout" not in artifact
        and not coordinate_points
    )
    return {
        "passed": passed,
        "path": str(path),
        "deployment": deployment,
        "coordinate_points": coordinate_points,
    }


def start_supervisor(run_id: str, campaign_dir: Path) -> Tuple[subprocess.Popen[Any], Any]:
    log_path = campaign_dir / "logs" / f"{run_id}.supervisor.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("a", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            str(SUPERVISOR),
            "--run-id",
            run_id,
            "--poll-sec",
            "30",
            "--stop-on-trigger",
            "--state-file",
            str(campaign_dir / "supervisor" / f"{run_id}.json"),
        ],
        cwd=str(ROOT),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return process, log_handle


def stop_owned_supervisor(process: subprocess.Popen[Any], log_handle: Any) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    log_handle.close()


def run_with_supervisor(
    *,
    run_id: str,
    command: Sequence[str],
    campaign_dir: Path,
    state_path: Path,
) -> Tuple[int, List[str], Path]:
    log_path = campaign_dir / "logs" / f"{run_id}.runner.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log_handle:
        runner = subprocess.Popen(
            list(command),
            cwd=str(ROOT),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        supervisor, supervisor_log = start_supervisor(run_id, campaign_dir)
        reported_decisions: set[str] = set()
        try:
            while runner.poll() is None:
                decisions = [str(path) for path in find_control_decisions(run_id)]
                new_decisions = sorted(set(decisions) - reported_decisions)
                if new_decisions:
                    reported_decisions.update(new_decisions)
                    record = {
                        "event": "recovery_control_decision_detected",
                        "timestamp": now_text(),
                        "run_id": run_id,
                        "control_decisions": new_decisions,
                        "action": "supervisor_stop_on_trigger_then_halt_pipeline",
                    }
                    append_jsonl(state_path, record)
                    print(json.dumps(record, ensure_ascii=False), flush=True)
                time.sleep(10.0)
            returncode = int(runner.wait())
            time.sleep(2.0)
            decisions = sorted(str(path) for path in find_control_decisions(run_id))
        finally:
            stop_owned_supervisor(supervisor, supervisor_log)
    return returncode, decisions, log_path


def audit_teacher_rank_run(
    *,
    run_id: str,
    scenario: str,
    expected_round: int,
) -> Dict[str, Any]:
    history_path = latest_history_path(run_id, scenario)
    completion = latest_event(history_path, "teacher_recovery_rank_only_complete")
    audit = completion.get("audit") or {}
    config = read_json(generated_config_path(run_id, scenario))
    bruteforce = (config.get("optimizer") or {}).get("bruteforce") or {}
    blind = bruteforce.get("blind_recovery") or {}
    teacher_cfg = bruteforce.get("teacher_recovery_audit") or {}
    config_passed = bool(
        blind.get("teacher_holdout_diagnostic") is True
        and blind.get("teacher_coordinates_affect_selection") is False
        and bruteforce.get("force_best_replay_anchor_seed_records") == []
        and teacher_cfg.get("stop_after_natural_recall") is True
        and set(config.get("initial_params") or {}) == set(COST_PARAM_KEYS)
        and set(config.get("param_ranges") or {}) == set(COST_PARAM_KEYS)
    )
    passed = bool(
        completion
        and int(completion.get("round_index", -1)) == int(expected_round)
        and audit.get("exact_match") is True
        and audit.get("within_top_k") is True
        and config_passed
    )
    return {
        "passed": passed,
        "run_id": run_id,
        "scenario": scenario,
        "history_path": str(history_path),
        "completion": completion,
        "config_passed": config_passed,
        "closest_rank": (audit.get("closest") or {}).get("rank"),
        "candidate_count": audit.get("candidate_count"),
    }


def audit_blind_run(
    *,
    run_id: str,
    scenario: str,
    teacher_params: Mapping[str, Any],
) -> Dict[str, Any]:
    history_path = latest_history_path(run_id, scenario)
    final_summary = latest_event(history_path, "final_summary")
    config = read_json(generated_config_path(run_id, scenario))
    bruteforce = (config.get("optimizer") or {}).get("bruteforce") or {}
    blind = bruteforce.get("blind_recovery") or {}
    calibration = (
        (bruteforce.get("candidate_gate") or {}).get("real_delta_calibration") or {}
    )
    artifact_path = Path(str(calibration.get("artifact_path") or ""))
    artifact_gate = verify_blind_artifact(artifact_path) if artifact_path.is_file() else {
        "passed": False,
        "path": str(artifact_path),
    }
    initial_params = config.get("initial_params") or {}
    ranges = config.get("param_ranges") or {}
    range_passed = all(
        name in ranges
        and float(ranges[name].get("min_value")) == BROAD_RANGES[name]["min_value"]
        and float(ranges[name].get("max_value")) == BROAD_RANGES[name]["max_value"]
        for name in COST_PARAM_KEYS
    )
    config_passed = bool(
        blind.get("answer_blind") is True
        and blind.get("teacher_coordinates_loaded") is False
        and bruteforce.get("teacher_recovery_audit") == {}
        and bruteforce.get("force_best_replay_anchor_seed_records") == []
        and initial_params == DEFAULT_COST_PARAMS
        and range_passed
        and set(bruteforce.get("active_params") or []) == set(COST_PARAM_KEYS)
        and artifact_gate.get("passed") is True
    )
    strong = final_summary.get("final_strong_constraint_status") or {}
    final_effect = final_summary.get("final_effect_evaluation") or {}
    optimized_params = final_summary.get("optimized_params") or {}
    distance = (
        normalized_param_distance(optimized_params, teacher_params, BROAD_RANGES)
        if set(optimized_params) == set(COST_PARAM_KEYS)
        else {}
    )
    passed = bool(
        final_summary
        and strong.get("strong_goal_met") is True
        and final_effect.get("target_achieved") is True
        and config_passed
    )
    return {
        "passed": passed,
        "run_id": run_id,
        "scenario": scenario,
        "history_path": str(history_path),
        "config_passed": config_passed,
        "artifact_gate": artifact_gate,
        "strong_constraint_status": strong,
        "final_effect_evaluation": final_effect,
        "optimized_params": optimized_params,
        "teacher_distance": distance,
        "global_best_db_qps_fallback": final_summary.get(
            "final_feedback_global_best_db_qps_candidate"
        ),
    }


def write_summary(campaign_dir: Path, state_path: Path) -> Tuple[Path, Path]:
    diagnostics: Dict[str, Dict[str, Any]] = {}
    blind: Dict[str, Dict[str, Any]] = {}
    for record in iter_jsonl(state_path):
        scenario = str(record.get("scenario") or "")
        if record.get("event") == "teacher_rank_complete":
            diagnostics[scenario] = record
        elif record.get("event") == "blind_recovery_complete":
            blind[scenario] = record
    summary = {
        "generated_at": now_text(),
        "diagnostics": diagnostics,
        "blind_recovery": blind,
        "all_teacher_rank_passed": all(
            diagnostics.get(name, {}).get("passed") is True for name in SCENARIO_ORDER
        ),
        "all_blind_recovery_passed": all(
            blind.get(name, {}).get("passed") is True for name in SCENARIO_ORDER
        ),
    }
    json_path = campaign_dir / "summary.json"
    json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Phase2 VM Teacher ",
        "",
        "|  | Teacher  top15 |  |  | DB QPS  |  |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for scenario in SCENARIO_ORDER:
        diagnostic = diagnostics.get(scenario) or {}
        recovery = blind.get(scenario) or {}
        strong = recovery.get("strong_constraint_status") or {}
        lines.append(
            "| {scenario} | {diagnostic_passed} | {rank} | {blind_passed} | {db_gain} | {strong_passed} |".format(
                scenario=scenario,
                diagnostic_passed="" if diagnostic.get("passed") else "/",
                rank=diagnostic.get("closest_rank", "-"),
                blind_passed="" if recovery.get("passed") else "/",
                db_gain=(
                    f"{float(strong.get('db_qps_delta_pct')):+.2f}%"
                    if strong.get("db_qps_delta_pct") is not None
                    else "-"
                ),
                strong_passed="" if strong.get("strong_goal_met") else "/",
            )
        )
    lines.extend(
        [
            "",
            "DescriptionTeacher  teacher  replay PostgreSQL  teacher ",
            "",
        ]
    )
    md_path = campaign_dir / "summary.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run gated VM teacher top-k diagnostics and answer-blind recovery."
    )
    parser.add_argument("--teacher-manifest", default=str(DEFAULT_TEACHER_MANIFEST))
    parser.add_argument(
        "--teacher-recovery-config",
        default=str(DEFAULT_TEACHER_RECOVERY_CONFIG),
    )
    parser.add_argument("--estimator-dir", default=str(DEFAULT_ESTIMATOR_DIR))
    parser.add_argument("--campaign-dir", default=str(DEFAULT_CAMPAIGN_DIR))
    parser.add_argument("--poll-sec", type=float, default=60.0)
    parser.add_argument("--timeout-sec", type=float, default=0.0)
    parser.add_argument("--scenario", action="append", choices=[*SCENARIO_ORDER, "all"])
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    teacher_manifest_path = Path(args.teacher_manifest).expanduser().resolve()
    teacher_recovery_path = Path(args.teacher_recovery_config).expanduser().resolve()
    estimator_dir = Path(args.estimator_dir).expanduser().resolve()
    campaign_dir = Path(args.campaign_dir).expanduser().resolve()
    campaign_dir.mkdir(parents=True, exist_ok=True)
    state_path = campaign_dir / "state.jsonl"
    manifest = read_json(teacher_manifest_path)
    validate_manifest(manifest)
    recovery_manifest = read_json(teacher_recovery_path)
    requested = args.scenario or ["all"]
    scenarios = list(SCENARIO_ORDER) if "all" in requested else [
        name for name in SCENARIO_ORDER if name in requested
    ]
    resolved_manifest = {
        "campaign_id": campaign_dir.name,
        "created_at": now_text(),
        "teacher_manifest": str(teacher_manifest_path),
        "teacher_recovery_config": str(teacher_recovery_path),
        "estimator_dir": str(estimator_dir),
        "scenario_order": scenarios,
        "policy": {
            "teacher_rank_top_k": 15,
            "teacher_coordinates_affect_selection": False,
            "final_answer_blind": True,
            "initial_params": DEFAULT_COST_PARAMS,
            "initial_ranges": BROAD_RANGES,
            "range_update_strategy": "binary_interval",
            "values_per_active_param": 5,
            "max_rounds": 24,
            "cost_param_count": 7,
        },
    }
    (campaign_dir / "manifest.json").write_text(
        json.dumps(resolved_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    append_jsonl(
        state_path,
        {
            "event": "recovery_pipeline_start",
            "timestamp": now_text(),
            "execute": bool(args.execute),
            "scenarios": scenarios,
        },
    )
    estimator_summary = wait_for_estimator_gate(
        estimator_dir,
        state_path=state_path,
        poll_sec=args.poll_sec,
        timeout_sec=args.timeout_sec,
    )
    audit_artifacts = estimator_summary.get("audit_artifacts") or estimator_summary.get(
        "artifacts"
    ) or {}
    blind_artifacts = estimator_summary.get("blind_artifacts") or {}
    for scenario in scenarios:
        if scenario not in audit_artifacts or scenario not in blind_artifacts:
            raise RuntimeError(f"missing estimator artifacts for {scenario}")
        blind_gate = verify_blind_artifact(Path(blind_artifacts[scenario]))
        if not blind_gate.get("passed"):
            raise RuntimeError(
                f"blind artifact gate failed for {scenario}: "
                + json.dumps(blind_gate, ensure_ascii=False, sort_keys=True)
            )

    completed_rank = completed_scenarios(state_path, "teacher_rank_complete")
    for scenario in scenarios:
        if scenario in completed_rank:
            continue
        wait_for_current_worktree_idle(
            state_path=state_path,
            poll_sec=args.poll_sec,
            timeout_sec=args.timeout_sec,
        )
        run_id = fresh_run_id(
            f"pg_bonect_phase2_teacher_rank_v82_{scenario}_20260711"
        )
        command = scenario_command(
            run_id=run_id,
            scenario=scenario,
            scenario_cfg=(manifest.get("scenarios") or {})[scenario],
            delta_artifact=Path(audit_artifacts[scenario]),
            teacher_recovery_config=teacher_recovery_path,
            answer_blind=False,
            execute=args.execute,
        )
        append_jsonl(
            state_path,
            {
                "event": "teacher_rank_start",
                "timestamp": now_text(),
                "scenario": scenario,
                "run_id": run_id,
                "command": command,
            },
        )
        if not args.execute:
            print(" ".join(command), flush=True)
            continue
        returncode, decisions, log_path = run_with_supervisor(
            run_id=run_id,
            command=command,
            campaign_dir=campaign_dir,
            state_path=state_path,
        )
        if returncode != 0 or decisions:
            raise RuntimeError(
                f"teacher rank run failed: scenario={scenario}, returncode={returncode}, "
                f"control_decisions={decisions}, log={log_path}"
            )
        recovery_cfg = (recovery_manifest.get("scenarios") or {})[scenario]
        audit = audit_teacher_rank_run(
            run_id=run_id,
            scenario=scenario,
            expected_round=int(recovery_cfg.get("expected_candidate_round", 0)),
        )
        record = {
            "event": "teacher_rank_complete",
            "timestamp": now_text(),
            **audit,
        }
        append_jsonl(state_path, record)
        write_summary(campaign_dir, state_path)
        if not audit.get("passed"):
            raise RuntimeError(
                "teacher natural top15 recall failed; fix estimator/ranking before blind run: "
                + json.dumps(audit, ensure_ascii=False, sort_keys=True)
            )

    if not args.execute:
        write_summary(campaign_dir, state_path)
        return 0
    completed_rank = completed_scenarios(state_path, "teacher_rank_complete")
    if any(scenario not in completed_rank for scenario in scenarios):
        raise RuntimeError("not all requested teacher rank diagnostics passed")

    completed_blind = completed_scenarios(state_path, "blind_recovery_complete")
    for scenario in scenarios:
        if scenario in completed_blind:
            continue
        wait_for_current_worktree_idle(
            state_path=state_path,
            poll_sec=args.poll_sec,
            timeout_sec=args.timeout_sec,
        )
        run_id = fresh_run_id(
            f"pg_bonect_phase2_blind_recovery_v83_{scenario}_20260711"
        )
        command = scenario_command(
            run_id=run_id,
            scenario=scenario,
            scenario_cfg=(manifest.get("scenarios") or {})[scenario],
            delta_artifact=Path(blind_artifacts[scenario]),
            teacher_recovery_config=None,
            answer_blind=True,
            execute=True,
        )
        append_jsonl(
            state_path,
            {
                "event": "blind_recovery_start",
                "timestamp": now_text(),
                "scenario": scenario,
                "run_id": run_id,
                "command": command,
            },
        )
        returncode, decisions, log_path = run_with_supervisor(
            run_id=run_id,
            command=command,
            campaign_dir=campaign_dir,
            state_path=state_path,
        )
        if returncode != 0 or decisions:
            raise RuntimeError(
                f"blind recovery run failed: scenario={scenario}, returncode={returncode}, "
                f"control_decisions={decisions}, log={log_path}"
            )
        teacher_params = (
            (recovery_manifest.get("scenarios") or {})[scenario].get("teacher_params")
            or {}
        )
        audit = audit_blind_run(
            run_id=run_id,
            scenario=scenario,
            teacher_params=teacher_params,
        )
        record = {
            "event": "blind_recovery_complete",
            "timestamp": now_text(),
            **audit,
        }
        append_jsonl(state_path, record)
        write_summary(campaign_dir, state_path)
        if not audit.get("passed"):
            raise RuntimeError(
                "blind recovery did not meet the strong goal; diagnose ranking/model/ranges "
                "before continuing to the next scenario: "
                + json.dumps(audit, ensure_ascii=False, sort_keys=True)
            )

    json_path, md_path = write_summary(campaign_dir, state_path)
    append_jsonl(
        state_path,
        {
            "event": "recovery_pipeline_complete",
            "timestamp": now_text(),
            "summary_json": str(json_path),
            "summary_md": str(md_path),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

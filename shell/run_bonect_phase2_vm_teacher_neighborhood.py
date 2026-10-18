#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
SHELL_DIR = ROOT / "shell"
if str(SHELL_DIR) not in sys.path:
    sys.path.insert(0, str(SHELL_DIR))

from run_bonect_phase2_vm_teacher_validation import (  # noqa: E402
    COST_PARAM_KEYS,
    REPORT_ROOT,
    SCENARIO_ORDER,
    active_current_worktree_processes,
    append_jsonl,
    compact_result,
    find_control_decisions,
    fresh_run_id,
    normalized_params,
    now_text,
    read_json,
    resolve_project_path,
    runner_command,
    slug,
    telemetry_gate,
    validate_manifest,
    value_in_range,
    wait_for_current_worktree_idle,
)


DEFAULT_MANIFEST = (
    ROOT / "config" / "bonect_phase2_vm_teacher_neighborhood_20260711.json"
)


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


def params_signature(params: Mapping[str, Any]) -> Tuple[float, ...]:
    normalized = normalized_params(params)
    return tuple(round(normalized[name], 12) for name in COST_PARAM_KEYS)


def interpolate_params(
    default: Mapping[str, Any],
    teacher: Mapping[str, Any],
    step: float,
) -> Dict[str, float]:
    default_values = normalized_params(default)
    teacher_values = normalized_params(teacher)
    return {
        name: default_values[name]
        + (teacher_values[name] - default_values[name]) * float(step)
        for name in COST_PARAM_KEYS
    }


def build_scenario_points(
    *,
    scenario: str,
    default: Mapping[str, Any],
    teacher: Mapping[str, Any],
    sampling: Mapping[str, Any],
    binary_parent: Optional[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    default_values = normalized_params(default)
    teacher_values = normalized_params(teacher)
    changed = [
        name
        for name in COST_PARAM_KEYS
        if abs(default_values[name] - teacher_values[name]) > 1e-12
    ]
    raw_points: List[Dict[str, Any]] = []
    for raw_step in sampling.get("path_steps") or []:
        step = float(raw_step)
        if not 0.0 < step < 1.0:
            raise RuntimeError(f"invalid path step: {step}")
        raw_points.append(
            {
                "name": f"default_teacher_path_{step:g}",
                "kind": "teacher_path",
                "params": interpolate_params(default_values, teacher_values, step),
                "metadata": {"path_step": step},
            }
        )
    if bool(sampling.get("include_default_single_parameter_projections", True)):
        for name in changed:
            params = dict(default_values)
            params[name] = teacher_values[name]
            raw_points.append(
                {
                    "name": f"default_projection_{name}",
                    "kind": "default_single_parameter_projection",
                    "params": params,
                    "metadata": {"changed_param": name},
                }
            )
    if bool(sampling.get("include_teacher_single_parameter_ablations", True)):
        for name in changed:
            params = dict(teacher_values)
            params[name] = default_values[name]
            raw_points.append(
                {
                    "name": f"teacher_ablation_{name}",
                    "kind": "teacher_single_parameter_ablation",
                    "params": params,
                    "metadata": {"ablated_param": name},
                }
            )
    if isinstance(binary_parent, Mapping):
        raw_points.append(
            {
                "name": str(binary_parent.get("name") or "binary_parent"),
                "kind": "binary_parent",
                "params": normalized_params(binary_parent.get("params") or {}),
                "metadata": {},
            }
        )

    excluded = {params_signature(default_values), params_signature(teacher_values)}
    seen = set(excluded)
    points: List[Dict[str, Any]] = []
    for raw in raw_points:
        params = normalized_params(raw.get("params") or {})
        signature = params_signature(params)
        if signature in seen:
            continue
        seen.add(signature)
        points.append(
            {
                **raw,
                "scenario": scenario,
                "point_index": len(points),
                "params": params,
            }
        )
    return points


def validate_teacher_summary(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"teacher validation summary is missing: {path}")
    summary = read_json(path)
    evaluations = summary.get("evaluations") or {}
    failed = [
        scenario
        for scenario in SCENARIO_ORDER
        if not isinstance(evaluations.get(scenario), Mapping)
        or evaluations[scenario].get("strict_passed") is not True
    ]
    if summary.get("all_teachers_strict_passed") is not True or failed:
        raise RuntimeError(
            "teacher neighborhood sampling requires four strict VM teacher passes; "
            f"failed_or_missing={failed}"
        )
    return summary


def wait_for_teacher_summary(
    path: Path,
    *,
    state_path: Path,
    poll_sec: float,
    timeout_sec: float,
) -> Dict[str, Any]:
    started = time.monotonic()
    next_log = 0.0
    while True:
        try:
            return validate_teacher_summary(path)
        except RuntimeError as exc:
            if path.is_file():
                summary = read_json(path)
                if summary.get("all_scenarios_complete") is True:
                    raise
            elapsed = time.monotonic() - started
            if timeout_sec > 0 and elapsed >= timeout_sec:
                raise RuntimeError(
                    f"timed out waiting for strict teacher validation: {exc}"
                ) from exc
            if time.monotonic() >= next_log:
                record = {
                    "event": "waiting_for_teacher_validation",
                    "timestamp": now_text(),
                    "elapsed_sec": elapsed,
                    "summary_path": str(path),
                    "detail": str(exc),
                }
                append_jsonl(state_path, record)
                print(json.dumps(record, ensure_ascii=False), flush=True)
                next_log = time.monotonic() + 300.0
            time.sleep(max(1.0, poll_sec))


def load_completed_points(state_path: Path) -> Dict[Tuple[str, int], Dict[str, Any]]:
    completed: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for record in iter_jsonl(state_path):
        if record.get("event") != "teacher_neighborhood_point_complete":
            continue
        scenario = str(record.get("scenario") or "")
        try:
            point_index = int(record.get("point_index"))
        except (TypeError, ValueError):
            continue
        completed[(scenario, point_index)] = record
    return completed


def run_measurement(
    *,
    campaign_id: str,
    state_path: Path,
    scenario: str,
    scenario_cfg: Mapping[str, Any],
    role: str,
    name: str,
    params: Mapping[str, float],
    point_index: int,
    block_index: int,
) -> Dict[str, Any]:
    base_run_id = (
        f"pg_bonect_phase2_vm_neighborhood_v80_{scenario}_"
        f"b{block_index:02d}_{slug(role)}_{slug(name)}"
    )
    run_id = fresh_run_id(base_run_id)
    start = {
        "event": "teacher_neighborhood_measurement_start",
        "timestamp": now_text(),
        "campaign_id": campaign_id,
        "scenario": scenario,
        "role": role,
        "name": name,
        "point_index": point_index,
        "block_index": block_index,
        "run_id": run_id,
        "params": normalized_params(params),
    }
    append_jsonl(state_path, start)
    print(json.dumps(start, ensure_ascii=False), flush=True)
    proc = subprocess.run(
        runner_command(
            run_id=run_id,
            scenario=scenario,
            scenario_cfg=scenario_cfg,
            params=params,
            execute=True,
        ),
        cwd=str(ROOT),
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        check=False,
    )
    decisions = find_control_decisions(run_id)
    if decisions:
        record = {
            **start,
            "event": "teacher_neighborhood_control_decision_pending",
            "paths": [str(path) for path in decisions],
        }
        append_jsonl(state_path, record)
        raise RuntimeError(json.dumps(record, ensure_ascii=False))
    if proc.returncode != 0:
        record = {
            **start,
            "event": "teacher_neighborhood_measurement_failed",
            "finished_at": now_text(),
            "returncode": proc.returncode,
        }
        append_jsonl(state_path, record)
        raise RuntimeError(json.dumps(record, ensure_ascii=False))
    result = compact_result(
        run_id=run_id,
        scenario=scenario,
        role=role,
        params=params,
    )
    result.update(
        {
            "campaign_id": campaign_id,
            "name": name,
            "point_index": point_index,
            "block_index": block_index,
        }
    )
    return result


def paired_baseline_payload(record: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: record.get(key)
        for key in (
            "run_id",
            "history_path",
            "db_qps",
            "db_xact_tps",
            "resource_normalized",
            "utilization_pct",
            "resource_sources",
            "stable_sample_count",
            "plan_features",
            "plan_feature_count",
            "plan_feature_signature",
        )
    }


def measurement_gate(
    record: Mapping[str, Any],
    *,
    scenario: str,
    gates: Mapping[str, Any],
    require_baseline_profile: bool,
) -> Dict[str, Any]:
    telemetry = telemetry_gate([record], gates)
    db_qps = record.get("db_qps")
    try:
        db_qps_ok = float(db_qps) > 0.0
    except (TypeError, ValueError):
        db_qps_ok = False
    resources = record.get("resource_normalized") or {}
    resource_signal_ok = all(
        isinstance(resources.get(name), (int, float))
        and float(resources[name]) > 0.0
        for name in ("cpu", "memory", "io")
    )
    profile_ok = True
    profile_detail: Dict[str, Any] = {}
    if require_baseline_profile:
        utilization = record.get("utilization_pct") or {}
        if scenario == "none":
            ranges = gates.get("none_baseline_ranges") or {}
            checks = {
                "cpu": value_in_range(utilization.get("cpu"), ranges.get("cpu_pct") or []),
                "memory": value_in_range(
                    utilization.get("memory"),
                    ranges.get("memory_pct") or [],
                ),
                "io": value_in_range(resources.get("io"), ranges.get("io_mib_s") or []),
            }
            profile_ok = all(checks.values())
            profile_detail = {"none_baseline_checks": checks}
        else:
            try:
                target_utilization = float(utilization.get(scenario))
            except (TypeError, ValueError):
                target_utilization = float("nan")
            target_min = float(
                gates.get("single_bottleneck_target_min_util_pct", 80.0)
            )
            non_target_max = float(
                gates.get("single_bottleneck_non_target_max_util_pct", 79.999)
            )
            non_target_checks = {
                name: (
                    isinstance(utilization.get(name), (int, float))
                    and float(utilization[name]) <= non_target_max
                )
                for name in ("cpu", "memory", "io")
                if name != scenario
            }
            profile_ok = (
                target_utilization >= target_min
                and all(non_target_checks.values())
            )
            profile_detail = {
                "target_resource": scenario,
                "target_utilization_pct": target_utilization,
                "required_min_pct": target_min,
                "non_target_max_pct": non_target_max,
                "non_target_not_bottleneck": non_target_checks,
                "exactly_one_bottleneck": all(non_target_checks.values()),
            }
    return {
        "passed": bool(
            telemetry.get("passed")
            and db_qps_ok
            and resource_signal_ok
            and profile_ok
        ),
        "telemetry": telemetry,
        "db_qps_ok": db_qps_ok,
        "resource_signal_ok": resource_signal_ok,
        "baseline_profile_required": require_baseline_profile,
        "baseline_profile_ok": profile_ok,
        "baseline_profile_detail": profile_detail,
    }


def write_summary(
    *,
    campaign_id: str,
    campaign_dir: Path,
    points_by_scenario: Mapping[str, Sequence[Mapping[str, Any]]],
    state_path: Path,
) -> Path:
    completed = load_completed_points(state_path)
    summary = {
        "campaign_id": campaign_id,
        "updated_at": now_text(),
        "teacher_exact_point_excluded": True,
        "scenarios": {
            scenario: {
                "planned": len(points_by_scenario.get(scenario) or []),
                "completed": sum(
                    (scenario, int(point["point_index"])) in completed
                    for point in points_by_scenario.get(scenario) or []
                ),
            }
            for scenario in SCENARIO_ORDER
        },
    }
    summary["all_complete"] = all(
        item["planned"] == item["completed"]
        for item in summary["scenarios"].values()
    )
    path = campaign_dir / "summary.json"
    path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect VM teacher-neighborhood labels without training on the exact teacher."
    )
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--wait-for-validation", action="store_true")
    parser.add_argument("--wait-for-idle", action="store_true")
    parser.add_argument("--poll-sec", type=float, default=30.0)
    parser.add_argument("--timeout-sec", type=float, default=0.0)
    parser.add_argument("--scenario", action="append", choices=[*SCENARIO_ORDER, "all"])
    args = parser.parse_args()

    manifest_path = resolve_project_path(args.manifest)
    manifest = read_json(manifest_path)
    campaign_id = str(manifest.get("campaign_id") or "phase2_vm_teacher_neighborhood")
    campaign_dir = REPORT_ROOT / campaign_id
    campaign_dir.mkdir(parents=True, exist_ok=True)
    state_path = campaign_dir / "state.jsonl"
    teacher_manifest_path = resolve_project_path(
        str(manifest.get("teacher_validation_manifest") or "")
    )
    teacher_manifest = read_json(teacher_manifest_path)
    validate_manifest(teacher_manifest)
    teacher_summary_path = resolve_project_path(
        str(manifest.get("teacher_validation_summary") or "")
    )
    scenarios = teacher_manifest.get("scenarios") or {}
    gates = teacher_manifest.get("gates") or {}
    default = normalized_params(teacher_manifest.get("default_params") or {})
    sampling = manifest.get("sampling") or {}
    binary_parents = manifest.get("binary_parent_points") or {}
    requested = args.scenario or ["all"]
    selected = list(SCENARIO_ORDER) if "all" in requested else [
        scenario for scenario in SCENARIO_ORDER if scenario in requested
    ]
    points_by_scenario = {
        scenario: build_scenario_points(
            scenario=scenario,
            default=default,
            teacher=(scenarios[scenario].get("teacher_params") or {}),
            sampling=sampling,
            binary_parent=(binary_parents.get(scenario)),
        )
        for scenario in selected
    }
    manifest_copy = {
        **manifest,
        "resolved_teacher_validation_manifest": str(teacher_manifest_path),
        "resolved_teacher_validation_summary": str(teacher_summary_path),
        "points": points_by_scenario,
    }
    (campaign_dir / "manifest.json").write_text(
        json.dumps(manifest_copy, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_summary(
        campaign_id=campaign_id,
        campaign_dir=campaign_dir,
        points_by_scenario=points_by_scenario,
        state_path=state_path,
    )
    if not args.execute:
        print(json.dumps(points_by_scenario, ensure_ascii=False, indent=2), flush=True)
        return 0

    append_jsonl(
        state_path,
        {
            "event": "teacher_neighborhood_campaign_start",
            "timestamp": now_text(),
            "campaign_id": campaign_id,
            "selected_scenarios": selected,
        },
    )
    if args.wait_for_validation:
        teacher_summary = wait_for_teacher_summary(
            teacher_summary_path,
            state_path=state_path,
            poll_sec=args.poll_sec,
            timeout_sec=args.timeout_sec,
        )
    else:
        teacher_summary = validate_teacher_summary(teacher_summary_path)
    append_jsonl(
        state_path,
        {
            "event": "teacher_validation_gate_passed",
            "timestamp": now_text(),
            "summary_path": str(teacher_summary_path),
            "updated_at": teacher_summary.get("updated_at"),
        },
    )
    if args.wait_for_idle:
        wait_for_current_worktree_idle(
            state_path=state_path,
            poll_sec=args.poll_sec,
            timeout_sec=args.timeout_sec,
        )
    else:
        active = active_current_worktree_processes()
        if active:
            raise RuntimeError(
                "refusing to start while current-worktree Bonect processes are active: "
                + json.dumps(active, ensure_ascii=False)
            )

    completed = load_completed_points(state_path)
    block_size = max(
        1,
        int(sampling.get("candidate_points_per_paired_default", 3)),
    )
    for scenario in selected:
        scenario_cfg = scenarios[scenario]
        points = points_by_scenario[scenario]
        for block_index, offset in enumerate(range(0, len(points), block_size)):
            block = points[offset : offset + block_size]
            pending = [
                point
                for point in block
                if (scenario, int(point["point_index"])) not in completed
            ]
            if not pending:
                continue
            baseline = run_measurement(
                campaign_id=campaign_id,
                state_path=state_path,
                scenario=scenario,
                scenario_cfg=scenario_cfg,
                role="paired_default",
                name=f"block_{block_index:02d}_default",
                params=default,
                point_index=-1,
                block_index=block_index,
            )
            baseline_gate = measurement_gate(
                baseline,
                scenario=scenario,
                gates=gates,
                require_baseline_profile=True,
            )
            baseline["measurement_gate"] = baseline_gate
            if not baseline_gate["passed"]:
                baseline["event"] = "teacher_neighborhood_baseline_gate_failed"
                append_jsonl(state_path, baseline)
                raise RuntimeError(
                    "paired default baseline gate failed: "
                    + json.dumps(baseline_gate, ensure_ascii=False)
                )
            baseline["event"] = "teacher_neighborhood_baseline_complete"
            append_jsonl(state_path, baseline)
            print(json.dumps(baseline, ensure_ascii=False), flush=True)
            for point in pending:
                result = run_measurement(
                    campaign_id=campaign_id,
                    state_path=state_path,
                    scenario=scenario,
                    scenario_cfg=scenario_cfg,
                    role="neighbor",
                    name=str(point["name"]),
                    params=point["params"],
                    point_index=int(point["point_index"]),
                    block_index=block_index,
                )
                candidate_gate = measurement_gate(
                    result,
                    scenario=scenario,
                    gates=gates,
                    require_baseline_profile=False,
                )
                result["measurement_gate"] = candidate_gate
                if not candidate_gate["passed"]:
                    result["event"] = "teacher_neighborhood_point_gate_failed"
                    append_jsonl(state_path, result)
                    raise RuntimeError(
                        "neighbor measurement gate failed: "
                        + json.dumps(candidate_gate, ensure_ascii=False)
                    )
                result.update(
                    {
                        "event": "teacher_neighborhood_point_complete",
                        "kind": point["kind"],
                        "metadata": point.get("metadata") or {},
                        "paired_baseline_valid": True,
                        "paired_baseline": paired_baseline_payload(baseline),
                    }
                )
                append_jsonl(state_path, result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
                completed[(scenario, int(point["point_index"]))] = result
                write_summary(
                    campaign_id=campaign_id,
                    campaign_dir=campaign_dir,
                    points_by_scenario=points_by_scenario,
                    state_path=state_path,
                )
    summary_path = write_summary(
        campaign_id=campaign_id,
        campaign_dir=campaign_dir,
        points_by_scenario=points_by_scenario,
        state_path=state_path,
    )
    append_jsonl(
        state_path,
        {
            "event": "teacher_neighborhood_campaign_complete",
            "timestamp": now_text(),
            "summary_path": str(summary_path),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

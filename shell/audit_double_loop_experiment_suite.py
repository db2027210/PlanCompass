#!/usr/bin/env python3
"""Audit the 18-run PG17 double-loop real-replay experiment suite."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


COST_PARAMS = {
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
}
OUTER_FIELDS = {
    "iter",
    "params",
    "qps",
    "latency_p99_ms",
    "latency_p95_ms",
    "resource",
    "utility",
    "explain_samples",
}
STATUS_PRIORITY = {
    "completed": 6,
    "running": 5,
    "preparing": 5,
    "pending": 4,
    "needs_review": 3,
    "held": 2,
}


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def finite(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def expected_tasks(matrix: Mapping[str, Any]) -> List[Dict[str, Any]]:
    tasks: List[Dict[str, Any]] = []
    for experiment in matrix.get("experiments") or []:
        if not isinstance(experiment, Mapping):
            continue
        exp_id = str(experiment["exp_id"])
        repeat_ids = experiment.get("repeat_ids")
        if isinstance(repeat_ids, list):
            repeats = [int(value) for value in repeat_ids]
        else:
            repeats = list(range(1, int(experiment.get("runs") or 1) + 1))
        for repeat_id in repeats:
            tasks.append(
                {
                    "task_id": f"{exp_id.lower()}_r{repeat_id}",
                    "exp_id": exp_id,
                    "repeat_id": repeat_id,
                    "benchmark": str(experiment["benchmark"]),
                    "bottleneck": str(experiment["bottleneck"]),
                    "anchor_exp_id": experiment.get("anchor_exp_id"),
                }
            )
    return tasks


def task_states(scheduler_roots: Sequence[Path]) -> Dict[str, List[Dict[str, Any]]]:
    result: Dict[str, List[Dict[str, Any]]] = {}
    for root in scheduler_roots:
        state_path = root / "scheduler_state.json"
        try:
            state = read_json(state_path)
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
            continue
        try:
            state_mtime_ns = state_path.stat().st_mtime_ns
        except OSError:
            state_mtime_ns = 0
        for task_id, task in (state.get("tasks") or {}).items():
            if not isinstance(task, Mapping):
                continue
            row = dict(task)
            row["scheduler_root"] = str(root)
            row["state_path"] = str(state_path)
            row["state_mtime_ns"] = state_mtime_ns
            result.setdefault(str(task_id), []).append(row)
    return result


def worker_is_alive(row: Mapping[str, Any]) -> bool:
    try:
        worker_pid = int(row.get("worker_pid") or 0)
    except (TypeError, ValueError):
        return False
    if worker_pid <= 0:
        return False
    try:
        stat_fields = (Path("/proc") / str(worker_pid) / "stat").read_text(
            encoding="utf-8",
            errors="replace",
        ).split()
    except OSError:
        return False
    return len(stat_fields) >= 3 and stat_fields[2] != "Z"


def choose_state(rows: Sequence[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
    if not rows:
        return None

    def effective_status_priority(row: Mapping[str, Any]) -> int:
        status = str(row.get("status"))
        if status in {"preparing", "running"} and not worker_is_alive(row):
            return 0
        return STATUS_PRIORITY.get(status, 0)

    return max(
        rows,
        key=lambda row: (
            str(row.get("status")) == "completed",
            worker_is_alive(row),
            effective_status_priority(row),
            int(row.get("attempt") or 0),
            int(row.get("state_mtime_ns") or 0),
        ),
    )


def artifact_candidates(
    task_id: str,
    rows: Sequence[Mapping[str, Any]],
    scheduler_roots: Sequence[Path],
) -> Iterable[Path]:
    seen = set()
    for row in rows:
        raw = row.get("export_path")
        if raw:
            path = Path(str(raw)).resolve()
            if path not in seen:
                seen.add(path)
                yield path
    for root in scheduler_roots:
        path = (root / "exports" / f"{task_id}.json").resolve()
        if path not in seen:
            seen.add(path)
            yield path


def count_jsonl_event(path: Path, event: str) -> int:
    count = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    for raw in lines:
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(row, Mapping) and row.get("event") == event:
            count += 1
    return count


def last_jsonl_event(path: Path, event: str) -> Optional[Dict[str, Any]]:
    result: Optional[Dict[str, Any]] = None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for raw in lines:
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("event") == event:
            result = row
    return result


def aggregate_topk_decisions(
    path: Path,
) -> Tuple[int, int, int, Optional[Dict[str, Any]]]:
    """Aggregate cumulative Top-K counters across independent outer rounds."""
    latest_by_round: Dict[str, Dict[str, Any]] = {}
    latest_overall: Optional[Dict[str, Any]] = None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0, 0, 0, None
    for raw in lines:
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("event") != "adaptive_topk_decision":
            continue
        # replayed_k is cumulative within one outer round, but resets when the
        # next round starts.  Keep only the final decision for each round and
        # then sum those per-round cumulative counters.
        round_key = str(row.get("round", 0))
        latest_by_round[round_key] = row
        latest_overall = row
    replayed = sum(int(row.get("replayed_k") or 0) for row in latest_by_round.values())
    usable = sum(
        int(row.get("usable_real_replay_count") or 0)
        for row in latest_by_round.values()
    )
    failed = sum(
        int(row.get("failed_real_replay_count") or 0)
        for row in latest_by_round.values()
    )
    return replayed, usable, failed, latest_overall


def progress_evidence(
    state: Optional[Mapping[str, Any]],
    bottleneck: str,
) -> Dict[str, Any]:
    if state is None:
        return {
            "phase": "not_started",
            "real_validation_files": 0,
            "usable_real_validations": 0,
            "calibration_rows": 0,
            "model_quality_gate_present": False,
            "model_quality_gate_passed": None,
            "optimizer_path": None,
            "outer_rounds": 0,
            "topk_replayed": 0,
            "topk_usable_real_replays": 0,
            "topk_failed_real_replays": 0,
            "topk_last_action": None,
            "topk_physical_validation_files": 0,
            "topk_real_replay_evidence_consistent": True,
            "failure_class": None,
            "guest_exit_diagnostics_path": None,
            "final_confirmation_present": False,
        }
    scenario_dirs: List[Path] = []
    run_dir = state.get("run_dir")
    if run_dir:
        scenario_dirs.append(Path(str(run_dir)) / bottleneck)
    task_root = state.get("task_root")
    if task_root:
        scenario_dirs.extend(Path(str(task_root)).glob(f"formal_runs/*/{bottleneck}"))
    existing = [path for path in scenario_dirs if path.is_dir()]
    if not existing:
        return {
            "phase": str(state.get("status") or "not_started"),
            "real_validation_files": 0,
            "usable_real_validations": 0,
            "calibration_rows": 0,
            "model_quality_gate_present": False,
            "model_quality_gate_passed": None,
            "optimizer_path": None,
            "outer_rounds": 0,
            "topk_replayed": 0,
            "topk_usable_real_replays": 0,
            "topk_failed_real_replays": 0,
            "topk_last_action": None,
            "topk_physical_validation_files": 0,
            "topk_real_replay_evidence_consistent": True,
            "failure_class": None,
            "guest_exit_diagnostics_path": None,
            "final_confirmation_present": False,
        }
    scenario_dir = max(existing, key=lambda path: path.stat().st_mtime)
    validation_files = sorted(
        (scenario_dir / "real_validations").glob(f"{bottleneck}_double_loop_*.json")
    )
    usable = 0
    for path in validation_files:
        try:
            row = read_json(path)
        except (json.JSONDecodeError, OSError, ValueError):
            continue
        if row.get("double_loop_measurement_usable") is True:
            usable += 1
    calibration_path = scenario_dir / "fresh_model_calibration.jsonl"
    # The calibration JSONL also contains one quality-gate summary event after
    # the replay rows.  Counting physical lines therefore overstates a complete
    # 15-replay calibration as 16 samples.
    calibration_rows = count_jsonl_event(
        calibration_path,
        "fresh_model_calibration_replay",
    )
    quality_gate_path = scenario_dir / "fresh_model_quality_gate.json"
    quality_gate_present = quality_gate_path.is_file()
    quality_gate_passed: Optional[bool] = None
    if quality_gate_present:
        try:
            quality_gate_passed = read_json(quality_gate_path).get("passed") is True
        except (json.JSONDecodeError, OSError, ValueError):
            quality_gate_passed = False
    # Production optimizer logs are timestamped (optimizer_YYYY-mm-dd_HHMMSS.jsonl).
    # Retain compatibility with older fixed-name optimizer.jsonl artifacts.
    optimizer_paths = sorted(
        scenario_dir.glob("optimizer*.jsonl"),
        key=lambda path: path.stat().st_mtime,
    )
    optimizer_path = optimizer_paths[-1] if optimizer_paths else None
    if optimizer_path is not None:
        # Current grid-search runs emit double_loop_grid_round; retain the
        # legacy name for old artifacts without double-counting mixed logs.
        outer_rounds = max(
            count_jsonl_event(optimizer_path, "double_loop_grid_round"),
            count_jsonl_event(optimizer_path, "double_loop_round"),
        )
    else:
        outer_rounds = 0
    if optimizer_path is not None:
        topk_replayed, topk_usable, topk_failed, topk_decision = (
            aggregate_topk_decisions(optimizer_path)
        )
    else:
        topk_replayed, topk_usable, topk_failed, topk_decision = 0, 0, 0, None
    summary_path = scenario_dir / "scenario_summary.json"
    final_path = scenario_dir / "final_confirmation.json"
    # A formal scenario writes two physical real validations before model
    # calibration: pre-baseline warmup and the fresh baseline.  Calibration
    # replays follow, then Top-K candidates, and finally one independent
    # confirmation replay when present.  This independent file count anchors
    # the controller's cumulative Top-K counter in actual replay artifacts.
    topk_physical_validation_files = max(
        0,
        len(validation_files)
        - 2
        - calibration_rows
        - int(final_path.is_file()),
    )
    topk_evidence_consistent = (
        topk_replayed == 0 or topk_physical_validation_files >= topk_replayed
    )
    failure_class: Optional[str] = None
    guest_diagnostics_path: Optional[Path] = None
    if task_root:
        candidate = Path(str(task_root)) / "guest_exit_diagnostics.json"
        diagnostic_parts: List[str] = []
        if candidate.is_file():
            guest_diagnostics_path = candidate
            try:
                diagnostic_parts.append(str(read_json(candidate).get("output") or ""))
            except (json.JSONDecodeError, OSError, ValueError):
                pass
        serial_candidate = Path(str(task_root)) / "rootless_vm" / "serial.log"
        if serial_candidate.is_file():
            if guest_diagnostics_path is None:
                guest_diagnostics_path = serial_candidate
            try:
                diagnostic_parts.append(
                    serial_candidate.read_text(encoding="utf-8", errors="replace")
                )
            except OSError:
                pass
        if diagnostic_parts:
            diagnostic_lower = "\n".join(diagnostic_parts).lower()
            if "out of memory: killed process" in diagnostic_lower or "oom-killer" in diagnostic_lower:
                failure_class = "guest_oom"
            elif "no space left on device" in diagnostic_lower:
                failure_class = "guest_disk_full"
            elif "terminated by signal 9: killed" in diagnostic_lower:
                failure_class = "guest_process_sigkill"
    if summary_path.is_file():
        try:
            summary_status = str(read_json(summary_path).get("status") or "finished")
        except (json.JSONDecodeError, OSError, ValueError):
            summary_status = "finished"
        phase = "failed" if summary_status == "failed" else "finished"
    elif final_path.is_file():
        phase = "final_confirmation"
    elif optimizer_path is not None:
        phase = "optimizer"
    elif calibration_path.is_file():
        phase = "model_calibration"
    elif (scenario_dir / "fresh_baseline.json").is_file():
        phase = "fresh_baseline"
    elif (scenario_dir / "pre_baseline_warmup.json").is_file():
        phase = "fresh_baseline_in_progress"
    else:
        phase = str(state.get("status") or "preparing")
    return {
        "phase": phase,
        "scenario_dir": str(scenario_dir),
        "real_validation_files": len(validation_files),
        "usable_real_validations": usable,
        "calibration_rows": calibration_rows,
        "model_quality_gate_present": quality_gate_present,
        "model_quality_gate_passed": quality_gate_passed,
        "optimizer_path": str(optimizer_path) if optimizer_path is not None else None,
        "outer_rounds": outer_rounds,
        "topk_replayed": topk_replayed,
        "topk_usable_real_replays": topk_usable,
        "topk_failed_real_replays": topk_failed,
        "topk_last_action": (topk_decision or {}).get("action"),
        "topk_physical_validation_files": topk_physical_validation_files,
        "topk_real_replay_evidence_consistent": topk_evidence_consistent,
        "failure_class": failure_class,
        "guest_exit_diagnostics_path": (
            str(guest_diagnostics_path) if guest_diagnostics_path is not None else None
        ),
        "final_confirmation_present": final_path.is_file(),
    }


def validate_artifact(
    path: Path,
    expected: Mapping[str, Any],
    final_goal: Mapping[str, Any],
) -> Tuple[bool, List[str], Dict[str, Any]]:
    errors: List[str] = []
    try:
        document = read_json(path)
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError) as exc:
        return False, [f"artifact_read:{type(exc).__name__}:{exc}"], {}

    meta = document.get("meta") if isinstance(document.get("meta"), Mapping) else {}
    outer = document.get("outer_loop") if isinstance(document.get("outer_loop"), list) else []
    inner = (
        document.get("inner_loop_summary")
        if isinstance(document.get("inner_loop_summary"), Mapping)
        else {}
    )
    result = document.get("result") if isinstance(document.get("result"), Mapping) else {}
    audit = document.get("audit") if isinstance(document.get("audit"), Mapping) else {}

    expected_meta = {
        "exp_id": expected["exp_id"],
        "repeat_id": expected["repeat_id"],
        "benchmark": expected["benchmark"],
        "bottleneck": expected["bottleneck"],
        "db": "pg17",
    }
    for key, value in expected_meta.items():
        if meta.get(key) != value:
            errors.append(f"meta.{key}_expected_{value!r}_got_{meta.get(key)!r}")
    if meta.get("method") != "coordinate_grid_resource_estimation_topk_replay":
        errors.append("meta.method_not_double_loop_coordinate_grid_topk_replay")
    machine = meta.get("machine_spec") if isinstance(meta.get("machine_spec"), Mapping) else {}
    for key in ("cpu_cores", "mem_gb", "disk_bw_mb"):
        if finite(machine.get(key)) is None:
            errors.append(f"meta.machine_spec.{key}_missing")
    if finite(meta.get("bottleneck_target")) is None and expected["bottleneck"] != "none":
        errors.append("meta.bottleneck_target_missing")

    if not outer:
        errors.append("outer_loop_empty")
    for index, row in enumerate(outer, start=1):
        if not isinstance(row, Mapping):
            errors.append(f"outer_loop[{index}]_not_object")
            continue
        missing = sorted(OUTER_FIELDS - set(row))
        errors.extend(f"outer_loop[{index}].{key}_missing" for key in missing)
        if not row.get("explain_samples"):
            errors.append(f"outer_loop[{index}].explain_samples_empty")
        if set(row.get("params") or {}) != COST_PARAMS:
            errors.append(f"outer_loop[{index}].params_not_exactly_seven")

    for key in ("total_iterations", "wall_time_ms", "best_estimated_utility", "best_params"):
        if key not in inner:
            errors.append(f"inner_loop_summary.{key}_missing")
    if int(inner.get("total_iterations") or 0) <= 0:
        errors.append("inner_loop_summary.total_iterations_not_positive")

    if result.get("bad_config") is not False:
        errors.append("result.bad_config_not_false")
    if set(result.get("final_params") or {}) != COST_PARAMS:
        errors.append("result.final_params_not_exactly_seven")
    qps_before = finite(result.get("qps_before"))
    qps_after = finite(result.get("qps_after"))
    if qps_before is None or qps_after is None or qps_before <= 0.0:
        errors.append("result.qps_missing")
        qps_gain = None
    else:
        qps_gain = (qps_after - qps_before) / qps_before
        minimum_gain = float(
            final_goal.get(
                "none_min_qps_gain_ratio"
                if expected["bottleneck"] == "none"
                else "bottleneck_min_qps_gain_ratio",
                0.0,
            )
        )
        if qps_gain + 1e-12 < minimum_gain:
            errors.append(f"result.qps_gain_below_{minimum_gain:.6f}")
    for key in ("p99_before_ms", "p99_after_ms", "total_wall_time_s"):
        if finite(result.get(key)) is None:
            errors.append(f"result.{key}_missing")
    if int(result.get("converge_outer_iters") or -1) != len(outer):
        errors.append("result.converge_outer_iters_mismatch")

    before = result.get("resource_before") if isinstance(result.get("resource_before"), Mapping) else {}
    after = result.get("resource_after") if isinstance(result.get("resource_after"), Mapping) else {}
    resource_deltas: Dict[str, Optional[float]] = {}
    for key in ("cpu", "mem", "io"):
        before_value = finite(before.get(key))
        after_value = finite(after.get(key))
        resource_deltas[key] = (
            after_value - before_value
            if before_value is not None and after_value is not None
            else None
        )
    bottleneck = str(expected["bottleneck"])
    target_key = "mem" if bottleneck == "memory" else bottleneck
    target_drop = None
    if bottleneck == "none":
        gains = [value for value in resource_deltas.values() if value is not None and value > 0.0]
        if final_goal.get("none_requires_any_resource_gain") is True and not gains:
            errors.append("result.none_no_resource_gain")
    else:
        before_value = finite(before.get(target_key))
        after_value = finite(after.get(target_key))
        if before_value is None or after_value is None or before_value <= 0.0:
            errors.append(f"result.{target_key}_resource_missing")
        else:
            target_drop = (before_value - after_value) / before_value
            minimum_drop = float(final_goal.get("bottleneck_min_target_drop_ratio") or 0.0)
            maximum_drop = float(final_goal.get("bottleneck_max_target_drop_ratio") or 1.0)
            if target_drop + 1e-12 < minimum_drop:
                errors.append(f"result.{target_key}_drop_below_{minimum_drop:.6f}")
            if target_drop - 1e-12 > maximum_drop:
                errors.append(f"result.{target_key}_drop_above_{maximum_drop:.6f}")

    if audit.get("independent_final_real_replay") is not True:
        errors.append("audit.independent_final_real_replay_false")
    raw_path = audit.get("final_raw_validation_path")
    if not isinstance(raw_path, str) or not Path(raw_path).is_file():
        errors.append("audit.final_raw_validation_path_missing")
    return not errors, errors, {
        "qps_before": qps_before,
        "qps_after": qps_after,
        "qps_gain_ratio": qps_gain,
        "target_drop_ratio": target_drop,
        "resource_deltas": resource_deltas,
    }


def audit_suite(
    matrix_path: Path,
    scheduler_roots: Sequence[Path],
) -> Dict[str, Any]:
    matrix = read_json(matrix_path)
    expected = expected_tasks(matrix)
    states = task_states(scheduler_roots)
    rows: List[Dict[str, Any]] = []
    for task in expected:
        task_id = str(task["task_id"])
        candidates = list(artifact_candidates(task_id, states.get(task_id, []), scheduler_roots))
        artifact_results = []
        for path in candidates:
            if not path.is_file():
                continue
            ok, errors, metrics = validate_artifact(path, task, matrix.get("final_goal") or {})
            artifact_results.append(
                {"path": str(path), "ok": ok, "errors": errors, "metrics": metrics}
            )
        valid = next((item for item in artifact_results if item["ok"]), None)
        chosen = choose_state(states.get(task_id, []))
        progress = progress_evidence(chosen, str(task["bottleneck"]))
        if progress.get("failure_class") is not None:
            valid = None
            for item in artifact_results:
                if "guest_instability_detected" not in item["errors"]:
                    item["errors"].append("guest_instability_detected")
                item["ok"] = False
        rows.append(
            {
                **task,
                "complete": valid is not None,
                "artifact": valid["path"] if valid else None,
                "artifact_checks": artifact_results,
                "status": chosen.get("status") if chosen else "missing",
                "attempt": int(chosen.get("attempt") or 0) if chosen else 0,
                "scheduler_root": chosen.get("scheduler_root") if chosen else None,
                "last_error": chosen.get("last_error") if chosen else None,
                "progress": progress,
            }
        )

    total_expected = int((matrix.get("run_count_semantics") or {}).get("total_formal_runs") or 0)
    completed = sum(bool(row["complete"]) for row in rows)
    errors: List[str] = []
    if len(expected) != total_expected:
        errors.append(f"matrix_expands_to_{len(expected)}_not_{total_expected}")
    task_ids = [str(row["task_id"]) for row in rows]
    if len(task_ids) != len(set(task_ids)):
        errors.append("duplicate_expected_task_ids")
    if completed != total_expected:
        errors.append(f"completed_{completed}_of_{total_expected}")
    return {
        "schema_version": 1,
        "matrix_config": str(matrix_path.resolve()),
        "scheduler_roots": [str(path.resolve()) for path in scheduler_roots],
        "expected_runs": total_expected,
        "expanded_runs": len(expected),
        "completed_runs": completed,
        "overall_complete": not errors,
        "errors": errors,
        "rows": rows,
    }


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix-config", type=Path, required=True)
    parser.add_argument("--scheduler-root", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = audit_suite(
        args.matrix_config.resolve(),
        [path.resolve() for path in args.scheduler_root],
    )
    if args.output is not None:
        write_json(args.output.resolve(), payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if payload["overall_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

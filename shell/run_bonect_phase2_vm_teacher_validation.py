#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "shell" / "run_pg_benchmark_bottleneck_matrix.py"
DEFAULT_MANIFEST = ROOT / "config" / "bonect_phase2_vm_teacher_validation_20260711.json"
HISTORY_ROOT = ROOT / "history" / "pg_benchmark_bottleneck_matrix"
GENERATED_CONFIG_ROOT = ROOT / "config" / "generated" / "pg_benchmark_bottleneck_matrix"
REPORT_ROOT = ROOT / "report" / "bonect"

COST_PARAM_KEYS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
VM_TEACHER_CPU_MODE = "vm_busy"
VM_TEACHER_MEMORY_MODE = "vm_used"
VM_TEACHER_IO_MODE = "disk_device"
DEFAULT_COST_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}
SCENARIO_ORDER = ("cpu", "memory", "io", "none")
POINT_ROLES = ("default_before", "teacher", "default_after")
WORKLOAD_HEALTH_PATTERNS = {
    "too_many_clients": re.compile(r"FATAL:\s+sorry, too many clients already", re.IGNORECASE),
    "connection_failures": re.compile(r"Getting connection failed", re.IGNORECASE),
    "null_pointer_exceptions": re.compile(r"NullPointerException", re.IGNORECASE),
}


class ControlDecisionPending(RuntimeError):
    pass


def now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return data


def append_jsonl(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(record), ensure_ascii=False, sort_keys=True) + "\n")


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


def finite_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def workload_health_from_text(
    text: str,
    gates: Mapping[str, Any],
    *,
    log_path: str = "",
) -> Dict[str, Any]:
    config = gates.get("workload_health") or {}
    counts = {
        name: len(pattern.findall(text))
        for name, pattern in WORKLOAD_HEALTH_PATTERNS.items()
    }
    limits = {
        "too_many_clients": max(0, int(config.get("max_too_many_clients", 0))),
        "connection_failures": max(0, int(config.get("max_connection_failures", 0))),
        "null_pointer_exceptions": max(
            0,
            int(config.get("max_null_pointer_exceptions", 0)),
        ),
    }
    completion = {
        "xp_workload_done": len(re.findall(r"XP Workload is done", text, re.IGNORECASE)),
        "test_summary": len(re.findall(r"Test Summary", text, re.IGNORECASE)),
    }
    require_done = bool(config.get("require_xp_workload_done", True))
    require_summary = bool(config.get("require_test_summary", True))
    count_checks_passed = all(counts[name] <= limits[name] for name in counts)
    completion_passed = (
        (not require_done or completion["xp_workload_done"] > 0)
        and (not require_summary or completion["test_summary"] > 0)
    )
    return {
        "passed": bool(text) and count_checks_passed and completion_passed,
        "log_path": log_path,
        "log_nonempty": bool(text),
        "counts": counts,
        "limits": limits,
        "completion": completion,
        "completion_requirements": {
            "xp_workload_done": require_done,
            "test_summary": require_summary,
        },
    }


def load_workload_health(
    *,
    run_id: str,
    scenario: str,
    gates: Mapping[str, Any],
) -> Dict[str, Any]:
    log_path = HISTORY_ROOT / run_id / scenario / "hybench_xp.log"
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        text = ""
    return workload_health_from_text(text, gates, log_path=str(log_path))


def point_measurement_issues(
    record: Mapping[str, Any],
    gates: Mapping[str, Any],
) -> List[str]:
    issues: List[str] = []

    def positive(value: Any) -> bool:
        number = finite_float(value)
        return number is not None and number > 0.0

    for field in ("db_qps", "db_xact_tps"):
        if not positive(record.get(field)):
            issues.append(field)
    for field in ("resource_normalized", "utilization_pct"):
        values = record.get(field) or {}
        for resource in ("cpu", "memory", "io"):
            if not positive(values.get(resource)):
                issues.append(f"{field}.{resource}")
    sources = record.get("resource_sources") or {}
    expected_sources = {
        "cpu_source": str(gates.get("required_cpu_source") or "vm_runtime_ssh"),
        "memory_source": str(gates.get("required_memory_source") or "vm_meminfo_used"),
        "io_source": str(gates.get("required_io_source") or "vm_disk_device"),
        "io_disk_devices": str(gates.get("required_io_device") or "vda"),
    }
    for field, expected in expected_sources.items():
        if expected not in str(sources.get(field) or ""):
            issues.append(f"resource_sources.{field}")
    min_samples = max(1, int(gates.get("min_stable_samples", 45)))
    samples = record.get("stable_sample_count") or {}
    for resource in ("cpu", "memory", "io"):
        sample_count = finite_float(samples.get(resource))
        if sample_count is None or sample_count < min_samples:
            issues.append(f"stable_sample_count.{resource}")
    if sorted(record.get("generated_initial_param_keys") or []) != sorted(COST_PARAM_KEYS):
        issues.append("generated_initial_param_keys")
    benchmark = record.get("benchmark") or {}
    if not isinstance(benchmark, Mapping) or not benchmark:
        issues.append("benchmark")
    else:
        for field in ("xp_qps", "xp_tps"):
            if not positive(benchmark.get(field)):
                issues.append(f"benchmark.{field}")
    if not positive(record.get("plan_capture_count")):
        issues.append("plan_capture_count")
    if not positive(record.get("all_sql_count")):
        issues.append("all_sql_count")
    if not positive(record.get("plan_feature_count")):
        issues.append("plan_feature_count")
    if not str(record.get("plan_feature_signature") or ""):
        issues.append("plan_feature_signature")
    health = record.get("workload_health") or {}
    if not isinstance(health, Mapping) or health.get("passed") is not True:
        issues.append("workload_health")
    return issues


def default_baseline_gate(
    record: Mapping[str, Any],
    gates: Mapping[str, Any],
) -> Dict[str, Any]:
    scenario = str(record.get("scenario") or "")
    issues = point_measurement_issues(record, gates)
    if record.get("baseline_only_gate_passed") is not True:
        issues.append("baseline_only_gate_passed")
    strong_gate = record.get("strong_none_gate") or {}
    if not isinstance(strong_gate, Mapping) or strong_gate.get("passed") is not True:
        issues.append("strong_none_gate.passed")
    if str(strong_gate.get("target_resource") or "") != scenario:
        issues.append("strong_none_gate.target_resource")
    utilization = record.get("utilization_pct") or {}
    target_min = float(gates.get("single_bottleneck_target_min_util_pct", 80.0))
    non_target_max = float(
        gates.get("single_bottleneck_non_target_max_util_pct", 79.999)
    )
    target_value = finite_float(utilization.get(scenario))
    if target_value is None or target_value < target_min:
        issues.append(f"utilization_pct.{scenario}.target_below_min")
    for resource in ("cpu", "memory", "io"):
        if resource == scenario:
            continue
        value = finite_float(utilization.get(resource))
        if value is None or value > non_target_max:
            issues.append(f"utilization_pct.{resource}.non_target_above_max")
    return {
        "passed": not issues,
        "scenario": scenario,
        "issues": sorted(set(issues)),
        "target_min_util_pct": target_min,
        "non_target_max_util_pct": non_target_max,
    }


def predecessor_measurement_issues(
    record: Mapping[str, Any],
    *,
    min_stable_samples: int,
    require_capture: bool,
) -> List[str]:
    """Validate persisted v78 records independently of their success flags."""
    issues: List[str] = []

    def positive(value: Any) -> bool:
        number = finite_float(value)
        return number is not None and number > 0.0

    for field in ("db_qps", "db_xact_tps"):
        if not positive(record.get(field)):
            issues.append(field)
    for field in ("resource_normalized", "utilization_pct"):
        values = record.get(field) or {}
        for resource in ("cpu", "memory", "io"):
            if not positive(values.get(resource)):
                issues.append(f"{field}.{resource}")
    sources = record.get("resource_sources") or {}
    expected_sources = {
        "cpu_source": "vm_pg_proc_cpu",
        "memory_source": "vm_pg_proc_rss",
        "io_source": "vm_disk_device",
        "io_disk_devices": "vda",
    }
    for field, expected in expected_sources.items():
        if expected not in str(sources.get(field) or ""):
            issues.append(f"resource_sources.{field}")
    samples = record.get("stable_sample_count") or {}
    for resource in ("cpu", "memory", "io"):
        sample_count = finite_float(samples.get(resource))
        if sample_count is None or sample_count < min_stable_samples:
            issues.append(f"stable_sample_count.{resource}")
    if not positive(record.get("plan_feature_count")):
        issues.append("plan_feature_count")
    if not str(record.get("plan_feature_signature") or ""):
        issues.append("plan_feature_signature")
    if require_capture:
        if not record.get("benchmark"):
            issues.append("benchmark")
        if not positive(record.get("plan_capture_count")):
            issues.append("plan_capture_count")
        if not positive(record.get("all_sql_count")):
            issues.append("all_sql_count")
        if not str(record.get("history_path") or ""):
            issues.append("history_path")
    return issues


def normalized_params(raw: Mapping[str, Any]) -> Dict[str, float]:
    keys = set(raw)
    invalid = sorted(keys - set(COST_PARAM_KEYS))
    missing = sorted(set(COST_PARAM_KEYS) - keys)
    if invalid or missing:
        raise RuntimeError(
            "point must contain exactly seven PostgreSQL cost parameters; "
            f"invalid={invalid}, missing={missing}"
        )
    result: Dict[str, float] = {}
    for name in COST_PARAM_KEYS:
        value = finite_float(raw.get(name))
        if value is None:
            raise RuntimeError(f"invalid parameter value: {name}={raw.get(name)!r}")
        result[name] = value
    return result


def normalized_param_rms_distance(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    reference_ranges: Mapping[str, Any],
) -> float:
    left_values = normalized_params(left)
    right_values = normalized_params(right)
    distances: List[float] = []
    for name in COST_PARAM_KEYS:
        bounds = reference_ranges.get(name)
        if (
            not isinstance(bounds, Sequence)
            or isinstance(bounds, (str, bytes))
            or len(bounds) != 2
        ):
            raise RuntimeError(f"invalid parameter distance range for {name}: {bounds!r}")
        low = finite_float(bounds[0])
        high = finite_float(bounds[1])
        if low is None or high is None or high <= low:
            raise RuntimeError(f"invalid parameter distance bounds for {name}: {bounds!r}")
        distances.append(abs(left_values[name] - right_values[name]) / (high - low))
    return math.sqrt(sum(value * value for value in distances) / len(distances))


def resolve_project_path(raw: str) -> Path:
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    default_params = normalized_params(manifest.get("default_params") or {})
    gates = manifest.get("gates") or {}
    reference_ranges = gates.get("param_distance_reference_ranges") or {}
    min_param_distance = float(gates.get("min_default_teacher_param_rms_distance", 0.0))
    scenarios = manifest.get("scenarios") or {}
    if not isinstance(scenarios, Mapping):
        raise RuntimeError("manifest.scenarios must be an object")
    for scenario in SCENARIO_ORDER:
        cfg = scenarios.get(scenario)
        if not isinstance(cfg, Mapping):
            raise RuntimeError(f"manifest is missing scenario: {scenario}")
        teacher_params = normalized_params(cfg.get("teacher_params") or {})
        distance = normalized_param_rms_distance(
            default_params,
            teacher_params,
            reference_ranges,
        )
        if distance < min_param_distance:
            raise RuntimeError(
                f"{scenario} default/teacher parameter distance {distance:.6f} "
                f"is below required {min_param_distance:.6f}"
            )
        vm_profile = cfg.get("vm_profile") or {}
        for key in ("cpu", "memory", "read_mb", "write_mb", "disk_dev"):
            if key not in vm_profile:
                raise RuntimeError(f"{scenario}.vm_profile missing {key}")
        capability = cfg.get("capability") or {}
        for resource in ("cpu", "memory", "io"):
            value = finite_float(capability.get(resource))
            if value is None or value <= 0:
                raise RuntimeError(f"invalid {scenario}.capability.{resource}")
        for raw_path in (cfg.get("statement_sql_files") or {}).values():
            path = resolve_project_path(str(raw_path))
            if not path.is_file():
                raise FileNotFoundError(f"statement SQL does not exist: {path}")
        for raw_path in cfg.get("setup_sql_files") or []:
            path = resolve_project_path(str(raw_path))
            if not path.is_file():
                raise FileNotFoundError(f"setup SQL does not exist: {path}")


def process_owned_by_current_worktree(pid: int, command: str) -> bool:
    root_text = str(ROOT)
    if root_text in command:
        return True
    try:
        cwd = (Path("/proc") / str(pid) / "cwd").resolve()
    except (FileNotFoundError, PermissionError, OSError):
        return False
    return cwd == ROOT or ROOT in cwd.parents


def active_current_worktree_processes() -> List[Dict[str, Any]]:
    proc = subprocess.run(
        ["ps", "-eo", "pid=,command="],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    active: List[Dict[str, Any]] = []
    watched = (
        "run_pg_benchmark_bottleneck_matrix.py",
        "parameter_optimizer.py",
        "run_bonect_phase2_none_delta_calibration.py",
        "bonect_phase2_v124_guarded_continue.py",
    )
    for line in proc.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        command = parts[1]
        if pid == os.getpid() or "run_bonect_phase2_vm_teacher_validation.py" in command:
            continue
        if not any(token in command for token in watched):
            continue
        if not process_owned_by_current_worktree(pid, command):
            continue
        active.append({"pid": pid, "command": command})
    return active


def wait_for_current_worktree_idle(
    *,
    state_path: Path,
    poll_sec: float,
    timeout_sec: float,
) -> None:
    started = time.monotonic()
    next_log = 0.0
    while True:
        active = active_current_worktree_processes()
        if not active:
            append_jsonl(
                state_path,
                {"event": "vm_worktree_idle", "timestamp": now_text()},
            )
            return
        elapsed = time.monotonic() - started
        if timeout_sec > 0 and elapsed >= timeout_sec:
            raise RuntimeError(
                "timed out waiting for current-worktree Bonect processes: "
                + json.dumps(active, ensure_ascii=False)
            )
        if time.monotonic() >= next_log:
            record = {
                "event": "waiting_for_vm_worktree_idle",
                "timestamp": now_text(),
                "elapsed_sec": elapsed,
                "active": active,
            }
            append_jsonl(state_path, record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
            next_log = time.monotonic() + 300.0
        time.sleep(max(1.0, poll_sec))


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")[:48]


def fresh_run_id(base_run_id: str) -> str:
    if not (HISTORY_ROOT / base_run_id).exists() and not (
        GENERATED_CONFIG_ROOT / base_run_id
    ).exists():
        return base_run_id
    retry = 2
    while True:
        candidate = f"{base_run_id}_r{retry}"
        if not (HISTORY_ROOT / candidate).exists() and not (
            GENERATED_CONFIG_ROOT / candidate
        ).exists():
            return candidate
        retry += 1


def runner_command(
    *,
    run_id: str,
    scenario: str,
    scenario_cfg: Mapping[str, Any],
    params: Mapping[str, float],
    execute: bool,
) -> List[str]:
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
    statement_timeout_ms = max(
        0,
        int(scenario_cfg.get("statement_timeout_ms", 300000)),
    )
    cmd = [
        sys.executable,
        str(RUNNER),
        "--scenario-set",
        "bonect",
        "--bonect-bottlenecks",
        scenario,
        "--run-id",
        run_id,
        "--use-resource-estimator",
        "--baseline-only",
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
        str(statement_timeout_ms),
        "--validation-warmup-timeout-sec",
        str(warmup_sec),
        "--runtime-monitor-warmup-sec",
        str(monitor_warmup_sec),
        "--runtime-monitor-interval-sec",
        "1",
        "--vm-runtime-cpu-mode",
        VM_TEACHER_CPU_MODE,
        "--vm-runtime-memory-mode",
        VM_TEACHER_MEMORY_MODE,
        "--vm-runtime-io-mode",
        VM_TEACHER_IO_MODE,
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
    if execute:
        cmd.append("--run")
    normalized = normalized_params(params)
    for name in COST_PARAM_KEYS:
        cmd.extend(["--bonect-initial-param", f"{name}={normalized[name]:.17g}"])
    return cmd


def compact_plan_hashes(record: Mapping[str, Any]) -> List[Dict[str, Any]]:
    hashes = record.get("plan_hashes") or []
    compact: List[Dict[str, Any]] = []
    if not isinstance(hashes, list):
        return compact
    for item in hashes:
        if not isinstance(item, Mapping):
            continue
        compact.append(
            {
                "queryid": item.get("queryid"),
                "plan_mode": item.get("plan_mode"),
                "plan_hash": item.get("plan_hash"),
                "plan_summary": item.get("plan_summary"),
                "query": item.get("query"),
            }
        )
    return compact


def compact_plan_features(plan_estimation: Mapping[str, Any]) -> Dict[str, float]:
    metadata = plan_estimation.get("estimation_metadata") or {}
    breakdown = metadata.get("resource_breakdown") or {}
    features: Dict[str, float] = {}
    for resource_name in ("cpu", "memory", "io", "io_read", "io_write"):
        item = breakdown.get(resource_name) or {}
        calibration = item.get("calibration_breakdown") or {}
        learned = (
            calibration.get("learned_model")
            or calibration.get("learned_model_fallback")
            or {}
        )
        learned_features = learned.get("features") or {}
        if not isinstance(learned_features, Mapping):
            continue
        for name, raw_value in learned_features.items():
            value = finite_float(raw_value)
            if value is not None:
                features.setdefault(str(name), value)
    return dict(sorted(features.items()))


def feature_signature(features: Mapping[str, float]) -> str:
    payload = json.dumps(dict(features), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compact_result(
    *,
    run_id: str,
    scenario: str,
    role: str,
    params: Mapping[str, float],
    gates: Mapping[str, Any],
) -> Dict[str, Any]:
    scenario_dir = HISTORY_ROOT / run_id / scenario
    histories = sorted(scenario_dir.glob("*optimizer_history*.jsonl"))
    if not histories:
        raise RuntimeError(f"optimizer history missing for {run_id}")
    history_path = histories[-1]
    runtime: Dict[str, Any] = {}
    db_view: Dict[str, Any] = {}
    baseline_only: Dict[str, Any] = {}
    strong_gate: Dict[str, Any] = {}
    baseline_top_sql: Dict[str, Any] = {}
    plan_estimation: Dict[str, Any] = {}
    for record in iter_jsonl(history_path):
        event = record.get("event")
        if event == "baseline_runtime_resource_summary":
            runtime = record
        elif event == "db_view_monitor_summary" and str(record.get("label")) == "baseline":
            db_view = record
        elif event == "baseline_only_complete":
            baseline_only = record
        elif event == "baseline_strong_constraint_gate":
            strong_gate = record
        elif event == "baseline_top_sql":
            baseline_top_sql = record
        elif event == "baseline_only_plan_resource_estimation":
            plan_estimation = record
    if not baseline_only:
        raise RuntimeError(f"baseline_only_complete missing for {run_id}")
    stats = runtime.get("resource_stats") or {}
    benchmark = runtime.get("benchmark") or baseline_only.get("benchmark") or {}
    benchmark_latency = runtime.get("benchmark_latency") or {}
    sources = {
        name: ((stats.get(name) or {}).get("value"))
        for name in ("cpu_source", "memory_source", "io_source", "io_disk_devices")
    }
    io_read = finite_float((runtime.get("resource_normalized") or {}).get("io_read"))
    io_write = finite_float((runtime.get("resource_normalized") or {}).get("io_write"))
    generated_config = GENERATED_CONFIG_ROOT / run_id / scenario / f"{history_path.stem.split('_optimizer_history')[0]}.json"
    generated_param_keys: List[str] = []
    if generated_config.is_file():
        generated_cfg = read_json(generated_config)
        generated_param_keys = sorted((generated_cfg.get("initial_params") or {}).keys())
    plan_features = compact_plan_features(plan_estimation)
    result = {
        "event": "teacher_validation_point_complete",
        "timestamp": now_text(),
        "run_id": run_id,
        "scenario": scenario,
        "role": role,
        "params": normalized_params(params),
        "history_path": str(history_path),
        "generated_config_path": str(generated_config),
        "generated_initial_param_keys": generated_param_keys,
        "resource_normalized": runtime.get("resource_normalized") or {},
        "utilization_pct": runtime.get("utilization_pct") or {},
        "resource_sources": sources,
        "stable_sample_count": stats.get("stable_sample_count") or {},
        "io_read_mib_s": io_read,
        "io_write_mib_s": io_write,
        "benchmark": benchmark,
        "benchmark_latency": benchmark_latency,
        "db_qps": db_view.get("db_qps"),
        "db_xact_tps": db_view.get("db_xact_tps"),
        "strong_none_gate": strong_gate,
        "baseline_only_gate_passed": baseline_only.get("strong_none_gate_passed"),
        "baseline_only_gate_failure": baseline_only.get("strong_none_gate_failure"),
        "plan_capture_file": baseline_top_sql.get("plan_capture_file"),
        "plan_capture_count": baseline_top_sql.get("plan_capture_count"),
        "all_sql_count": baseline_top_sql.get("all_sql_count"),
        "plan_hashes": compact_plan_hashes(baseline_top_sql),
        "plan_estimated_resource_normalized": (
            plan_estimation.get("estimated_resource_normalized") or {}
        ),
        "plan_features": plan_features,
        "plan_feature_count": len(plan_features),
        "plan_feature_signature": feature_signature(plan_features) if plan_features else "",
        "workload_health": load_workload_health(
            run_id=run_id,
            scenario=scenario,
            gates=gates,
        ),
    }
    issues = point_measurement_issues(result, gates)
    result["measurement_issues"] = issues
    result["measurement_valid"] = not issues
    if role in ("default_before", "default_after"):
        result["default_baseline_gate"] = default_baseline_gate(result, gates)
    return result


def mean(values: Sequence[Optional[float]]) -> Optional[float]:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return sum(clean) / len(clean) if clean else None


def pct_change(value: Optional[float], baseline: Optional[float]) -> Optional[float]:
    if value is None or baseline is None or abs(baseline) <= 1e-12:
        return None
    return (value - baseline) / abs(baseline) * 100.0


def symmetric_drift_pct(left: Optional[float], right: Optional[float]) -> Optional[float]:
    if left is None or right is None:
        return None
    denominator = (abs(left) + abs(right)) / 2.0
    if denominator <= 1e-12:
        return 0.0 if abs(left - right) <= 1e-12 else None
    return abs(left - right) / denominator * 100.0


def record_value(record: Mapping[str, Any], group: str, name: str) -> Optional[float]:
    values = record.get(group) or {}
    return finite_float(values.get(name)) if isinstance(values, Mapping) else None


def baseline_average(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "db_qps": mean([finite_float(before.get("db_qps")), finite_float(after.get("db_qps"))]),
        "resource_normalized": {
            name: mean(
                [
                    record_value(before, "resource_normalized", name),
                    record_value(after, "resource_normalized", name),
                ]
            )
            for name in ("cpu", "memory", "io", "io_read", "io_write")
        },
        "utilization_pct": {
            name: mean(
                [
                    record_value(before, "utilization_pct", name),
                    record_value(after, "utilization_pct", name),
                ]
            )
            for name in ("cpu", "memory", "io")
        },
        "p95": mean([extract_latency(before, "p95"), extract_latency(after, "p95")]),
        "p99": mean([extract_latency(before, "p99"), extract_latency(after, "p99")]),
    }


def extract_latency(record: Mapping[str, Any], percentile: str) -> Optional[float]:
    benchmark = record.get("benchmark") or {}
    latency = record.get("benchmark_latency") or {}
    key = f"latency_{percentile}"
    return finite_float(benchmark.get(key)) or finite_float(latency.get(f"all_{percentile}_avg"))


def value_in_range(value: Optional[float], bounds: Sequence[Any]) -> bool:
    if value is None or len(bounds) != 2:
        return False
    low = finite_float(bounds[0])
    high = finite_float(bounds[1])
    return low is not None and high is not None and low <= value <= high


def telemetry_gate(
    records: Sequence[Mapping[str, Any]],
    gates: Mapping[str, Any],
) -> Dict[str, Any]:
    required_source = str(gates.get("required_io_source") or "vm_disk_device")
    required_device = str(gates.get("required_io_device") or "vda")
    min_samples = max(1, int(gates.get("min_stable_samples", 45)))
    checks: List[Dict[str, Any]] = []
    for record in records:
        sources = record.get("resource_sources") or {}
        stable = record.get("stable_sample_count") or {}
        io_read = finite_float(record.get("io_read_mib_s")) or 0.0
        io_write = finite_float(record.get("io_write_mib_s")) or 0.0
        stable_values = [finite_float(stable.get(name)) for name in ("cpu", "memory", "io")]
        stable_ok = all(value is not None and value >= min_samples for value in stable_values)
        source_ok = str(sources.get("io_source") or "") == required_source
        device_ok = required_device in str(sources.get("io_disk_devices") or "")
        io_nonzero = io_read > 0.0 or io_write > 0.0
        params_ok = sorted(record.get("generated_initial_param_keys") or []) == sorted(COST_PARAM_KEYS)
        checks.append(
            {
                "role": record.get("role"),
                "stable_ok": stable_ok,
                "stable_sample_count": stable,
                "source_ok": source_ok,
                "device_ok": device_ok,
                "io_nonzero": io_nonzero,
                "io_read_mib_s": io_read,
                "io_write_mib_s": io_write,
                "exactly_seven_generated_params": params_ok,
                "passed": stable_ok and source_ok and device_ok and io_nonzero and params_ok,
            }
        )
    return {"passed": all(item["passed"] for item in checks), "checks": checks}


def plan_hash_set(record: Mapping[str, Any]) -> set[Tuple[Any, Any, Any]]:
    return {
        (item.get("queryid"), item.get("plan_mode"), item.get("plan_hash"))
        for item in record.get("plan_hashes") or []
        if isinstance(item, Mapping) and item.get("plan_hash")
    }


def evaluate_scenario(
    *,
    scenario: str,
    scenario_cfg: Mapping[str, Any],
    gates: Mapping[str, Any],
    before: Mapping[str, Any],
    teacher: Mapping[str, Any],
    after: Mapping[str, Any],
) -> Dict[str, Any]:
    baseline = baseline_average(before, after)
    baseline_db_qps = finite_float(baseline.get("db_qps"))
    teacher_db_qps = finite_float(teacher.get("db_qps"))
    db_gain_pct = pct_change(teacher_db_qps, baseline_db_qps)
    resource_delta_pct: Dict[str, Optional[float]] = {}
    resource_delta_abs: Dict[str, Optional[float]] = {}
    for resource in ("cpu", "memory", "io"):
        candidate_value = record_value(teacher, "resource_normalized", resource)
        baseline_value = finite_float((baseline.get("resource_normalized") or {}).get(resource))
        resource_delta_pct[resource] = pct_change(candidate_value, baseline_value)
        resource_delta_abs[resource] = (
            candidate_value - baseline_value
            if candidate_value is not None and baseline_value is not None
            else None
        )
    db_drift_pct = symmetric_drift_pct(
        finite_float(before.get("db_qps")),
        finite_float(after.get("db_qps")),
    )
    resource_drift_pct = {
        resource: symmetric_drift_pct(
            record_value(before, "resource_normalized", resource),
            record_value(after, "resource_normalized", resource),
        )
        for resource in ("cpu", "memory", "io")
    }
    drift_passed = (
        db_drift_pct is not None
        and db_drift_pct <= float(gates.get("max_default_db_qps_drift_pct", 10.0))
        and all(
            value is not None
            and value <= float(gates.get("max_default_resource_drift_pct", 15.0))
            for value in resource_drift_pct.values()
        )
    )
    telemetry = telemetry_gate([before, teacher, after], gates)
    p95_gain = pct_change(extract_latency(teacher, "p95"), finite_float(baseline.get("p95")))
    p99_gain = pct_change(extract_latency(teacher, "p99"), finite_float(baseline.get("p99")))
    latency_guard = (
        (p95_gain is None or p95_gain <= float(gates.get("max_p95_regression_pct", 200.0)))
        and (p99_gain is None or p99_gain <= float(gates.get("max_p99_regression_pct", 500.0)))
    )
    min_db_gain = float(gates.get("min_db_qps_gain_pct", 15.0))
    db_gate = db_gain_pct is not None and db_gain_pct >= min_db_gain
    sweet = gates.get("sweet_spots") or {}
    teacher_util = teacher.get("utilization_pct") or {}
    teacher_resource = teacher.get("resource_normalized") or {}
    sweet_checks = {
        "cpu": value_in_range(finite_float(teacher_util.get("cpu")), sweet.get("cpu_pct") or []),
        "memory": value_in_range(
            finite_float(teacher_util.get("memory")),
            sweet.get("memory_pct") or [],
        ),
        "io": value_in_range(
            finite_float(teacher_resource.get("io")),
            sweet.get("io_mib_s") or [],
        ),
    }
    param_rms_distance = normalized_param_rms_distance(
        scenario_cfg.get("default_params") or DEFAULT_COST_PARAMS,
        scenario_cfg.get("teacher_params") or {},
        gates.get("param_distance_reference_ranges") or {},
    )
    param_distance_passed = param_rms_distance >= float(
        gates.get("min_default_teacher_param_rms_distance", 0.0)
    )
    goal_checks: Dict[str, Any]
    if scenario == "none":
        none_ranges = gates.get("none_baseline_ranges") or {}
        baseline_util = baseline.get("utilization_pct") or {}
        baseline_resource = baseline.get("resource_normalized") or {}
        baseline_no_bottleneck = {
            "cpu": value_in_range(
                finite_float(baseline_util.get("cpu")),
                none_ranges.get("cpu_pct") or [],
            ),
            "memory": value_in_range(
                finite_float(baseline_util.get("memory")),
                none_ranges.get("memory_pct") or [],
            ),
            "io": value_in_range(
                finite_float(baseline_resource.get("io")),
                none_ranges.get("io_mib_s") or [],
            ),
        }
        resources_up = [
            name
            for name, value in resource_delta_abs.items()
            if value is not None and value > 0.0
        ]
        baseline_metric = {
            "cpu": finite_float(baseline_util.get("cpu")),
            "memory": finite_float(baseline_util.get("memory")),
            "io": finite_float(baseline_resource.get("io")),
        }
        teacher_metric = {
            "cpu": finite_float(teacher_util.get("cpu")),
            "memory": finite_float(teacher_util.get("memory")),
            "io": finite_float(teacher_resource.get("io")),
        }
        sweet_lift = gates.get("min_none_sweet_lift") or {}
        sweet_lift_resources = [
            name
            for name in ("cpu", "memory", "io")
            if sweet_checks[name]
            and baseline_metric[name] is not None
            and teacher_metric[name] is not None
            and teacher_metric[name] - baseline_metric[name]
            >= float(sweet_lift.get(name, 0.0))
        ]
        goal_checks = {
            "baseline_no_bottleneck": baseline_no_bottleneck,
            "baseline_no_bottleneck_passed": all(baseline_no_bottleneck.values()),
            "resources_up": resources_up,
            "any_resource_up": bool(resources_up),
            "sweet_resources": [name for name, passed in sweet_checks.items() if passed],
            "any_resource_in_sweet_spot": any(sweet_checks.values()),
            "sweet_lift_resources": sweet_lift_resources,
            "any_resource_lifted_into_sweet_spot": bool(sweet_lift_resources),
            "default_teacher_param_rms_distance": param_rms_distance,
            "default_teacher_param_distance_passed": param_distance_passed,
        }
        resource_goal_passed = (
            goal_checks["baseline_no_bottleneck_passed"]
            and goal_checks["any_resource_up"]
            and goal_checks["any_resource_lifted_into_sweet_spot"]
            and param_distance_passed
        )
    else:
        target = scenario
        baseline_target_util = finite_float(
            (baseline.get("utilization_pct") or {}).get(target)
        )
        teacher_target_util = finite_float(teacher_util.get(target))
        target_delta = resource_delta_abs.get(target)
        other_resources_up = [
            name
            for name, value in resource_delta_abs.items()
            if name != target and value is not None and value > 0.0
        ]
        non_target_resources = [
            name for name in ("cpu", "memory", "io") if name != target
        ]
        non_target_max = float(
            gates.get("single_bottleneck_non_target_max_util_pct", 79.999)
        )
        baseline_non_target_not_bottleneck = {
            name: (
                finite_float((baseline.get("utilization_pct") or {}).get(name)) is not None
                and float((baseline.get("utilization_pct") or {}).get(name)) <= non_target_max
            )
            for name in non_target_resources
        }
        target_relief_pp = (
            baseline_target_util - teacher_target_util
            if baseline_target_util is not None and teacher_target_util is not None
            else None
        )
        min_target_relief_pp = float(gates.get("min_target_relief_util_pp", 5.0))
        preferred_target_relief_pp = max(
            min_target_relief_pp,
            float(gates.get("preferred_target_relief_util_pp", 10.0)),
        )
        other_up_into_sweet = [
            name
            for name in other_resources_up
            if sweet_checks.get(name)
        ]
        non_target_sweet_resources = [
            name for name in non_target_resources if sweet_checks.get(name)
        ]
        goal_checks = {
            "target": target,
            "baseline_target_utilization_pct": baseline_target_util,
            "teacher_target_utilization_pct": teacher_target_util,
            "baseline_target_is_bottleneck": (
                baseline_target_util is not None
                and baseline_target_util
                >= float(gates.get("single_bottleneck_target_min_util_pct", 80.0))
            ),
            "teacher_target_not_bottleneck": (
                teacher_target_util is not None and teacher_target_util < 80.0
            ),
            "target_resource_down": target_delta is not None and target_delta < 0.0,
            "other_resources_up": other_resources_up,
            "any_other_resource_up": bool(other_resources_up),
            "baseline_non_target_not_bottleneck": baseline_non_target_not_bottleneck,
            "exactly_one_baseline_bottleneck": all(
                baseline_non_target_not_bottleneck.values()
            ),
            "target_relief_util_pp": target_relief_pp,
            "min_target_relief_util_pp": min_target_relief_pp,
            "preferred_target_relief_util_pp": preferred_target_relief_pp,
            "target_relief_large_enough": (
                target_relief_pp is not None and target_relief_pp >= min_target_relief_pp
            ),
            "target_relief_preferred": (
                target_relief_pp is not None
                and target_relief_pp >= preferred_target_relief_pp
            ),
            "sweet_resources": [name for name, passed in sweet_checks.items() if passed],
            "other_resources_up_into_sweet": other_up_into_sweet,
            "any_other_resource_up_into_sweet": bool(other_up_into_sweet),
            "non_target_sweet_resources": non_target_sweet_resources,
            "any_non_target_resource_in_sweet": bool(non_target_sweet_resources),
            "default_teacher_param_rms_distance": param_rms_distance,
            "default_teacher_param_distance_passed": param_distance_passed,
        }
        resource_goal_passed = all(
            [
                goal_checks["baseline_target_is_bottleneck"],
                goal_checks["exactly_one_baseline_bottleneck"],
                goal_checks["teacher_target_not_bottleneck"],
                goal_checks["target_resource_down"],
                goal_checks["any_other_resource_up"],
                goal_checks["target_relief_large_enough"],
                goal_checks["any_non_target_resource_in_sweet"],
                goal_checks["default_teacher_param_distance_passed"],
            ]
        )
    strict_passed = bool(
        db_gate
        and resource_goal_passed
        and drift_passed
        and telemetry.get("passed")
        and latency_guard
    )
    teacher_hashes = plan_hash_set(teacher)
    baseline_hashes = plan_hash_set(before) | plan_hash_set(after)
    return {
        "event": "teacher_validation_scenario_evaluation",
        "timestamp": now_text(),
        "scenario": scenario,
        "strict_passed": strict_passed,
        "db_qps": {
            "default_before": finite_float(before.get("db_qps")),
            "baseline_average": baseline_db_qps,
            "teacher": teacher_db_qps,
            "default_after": finite_float(after.get("db_qps")),
            "gain_pct": db_gain_pct,
            "required_gain_pct": min_db_gain,
            "passed": db_gate,
        },
        "resource_baseline_average": baseline.get("resource_normalized") or {},
        "resource_teacher": teacher.get("resource_normalized") or {},
        "resource_delta_abs": resource_delta_abs,
        "resource_delta_pct": resource_delta_pct,
        "utilization_baseline_average_pct": baseline.get("utilization_pct") or {},
        "utilization_teacher_pct": teacher.get("utilization_pct") or {},
        "sweet_checks": sweet_checks,
        "resource_goal": goal_checks,
        "resource_goal_passed": resource_goal_passed,
        "default_drift": {
            "db_qps_pct": db_drift_pct,
            "resource_pct": resource_drift_pct,
            "passed": drift_passed,
        },
        "telemetry_gate": telemetry,
        "latency_guard": {
            "p95_regression_pct": p95_gain,
            "p99_regression_pct": p99_gain,
            "passed": latency_guard,
        },
        "plan_change": {
            "detected": teacher_hashes != baseline_hashes,
            "baseline_plan_hash_count": len(baseline_hashes),
            "teacher_plan_hash_count": len(teacher_hashes),
            "teacher_only_plan_hash_count": len(teacher_hashes - baseline_hashes),
        },
        "teacher_params": normalized_params(scenario_cfg.get("teacher_params") or {}),
        "fallback_dbqps_candidate": bool(db_gain_pct is not None and db_gain_pct > 0.0),
        "point_run_ids": {
            "default_before": before.get("run_id"),
            "teacher": teacher.get("run_id"),
            "default_after": after.get("run_id"),
        },
    }


def load_completed_points(
    state_path: Path,
    gates: Mapping[str, Any],
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    terminal: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for record in iter_jsonl(state_path):
        scenario = str(record.get("scenario") or "")
        role = str(record.get("role") or "")
        if scenario not in SCENARIO_ORDER or role not in POINT_ROLES:
            continue
        event = str(record.get("event") or "")
        if event == "teacher_validation_point_complete" or event.endswith("_failed"):
            terminal[(scenario, role)] = record
    completed: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for key, record in terminal.items():
        if record.get("event") != "teacher_validation_point_complete":
            continue
        if point_measurement_issues(record, gates):
            continue
        if key[1] in ("default_before", "default_after"):
            if not default_baseline_gate(record, gates)["passed"]:
                continue
        completed[key] = record
    return completed


def find_control_decisions(run_id: str) -> List[Path]:
    run_dir = HISTORY_ROOT / run_id
    if not run_dir.is_dir():
        return []
    paths = list(run_dir.rglob("control_decision_*.md"))
    paths.extend(run_dir.rglob("control_decision_*.json"))
    return sorted(set(paths))


def validate_predecessor(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    cfg = manifest.get("predecessor") or {}
    if not isinstance(cfg, Mapping) or not cfg:
        return {"configured": False, "passed": True}
    state_path = resolve_project_path(str(cfg.get("state_path") or ""))
    if not state_path.is_file():
        raise RuntimeError(f"predecessor state is missing: {state_path}")
    required_indices = {
        int(value) for value in (cfg.get("required_point_indices") or [])
    }
    terminal_records: Dict[int, Dict[str, Any]] = {}
    run_ids: set[str] = set()
    for record in iter_jsonl(state_path):
        index_raw = record.get("point_index")
        try:
            point_index = int(index_raw)
        except (TypeError, ValueError):
            point_index = -1
        if point_index not in required_indices:
            continue
        run_id = str(record.get("run_id") or "")
        if run_id:
            run_ids.add(run_id)
        event = str(record.get("event") or "")
        if event == "calibration_point_complete" or event.endswith("_failed"):
            terminal_records[point_index] = record
    completed = {
        point_index: record
        for point_index, record in terminal_records.items()
        if str(record.get("event") or "") == "calibration_point_complete"
    }
    failed = [
        record
        for record in terminal_records.values()
        if str(record.get("event") or "").endswith("_failed")
    ]
    missing = sorted(required_indices - set(completed))
    min_stable_samples = max(
        1,
        int(float((manifest.get("gates") or {}).get("min_stable_samples", 45))),
    )
    invalid_pairs = sorted(
        point_index
        for point_index, record in completed.items()
        if bool(cfg.get("require_paired_baseline_valid", True))
        and record.get("paired_baseline_valid") is not True
    )
    invalid_measurements: Dict[int, Dict[str, Any]] = {}
    for point_index, record in completed.items():
        point_issues = predecessor_measurement_issues(
            record,
            min_stable_samples=min_stable_samples,
            require_capture=True,
        )
        paired = record.get("paired_baseline") or {}
        paired_issues = predecessor_measurement_issues(
            paired,
            min_stable_samples=min_stable_samples,
            require_capture=False,
        )
        if point_issues or paired_issues:
            invalid_measurements[point_index] = {
                "point_issues": point_issues,
                "paired_baseline_issues": paired_issues,
            }
    decisions: List[str] = []
    if bool(cfg.get("reject_control_decisions", True)):
        for run_id in sorted(run_ids):
            decisions.extend(str(path) for path in find_control_decisions(run_id))
    passed = (
        not missing
        and not invalid_pairs
        and not invalid_measurements
        and (not bool(cfg.get("reject_failed_events", True)) or not failed)
        and not decisions
    )
    result = {
        "configured": True,
        "passed": passed,
        "campaign_id": cfg.get("campaign_id"),
        "state_path": str(state_path),
        "required_point_indices": sorted(required_indices),
        "completed_point_indices": sorted(completed),
        "missing_point_indices": missing,
        "invalid_paired_baseline_indices": invalid_pairs,
        "invalid_measurements": invalid_measurements,
        "failed_event_count": len(failed),
        "failed_events": failed,
        "control_decisions": decisions,
    }
    if decisions:
        raise ControlDecisionPending(json.dumps(result, ensure_ascii=False))
    if not passed:
        raise RuntimeError(
            "predecessor campaign is not safely complete: "
            + json.dumps(result, ensure_ascii=False)
        )
    return result


def write_report(
    *,
    manifest: Mapping[str, Any],
    campaign_dir: Path,
    state_path: Path,
) -> Tuple[Path, Path]:
    gates = manifest.get("gates") or {}
    completed = load_completed_points(state_path, gates)
    evaluations: Dict[str, Dict[str, Any]] = {}
    scenarios = manifest.get("scenarios") or {}
    for scenario in SCENARIO_ORDER:
        records = [completed.get((scenario, role)) for role in POINT_ROLES]
        if all(isinstance(record, Mapping) for record in records):
            evaluations[scenario] = evaluate_scenario(
                scenario=scenario,
                scenario_cfg=scenarios[scenario],
                gates=gates,
                before=records[0] or {},
                teacher=records[1] or {},
                after=records[2] or {},
            )
    summary = {
        "campaign_id": manifest.get("campaign_id"),
        "updated_at": now_text(),
        "source_audit": manifest.get("source_audit"),
        "complete_scenario_count": len(evaluations),
        "all_scenarios_complete": len(evaluations) == len(SCENARIO_ORDER),
        "all_teachers_strict_passed": (
            len(evaluations) == len(SCENARIO_ORDER)
            and all(item.get("strict_passed") for item in evaluations.values())
        ),
        "evaluations": evaluations,
    }
    summary_path = campaign_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report_path = REPORT_ROOT / f"{manifest.get('campaign_id')}.md"
    lines = [
        "# Phase2 VM Teacher ",
        "",
        f"`{summary['updated_at']}`  ",
        f"`{manifest.get('source_audit')}`  ",
        " PostgreSQL teacher teacher ",
        "",
        "## ",
        "",
        "|  |  | DB QPS  -> teacher |  |  |  |",
        "|---|---:|---|---:|---|---:|",
    ]
    for scenario in SCENARIO_ORDER:
        evaluation = evaluations.get(scenario)
        if not evaluation:
            roles = [role for role in POINT_ROLES if (scenario, role) in completed]
            lines.append(
                f"| {scenario} | {len(roles)}/3 | - | - |  |  |"
            )
            continue
        qps = evaluation.get("db_qps") or {}
        resource_goal = evaluation.get("resource_goal") or {}
        if scenario == "none":
            resource_text = (
                "=" + ",".join(resource_goal.get("sweet_resources") or [])
                + "; =" + ",".join(resource_goal.get("resources_up") or [])
            )
        else:
            resource_text = (
                f"{scenario}; ="
                + ",".join(resource_goal.get("other_resources_up") or [])
                + "; ="
                + ",".join(resource_goal.get("non_target_sweet_resources") or [])
            )
        lines.append(
            "| {scenario} | 3/3 | {baseline:.6f} -> {teacher:.6f} | {gain:+.3f}% | {resource} | {passed} |".format(
                scenario=scenario,
                baseline=float(qps.get("baseline_average") or 0.0),
                teacher=float(qps.get("teacher") or 0.0),
                gain=float(qps.get("gain_pct") or 0.0),
                resource=resource_text,
                passed="" if evaluation.get("strict_passed") else "",
            )
        )
    lines.extend(
        [
            "",
            "## ",
            "",
        ]
    )
    for scenario in SCENARIO_ORDER:
        lines.append(f"### {scenario}")
        lines.append("")
        evaluation = evaluations.get(scenario)
        if not evaluation:
            lines.append("")
            lines.append("")
            continue
        qps = evaluation["db_qps"]
        lines.append(
            f"- DB QPS `{qps.get('default_before')}` `{qps.get('default_after')}`"
            f" `{qps.get('baseline_average')}`teacher `{qps.get('teacher')}`"
            f" `{qps.get('gain_pct')}%`"
        )
        lines.append(
            f"- `{json.dumps(evaluation.get('resource_baseline_average'), ensure_ascii=False)}`"
        )
        lines.append(
            f"-  teacher`{json.dumps(evaluation.get('resource_teacher'), ensure_ascii=False)}`"
        )
        lines.append(
            f"- `{json.dumps(evaluation.get('resource_delta_pct'), ensure_ascii=False)}`"
        )
        lines.append(
            f"- `{json.dumps(evaluation.get('default_drift'), ensure_ascii=False)}`"
        )
        lines.append(
            f"- `{json.dumps(evaluation.get('resource_goal'), ensure_ascii=False)}`"
        )
        lines.append(
            f"- IO/`passed={evaluation.get('telemetry_gate', {}).get('passed')}`"
            f"`{json.dumps(evaluation.get('plan_change'), ensure_ascii=False)}`"
        )
        lines.append(
            f"- `strict_passed={evaluation.get('strict_passed')}`"
            f"DB QPS `{evaluation.get('fallback_dbqps_candidate')}`"
        )
        lines.append("")
    lines.extend(
        [
            "## ",
            "",
            " VM teacher  estimator teacher labels leave-one-teacher-out  top-15  PostgreSQL ",
            "",
        ]
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path, summary_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate four Binary-Plan teacher points on the VM with bracketed default baselines."
        )
    )
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument(
        "--scenario",
        action="append",
        choices=[*SCENARIO_ORDER, "all"],
        default=[],
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--wait-for-idle", action="store_true")
    parser.add_argument("--idle-poll-sec", type=float, default=30.0)
    parser.add_argument("--idle-timeout-sec", type=float, default=0.0)
    parser.add_argument("--continue-on-failure", action="store_true")
    parser.add_argument("--rerun-completed", action="store_true")
    args = parser.parse_args()

    manifest_path = resolve_project_path(args.manifest)
    manifest = read_json(manifest_path)
    validate_manifest(manifest)
    campaign_id = str(manifest.get("campaign_id") or "phase2_vm_teacher_validation")
    campaign_dir = REPORT_ROOT / campaign_id
    campaign_dir.mkdir(parents=True, exist_ok=True)
    state_path = campaign_dir / "state.jsonl"
    manifest_copy_path = campaign_dir / "manifest.json"
    manifest_copy_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    requested = args.scenario or ["all"]
    selected = list(SCENARIO_ORDER) if "all" in requested else [
        scenario for scenario in SCENARIO_ORDER if scenario in requested
    ]
    scenarios = manifest.get("scenarios") or {}
    default_params = normalized_params(manifest.get("default_params") or {})
    gates = manifest.get("gates") or {}
    completed = (
        {}
        if args.rerun_completed
        else load_completed_points(state_path, gates)
    )

    if not args.execute:
        print(f"manifest={manifest_path}", flush=True)
        print(f"campaign_dir={campaign_dir}", flush=True)
        for scenario in selected:
            scenario_cfg = scenarios[scenario]
            for role in POINT_ROLES:
                params = (
                    normalized_params(scenario_cfg.get("teacher_params") or {})
                    if role == "teacher"
                    else default_params
                )
                run_id = (
                    f"pg_bonect_phase2_vm_teacher_v79_{scenario}_{slug(role)}"
                )
                cmd = runner_command(
                    run_id=run_id,
                    scenario=scenario,
                    scenario_cfg=scenario_cfg,
                    params=params,
                    execute=False,
                )
                initial_param_count = sum(
                    1 for token in cmd if token == "--bonect-initial-param"
                )
                if initial_param_count != len(COST_PARAM_KEYS):
                    raise RuntimeError(
                        f"dry-run command has {initial_param_count} initial params, expected 7"
                    )
                print(json.dumps({"scenario": scenario, "role": role, "command": cmd}), flush=True)
        write_report(
            manifest=manifest,
            campaign_dir=campaign_dir,
            state_path=state_path,
        )
        return 0

    append_jsonl(
        state_path,
        {
            "event": "teacher_validation_campaign_start",
            "timestamp": now_text(),
            "campaign_id": campaign_id,
            "selected_scenarios": selected,
            "manifest": str(manifest_path),
            "wait_for_idle": bool(args.wait_for_idle),
        },
    )
    if args.wait_for_idle:
        wait_for_current_worktree_idle(
            state_path=state_path,
            poll_sec=args.idle_poll_sec,
            timeout_sec=args.idle_timeout_sec,
        )
    else:
        active = active_current_worktree_processes()
        if active:
            raise RuntimeError(
                "refusing to start while current-worktree Bonect processes are active: "
                + json.dumps(active, ensure_ascii=False)
            )

    try:
        predecessor = validate_predecessor(manifest)
    except ControlDecisionPending as exc:
        append_jsonl(
            state_path,
            {
                "event": "teacher_validation_predecessor_control_decision_pending",
                "timestamp": now_text(),
                "detail": str(exc),
            },
        )
        print(str(exc), flush=True)
        return 3
    except Exception as exc:
        append_jsonl(
            state_path,
            {
                "event": "teacher_validation_predecessor_gate_failed",
                "timestamp": now_text(),
                "error": str(exc),
            },
        )
        print(str(exc), flush=True)
        return 4
    append_jsonl(
        state_path,
        {
            "event": "teacher_validation_predecessor_gate_passed",
            "timestamp": now_text(),
            "predecessor": predecessor,
        },
    )

    failed_scenarios: List[str] = []
    try:
        for scenario in selected:
            scenario_cfg = scenarios[scenario]
            scenario_failed = False
            for role in POINT_ROLES:
                if (scenario, role) in completed:
                    print(f"skip completed {scenario}/{role}", flush=True)
                    continue
                params = (
                    normalized_params(scenario_cfg.get("teacher_params") or {})
                    if role == "teacher"
                    else default_params
                )
                base_run_id = f"pg_bonect_phase2_vm_teacher_v79_{scenario}_{slug(role)}"
                run_id = fresh_run_id(base_run_id)
                started = {
                    "event": "teacher_validation_point_start",
                    "timestamp": now_text(),
                    "campaign_id": campaign_id,
                    "scenario": scenario,
                    "role": role,
                    "run_id": run_id,
                    "params": params,
                }
                append_jsonl(state_path, started)
                print(json.dumps(started, ensure_ascii=False), flush=True)
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
                    decision_record = {
                        "event": "teacher_validation_control_decision_pending",
                        "timestamp": now_text(),
                        "scenario": scenario,
                        "role": role,
                        "run_id": run_id,
                        "paths": [str(path) for path in decisions],
                    }
                    append_jsonl(state_path, decision_record)
                    raise ControlDecisionPending(json.dumps(decision_record, ensure_ascii=False))
                if proc.returncode != 0:
                    failed = {
                        **started,
                        "event": "teacher_validation_point_failed",
                        "finished_at": now_text(),
                        "returncode": proc.returncode,
                    }
                    append_jsonl(state_path, failed)
                    print(json.dumps(failed, ensure_ascii=False), flush=True)
                    scenario_failed = True
                    if not args.continue_on_failure:
                        write_report(
                            manifest=manifest,
                            campaign_dir=campaign_dir,
                            state_path=state_path,
                        )
                        return proc.returncode or 1
                    break
                try:
                    result = compact_result(
                        run_id=run_id,
                        scenario=scenario,
                        role=role,
                        params=params,
                        gates=gates,
                    )
                except Exception as exc:
                    failed = {
                        **started,
                        "event": "teacher_validation_point_measurement_failed",
                        "finished_at": now_text(),
                        "error": str(exc),
                    }
                    append_jsonl(state_path, failed)
                    print(json.dumps(failed, ensure_ascii=False), flush=True)
                    write_report(
                        manifest=manifest,
                        campaign_dir=campaign_dir,
                        state_path=state_path,
                    )
                    return 6
                if role in ("default_before", "default_after"):
                    baseline_gate = result.get("default_baseline_gate") or {}
                    if baseline_gate.get("passed") is not True:
                        failed = {
                            **result,
                            "event": "teacher_validation_default_baseline_failed",
                            "finished_at": now_text(),
                        }
                        append_jsonl(state_path, failed)
                        print(json.dumps(failed, ensure_ascii=False), flush=True)
                        write_report(
                            manifest=manifest,
                            campaign_dir=campaign_dir,
                            state_path=state_path,
                        )
                        return 5
                elif result.get("measurement_valid") is not True:
                    failed = {
                        **result,
                        "event": "teacher_validation_point_measurement_failed",
                        "finished_at": now_text(),
                    }
                    append_jsonl(state_path, failed)
                    print(json.dumps(failed, ensure_ascii=False), flush=True)
                    write_report(
                        manifest=manifest,
                        campaign_dir=campaign_dir,
                        state_path=state_path,
                    )
                    return 6
                append_jsonl(state_path, result)
                completed[(scenario, role)] = result
                print(json.dumps(result, ensure_ascii=False), flush=True)
                write_report(
                    manifest=manifest,
                    campaign_dir=campaign_dir,
                    state_path=state_path,
                )
            if scenario_failed:
                failed_scenarios.append(scenario)
                continue
            records = [completed.get((scenario, role)) for role in POINT_ROLES]
            if all(isinstance(record, Mapping) for record in records):
                evaluation = evaluate_scenario(
                    scenario=scenario,
                    scenario_cfg=scenario_cfg,
                    gates=manifest.get("gates") or {},
                    before=records[0] or {},
                    teacher=records[1] or {},
                    after=records[2] or {},
                )
                append_jsonl(state_path, evaluation)
                print(json.dumps(evaluation, ensure_ascii=False), flush=True)
    except ControlDecisionPending as exc:
        print(str(exc), flush=True)
        write_report(
            manifest=manifest,
            campaign_dir=campaign_dir,
            state_path=state_path,
        )
        return 3

    report_path, summary_path = write_report(
        manifest=manifest,
        campaign_dir=campaign_dir,
        state_path=state_path,
    )
    summary = read_json(summary_path)
    completed_record = {
        "event": "teacher_validation_campaign_complete",
        "timestamp": now_text(),
        "campaign_id": campaign_id,
        "failed_scenarios": failed_scenarios,
        "all_teachers_strict_passed": summary.get("all_teachers_strict_passed"),
        "report_path": str(report_path),
        "summary_path": str(summary_path),
        "next_action": (
            "build_leave_one_teacher_out_estimator_labels"
            if summary.get("all_teachers_strict_passed")
            else "diagnose_workload_profile_or_teacher_transfer_failure_before_blind_recovery"
        ),
    }
    append_jsonl(state_path, completed_record)
    print(json.dumps(completed_record, ensure_ascii=False), flush=True)
    return 0 if summary.get("all_teachers_strict_passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())

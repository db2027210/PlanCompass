#!/usr/bin/env python3
"""Broadcast the four mysql_only Algorithm C lane states every five minutes."""

from __future__ import annotations

import argparse
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_sgd_env_agents_20260723"
CONTROLLER_LOG = HISTORY / "controller_patrol_log_20260723.jsonl"
DEFAULT_LOG = HISTORY / "automated_four_lane_five_minute_broadcast_20260726.jsonl"
REPORTER_LOCK = HISTORY / "four_lane_five_minute_broadcast.lock"
REPORTER_STATE = HISTORY / "four_lane_five_minute_broadcast.state.json"
READINESS = ROOT / "shell/verify_mysql_sgd_v4_readiness.py"
PLAN_STATUS = (
    ROOT
    / "config/formal/frozen_mysql_workloads_and_parameters_20260723_v3"
    / "plan_workload_registries/strict_plan_registry_status_20260723.json"
)
HOST04_PATROL = HISTORY / "host04_mysql_capture_patrol/latest/patrol_summary.json"
MANUAL_CONTROLLERS = HISTORY / "manual_controllers"
PRODUCTION_CONFIGS = ROOT / "config/formal/mysql_sgd_production"

SCENARIOS = (
    ("job_memory", "JOB / Memory", "02 / /dev/nvme1n1", "job"),
    ("hybench_xp_io", "XP / IO", "02 / /dev/nvme0n1p1", "xp"),
    ("tpch_memory", "TPC-H / Memory", "node01 / /mnt/nvme1n1", "tpch"),
    ("tpcc_cpu", "TPC-C / CPU", "node01 / /mnt/mysql_nvme0n1", "tpcc"),
)
TARGET_RESOURCES = {
    "job_memory": "memory",
    "hybench_xp_io": "io",
    "tpch_memory": "memory",
    "tpcc_cpu": "cpu",
}

STOP = False


def request_stop(_signum: int, _frame: object) -> None:
    global STOP
    STOP = True


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def latest_campaign(key: str) -> tuple[dict[str, Any], Path | None]:
    candidates = sorted(
        (HISTORY / key / "formal_sgd_campaigns").glob("*/campaign_runtime.json"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    if not candidates:
        return {}, None
    loaded = [(load_json(path), path) for path in candidates]
    for state, path in loaded:
        if pid_live(state.get("campaign_pid")) or pid_live(state.get("workload_pid")):
            return state, path
    return loaded[0]


def line_count(path: Path) -> int:
    try:
        with path.open("rb") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def json_payload(line: str) -> dict[str, Any]:
    start = line.find("{")
    if start < 0:
        return {}
    try:
        value = json.loads(line[start:])
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def workload_replay_progress(
    run_root: Path, benchmark: str
) -> tuple[str, str]:
    workload_log = run_root / "workload.stdout.log"
    try:
        lines = workload_log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    session: dict[str, Any] = {}
    sample: dict[str, Any] = {}
    for line in lines:
        if "_FORMAL_SESSION_JSON=" in line or '"marker":"FORMAL_SQL_SESSION_JSON"' in line:
            candidate = json_payload(line)
            if candidate:
                session = candidate
        if "_FORMAL_SAMPLE_JSON=" in line:
            candidate = json_payload(line)
            if candidate:
                sample = candidate

    start_ms = session.get("measurement_start_epoch_ms")
    maximum = int(session.get("maximum_measurement_seconds", 600) or 600)
    last_ms = sample.get("epoch_ms")
    if not isinstance(last_ms, (int, float)):
        last_ms = time.time() * 1000
    elapsed = (
        max(0, min(maximum, int((last_ms - start_ms) / 1000)))
        if isinstance(start_ms, (int, float))
        else 0
    )
    timing = f"={elapsed}/{maximum}s({elapsed / max(maximum, 1):.0%})"

    if benchmark == "job":
        completed = line_count(run_root / "completion_events.jsonl")
        replay = f"SQL={completed}{timing}"
    elif benchmark in {"hybench_ap", "hybench_xp"}:
        ap_total = int(sample.get("xap_or_ap_total", 0) or 0)
        tp_total = int(sample.get("xtp_total", 0) or 0)
        label = "AP" if benchmark == "hybench_ap" else "XP"
        replay = f"{label}={ap_total}, TP={tp_total}{timing}"
    else:
        completed = int(sample.get("completed_total", 0) or 0)
        replay = f"/={completed}{timing}"

    resource_count = line_count(run_root / "resource_samples.jsonl")
    database_count = line_count(run_root / "database_samples.jsonl")
    position = (
        f"{run_root.name}resource:L{resource_count} / db:L{database_count}"
    )
    return replay, position


def latest_measurement_evidence(run_root: Path) -> tuple[str, Path | None]:
    candidates = [
        run_root / name
        for name in (
            "resource_samples.jsonl",
            "database_samples.jsonl",
            "workload.stdout.log",
            "completion_events.jsonl",
            "at_outcomes.jsonl",
            "tpcc_sensitive_events.jsonl",
            "r0_result.json",
            "failure.txt",
        )
        if (run_root / name).is_file()
    ]
    if not candidates:
        return "-", None
    newest = max(candidates, key=lambda path: path.stat().st_mtime_ns)
    updated = datetime.fromtimestamp(newest.stat().st_mtime).astimezone()
    count = line_count(newest) if newest.suffix in {".jsonl", ".log", ".tsv"} else 1
    return f"{newest.name}:L{count} @{updated:%H:%M:%S}", newest


def pid_live(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return False
    if pid <= 1:
        return False
    return Path(f"/proc/{pid}/stat").is_file()


def controller_pid_live(value: Any, scenario: str) -> bool:
    if not pid_live(value):
        return False
    try:
        command = Path(f"/proc/{int(value)}/cmdline").read_bytes().replace(b"\0", b" ")
    except OSError:
        return False
    return (
        b"manual_mysql_sgd_lane_controller.py" in command
        and f"--scenario {scenario}".encode() in command
    )


def file_recent(path: Path, max_age_seconds: float = 90.0) -> bool:
    try:
        return time.time() - path.stat().st_mtime <= max_age_seconds
    except OSError:
        return False


def controller_state_live(
    state: dict[str, Any], scenario: str, state_path: Path
) -> bool:
    return controller_pid_live(state.get("controller_pid"), scenario) or file_recent(
        state_path
    )


def current_round(campaign: dict[str, Any], run_root: Path | None) -> str:
    for name in ("outer_round", "round_index", "current_round", "round"):
        value = campaign.get(name)
        if value is not None:
            return str(value)
    if run_root is not None:
        rounds = sorted(
            (path.name for path in run_root.glob("round_*") if path.is_dir()),
            reverse=True,
        )
        if rounds:
            return rounds[0]
    phase = str(campaign.get("phase", ""))
    if phase.startswith("R0"):
        return "R0"
    accepted = campaign.get("accepted_shrinkage")
    return f"outer-{int(accepted)}" if isinstance(accepted, int) else "-"


def controller_events() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for line in CONTROLLER_LOG.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    except OSError:
        pass
    return rows


def latest_event(rows: list[dict[str, Any]], event: str) -> dict[str, Any]:
    return next((row for row in reversed(rows) if row.get("event") == event), {})


def readiness() -> dict[str, Any]:
    try:
        result = subprocess.run(
            [sys.executable, str(READINESS), "--json"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        value = json.loads(result.stdout)
        return value if isinstance(value, dict) else {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        # A verifier execution/parsing failure is never readiness evidence.
        return {"status": "VERIFY_ERROR", "passed": False, "failed": -1, "ready": False}


def latest_summary(prefix: str) -> dict[str, Any]:
    candidates = sorted(
        HISTORY.glob(f"{prefix}/**/summary.json"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    return load_json(candidates[0]) if candidates else {}


def plan_by_benchmark() -> dict[str, dict[str, Any]]:
    value = load_json(PLAN_STATUS)
    return {
        str(row.get("benchmark")): row
        for row in value.get("scenarios", [])
        if isinstance(row, dict)
    }


def accepted_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    report = latest_event(rows, "five_minute_progress_report")
    raw = report.get("accepted_shrinkage_by_scenario", {})
    return {
        scenario: int(raw.get(scenario, 0)) if isinstance(raw, dict) else 0
        for scenario, *_ in SCENARIOS
    }


def evidence_flags() -> dict[str, dict[str, bool]]:
    return {
        "ap": {
            "template": (ROOT / "config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft/evidence/ap_template_manifest_20260723.json").is_file(),
            "envelope": (HISTORY / "ap/formal_mysql_normalized_envelope_readback.json").is_file(),
            "isolation": (HISTORY / "ap/native_mysqld_continuous_gate_20260723_0707.json").is_file(),
            "t_input": (ROOT / "config/formal/frozen_mysql_workloads_and_parameters_20260723_v5_estimator_t/checksums.sha256").is_file(),
        },
        "job": {
            "template": (ROOT / "config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft/evidence/job_template_manifest_20260723.json").is_file(),
            "envelope": (HISTORY / "job/formal_mysql_normalized_envelope_readback.json").is_file(),
            "oat_contract": (ROOT / "config/formal/frozen_mysql_job_oat_calibration_20260723_v1/checksums.sha256").is_file(),
        },
        "xp": {
            "template": (ROOT / "config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft/evidence/xp_template_deployment_20260723.json").is_file(),
            "envelope": (HISTORY / "xp/formal_mysql_normalized_envelope_readback.json").is_file(),
            "binding_contract": (ROOT / "config/formal/frozen_mysql_workloads_and_parameters_20260723_v3/plan_workload_registries/hybench_xp.production_binding_capture_contract_v1.json").is_file(),
        },
        "tpch": {
            "template": (ROOT / "config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft/evidence/tpch_template_manifest_20260723.json").is_file(),
            "envelope": (HISTORY / "tpch/formal_mysql_normalized_envelope_readback.json").is_file(),
            "binding_contract": (ROOT / "config/formal/frozen_mysql_workloads_and_parameters_20260723_v3/plan_workload_registries/tpch.production_binding_capture_contract_v1.json").is_file(),
        },
        "tpcc": {
            "template": (ROOT / "config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft/evidence/tpcc_e1_template_deployment_20260723.json").is_file(),
            "envelope": (HISTORY / "tpcc/formal_mysql_normalized_envelope_readback.json").is_file(),
            "binding_contract": (ROOT / "config/formal/frozen_mysql_workloads_and_parameters_20260723_v3/plan_workload_registries/tpcc.production_binding_capture_contract_v1.json").is_file(),
        },
    }


def _compact_envelope(envelope: Any) -> dict[str, Any]:
    if not isinstance(envelope, dict):
        return {}
    return {
        name: envelope.get(name)
        for name in (
            "cpu_quota_cores",
            "cpuset_cpus",
            "cpuset_mems",
            "memory_bytes",
            "memory_swap_bytes",
            "io_read_bps",
            "io_write_bps",
            "pids_max",
            "cpu_isolation_degree",
            "numa_isolation_degree",
        )
    }


def _root_path(value: Any) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def direct_state(scenario: str) -> dict[str, Any]:
    return load_json(MANUAL_CONTROLLERS / scenario / "direct_campaign_state.json")


def production_details(scenario: str) -> tuple[dict[str, Any], dict[str, Any]]:
    config = load_json(PRODUCTION_CONFIGS / f"{scenario}.json")
    profile_path = _root_path(config.get("profile"))
    profile = load_json(profile_path) if profile_path is not None else {}
    return config, profile


def pre_tuning_details(scenario: str, manual: dict[str, Any]) -> dict[str, Any]:
    context_path = MANUAL_CONTROLLERS / scenario / "pre_tuning_context.json"
    context = load_json(context_path)
    source = context if context else manual
    bottleneck = source.get("bottleneck_construction")
    snapshot_value = source.get("pre_tuning_snapshot")
    bottleneck = bottleneck if isinstance(bottleneck, dict) else {}
    snapshot_value = snapshot_value if isinstance(snapshot_value, dict) else {}
    config, profile = production_details(scenario)
    r0_path = _root_path(config.get("r0_evidence"))
    r0 = load_json(r0_path) if r0_path is not None else {}
    utilization = r0.get("resource_utilization")
    utilization = utilization if isinstance(utilization, dict) else {}
    target_resource = TARGET_RESOURCES[scenario]
    non_target_utilization = {
        name: value
        for name, value in utilization.items()
        if name != target_resource
    }
    readback = snapshot_value.get("effective_resource_readback")
    readback = readback if isinstance(readback, dict) else {}
    effective = _compact_envelope(readback.get("values"))
    configured = _compact_envelope(snapshot_value.get("configured_resource_envelope"))
    if profile:
        configured = {
            "cpu_quota_cores": profile.get("cpu_cores"),
            "cpuset_cpus": profile.get("cpuset"),
            "cpuset_mems": profile.get("mems"),
            "memory_bytes": profile.get("memory_bytes"),
            "memory_swap_bytes": profile.get(
                "memory_swap_bytes", profile.get("memory_bytes")
            ),
            "io_read_bps": profile.get("read_bps"),
            "io_write_bps": profile.get("write_bps"),
            "pids_max": profile.get("pids"),
            "innodb_buffer_pool_bytes": profile.get("buffer_pool_bytes"),
        }
    attempts = bottleneck.get("attempts")
    process_inventory = snapshot_value.get("process_inventory")
    process_inventory = process_inventory if isinstance(process_inventory, dict) else {}
    interference = snapshot_value.get("dynamic_interference")
    interference = interference if isinstance(interference, dict) else {}
    inventory_evidence = process_inventory.get("evidence")
    inventory_evidence = (
        inventory_evidence if isinstance(inventory_evidence, dict) else {}
    )
    interference_path = _root_path(inventory_evidence.get("path"))
    interference_evidence = (
        load_json(interference_path) if interference_path is not None else {}
    )
    config_pointer = snapshot_value.get("config_json")
    config_pointer = config_pointer if isinstance(config_pointer, dict) else {}
    return {
        "target_resource": target_resource,
        "bottleneck_status": (
            "PROVEN"
            if r0.get("passed") is True
            else str(bottleneck.get("status") or "NOT_PROVEN")
        ),
        "target_utilization": utilization.get(target_resource),
        "non_target_utilization": non_target_utilization,
        "attempts": len(attempts) if isinstance(attempts, list) else 0,
        "snapshot_status": (
            "FROZEN"
            if context
            else (
                "VALIDATED"
                if manual.get("prerequisites", {}).get("pre_tuning_snapshot") is True
                else "NOT_FROZEN"
            )
        ),
        "captured_at": snapshot_value.get("captured_at"),
        "host_id": snapshot_value.get("host_id"),
        "hostname": snapshot_value.get("hostname"),
        "device": snapshot_value.get("device"),
        "configured_resource_envelope": configured,
        "effective_resource_readback": effective,
        "config_json": config_pointer.get("path"),
        "process_inventory_complete": process_inventory.get("complete") is True,
        "co_resident_process_count": len(
            process_inventory.get("database_and_benchmark_processes", [])
        ) if isinstance(process_inventory.get("database_and_benchmark_processes"), list) else 0,
        "dynamic_interference_passed": (
            interference_evidence.get("passed") is True
            or process_inventory.get("passed") is True
            or interference.get("passed") is True
        ),
        "dynamic_interference_window_seconds": (
            interference_evidence.get("latest_window_seconds")
            or interference_evidence.get("host_analysis", {}).get("window_seconds")
            or interference.get("window_seconds")
        ),
        "context_path": str(context_path.relative_to(ROOT)) if context else None,
    }


def auto_launch_blocker(manual: dict[str, Any], direct: dict[str, Any]) -> str:
    if (
        direct.get("phase") == "SGD_RUNNING"
        or manual.get("phase") == "SGD_RUNNING"
    ):
        return ""
    prerequisites = manual.get("prerequisites")
    prerequisites = prerequisites if isinstance(prerequisites, dict) else {}
    ordered = (
        ("experience_table_t", "experience table T "),
        ("static_isolation", " CPU/NUMA/"),
        ("dynamic_interference", "180 "),
        ("r0", "R0 "),
        ("exclusive_lease", " lane lease "),
    )
    for name, message in ordered:
        if prerequisites.get(name) is False:
            return message
    errors = manual.get("auto_launch_errors")
    if isinstance(errors, list) and errors:
        return str(errors[0]).replace("|", "/")
    if prerequisites.get("global_readiness") is False:
        return "global readiness NOT_READY"
    return "-"


def scenario_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flags = evidence_flags()
    plans = plan_by_benchmark()
    accepted = accepted_counts(events)
    host04 = load_json(HOST04_PATROL)
    ap_run = latest_summary("ap/ap_v5_endpoint_*")

    progress = {
        "ap": "/envelope/56 binding17/17 endpointT",
        "job": "/envelope/9strict plan27OAT",
        "xp": "/envelope/binding",
        "tpch": "900scapture12/22 binding",
        "tpcc": "/envelope/34 SQLnative mysqld",
    }
    if ap_run:
        progress["ap"] += f"endpoint={ap_run.get('status', 'UNKNOWN')}"
    if plans.get("job", {}).get("strict_cluster_usable") is True:
        progress["job"] += "strict cluster"

    action = {
        "ap": "T SHA=f54c542a…production factory/R0exec SGD",
        "job": "PG/lease27OAT",
        "xp": "tracetrace/raw EXPLAIN",
        "tpch": "Q2/Q3/Q5/Q8/Q9/Q10/Q14/Q19/Q21/Q22raw EXPLAIN/Thost04=" + str(host04.get("decision", "UNKNOWN")),
        "tpcc": "TPC-Hbinding/raw EXPLAINhost04=" + str(host04.get("decision", "UNKNOWN")),
    }
    result: list[dict[str, Any]] = []
    for scenario, label, machine, key in SCENARIOS:
        direct = direct_state(scenario)
        campaign, runtime_path = latest_campaign(key)
        if campaign and runtime_path is not None:
            run_root = runtime_path.parent
            phase = str(campaign.get("phase", "UNKNOWN"))
            is_recalibration = str(campaign.get("run_id", "")).startswith(
                "recal_qpsv2_"
            )
            campaign_pid = campaign.get("campaign_pid", "-")
            workload_pid = campaign.get("workload_pid", "-")
            terminal_phase = phase in {"FAILED", "R0_COMPLETE", "COMPLETE", "STOPPED"}
            experiment_live = (
                not terminal_phase
                and (
                    pid_live(campaign_pid)
                    or pid_live(workload_pid)
                    or (
                        phase in {"R0_RUNNING", "SGD_RUNNING"}
                        and (
                            file_recent(run_root / "resource_samples.jsonl")
                            or file_recent(run_root / "database_samples.jsonl")
                        )
                    )
                    or (
                        direct.get("phase") == "SGD_RUNNING"
                        and phase in {"R0_RUNNING", "SGD_RUNNING"}
                    )
                )
            )
            manual_path = MANUAL_CONTROLLERS / scenario / "state.json"
            manual = load_json(manual_path)
            controller_live = controller_state_live(manual, scenario, manual_path)
            experiment_live = experiment_live or (
                direct.get("phase") == "SGD_RUNNING"
                and pid_live(manual.get("campaign_pid"))
            )
            direct_stage = str(direct.get("stage") or manual.get("direct_stage") or "")
            current_stage = (
                f"RECALIBRATING/{phase}"
                if is_recalibration
                else f"{direct_stage}/{phase}" if direct_stage and experiment_live else
                (phase if experiment_live else str(manual.get("phase") or f"LAST_{phase}"))
            )
            current_round_value = (
                "calibration"
                if is_recalibration
                else f"direct-{direct.get('round')}"
                if experiment_live and direct.get("round") is not None
                else (current_round(campaign, run_root) if experiment_live else "-")
            )
            resources = line_count(run_root / "resource_samples.jsonl")
            database = line_count(run_root / "database_samples.jsonl")
            replay_progress, measurement_position = workload_replay_progress(
                run_root, str(campaign.get("benchmark") or key)
            )
            recent_evidence, evidence_path = latest_measurement_evidence(run_root)
            error = str(campaign.get("error", "")).strip()
            if phase == "R0_RUNNING":
                blocker = (
                    "QPS" if is_recalibration else ""
                    if evidence_path is not None and file_recent(evidence_path)
                    else "90"
                )
            elif phase == "FAILED":
                blocker = "" + (error or "failure.txt")
            elif phase == "R0_COMPLETE":
                blocker = (
                    ""
                    if is_recalibration
                    else ""
                )
            elif is_recalibration:
                blocker = "QPS"
            else:
                blocker = ""
            accepted_count = int(direct.get("accepted_shrinkage", 0) or 0)
            rejected_count = int(direct.get("rejected_count", 0) or 0)
            technical_count = int(
                direct.get("technical_failure_count", 0) or 0
            )
            if direct.get("phase") == "BLOCKED":
                blocker = "" + str(
                    direct.get("blocked_reason") or " technical_failure "
                )
            elif technical_count:
                blocker = (
                    f"={technical_count}"
                    "Outer-loop exploration/"
                )
            result.append(
                {
                    "scenario": scenario,
                    "benchmark": str(campaign.get("benchmark") or key),
                    "experiment": label,
                    "machine_disk": machine,
                    "alive": (
                        ":"
                        if experiment_live
                        else (": / :" if controller_live else ": / :")
                    ),
                    "round": current_round_value,
                    "outer_exploration": (
                        ""
                        if is_recalibration
                        else f"{int(direct.get('round', 0) or 0)}/100"
                        if direct.get("round") is not None
                        else current_round_value
                    ),
                    "stage": current_stage,
                    "replay_progress": replay_progress,
                    "measurement_position": measurement_position,
                    "recent_evidence": recent_evidence,
                    "progress": (
                        f"run={campaign.get('run_id', run_root.name)}phase={phase}"
                        f"campaign PID={campaign_pid} workload PID={workload_pid}"
                        f"resource/db samples={resources}/{database}"
                    ),
                    "accepted_shrinkage": (
                        ""
                        if is_recalibration
                        else f"{int(direct.get('accepted_shrinkage', 0) or 0)}/10"
                    ),
                    "blocker_action": blocker,
                    "evidence_flags": flags[key],
                    "pre_tuning": pre_tuning_details(scenario, manual),
                    "auto_launch_blocker": auto_launch_blocker(manual, direct),
                }
            )
            continue
        manual_path = MANUAL_CONTROLLERS / scenario / "state.json"
        manual = load_json(manual_path)
        manual_phase = manual.get("phase", "NOT_STARTED")
        manual_pid = manual.get("controller_pid", "-")
        result.append(
            {
                "scenario": scenario,
                "benchmark": key,
                "experiment": label,
                "machine_disk": machine,
                "alive": (
                    ": / :"
                    if controller_state_live(manual, scenario, manual_path)
                    else ": / :"
                ),
                    "round": "-",
                    "outer_exploration": (
                        f"{int(direct.get('round', 0) or 0)}/100"
                        if direct.get("round") is not None
                        else "-"
                    ),
                    "stage": str(manual_phase),
                    "replay_progress": "-",
                    "measurement_position": "-",
                    "recent_evidence": "-",
                "progress": f"manual PID={manual_pid} phase={manual_phase}{progress[key]}",
                "accepted_shrinkage": (
                    f"{int(direct.get('accepted_shrinkage', accepted[scenario]) or 0)}/10"
                ),
                "blocker_action": action[key],
                "evidence_flags": flags[key],
                "pre_tuning": pre_tuning_details(scenario, manual),
                "auto_launch_blocker": auto_launch_blocker(manual, direct),
            }
        )
    return result


def snapshot(sequence: int) -> dict[str, Any]:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from shell.monitor_mysql_algorithm_c_four_scenes import row as current_row

    ready = readiness()
    rows = []
    for scenario, _label, _machine, benchmark in SCENARIOS:
        current = current_row(scenario)
        parts = [part.strip() for part in current["Experiment benchmark"].split("/")]
        experiment = " / ".join(parts[:-1])
        current_benchmark = parts[-1]
        rows.append(
            {
                "experiment": experiment,
                "benchmark": current_benchmark or benchmark,
                "machine_disk": current["Machine"],
                "alive": current["Alive"],
                "outer_exploration": current["Outer-loop exploration"],
                "accepted_shrinkage": current["Accepted refinement"],
                "stage": current["Current phase"],
                "replay_progress": current["Replay progress"],
                "measurement_position": current["Replay and measurement location"],
                "recent_evidence": current["Latest evidence"],
                "blocker_action": current["Blocking issue"],
            }
        )
    manual_states = [
        (
            scenario,
            MANUAL_CONTROLLERS / scenario / "state.json",
            load_json(MANUAL_CONTROLLERS / scenario / "state.json"),
        )
        for scenario, *_ in SCENARIOS
    ]
    manual_active = sum(
        controller_state_live(state, scenario, path)
        for scenario, path, state in manual_states
    )
    formal_started = sum(str(row.get("alive", "")).startswith(":") for row in rows)
    return {
        "schema_version": "mysql_sgd_five_minute_broadcast_v1",
        "sequence": sequence,
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "rows": rows,
        "manual_controllers": {"active": manual_active, "target": len(SCENARIOS)},
        "readiness": {
            "status": ready.get("status"),
            "passed": ready.get("passed"),
            "failed": ready.get("failed"),
            "ready": ready.get("ready", False),
        },
        "formal_scenarios_started": formal_started,
    }


def markdown(value: dict[str, Any]) -> str:
    lines = [
        f"[{value['timestamp']}] MySQL SGD  #{value['sequence']}",
        "Experiment benchmark | Machine | Alive | Outer-loop exploration | Accepted refinement | Current phase | "
        "Replay progress | Replay and measurement location | Latest evidence | Blocking issue",
        "---|---|---|---|---|---|---|---|---|---",
    ]
    for row in value["rows"]:
        lines.append(
            "{experiment} / {benchmark} | {machine_disk} | {alive} | "
            "{outer_exploration} | {accepted_shrinkage} | {stage} | "
            "{replay_progress} | {measurement_position} | {recent_evidence} | "
            "{blocker_action}".format(**row)
        )
    manual = value["manual_controllers"]
    lines.append(
        f" |  | {manual['active']}/{manual['target']}  | — | "
        f"— | mysql_only direct parallel | — | — | — | —"
    )
    lines.append(
        f" / 4 benchmarks | mysql_only | "
        f"={value['formal_scenarios_started']}/4 | — | — | "
        f" | — | — | — | —"
    )
    return "\n".join(lines)


def append_log(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, line.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_reporter_state(*, status: str, sequence: int, interval_seconds: int) -> None:
    value = {
        "schema_version": "mysql_sgd_progress_reporter_state_v1",
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "pid": os.getpid(),
        "status": status,
        "sequence": sequence,
        "interval_seconds": interval_seconds,
    }
    temporary = REPORTER_STATE.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, REPORTER_STATE)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--stop-file", type=Path, default=HISTORY / "STOP_FIVE_MINUTE_BROADCAST")
    args = parser.parse_args()
    if args.interval_seconds < 1:
        parser.error("--interval-seconds must be positive")
    HISTORY.mkdir(parents=True, exist_ok=True)
    lock_descriptor = os.open(REPORTER_LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock_descriptor)
        print("", file=sys.stderr)
        return 2
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    sequence = 1
    deadline = time.monotonic()
    try:
        write_reporter_state(
            status="RUNNING",
            sequence=sequence,
            interval_seconds=args.interval_seconds,
        )
        while not STOP and not args.stop_file.exists():
            value = snapshot(sequence)
            print(markdown(value), flush=True)
            append_log(args.log.resolve(), value)
            write_reporter_state(
                status="RUNNING",
                sequence=sequence,
                interval_seconds=args.interval_seconds,
            )
            if args.once:
                break
            sequence += 1
            deadline += args.interval_seconds
            while not STOP and not args.stop_file.exists() and time.monotonic() < deadline:
                time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
        print("", flush=True)
        write_reporter_state(
            status="STOPPED",
            sequence=sequence,
            interval_seconds=args.interval_seconds,
        )
        return 0
    finally:
        os.close(lock_descriptor)


if __name__ == "__main__":
    raise SystemExit(main())

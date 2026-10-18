#!/usr/bin/env python3
"""Five-minute, current-run-only monitor for four mysql_only lanes."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import time
from typing import Any

from shell.launch_mysql_algorithm_c_four_scenes import (
    ROOT,
    SCENARIOS,
    STATE_ROOT,
    frozen_manifest,
    launch_specs,
    pid_live,
)


DISPLAY = {
    "job_memory": ("JOB / Memory / job", "job_memory"),
    "hybench_xp_io": ("XP / IO / hybench_xp", "hybench_xp_io"),
    "tpch_memory": ("TPC-H / Memory / tpch", "tpch_memory"),
    "tpcc_cpu": ("TPC-C / CPU / tpcc", "tpcc_cpu"),
}
CONTROLLER_ROOT = (
    ROOT / "history/mysql_sgd_env_agents_20260723/manual_controllers"
)
PRODUCTION_ROOT = ROOT / "history/mysql_sgd_algorithm_c"
FORMAL_CAMPAIGN_ROOT = (
    ROOT / "history/mysql_sgd_env_agents_20260723"
)
FORMAL_KEYS = {
    "job_memory": "job",
    "hybench_xp_io": "xp",
    "tpch_memory": "tpch",
    "tpcc_cpu": "tpcc",
}


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def newest_runtime(scenario: str) -> tuple[Path | None, dict[str, Any]]:
    _, directory = DISPLAY[scenario]
    paths = list(
        (PRODUCTION_ROOT / directory / "production_points").glob(
            "*/campaign_runtime.json"
        )
    )
    paths.extend(
        (FORMAL_CAMPAIGN_ROOT / FORMAL_KEYS[scenario] / "formal_sgd_campaigns").glob(
            "*/campaign_runtime.json"
        )
    )
    if not paths:
        return None, {}
    path = max(paths, key=lambda item: item.stat().st_mtime)
    return path, load_json(path)


def line_count(path: Path) -> int:
    try:
        with path.open("rb") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def latest_file(point: Path | None) -> tuple[Path | None, float]:
    if point is None:
        return None, 0.0
    candidates = [
        path
        for name in (
            "resource_samples.jsonl",
            "database_samples.jsonl",
            "workload.stdout.log",
            "campaign_runtime.json",
            "r0_result.json",
        )
        if (path := point / name).is_file()
    ]
    if not candidates:
        return None, 0.0
    path = max(candidates, key=lambda item: item.stat().st_mtime)
    return path, path.stat().st_mtime


def recently_updated(path: Path, seconds: float = 90.0) -> bool:
    try:
        return time.time() - path.stat().st_mtime <= seconds
    except OSError:
        return False


def error_hint(scenario: str, runtime: dict[str, Any] | None = None) -> str:
    current_error = str((runtime or {}).get("error") or "").strip()
    if current_error:
        if "initial workload session marker timed out" in current_error:
            return "TPC-H binding capture  marker "
        if "stability gate failed" in current_error:
            return "R0 "
        if "timed out" in current_error and ("remote" in current_error or "SSH" in current_error):
            return "/SSH "
        return current_error[:120]
    if scenario == "job_memory":
        retry_log = CONTROLLER_ROOT.parent / "job" / "job_r0_v28_retry.log"
        try:
            retry_text = retry_log.read_text(
                encoding="utf-8", errors="replace"
            )
            if (
                "JOB_WAITING_FOR_NODE01_TO_NODE02_SSH" in retry_text
                and recently_updated(retry_log, 90.0)
            ):
                return " node01→node02 SSH "
        except OSError:
            pass
    candidates = list((CONTROLLER_ROOT / scenario).glob("*.stderr.log"))
    path = (
        max(candidates, key=lambda item: item.stat().st_mtime)
        if candidates
        else CONTROLLER_ROOT / scenario / "campaign.stderr.log"
    )
    try:
        lines = [line.strip() for line in path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines() if line.strip()]
    except OSError:
        return ""
    text = "\n".join(lines[-25:])
    if "JOB correctness failed" in text:
        return "JOB "
    if "port is already allocated" in text:
        return "TPC-C MySQL "
    if "mutex is busy" in text:
        return " JOB/XP "
    if "timed out" in text and ("docker" in text or "ssh" in text):
        return "/SSH "
    if "No route to host" in text:
        return "node01 "
    if "workload exited before initial marker" in text:
        return "workload "
    if lines:
        return lines[-1][:120]
    return ""


def evidence_text(point: Path | None) -> str:
    path, modified = latest_file(point)
    if path is None:
        return "-"
    stamp = datetime.fromtimestamp(modified).astimezone().strftime("%H:%M:%S")
    suffix = f":L{line_count(path)}" if path.suffix in {".jsonl", ".log"} else ""
    return f"{path.name}{suffix} @{stamp}"


def r0_profile_status(
    runtime: dict[str, Any], point: Path | None,
) -> tuple[str | None, str | None]:
    if str(runtime.get("phase") or "") != "R0_COMPLETE" or point is None:
        return None, None
    result = load_json(point / "r0_result.json")
    selected = (
        result.get("rolling_measurement_control", {})
        .get("selected_observation", {})
    )
    means = selected.get("resource_means")
    target = str(runtime.get("target_resource") or "")
    if not isinstance(means, dict) or target not in {"cpu", "memory", "io"}:
        return "R0_COMPLETE/PROFILE_NOT_PROVEN", "R0 "
    failures = []
    for resource in ("cpu", "memory", "io"):
        try:
            value = float(means[resource])
        except (KeyError, TypeError, ValueError):
            failures.append(f"{resource}=missing")
            continue
        low, high = ((0.85, 0.95) if resource == target else (0.30, 0.50))
        if not low <= value <= high:
            failures.append(f"{resource}={value:.1%} not in {low:.0%}-{high:.0%}")
    if failures:
        return "R0_COMPLETE/PROFILE_NOT_PROVEN", "".join(failures)
    return "R0_COMPLETE/PROFILE_PROVEN", "R0 "


def row(scenario: str) -> dict[str, str]:
    name, _ = DISPLAY[scenario]
    controller = load_json(CONTROLLER_ROOT / scenario / "state.json")
    runtime_path, runtime = newest_runtime(scenario)
    point = runtime_path.parent if runtime_path is not None else None

    controller_state_path = CONTROLLER_ROOT / scenario / "state.json"
    controller_live = (
        pid_live(controller.get("controller_pid"))
        or recently_updated(controller_state_path)
    )
    campaign_pid = runtime.get("campaign_pid")
    if campaign_pid is None:
        campaign_pid = controller.get("campaign_pid")
    _recent_path, recent_mtime = latest_file(point)
    active_phase = str(runtime.get("phase") or "") in {
        "PREPARING_FRESH_R0", "PREPARING_TPCH_BINDING_CAPTURE",
        "STARTING_TUNNEL_AND_SAMPLER", "R0_RUNNING", "TPCH_BINDING_RUNNING",
        "TPCH_BINDING_DRAINING",
        "MEASURING_CANDIDATE", "SGD_RUNNING",
    }
    freshness_seconds = (
        600.0
        if str(runtime.get("phase") or "") in {
            "PREPARING_FRESH_R0", "PREPARING_TPCH_BINDING_CAPTURE"
        }
        else 20.0
    )
    explicit_campaign_pid = campaign_pid is not None
    campaign_live = (
        pid_live(campaign_pid)
        or (
            not explicit_campaign_pid
            and
            active_phase
            and recent_mtime > 0
            and time.time() - recent_mtime <= freshness_seconds
        )
    )
    if str(runtime.get("phase") or "") in {
        "FAILED", "INVALID_R0_UNSTABLE", "INVALID_R0_PROFILE",
        "COMPLETE", "R0_COMPLETE", "TPCH_BINDING_CAPTURE_COMPLETE",
    } and runtime.get("algorithm_process_live") is False:
        campaign_live = False

    host = str(runtime.get("hostname") or runtime.get("host") or "-")
    device = str(runtime.get("device") or "-")
    machine = f"{host} / {device}"

    phase = str(runtime.get("phase") or controller.get("phase") or "WAITING")
    if (
        phase == "STARTING_TUNNEL_AND_SAMPLER"
        and point is not None
        and (point / "workload.stdout.log").is_file()
        and (resource_count := line_count(point / "resource_samples.jsonl")) > 0
    ):
        phase = "R0_RUNNING ( marker/)"
    profile_phase, profile_blocker = r0_profile_status(runtime, point)
    if profile_phase is not None:
        phase = profile_phase
    shared_gate_log = FORMAL_CAMPAIGN_ROOT / "host02_v31_shared_gate.log"
    shared_gate_waiting = False
    if scenario in {"job_memory", "hybench_xp_io"} and not campaign_live:
        try:
            shared_gate_waiting = (
                recently_updated(shared_gate_log, 90.0)
                and "HOST02_WAITING_FOR_3_CONSECUTIVE_SSH_CHECKS"
                in shared_gate_log.read_text(encoding="utf-8", errors="replace")
            )
        except OSError:
            shared_gate_waiting = False
    if shared_gate_waiting:
        phase = "WAITING_HOST02_SSH_GATE"
        controller_live = True
    if runtime and not campaign_live and phase in {
        "PREPARING_FRESH_R0", "STARTING_TUNNEL_AND_SAMPLER", "R0_RUNNING"
    }:
        phase = f"{phase}/EXITED"

    accepted = runtime.get("accepted_shrinkage")
    accepted_text = f"{int(accepted or 0)}/10" if runtime else "0/10"
    outer = runtime.get("outer_round")
    if runtime.get("capture_tpch_bindings") is True:
        binding_query = runtime.get("binding_query") or ""
        outer_text = f"Binding capture {binding_query}"
    else:
        outer_text = f"{outer}/10" if outer is not None else "R0"

    resource_count = (
        line_count(point / "resource_samples.jsonl") if point is not None else 0
    )
    database_count = (
        line_count(point / "database_samples.jsonl") if point is not None else 0
    )
    if resource_count or database_count:
        replay_progress = f"={resource_count}DB={database_count}"
    else:
        replay_progress = ""

    if point is None:
        position = "-"
    else:
        position = point.name
        if resource_count or database_count:
            position += f"resource:L{resource_count} / db:L{database_count}"

    if shared_gate_waiting:
        blocker = " SSH  3 "
    elif profile_blocker is not None:
        blocker = profile_blocker
    elif phase.startswith("INVALID_R0_"):
        result = load_json(point / "r0_result.json") if point is not None else {}
        stop_reason = str(
            result.get("rolling_measurement_control", {}).get("stop_reason")
            or phase.removeprefix("INVALID_R0_").lower()
        )
        blocker = f"R0 {stop_reason}"
    elif phase == "FAILED":
        blocker = str(runtime.get("error") or error_hint(scenario, runtime))
    elif phase == "R0_COMPLETE":
        blocker = "R0 "
    elif phase in {"COMPLETE", "TPCH_BINDING_CAPTURE_COMPLETE"}:
        blocker = ""
    elif campaign_live:
        blocker = ""
    else:
        blocker = error_hint(scenario, runtime)

    return {
        "Experiment benchmark": name,
        "Machine": machine,
        "Alive": (
            f":{'' if campaign_live else ''} / "
            f":{'' if controller_live else ''}"
        ),
        "Outer-loop exploration": outer_text,
        "Accepted refinement": accepted_text,
        "Current phase": phase,
        "Replay progress": replay_progress,
        "Replay and measurement location": position,
        "Latest evidence": evidence_text(point),
        "Blocking issue": blocker,
    }


def markdown(manifest: dict[str, Any], state_root: Path = STATE_ROOT) -> str:
    del state_root  # Kept for CLI compatibility; current controller state is authoritative.
    launch_specs(manifest)
    headers = (
        "Experiment benchmark", "Machine", "Alive", "Outer-loop exploration", "Accepted refinement",
        "Current phase", "Replay progress", "Replay and measurement location", "Latest evidence", "Blocking issue",
    )
    rows = [row(scenario) for scenario in SCENARIOS]
    lines = [
        f"[{datetime.now().astimezone().isoformat(timespec='seconds')}] "
        "MySQL ",
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for item in rows:
        lines.append(
            "| " + " | ".join(
                item[header].replace("|", "\\|").replace("\n", " ")
                for header in headers
            ) + " |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--state-dir", type=Path, default=STATE_ROOT)
    args = parser.parse_args(argv)
    if args.interval_seconds <= 0:
        parser.error("--interval-seconds must be positive")
    _, manifest = frozen_manifest(args.manifest)
    while True:
        print(markdown(manifest, args.state_dir), flush=True)
        if args.once:
            return 0
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())

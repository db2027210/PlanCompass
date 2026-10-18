#!/usr/bin/env python3
"""Verify that every formal Algorithm C lane completed one clean physical wave."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.prepare_pg_algorithm_c_budgeted_formal import (  # noqa: E402
    ALGORITHM_CORE_SOURCES,
    FORMAL_ROLE,
    FORMAL_RUNTIME_SOURCES,
    SCENES,
    algorithm_design_sha256,
    source_hashes,
)


REQUIRED_CONTROLLER = "run_pg_algorithm_c_budgeted_scene.py"
REQUIRED_SUPPORT = "run_pg_algorithm_c_budgeted_support.py"


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def process_cmdline(pid: int) -> Optional[str]:
    path = Path(f"/proc/{pid}/cmdline")
    try:
        return path.read_bytes().replace(b"\0", b" ").decode("utf-8", errors="replace").strip()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None


def iter_events(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def valid_baseline_row(event: Mapping[str, Any]) -> Optional[Tuple[int, str, float]]:
    if event.get("event") != "algorithm_c_baseline_worker_validation":
        return None
    validation = event.get("validation") or {}
    repetition = validation.get("baseline_repetition")
    worker = validation.get("worker_key")
    stamp = event.get("time")
    valid = bool(
        isinstance(repetition, int)
        and worker in {"w1", "w2"}
        and finite_number(stamp)
        and validation.get("status") == "ok"
        and validation.get("technical_failure") is False
        and (validation.get("resource_stats") or {}).get("resource_monitor_ok") is True
        and (validation.get("workload_completeness") or {}).get("passed") is True
    )
    return (repetition, worker, float(stamp)) if valid else None


def stable_wave_evidence(log_path: Path, started_at: float) -> Dict[str, Any]:
    baseline_groups: Dict[int, Dict[str, float]] = {}
    accounted = []
    technical_events = []
    malformed = 0
    support_expansion_events = []
    with log_path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            stamp = event.get("time")
            if not finite_number(stamp) or float(stamp) < started_at:
                continue
            name = event.get("event")
            if name in {
                "algorithm_c_candidate_technical_failure",
                "algorithm_c_inflight_physical_wave_recovered",
            }:
                technical_events.append({"event": name, "line": line_number})
            baseline = valid_baseline_row(event)
            if baseline:
                repetition, worker, baseline_stamp = baseline
                baseline_groups.setdefault(repetition, {})[worker] = baseline_stamp
            if name == "algorithm_c_physical_wave_accounted":
                accounting = event.get("accounting") or {}
                if (
                    accounting.get("valid_replays") == 2
                    and accounting.get("technical_failures") == 0
                    and accounting.get("physical_workload_invocations") == 2
                ):
                    accounted.append({
                        "line": line_number,
                        "time": float(stamp),
                        "batch_kind": event.get("batch_kind"),
                        "wave_sequence": event.get("wave_sequence"),
                    })
            if name == "algorithm_c_global_plan_coverage_ranking" and int(
                event.get("target_support_expansion_probe_count") or 0
            ) > 0:
                support_expansion_events.append({
                    "line": line_number,
                    "time": float(stamp),
                    "round_index": event.get("round_index"),
                    "probe_count": event.get("target_support_expansion_probe_count"),
                    "qps_used": event.get("target_support_expansion_uses_qps"),
                    "real_replay_required": event.get(
                        "target_support_expansion_requires_real_replay"
                    ),
                })
    baseline_waves = [
        {
            "baseline_repetition": repetition,
            "workers": sorted(workers),
            "completed_at_epoch_s": max(workers.values()),
        }
        for repetition, workers in sorted(baseline_groups.items())
        if set(workers) == {"w1", "w2"}
    ]
    return {
        "passed": bool((baseline_waves or accounted) and not technical_events and malformed == 0),
        "baseline_waves": baseline_waves,
        "accounted_candidate_waves": accounted,
        "support_expansion_events": support_expansion_events,
        "technical_events_since_launch": technical_events,
        "malformed_jsonl_rows": malformed,
    }


def verify(root: Path, *, require_search_wave: bool = False) -> Dict[str, Any]:
    root = root.resolve()
    errors: List[str] = []
    manifest_path = root / "budgeted_launch_manifest.json"
    preflight_path = root / "formal_launch_preflight.json"
    launcher_path = root / "launcher_status.json"
    for path in (manifest_path, preflight_path, launcher_path):
        if not path.is_file():
            errors.append(f"missing:{path.name}")
    if errors:
        return {
            "contract": "algorithm_c_formal_startup_stability_v1",
            "campaign_root": str(root),
            "passed": False,
            "errors": errors,
            "checked_at_epoch_s": time.time(),
        }

    manifest = read_json(manifest_path)
    preflight = read_json(preflight_path)
    launcher = read_json(launcher_path)
    if manifest.get("campaign_role") != FORMAL_ROLE:
        errors.append("formal_campaign_role_required")
    if preflight.get("passed") is not True:
        errors.append("formal_preflight_not_passed")
    if launcher.get("phase") != "launched":
        errors.append(f"launcher_not_active:{launcher.get('phase')}")
    if launcher.get("failures"):
        errors.append("launcher_has_failures")
    if launcher.get("deadline_signals"):
        errors.append("launcher_has_deadline_signals")

    sealed_source_hashes = manifest.get("source_sha256")
    observed_source_hashes = source_hashes()
    source_integrity_errors: List[str] = []
    if not isinstance(sealed_source_hashes, Mapping):
        source_integrity_errors.append("sealed_runtime_source_hashes_required")
    else:
        if set(sealed_source_hashes) != set(FORMAL_RUNTIME_SOURCES):
            source_integrity_errors.append("sealed_runtime_source_set_mismatch")
        for relative in FORMAL_RUNTIME_SOURCES:
            if sealed_source_hashes.get(relative) != observed_source_hashes.get(relative):
                source_integrity_errors.append(f"sealed_runtime_source_changed:{relative}")
    observed_design_sha256 = algorithm_design_sha256(observed_source_hashes)
    if manifest.get("algorithm_design_sha256") != observed_design_sha256:
        source_integrity_errors.append("algorithm_design_sha256_mismatch")
    errors.extend(source_integrity_errors)
    started_at = launcher.get("started_at_epoch_s")
    if not finite_number(started_at):
        errors.append("launcher_start_time_missing")
        started_at = math.inf

    scene_rows: Dict[str, Any] = {}
    pids = launcher.get("pids") or {}
    if set(pids) != set(SCENES):
        errors.append("exact_four_controller_pids_required")
    for scene in SCENES:
        scene_errors = []
        pid = pids.get(scene)
        command = process_cmdline(pid) if isinstance(pid, int) else None
        if not command:
            scene_errors.append("controller_process_missing")
        elif REQUIRED_CONTROLLER not in command or str(root / scene) not in command:
            scene_errors.append("controller_process_identity_mismatch")
        log_path = root / scene / "algorithm_c.jsonl"
        if not log_path.is_file():
            wave = {
                "passed": False,
                "baseline_waves": [],
                "accounted_candidate_waves": [],
                "technical_events_since_launch": [],
                "malformed_jsonl_rows": 0,
            }
            scene_errors.append("structured_log_missing")
        else:
            wave = stable_wave_evidence(log_path, float(started_at))
            if not wave["passed"]:
                scene_errors.append("no_clean_complete_physical_wave")
            if require_search_wave and not any(
                row.get("batch_kind") == "formal_search"
                for row in wave["accounted_candidate_waves"]
            ):
                scene_errors.append("no_clean_complete_formal_search_wave")
            if require_search_wave:
                model_path = root / scene / "experience_table_c_only.json"
                model = read_json(model_path) if model_path.is_file() else {}
                target = {
                    "job_memory": "memory",
                    "tpch_memory": "memory",
                    "xp_cpu": "cpu",
                    "xp_io": "io",
                }[scene]
                target_calibration = (
                    (((model.get("resource_prediction_calibration") or {}).get("resources") or {}).get(target))
                    or {}
                )
                if target_calibration.get("parameter_support_abstention_enabled") is True:
                    valid_expansions = [
                        event for event in wave["support_expansion_events"]
                        if event.get("qps_used") is False
                        and event.get("real_replay_required") is True
                        and int(event.get("probe_count") or 0) >= 2
                    ]
                    if not valid_expansions:
                        scene_errors.append("support_abstention_scene_lacks_bounded_expansion_evidence")
        scene_rows[scene] = {
            "pid": pid,
            "process_live_and_owned": command is not None and not any(
                error.startswith("controller_process") for error in scene_errors
            ),
            "stable_wave": wave,
            "errors": scene_errors,
        }
        errors.extend(f"{scene}:{error}" for error in scene_errors)

    support_pid = launcher.get("support_pid")
    support_command = process_cmdline(support_pid) if isinstance(support_pid, int) else None
    if not support_command:
        errors.append("support_process_missing")
    elif REQUIRED_SUPPORT not in support_command or str(root) not in support_command:
        errors.append("support_process_identity_mismatch")

    return {
        "contract": "algorithm_c_formal_startup_stability_v1",
        "campaign_root": str(root),
        "algorithm_revision": manifest.get("algorithm_revision"),
        "source_integrity": {
            "passed": not source_integrity_errors,
            "errors": source_integrity_errors,
            "formal_runtime_sources": list(FORMAL_RUNTIME_SOURCES),
            "algorithm_core_sources": list(ALGORITHM_CORE_SOURCES),
            "sealed_source_sha256": sealed_source_hashes,
            "observed_source_sha256": observed_source_hashes,
            "sealed_algorithm_design_sha256": manifest.get("algorithm_design_sha256"),
            "observed_algorithm_design_sha256": observed_design_sha256,
        },
        "passed": not errors,
        "errors": errors,
        "scene_rows": scene_rows,
        "support_pid": support_pid,
        "support_process_live_and_owned": bool(
            support_command
            and REQUIRED_SUPPORT in support_command
            and str(root) in support_command
        ),
        "checked_at_epoch_s": time.time(),
        "meaning": "all four live formal lanes completed at least one clean two-worker physical wave",
        "formal_search_wave_required": require_search_wave,
        "terminal_quality_or_efficiency_claim": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-search-wave", action="store_true")
    args = parser.parse_args()
    report = verify(args.campaign_root, require_search_wave=args.require_search_wave)
    if args.output:
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

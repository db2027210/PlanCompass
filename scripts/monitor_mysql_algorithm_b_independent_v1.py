#!/usr/bin/env python3
"""Persist a read-only progress snapshot for independent Algorithm B."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
SCENES = {
    "job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io",
}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def audit_batch(path: Path) -> dict[str, Any]:
    batch = load(path)
    measurements = batch.get("measurements") or {}
    scene_set = {
        str(row.get("scene")) for row in measurements.values()
        if isinstance(row, Mapping)
    }
    passed = (
        batch.get("status") == "PASS"
        and batch.get("formal_scope") == "mysql_only"
        and batch.get("cross_engine_comparison_prohibited") is True
        and batch.get("failures") in ([], None)
        and scene_set == SCENES
        and all(
            isinstance(row, Mapping)
            and row.get("hard_gate_passed") is True
            and row.get("coverage_complete") is True
            for row in measurements.values()
        )
    )
    return {
        "path": str(path),
        "sha256": sha(path),
        "passed": passed,
        "status": batch.get("status"),
        "elapsed_seconds": batch.get("elapsed_seconds"),
        "measurement_count": len(measurements),
        "scenes": sorted(scene_set),
        "failure_count": len(batch.get("failures") or []),
    }


def snapshot(run_root: Path) -> dict[str, Any]:
    state_path = run_root / "run_state.json"
    state = load(state_path)
    calibration: list[dict[str, Any]] = []
    for wave in range(1, 16):
        path = run_root / f"fresh_calibration/wave_{wave:02d}/batch_result.json"
        if path.is_file():
            calibration.append({"wave": wave, **audit_batch(path)})
    outer_evidence: dict[str, Any] = {}
    for outer_root in sorted(run_root.glob("outer_[0-9][0-9][0-9]")):
        if not outer_root.is_dir():
            continue
        waves = []
        for rank in range(1, 6):
            path = outer_root / f"top5_batch_{rank}/batch_result.json"
            if path.is_file():
                waves.append({"rank": rank, **audit_batch(path)})
        decisions = sorted(path.name for path in outer_root.glob("*.decision.json"))
        outer_evidence[outer_root.name] = {
            "completed_top5_waves": len(waves),
            "waves": waves,
            "decisions": decisions,
        }
    baseline_path = run_root / "formal_independent_baseline/batch_result.json"
    baseline = audit_batch(baseline_path) if baseline_path.is_file() else None
    controller_pid = int(state.get("pid") or 0)
    all_batches = (
        ([baseline] if baseline is not None else [])
        + calibration
        + [
            wave
            for outer in outer_evidence.values()
            for wave in outer["waves"]
        ]
    )
    evidence_failures = [
        str(row["path"]) for row in all_batches if row.get("passed") is not True
    ]
    monitor_status = "OBSERVING"
    if evidence_failures:
        monitor_status = "ALERT_EVIDENCE_FAILURE"
    elif state.get("status") == "RUNNING" and not pid_alive(controller_pid):
        monitor_status = "ALERT_STALE_RUNNING_CONTROLLER_DEAD"
    elif state.get("status") in {"COMPLETE", "NON_CONVERGED", "FAILED_CLOSED"}:
        monitor_status = "TERMINAL"
    return {
        "schema_version": "mysql_algorithm_b_independent_progress_monitor_v1",
        "monitor_status": monitor_status,
        "captured_at": now(),
        "run_id": run_root.name,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "controller": {
            "pid": controller_pid,
            "pid_alive": pid_alive(controller_pid),
            "status": state.get("status"),
            "stage": state.get("stage"),
            "stage_updated_at": state.get("stage_updated_at"),
            "error": state.get("error"),
        },
        "progress": {
            "baseline_complete": baseline is not None and baseline["passed"],
            "calibration_completed": len(calibration),
            "calibration_total": 15,
            "outer_attempt": int(state.get("outer_attempt") or 0),
            "accepted_rounds": state.get("accepted_rounds"),
            "interval_stable_rounds": state.get("interval_stable_rounds"),
            "converged": state.get("converged"),
        },
        "baseline": baseline,
        "calibration": calibration,
        "outer_evidence": outer_evidence,
        "evidence_failure_paths": evidence_failures,
        "source": {
            "path": str(Path(__file__).resolve()),
            "sha256": sha(Path(__file__).resolve()),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run id")
    if args.interval_seconds < 5:
        parser.error("monitor interval must be at least 5 seconds")
    run_root = (HISTORY / args.run_id).resolve()
    if run_root.parent != HISTORY.resolve() or not run_root.is_dir():
        raise RuntimeError("Algorithm B run root absent")
    output = run_root / "progress_monitor.json"
    events = run_root / "progress_monitor_events.jsonl"
    previous_signature: tuple[Any, ...] | None = None
    while True:
        value = snapshot(run_root)
        atomic(output, value)
        controller = value["controller"]
        progress = value["progress"]
        signature = (
            value["monitor_status"], controller["status"], controller["stage"],
            progress["calibration_completed"], progress["outer_attempt"],
            json.dumps(progress["accepted_rounds"], sort_keys=True),
            json.dumps(progress["converged"], sort_keys=True),
        )
        if signature != previous_signature:
            with events.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({
                    "captured_at": value["captured_at"],
                    "monitor_status": value["monitor_status"],
                    "controller": controller,
                    "progress": progress,
                }, sort_keys=True) + "\n")
            previous_signature = signature
        if value["monitor_status"] != "OBSERVING":
            return 0 if value["monitor_status"] == "TERMINAL" else 2
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run an XP Top-5 diagnostic serially with a five-call hard cap."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def event(path: Path, value: dict[str, Any]) -> None:
    item = dict(value)
    item.setdefault("timestamp_epoch", time.time())
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--support", required=True, type=Path)
    parser.add_argument("--adapter", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    selection = load(args.selection)
    if selection.get("contract") != "xp_v3g_trusted3_probe2_diagnostic_top5_v1":
        raise ValueError("selection contract mismatch")
    if selection.get("diagnostic_only") is not True:
        raise ValueError("selection is not diagnostic-only")
    candidates = selection.get("top5") or []
    if len(candidates) != 5:
        raise ValueError("selection must contain exactly five candidates")
    if [item.get("selection_role") for item in candidates] != [
        "trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"
    ]:
        raise ValueError("selection is not trusted3 + probe2")

    args.output_dir.mkdir(parents=True, exist_ok=False)
    events = args.output_dir / "events.jsonl"
    valid: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    lock_path = args.output_dir / "replay.lock"
    with lock_path.open("w", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        event(events, {
            "event": "xp_v3g_diagnostic_start",
            "candidate_count": 5,
            "maximum_physical_workload_calls": 5,
            "serial": True,
            "diagnostic_only": True,
        })
        for candidate in candidates:
            rank = int(candidate["rank"])
            point = args.output_dir / f"rank_{rank:02d}"
            point.mkdir()
            candidate_path = point / "candidate.json"
            write(candidate_path, candidate)
            event(events, {
                "event": "xp_v3g_point_started",
                "rank": rank,
                "selection_role": candidate["selection_role"],
                "candidate_score": candidate["candidate_score"],
                "feature_ratio": candidate["feature_ratio"],
                "predicted_resource": candidate["predicted_resource"],
                "utilization": candidate["utilization"],
                "dScore/dU": candidate["dScore/dU"],
                "T": candidate["T"],
                "confidence": candidate["confidence"],
                "g_j": candidate["g_j"],
                "eta": candidate["eta"],
                "delta_param": candidate["delta_param"],
                "three_level_fingerprint": candidate["three_level_fingerprint"],
            })
            env = os.environ.copy()
            env.update({
                "XP_REPLAY_CONFIG": str(args.config),
                "XP_REPLAY_SUPPORT": str(args.support),
                "PYTHONUNBUFFERED": "1",
                "PYTHONPATH": "/home/anonymous/program/resource_adaptive",
            })
            completed = subprocess.run(
                [
                    sys.executable,
                    str(args.adapter),
                    "--candidate", str(candidate_path),
                    "--output-dir", str(point),
                ],
                check=False,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            (point / "adapter.log").write_text(completed.stdout, encoding="utf-8")
            marker = point / "physical_call_started.json"
            physical_call_started = marker.exists()
            if (point / "evidence.json").exists() and completed.returncode == 0:
                evidence = load(point / "evidence.json")
                valid.append(evidence)
                event(events, {"event": "xp_v3g_point_complete", **evidence})
            else:
                failure = load(point / "technical_failure.json") if (
                    point / "technical_failure.json"
                ).exists() else {
                    "technical_failure": True,
                    "valid_replay": False,
                    "error": f"adapter returned {completed.returncode} without failure evidence",
                }
                failure.update({
                    "rank": rank,
                    "selection_role": candidate["selection_role"],
                    "physical_workload_call_started": physical_call_started,
                })
                failures.append(failure)
                event(events, {"event": "xp_v3g_point_technical_failure", **failure})

    physical_calls = sum(
        1 for rank in range(1, 6)
        if (args.output_dir / f"rank_{rank:02d}" / "physical_call_started.json").exists()
    )
    if physical_calls > 5:
        raise RuntimeError("physical workload call cap exceeded")
    baseline_restored = all(
        (args.output_dir / f"rank_{rank:02d}" / "baseline_restored.json").exists()
        for rank in range(1, 6)
    )
    summary = {
        "contract": "xp_v3g_strict_serial_top5_replay_v1",
        "diagnostic_only": True,
        "physical_workload_call_count": physical_calls,
        "maximum_physical_workload_calls": 5,
        "valid_replay_count": len(valid),
        "technical_failure_count": len(failures),
        "successful": valid,
        "technical_failures": failures,
        "baseline_restored_after_every_point": baseline_restored,
        "accepted_count_delta": 0,
        "incumbent_updated": False,
        "range_updated": False,
        "gradient_updated": False,
        "qps_used_for_inner_scoring": False,
        "qps_role": "outer_audit_only",
    }
    write(args.output_dir / "summary.json", summary)
    event(events, {"event": "xp_v3g_diagnostic_complete", **{
        key: value for key, value in summary.items()
        if key not in {"successful", "technical_failures"}
    }})
    print(json.dumps({
        "output": str(args.output_dir),
        "physical_calls": physical_calls,
        "valid": len(valid),
        "technical_failures": len(failures),
        "baseline_restored": baseline_restored,
    }, sort_keys=True), flush=True)
    return 0 if not failures and baseline_restored else 2


if __name__ == "__main__":
    raise SystemExit(main())

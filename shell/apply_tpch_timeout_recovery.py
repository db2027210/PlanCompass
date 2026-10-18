#!/usr/bin/env python3
"""Auditably enforce the pre-formal TPC-H hard wall-time timeout.

TPC-H Q1--Q22 must finish within the serialized 900-second workload contract.
The process timeout may add at most 120 seconds for monitoring and teardown;
it must never turn an over-wall replay into a valid replay.  This helper is
lane-local and refuses to mutate an active or formally started authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json_atomic(path: Path, value: Dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def active_controller_pid(scene_dir: Path) -> Optional[int]:
    pid_path = scene_dir / "controller.pid"
    if not pid_path.is_file():
        return None
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        raise RuntimeError("TPC-H controller.pid is unreadable")
    if pid > 0 and Path(f"/proc/{pid}").exists():
        return pid
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--timeout-s", type=int, default=1020)
    parser.add_argument("--timeout-grace-s", type=int, default=120)
    parser.add_argument("--failed-run", type=Path)
    args = parser.parse_args()

    target_wall_time_s = 900
    if not target_wall_time_s < args.timeout_s <= target_wall_time_s + 120:
        raise ValueError("TPC-H timeout must be in (900, 1020] seconds")
    if not 0 <= args.timeout_grace_s <= 120:
        raise ValueError("TPC-H timeout grace must be in [0, 120] seconds")

    scene_dir = args.run_root / "tpch_memory"
    config_path = scene_dir / "optimizer_config.json"
    manifest_path = args.run_root / "launch_manifest.json"
    if not config_path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("TPC-H run is missing config or launch manifest")
    pid = active_controller_pid(scene_dir)
    if pid is not None:
        raise RuntimeError(f"timeout enforcement forbidden while controller {pid} is active")
    formal_state_path = scene_dir / "algorithm_c_state.json"
    if formal_state_path.is_file():
        formal_state = read_json(formal_state_path)
        if formal_state.get("formal_started") is True:
            raise RuntimeError("timeout enforcement is forbidden after formal start")

    config = read_json(config_path)
    algorithm_c = config.setdefault("algorithm_c", {})
    if algorithm_c.get("scene") != "tpch_memory":
        raise RuntimeError("timeout enforcement target is not tpch_memory")
    workload_contract = algorithm_c.setdefault("workload_contract", {})
    concurrency = workload_contract.get(
        "benchmark_concurrency"
    ) or {}
    if int(concurrency.get("target_wall_time_s") or 0) != target_wall_time_s:
        raise RuntimeError("TPC-H serialized target wall time is not 900 seconds")
    previous_advisory = concurrency.get("target_wall_time_is_advisory")
    if previous_advisory not in {True, False}:
        raise RuntimeError("TPC-H serialized wall-time semantics are missing")
    concurrency["target_wall_time_is_advisory"] = False
    workload_contract["benchmark_concurrency"] = concurrency

    hybench = (
        config.setdefault("binary_plan_tuning", {})
        .setdefault("validation", {})
        .setdefault("hybench_xp", {})
    )
    previous = {
        "timeout_s": hybench.get("timeout_s"),
        "timeout_grace_s": hybench.get("timeout_grace_s"),
    }
    hybench["timeout_s"] = int(args.timeout_s)
    hybench["timeout_grace_s"] = int(args.timeout_grace_s)

    evidence: Dict[str, Any] = {}
    if args.failed_run is not None:
        failed_state_path = args.failed_run / "calibration_state.json"
        failed_replays_path = args.failed_run / "calibration_replays.jsonl"
        failed_state = read_json(failed_state_path)
        evidence = {
            "calibration_state": str(failed_state_path),
            "calibration_state_sha256": sha256(failed_state_path),
            "physical_workload_invocations": int(
                failed_state.get("physical_workload_invocations") or 0
            ),
            "valid_replays": int(failed_state.get("valid_replays") or 0),
            "technical_failures": int(
                failed_state.get("technical_failures") or 0
            ),
            "accepted_state_mutated": False,
        }
        if failed_replays_path.is_file():
            evidence.update(
                {
                    "calibration_replays": str(failed_replays_path),
                    "calibration_replays_sha256": sha256(failed_replays_path),
                }
            )

    helper_path = Path(__file__).resolve()
    enforcement = {
        "contract": "algorithm_c_tpch_hard_wall_time_enforcement_v2",
        "applied_before_formal_start": True,
        "controller_active": False,
        "previous": previous,
        "previous_target_wall_time_is_advisory": previous_advisory,
        "current": {
            "target_wall_time_s": target_wall_time_s,
            "target_wall_time_is_advisory": False,
            "timeout_s": int(args.timeout_s),
            "timeout_grace_s": int(args.timeout_grace_s),
        },
        "reason": "prevent_over_wall_replay_from_consuming_lane_or_becoming_valid",
        "failed_run": str(args.failed_run) if args.failed_run is not None else None,
        "failed_evidence": evidence,
        "helper": str(helper_path),
        "helper_sha256": sha256(helper_path),
        "recorded_at_epoch_s": time.time(),
    }
    workload_contract_path = scene_dir / "workload_contract.json"
    serialized_contract: Optional[Dict[str, Any]] = None
    if workload_contract_path.is_file():
        serialized_contract = read_json(workload_contract_path)
        serialized_concurrency = serialized_contract.get(
            "benchmark_concurrency"
        ) or {}
        if int(serialized_concurrency.get("target_wall_time_s") or 0) != 900:
            raise RuntimeError("standalone TPC-H workload contract wall time differs")
        serialized_concurrency["target_wall_time_is_advisory"] = False
        serialized_contract["benchmark_concurrency"] = serialized_concurrency

    algorithm_c["preformal_runtime_timeout_enforcement"] = enforcement
    write_json_atomic(config_path, config)
    if serialized_contract is not None:
        write_json_atomic(workload_contract_path, serialized_contract)
        enforcement["workload_contract_path"] = str(workload_contract_path)
        enforcement["workload_contract_sha256"] = sha256(workload_contract_path)

    enforcement["optimizer_config_sha256"] = sha256(config_path)
    manifest = read_json(manifest_path)
    manifest["tpch_timeout_enforcement"] = enforcement
    write_json_atomic(manifest_path, manifest)
    print(json.dumps(enforcement, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

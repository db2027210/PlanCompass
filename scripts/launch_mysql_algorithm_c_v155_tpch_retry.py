#!/usr/bin/env python3
"""Retry only the v154 TPC-H scene after its pre-measurement code failure."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v65.py"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_paper_baseline_20260923_v155/"
    "algorithm_c_paper_baseline_contract.json"
)
SOURCE_CAMPAIGN = HISTORY / "c_v154_four_scene_candidate_search_20260923"
SOURCE_TPCH = HISTORY / "c_v154_four_scene_candidate_search_20260923_tpch"
CHECKPOINT = HISTORY / (
    "c_v150_paper_resource_v36_bootstrap_20260923/checkpoint/prepared_binding.json"
)
QUARANTINE = HISTORY / (
    "c_v150_paper_resource_v36_bootstrap_20260923/"
    "replay_quarantine/replay_quarantine.json"
)


def load(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular JSON required: {path}")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    os.replace(temporary, path)


def cpus(value: str) -> set[int]:
    result: set[int] = set()
    for item in value.split(","):
        left, separator, right = item.partition("-")
        result.update(range(int(left), int(right) + 1) if separator else [int(left)])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run id")
    output = HISTORY / args.run_id
    campaign = HISTORY / f"{args.run_id}_launch"
    barrier = Path("/tmp/mysql_async_outer_start") / args.run_id / "algorithm_c"
    if output.exists() or campaign.exists() or barrier.exists():
        raise RuntimeError("fresh TPC-H retry paths required")

    source_manifest_path = SOURCE_CAMPAIGN / "launch_manifest.json"
    source_state_path = SOURCE_TPCH / "run_state.json"
    source_manifest = load(source_manifest_path)
    source_state = load(source_state_path)
    source_tpch = dict((source_manifest.get("launches") or {}).get("tpch_sf4_memory") or {})
    if (
        source_manifest.get("status") != "RUNNING"
        or source_manifest.get("all_four_scenes_started_together") is not True
        or set(source_manifest.get("candidate_scenes") or []) != {
            "job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io"
        }
        or source_state.get("status") != "FAILED_CLOSED"
        or source_state.get("stage") != "FAILED"
        or int(source_state.get("outer_attempt") or 0) != 0
        or source_state.get("error")
        != "RuntimeError: SHA-bound TPC-H normalization audit differs"
        or (SOURCE_TPCH / "outer_001/top5_batch_1/batch_result.json").exists()
        or source_tpch.get("controller_cpuset") != "8-15,264-271"
    ):
        raise RuntimeError("v154 TPC-H safe retry boundary differs")

    authority = Path(str(source_manifest["authority_root"])).resolve()
    fresh_r0 = Path(str(source_manifest["fresh_r0"])).resolve()
    baseline_set = Path(str(source_manifest["formal_baseline_set"]["path"])).resolve()
    baseline = Path(str(
        load(baseline_set)["scenes"]["tpch_sf4_memory"]["batch_result_path"]
    )).resolve()
    preflight = authority / "transition_preflight.json"
    prepared = authority / "prepared_binding.json"
    accounts = authority / "benchmark_accounts_binding.json"
    for path in (
        CONTROLLER, CONTRACT, CHECKPOINT, QUARANTINE, fresh_r0, baseline_set,
        baseline, preflight, prepared, accounts,
    ):
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"regular retry input required: {path}")

    campaign.mkdir(parents=True, mode=0o700)
    barrier.mkdir(parents=True, mode=0o700)
    log_path = campaign / "tpch.controller.log"
    environment = os.environ.copy()
    environment["MYSQL_TWO_WORKER_AUTHORITY_ROOT"] = str(authority)
    command = [
        sys.executable, str(CONTROLLER), "--run-id", args.run_id,
        "--checkpoint", str(CHECKPOINT),
        "--runtime-preflight", str(preflight),
        "--prepared-binding", str(prepared),
        "--benchmark-accounts", str(accounts),
        "--fresh-r0", str(fresh_r0),
        "--replay-quarantine", str(QUARANTINE),
        "--fresh-search", "--scene", "tpch_sf4_memory",
        "--scene-start-barrier", str(barrier),
        "--formal-baseline", str(baseline),
        "--formal-r0-set", str(baseline_set),
    ]
    with log_path.open("w") as log:
        process = subprocess.Popen(
            command, cwd=ROOT, env=environment, stdout=log,
            stderr=subprocess.STDOUT, start_new_session=True,
            preexec_fn=lambda: os.sched_setaffinity(0, cpus("8-15,264-271")),
        )
    atomic(campaign / "launch_manifest.json", {
        "schema_version": "mysql_algorithm_c_v155_tpch_safe_retry_launch_v1",
        "status": "RUNNING",
        "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "source_four_scene_campaign": str(SOURCE_CAMPAIGN),
        "source_four_scene_launch_manifest": str(source_manifest_path),
        "source_failed_tpch_state": str(source_state_path),
        "safe_boundary": "before_first_candidate_batch_result",
        "other_scene_completion_dependency": False,
        "other_scene_control_or_mutation_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "run_id": args.run_id,
        "pid": process.pid,
        "controller_cpuset": "8-15,264-271",
        "controller": str(CONTROLLER),
        "contract": str(CONTRACT),
        "authority_root": str(authority),
        "fresh_r0": str(fresh_r0),
        "formal_r0_set": str(baseline_set),
        "formal_baseline": str(baseline),
        "log": str(log_path),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Replay Outer5 batch 3 as a non-formal shared-disk interference diagnostic."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_v2 as alg
from scripts import run_mysql_four_scene_algorithm_c_resume_v1 as resume


HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = alg.FREEZE
CHECKPOINT = (
    HISTORY
    / "algorithm_c_outer4_checkpoint_v2_optimized_20260812"
    / "prepared_binding.json"
)
RANKINGS = (
    HISTORY
    / "four_scene_algorithm_c_boundary_resume_outer4_optimized_v11_retry1_20260812T1526p0800"
    / "outer_005/predicted_rankings.json"
)
PRIOR_PREPARED = (
    HISTORY
    / "four_scene_live_rebind_boundary_outer5_v1_20260812_retry11"
    / "prepared_binding.json"
)
PRIOR_ACCOUNTS = (
    HISTORY
    / "benchmark_accounts_boundary_outer5_v1_20260812_retry11"
    / "benchmark_accounts_binding.json"
)
FRESH_XP = (
    HISTORY
    / "algorithm_c_boundary_outer5_xp_binding_v1_20260812_retry12"
    / "prepared_binding.json"
)
SCENES = ["job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io"]
XP_IDS = {"xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def rows_by_id(document: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["worker_id"]): dict(row) for row in document["workers"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run id")

    output = HISTORY / "diagnostics" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh diagnostic output exists: {output}")
    output.mkdir(parents=True, mode=0o700)

    lock = Path("/tmp/mysql_four_scene_algorithm_c_v1.lock").open("w")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise RuntimeError("another four-scene controller owns the lock") from error

    freeze = load(FREEZE)
    checkpoint = load(CHECKPOINT)
    ranking_document = load(RANKINGS)
    prepared = rows_by_id(load(PRIOR_PREPARED))
    accounts = rows_by_id(load(PRIOR_ACCOUNTS))
    fresh_xp = rows_by_id(load(FRESH_XP))
    frozen = rows_by_id(freeze)
    if set(frozen) != set(alg.WORKER_IDS) or set(fresh_xp) != XP_IDS:
        raise RuntimeError("diagnostic worker authority differs")

    workers: dict[str, dict[str, Any]] = {}
    for worker_id in alg.WORKER_IDS:
        if worker_id in XP_IDS:
            workers[worker_id] = {**frozen[worker_id], **fresh_xp[worker_id]}
        else:
            workers[worker_id] = {
                **frozen[worker_id], **prepared[worker_id], **accounts[worker_id]
            }
        inspected = alg.reset.prep._docker_json(
            "inspect", workers[worker_id]["container"]
        )
        if (
            inspected.get("Id") != workers[worker_id].get("container_id")
            or (inspected.get("State") or {}).get("Running") is not True
        ):
            raise RuntimeError(f"{worker_id} live identity differs")

    reconstructed = checkpoint["reconstructed_state"]
    rankings = ranking_document["selected_top_k"]
    if set(rankings) != set(SCENES):
        raise RuntimeError("Outer5 selected scene set differs")
    assignments, companions = resume.build_batch_assignments(
        rankings=rankings,
        params_by_scene=reconstructed["params_by_scene"],
        workers=workers,
        ranks=(5,),
    )
    if companions or set(assignments) != {
        "job_w1", "tpch_w1", "xp_cpu_w1", "xp_io_w1"
    }:
        raise RuntimeError("Outer5 batch3 assignment differs")

    marker = {
        "schema_version": "mysql_outer5_batch3_interference_shadow_v1",
        "status": "RUNNING",
        "started_at": alg.now(),
        "diagnostic_only": True,
        "formal_eligible": False,
        "formal_launch_authorized": False,
        "accepted_rounds_mutation": False,
        "checkpoint_mutation": False,
        "reason": "user_requested_shared_disk_interference_trial",
        "known_interference": {
            "container": "bench-pg-mixstrg-0708",
            "direct_bind": "/disk2/wxr-data/cmp/mixstrg-0708",
        },
        "source_outer": 5,
        "source_batch": 3,
        "assignments": assignments,
    }
    alg.atomic(output / "diagnostic_state.json", marker)
    try:
        measured = alg.measure_batch(
            freeze,
            workers,
            assignments,
            output / "outer_005/top5_batch_3",
            "diagnostic_outer_005_batch_3",
            state_path=output / "diagnostic_state.json",
            stage_prefix="DIAGNOSTIC_OUTER_005_BATCH_3",
        )
        batch = load(output / "outer_005/top5_batch_3/batch_result.json")
        marker.update({
            "status": "PASS" if batch.get("status") == "PASS" else "FAILED",
            "completed_at": alg.now(),
            "batch_status": batch.get("status"),
            "elapsed_seconds": batch.get("elapsed_seconds"),
            "hard_gate_passed": {
                worker_id: row.get("hard_gate_passed")
                for worker_id, row in measured.items()
            },
            "accepted_rounds_after": checkpoint["reconstructed_state"]["accepted_rounds"],
            "accepted_rounds_unchanged": True,
        })
        alg.atomic(output / "diagnostic_state.json", marker)
        return 0 if batch.get("status") == "PASS" else 2
    except BaseException as error:
        marker.update({
            "status": "FAILED",
            "failed_at": alg.now(),
            "error": f"{type(error).__name__}: {error}",
            "accepted_rounds_unchanged": True,
        })
        alg.atomic(output / "diagnostic_state.json", marker)
        raise


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Recover IO-A Round1 Wave1 after only w5 failed its point reset.

The failed controller changed no accepted/rejected state and started no
candidate workload.  Seven workers completed a physical template reset across
its three technical attempts, while w5 remained on the stopped predecessor.
This recovery selects the newest completed row for every sibling, resets only
w5, re-provisions the eight benchmark accounts, and seals one current reset
binding.  It creates no optimizer credit and reuses no candidate measurement.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_async_xp_post_reset_settle_v2 as disk2_settle
from scripts import provision_mysql_four_scene_benchmark_users_v5 as users
from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_scene_v6 as reset8
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg


SCENE = "hybench_xp_io"
FAILED_WORKER = "xp_io_w5"
EXPECTED = {f"xp_io_w{i}" for i in range(1, 9)}


def load(path: Path) -> dict[str, Any]:
    value = alg.load(path)
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--failed-run", type=Path, required=True)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--source-full-binding", type=Path, required=True)
    parser.add_argument("--source-w5-binding", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reset-token", required=True)
    args = parser.parse_args()

    failed_run = args.failed_run.resolve()
    freeze_path = args.freeze.resolve()
    source_full_path = args.source_full_binding.resolve()
    source_w5_path = args.source_w5_binding.resolve()
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh recovery output required: {output}")

    state_path = failed_run / "algorithm_a_state.json"
    state = load(state_path)
    freeze = load(freeze_path)
    source_full = load(source_full_path)
    source_w5 = load(source_w5_path)
    frozen = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in freeze.get("workers") or []
        if row.get("worker_id") in EXPECTED
    }
    source_workers = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in source_full.get("workers") or []
        if row.get("worker_id") in EXPECTED
    }
    w5_rows = [
        copy.deepcopy(dict(row)) for row in source_w5.get("workers") or []
        if row.get("worker_id") == FAILED_WORKER
    ]
    error = str(state.get("error") or "")
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or int(state.get("round_index") or 0) != 1
        or int(state.get("accepted") or 0) != 0
        or int(state.get("rejected_rounds") or 0) != 0
        or "job_w2/credentials/selected.cnf" not in error
        or "returned non-zero exit status 1" not in error
        or source_full.get("status") != "PASS"
        or source_w5.get("status") != "PASS"
        or set(frozen) != EXPECTED
        or set(source_workers) != EXPECTED
        or len(w5_rows) != 1
        or w5_rows[0].get("ready") is not True
    ):
        raise RuntimeError("IO-A Round1 w5 recovery boundary differs")

    # Pick the newest completed physical reset row for every sibling.  The
    # failed reset wrote rows before the batch exception, so later attempts
    # supersede earlier rows when both exist.
    completed: dict[str, dict[str, Any]] = {}
    completed_sources: dict[str, str] = {}
    reset_root = failed_run / "round_001/wave_001/xp_resets"
    for attempt in (1, 2, 3):
        token = (
            f"{failed_run.name}_round_001_wave_001_attempt_{attempt:02d}"
        )
        stage = reset_root / token
        for worker_path in sorted(stage.glob("xp_io_w*/reset_worker.json")):
            row = load(worker_path)
            worker_id = str(row.get("worker_id") or "")
            if worker_id in EXPECTED and worker_id != FAILED_WORKER:
                completed[worker_id] = row
                completed_sources[worker_id] = str(worker_path)
    if set(completed) != EXPECTED - {FAILED_WORKER}:
        raise RuntimeError(
            f"completed sibling reset set differs: {sorted(completed)}"
        )

    # Install the exact current reset implementation for the whole scene, but
    # invoke its physical template reset for w5 only.
    install_workers = copy.deepcopy(source_workers)
    install_workers[FAILED_WORKER] = w5_rows[0]
    reset8.install_for_scene(install_workers)
    reset8.configure(freeze=freeze_path, prepared=source_full_path)
    output.mkdir(parents=True, mode=0o700)
    stage = output / "xp_resets" / args.reset_token
    quarantine = (
        Path(str(freeze["storage"]["runtime_root"]))
        / "quarantine" / output.name / args.reset_token
    )
    reset_started = time.monotonic()
    recovered = reset8._prepare_fresh_xp(
        freeze,
        frozen[FAILED_WORKER],
        w5_rows[0],
        quarantine,
        stage,
        False,
        "formal_point",
        allow_unresponsive_old_container=True,
    )

    # Apply the unchanged single-device post-reset quiet gate without invoking
    # a second physical reset.
    original_reset = disk2_settle.ORIGINAL_RESET_XP
    disk2_settle.ORIGINAL_RESET_XP = lambda *unused_args, **unused_kwargs: None
    try:
        disk2_settle.reset_xp(
            freeze,
            {FAILED_WORKER: {**frozen[FAILED_WORKER], **recovered}},
            output,
            args.reset_token,
            worker_ids={FAILED_WORKER},
        )
    finally:
        disk2_settle.ORIGINAL_RESET_XP = original_reset

    rows = {worker_id: copy.deepcopy(row) for worker_id, row in completed.items()}
    rows[FAILED_WORKER] = {**frozen[FAILED_WORKER], **recovered}
    account_root = stage / "accounts"
    account_root.mkdir(exist_ok=False)
    for worker_id in sorted(EXPECTED):
        account = users._run_worker(rows[worker_id], account_root, True, False)
        rows[worker_id] = {**frozen[worker_id], **rows[worker_id], **account}

    # Prove every selected row is the current live identity and carries the
    # frozen cgroup/I/O envelope before sealing the combined binding.
    impl = reset8.predecessor.predecessor.predecessor.base._impl
    for worker_id, row in sorted(rows.items()):
        inspected = impl["_docker_json"]("inspect", str(row["container"]))
        if (
            inspected.get("Id") != row.get("container_id")
            or (inspected.get("State") or {}).get("Running") is not True
            or row.get("ready") is not True
        ):
            raise RuntimeError(f"{worker_id} recovered live identity differs")
        impl["prep"]._kernel_resource_readback(inspected, row)
        impl["prep"]._enforce_blkio(inspected, freeze, row)

    binding = stage / "reset_binding.json"
    alg.atomic(binding, {
        "schema_version": "mysql_algorithm_a_xp_io_round1_w5_reset_recovery_v1",
        "status": "PASS",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": SCENE,
        "freeze_sha256": alg.sha(freeze_path),
        "workers": [rows[worker_id] for worker_id in sorted(EXPECTED)],
        "reset_worker_ids": sorted(EXPECTED),
        "physical_worker_reset_in_this_recovery": [FAILED_WORKER],
        "completed_sibling_resets_reused": sorted(EXPECTED - {FAILED_WORKER}),
        "completed_sibling_sources": completed_sources,
        "source_failed_state": str(state_path),
        "source_failed_state_sha256": alg.sha(state_path),
        "source_w5_binding": str(source_w5_path),
        "source_w5_binding_sha256": alg.sha(source_w5_path),
        "candidate_measurements_reused": 0,
        "workload_started_in_failed_wave": False,
        "accepted_or_rejected_round_credit": 0,
        "worker_reset_finished_seconds": {
            FAILED_WORKER: time.monotonic() - reset_started,
        },
        "elapsed_seconds": time.monotonic() - reset_started,
    })
    reset8.purge_completed_discard_quarantine(
        quarantine_root=quarantine,
        reset_workers={FAILED_WORKER: rows[FAILED_WORKER]},
        binding_path=binding,
        cleanup_path=stage / "quarantine_cleanup.json",
    )
    alg.atomic(output / "recovery_manifest.json", {
        "schema_version": "mysql_algorithm_a_xp_io_round1_w5_recovery_manifest_v1",
        "status": "PASS",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "binding_path": str(binding),
        "binding_sha256": alg.sha(binding),
        "failed_worker": FAILED_WORKER,
        "physical_worker_reset_in_this_recovery": [FAILED_WORKER],
        "candidate_measurements_reused": 0,
        "accepted_or_rejected_round_credit": 0,
    })
    print(json.dumps({
        "status": "PASS",
        "binding": str(binding),
        "physical_worker_reset": FAILED_WORKER,
        "siblings_reused": sorted(EXPECTED - {FAILED_WORKER}),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Finish one XP8 point reset after exactly one Docker-start task failure.

This is technical recovery only: it does not run or credit a candidate.  Seven
successful sibling resets are read back live, the single missing worker is
finished from its already staged fresh copy, and a normal reset binding is
sealed for a subsequent controller authority refresh.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import provision_mysql_four_scene_benchmark_users_v5 as users
from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_v1 as reset8
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg


FREEZE = (
    ROOT
    / "config/formal/frozen_mysql_xp_eight_worker_20260827_v1"
    / "xp_effective_resource_successor.json"
)
PREFIX = {"hybench_xp_cpu": "xp_cpu", "hybench_xp_io": "xp_io"}


def load(path: Path) -> dict[str, Any]:
    value = alg.load(path)
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=sorted(PREFIX), required=True)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--authority-prepared", required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()

    run_root = Path(args.run_root).resolve()
    stage = run_root / "xp_resets" / args.token
    state = load(run_root / "run_state.json")
    freeze = load(FREEZE)
    prepared_path = Path(args.authority_prepared).resolve()
    prepared = load(prepared_path)
    prefix = PREFIX[args.scene]
    expected = {f"{prefix}_w{i}" for i in range(1, 9)}
    frozen = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in freeze.get("workers") or []
        if row.get("worker_id") in expected
    }
    prior = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in prepared.get("workers") or []
        if row.get("worker_id") in expected
    }
    existing = {
        path.parent.name: load(path)
        for path in stage.glob("*/reset_worker.json")
    }
    missing = expected - set(existing)
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or "TimeoutExpired" not in str(state.get("error") or "")
        or "docker', 'start'" not in str(state.get("error") or "")
        or int(state.get("outer_attempt") or 0) != 9
        or set(frozen) != expected
        or set(prior) != expected
        or len(existing) != 7
        or len(missing) != 1
        or (stage / "reset_binding.json").exists()
        or (run_root / "outer_010/top5_batch_1/batch_result.json").exists()
    ):
        raise RuntimeError("failed XP8 point-reset recovery boundary differs")

    reset8.install_for_workers({
        str(row["worker_id"]): dict(row) for row in freeze.get("workers") or []
    })
    reset8.configure(freeze=FREEZE, prepared=prepared_path)
    impl = reset8.base._impl

    # Seven sibling resets are reusable only after exact live identity and
    # schema/resource readback.  No workload result is reused here.
    rows: dict[str, dict[str, Any]] = {}
    for worker_id, row in sorted(existing.items()):
        inspect = impl["_docker_json"]("inspect", str(row["container"]))
        defaults = Path(str(row["credential_defaults"]))
        tables = impl["_host_mysql"](
            row, defaults,
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='hybench_sf10x';",
            timeout=60,
        ).stdout.strip()
        if (
            inspect.get("Id") != row.get("container_id")
            or (inspect.get("State") or {}).get("Running") is not True
            or tables != "8"
        ):
            raise RuntimeError(f"{worker_id} completed sibling reset differs")
        impl["prep"]._kernel_resource_readback(inspect, row)
        impl["prep"]._enforce_blkio(inspect, freeze, row)
        rows[worker_id] = row

    missing_id = next(iter(missing))
    storage = dict((freeze.get("storage_by_worker") or {}).get(missing_id) or freeze["storage"])
    quarantine = (
        Path(str(storage["runtime_root"]))
        / "quarantine" / run_root.name / args.token
    )
    recovered = reset8.base._impl["_prepare_fresh_xp"](
        freeze, frozen[missing_id], prior[missing_id], quarantine, stage, True,
        "formal_point", allow_bootstrap_touched_staging=True,
    )
    recovered["resumed_after_parallel_sibling_failure"] = True
    rows[missing_id] = recovered

    account_root = stage / "accounts"
    account_root.mkdir(exist_ok=False)
    workers: dict[str, dict[str, Any]] = {}
    for worker_id in sorted(expected):
        account = users._run_worker(rows[worker_id], account_root, True, False)
        workers[worker_id] = {
            **frozen[worker_id], **rows[worker_id], **account,
        }

    started = (stage / "pre_reset_session_drain.json").stat().st_mtime
    binding = stage / "reset_binding.json"
    alg.atomic(binding, {
        "schema_version": "mysql_xp8_parallel_point_reset_recovery_v1",
        "status": "PASS",
        "created_at": alg.now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_sha256": alg.sha(FREEZE),
        "workers": [workers[worker_id] for worker_id in sorted(expected)],
        "reset_worker_ids": sorted(expected),
        "reset_concurrency": 8,
        "elapsed_seconds": max(0.0, time.time() - started),
        "technical_recovery": {
            "source_failed_run_state": str(run_root / "run_state.json"),
            "missing_worker_recovered": missing_id,
            "completed_sibling_resets_reused": sorted(set(existing)),
            "candidate_measurements_reused": 0,
            "accepted_or_rejected_round_credit": 0,
            "docker_create_start_lock": False,
        },
    })
    reset8.purge_completed_discard_quarantine(
        quarantine_root=quarantine,
        reset_workers=workers,
        binding_path=binding,
        cleanup_path=stage / "quarantine_cleanup.json",
    )
    print(json.dumps({
        "status": "PASS", "binding": str(binding),
        "recovered_worker": missing_id, "workers": sorted(workers),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

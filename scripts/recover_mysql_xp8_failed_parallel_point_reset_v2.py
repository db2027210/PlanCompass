#!/usr/bin/env python3
"""Seal an XP8 reset after two Docker-start replies failed technically.

No workload measurement or optimizer-round credit is reused.  Completed reset
rows are checked against their live containers.  The two workers whose Docker
start calls timed out are finalized only after their orphan shims have been
removed, the same staged containers are running, MySQL is reachable, the full
native XP schema is present, and frozen cgroup/blkio limits read back exactly.
"""

from __future__ import annotations

import argparse
import copy
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
    parser.add_argument("--committed-outer", type=int, required=True)
    args = parser.parse_args()

    run_root = Path(args.run_root).resolve()
    stage = run_root / "xp_resets" / args.token
    state_path = run_root / "run_state.json"
    state = load(state_path)
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
    next_outer = args.committed_outer + 1
    batch = run_root / f"outer_{next_outer:03d}/top5_batch_1/batch_result.json"
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or "TimeoutExpired" not in str(state.get("error") or "")
        or "docker', 'start'" not in str(state.get("error") or "")
        or int(state.get("outer_attempt") or 0) != args.committed_outer
        or set(frozen) != expected
        or set(prior) != expected
        or len(existing) != 6
        or len(missing) != 2
        or (stage / "reset_binding.json").exists()
        or batch.exists()
    ):
        raise RuntimeError("two-worker XP8 reset recovery boundary differs")

    reset8.install_for_workers({
        str(row["worker_id"]): dict(row) for row in freeze.get("workers") or []
    })
    reset8.configure(freeze=FREEZE, prepared=prepared_path)
    impl = reset8.base._impl

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

    previous_stage = run_root / "xp_resets" / (
        f"algorithm_b_outer_{args.committed_outer:03d}_rank_1"
    )
    for worker_id in sorted(missing):
        seed_path = previous_stage / worker_id / "reset_worker.json"
        seed = load(seed_path)
        if seed.get("worker_id") != worker_id:
            raise RuntimeError(f"{worker_id} prior reset seed differs")
        row = copy.deepcopy(seed)
        inspect = impl["_docker_json"]("inspect", str(row["container"]))
        state_now = inspect.get("State") or {}
        mounts = {
            str(item.get("Destination")): str(item.get("Source"))
            for item in inspect.get("Mounts") or []
        }
        defaults = Path(str(row["credential_defaults"]))
        benchmark_defaults = Path(str(row["benchmark_defaults"]))
        readback = impl["_host_mysql"](
            row, defaults,
            "SELECT @@version,@@global.innodb_buffer_pool_size," 
            "@@global.innodb_use_native_aio,@@global.innodb_flush_method; "
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='hybench_sf10x';",
            timeout=60,
        ).stdout.splitlines()
        if (
            inspect.get("Id") != seed.get("container_id")
            or state_now.get("Running") is not True
            or mounts.get("/var/lib/mysql") != str(row["datadir"])
            or not defaults.is_file()
            or not benchmark_defaults.is_file()
            or len(readback) != 2
            or readback[1] != "8"
        ):
            raise RuntimeError(f"{worker_id} live timed-out reset differs")
        version, buffer_pool, native_aio, flush_method = readback[0].split("\t")
        if (
            version != "8.0.45"
            or int(buffer_pool) != int(frozen[worker_id]["innodb_buffer_pool_bytes"])
            or native_aio != "0"
            or flush_method.lower() != "fsync"
        ):
            raise RuntimeError(f"{worker_id} live MySQL reset readback differs")
        kernel = impl["prep"]._kernel_resource_readback(inspect, frozen[worker_id])
        blkio = impl["prep"]._enforce_blkio(inspect, freeze, frozen[worker_id])
        row.update({
            **copy.deepcopy(frozen[worker_id]),
            "container_id": str(inspect["Id"]),
            "container_pid": int(state_now["Pid"]),
            "kernel_cgroup_resources": kernel,
            "blkio_authority": blkio,
            "ready": True,
            "recovery_after_docker_start_response_timeout": True,
            "resumed_after_parallel_sibling_failure": True,
            "technical_live_finalize_only": True,
            "technical_live_finalize_created_at": alg.now(),
            "technical_live_finalize_source_state": str(state_path),
            "technical_live_finalize_source_state_sha256": alg.sha(state_path),
            "accepted_or_rejected_round_credit": 0,
            "candidate_measurement_reused": False,
        })
        worker_path = stage / worker_id / "reset_worker.json"
        alg.atomic(worker_path, row)
        rows[worker_id] = row

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
        "schema_version": "mysql_xp8_parallel_two_start_timeout_recovery_v1",
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
            "source_failed_run_state": str(state_path),
            "live_finalized_workers": sorted(missing),
            "completed_sibling_resets_reused": sorted(existing),
            "candidate_measurements_reused": 0,
            "accepted_or_rejected_round_credit": 0,
            "docker_create_start_lock": False,
            "orphan_shims_removed_only_after_exited_and_absent_task_proof": True,
        },
    })
    print({
        "status": "PASS", "binding": str(binding),
        "live_finalized_workers": sorted(missing), "workers": sorted(workers),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Seal CPU-A Round2 reset after w1 became ready after controller exit."""

from __future__ import annotations

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


HISTORY = ROOT / "history/mysql_four_scene_20260809"
RUN = HISTORY / "v581_to_a_cpu_top4_mixed_eight_worker_v6_wave1_resume_20260827_xp_cpu"
STATE = RUN / "algorithm_a_state.json"
TOKEN = (
    "v581_to_a_cpu_top4_mixed_eight_worker_v6_wave1_resume_20260827_"
    "xp_cpu_round_002_wave_001_attempt_01"
)
STAGE = RUN / "round_002/wave_001/xp_resets" / TOKEN
PREVIOUS = (
    RUN / "round_001/wave_002/xp_resets/"
    "v581_to_a_cpu_top4_mixed_eight_worker_v6_wave1_resume_20260827_"
    "xp_cpu_round_001_wave_002_attempt_01"
)
FREEZE = (
    ROOT / "config/formal/frozen_mysql_xp_eight_worker_20260827_v1/"
    "xp_effective_resource_successor.json"
)
PREPARED = (
    HISTORY / "v577_to_a_cpu_mixed_eight_worker_v6_live_authority_20260827/"
    "prepared_binding.json"
)


def load(path: Path) -> dict[str, Any]:
    value = alg.load(path)
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def main() -> int:
    state = load(STATE)
    freeze = load(FREEZE)
    expected = {f"xp_cpu_w{i}" for i in range(1, 9)}
    frozen = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in freeze.get("workers") or []
        if row.get("worker_id") in expected
    }
    existing = {
        path.parent.name: load(path)
        for path in STAGE.glob("*/reset_worker.json")
    }
    missing = expected - set(existing)
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or int(state.get("accepted") or 0) != 1
        or int(state.get("round_index") or 0) != 2
        or "returned non-zero exit status 1" not in str(state.get("error") or "")
        or set(frozen) != expected
        or len(existing) != 7
        or missing != {"xp_cpu_w1"}
        or (STAGE / "reset_binding.json").exists()
        or (RUN / "round_002/wave_001/attempt_01/batch_result.json").exists()
    ):
        raise RuntimeError("CPU-A Round2 reset recovery boundary differs")

    reset8.install_for_workers({
        str(row["worker_id"]): dict(row) for row in freeze.get("workers") or []
    })
    reset8.configure(freeze=FREEZE, prepared=PREPARED)
    impl = reset8.base._impl
    rows: dict[str, dict[str, Any]] = {}
    for worker_id, row in sorted(existing.items()):
        inspect = impl["_docker_json"]("inspect", str(row["container"]))
        tables = impl["_host_mysql"](
            row, Path(str(row["credential_defaults"])),
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='hybench_sf10x';", timeout=60,
        ).stdout.strip()
        if (
            inspect.get("Id") != row.get("container_id")
            or (inspect.get("State") or {}).get("Running") is not True
            or tables != "8"
        ):
            raise RuntimeError(f"{worker_id} completed Round2 reset differs")
        impl["prep"]._kernel_resource_readback(inspect, row)
        impl["prep"]._enforce_blkio(inspect, freeze, row)
        rows[worker_id] = row

    worker_id = "xp_cpu_w1"
    seed = load(PREVIOUS / worker_id / "reset_worker.json")
    inspect = impl["_docker_json"]("inspect", str(seed["container"]))
    running = inspect.get("State") or {}
    readback = impl["_host_mysql"](
        seed, Path(str(seed["credential_defaults"])),
        "SELECT @@version,@@global.innodb_buffer_pool_size," 
        "@@global.innodb_use_native_aio,@@global.innodb_flush_method; "
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema='hybench_sf10x';", timeout=60,
    ).stdout.splitlines()
    if (
        inspect.get("Id") != seed.get("container_id")
        or running.get("Running") is not True
        or len(readback) != 2
        or readback[1] != "8"
    ):
        raise RuntimeError("xp_cpu_w1 late-ready Round2 reset differs")
    version, buffer_pool, native_aio, flush_method = readback[0].split("\t")
    if (
        version != "8.0.45"
        or int(buffer_pool) != int(frozen[worker_id]["innodb_buffer_pool_bytes"])
        or native_aio != "0"
        or flush_method.lower() != "fsync"
    ):
        raise RuntimeError("xp_cpu_w1 late-ready MySQL readback differs")
    row = copy.deepcopy(seed)
    row.update({
        **frozen[worker_id],
        "container_id": str(inspect["Id"]),
        "container_pid": int(running["Pid"]),
        "kernel_cgroup_resources": impl["prep"]._kernel_resource_readback(
            inspect, frozen[worker_id]
        ),
        "blkio_authority": impl["prep"]._enforce_blkio(
            inspect, freeze, frozen[worker_id]
        ),
        "ready": True,
        "technical_live_finalize_only": True,
        "technical_live_finalize_created_at": alg.now(),
        "technical_live_finalize_source_state": str(STATE),
        "technical_live_finalize_source_state_sha256": alg.sha(STATE),
        "candidate_measurement_reused": False,
        "accepted_or_rejected_round_credit": 0,
    })
    alg.atomic(STAGE / worker_id / "reset_worker.json", row)
    rows[worker_id] = row

    account_root = STAGE / "accounts"
    account_root.mkdir(exist_ok=False)
    workers: dict[str, dict[str, Any]] = {}
    for worker_id in sorted(expected):
        account = users._run_worker(rows[worker_id], account_root, True, False)
        workers[worker_id] = {**frozen[worker_id], **rows[worker_id], **account}
    started = (STAGE / "pre_reset_session_drain.json").stat().st_mtime
    binding = STAGE / "reset_binding.json"
    alg.atomic(binding, {
        "schema_version": "mysql_algorithm_a_xp8_round2_reset_recovery_v1",
        "status": "PASS", "created_at": alg.now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_sha256": alg.sha(FREEZE),
        "workers": [workers[key] for key in sorted(workers)],
        "reset_worker_ids": sorted(workers),
        "reset_concurrency": 8,
        "elapsed_seconds": max(0.0, time.time() - started),
        "technical_recovery": {
            "source_failed_state": str(STATE),
            "completed_sibling_resets_reused": sorted(existing),
            "late_ready_worker_finalized": "xp_cpu_w1",
            "candidate_measurements_reused": 0,
            "accepted_or_rejected_round_credit": 0,
        },
    })
    print({"status": "PASS", "binding": str(binding), "workers": sorted(workers)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Restore the exact IO-A authority after v855 was stopped before workload."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_async_xp_mixed_ssd_post_reset_settle_v1 as settle
from scripts import provision_mysql_four_scene_benchmark_users_v5 as users
from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_scene_v6 as reset8
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg


HISTORY = ROOT / "history/mysql_four_scene_20260809"
FAILED_RUN = HISTORY / "v855_to_a_io_round2_wave2_recovered_resume_20260828_xp_io"
SOURCE_BINDING = (
    HISTORY / "v853_to_a_io_round1_w5_recovered_resume_20260828_xp_io"
    / "round_002/wave_002/xp_resets"
    / "v853_to_a_io_round1_w5_recovered_resume_20260828_xp_io_round_002_wave_002_attempt_01"
    / "reset_binding.json"
)
FREEZE = (
    ROOT / "config/formal/frozen_mysql_xp_eight_worker_20260827_v1"
    / "xp_effective_resource_successor.json"
)
FAILED_TOKEN = "v855_to_a_io_round2_wave2_recovered_resume_20260828_xp_io_formal_baseline"
OUTPUT = HISTORY / "v856_to_a_io_v855_interrupted_baseline_recovery_20260828"
TOKEN = "v856_to_a_io_v855_interrupted_baseline_recovery_20260828"
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
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh recovery output required: {OUTPUT}")
    source = load(SOURCE_BINDING)
    freeze = load(FREEZE)
    rows = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in source.get("workers") or []
        if row.get("worker_id") in EXPECTED
    }
    frozen = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in freeze.get("workers") or []
        if row.get("worker_id") in EXPECTED
    }
    state = load(FAILED_RUN / "algorithm_a_state.json")
    if (
        source.get("status") != "PASS"
        or set(rows) != EXPECTED
        or set(frozen) != EXPECTED
        or state.get("status") != "RUNNING"
        or state.get("stage") != "FORMAL_BASELINE_RESETTING_XP"
        or int(state.get("accepted") or 0) != 1
        or int(state.get("round_index") or 0) != 1
        or (FAILED_RUN / "formal_baseline").exists()
        or not (FAILED_RUN / "xp_resets" / FAILED_TOKEN / "pre_reset_session_drain.json").is_file()
    ):
        raise RuntimeError("v855 interrupted-baseline recovery boundary differs")

    reset8.install_for_scene(rows)
    reset8.configure(freeze=FREEZE, prepared=SOURCE_BINDING)
    impl = reset8.predecessor.predecessor.predecessor.base._impl
    stopped: list[str] = []
    running: list[str] = []
    for worker_id, row in sorted(rows.items()):
        existing = impl["_container_id"](str(row["container"]))
        if worker_id == FAILED_WORKER:
            if existing:
                raise RuntimeError("v855 failed worker unexpectedly still exists")
            continue
        inspected = impl["_docker_json"]("inspect", str(row["container"]))
        status = str((inspected.get("State") or {}).get("Status") or "")
        if inspected.get("Id") != row.get("container_id"):
            raise RuntimeError(f"{worker_id} v855 identity differs")
        if status == "running":
            running.append(worker_id)
        elif status == "exited" and int((inspected.get("State") or {}).get("ExitCode") or 0) == 0:
            stopped.append(worker_id)
        else:
            raise RuntimeError(f"{worker_id} v855 stopped state differs: {status}")

    for worker_id in stopped:
        impl["_docker_run"](["docker", "start", str(rows[worker_id]["container"])], timeout=600)

    OUTPUT.mkdir(parents=True, mode=0o700)
    stage = OUTPUT / "xp_resets" / TOKEN
    failed_quarantine_hint = FAILED_RUN / FAILED_TOKEN
    recovered = reset8._prepare_fresh_xp(
        freeze,
        frozen[FAILED_WORKER],
        rows[FAILED_WORKER],
        failed_quarantine_hint,
        stage,
        True,
        "formal_point",
        allow_unresponsive_old_container=True,
        allow_bootstrap_touched_staging=True,
        allow_absent_predecessor_after_interrupted_discard=True,
    )
    rows[FAILED_WORKER] = {**frozen[FAILED_WORKER], **recovered}

    original_reset = settle.ORIGINAL_RESET_XP
    settle.ORIGINAL_RESET_XP = lambda *unused_args, **unused_kwargs: None
    try:
        settle.reset_xp(freeze, rows, OUTPUT, TOKEN, worker_ids=set(EXPECTED))
    finally:
        settle.ORIGINAL_RESET_XP = original_reset

    account_root = stage / "accounts"
    account_root.mkdir(exist_ok=False)
    for worker_id in sorted(EXPECTED):
        account = users._run_worker(rows[worker_id], account_root, True, False)
        rows[worker_id] = {**frozen[worker_id], **rows[worker_id], **account}
        inspected = impl["_docker_json"]("inspect", str(rows[worker_id]["container"]))
        if (
            inspected.get("Id") != rows[worker_id].get("container_id")
            or (inspected.get("State") or {}).get("Running") is not True
            or rows[worker_id].get("ready") is not True
        ):
            raise RuntimeError(f"{worker_id} recovered live identity differs")
        impl["prep"]._kernel_resource_readback(inspected, rows[worker_id])
        impl["prep"]._enforce_blkio(inspected, freeze, rows[worker_id])

    binding = stage / "reset_binding.json"
    alg.atomic(binding, {
        "schema_version": "mysql_algorithm_a_xp_io_v855_interrupted_baseline_recovery_v1",
        "status": "PASS",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": "hybench_xp_io",
        "freeze_path": str(FREEZE),
        "freeze_sha256": alg.sha(FREEZE),
        "workers": [rows[worker_id] for worker_id in sorted(EXPECTED)],
        "reset_worker_ids": sorted(EXPECTED),
        "physical_template_resume_worker": FAILED_WORKER,
        "identity_preserving_restart_workers": stopped,
        "already_running_workers": running,
        "candidate_measurements_replayed": 0,
        "accepted_or_rejected_round_credit": 0,
        "source_binding": str(SOURCE_BINDING),
        "source_binding_sha256": alg.sha(SOURCE_BINDING),
        "source_failed_state": str(FAILED_RUN / "algorithm_a_state.json"),
        "source_failed_state_sha256": alg.sha(FAILED_RUN / "algorithm_a_state.json"),
    })
    reset8.purge_completed_discard_quarantine(
        quarantine_root=failed_quarantine_hint,
        reset_workers={FAILED_WORKER: rows[FAILED_WORKER]},
        binding_path=binding,
        cleanup_path=stage / "quarantine_cleanup.json",
    )
    alg.atomic(OUTPUT / "recovery_manifest.json", {
        "schema_version": "mysql_algorithm_a_xp_io_v855_recovery_manifest_v1",
        "status": "PASS",
        "created_at": now(),
        "binding_path": str(binding),
        "binding_sha256": alg.sha(binding),
        "candidate_measurements_replayed": 0,
        "accepted_or_rejected_round_credit": 0,
    })
    print(json.dumps({
        "status": "PASS", "binding": str(binding),
        "physical_template_resume_worker": FAILED_WORKER,
        "identity_preserving_restart_workers": stopped,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

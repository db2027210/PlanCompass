#!/usr/bin/env python3
"""Recover only XP/I/O A Round2 Wave2 worker w3 after an orphan shim.

Round2 Wave1 is immutable completed evidence.  Wave2 attempt 1 completed the
template reset for seven workers, while w3 completed its copy but Docker could
not start the staged container because the exact stopped-container shim was
left behind.  Resume that staged copy only, combine it with the seven completed
rows, and create no optimizer credit or candidate measurement.
"""

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


SCENE = "hybench_xp_io"
FAILED_WORKER = "xp_io_w3"
EXPECTED = {f"xp_io_w{i}" for i in range(1, 9)}
FAILED_RUN = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v853_to_a_io_round1_w5_recovered_resume_20260828_xp_io"
)
FREEZE = (
    ROOT / "config/formal/frozen_mysql_xp_eight_worker_20260827_v1"
    / "xp_effective_resource_successor.json"
)
ROUND = FAILED_RUN / "round_002"
WAVE1_RESET = (
    ROUND / "wave_001/xp_resets"
    / "v853_to_a_io_round1_w5_recovered_resume_20260828_xp_io_round_002_wave_001_attempt_01"
)
WAVE2_TOKEN = (
    "v853_to_a_io_round1_w5_recovered_resume_20260828_xp_io_"
    "round_002_wave_002_attempt_01"
)
WAVE2_ROOT = ROUND / "wave_002"
WAVE2_RESET = WAVE2_ROOT / "xp_resets" / WAVE2_TOKEN


def load(path: Path) -> dict[str, Any]:
    value = alg.load(path)
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def main() -> int:
    state_path = FAILED_RUN / "algorithm_a_state.json"
    state = load(state_path)
    freeze = load(FREEZE)
    prior_binding_path = WAVE1_RESET / "reset_binding.json"
    prior_binding = load(prior_binding_path)
    prior = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in prior_binding.get("workers") or []
        if row.get("worker_id") in EXPECTED
    }
    frozen = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in freeze.get("workers") or []
        if row.get("worker_id") in EXPECTED
    }
    checkpoint = state.get("round_checkpoint") or {}
    error = str(state.get("error") or "")
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or int(state.get("round_index") or 0) != 2
        or int(state.get("accepted") or 0) != 1
        or int(state.get("rejected_rounds") or 0) != 0
        or list(checkpoint.get("attempted_ranks") or []) != list(range(1, 9))
        or list(checkpoint.get("valid_ranks") or []) != [3, 4, 5, 7, 8]
        or list(checkpoint.get("technical_ranks") or []) != [1, 2, 6]
        or "xp_io_w1/credentials/selected.cnf" not in error
        or "returned non-zero exit status 1" not in error
        or prior_binding.get("status") != "PASS"
        or set(prior) != EXPECTED
        or set(frozen) != EXPECTED
    ):
        raise RuntimeError("IO-A Round2 Wave2 w3 recovery boundary differs")

    completed: dict[str, dict[str, Any]] = {}
    completed_sources: dict[str, str] = {}
    for worker_path in sorted(WAVE2_RESET.glob("xp_io_w*/reset_worker.json")):
        row = load(worker_path)
        worker_id = str(row.get("worker_id") or "")
        if worker_id in EXPECTED and worker_id != FAILED_WORKER:
            completed[worker_id] = row
            completed_sources[worker_id] = str(worker_path)
    if set(completed) != EXPECTED - {FAILED_WORKER}:
        raise RuntimeError(
            f"completed Wave2 sibling set differs: {sorted(completed)}"
        )
    reset8.install_for_scene(prior)
    reset8.configure(freeze=FREEZE, prepared=prior_binding_path)
    started = time.monotonic()
    recovered_path = WAVE2_RESET / FAILED_WORKER / "reset_worker.json"
    if recovered_path.is_file() and not recovered_path.is_symlink():
        recovered = load(recovered_path)
    else:
        recovered = reset8._prepare_fresh_xp(
            freeze,
            frozen[FAILED_WORKER],
            prior[FAILED_WORKER],
            WAVE2_ROOT / WAVE2_TOKEN,
            WAVE2_RESET,
            True,
            "formal_point",
            allow_unresponsive_old_container=True,
            allow_bootstrap_touched_staging=True,
        )

    rows = {worker_id: copy.deepcopy(row) for worker_id, row in completed.items()}
    rows[FAILED_WORKER] = {**frozen[FAILED_WORKER], **recovered}

    # The formal XP8 runtime uses the mixed-SSD settle gate.  Apply it once to
    # the complete recovered scene, with the physical reset callback disabled.
    original_reset = settle.ORIGINAL_RESET_XP
    settle.ORIGINAL_RESET_XP = lambda *unused_args, **unused_kwargs: None
    try:
        settle.reset_xp(
            freeze, rows, WAVE2_ROOT, WAVE2_TOKEN, worker_ids=set(EXPECTED),
        )
    finally:
        settle.ORIGINAL_RESET_XP = original_reset

    account_root = WAVE2_RESET / "accounts"
    account_root.mkdir(exist_ok=False)
    for worker_id in sorted(EXPECTED):
        account = users._run_worker(rows[worker_id], account_root, True, False)
        rows[worker_id] = {**frozen[worker_id], **rows[worker_id], **account}

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

    binding = WAVE2_RESET / "reset_binding.json"
    alg.atomic(binding, {
        "schema_version": "mysql_algorithm_a_xp_io_round2_wave2_w3_reset_recovery_v1",
        "status": "PASS",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": SCENE,
        "freeze_path": str(FREEZE),
        "freeze_sha256": alg.sha(FREEZE),
        "workers": [rows[worker_id] for worker_id in sorted(EXPECTED)],
        "reset_worker_ids": sorted(EXPECTED),
        "physical_worker_reset_in_this_recovery": [FAILED_WORKER],
        "completed_sibling_resets_reused": sorted(EXPECTED - {FAILED_WORKER}),
        "completed_sibling_sources": completed_sources,
        "source_failed_state": str(state_path),
        "source_failed_state_sha256": alg.sha(state_path),
        "source_prior_binding": str(prior_binding_path),
        "source_prior_binding_sha256": alg.sha(prior_binding_path),
        "round2_wave1_candidate_measurements_retained": 8,
        "round2_wave2_candidate_measurements_reused": 0,
        "accepted_or_rejected_round_credit": 0,
        "elapsed_seconds": time.monotonic() - started,
    })
    reset8.purge_completed_discard_quarantine(
        quarantine_root=WAVE2_ROOT / WAVE2_TOKEN,
        reset_workers=rows,
        binding_path=binding,
        cleanup_path=WAVE2_RESET / "quarantine_cleanup.json",
    )
    manifest = WAVE2_RESET / "recovery_manifest.json"
    alg.atomic(manifest, {
        "schema_version": "mysql_algorithm_a_xp_io_round2_wave2_w3_recovery_manifest_v1",
        "status": "PASS",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "binding_path": str(binding),
        "binding_sha256": alg.sha(binding),
        "physical_worker_reset": FAILED_WORKER,
        "completed_sibling_resets_reused": sorted(EXPECTED - {FAILED_WORKER}),
        "round2_wave1_candidate_measurements_retained": 8,
        "round2_wave2_candidate_measurements_reused": 0,
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

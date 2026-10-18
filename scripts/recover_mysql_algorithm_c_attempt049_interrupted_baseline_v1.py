#!/usr/bin/env python3
"""Resume the four XP resets interrupted by the Attempt-049 launcher stop."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import provision_mysql_four_scene_benchmark_users_v5 as users
from scripts import reset_mysql_xp_data_planes_v6 as reset


XP_IDS = {"xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"}
EXPECTED_STAGE = "FORMAL_INCUMBENT_BASELINE_RESETTING_XP"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular non-symlink JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--predecessor-binding", type=Path, required=True)
    parser.add_argument("--interrupted-run-state", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run-id")

    predecessor_path = args.predecessor_binding.expanduser().resolve()
    interrupted_path = args.interrupted_run_state.expanduser().resolve()
    predecessor = load(predecessor_path)
    interrupted = load(interrupted_path)
    freeze_path = (
        ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18"
        / "four_scene_effective_resource_successor.json"
    ).resolve()
    freeze = load(freeze_path)
    prior = {
        str(row["worker_id"]): dict(row)
        for row in (predecessor.get("workers") or [])
    }
    frozen = {
        str(row["worker_id"]): dict(row)
        for row in freeze["workers"] if row.get("worker_id") in XP_IDS
    }
    interrupted_root = interrupted_path.parent
    stage = interrupted_root / "xp_resets/v18_pruning_v2_formal_incumbent_baseline"
    quiet = interrupted_root / "formal_incumbent_baseline_cross_project_wave_quiet_gate_attempt_001.json"
    allowed_stage_files = {"pre_reset_session_drain.json", "pre_reset_session_drain.json.sha256"}
    if (
        predecessor.get("status") not in {"PASS", "PREPARED"}
        or predecessor.get("freeze_sha256") != sha(freeze_path)
        or set(prior) != XP_IDS
        or set(frozen) != XP_IDS
        or interrupted.get("status") != "RUNNING"
        or interrupted.get("stage") != EXPECTED_STAGE
        or interrupted.get("completed_predecessor_outer_attempts") != 17
        or interrupted.get("formal_incumbent_baseline") is not None
        or not quiet.is_file()
        or not stage.is_dir()
        or {path.name for path in stage.iterdir()} != allowed_stage_files
        or (interrupted_root / "outer_attempt_018").exists()
    ):
        raise RuntimeError("Attempt-049 interrupted-baseline recovery authority differs")
    for worker_id in XP_IDS:
        if (
            frozen[worker_id].get("container") != prior[worker_id].get("container")
            or frozen[worker_id].get("container")
            not in reset.XP_CONTAINER_ALLOWLIST.values()
        ):
            raise RuntimeError(f"{worker_id} container authority differs")

    quarantine = (
        Path(str(freeze["storage"]["runtime_root"]))
        / "quarantine" / interrupted_root.name / stage.name
    )
    if not quarantine.is_dir() or {
        path.name for path in quarantine.iterdir() if path.is_dir()
    } != XP_IDS:
        raise RuntimeError("interrupted quarantine worker set differs")

    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh recovery output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    recovery_stage = output / "xp_resets/attempt049_interrupted_baseline_v1"
    recovery_stage.mkdir(parents=True, mode=0o700)

    started = time.monotonic()
    reset_rows: dict[str, dict[str, Any]] = {}
    finished: dict[str, float] = {}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="attempt049-xp") as pool:
        futures = {
            pool.submit(
                reset._prepare_fresh_xp,
                freeze,
                frozen[worker_id],
                prior[worker_id],
                quarantine,
                recovery_stage,
                True,
                "formal_point",
                True,
                True,
                False,
                False,
            ): worker_id
            for worker_id in sorted(XP_IDS)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            reset_rows[worker_id] = future.result()
            finished[worker_id] = time.monotonic() - started

    account_root = recovery_stage / "accounts"
    account_root.mkdir()
    workers: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="attempt049-users") as pool:
        futures = {
            pool.submit(users._run_worker, reset_rows[worker_id], account_root, True, False): worker_id
            for worker_id in sorted(XP_IDS)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            workers[worker_id] = {
                **frozen[worker_id], **reset_rows[worker_id], **future.result(),
            }

    if (
        set(workers) != XP_IDS
        or any(row.get("ready") is not True for row in workers.values())
        or any(
            workers[worker_id].get("container_id")
            != prior[worker_id].get("container_id")
            for worker_id in XP_IDS
        )
        or any(
            workers[worker_id].get("container_creation_avoided") is not True
            for worker_id in XP_IDS
        )
    ):
        raise RuntimeError("Attempt-049 recovery result differs")

    binding = output / "recovered_xp_binding.json"
    atomic(binding, {
        "schema_version": "mysql_algorithm_c_attempt049_interrupted_baseline_recovery_v1",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(freeze_path),
        "freeze_sha256": sha(freeze_path),
        "predecessor_binding_path": str(predecessor_path),
        "predecessor_binding_sha256": sha(predecessor_path),
        "interrupted_run_state_path": str(interrupted_path),
        "interrupted_run_state_sha256": sha(interrupted_path),
        "workers": [workers[worker_id] for worker_id in sorted(workers)],
        "reset_worker_ids": sorted(XP_IDS),
        "reset_concurrency": 4,
        "worker_reset_finished_seconds": finished,
        "elapsed_seconds": time.monotonic() - started,
        "workload_started": False,
        "formal_outer_started": False,
        "accepted_rounds_mutation": False,
        "rejected_rounds_mutation": False,
        "interval_convergence_mutation": False,
    })
    cleanup = reset.purge_completed_discard_quarantine(
        quarantine_root=quarantine,
        reset_workers=workers,
        binding_path=binding,
        cleanup_path=output / "quarantine_cleanup.json",
    )
    print(json.dumps({
        "status": "PASS",
        "binding": str(binding),
        "elapsed_seconds": time.monotonic() - started,
        "quarantine_deleted_bytes": cleanup["deleted_bytes"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

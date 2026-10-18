#!/usr/bin/env python3
"""Rebuild four hung discard-only XP planes after an Outer 15 timeout.

This recovery is deliberately narrow.  It accepts an exact technical timeout
batch followed by an empty next-rank pre-reset drain failure.  It proves that
the batch changed no optimizer state, proves every recorded client is gone,
and replaces the four XP planes from the sealed templates.  Every non-MySQL
container is read-only.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import provision_mysql_four_scene_benchmark_users_v5 as users
from scripts import reset_mysql_xp_data_planes_v6 as reset
from scripts import run_mysql_four_scene_algorithm_c_v2 as algorithm


XP_IDS = {"xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"}
TARGETS = set(XP_IDS)
COMPANIONS: set[str] = set()
ERROR_PREFIX = (
    "RuntimeError: XP active client sessions were not proven drained before "
    "the 60s bound:"
)


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
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def unit_fields(unit: str) -> dict[str, str]:
    shown = subprocess.run(
        ["systemctl", "--user", "show", unit, "-p", "ActiveState", "-p",
         "SubState", "-p", "MainPID", "-p", "Result"],
        capture_output=True, text=True, check=True, timeout=20,
    ).stdout
    return dict(line.split("=", 1) for line in shown.splitlines() if "=" in line)


def recorded_client_pids(batch_root: Path) -> set[int]:
    result: set[int] = set()
    for path in batch_root.glob("*/client_processes.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            for field in ("launcher_pid", "workload_pid"):
                pid = int(row.get(field) or 0)
                if pid > 1:
                    result.add(pid)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--failed-root", type=Path, required=True)
    parser.add_argument("--predecessor-binding", type=Path, required=True)
    parser.add_argument("--timeout-rank", type=int, required=True)
    parser.add_argument("--resume-rank", type=int, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("safe run id required")

    failed_root = args.failed_root.expanduser().resolve()
    predecessor_path = args.predecessor_binding.expanduser().resolve()
    if (
        args.timeout_rank not in range(1, 5)
        or args.resume_rank != args.timeout_rank + 1
    ):
        parser.error("timeout rank 1..4 and its immediate resume rank required")
    state_path = failed_root / "run_state.json"
    receipt_path = failed_root / "launch_receipt.json"
    state = load(state_path)
    receipt = load(receipt_path)
    predecessor = load(predecessor_path)
    gate_path = (
        failed_root
        / (
            f"outer_015/top5_batch_{args.resume_rank}_"
            "cross_project_wave_quiet_gate_attempt_001.json"
        )
    )
    gate = load(gate_path)
    reset_root = (
        failed_root / "xp_resets" / f"outer_015_batch_{args.resume_rank}"
    )
    previous_batch_path = (
        failed_root / "outer_015"
        / f"top5_batch_{args.timeout_rank}/batch_result.json"
    )
    previous_batch = load(previous_batch_path)
    current_batch = (
        failed_root / "outer_015"
        / f"top5_batch_{args.resume_rank}/batch_result.json"
    )
    workers = {
        str(row["worker_id"]): dict(row)
        for row in (predecessor.get("workers") or [])
    }
    freeze_path = Path(str(state.get("freeze_path") or "")).resolve()
    freeze = load(freeze_path)
    frozen = {
        str(row["worker_id"]): dict(row)
        for row in (freeze.get("workers") or [])
        if row.get("worker_id") in XP_IDS
    }
    fields = unit_fields(str(receipt.get("systemd_unit") or ""))
    client_pids = recorded_client_pids(
        failed_root / "outer_015" / f"top5_batch_{args.timeout_rank}"
    )
    live_clients = sorted(pid for pid in client_pids if Path(f"/proc/{pid}").exists())
    reset_files = [path for path in reset_root.rglob("*") if path.is_file()]
    timeout_tasks = {
        str(row.get("task"))
        for row in (previous_batch.get("failures") or [])
        if str(row.get("error") or "").startswith("TimeoutExpired:")
        and "timed out after 900 seconds" in str(row.get("error") or "")
    }

    if (
        failed_root.parent != ROOT / "history/mysql_four_scene_20260809"
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or not str(state.get("error") or "").startswith(ERROR_PREFIX)
        or int(state.get("outer_attempt") or -1) != 14
        or state.get("accepted_rounds")
        != {"job_memory": 9, "tpch_sf4_memory": 9,
            "hybench_xp_cpu": 9, "hybench_xp_io": 8}
        or state.get("interval_stable_rounds")
        != {"job_memory": 1, "tpch_sf4_memory": 1,
            "hybench_xp_cpu": 1, "hybench_xp_io": 0}
        or gate.get("status") != "PASS"
        or gate.get("errors") not in ([], None)
        or previous_batch.get("status") != "FAILED_CLOSED"
        or not {"pair_1", "xp_cpu_w1", "xp_io_w1"}.issubset(timeout_tasks)
        or not (900.0 <= float(previous_batch.get("elapsed_seconds") or 0) <= 910.0)
        or not reset_root.is_dir()
        or reset_files
        or current_batch.exists()
        or list((failed_root / "outer_015").glob("*.decision.json"))
        or fields.get("ActiveState") not in {"inactive", "failed"}
        or int(fields.get("MainPID") or 0) != 0
        or predecessor.get("status") != "PASS"
        or set(workers) != XP_IDS
        or set(frozen) != XP_IDS
        or set(predecessor.get("reset_worker_ids") or []) != XP_IDS
        or predecessor.get("freeze_sha256") != sha(freeze_path)
        or state.get("freeze_sha256") != sha(freeze_path)
        or live_clients
    ):
        raise RuntimeError("Outer 15 technical-timeout recovery authority differs")

    companion_before: dict[str, dict[str, Any]] = {}
    for worker_id in sorted(XP_IDS):
        inspected = reset._docker_json("inspect", workers[worker_id]["container"])
        if inspected.get("Id") != workers[worker_id].get("container_id"):
            raise RuntimeError(f"{worker_id} predecessor identity differs")
        if worker_id in COMPANIONS:
            companion_before[worker_id] = {
                "container_id": inspected["Id"],
                "container_pid": int((inspected.get("State") or {}).get("Pid") or 0),
                "mounts": sorted(
                    (str(row.get("Source")), str(row.get("Destination")))
                    for row in (inspected.get("Mounts") or [])
                ),
            }
            if (
                (inspected.get("State") or {}).get("Running") is not True
                or algorithm.xp_active_client_session_count(workers[worker_id]) != 0
            ):
                raise RuntimeError(f"{worker_id} companion is not clean")

    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh recovery output exists: {output}")
    stage = (
        output / "xp_resets"
        / f"outer15_rank{args.resume_rank}_technical_timeout_recovery_v3"
    )
    stage.mkdir(parents=True, mode=0o700)
    quarantine = (
        Path(str(freeze["storage"]["runtime_root"]))
        / "quarantine" / output.name / stage.name
    )
    started = time.monotonic()
    recovered: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="timeout-xp") as pool:
        futures = {
            pool.submit(
                reset._prepare_fresh_xp,
                freeze, frozen[worker_id], workers[worker_id], quarantine,
                stage, False, "formal_point", True, False, False, False,
            ): worker_id
            for worker_id in sorted(TARGETS)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            recovered[worker_id] = future.result()
            print(json.dumps({
                "worker_id": worker_id,
                "reset_complete": True,
                "elapsed_seconds": time.monotonic() - started,
            }), flush=True)

    account_root = stage / "accounts"
    account_root.mkdir()
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="timeout-users") as pool:
        futures = {
            pool.submit(users._run_worker, recovered[worker_id], account_root, True, False):
                worker_id for worker_id in sorted(TARGETS)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            recovered[worker_id].update(future.result())

    all_workers = {
        **{worker_id: dict(workers[worker_id]) for worker_id in COMPANIONS},
        **recovered,
    }
    companion_after: dict[str, dict[str, Any]] = {}
    for worker_id in sorted(COMPANIONS):
        inspected = reset._docker_json("inspect", workers[worker_id]["container"])
        companion_after[worker_id] = {
            "container_id": inspected.get("Id"),
            "container_pid": int((inspected.get("State") or {}).get("Pid") or 0),
            "mounts": sorted(
                (str(row.get("Source")), str(row.get("Destination")))
                for row in (inspected.get("Mounts") or [])
            ),
        }
    if (
        companion_after != companion_before
        or set(all_workers) != XP_IDS
        or any(row.get("ready") is not True for row in recovered.values())
        or any(
            algorithm.xp_active_client_session_count(all_workers[worker_id]) != 0
            for worker_id in XP_IDS
        )
    ):
        raise RuntimeError("Outer 15 technical-timeout recovery readback differs")

    binding = output / "recovered_xp_binding.json"
    atomic(binding, {
        "schema_version": "mysql_algorithm_c_outer15_technical_timeout_recovery_v3",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "failed_state": {"path": str(state_path), "sha256": sha(state_path)},
        "failed_launch_receipt": {
            "path": str(receipt_path), "sha256": sha(receipt_path),
        },
        "technical_timeout_batch": {
            "path": str(previous_batch_path), "sha256": sha(previous_batch_path),
            "rank": args.timeout_rank,
            "technical_timeout_excluded": True,
            "formal_state_credit_increment": 0,
        },
        "quiet_gate": {"path": str(gate_path), "sha256": sha(gate_path)},
        "predecessor_binding": {
            "path": str(predecessor_path), "sha256": sha(predecessor_path),
        },
        "freeze_path": str(freeze_path),
        "freeze_sha256": sha(freeze_path),
        "recorded_client_pids": sorted(client_pids),
        "live_recorded_client_pids": [],
        "reset_worker_ids": sorted(TARGETS),
        "timeout_rank": args.timeout_rank,
        "resume_rank": args.resume_rank,
        "companion_worker_ids": sorted(COMPANIONS),
        "companion_before": companion_before,
        "companion_after": companion_after,
        "workers": [all_workers[row] for row in sorted(XP_IDS)],
        "workload_started": False,
        "accepted_rounds_mutation": False,
        "rejected_rounds_mutation": False,
        "interval_convergence_mutation": False,
        "external_container_mutation": False,
        "elapsed_seconds": time.monotonic() - started,
        "recovery_source_path": str(Path(__file__).resolve()),
        "recovery_source_sha256": sha(Path(__file__).resolve()),
    })
    cleanup = reset.purge_completed_discard_quarantine(
        quarantine_root=quarantine,
        reset_workers=recovered,
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

#!/usr/bin/env python3
"""Recover only an inactive formal controller interrupted inside an XP reset."""

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
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import provision_mysql_four_scene_benchmark_users_v5 as users
from scripts import reset_mysql_xp_data_planes_v5 as reset
from scripts import run_mysql_four_scene_algorithm_c_v2 as algorithm


XP_IDS = {"xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"}


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
    parser.add_argument("--interrupted-run", type=Path, required=True)
    parser.add_argument("--predecessor-binding", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run-id")
    interrupted = args.interrupted_run.expanduser().resolve()
    predecessor_path = args.predecessor_binding.expanduser().resolve()
    state_path = interrupted / "run_state.json"
    receipt_path = interrupted / "launch_receipt.json"
    state = load(state_path)
    receipt = load(receipt_path)
    incomplete = [
        path.parent for path in interrupted.glob(
            "xp_resets/outer_*_batch_*/pre_reset_session_drain.json"
        )
        if not (path.parent / "reset_binding.json").exists()
    ]
    if len(incomplete) != 1:
        raise RuntimeError("exactly one incomplete formal reset is required")
    incomplete_stage = incomplete[0]
    token_match = re.fullmatch(r"outer_(\d{3})_batch_([123])", incomplete_stage.name)
    if token_match is None:
        raise RuntimeError("incomplete reset token differs")
    outer_number, batch_number = map(int, token_match.groups())
    ranking_path = interrupted / f"outer_{outer_number:03d}/predicted_rankings.json"
    drain_path = incomplete_stage / "pre_reset_session_drain.json"
    ranking = load(ranking_path)
    drain = load(drain_path)
    predecessor = load(predecessor_path)
    unit = str(receipt.get("systemd_unit") or "")
    shown = subprocess.run(
        ["systemctl", "--user", "show", unit, "-p", "ActiveState",
         "-p", "SubState", "-p", "MainPID", "-p", "Result"],
        text=True, capture_output=True, check=True, timeout=20,
    ).stdout
    unit_fields = dict(
        line.split("=", 1) for line in shown.splitlines() if "=" in line
    )
    drain_polls = list(drain.get("polls") or [])
    drained_sessions = (
        drain_polls[-1].get("active_sessions") if drain_polls else {}
    )
    current_batch_result = (
        interrupted / f"outer_{outer_number:03d}/top5_batch_{batch_number}/batch_result.json"
    )
    workers = {
        str(row["worker_id"]): dict(row)
        for row in (predecessor.get("workers") or [])
        if row.get("worker_id") in XP_IDS
    }
    freeze = algorithm.load(algorithm.FREEZE)
    frozen = {
        str(row["worker_id"]): dict(row)
        for row in freeze["workers"] if row.get("worker_id") in XP_IDS
    }
    if (
        interrupted.parent != ROOT / "history/mysql_four_scene_20260809"
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or int(state.get("outer_attempt") or 0) != outer_number - 1
        or "credentials/benchmark.cnf" not in str(state.get("error") or "")
        or "timed out after 30 seconds" not in str(state.get("error") or "")
        or receipt.get("status") != "STARTED"
        or ranking.get("outer_attempt") != outer_number
        or drain.get("status") != "PASS"
        or set(drained_sessions) != XP_IDS
        or any(int(value) != 0 for value in drained_sessions.values())
        or unit_fields.get("ActiveState") not in {"inactive", "failed"}
        or int(unit_fields.get("MainPID") or 0) != 0
        or current_batch_result.exists()
        or predecessor.get("status") not in {"PREPARED", "PASS"}
        or predecessor.get("freeze_sha256") != algorithm.sha(algorithm.FREEZE)
        or set(workers) != XP_IDS
        or set(frozen) != XP_IDS
        or any(row.get("ready") is not True for row in workers.values())
    ):
        raise RuntimeError("interrupted formal reset recovery authority differs")
    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh recovery output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    reset_root = output / "reset_workers"
    reset_root.mkdir(mode=0o700)
    quarantine = (
        Path(str(freeze["storage"]["runtime_root"]))
        / "quarantine" / interrupted.name / incomplete_stage.name
    )
    started = time.monotonic()
    partial_rows: dict[str, dict[str, Any]] = {}
    for evidence in sorted(incomplete_stage.glob("*/reset_worker.json")):
        row = load(evidence)
        worker_id = str(row.get("worker_id") or "")
        if worker_id not in XP_IDS or worker_id in partial_rows or row.get("ready") is not True:
            raise RuntimeError("partial reset worker evidence differs")
        inspect = reset._docker_json("inspect", str(row.get("container") or ""))
        if (
            inspect.get("Id") != row.get("container_id")
            or (inspect.get("State") or {}).get("Running") is not True
        ):
            raise RuntimeError(f"{worker_id} partial reset live identity differs")
        row["worker_evidence_path"] = str(evidence)
        row["worker_evidence_sha256"] = sha(evidence)
        partial_rows[worker_id] = row
    if not partial_rows or set(partial_rows) == XP_IDS:
        raise RuntimeError("a strict nonempty partial reset worker set is required")
    results: dict[str, dict[str, Any]] = {
        worker_id: {**frozen[worker_id], **workers[worker_id]}
        for worker_id in XP_IDS - set(partial_rows)
    }
    account_root = output / "accounts"
    account_root.mkdir()
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="interrupted-users") as pool:
        futures = {
            pool.submit(users._run_worker, partial_rows[worker_id], account_root, True, False): worker_id
            for worker_id in sorted(partial_rows)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            results[worker_id] = {
                **frozen[worker_id], **partial_rows[worker_id], **future.result(),
            }
    if (
        set(results) != XP_IDS
        or any(row.get("ready") is not True for row in results.values())
        or any(algorithm.xp_active_client_session_count(row) != 0 for row in results.values())
    ):
        raise RuntimeError("interrupted formal reset recovery result differs")
    binding = output / "recovered_xp_binding.json"
    atomic(binding, {
        "schema_version": "mysql_algorithm_c_interrupted_formal_reset_binding_v1",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(algorithm.FREEZE),
        "freeze_sha256": algorithm.sha(algorithm.FREEZE),
        "workers": [results[worker_id] for worker_id in sorted(XP_IDS)],
        "reset_worker_ids": sorted(partial_rows),
        "workload_started": False,
        "formal_outer_started": False,
        "accepted_rounds_mutation": False,
        "rejected_rounds_mutation": False,
        "interval_convergence_mutation": False,
        "reset_concurrency": 4,
        "elapsed_seconds": time.monotonic() - started,
    })
    cleanup_path = output / "quarantine_cleanup.json"
    cleanup = reset.purge_completed_discard_quarantine(
        quarantine_root=quarantine,
        reset_workers={worker_id: results[worker_id] for worker_id in partial_rows},
        binding_path=binding,
        cleanup_path=cleanup_path,
    )
    result_path = output / "recovery_result.json"
    atomic(result_path, {
        "schema_version": "mysql_algorithm_c_interrupted_formal_reset_recovery_v1",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "interrupted_run_path": str(interrupted),
        "interrupted_run_state_sha256": sha(state_path),
        "interrupted_launch_receipt_sha256": sha(receipt_path),
        "interrupted_ranking_sha256": sha(ranking_path),
        "interrupted_pre_reset_drain_sha256": sha(drain_path),
        "interrupted_outer_attempt": outer_number,
        "interrupted_batch_index": batch_number,
        "recovered_partial_worker_ids": sorted(partial_rows),
        "systemd_readback": unit_fields,
        "no_measurement_batch_result_present": True,
        "predecessor_binding_path": str(predecessor_path),
        "predecessor_binding_sha256": sha(predecessor_path),
        "recovered_xp_binding_path": str(binding),
        "recovered_xp_binding_sha256": sha(binding),
        "quarantine_cleanup_path": str(cleanup_path),
        "quarantine_cleanup_sha256": sha(cleanup_path),
        "quarantine_deleted_bytes": cleanup["deleted_bytes"],
        "recovery_source_path": str(Path(__file__).resolve()),
        "recovery_source_sha256": sha(Path(__file__).resolve()),
        "formal_state_mutation": False,
        "workload_started": False,
    })
    print(json.dumps({
        "status": "PASS", "elapsed_seconds": time.monotonic() - started,
        "binding": str(binding), "result": str(result_path),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

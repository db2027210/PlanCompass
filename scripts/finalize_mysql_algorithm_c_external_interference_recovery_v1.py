#!/usr/bin/env python3
"""Finalize live XP planes whose fresh copies survived a Docker start failure."""

from __future__ import annotations

import argparse
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
from scripts import recover_mysql_algorithm_c_boundary_docker_run_timeout_v1 as recover
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
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--predecessor-binding", type=Path, required=True)
    parser.add_argument("--interrupted-recovery", type=Path, required=True)
    parser.add_argument("--external-container", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run id")

    predecessor_path = args.predecessor_binding.expanduser().resolve()
    interrupted = args.interrupted_recovery.expanduser().resolve()
    predecessor = load(predecessor_path)
    freeze = load(algorithm.FREEZE)
    prior = {
        str(row["worker_id"]): dict(row)
        for row in (predecessor.get("workers") or [])
    }
    frozen = {
        str(row["worker_id"]): dict(row)
        for row in freeze["workers"] if row.get("worker_id") in XP_IDS
    }
    stage = interrupted / "xp_resets/external_interference_discard_v1"
    stderr_path = (
        ROOT / "history/mysql_four_scene_20260809/formal_launch_logs"
        / f"{interrupted.name}.stderr.log"
    )
    # The launcher log uses the recovery run id as its exact stem.
    if not stderr_path.is_file():
        stderr_path = (
            ROOT / "history/mysql_four_scene_20260809/formal_launch_logs"
            / "algorithm_c_external_interference_recovery_retry13_v1_20260812T1716p0800.stderr.log"
        )
    stderr = stderr_path.read_text(encoding="utf-8")
    external_running = subprocess.run(
        ["docker", "ps", "-q", "--filter", f"name=^/{args.external_container}$"],
        capture_output=True, text=True, check=True, timeout=30,
    ).stdout.strip()
    quarantine = (
        Path(str(freeze["storage"]["runtime_root"])) / "quarantine"
        / interrupted.name / stage.name
    )
    old_roots = {worker_id: quarantine / worker_id for worker_id in XP_IDS}
    partial_rows = list(stage.glob("*/reset_worker.json"))
    if (
        predecessor.get("status") != "PREPARED"
        or predecessor.get("freeze_sha256") != algorithm.sha(algorithm.FREEZE)
        or set(prior) != XP_IDS
        or set(frozen) != XP_IDS
        or not stage.is_dir()
        or external_running
        or "docker', 'start'" not in stderr
        or "returned non-zero exit status 1" not in stderr
        or len(partial_rows) != 1
        or partial_rows[0].parent.name != "xp_cpu_w1"
        or any(not path.is_dir() for path in old_roots.values())
        or any(
            not (Path(str(freeze["storage"]["runtime_root"])) / worker_id / "datadir").is_dir()
            for worker_id in XP_IDS
        )
    ):
        raise RuntimeError("interrupted external recovery finalization authority differs")

    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    started = time.monotonic()
    recovered: dict[str, dict[str, Any]] = {}
    for worker_id in sorted(XP_IDS):
        recovered[worker_id] = recover.recover_worker(
            freeze=freeze,
            frozen=frozen[worker_id],
            prior=prior[worker_id],
            partial_stage=stage,
            output=output,
        )
        # recover_worker intentionally inherits predecessor metadata when no
        # completed per-worker reset artifact exists.  Here the four old roots
        # are independently proven to be in this interrupted recovery's exact
        # quarantine, so bind that current discard target before cleanup.
        recovered[worker_id]["quarantined_invalid_runtime_root"] = str(
            old_roots[worker_id]
        )
        recovered[worker_id]["quarantined_predecessor_runtime_root"] = str(
            old_roots[worker_id]
        )
        evidence = output / worker_id / "recovered_worker.json"
        recovered[worker_id].pop("worker_evidence_path", None)
        recovered[worker_id].pop("worker_evidence_sha256", None)
        atomic(evidence, recovered[worker_id])
        recovered[worker_id]["worker_evidence_path"] = str(evidence)
        recovered[worker_id]["worker_evidence_sha256"] = sha(evidence)
    account_root = output / "accounts"
    account_root.mkdir()
    workers: dict[str, dict[str, Any]] = {}
    for worker_id in sorted(XP_IDS):
        account = users._run_worker(
            recovered[worker_id], account_root,
            verify_existing=False, rotate_all=True,
        )
        workers[worker_id] = {
            **frozen[worker_id], **recovered[worker_id], **account,
        }
    if (
        any(row.get("ready") is not True for row in workers.values())
        or any(algorithm.xp_active_client_session_count(row) != 0 for row in workers.values())
    ):
        raise RuntimeError("finalized XP live readback differs")

    binding = output / "reset_binding.json"
    atomic(binding, {
        "schema_version": "mysql_four_scene_algorithm_c_xp_point_reset_v1",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(algorithm.FREEZE),
        "freeze_sha256": algorithm.sha(algorithm.FREEZE),
        "workers": [workers[worker_id] for worker_id in sorted(XP_IDS)],
        "reset_worker_ids": sorted(XP_IDS),
        "reset_concurrency": 4,
        "elapsed_seconds": time.monotonic() - started,
        "external_container": args.external_container,
        "external_container_running_at_finalization": False,
        "interrupted_recovery_path": str(interrupted),
        "interrupted_recovery_stderr_path": str(stderr_path),
        "interrupted_recovery_stderr_sha256": sha(stderr_path),
        "fresh_copy_evidence": {
            worker_id: {
                "quarantined_predecessor_root": str(old_roots[worker_id]),
                "quarantined_predecessor_present": True,
                "fresh_runtime_root": str(
                    Path(str(freeze["storage"]["runtime_root"])) / worker_id
                ),
                "failure_phase": "after_fresh_copy_before_or_during_docker_start",
            }
            for worker_id in sorted(XP_IDS)
        },
        "workload_started": False,
        "formal_outer_started": False,
        "accepted_rounds_mutation": False,
    })
    cleanup = reset.purge_completed_discard_quarantine(
        quarantine_root=quarantine,
        reset_workers=workers,
        binding_path=binding,
        cleanup_path=output / "quarantine_cleanup.json",
    )
    print(json.dumps({
        "status": "PASS", "binding": str(binding),
        "workers": len(workers),
        "elapsed_seconds": time.monotonic() - started,
        "quarantine_deleted_bytes": cleanup["deleted_bytes"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Finish the exact partial all-XP Rank 2 recovery after a Docker race."""

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


CPU_IDS = {"xp_cpu_w1", "xp_cpu_w2"}
IO_IDS = {"xp_io_w1", "xp_io_w2"}
XP_IDS = CPU_IDS | IO_IDS
PARTIAL_UNIT = (
    "mysql-four-scene-algc-rank2-hung-all-xp-recovery-v1-attempt003.service"
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


def unit_fields() -> dict[str, str]:
    shown = subprocess.run(
        ["systemctl", "--user", "show", PARTIAL_UNIT, "-p", "ActiveState",
         "-p", "SubState", "-p", "MainPID", "-p", "Result", "-p",
         "ExecMainStatus"],
        capture_output=True, text=True, check=True, timeout=20,
    ).stdout
    return dict(line.split("=", 1) for line in shown.splitlines() if "=" in line)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--partial-root", type=Path, required=True)
    parser.add_argument("--failed-root", type=Path, required=True)
    parser.add_argument("--predecessor-binding", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("safe run id required")

    partial_root = args.partial_root.expanduser().resolve()
    failed_root = args.failed_root.expanduser().resolve()
    failed_state_path = failed_root / "run_state.json"
    failed_state = load(failed_state_path)
    predecessor_path = args.predecessor_binding.expanduser().resolve()
    predecessor = load(predecessor_path)
    workers = {
        str(row["worker_id"]): dict(row)
        for row in (predecessor.get("workers") or [])
    }
    freeze_path = Path(str(failed_state.get("freeze_path") or "")).resolve()
    freeze = load(freeze_path)
    frozen = {
        str(row["worker_id"]): dict(row)
        for row in (freeze.get("workers") or [])
        if row.get("worker_id") in XP_IDS
    }
    source_stage = (
        partial_root / "xp_resets/rank2_hung_all_xp_recovery_v1"
    )
    cpu_paths = {
        worker_id: source_stage / worker_id / "reset_worker.json"
        for worker_id in CPU_IDS
    }
    cpu_rows = {worker_id: load(path) for worker_id, path in cpu_paths.items()}
    partial_stderr = (
        ROOT / "history/mysql_four_scene_20260809/formal_launch_logs"
        / "rank2_hung_all_xp_recovery_v1_attempt003.stderr.log"
    )
    stderr_text = partial_stderr.read_text(encoding="utf-8")
    fields = unit_fields()
    if (
        partial_root.parent != ROOT / "history/mysql_four_scene_20260809"
        or failed_root.parent != ROOT / "history/mysql_four_scene_20260809"
        or failed_state.get("status") != "FAILED_CLOSED"
        or failed_state.get("stage") != "FAILED"
        or int(failed_state.get("outer_attempt") or -1) != 14
        or failed_state.get("accepted_rounds")
        != {"job_memory": 9, "tpch_sf4_memory": 9,
            "hybench_xp_cpu": 9, "hybench_xp_io": 8}
        or failed_state.get("interval_stable_rounds")
        != {"job_memory": 1, "tpch_sf4_memory": 1,
            "hybench_xp_cpu": 1, "hybench_xp_io": 0}
        or set(workers) != XP_IDS
        or set(frozen) != XP_IDS
        or predecessor.get("status") != "PASS"
        or predecessor.get("freeze_sha256") != sha(freeze_path)
        or fields.get("ActiveState") != "failed"
        or fields.get("Result") != "exit-code"
        or fields.get("ExecMainStatus") != "1"
        or int(fields.get("MainPID") or 0) != 0
        or "docker', 'inspect', 'ra_mysql_disk2_algc_xp_io_w2_20260809'"
        not in stderr_text
        or "returned non-zero exit status 1" not in stderr_text
        or set(path.parent.name for path in source_stage.glob("*/reset_worker.json"))
        != CPU_IDS
        or any(row.get("ready") is not True for row in cpu_rows.values())
    ):
        raise RuntimeError("partial all-XP recovery authority differs")

    for worker_id in sorted(CPU_IDS):
        row = cpu_rows[worker_id]
        inspected = reset._docker_json("inspect", row["container"])
        if (
            inspected.get("Id") != row.get("container_id")
            or (inspected.get("State") or {}).get("Running") is not True
        ):
            raise RuntimeError(f"{worker_id} completed reset identity differs")

    io_absence: dict[str, dict[str, Any]] = {}
    for worker_id in sorted(IO_IDS):
        prior = workers[worker_id]
        container = str(prior["container"])
        listed = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"name=^{container}$"],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout.strip()
        cgroup_path = Path(str((prior.get("blkio_authority") or {}).get("cgroup_path") or ""))
        runtime_root = Path(str(freeze["storage"]["runtime_root"])) / worker_id
        datadir = runtime_root / "datadir"
        credentials = runtime_root / "credentials"
        if (
            listed
            or cgroup_path.exists()
            or not str(cgroup_path).startswith("/sys/fs/cgroup/blkio/docker-limited/")
            or not runtime_root.is_dir()
            or runtime_root.is_symlink()
            or not datadir.is_dir()
            or datadir.is_symlink()
            or not credentials.is_dir()
            or credentials.is_symlink()
        ):
            raise RuntimeError(f"{worker_id} absent predecessor authority differs")
        io_absence[worker_id] = {
            "container": container,
            "prior_container_id": prior["container_id"],
            "prior_cgroup_path": str(cgroup_path),
            "container_absent": True,
            "cgroup_absent": True,
            "runtime_root_present": True,
            "old_datadir_bytes": reset._host_tree_bytes(datadir),
        }

    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh continuation output exists: {output}")
    stage = output / "xp_resets/rank2_partial_all_xp_continuation_v1"
    stage.mkdir(parents=True, mode=0o700)
    quarantine = (
        Path(str(freeze["storage"]["runtime_root"]))
        / "quarantine" / output.name / stage.name
    )
    quarantine.mkdir(parents=True, mode=0o700)
    started = time.monotonic()
    io_rows: dict[str, dict[str, Any]] = {}
    for worker_id in sorted(IO_IDS):
        runtime_root = Path(str(freeze["storage"]["runtime_root"])) / worker_id
        datadir = runtime_root / "datadir"
        old_target = quarantine / worker_id
        old_target.mkdir(mode=0o700)
        subprocess.run(
            ["sudo", "-n", "mv", str(datadir), str(old_target / "datadir")],
            check=True, timeout=60,
        )
        datadir.mkdir(mode=0o700)
        io_rows[worker_id] = reset._prepare_fresh_xp(
            freeze, frozen[worker_id], workers[worker_id], quarantine, stage,
            True, "formal_point", True, False, True, True,
        )
        print(json.dumps({
            "worker_id": worker_id,
            "reset_complete": True,
            "elapsed_seconds": time.monotonic() - started,
        }), flush=True)

    all_rows = {**cpu_rows, **io_rows}
    account_root = stage / "accounts"
    account_root.mkdir()
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="rank2-partial-users") as pool:
        futures = {
            pool.submit(users._run_worker, all_rows[worker_id], account_root, True, False):
                worker_id for worker_id in sorted(XP_IDS)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            all_rows[worker_id].update(future.result())

    if (
        set(all_rows) != XP_IDS
        or any(row.get("ready") is not True for row in all_rows.values())
        or any(
            algorithm.xp_active_client_session_count(all_rows[worker_id]) != 0
            for worker_id in XP_IDS
        )
    ):
        raise RuntimeError("completed all-XP recovery readback differs")

    binding = output / "recovered_xp_binding.json"
    atomic(binding, {
        "schema_version": "mysql_algorithm_c_rank2_partial_all_xp_recovery_v1",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "partial_recovery_root": str(partial_root),
        "failed_formal_root": {
            "path": str(failed_root), "run_state_sha256": sha(failed_state_path),
        },
        "partial_recovery_stderr": {
            "path": str(partial_stderr), "sha256": sha(partial_stderr),
        },
        "predecessor_binding": {
            "path": str(predecessor_path), "sha256": sha(predecessor_path),
        },
        "freeze_path": str(freeze_path),
        "freeze_sha256": sha(freeze_path),
        "io_absent_predecessor_readback": io_absence,
        "completed_partial_worker_ids": sorted(CPU_IDS),
        "continued_worker_ids": sorted(IO_IDS),
        "reset_worker_ids": sorted(XP_IDS),
        "workers": [all_rows[row] for row in sorted(XP_IDS)],
        "workload_started": False,
        "accepted_rounds_mutation": False,
        "rejected_rounds_mutation": False,
        "interval_convergence_mutation": False,
        "external_container_mutation": False,
        "elapsed_seconds": time.monotonic() - started,
        "recovery_source_path": str(Path(__file__).resolve()),
        "recovery_source_sha256": sha(Path(__file__).resolve()),
    })

    cpu_quarantine = (
        Path(str(freeze["storage"]["runtime_root"])) / "quarantine"
        / partial_root.name / source_stage.name
    )
    cpu_cleanup = reset.purge_completed_discard_quarantine(
        quarantine_root=cpu_quarantine,
        reset_workers=cpu_rows,
        binding_path=binding,
        cleanup_path=output / "cpu_quarantine_cleanup.json",
    )
    io_cleanup = reset.purge_completed_discard_quarantine(
        quarantine_root=quarantine,
        reset_workers=io_rows,
        binding_path=binding,
        cleanup_path=output / "io_quarantine_cleanup.json",
    )
    print(json.dumps({
        "status": "PASS",
        "binding": str(binding),
        "elapsed_seconds": time.monotonic() - started,
        "quarantine_deleted_bytes": (
            int(cpu_cleanup["deleted_bytes"]) + int(io_cleanup["deleted_bytes"])
        ),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

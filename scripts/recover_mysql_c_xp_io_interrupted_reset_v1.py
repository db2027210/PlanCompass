#!/usr/bin/env python3
"""Recover only the two XP-I/O workers lost by an interrupted C reset."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_data_planes_client_primary_v2 as reset


HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = ROOT / (
    "config/formal/frozen_mysql_four_scene_two_worker_20260903_v20/"
    "four_scene_effective_resource_successor.json"
)
SOURCE_AUTHORITY = HISTORY / "v1236_c_xp_io_scene_authority_v107_20260905/authority"
OUTPUT = HISTORY / "v1238_c_xp_io_interrupted_reset_recovery_v107_20260905"
QUARANTINE = Path(
    "/disk2/resource_adaptive_mysql_algorithm_c_disk2_v4/quarantine/"
    "v1238_c_xp_io_interrupted_reset_recovery_v107_20260905"
)
WORKER_IDS = {"xp_io_w1", "xp_io_w2"}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
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
    if OUTPUT.exists() or QUARANTINE.exists():
        raise RuntimeError("fresh XP-I/O technical recovery paths required")
    freeze = load(FREEZE)
    prepared = load(SOURCE_AUTHORITY / "prepared_binding.json")
    frozen = {
        str(row["worker_id"]): dict(row) for row in freeze.get("workers") or []
        if str(row.get("worker_id")) in WORKER_IDS
    }
    prior = {
        str(row["worker_id"]): dict(row) for row in prepared.get("workers") or []
        if str(row.get("worker_id")) in WORKER_IDS
    }
    if set(frozen) != WORKER_IDS or set(prior) != WORKER_IDS:
        raise RuntimeError("XP-I/O recovery worker authority differs")
    all_xp = {
        str(row["worker_id"]): dict(row) for row in prepared.get("workers") or []
        if str(row.get("scene")) in {"hybench_xp_cpu", "hybench_xp_io"}
    }
    if set(all_xp) != {"xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"}:
        raise RuntimeError("client-primary XP authority differs")
    reset.configure(
        freeze=FREEZE,
        prepared=SOURCE_AUTHORITY / "prepared_binding.json",
        xp_workers=all_xp,
    )
    cgroup_root = Path("/sys/fs/cgroup/blkio/docker-limited")
    for worker_id in sorted(WORKER_IDS):
        container = str(prior[worker_id]["container"])
        prior_id = str(prior[worker_id].get("container_id") or "")
        runtime_root = Path(str(freeze["storage"]["runtime_root"])) / worker_id
        current_id = str(reset._container_id(container) or "")
        prior_cgroup = cgroup_root / prior_id
        if (
            len(prior_id) != 64
            or not (runtime_root / "datadir").is_dir()
            or not (runtime_root / "credentials").is_dir()
        ):
            raise RuntimeError(f"{worker_id} interrupted-discard proof differs")
        if current_id:
            inspected = reset._docker_json("inspect", container)
            state = dict(inspected.get("State") or {})
            labels = dict((inspected.get("Config") or {}).get("Labels") or {})
            if (
                inspected.get("Id") != prior_id
                or state.get("Status") != "exited"
                or state.get("Running") is not False
                or int(state.get("Pid") or 0) != 0
                or state.get("ExitCode") != 0
                or state.get("Dead") is not False
                or state.get("Restarting") is not False
                or labels.get("ra.owner") != "mysql_four_scene_v5"
                or labels.get("ra.worker_id") != worker_id
            ):
                raise RuntimeError(f"{worker_id} stopped identity differs")
            if prior_cgroup.exists():
                tasks = prior_cgroup / "tasks"
                processes = prior_cgroup / "cgroup.procs"
                children = [path for path in prior_cgroup.iterdir() if path.is_dir()]
                if (
                    not tasks.is_file() or tasks.read_text(encoding="ascii").strip()
                    or not processes.is_file()
                    or processes.read_text(encoding="ascii").strip() or children
                ):
                    raise RuntimeError(f"{worker_id} stopped cgroup is not empty")
                subprocess.run(
                    ["sudo", "-n", "rmdir", str(prior_cgroup)], check=True,
                    capture_output=True, text=True, timeout=30,
                )
                if prior_cgroup.exists():
                    raise RuntimeError(f"{worker_id} empty cgroup reap failed")
        elif prior_cgroup.exists():
            raise RuntimeError(f"{worker_id} absent container cgroup remains")

    OUTPUT.mkdir(parents=True, mode=0o700)
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="c-xpio-recovery") as pool:
        futures = {
            pool.submit(
                reset._prepare_fresh_xp,
                freeze, frozen[worker_id], prior[worker_id], QUARANTINE,
                OUTPUT, True, "formal_point", True, False, False, True,
            ): worker_id
            for worker_id in sorted(WORKER_IDS)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            results[worker_id] = future.result()
            print(json.dumps({
                "stage": "WORKER_RECOVERED", "worker_id": worker_id,
                "container_id": results[worker_id]["container_id"],
            }), flush=True)
    binding = OUTPUT / "reset_binding.json"
    atomic(binding, {
        "schema_version": "mysql_algorithm_c_xp_io_interrupted_reset_recovery_v1",
        "status": "PASS", "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "scene": "hybench_xp_io", "workers": [results[k] for k in sorted(results)],
        "physical_worker_parallelism": 2, "accepted_or_rejected_round_credit": 0,
        "candidate_measurements_reused": 0,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "freeze_path": str(FREEZE), "freeze_sha256": sha(FREEZE),
        "source_authority": str(SOURCE_AUTHORITY),
    })
    print(binding, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

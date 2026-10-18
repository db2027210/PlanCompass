#!/usr/bin/env python3
"""Freeze the current four-scene, two-worker-per-scene disk2 layout."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
MEMORY_FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18"
    / "four_scene_effective_resource_successor.json"
)
XP_FREEZE = (
    ROOT / "config/formal/frozen_mysql_xp_cpu_disjoint_slots_20260826_v4"
    / "resource_successor.json"
)
OUTPUT = ROOT / "config/formal/frozen_mysql_four_scene_two_worker_20260826_v19"
FREEZE = OUTPUT / "four_scene_effective_resource_successor.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
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
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh release required: {OUTPUT}")
    memory = load(MEMORY_FREEZE)
    xp = load(XP_FREEZE)
    memory_rows = {
        str(row["worker_id"]): copy.deepcopy(row)
        for row in memory.get("workers") or []
        if row.get("worker_id") in {"job_w1", "job_w2", "tpch_w1", "tpch_w2"}
    }
    xp_rows = {
        str(row["worker_id"]): copy.deepcopy(row)
        for row in xp.get("workers") or []
        if row.get("worker_id") in {
            "xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"
        }
    }
    rows = {**memory_rows, **xp_rows}
    expected = {
        "job_w1", "job_w2", "tpch_w1", "tpch_w2",
        "xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2",
    }
    if set(rows) != expected:
        raise RuntimeError("four-scene two-worker cohort differs")
    freeze = copy.deepcopy(memory)
    freeze.update({
        "schema_version": "mysql_four_scene_two_worker_qps_successor_v19",
        "status": "FROZEN_RUNTIME_READBACK_PENDING",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "formal_launch_authorized": False,
        "launch_performed": False,
        "active_scenes": [
            "job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io"
        ],
        "workers": [rows[name] for name in sorted(rows)],
        "full_parallel_successor": {
            "physical_workers_per_scene": 2,
            "maximum_concurrent_mysql_workers": 8,
            "four_scene_barrier": False,
            "sibling_scene_wait": False,
            "lease_mode": "none",
            "candidate_sampling": False,
            "job_completion_reassigns_client_slots_to_xp_cpu_w3_w4": True,
            "tpch_completion_reassigns_database_and_client_slots_to_xp_io_w3_w4": True,
        },
        "predecessor_freezes": {
            "memory_path": str(MEMORY_FREEZE),
            "memory_sha256": sha(MEMORY_FREEZE),
            "xp_path": str(XP_FREEZE),
            "xp_sha256": sha(XP_FREEZE),
        },
        "accepted_rounds": {scene: 0 for scene in (
            "job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io"
        )},
        "version_transition_credit_policy": {
            "accepted_round_credit_increment": 0,
            "completed_candidate_replay_increment": 0,
            "recalibration_performed": False,
            "fresh_search_required": True,
        },
    })
    freeze["templates"] = {
        **dict(memory.get("templates") or {}),
        **dict(xp.get("templates") or {}),
    }
    atomic(FREEZE, freeze)
    print(FREEZE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

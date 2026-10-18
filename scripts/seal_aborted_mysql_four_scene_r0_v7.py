#!/usr/bin/env python3
"""Seal the early-aborted v7 R0 as failed closed with zero credit."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_native_iq1_iq7_r0_v7_20260809T194800p0800"
)
FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v7"
    / "four_scene_effective_resource_successor.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    binding = OUTPUT / "r0_binding.json"
    if binding.exists() or not OUTPUT.is_dir():
        raise SystemExit("aborted R0 output identity differs")
    document = {
        "schema_version": "mysql_four_scene_aborted_r0_v7",
        "status": "R0_FAILED_CLOSED",
        "failed_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE),
        "freeze_sha256": sha(FREEZE),
        "failure": "NameError: IQ1-IQ7 workload authority referenced undefined ROOT",
        "xp_workload_started": False,
        "job_tpch_process_group_terminated": 612332,
        "fresh_r0_complete": False,
        "formal_outer_launch_authorized": False,
        "formal_outer_started": False,
        "accepted_rounds": {
            "job_memory": 0,
            "tpch_sf4_memory": 0,
            "hybench_xp_cpu": 0,
            "hybench_xp_io": 0,
        },
    }
    binding.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    binding.with_name(binding.name + ".sha256").write_text(
        f"{sha(binding)}  {binding.name}\n", encoding="ascii"
    )
    print(binding)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

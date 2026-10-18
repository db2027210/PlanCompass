#!/usr/bin/env python3
"""Seal the invalid v8 R0 after its 900-second JOB limit expired."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_native_iq1_iq7_r0_v8_20260809T202300p0800"
)
FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v8"
    / "four_scene_effective_resource_successor.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    binding = OUTPUT / "r0_binding.json"
    if binding.exists() or not OUTPUT.is_dir():
        raise SystemExit("aborted v8 R0 output identity differs")
    document = {
        "schema_version": "mysql_four_scene_aborted_r0_v8",
        "status": "R0_FAILED_CLOSED",
        "failed_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE),
        "freeze_sha256": sha(FREEZE),
        "failures": [
            {
                "workers": ["xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"],
                "cause": "adapter v2 IQ1-IQ7 coverage false-negative",
                "actual_native_coverage_present": True,
            },
            {
                "workers": ["job_w1", "job_w2"],
                "cause": "full JOB cold-cache wall time exceeded 900 seconds",
                "process_group_terminated": 1060201,
            },
        ],
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
    binding.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    binding.with_name(binding.name + ".sha256").write_text(
        f"{sha(binding)}  {binding.name}\n", encoding="ascii"
    )
    print(binding)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

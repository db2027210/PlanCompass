#!/usr/bin/env python3
"""Refresh one live two-worker memory scene under full-parallel policy."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "refresh_mysql_cpu_a_slots_two_worker_scene_authority_v2.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "mysql_cross_project_wave_coordination_cpu_a_slots_v12 as dynamic_gate":
        "mysql_cross_project_wave_coordination_cpu_a_slots_v13 as dynamic_gate",
    "maximum_wait_seconds=180.0": "maximum_wait_seconds=600.0",
    "maximum_load1=16.0": "maximum_load1=512.0",
    "mysql_cpu_a_slots_two_worker_scene_live_authority_v2":
        "mysql_cpu_a_slots_two_worker_scene_live_authority_v3",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"two-worker authority predecessor differs: {old}")
    text = text.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._refresh_mysql_cpu_a_slots_two_worker_scene_authority_v3_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)


def main() -> int:
    result = int(implementation["main"]())
    output = Path(sys.argv[sys.argv.index("--output") + 1]).expanduser().resolve()
    live_path = output / "live_authority.json"
    live = implementation["load"](live_path)
    live.update({
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "physical_replay_parallelism": 2,
        "full_parallel_co_resident_scene_allowed": True,
        "four_scene_wave_barrier": False,
        "shared_nvme_lease": False,
        "recalibration_performed": False,
        "accepted_state_credit_increment": 0,
    })
    implementation["atomic"](live_path, live)
    print(json.dumps(live, ensure_ascii=False, sort_keys=True))
    return result


if __name__ == "__main__":
    raise SystemExit(main())

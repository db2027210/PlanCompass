#!/usr/bin/env python3
"""Refresh one four-worker XP scene on the CPU-A-slot successor."""

import json
import sys
from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "refresh_mysql_cpu_a_slots_two_worker_scene_authority_v2.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "live two-worker scene": "live four-worker XP scene",
    "mysql_cross_project_wave_coordination_cpu_a_slots_v12 as dynamic_gate":
        "mysql_cross_project_wave_coordination_cpu_a_slots_v13 as dynamic_gate",
    "frozen_mysql_four_scene_cpu_a_slots_two_worker_20260828_v2":
        "frozen_mysql_four_scene_cpu_a_slots_four_worker_20260828_v3",
    "mysql_four_scene_cpu_a_slots_two_worker_successor_v2":
        "mysql_four_scene_cpu_a_slots_four_worker_successor_v3",
    "if len(target_ids) != 2:": "if len(target_ids) != 4:",
    "two-worker scene cardinality differs": "four-worker XP scene cardinality differs",
    "maximum_wait_seconds=180.0": "maximum_wait_seconds=600.0",
    "maximum_load1=16.0": "maximum_load1=512.0",
    "mysql_two_worker_scene_handoff_preflight_v1":
        "mysql_four_worker_scene_handoff_preflight_v1",
    "mysql_two_worker_scene_handoff_prepared_v1":
        "mysql_four_worker_scene_handoff_prepared_v1",
    "mysql_two_worker_scene_handoff_accounts_v1":
        "mysql_four_worker_scene_handoff_accounts_v1",
    "mysql_cpu_a_slots_two_worker_scene_live_authority_v2":
        "mysql_cpu_a_slots_four_worker_scene_live_authority_v1",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"four-worker authority predecessor differs: {old}")
    text = text.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._refresh_mysql_cpu_a_slots_four_worker_scene_authority_v1_impl",
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
        "physical_replay_parallelism": 4,
        "recalibration_performed": False,
        "accepted_state_credit_increment": 0,
        "resource_freeze_path": live["freeze_path"],
        "resource_freeze_sha256": live["freeze_sha256"],
        "artifacts": {
            "preflight": live["preflight"],
            "prepared": live["prepared"],
            "accounts": live["accounts"],
        },
    })
    implementation["atomic"](live_path, live)
    print(json.dumps(live, ensure_ascii=False, sort_keys=True))
    return result


if __name__ == "__main__":
    raise SystemExit(main())

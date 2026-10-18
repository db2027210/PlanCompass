#!/usr/bin/env python3
"""Advance four-worker XP C->B->A with scoped C terminal repair."""

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import repair_mysql_algorithm_c_scoped_terminal_status_v1 as repair


SOURCE = Path(__file__).resolve().with_name(
    "monitor_mysql_xp_cba_cpu_a_slots_v1.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "launch_mysql_xp_top4_algorithm_b_cpu_a_slots_scene_v2.py":
        "launch_mysql_xp_top4_algorithm_b_cpu_a_slots_four_worker_scene_v1.py",
    "launch_mysql_algorithm_a_xp_top4_cpu_a_slots_scene_v2.py":
        "launch_mysql_algorithm_a_xp_top4_cpu_a_slots_four_worker_scene_v1.py",
    "mysql_xp_cba_cpu_a_slots_independent_pipeline_v1":
        "mysql_xp_cba_cpu_a_slots_four_worker_independent_pipeline_v3",
    "        c_state = load(args.c_state)\n":
        "        repair.repair_if_scoped_complete(args.c_state, scene)\n"
        "        c_state = load(args.c_state)\n",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"four-worker CBA v3 predecessor differs: {old}")
    text = text.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._monitor_mysql_xp_cba_cpu_a_slots_four_worker_v3_impl",
    "__package__": "scripts",
    "repair": repair,
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

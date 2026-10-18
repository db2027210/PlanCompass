#!/usr/bin/env python3
"""Run XP Top4-A with four current CPU-A-slot workers."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_a_xp_top4_mixed_four_worker_v10.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "mysql_cross_project_wave_coordination_v16 as gate_v16":
        "mysql_cross_project_wave_coordination_cpu_a_slots_v13 as gate_v13",
    "frozen_mysql_xp_top4_algorithm_a_mixed_four_worker_20260827_v10":
        "frozen_mysql_xp_top4_algorithm_a_cpu_a_slots_four_worker_20260828_v13",
    "predecessor.no_lease.coordination = gate_v16":
        "predecessor.no_lease.coordination = gate_v13",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"CPU-A-slot four-worker A predecessor differs: {old}")
    text = text.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_a_xp_top4_cpu_a_slots_four_worker_v1_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

#!/usr/bin/env python3
"""Run fresh Top4 Algorithm B on one current eight-worker XP scene."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v1.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "reset_mysql_xp_data_planes_mixed_eight_worker_v1 as reset_mixed":
        "reset_mysql_xp_data_planes_mixed_eight_worker_v5 as reset_mixed",
    "frozen_mysql_xp_eight_worker_20260827_v1":
        "frozen_mysql_xp_eight_worker_cpu_a_slots_20260828_v6",
    "mysql_xp_eight_worker_resource_successor_v1":
        "mysql_xp_eight_worker_cpu_a_slots_successor_v6",
    "frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v1":
        "frozen_mysql_xp_top4_algorithm_b_cpu_a_slots_eight_worker_20260828_v48",
    "mysql_algorithm_b_xp_top4_mixed_eight_worker_contract_v1":
        "mysql_algorithm_b_xp_top4_cpu_a_slots_eight_worker_contract_v1",
    "mysql_algorithm_b_top4_mixed_eight_worker_no_lease_coordination_v1":
        "mysql_algorithm_b_top4_cpu_a_slots_eight_worker_no_lease_coordination_v1",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"current XP8 B predecessor differs: {old}")
    text = text.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_b_xp8_cpu_a_slots_v1_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

#!/usr/bin/env python3
"""Run XP Top4-A on mixed-SSD v4 with nonblocking gate v73."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name("run_mysql_algorithm_a_xp_top4_mixed_four_worker_v10.py")
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "mysql_cross_project_wave_coordination_v16 as gate_v16":
        "mysql_cross_project_wave_coordination_v17 as gate_v17",
    "frozen_mysql_xp_top4_algorithm_a_mixed_four_worker_20260827_v10":
        "frozen_mysql_xp_top4_algorithm_a_mixed_four_worker_20260827_v11",
    "predecessor.no_lease.coordination = gate_v16":
        "predecessor.no_lease.coordination = gate_v17",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"mixed A v11 predecessor differs: {old}")
    text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_a_xp_top4_mixed_v11_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

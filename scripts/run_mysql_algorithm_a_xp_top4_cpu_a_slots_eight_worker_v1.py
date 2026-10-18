#!/usr/bin/env python3
"""Run current CPU-A-slot Top4 Algorithm A on one eight-worker XP scene."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v10.py"
)
text = SOURCE.read_text(encoding="utf-8")
old = "frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v10"
new = "frozen_mysql_xp_top4_algorithm_a_cpu_a_slots_eight_worker_20260828_v15"
if text.count(old) != 1:
    raise RuntimeError("current XP8 A predecessor differs")
text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_a_xp8_cpu_a_slots_v1_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

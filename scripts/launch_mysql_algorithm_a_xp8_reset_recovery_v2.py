#!/usr/bin/env python3
"""Recover XP8 Algorithm A with the effective v5 reset dispatcher."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "launch_mysql_algorithm_a_xp8_reset_recovery_v1.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v4.py":
        "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v5.py",
    "frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v4":
        "frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v5",
    "Algorithm A v4 recovery": "Algorithm A v5 recovery",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"XP8 A recovery v2 predecessor differs: {old}")
    text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._launch_mysql_algorithm_a_xp8_recovery_v2_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())


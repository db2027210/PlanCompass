#!/usr/bin/env python3
"""XP8 Algorithm-B v11 with 900-second Docker-control bounds."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v9.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v9":
        "frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v11",
    "reset_mysql_xp_data_planes_mixed_eight_worker_v4":
        "reset_mysql_xp_data_planes_mixed_eight_worker_v5",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"XP8 B v11 predecessor differs: {old}")
    text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_b_xp8_v11_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


if __name__ == "__main__":
    raise SystemExit(implementation["implementation"]["implementation"]["main"]())

#!/usr/bin/env python3
"""XP8 Algorithm-B runtime using serialized Docker create/start control."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name("run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v1.py")
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "reset_mysql_xp_data_planes_mixed_eight_worker_v1 as reset_mixed":
        "reset_mysql_xp_data_planes_mixed_eight_worker_v2 as reset_mixed",
    "frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v1":
        "frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v2",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"XP8 B v2 predecessor differs: {old}")
    text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_b_xp8_v2_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

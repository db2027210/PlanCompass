#!/usr/bin/env python3
"""Monitor XP8 B and launch corrected long-storage Algorithm-A v9."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "monitor_mysql_b_then_a_mixed_eight_worker_scene_v9.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "frozen_mysql_xp_b_to_a_monitor_20260827_v9":
        "frozen_mysql_xp_b_to_a_monitor_20260827_v10",
    "launch_mysql_algorithm_a_xp_top4_mixed_eight_worker_v8.py":
        "launch_mysql_algorithm_a_xp_top4_mixed_eight_worker_v9.py",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"XP8 B-to-A monitor v10 predecessor differs: {old}")
    text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._monitor_mysql_b_then_a_mixed_eight_worker_v10_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


if __name__ == "__main__":
    implementation["validate_contract"]()
    raise SystemExit(implementation["implementation"]["implementation"]["main"]())

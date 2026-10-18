#!/usr/bin/env python3
"""Launch one XP8 Algorithm-A v5 scene after terminal B."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "launch_mysql_algorithm_a_xp_top4_mixed_eight_worker_v1.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "scripts/run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v1.py":
        "scripts/run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v5.py",
    "frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v1":
        "frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v5",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"XP8 A launcher v5 predecessor differs: {old}")
    text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._launch_mysql_algorithm_a_xp8_v5_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)


if __name__ == "__main__":
    raise SystemExit(implementation["implementation"]["main"]())

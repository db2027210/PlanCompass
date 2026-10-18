#!/usr/bin/env python3
"""Launch one mixed-SSD XP Top4-A scene from its bound terminal B state."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name("launch_mysql_algorithm_a_xp_top4_scene_v9.py")
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "scripts/run_mysql_algorithm_a_xp_top4_async_v9.py":
        "scripts/run_mysql_algorithm_a_xp_top4_mixed_four_worker_v10.py",
    "scripts/refresh_mysql_xp_combined_v4_live_authority_v2.py":
        "scripts/refresh_mysql_xp_mixed_v4_live_authority_v3.py",
    "frozen_mysql_xp_top4_algorithm_a_20260826_v9":
        "frozen_mysql_xp_top4_algorithm_a_mixed_four_worker_20260827_v10",
    'MANAGEMENT_CPUSET = "240-241,496-497"':
        'MANAGEMENT_CPUSET = "488-489,498-499"',
    "MySQL v9 Top4 Algorithm A": "MySQL v10 mixed Top4 Algorithm A",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"mixed A launcher predecessor differs: {old}")
    text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._launch_mysql_algorithm_a_xp_top4_mixed_v10_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

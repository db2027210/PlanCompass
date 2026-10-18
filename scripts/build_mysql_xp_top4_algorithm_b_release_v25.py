#!/usr/bin/env python3
"""Freeze Top4-B v35 with a single-layer IO launcher."""

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/build_mysql_xp_top4_algorithm_b_release_v23.py").read_text(
    encoding="utf-8"
)
replacements = {
    "frozen_mysql_xp_top4_algorithm_b_20260826_v33":
        "frozen_mysql_xp_top4_algorithm_b_20260826_v35",
    "run_mysql_algorithm_b_xp_top4_async_v24.py":
        "run_mysql_algorithm_b_xp_top4_async_v28.py",
    "launch_mysql_xp_top4_algorithm_b_scene_v15.py":
        "launch_mysql_xp_top4_algorithm_b_scene_v17.py",
    'builder.BOOTSTRAP_VERSION = "v33"':
        'builder.BOOTSTRAP_VERSION = "v35"',
    "v161_to_b_top4_fresh_bootstrap_20260826_v33_":
        "v161_to_b_top4_fresh_bootstrap_20260826_v35_",
}
for old, new in replacements.items():
    if source.count(old) != 1:
        raise RuntimeError(f"Top4-B v35 builder fragment differs: {old}")
    source = source.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._build_b_v35_impl",
    "__package__": "scripts",
}
exec(compile(source, str(Path(__file__).resolve()), "exec"), implementation)

if __name__ == "__main__":
    raise SystemExit(int(implementation["main"]()))

#!/usr/bin/env python3
"""Launch fresh IO Top4-B under v34."""

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_scene_v15.py").read_text(
    encoding="utf-8"
)
replacements = {
    "run_mysql_algorithm_b_xp_top4_async_v24.py":
        "run_mysql_algorithm_b_xp_top4_async_v27.py",
    "frozen_mysql_xp_top4_algorithm_b_20260826_v33":
        "frozen_mysql_xp_top4_algorithm_b_20260826_v34",
    "v161_to_b_top4_fresh_bootstrap_20260826_v33_cpu":
        "v161_to_b_top4_fresh_bootstrap_20260826_v34_cpu",
    "v161_to_b_top4_fresh_bootstrap_20260826_v33_io":
        "v161_to_b_top4_fresh_bootstrap_20260826_v34_io",
}
for old, new in replacements.items():
    if source.count(old) != 1:
        raise RuntimeError(f"Top4-B launcher v16 fragment differs: {old}")
    source = source.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._b_v34_io_launch",
    "__package__": "scripts",
}
exec(compile(source, str(Path(__file__).resolve()), "exec"), implementation)

if __name__ == "__main__":
    raise SystemExit(int(implementation["main"]()))

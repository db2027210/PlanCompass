#!/usr/bin/env python3
"""Launch a memory Algorithm-B v39 scene after its own v61 C completes."""

from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/launch_mysql_algorithm_b_qps_only_scene_v1.py").read_text(
    encoding="utf-8"
)
replacements = {
    'CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_async_scene_v37.py"':
        'CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_async_scene_v38.py"',
    "frozen_mysql_async_outer_20260826_v38":
        "frozen_mysql_async_outer_20260826_v39",
    'and int((c_state.get("accepted_rounds") or {}).get(scene) or 0) >= 10\n':
        '',
    "v60 Algorithm C is not complete": "v61 Algorithm C is not complete",
    "mysql_algorithm_b_qps_only_scene_launch_v1":
        "mysql_algorithm_b_qps_rejection_scene_launch_v2",
}
for old, new in replacements.items():
    if source.count(old) != 1:
        raise RuntimeError(f"Algorithm-B v39 launcher fragment differs: {old}")
    source = source.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._b_v39_qps_rejection_scene_launch",
    "__package__": "scripts",
}
exec(compile(source, str(Path(__file__).resolve()), "exec"), implementation)


if __name__ == "__main__":
    raise SystemExit(int(implementation["main"]()))

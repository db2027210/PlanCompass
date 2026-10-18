#!/usr/bin/env python3
"""Monitor one eight-worker B scene and independently launch eight-worker A."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name("monitor_mysql_b_then_a_mixed_four_worker_scene_v1.py")
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "scripts/launch_mysql_algorithm_a_xp_top4_mixed_four_worker_v10.py":
        "scripts/launch_mysql_algorithm_a_xp_top4_mixed_eight_worker_v1.py",
    "mysql_b_then_a_mixed_four_worker_scene_pipeline_v1":
        "mysql_b_then_a_mixed_eight_worker_scene_pipeline_v1",
    '"physical_workers": 4, "maximum_scene_mysql_workers": 4':
        '"physical_workers": 8, "maximum_scene_mysql_workers": 8',
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"eight-worker B-to-A monitor predecessor differs: {old!r}")
    text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._monitor_mysql_b_then_a_mixed_eight_worker_v1_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

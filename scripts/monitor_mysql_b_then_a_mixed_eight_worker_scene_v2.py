#!/usr/bin/env python3
"""Monitor one XP8 B scene and independently launch Algorithm-A v2."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "monitor_mysql_b_then_a_mixed_eight_worker_scene_v1.py"
)
text = SOURCE.read_text(encoding="utf-8")
old = "scripts/launch_mysql_algorithm_a_xp_top4_mixed_eight_worker_v1.py"
new = "scripts/launch_mysql_algorithm_a_xp_top4_mixed_eight_worker_v2.py"
if text.count(old) != 1:
    raise RuntimeError("XP8 B-to-A monitor v2 predecessor differs")
text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._monitor_mysql_b_then_a_mixed_eight_worker_v2_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)


if __name__ == "__main__":
    raise SystemExit(implementation["implementation"]["main"]())

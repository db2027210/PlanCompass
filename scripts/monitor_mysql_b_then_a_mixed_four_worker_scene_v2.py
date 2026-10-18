#!/usr/bin/env python3
"""B-to-A monitor bound to the mixed Top4-A v11 launcher."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name("monitor_mysql_b_then_a_mixed_four_worker_scene_v1.py")
text = SOURCE.read_text(encoding="utf-8")
old = "scripts/launch_mysql_algorithm_a_xp_top4_mixed_four_worker_v10.py"
new = "scripts/launch_mysql_algorithm_a_xp_top4_mixed_four_worker_v11.py"
if text.count(old) != 1:
    raise RuntimeError("mixed B-to-A v2 predecessor differs")
text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._monitor_mysql_b_then_a_mixed_v2_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

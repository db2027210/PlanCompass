#!/usr/bin/env python3
"""XP8 Algorithm-A v13 with refreshed post-C source bindings."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v12.py"
)
text = SOURCE.read_text(encoding="utf-8")
old = "frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v12"
new = "frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260828_v13"
if text.count(old) != 1:
    raise RuntimeError("XP8 A v13 predecessor contract differs")
text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_a_xp8_v13_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

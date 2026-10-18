#!/usr/bin/env python3
"""XP8 B v3 runtime bound to the CPU Outer-10 transition release."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name("run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v2.py")
text = SOURCE.read_text(encoding="utf-8")
old = "frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v2"
new = "frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v3"
if text.count(old) != 1:
    raise RuntimeError("XP8 B v3 runtime predecessor differs")
text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_b_xp8_v3_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

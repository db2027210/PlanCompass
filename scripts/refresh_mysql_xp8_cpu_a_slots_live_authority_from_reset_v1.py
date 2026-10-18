#!/usr/bin/env python3
"""Refresh current CPU-A-slot XP8 live authority from terminal B reset."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "refresh_mysql_xp8_live_authority_from_reset_v1.py"
)
text = SOURCE.read_text(encoding="utf-8")
old = "frozen_mysql_xp_eight_worker_20260827_v1"
new = "frozen_mysql_xp_eight_worker_cpu_a_slots_20260828_v6"
if text.count(old) != 1:
    raise RuntimeError("current XP8 A authority predecessor differs")
text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._refresh_mysql_xp8_cpu_a_slots_v1_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

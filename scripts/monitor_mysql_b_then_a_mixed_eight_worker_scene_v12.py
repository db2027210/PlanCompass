#!/usr/bin/env python3
"""Monitor XP8 B and launch frozen Algorithm-A v9 (correct entrypoint)."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "monitor_mysql_b_then_a_mixed_eight_worker_scene_v11.py"
)
text = SOURCE.read_text(encoding="utf-8")
old = "frozen_mysql_xp_b_to_a_monitor_20260827_v11"
new = "frozen_mysql_xp_b_to_a_monitor_20260827_v12"
if text.count(old) != 1:
    raise RuntimeError("XP8 B-to-A monitor v12 predecessor differs")
text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._monitor_mysql_b_then_a_mixed_eight_worker_v12_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


if __name__ == "__main__":
    validate_contract()
    raise SystemExit(implementation["implementation"]["main"]())

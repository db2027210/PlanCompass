#!/usr/bin/env python3
"""Handoff user-level IO recovery to B-v11 then A-v9."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "monitor_mysql_xpio_unlocked_recovery_then_ba_v1.py"
)
text = SOURCE.read_text(encoding="utf-8")
text = text.replace(
    "mysql_xpio_unlocked_recovery_then_ba_v1",
    "mysql_xpio_partial_user_recovery_then_ba_v4",
)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._monitor_mysql_xpio_partial_recovery_then_ba_v4_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
implementation["B_SCRIPT"] = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_b_top4_mixed_eight_worker_io_outer11_resume_v3.py"
)
implementation["BA_MONITOR"] = Path(__file__).resolve().with_name(
    "monitor_mysql_b_then_a_mixed_eight_worker_scene_v12.py"
)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

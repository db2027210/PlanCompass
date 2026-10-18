#!/usr/bin/env python3
"""Handoff recovered IO Outer15 reset to B, then automatically to A."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "monitor_mysql_xpio_unlocked_recovery_then_ba_v1.py"
)
text = SOURCE.read_text(encoding="utf-8")
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._monitor_mysql_xpio_outer15_recovery_v20_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
implementation["B_SCRIPT"] = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_b_top4_mixed_eight_worker_io_outer14_resume_v6.py"
)
implementation["BA_MONITOR"] = Path(__file__).resolve().with_name(
    "monitor_mysql_b_then_a_mixed_eight_worker_scene_v14.py"
)
implementation["COMPOSED_SOURCE"] = (
    Path(__file__).resolve().parents[1]
    / "history/mysql_four_scene_20260809"
    / "v775_to_b_io_outer14_drain_repaired_resume_20260827_xp_io"
)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

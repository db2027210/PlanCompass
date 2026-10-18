#!/usr/bin/env python3
"""Handoff user-level CPU recovery to corrected Algorithm A v9."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "monitor_mysql_xp_cpu_unlocked_recovery_then_a_v1.py"
)
text = SOURCE.read_text(encoding="utf-8")
text = text.replace(
    "mysql_xp_cpu_unlocked_recovery_then_a_v1",
    "mysql_xp_cpu_partial_user_recovery_then_a_v5",
)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._monitor_mysql_xp_cpu_partial_recovery_then_a_v5_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
implementation["SCRIPT"] = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_a_xp_cpu_round2_wave1_committed_resume_v5.py"
)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

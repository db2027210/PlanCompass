#!/usr/bin/env python3
"""Handoff CPU recovery to corrected long-storage Algorithm A v9."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "monitor_mysql_xp_cpu_unlocked_recovery_then_a_v1.py"
)
text = SOURCE.read_text(encoding="utf-8")
old = '["systemctl", "--user", "is-active", args.recovery_unit]'
new = '["systemctl", "is-active", args.recovery_unit]'
if text.count(old) != 1:
    raise RuntimeError("XP CPU partial recovery monitor predecessor differs")
text = text.replace(old, new)
text = text.replace(
    "mysql_xp_cpu_unlocked_recovery_then_a_v1",
    "mysql_xp_cpu_partial_unlocked_recovery_then_a_v2",
)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._monitor_mysql_xp_cpu_partial_recovery_then_a_v2_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
implementation["SCRIPT"] = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_a_xp_cpu_round2_wave1_committed_resume_v4.py"
)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

#!/usr/bin/env python3
"""Handoff final-w1 Docker900 CPU recovery to Algorithm A v9."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "monitor_mysql_xp_cpu_partial_recovery_then_a_v7.py"
)
text = SOURCE.read_text(encoding="utf-8")
old = "run_mysql_algorithm_a_xp_cpu_round2_wave1_committed_resume_v7.py"
new = "run_mysql_algorithm_a_xp_cpu_round2_wave1_committed_resume_v8.py"
if text.count(old) != 1:
    raise RuntimeError("CPU recovery-to-A v8 predecessor differs")
text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._monitor_mysql_xp_cpu_partial_recovery_then_a_v8_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

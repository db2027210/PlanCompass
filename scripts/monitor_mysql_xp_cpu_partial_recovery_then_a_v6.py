#!/usr/bin/env python3
"""Handoff permission-repaired CPU recovery to Algorithm A v9."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "monitor_mysql_xp_cpu_partial_recovery_then_a_v5.py"
)
text = SOURCE.read_text(encoding="utf-8")
old = "run_mysql_algorithm_a_xp_cpu_round2_wave1_committed_resume_v5.py"
new = "run_mysql_algorithm_a_xp_cpu_round2_wave1_committed_resume_v6.py"
if text.count(old) != 1:
    raise RuntimeError("CPU recovery-to-A v6 predecessor differs")
text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._monitor_mysql_xp_cpu_partial_recovery_then_a_v6_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

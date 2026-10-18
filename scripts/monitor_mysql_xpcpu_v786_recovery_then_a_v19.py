#!/usr/bin/env python3
"""Handoff the v786 partial recovery to CPU-A Round3 continuation v15."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "monitor_mysql_xp_cpu_unlocked_recovery_then_a_v1.py"
)
text = SOURCE.read_text(encoding="utf-8")
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._monitor_mysql_xpcpu_v786_recovery_v19_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
implementation["SCRIPT"] = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_a_xp_cpu_round3_recovered_resume_v15.py"
)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

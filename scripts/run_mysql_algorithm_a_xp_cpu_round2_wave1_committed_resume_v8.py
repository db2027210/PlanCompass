#!/usr/bin/env python3
"""Resume CPU-A v9 from the final-w1 Docker900 recovery."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_a_xp_cpu_round2_wave1_committed_resume_v7.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "v707_to_a_cpu_round2_missing_four_all_permissions_repaired_20260827":
        "v717_to_a_cpu_round2_w1_docker900_recovery_20260827",
    "v708_to_a_cpu_missing_four_live_authority_20260827":
        "v718_to_a_cpu_w1_docker900_live_authority_20260827",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"CPU-A v8 continuation predecessor differs: {old}")
    text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_a_xp_cpu_round2_wave1_resume_v8_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

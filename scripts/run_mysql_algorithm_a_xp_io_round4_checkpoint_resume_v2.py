#!/usr/bin/env python3
"""Resume XP/I/O Algorithm A Round 4 using the v13 runtime binding."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_a_xp_io_round4_checkpoint_resume_v1.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v12":
        "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v13",
    "frozen_mysql_algorithm_a_xp_io_round4_checkpoint_resume_20260828_v1":
        "frozen_mysql_algorithm_a_xp_io_round4_checkpoint_resume_20260828_v2",
    "mysql_algorithm_a_xp_io_round4_checkpoint_resume_contract_v1":
        "mysql_algorithm_a_xp_io_round4_checkpoint_resume_contract_v2",
    '"runtime_v12"': '"runtime_v13"',
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"XP/I/O A Round-4 v2 predecessor differs: {old}")
    text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_a_xp_io_round4_v2_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

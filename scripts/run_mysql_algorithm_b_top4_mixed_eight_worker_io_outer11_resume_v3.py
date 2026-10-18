#!/usr/bin/env python3
"""Resume IO-B v11 after Outer11 with long Docker-control bounds."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_b_top4_mixed_eight_worker_io_outer11_resume_v2.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v10 as runtime_v8":
        "run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v11 as runtime_v8",
    "frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v10":
        "frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v11",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"IO-B v3 continuation predecessor differs: {old}")
    text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_b_io_outer11_resume_v3_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

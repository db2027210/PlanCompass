#!/usr/bin/env python3
"""XP8 B boundary resume v2 with safe Docker control serialization."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name("run_mysql_algorithm_b_top4_mixed_eight_worker_boundary_resume_v1.py")
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v1 as runtime8":
        "run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v2 as runtime8",
    "frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v1":
        "frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v2",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"XP8 B boundary v2 predecessor differs: {old}")
    text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_b_xp8_boundary_v2_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

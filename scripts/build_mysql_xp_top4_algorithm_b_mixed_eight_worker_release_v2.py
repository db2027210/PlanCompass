#!/usr/bin/env python3
"""Freeze XP8 Algorithm-B v2 Docker-control successor."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name("build_mysql_xp_top4_algorithm_b_mixed_eight_worker_release_v1.py")
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v1":
        "frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v2",
    '"runtime": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v1.py"':
        '"runtime": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v2.py"',
    '"boundary_controller": ROOT / "scripts/run_mysql_algorithm_b_top4_mixed_eight_worker_boundary_resume_v1.py"':
        '"boundary_controller": ROOT / "scripts/run_mysql_algorithm_b_top4_mixed_eight_worker_boundary_resume_v2.py"',
    '"reset_authority": ROOT / "scripts/reset_mysql_xp_data_planes_mixed_eight_worker_v1.py"':
        '"reset_authority": ROOT / "scripts/reset_mysql_xp_data_planes_mixed_eight_worker_v2.py"',
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"XP8 B release v2 predecessor differs: {old}")
    text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._build_mysql_xp8_b_release_v2_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
implementation["SOURCES"]["builder"] = Path(__file__).resolve()


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

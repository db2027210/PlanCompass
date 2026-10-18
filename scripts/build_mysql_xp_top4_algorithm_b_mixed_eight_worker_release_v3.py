#!/usr/bin/env python3
"""Freeze CPU Outer-10 Docker-control v2 continuation release."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name("build_mysql_xp_top4_algorithm_b_mixed_eight_worker_release_v2.py")
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v2":
        "frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v3",
    '"runtime": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v2.py"':
        '"runtime": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v3.py"',
    '"boundary_controller": ROOT / "scripts/run_mysql_algorithm_b_top4_mixed_eight_worker_boundary_resume_v2.py"':
        '"boundary_controller": ROOT / "scripts/run_mysql_algorithm_b_top4_mixed_eight_worker_cpu_outer10_v2_resume_v1.py"',
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"XP8 B release v3 predecessor differs: {old}")
    text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._build_mysql_xp8_b_release_v3_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
inner = implementation["implementation"]
inner["SOURCES"].update({
    "builder": Path(__file__).resolve(),
    "boundary_sealer": Path(__file__).resolve().with_name("seal_mysql_b_cpu_docker_control_v2_boundary_v1.py"),
})


if __name__ == "__main__":
    raise SystemExit(inner["main"]())

#!/usr/bin/env python3
"""Resume CPU B after Outer-10 under Docker-control v2."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name("run_mysql_algorithm_b_top4_mixed_eight_worker_boundary_resume_v1.py")
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v1 as runtime8":
        "run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v3 as runtime8",
    "frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v1":
        "frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v3",
    "v512_to_b_cpu_mixed_four_worker_boundary_resume_v72_20260827_xp_cpu":
        "v538_to_b_cpu_mixed_eight_worker_20260827_xp_cpu",
    '"accepted": 4, "rejected": 4, "outer": 8,':
        '"accepted": 4, "rejected": 6, "outer": 10,',
    "EightWorkerBoundaryExpansion: incomplete physical wave discarded":
        "DockerControlV2Boundary: completed Outer-10 preserved",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"CPU Outer-10 v2 resume predecessor differs: {old}")
    text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_b_cpu_outer10_v2_resume_v1_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
implementation["CONFIG"]["hybench_xp_cpu"]["decision_roots"] = {
    outer: Path(__file__).resolve().parents[1]
    / "history/mysql_four_scene_20260809"
    / "v512_to_b_cpu_mixed_four_worker_boundary_resume_v72_20260827_xp_cpu"
    for outer in range(1, 9)
}


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())

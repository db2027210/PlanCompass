#!/usr/bin/env python3
"""Eight-worker scheduler: all five Algorithm-B candidates in one wave."""

from __future__ import annotations

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "mysql_algorithm_b_fresh_parallel_4x_scheduler_v26.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "Four-lane scheduler for Top4 B calibration and formal Top-5 waves.":
        "Eight-worker scheduler for Top4 B calibration and one formal Top-5 wave.",
    "if len(scene_workers) != 4 or not 1 <= len(ranks) <= 4:":
        "if len(scene_workers) != 8 or not 1 <= len(ranks) <= 8:",
    "f\"{scene} frozen four-worker cardinality differs\"":
        "f\"{scene} frozen eight-worker cardinality differs\"",
    "group_start = ((rank - 1) // 4) * 4 + 1":
        "group_start = ((rank - 1) // 8) * 8 + 1",
    "ranks = tuple(range(group_start, min(group_start + 4, 16)))":
        "ranks = tuple(range(group_start, min(group_start + 8, 16)))",
    "ranks = (rank, rank + 1, rank + 2, rank + 3)":
        "ranks = (rank, rank + 1, rank + 2, rank + 3, rank + 4)",
    'state["four_lane_last_physical_wave"] = list(ranks)':
        'state["eight_lane_last_physical_wave"] = list(ranks)',
    '"calibration_candidate_batches"] = [\n            [1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12], [13, 14, 15]\n        ]':
        '"calibration_candidate_batches"] = [\n            [1, 2, 3, 4, 5, 6, 7, 8], [9, 10, 11, 12, 13, 14, 15]\n        ]',
}
for old, new in replacements.items():
    count = text.count(old)
    expected = 2 if old == "group_start = ((rank - 1) // 4) * 4 + 1" else 1
    if count != expected:
        raise RuntimeError(
            f"eight-worker scheduler predecessor differs: {old!r} count={count}"
        )
    text = text.replace(old, new)

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._mysql_algorithm_b_fresh_parallel_8x_scheduler_v27_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value

# The predecessor is a normal module, while this successor is source-composed.
# The Algorithm-B core assigns scheduler hooks as module attributes during
# scene installation.  Synchronize those late-bound attributes into the
# composed function globals immediately before each call.
_implementation_build_assignments = implementation["_build_assignments_221"]
_implementation_measure = implementation["_measure_221"]


def _build_assignments_221(**kwargs: Any):
    implementation.update({
        "SCENES": globals()["SCENES"],
        "_ORIGINAL_ASSIGNMENTS": globals()["_ORIGINAL_ASSIGNMENTS"],
    })
    return _implementation_build_assignments(**kwargs)


def _measure_221(**kwargs: Any):
    implementation.update({
        "SCENES": globals()["SCENES"],
        "_ORIGINAL_MEASURE": globals()["_ORIGINAL_MEASURE"],
    })
    return _implementation_measure(**kwargs)

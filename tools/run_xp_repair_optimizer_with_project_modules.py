#!/usr/bin/env python3
"""Run the frozen XP optimizer while resolving shared modules from this project."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


PROJECT_ROOT = Path("/home/anonymous/program/resource_adaptive")
REPAIR_ROOT = Path(
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
    "hybench_xp_compute00_59630/experience_sgd/"
    "attempt39_fresh_from_accepted3_20260726/repair"
)
OPTIMIZER = REPAIR_ROOT / "parameter_optimizer.py"

sys.path = [
    str(PROJECT_ROOT),
    *[
        item
        for item in sys.path
        if Path(item or ".").resolve() not in {PROJECT_ROOT, REPAIR_ROOT}
    ],
    str(REPAIR_ROOT),
]
runpy.run_path(str(OPTIMIZER), run_name="__main__")

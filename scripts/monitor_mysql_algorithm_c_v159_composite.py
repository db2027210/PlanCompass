#!/usr/bin/env python3
"""Monitor the three live v158 scenes plus the corrected v159 TPC-H scene."""

from __future__ import annotations

import copy
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import monitor_mysql_algorithm_c_v157_full_resume as base


def main() -> int:
    original_load = base.load

    def compatible_load(path: Path) -> dict:
        value = original_load(path)
        if value.get("schema_version") == "mysql_algorithm_c_v159_composite_launch_v1":
            value = copy.deepcopy(value)
            value["schema_version"] = "mysql_algorithm_c_v157_full_resume_launch_v1"
        return value

    base.load = compatible_load
    try:
        return int(base.main())
    finally:
        base.load = original_load


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run client-primary C with the scene-scoped XP writeback settle gate."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_async_xp_post_reset_settle_v2 as scoped_settle
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v9 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v32 as boundary_v32


def main() -> int:
    original_boundary_main = boundary_v32.main

    def scoped_boundary_main() -> int:
        original_reset = boundary_v32.reset_xp
        boundary_v32.reset_xp = scoped_settle.reset_xp
        try:
            return int(original_boundary_main())
        finally:
            boundary_v32.reset_xp = original_reset

    boundary_v32.main = scoped_boundary_main
    try:
        return int(predecessor.main())
    finally:
        boundary_v32.main = original_boundary_main


if __name__ == "__main__":
    raise SystemExit(main())

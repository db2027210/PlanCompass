#!/usr/bin/env python3
"""Rebind v13 workers after the boundary-only R0 XP reset."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import rebind_mysql_four_scene_live_v12 as v13_defaults
from scripts import rebind_mysql_four_scene_live_v8 as implementation


implementation.XP_PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "xp_reset_after_failed_r0_v14_20260811T120000p0800"
    / "prepared_binding.json"
)


if __name__ == "__main__":
    raise SystemExit(implementation.main())

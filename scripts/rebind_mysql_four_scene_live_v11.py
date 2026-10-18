#!/usr/bin/env python3
"""Rebind frozen v12 workers after the formal-v10 XP recovery."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import rebind_mysql_four_scene_live_v8 as implementation


implementation.XP_PREPARED = (
    ROOT
    / "history/mysql_four_scene_20260809"
    / "xp_restore_after_failed_formal_v21_20260811T021430p0800"
    / "prepared_binding.json"
)


if __name__ == "__main__":
    raise SystemExit(implementation.main())

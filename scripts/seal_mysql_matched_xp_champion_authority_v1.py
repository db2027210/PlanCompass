#!/usr/bin/env python3
"""Seal post-baseline live authority for matched XP champion replays."""

from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import seal_mysql_matched_xp_replay_authority_v1 as implementation


implementation.OUTPUT = ROOT / (
    "history/mysql_four_scene_20260809/"
    "v1105_matched_xp_post_baseline_live_authority_20260829"
)


if __name__ == "__main__":
    raise SystemExit(implementation.main())

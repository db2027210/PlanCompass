#!/usr/bin/env python3
"""Build a scene authority against the mixed-SSD client-affinity v4 freeze."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import build_mysql_mixed_disk_four_worker_scene_authority_v1 as predecessor


predecessor.FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_mixed_disk_four_worker_20260827_v4"
    / "four_scene_effective_resource_successor.json"
)
predecessor.FREEZE_SCHEMA = "mysql_four_scene_mixed_disk_four_worker_successor_v4"


if __name__ == "__main__":
    raise SystemExit(predecessor.main())

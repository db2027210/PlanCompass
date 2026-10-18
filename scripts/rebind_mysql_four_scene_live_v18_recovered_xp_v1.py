#!/usr/bin/env python3
"""Bind all eight v18 workers to the recovered live XP identities."""

from pathlib import Path
import os
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import rebind_mysql_four_scene_live_v18_formal as release


XP_PREPARED = os.environ.get("MYSQL_V18_RECOVERED_XP_BINDING")
if not XP_PREPARED:
    raise SystemExit("MYSQL_V18_RECOVERED_XP_BINDING is required")

release.implementation.XP_PREPARED = Path(XP_PREPARED).resolve()
release.implementation.OUTPUT_SCHEMA = (
    "mysql_four_scene_live_rebind_v18_recovered_xp_v1"
)
release.implementation.AUTHORITY_VERSION = "v18_recovered_xp_v1"


if __name__ == "__main__":
    raise SystemExit(release.implementation.main())

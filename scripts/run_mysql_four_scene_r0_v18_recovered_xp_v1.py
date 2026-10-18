#!/usr/bin/env python3
"""Run fresh formal v18 R0 with the current isolation-v4 semantics."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import watch_mysql_formal_external_postgres_interference_v5 as watchdog


watchdog.install()
from scripts import run_mysql_four_scene_r0_v18 as release


if __name__ == "__main__":
    raise SystemExit(release.implementation.main())

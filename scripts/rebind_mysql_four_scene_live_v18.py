#!/usr/bin/env python3
"""Rebind v15 workers after the post-failure resume preflight v32."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import rebind_mysql_four_scene_live_v16 as implementation


implementation.implementation.PREFLIGHT = (
    ROOT / "preflight/mysql_four_scene_20260809/runtime_preflight_v32.json"
)
implementation.implementation.OUTPUT_SCHEMA = (
    "mysql_four_scene_live_rebind_v15_outer1_resume_retry_v1"
)
implementation.implementation.AUTHORITY_VERSION = (
    "v15_algorithm_c_outer1_resume_retry_v1"
)


if __name__ == "__main__":
    raise SystemExit(implementation.implementation.main())

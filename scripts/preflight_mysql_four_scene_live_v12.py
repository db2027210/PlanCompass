#!/usr/bin/env python3
"""Run the v15 live gate after the formal Docker-remove timeout recovery."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Importing v11 installs the frozen v15/v14 successor contract and its exact
# resource-envelope change validator on the shared implementation module.
from scripts import preflight_mysql_four_scene_live_v11 as v15_defaults  # noqa: F401,E402
from scripts import preflight_mysql_four_scene_live_v8 as implementation  # noqa: E402


implementation.XP_PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "xp_recovery_docker_rm_timeout_v1_20260811T164300p0800"
    / "prepared_binding.json"
)
implementation.OUTPUT_SCHEMA = "mysql_four_scene_live_runtime_preflight_v16"
implementation.AUTHORITY_VERSION = "v16_post_docker_rm_timeout_recovery"


if __name__ == "__main__":
    raise SystemExit(implementation.main())

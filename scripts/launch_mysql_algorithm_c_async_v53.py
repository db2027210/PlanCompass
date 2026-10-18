#!/usr/bin/env python3
"""Launch independent XP Top-4 Algorithm C v53 controllers."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_algorithm_c_async_v49 as base
from scripts import run_mysql_algorithm_c_async_scene_v53 as controller


base.CONTROLLER = Path(controller.__file__).resolve()
base.CONTRACT = controller.ASYNC_CONTRACT
base.validate_contract = controller.validate_contract


if __name__ == "__main__":
    raise SystemExit(base.main())

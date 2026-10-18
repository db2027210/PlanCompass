#!/usr/bin/env python3
"""Launch the v51 cached-incumbent preemption successor."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_algorithm_c_async_v49 as predecessor
from scripts import run_mysql_algorithm_c_async_scene_v51 as controller


predecessor.CONTROLLER = Path(controller.__file__).resolve()
predecessor.CONTRACT = controller.ASYNC_CONTRACT
predecessor.validate_contract = controller.validate_contract


if __name__ == "__main__":
    raise SystemExit(predecessor.main())

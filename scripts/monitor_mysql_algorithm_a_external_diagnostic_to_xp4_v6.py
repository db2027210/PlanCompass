#!/usr/bin/env python3
"""Monitor v129 and hand off to the parallel-reset XP4 successors."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import monitor_mysql_algorithm_a_external_diagnostic_to_xp4_v5 as prior


prior.CONFIG = ROOT / "config/formal/frozen_mysql_algorithm_a_external_diagnostic_to_xp4_20260824_v8"
prior.monitor.CONTRACT_DIR = prior.CONFIG
prior.monitor.SUCCESSOR_FREEZE = prior.CONFIG / "xp_four_worker_resource_successor.json"
prior.monitor.EXECUTOR = ROOT / "scripts/execute_mysql_algorithm_a_external_diagnostic_to_xp4_v5.py"


if __name__ == "__main__":
    raise SystemExit(prior.main())

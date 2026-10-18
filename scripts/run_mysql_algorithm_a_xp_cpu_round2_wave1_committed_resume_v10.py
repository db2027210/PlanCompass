#!/usr/bin/env python3
"""Resume CPU-A on the refreshed v10 frozen release."""

import os
from pathlib import Path
import sys
import traceback


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_a_xp_cpu_round2_wave1_committed_resume_v9 as predecessor
from scripts import run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v10 as runtime_v10


if predecessor.implementation.get("runtime") is not predecessor.runtime:
    raise RuntimeError("CPU-A v10 continuation runtime binding differs")
predecessor.implementation["runtime"] = runtime_v10
predecessor.runtime = runtime_v10


if __name__ == "__main__":
    try:
        raise SystemExit(predecessor.implementation["main"]())
    except BaseException:
        diagnostic = os.environ.get("MYSQL_XP_CPU_A_FAILURE_DIAGNOSTIC", "")
        if diagnostic:
            Path(diagnostic).write_text(traceback.format_exc(), encoding="utf-8")
        raise

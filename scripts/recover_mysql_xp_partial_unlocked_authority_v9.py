#!/usr/bin/env python3
"""Recover the interrupted IO-B Outer15 rank-1 reset."""

import os
from pathlib import Path
import sys
import traceback


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import recover_mysql_xp_partial_unlocked_authority_v2 as recovery


recovery.SCENES["hybench_xp_io"]["token"] = "algorithm_b_outer_015_rank_1"


if __name__ == "__main__":
    try:
        raise SystemExit(recovery.main())
    except BaseException:
        diagnostic = os.environ.get("MYSQL_XP_RECOVERY_FAILURE_DIAGNOSTIC", "")
        if diagnostic:
            Path(diagnostic).write_text(traceback.format_exc(), encoding="utf-8")
        raise

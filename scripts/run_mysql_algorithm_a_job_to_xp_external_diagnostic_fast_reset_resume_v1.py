#!/usr/bin/env python3
"""Resume XP4 with external diagnostics and parallel scene-scoped resets."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_async_xp_post_reset_settle_v2 as fast_reset
from scripts import mysql_single_scene_measurement_v1 as single_measurement
from scripts import run_mysql_algorithm_a_job_to_xp_external_diagnostic_resume_v1 as prior


_ORIGINAL_CONFIGURE = prior.configure_runtime


def configure_runtime(**kwargs):
    result = _ORIGINAL_CONFIGURE(**kwargs)
    single_measurement.async_xp_settle = fast_reset
    return result


def main() -> int:
    prior.configure_runtime = configure_runtime
    prior.predecessor.configure_runtime = configure_runtime
    single_measurement.async_xp_settle = fast_reset
    return prior.main()


if __name__ == "__main__":
    raise SystemExit(main())

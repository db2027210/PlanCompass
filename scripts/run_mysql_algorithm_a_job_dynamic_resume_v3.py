#!/usr/bin/env python3
"""Run the four-worker JOB resume on the current v4 Algorithm A controller.

The audited JOB transition adapter is deliberately kept unchanged.  This
thin successor selects the v4 controller, whose only flow change is retaining
valid candidates when their wave sibling fails technically.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_a_async_scene_v3 as current
from scripts import run_mysql_algorithm_a_job_dynamic_resume_v2 as adapter


def main() -> int:
    adapter.base = current
    return adapter.main()


if __name__ == "__main__":
    raise SystemExit(main())

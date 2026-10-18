#!/usr/bin/env python3
"""Resume an interrupted discard reset with one worker at a time."""

from concurrent.futures import ThreadPoolExecutor as BaseThreadPoolExecutor
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import pilot_mysql_algorithm_c_discard_reset_v2 as implementation


class SerialThreadPoolExecutor(BaseThreadPoolExecutor):
    def __init__(self, max_workers=None, thread_name_prefix="", **kwargs):
        super().__init__(
            max_workers=1,
            thread_name_prefix=f"serial-{thread_name_prefix}",
            **kwargs,
        )


implementation.ThreadPoolExecutor = SerialThreadPoolExecutor


if __name__ == "__main__":
    raise SystemExit(implementation.main())

#!/usr/bin/env python3
"""Prepare the C-to-B handoff with current Algorithm B v2 bindings."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_b_from_algorithm_c_release_v2 as release_v2
from scripts import wait_prepare_mysql_algorithm_b_after_c_v1 as predecessor


def main() -> int:
    predecessor.handoff = release_v2
    predecessor.SCENES = list(release_v2.SCENES)
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())

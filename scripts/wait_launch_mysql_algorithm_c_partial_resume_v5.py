#!/usr/bin/env python3
"""Wait for a fresh clean window and launch the v5 partial recovery."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import wait_launch_mysql_algorithm_c_partial_resume_v4 as waiter


def main() -> int:
    waiter.LAUNCHER = (
        ROOT / "scripts/launch_mysql_algorithm_c_boundary_resume_v5.py"
    )
    return waiter.main()


if __name__ == "__main__":
    raise SystemExit(main())

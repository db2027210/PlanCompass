#!/usr/bin/env python3
"""Launch v29 with a 90-second controller publication window."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_algorithm_c_boundary_resume_v29 as v29


def main() -> int:
    launcher = v29.v28.v25.v24.v23.v22.v20.v19.launcher
    real_monotonic = launcher.time.monotonic
    real_origin = real_monotonic()

    def extended_startup_clock() -> float:
        # The v2 launcher's 30-second state-publication window predates the
        # Outer-17 checkpoint chain.  Slowing only its deadline clock by 3x
        # yields a 90-second wall-clock bound; subprocess timeouts and the
        # formal controller's measurement clocks are untouched.
        return real_origin + (real_monotonic() - real_origin) / 3.0

    launcher.time.monotonic = extended_startup_clock
    try:
        return v29.main()
    finally:
        launcher.time.monotonic = real_monotonic


if __name__ == "__main__":
    raise SystemExit(main())

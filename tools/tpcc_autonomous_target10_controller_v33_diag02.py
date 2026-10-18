#!/usr/bin/env python3
"""TPC-C v33: start from a sealed diag02-calibrated trusted3/probe2 run."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import tpcc_autonomous_target10_controller_v32_validation_repeat as v32


def main() -> int:
    return v32.main()


if __name__ == "__main__":
    raise SystemExit(main())

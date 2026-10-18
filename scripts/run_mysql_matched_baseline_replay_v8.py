#!/usr/bin/env python3
"""Run the v7 baseline replay implementation with an explicitly frozen contract."""

from __future__ import annotations

import argparse
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import run_mysql_matched_baseline_replay_v7 as implementation


DEFAULT_CONTRACT = Path(
    "config/formal/frozen_mysql_matched_xp_baseline_replay_20260829_v2/"
    "baseline_replay_contract.json"
)


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    args, remaining = parser.parse_known_args()
    implementation.CONTRACT = args.contract.resolve()
    sys.argv = [sys.argv[0], *remaining]
    return implementation.main()


if __name__ == "__main__":
    raise SystemExit(main())

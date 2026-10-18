#!/usr/bin/env python3
"""Correct the v14 dynamic-implementation sidecar lookup."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_b_xp_top4_async_v14 as predecessor

predecessor.successor.CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v24"
)


def _load_verified(path: Path):
    core = predecessor.successor.predecessor
    core._impl["_verified_sidecar"](path)
    return core._impl["alg"].load(path)


predecessor._load_verified = _load_verified

if __name__ == "__main__":
    raise SystemExit(predecessor.main())

#!/usr/bin/env python3
"""Freeze the four-worker Top4 IO Algorithm C boundary-recovery release."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import prepare_mysql_algorithm_c_xp4_handoff_v1 as release


OUTPUT = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_top4_io_xp4_recovery_20260826_v8"
)
SUCCESSOR = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_xp4_handoff_20260826_v2"
    / "xp_four_worker_resource_successor.json"
)


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError("fresh Algorithm C IO XP4 recovery release required")
    OUTPUT.mkdir(parents=True, mode=0o700)
    release.CONTRACT_ROOT = OUTPUT
    release.SUCCESSOR_FREEZE = SUCCESSOR
    release.TRANSITION_CONTRACT = OUTPUT / "transition_contract.json"
    release.OPTIMIZATION_CONTRACT = OUTPUT / "optimization_contract.json"
    release.COORDINATION_CONTRACT = OUTPUT / "coordination_contract.json"
    release.ASYNC_CONTRACT = OUTPUT / "algorithm_c_xp4_async_contract.json"
    release.write_contracts(release.sha(SUCCESSOR))
    print(json.dumps({
        "status": "FROZEN",
        "release": str(OUTPUT),
        "successor_freeze": str(SUCCESSOR),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

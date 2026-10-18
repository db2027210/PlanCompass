#!/usr/bin/env python3
"""Run v95 C from a memory-pair checkpoint with the v81 rolling gate."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_memory_pair_gate_checkpoint_v1 as checkpoint
from scripts import mysql_cross_project_wave_coordination_v25 as gate_v25
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v6 as predecessor


def main() -> int:
    v5 = predecessor.predecessor
    v4 = v5.predecessor
    v3 = v4.predecessor
    v1 = v3.predecessor
    v74 = v1.predecessor
    v73 = v74.predecessor
    original_gate = v73.gate_v11
    original_checkpoint_verify = v73._ORIGINAL_CHECKPOINT_VERIFY
    v73.gate_v11 = gate_v25
    v73._ORIGINAL_CHECKPOINT_VERIFY = checkpoint.verify_checkpoint
    try:
        return int(predecessor.main())
    finally:
        v73.gate_v11 = original_gate
        v73._ORIGINAL_CHECKPOINT_VERIFY = original_checkpoint_verify


if __name__ == "__main__":
    raise SystemExit(main())

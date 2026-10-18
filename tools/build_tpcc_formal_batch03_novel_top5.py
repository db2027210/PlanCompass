#!/usr/bin/env python3
"""Build the next novel TPC-C formal Top-5 after formal batch 02."""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tools.build_tpcc_round14_robust_regime_top5 as robust


ATTEMPT = robust.ATTEMPT
ROUND14 = (
    ATTEMPT / "gradient_debug_round14_robust_regime_interval_v2_20260729T1824"
)
FORMAL02 = ATTEMPT / "formal10_tpcc_batch02_robust_gate_20260729T1902"
robust.SOURCES = robust.SOURCES + (
    (
        ROUND14 / "top5.json",
        ROUND14 / "strict_top5_real_replay_round14_ledger_v1.json",
        "round14_robust_diagnostic",
    ),
    (
        FORMAL02 / "top5.json",
        FORMAL02 / "strict_top5_real_replay_round14_ledger_v1.json",
        "formal_batch02",
    ),
)
robust.OUTPUT = Path("/tmp/tpcc_formal_batch03_novel_top5_v1.json")


if __name__ == "__main__":
    robust.main()

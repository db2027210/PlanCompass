#!/usr/bin/env python3
"""Build TPC-C formal batch 04 from all clean evidence through batch 03."""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tools.build_tpcc_formal_batch03_novel_top5 as batch03


robust = batch03.robust
ATTEMPT = robust.ATTEMPT
FORMAL01 = (
    ATTEMPT / "formal10_tpcc_batch01_after_entry_20260729T1554"
)
FORMAL03 = (
    ATTEMPT / "formal10_tpcc_batch03_novel_retry2_20260729T1945"
)
robust.SOURCES = robust.SOURCES + (
    (
        FORMAL01 / "top5.json",
        FORMAL01 / "strict_top5_real_replay_round13_ledger_v1.json",
        "formal_batch01",
    ),
    (
        FORMAL03 / "top5.json",
        FORMAL03 / "strict_top5_real_replay_round14_ledger_v1.json",
        "formal_batch03",
    ),
)
robust.OUTPUT = Path("/tmp/tpcc_formal_batch04_novel_top5_v2.json")


if __name__ == "__main__":
    robust.main()

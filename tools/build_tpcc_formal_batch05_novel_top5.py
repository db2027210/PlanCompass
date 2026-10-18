#!/usr/bin/env python3
"""Build TPC-C formal batch 05 from clean evidence through batch 04.

This generator performs plan-estimator EXPLAIN calls but never invokes the
TPC-C workload.  It is intentionally reserved for the main agent; subagents
may only inspect and test it offline.
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tools.build_tpcc_formal_batch04_novel_top5 as batch04


robust = batch04.robust
ATTEMPT = robust.ATTEMPT
FORMAL04 = ATTEMPT / "formal10_tpcc_batch04_novel_v2_20260729T2023"
robust.SOURCES = robust.SOURCES + (
    (
        FORMAL04 / "top5.json",
        FORMAL04 / "strict_top5_real_replay_round14_ledger_v1.json",
        "formal_batch04",
    ),
)
robust.OUTPUT = Path("/tmp/tpcc_formal_batch05_novel_top5_v1.json")


if __name__ == "__main__":
    robust.main()

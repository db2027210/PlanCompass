#!/usr/bin/env python3
"""Build TPC-C formal batch 06 from clean evidence through batch 05.

This generator updates exact plan-regime residual calibration with the clean
batch05 replay.  It performs EXPLAIN-only plan estimation and never invokes
the TPC-C workload.  The two probes are restricted to previously unseen plan
regimes.
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tools.build_tpcc_formal_batch05_novel_top5 as batch05


robust = batch05.robust
ATTEMPT = robust.ATTEMPT
FORMAL05 = (
    ATTEMPT
    / "formal10_tpcc_batch05_novel_v1_retry2_runnerdeps_20260729T2110"
)
robust.SOURCES = robust.SOURCES + (
    (
        FORMAL05 / "top5.json",
        FORMAL05 / "strict_top5_real_replay_round14_ledger_v1.json",
        "formal_batch05",
    ),
)
robust.PROBES_MUST_BE_UNKNOWN = True
robust.PARAM_RANGES_OVERRIDE = ATTEMPT / "formal10_tpcc_state.json"
robust.OUTPUT = Path(
    "/tmp/tpcc_formal_batch06_novel_top5_v2_rangebound.json"
)


if __name__ == "__main__":
    robust.main()

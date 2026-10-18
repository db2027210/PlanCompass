#!/usr/bin/env python3
"""Build formal TPC-C batch06 after the clean two-regime acquisition.

The builder performs EXPLAIN-only estimation.  It must be run by the main
agent; subagents only prepare and inspect it offline.
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tools.build_tpcc_formal_batch06_novel_top5 as batch06


robust = batch06.robust
ATTEMPT = robust.ATTEMPT
ACQUISITION = (
    ATTEMPT
    / "gradient_debug_batch06_minimal_unknown_probe2_v2_20260729T2340"
)
robust.SOURCES = robust.SOURCES + (
    (
        ACQUISITION / "top2.json",
        ACQUISITION / "strict_top2_real_replay_acquisition_ledger_v1.json",
        "diagnostic_minimal_probe2",
    ),
)
robust.PARAM_RANGES_OVERRIDE = None
robust.FORMAL_STATE_AUDIT_SOURCE = ATTEMPT / "formal10_tpcc_state.json"
robust.CANDIDATE_RANGE_POLICY = (
    "state_preserving_exploration_domain_v1"
)
robust.PROBES_MUST_BE_UNKNOWN = False
robust.ALLOW_SPARSE_EXACT_REGIME_PROBES = True
robust.SPARSE_EXACT_REGIME_MAX_OBSERVATIONS = 1
robust.TRUSTED_PLAN_DIVERSITY_REQUIRED = True
robust.OUTPUT = Path(
    "/tmp/tpcc_formal_batch06_post_probe2_top5_v1.json"
)


if __name__ == "__main__":
    robust.main()

#!/usr/bin/env python3
"""Seal r72 with the shared baseline-ENOSPC manifest contract."""

from __future__ import annotations

from pathlib import Path

import seal_pg_algorithm_c_r71_enospc_failure as seal


seal.ROOT = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260822_algorithm_c_monotonic_qps_top10_current_interval_clean_r72"
)
seal.EVIDENCE = seal.ROOT / "failure_evidence/enospc_baseline_rep2"
seal.OUTPUT = seal.ROOT / "campaign_technical_failure_manifest.json"
seal.SPACE_RELIEF = {
    "source": None,
    "destination": None,
    "experiment_authority_removed": False,
}


if __name__ == "__main__":
    raise SystemExit(seal.main())

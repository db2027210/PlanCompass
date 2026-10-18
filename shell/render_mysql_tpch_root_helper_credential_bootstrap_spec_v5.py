#!/usr/bin/env python3
"""Render a qualification-only spec for the workload-lifetime successor."""
from __future__ import annotations

from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import render_mysql_tpch_v23_recalibrated_bootstrap_spec_v3 as renderer


BUNDLE_ID = "c4fd221bb397ad7077e2cc4463d8dd93514fc9d3ed89772d45c33b2332e401cd"
MANIFEST_SHA256 = "e4da2fe2b714177e10fdaab5da0d39686ba13f40727f122245a65b88166e761f"
MANIFEST = Path(
    "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/"
    "runtime_algorithm_c_successor_20260729/bundles/"
    + BUNDLE_ID
    + "/bundle_manifest.json"
)
renderer.BINDINGS[BUNDLE_ID] = {
    "manifest": MANIFEST,
    "manifest_sha256": MANIFEST_SHA256,
    "predecessor_bundle_id":
        "a306d4ca1f7e3b4ca975e78efe313b6d99752b8d433042bf3b798e57b1ec9a91",
    "envelope": {
        "cpu_quota_cores": 5.0,
        "memory_bytes": 6174015488,
        "memory_swap_bytes": 6174015488,
        "io_read_bps": 272629760,
        "io_write_bps": 272629760,
        "profile_intervals": {
            "cpu": [0.3, 0.5],
            "io": [0.3, 0.5],
            "memory": [0.85, 0.95],
        },
    },
    "recalibration_evidence": "tpch_io_margin_recalibration_evidence_v2.json",
    "affinity_remediation": True,
    "memswap_consistency": True,
}
renderer.BUNDLE_ID = BUNDLE_ID
renderer.MANIFEST_SHA256 = MANIFEST_SHA256
renderer.DEFAULT_BUNDLE_MANIFEST = MANIFEST


if __name__ == "__main__":
    raise SystemExit(renderer.main())

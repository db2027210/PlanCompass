#!/usr/bin/env python3
"""Render a qualification-only bootstrap spec for the 260 MiB/s successor."""
from __future__ import annotations

from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import render_mysql_tpch_v23_recalibrated_bootstrap_spec_v3 as renderer


ROOT = HERE.parent
BUNDLE_ID = "7c83ce8ebdff862225d8e8cc5a78213e1a3b8808a740dbcb4e421d9f715fe01c"
MANIFEST_SHA256 = "762e307d5c3ddbe6ba44eb9d8c4b7b0b6f7abf2d4c00cd10e59d517b530d79e3"
MANIFEST = (
    ROOT / "artifacts/mysql_tpch_io_margin_successor_v3"
    / BUNDLE_ID / "bundle_manifest.json"
)
renderer.BINDINGS[BUNDLE_ID] = {
    "manifest": MANIFEST,
    "manifest_sha256": MANIFEST_SHA256,
    "predecessor_bundle_id":
        "87ed507ed689c103781db5f96332ae7875e4773d04d69b98a7e63e3d4097b257",
    "envelope": {
        "cpu_quota_cores": 5.0,
        "memory_bytes": 6174015488,
        "memory_swap_bytes": 6174015488,
        "io_read_bps": 272629760,
        "io_write_bps": 272629760,
        "profile_intervals": {
            "cpu": [0.3, 0.5], "io": [0.3, 0.5],
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

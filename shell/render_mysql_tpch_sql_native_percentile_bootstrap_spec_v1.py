#!/usr/bin/env python3
"""Render a qualification-only spec bound exactly to the b235 successor."""
from __future__ import annotations

from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import render_mysql_tpch_v23_recalibrated_bootstrap_spec_v3 as renderer


BUNDLE_ID = "b235d1ca300e5ad02fe77005096fc511bfbdc5d91b9601233a66a10502be5a47"
MANIFEST_SHA256 = "386f59b23f20eb7be59665c42c44fe2453512bf2112d850e12df6b398a16f439"
MANIFEST = Path(
    "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/"
    "runtime_algorithm_c_successor_20260729/bundles/"
    + BUNDLE_ID
    + "/bundle_manifest.json"
)
renderer.BINDINGS = {
    BUNDLE_ID: {
        "manifest": MANIFEST,
        "manifest_sha256": MANIFEST_SHA256,
        "predecessor_bundle_id":
            "ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4",
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
        "recalibration_evidence":
            "tpch_io_margin_recalibration_evidence_v2.json",
        "affinity_remediation": True,
        "memswap_consistency": True,
        "sealed_bundle_pytest_regression_count": 43,
        "successor_generation":
            "sql_native_completed_latency_contract_v1",
        "shutdown_successor_generation":
            "deadline_bounded_query_join_v1",
        "collector_sha256":
            "89713310afbd157963bd9efb101cc495014e2ae5722004db3e51664b3dd9d2de",
    }
}
renderer.BUNDLE_ID = BUNDLE_ID
renderer.MANIFEST_SHA256 = MANIFEST_SHA256
renderer.DEFAULT_BUNDLE_MANIFEST = MANIFEST


if __name__ == "__main__":
    try:
        raise SystemExit(renderer.main())
    except (OSError, renderer.RenderError, UnicodeError, ValueError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)

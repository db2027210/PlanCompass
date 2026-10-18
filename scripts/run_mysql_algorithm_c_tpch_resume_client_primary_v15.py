#!/usr/bin/env python3
"""TPC-H C resume entrypoint restoring its SHA-bound client-QPS baseline."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_c_measurement_client_primary_v4 as metric
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v14 as base


NORMALIZATION = ROOT / (
    "history/mysql_four_scene_20260809/"
    "v1223_to_c_pg_latest_v103_20260905_tpch/formal_incumbent_baseline/"
    "same_worker_qps_normalization.json"
)
EXPECTED_SHA256 = "35e1eb20754cd774ca44d214c592153808b7a749bc32106b109415c274f6e73a"


def install_normalization() -> None:
    document = metric.predecessor.predecessor.load(NORMALIZATION)
    baseline = Path(str(document.get("baseline_batch_path") or "")).resolve()
    if (
        metric.predecessor.predecessor.sha(NORMALIZATION) != EXPECTED_SHA256
        or document.get("status") != "PASS"
        or document.get("formal_scope") != "mysql_only"
        or document.get("cross_engine_comparison_prohibited") is not True
        or document.get("scene") != "tpch_sf4_memory"
        or document.get("schema_version")
        != "mysql_tpch_same_worker_client_qps_normalization_v1"
        or set(document.get("worker_baseline_client_qps") or {})
        != {"tpch_w1", "tpch_w2"}
        or not baseline.is_file()
        or metric.predecessor.predecessor.sha(baseline)
        != document.get("baseline_batch_sha256")
    ):
        raise RuntimeError("TPC-H C resume normalization authority differs")
    metric._NORMALIZATION.clear()
    metric._NORMALIZATION.update(document)


if __name__ == "__main__":
    install_normalization()
    raise SystemExit(base.base.main())

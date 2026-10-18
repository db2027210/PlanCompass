#!/usr/bin/env python3
"""Resume v81-gated C with checkpoint TPC-H normalization carry-forward."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v7 as predecessor


NORMALIZATION = ROOT / (
    "history/mysql_four_scene_20260809/"
    "v1194_to_c_client_primary_four_scene_20260903_memory_pair_job/"
    "formal_incumbent_baseline/same_worker_qps_normalization.json"
)
NORMALIZATION_SHA256 = (
    "fc50b7c5fa8fd02f6ce174896bd3313247b2046cfb6b29db9c004678d4faee85"
)
BASELINE = ROOT / (
    "history/mysql_four_scene_20260809/"
    "v1194_to_c_client_primary_four_scene_20260903_memory_pair_job/"
    "formal_incumbent_baseline/batch_result.json"
)
BASELINE_SHA256 = (
    "6003b344a3e6be5b60338cf205c9dc8dbe68ed7aa05ea4bfb97ea10da540300f"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalization_carry_forward() -> dict[str, Any]:
    for path, expected in (
        (NORMALIZATION, NORMALIZATION_SHA256),
        (BASELINE, BASELINE_SHA256),
    ):
        if not path.is_file() or path.is_symlink() or sha(path) != expected:
            raise RuntimeError(f"checkpoint baseline normalization source differs: {path}")
    value = json.loads(NORMALIZATION.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError("checkpoint baseline normalization object required")
    document = dict(value)
    worker_qps = dict(document.get("worker_baseline_client_qps") or {})
    factors = dict(document.get("worker_to_canonical_factors") or {})
    if (
        document.get("schema_version")
        != "mysql_tpch_same_worker_client_qps_normalization_v1"
        or document.get("status") != "PASS"
        or document.get("formal_scope") != "mysql_only"
        or document.get("cross_engine_comparison_prohibited") is not True
        or document.get("scene") != "tpch_sf4_memory"
        or Path(str(document.get("baseline_batch_path") or "")).resolve()
        != BASELINE.resolve()
        or document.get("baseline_batch_sha256") != BASELINE_SHA256
        or set(worker_qps) != {"tpch_w1", "tpch_w2"}
        or set(factors) != {"tpch_w1", "tpch_w2"}
        or any(float(value) <= 0.0 for value in worker_qps.values())
        or any(float(value) <= 0.0 for value in factors.values())
        or float(document.get("canonical_baseline_client_qps") or 0.0) <= 0.0
    ):
        raise RuntimeError("checkpoint baseline normalization authority differs")
    # Runtime normalization contains only the mathematical contract, matching
    # the value originally stored in the v1194 run state.
    return {
        key: copy.deepcopy(document[key])
        for key in (
            "schema_version",
            "scene",
            "canonical_baseline_client_qps",
            "worker_baseline_client_qps",
            "worker_to_canonical_factors",
            "comparison_semantics",
        )
    }


def main() -> int:
    measurement = predecessor.predecessor.measurement
    original = copy.deepcopy(measurement._NORMALIZATION)
    measurement._NORMALIZATION.clear()
    measurement._NORMALIZATION.update(normalization_carry_forward())
    try:
        return int(predecessor.main())
    finally:
        measurement._NORMALIZATION.clear()
        measurement._NORMALIZATION.update(original)


if __name__ == "__main__":
    raise SystemExit(main())

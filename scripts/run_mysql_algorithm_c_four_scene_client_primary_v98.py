#!/usr/bin/env python3
"""Fresh PG-exact C search with deterministic CPU-only hwloc discovery."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v97 as predecessor


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_hwloc_cpu_only_20260929_v190/"
    "algorithm_c_hwloc_contract.json"
)
HWLOC_COMPONENTS = "-opencl,-cuda,-nvml,-rsmi,-levelzero,-gl"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def validate_contract() -> dict[str, Any]:
    value = load(CONTRACT)
    if (
        value.get("release_version") != "v190-hwloc-cpu-only-discovery"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("fresh_search_required") is not True
        or value.get("hwloc_components") != HWLOC_COMPONENTS
        or value.get("cpu_or_numa_binding_changed") is not False
        or value.get("workload_or_candidate_policy_changed") is not False
    ):
        raise RuntimeError("Algorithm C v190 hwloc contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = Path(str(raw.get("path") or ""))
        if not path.is_absolute():
            path = ROOT / path
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v190 source differs: {name}: {path}")
    return value


def main() -> int:
    validate_contract()
    if "--fresh-search" not in sys.argv[1:]:
        raise RuntimeError("v190 requires fresh search")
    existed = "HWLOC_COMPONENTS" in os.environ
    previous = os.environ.get("HWLOC_COMPONENTS")
    os.environ["HWLOC_COMPONENTS"] = HWLOC_COMPONENTS
    try:
        return int(predecessor.main())
    finally:
        if existed:
            os.environ["HWLOC_COMPONENTS"] = str(previous)
        else:
            os.environ.pop("HWLOC_COMPONENTS", None)


if __name__ == "__main__":
    raise SystemExit(main())

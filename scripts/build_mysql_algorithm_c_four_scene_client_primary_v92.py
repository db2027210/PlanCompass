#!/usr/bin/env python3
"""Freeze v92 by adding TPC-H client-QPS semantics to v91."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PARENT_ROOT = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260903_v91"
)
OUTPUT_ROOT = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260903_v92"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def binding(relative: str) -> dict[str, str]:
    path = ROOT / relative
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"v92 source binding absent: {path}")
    return {"path": relative, "sha256": sha(path)}


def main() -> int:
    if OUTPUT_ROOT.exists() or OUTPUT_ROOT.is_symlink():
        raise RuntimeError(f"fresh v92 contract root required: {OUTPUT_ROOT}")
    from scripts import mysql_tpch_client_qps_measurement_v2 as tpch_metric

    parent = load(PARENT_ROOT / "algorithm_c_async_contract.json")
    parent.update({
        "created_at": datetime.now().astimezone().isoformat(),
        "release_version": "v92-client-primary",
        "tpch_outer_primary_metric": tpch_metric.METRIC,
        "tpch_server_questions_role": "audit_only",
        "tpch_qps_metric_contract": tpch_metric.contract(),
        "tpch_same_worker_client_qps_normalization_required": True,
    })
    parent["source_bindings"].update({
        "builder_v92": binding(
            "scripts/build_mysql_algorithm_c_four_scene_client_primary_v92.py"
        ),
        "controller_v92": binding(
            "scripts/run_mysql_algorithm_c_four_scene_client_primary_v3.py"
        ),
        "measurement_v92": binding(
            "scripts/mysql_algorithm_c_measurement_client_primary_v2.py"
        ),
        "tpch_client_qps_adapter": binding(
            "scripts/mysql_tpch_client_qps_measurement_v2.py"
        ),
        "tpch_worker_normalization": binding(
            "scripts/mysql_tpch_worker_qps_normalization_v1.py"
        ),
    })
    parent_path = OUTPUT_ROOT / "algorithm_c_async_contract.json"
    atomic(parent_path, parent)

    optimization = load(PARENT_ROOT / "outer_runtime_optimization.json")
    optimization.update({
        "created_at": datetime.now().astimezone().isoformat(),
        "release_version": "v92-client-primary",
        "tpch_outer_primary_metric": tpch_metric.METRIC,
        "tpch_same_worker_client_qps_normalization_required": True,
    })
    atomic(OUTPUT_ROOT / "outer_runtime_optimization.json", optimization)

    successor = load(PARENT_ROOT / "global_monotonic_incumbent_contract.json")
    successor.update({
        "created_at": datetime.now().astimezone().isoformat(),
        "release_version": "v92-client-primary",
        "parent_contract": {
            "path": str(parent_path),
            "sha256": sha(parent_path),
        },
        "tpch_outer_primary_metric": tpch_metric.METRIC,
        "tpch_server_questions_role": "audit_only",
        "tpch_same_worker_client_qps_normalization_required": True,
    })
    successor["source_bindings"].update(parent["source_bindings"])
    atomic(OUTPUT_ROOT / "global_monotonic_incumbent_contract.json", successor)
    print(json.dumps({
        "status": "PASS",
        "parent_sha256": sha(parent_path),
        "optimization_sha256": sha(OUTPUT_ROOT / "outer_runtime_optimization.json"),
        "successor_sha256": sha(
            OUTPUT_ROOT / "global_monotonic_incumbent_contract.json"
        ),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

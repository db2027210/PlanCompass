#!/usr/bin/env python3
"""Resume v156 while preserving its passed startup paper-baseline gate."""

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

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v67 as predecessor


runtime = predecessor.runtime
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_full_resume_20260923_v158/"
    "algorithm_c_full_resume_contract.json"
)
SOURCE_GATES = {
    "job_memory": ROOT / "history/mysql_four_scene_20260809/c_v156_four_scene_candidate_search_20260923_job/paper_baseline_resource_gate.json",
    "tpch_sf4_memory": ROOT / "history/mysql_four_scene_20260809/c_v156_four_scene_candidate_search_20260923_tpch/paper_baseline_resource_gate.json",
    "hybench_xp_cpu": ROOT / "history/mysql_four_scene_20260809/c_v156_four_scene_candidate_search_20260923_xp_cpu/paper_baseline_resource_gate.json",
    "hybench_xp_io": ROOT / "history/mysql_four_scene_20260809/c_v156_four_scene_candidate_search_20260923_xp_io/paper_baseline_resource_gate.json",
}


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
        value.get("schema_version") != "mysql_algorithm_c_full_resume_contract_v158"
        or value.get("release_version") != "v158-startup-baseline-lineage-resume"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("startup_paper_baseline_gate_is_campaign_invariant") is not True
        or value.get("post_outer_incumbent_is_not_startup_baseline") is not True
        or value.get("paper_baseline_tolerance_percentage_points") != 1.0
        or value.get("full_search_runs_until_runtime_terminal_condition") is not True
        or value.get("parent_contract", {}).get("sha256") != sha(predecessor.CONTRACT)
    ):
        raise RuntimeError("Algorithm C v158 resume contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v158 source differs: {name}: {path}")
    return value


def reuse_startup_paper_gate(
    *, incumbents: Mapping[str, Any], baseline_rows: Mapping[str, Any],
    output: Path,
) -> dict[str, Any]:
    scenes = list(incumbents)
    if len(scenes) != 1 or scenes[0] not in SOURCE_GATES:
        raise RuntimeError("single-scene v158 paper baseline lineage required")
    scene = scenes[0]
    if len(baseline_rows) != 2:
        raise RuntimeError("two semantic incumbent rows required on v158 resume")
    source_path = SOURCE_GATES[scene]
    source = load(source_path)
    resource_rows = dict(
        (source.get("scenes") or {}).get(scene)
        or source.get("resources") or {}
    )
    if (
        source.get("status") != "PASS"
        or source.get("candidate_search_authorized") is not True
        or float(source.get("tolerance_percentage_points") or 0) != 1.0
        or set(resource_rows) != {"cpu", "memory", "io"}
        or any(
            row.get("passed") is not True
            or abs(float(row.get("delta_percentage_points") or 0)) > 1.0
            for row in resource_rows.values()
        )
    ):
        raise RuntimeError(f"source startup paper baseline gate differs: {scene}")
    result = {
        **copy.deepcopy(source),
        "schema_version": "mysql_algorithm_c_reused_startup_paper_baseline_gate_v1",
        "source_gate_path": str(source_path),
        "source_gate_sha256": sha(source_path),
        "resume_after_completed_outer": 1,
        "startup_paper_baseline_gate_is_campaign_invariant": True,
        "post_outer_incumbent_is_not_startup_baseline": True,
        "physical_baseline_remeasurement_performed": False,
        "current_candidate_search_authorized": True,
    }
    runtime.alg.atomic(output, result)
    return result


def main() -> int:
    predecessor.validate_contract()
    validate_contract()
    predecessor.validate_resume_boundary()
    old = runtime.paper_baseline_gate.validate_and_write
    runtime.paper_baseline_gate.validate_and_write = reuse_startup_paper_gate
    try:
        return int(predecessor.main())
    finally:
        runtime.paper_baseline_gate.validate_and_write = old


if __name__ == "__main__":
    raise SystemExit(main())

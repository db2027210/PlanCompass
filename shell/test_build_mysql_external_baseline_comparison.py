#!/usr/bin/env python3
"""Self-tests for the MySQL external-baseline comparison builder."""

from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path
from typing import Any, Dict

import build_mysql_external_baseline_comparison as comparison


ACTIVE = ["cost_a", "cost_b", "cost_c", "cost_d"]
INACTIVE = ["cost_e", "cost_f", "cost_g", "cost_h"]


def write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def scene_authority(method: str, scene: str) -> Dict[str, Any]:
    return {
        "schema_version": "mysql_external_baseline_scene_authority_v1",
        "status": "complete",
        "method": method,
        "scene": scene,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "active_parameters": ACTIVE,
        "active_parameter_count": 4,
        "inactive_optimizer_costs_fixed_to_default": INACTIVE,
        "baseline": {"valid": True, "db_qps": 10.0, "resource_means": {}},
        "baseline_wave_count": 1,
        "baseline_tuned_or_updated": False,
        "candidate_budget": 2,
        "candidate_observations": [
            {"valid": True, "technical_failure": False},
            {"valid": False, "technical_failure": True},
        ],
        "frozen_champion": {"trial": 1, "params": dict.fromkeys(ACTIVE, 1.0)},
        "final_champion_validation": {
            "valid": True,
            "db_qps": 11.0,
            "relative_qps_improvement_pct": 10.0,
            "resource_means": {},
        },
        "clean_restore": {"status": "PASS"},
        "search_observations_are_not_final_results": True,
        "manual_mutation_after_start": False,
    }


def root_authority(method: str) -> Dict[str, Any]:
    return {
        "status": "complete",
        "method": method,
        "two_workers_per_scene": True,
        "four_scenes_run_in_parallel": True,
        "physical_workload_invocations": 16,
        "valid_replays": 12,
        "technical_failures": 4,
    }


def main() -> None:
    internal = comparison.load_internal_rows(
        comparison.load_json(comparison.DEFAULT_FORMAL_DATA)
    )
    assert len(internal) == 8
    with tempfile.TemporaryDirectory(prefix="mysql_external_comparison_test_") as raw:
        root = Path(raw)
        for method in comparison.EXTERNAL_METHODS:
            write_json(root / method / "authority_manifest.json", root_authority(method))
            for scene in comparison.SCENES:
                write_json(
                    root / method / scene / "authority_manifest.json",
                    scene_authority(method, scene),
                )
        external = comparison.load_external_rows(root)
        assert len(external) == 8
        assert all(
            math.isclose(
                row["relative_qps_improvement_pct"],
                10.0,
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
            for row in external
        )
        assert all(len(row["active_parameters"]) == 4 for row in external)
    print("PASS: MySQL external-baseline comparison builder", flush=True)


if __name__ == "__main__":
    main()

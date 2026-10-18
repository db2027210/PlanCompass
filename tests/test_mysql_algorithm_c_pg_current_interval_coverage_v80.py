import hashlib
import json
from pathlib import Path

from scripts import mysql_algorithm_c_global_coverage_v1 as coverage
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v35 as runtime
from scripts.run_mysql_algorithm_c_async_scene_v80 import validate_contract


ROOT = Path(__file__).resolve().parents[1]


def test_legacy_v5_was_restored_byte_exactly() -> None:
    path = ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v5.py"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == (
        "bda3c1cdc7955717502eff1ad9c870102e642f64cff3f7661a706d6ae3d8aac7"
    )


def test_current_interval_design_is_deterministic_unique_and_bounded() -> None:
    incumbent = {"p1": 2.0, "p2": 20.0, "p3": 200.0, "p4": 2000.0}
    ranges = {
        "p1": {"lower": 1.0, "upper": 4.0},
        "p2": {"lower": 10.0, "upper": 40.0},
        "p3": {"lower": 100.0, "upper": 400.0},
        "p4": {"lower": 1000.0, "upper": 4000.0},
    }
    first = coverage.deterministic_current_interval_vectors(
        incumbent, ranges, list(incumbent), sample_count=256,
    )
    second = coverage.deterministic_current_interval_vectors(
        incumbent, ranges, list(incumbent), sample_count=256,
    )
    assert first == second
    assert len(first) == 256
    assert len({json.dumps(row, sort_keys=True) for row in first}) == 256
    assert all(
        ranges[name]["lower"] <= row[name] <= ranges[name]["upper"]
        for row in first for name in incumbent
    )
    assert incumbent not in first


def test_runtime_binds_pg_coverage_before_second_rejection_shrink() -> None:
    source = Path(runtime.__file__).read_text(encoding="utf-8")
    coverage_position = source.index("current_interval_plan_coverage_audit.json")
    decision_position = source.index("for scene in active_scenes:", coverage_position)
    assert coverage_position < decision_position
    assert 'state["consecutive_failed_rounds"][scene]) != 1' in source
    assert '"qps_used_for_generation_or_ranking": False' in source
    assert '"external_algorithm_evidence_used": False' in source


def test_both_nested_paired_runtime_installers_are_rebound() -> None:
    controller = (
        ROOT / "scripts/run_mysql_algorithm_c_async_scene_v80.py"
    ).read_text(encoding="utf-8")
    assert "predecessor.paired_runtime = coverage_runtime" in controller
    assert "predecessor.predecessor.paired_runtime = coverage_runtime" in controller


def test_v80_contract_is_frozen_and_source_bound() -> None:
    contract = validate_contract()["successor"]
    policy = contract["current_interval_plan_coverage"]
    assert contract["release_version"] == "v80"
    assert policy["sample_count"] == 256
    assert policy["coverage_precedes_second_rejection_shrink"] is True
    assert policy["qps_used_for_generation_or_ranking"] is False
    assert policy["external_algorithm_evidence_used"] is False

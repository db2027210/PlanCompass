from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FREEZE = ROOT / "config/formal/frozen_mysql_pg_outer_20260819_v35"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_b_and_c_share_adaptive_measurement_contract() -> None:
    b = json.loads((FREEZE / "algorithm_b_fresh_contract.json").read_text())
    c = json.loads((FREEZE / "algorithm_c_optimization_contract.json").read_text())
    for contract in (b, c):
        measurement = contract["measurement_stability_contract"]
        assert measurement["minimum_measurement_seconds"] == 300
        assert measurement["xp_maximum_measurement_seconds"] == 900
        assert measurement["job_tpch_hard_timeout_seconds"] == 900
        assert measurement["job_tpch_full_sql_coverage_required"] is True
        assert measurement["target_resource_only"] is True
        assert measurement["qps_participates_in_stability"] is False
    assert b["pg_search_outer"]["post_search_stability_confirmation"] is False
    assert c["pg_search_outer"]["post_search_stability_confirmation"] is False


def test_all_v35_b_source_bindings_are_exact() -> None:
    contract = json.loads((FREEZE / "algorithm_b_fresh_contract.json").read_text())
    for binding in contract["source_bindings"].values():
        path = ROOT / binding["path"]
        assert path.is_file() and not path.is_symlink()
        assert sha(path) == binding["sha256"]


def test_c_runtime_and_wrapper_bindings_are_exact() -> None:
    contract = json.loads((FREEZE / "algorithm_c_optimization_contract.json").read_text())
    bindings = contract["bindings"]
    for prefix in (
        "algorithm_source", "adaptive_stability_source", "mysql_workload_adapter",
        "hybench_adaptive_java_source", "hybench_adaptive_runner_jar",
        "fresh_entrypoint", "fresh_launcher", "superseded_v32_campaign",
    ):
        path = ROOT / bindings[f"{prefix}_path"]
        assert path.is_file() and not path.is_symlink()
        assert sha(path) == bindings[f"{prefix}_sha256"]


def test_old_v32_campaign_is_explicitly_ineligible() -> None:
    invalidation = json.loads((
        ROOT / "history/mysql_four_scene_20260809"
        / "v32_formal_pg_outer_fresh_20260819_attempt029/SUPERSEDED.json"
    ).read_text())
    assert invalidation["status"] == "SUPERSEDED_INVALID_FOR_FORMAL_RESULTS"
    assert invalidation["formal_credit_allowed"] is False
    assert invalidation["accepted_round_credit_allowed"] is False
    assert invalidation["candidate_measurement_credit_allowed"] is False


def test_v33_and_v34_failures_are_bound_as_empirical_successor_evidence() -> None:
    b = json.loads((FREEZE / "algorithm_b_fresh_contract.json").read_text())
    basis = b["measurement_stability_contract"]["empirical_xp_maximum_basis"]
    assert basis["v33_attempt031_last_cv_at_570"] > 0.1
    assert basis["v33_attempt032_last_cv_at_570"] > 0.1
    assert basis["v34_attempt033_last_cv_at_660"] > 0.1
    assert basis["selected_hard_maximum_seconds"] == 900


def test_frozen_contract_sidecars_match() -> None:
    for name in (
        "algorithm_b_fresh_contract.json", "algorithm_c_optimization_contract.json",
        "coordination_contract.json", "serial_b_then_c_contract.json",
        "unified_algorithm_flow_contract.json",
    ):
        expected = (FREEZE / f"{name}.sha256").read_text().split()[0]
        assert expected == sha(FREEZE / name)

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FREEZE = ROOT / "config/formal/frozen_mysql_pg_outer_20260819_v33"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_b_and_c_share_adaptive_measurement_contract() -> None:
    b = json.loads((FREEZE / "algorithm_b_fresh_contract.json").read_text())
    c = json.loads((FREEZE / "algorithm_c_optimization_contract.json").read_text())
    for contract in (b, c):
        measurement = contract["measurement_stability_contract"]
        assert measurement["minimum_measurement_seconds"] == 300
        assert measurement["xp_maximum_measurement_seconds"] == 570
        assert measurement["job_tpch_hard_timeout_seconds"] == 900
        assert measurement["job_tpch_full_sql_coverage_required"] is True
        assert measurement["target_resource_only"] is True
        assert measurement["qps_participates_in_stability"] is False
    assert b["pg_search_outer"]["post_search_stability_confirmation"] is False
    assert c["pg_search_outer"]["post_search_stability_confirmation"] is False


def test_v33_runtime_bindings_are_superseded_by_v34() -> None:
    contract = json.loads((FREEZE / "algorithm_b_fresh_contract.json").read_text())
    binding = contract["source_bindings"]["adaptive_stability_gate"]
    assert sha(ROOT / binding["path"]) != binding["sha256"]
    successor = ROOT / "config/formal/frozen_mysql_pg_outer_20260819_v34"
    assert successor.is_dir()


def test_old_v32_campaign_is_explicitly_ineligible() -> None:
    invalidation = json.loads((
        ROOT / "history/mysql_four_scene_20260809"
        / "v32_formal_pg_outer_fresh_20260819_attempt029/SUPERSEDED.json"
    ).read_text())
    assert invalidation["status"] == "SUPERSEDED_INVALID_FOR_FORMAL_RESULTS"
    assert invalidation["formal_credit_allowed"] is False
    assert invalidation["accepted_round_credit_allowed"] is False
    assert invalidation["candidate_measurement_credit_allowed"] is False


def test_frozen_contract_sidecars_match() -> None:
    for name in (
        "algorithm_b_fresh_contract.json", "algorithm_c_optimization_contract.json",
        "coordination_contract.json", "serial_b_then_c_contract.json",
        "unified_algorithm_flow_contract.json",
    ):
        expected = (FREEZE / f"{name}.sha256").read_text().split()[0]
        assert expected == sha(FREEZE / name)

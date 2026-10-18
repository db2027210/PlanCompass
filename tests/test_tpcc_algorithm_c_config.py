import json
from pathlib import Path

import pytest

from mysql_algorithm_c_production_services import build
from mysql_parameter_optimizer import validate_config


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config/formal/mysql_sgd_production/tpcc_cpu.algorithm_c.optimizer.json"


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def test_tpcc_algorithm_c_config_contract() -> None:
    config = load_config()
    assert config["method_id"] == "experience_table_sgd"
    assert config["profile"] == "config/formal/mysql_sgd_production/profiles/tpcc_cpu.json"
    assert config["output"]["directory"] == "history/mysql_sgd_algorithm_c/tpcc_cpu"
    assert "algorithm_b" not in config["schema_version"]
    assert "algorithm_b" not in config["exp_id"]
    protocol = config["formal_outer_protocol"]
    assert protocol["formal_search_initial_replay_top_k"] == 5
    assert protocol["formal_search_max_replay_top_k"] == 5
    assert config["scoring"]["qps_role"] == "outer_acceptance_only"
    assert config["optimizer"]["candidate_generator"] == (
        "all_nonempty_subsets_experience_table_analytic_sgd_v1"
    )


def test_unified_algorithm_c_factory_constructs_without_running_campaign() -> None:
    bundle = build(load_config())
    assert set(bundle) >= {
        "registration", "dependencies", "r0_measurement", "campaign_cleanup"
    }


def test_validate_config_fails_closed_until_tpcc_bindings_are_authorized() -> None:
    with pytest.raises(ValueError, match="immutable|execution-authorized"):
        validate_config(load_config())

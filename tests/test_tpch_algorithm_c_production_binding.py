from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import py_compile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG = PROJECT_ROOT / "config/formal/mysql_sgd_production/tpch_memory.algorithm_c.optimizer.json"
BINDING = PROJECT_ROOT / "config/formal/mysql_sgd_production/tpch_memory.algorithm_c.factory_binding.json"
FACTORY = PROJECT_ROOT / "mysql_algorithm_b_production_services.py"


def _load_factory():
    spec = importlib.util.spec_from_file_location("tpch_production_services", FACTORY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tpch_c_static_contract() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    binding = json.loads(BINDING.read_text(encoding="utf-8"))
    assert config["method_id"] == "experience_table_sgd"
    assert config["profile"] == "config/formal/mysql_sgd_production/profiles/tpch_memory.json"
    assert config["output"]["directory"] == "history/mysql_sgd_algorithm_c/tpch_memory"
    assert "algorithm_b" not in config["schema_version"]
    assert "algorithm_b" not in config["exp_id"]
    assert config["formal_outer_protocol"]["formal_search_initial_replay_top_k"] == 5
    assert config["formal_outer_protocol"]["formal_search_max_replay_top_k"] == 5
    assert config["estimator"]["feature_source"]["require_strict_plan_registry"] is True
    assert binding["method_id"] == "experience_table_sgd"
    assert binding["data_binding"]["migration_status"] == "external_pending"


def test_tpch_factory_compiles_and_builds_without_starting() -> None:
    py_compile.compile(str(FACTORY), doraise=True)
    binding = json.loads(BINDING.read_text(encoding="utf-8"))
    assert hashlib.sha256(FACTORY.read_bytes()).hexdigest() == binding["factory_source_sha256"]
    module = _load_factory()
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    bundle = module.build(config)
    assert bundle["registration"]["id"] == "tpch_memory.experience_table_sgd.production_v1"
    owner = bundle["service_owner"]
    profile = json.loads((PROJECT_ROOT / config["profile"]).read_text(encoding="utf-8"))
    assert owner.lane.cpuset == profile["cpuset"]
    assert owner.lane.mems == profile["mems"]
    assert owner.lane.cpu_cores == profile["cpu_cores"]
    assert owner.lane.memory_bytes == profile["memory_bytes"]
    assert owner.lane.pids == profile["pids"]
    assert owner.lane.read_bps == profile["read_bps"]
    assert owner.lane.write_bps == profile["write_bps"]
    assert owner.lane.buffer_pool_bytes == profile["buffer_pool_bytes"]
    assert callable(bundle["r0_measurement"])
    assert callable(bundle["campaign_cleanup"])


def test_tpch_validate_config_stays_fail_closed_on_unbound_dynamic_registry() -> None:
    import sys

    sys.path.insert(0, str(PROJECT_ROOT))
    from mysql_parameter_optimizer import validate_config

    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    try:
        validate_config(config)
    except ValueError as exc:
        assert "lacks immutable or execution-authorized status" in str(exc)
    else:
        raise AssertionError("TPC-H dynamic registry must block formal validation")

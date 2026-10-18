from __future__ import annotations

import hashlib
import json
from pathlib import Path
import py_compile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG = PROJECT_ROOT / "config/formal/mysql_sgd_production/job_memory.algorithm_b.optimizer.json"
FACTORY = PROJECT_ROOT / "mysql_algorithm_b_production_services.py"
BINDING = PROJECT_ROOT / "config/formal/mysql_sgd_production/job_memory.production_factory_binding.json"


def test_job_binding_names_the_formal_factory_and_config() -> None:
    binding = json.loads(BINDING.read_text(encoding="utf-8"))
    assert binding["scenario"] == "job_memory"
    assert binding["benchmark"] == "job"
    assert binding["service_factory"] == "mysql_algorithm_b_production_services:build"
    assert binding["production_entry"] == "formal_mysql_sgd_campaign:run_production_campaign"
    assert binding["readiness_required"] is True
    assert binding["algorithm_contract"]["global_fixed_replay_top_k"] == 5
    assert binding["algorithm_contract"]["qps_role"] == "outer_acceptance_only"


def test_job_factory_source_compiles_and_binding_sha_matches() -> None:
    py_compile.compile(str(FACTORY), doraise=True)
    digest = hashlib.sha256(FACTORY.read_bytes()).hexdigest()
    binding = json.loads(BINDING.read_text(encoding="utf-8"))
    assert digest == binding["factory_source_sha256"]


def test_job_optimizer_config_is_fixed_top5_and_host02_bound() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert config["scenario"] == "job_memory"
    assert config["benchmark"] == "job"
    assert config["target_resource"] == "memory"
    outer = config["formal_outer_protocol"]
    assert outer["formal_search_initial_replay_top_k"] == 5
    assert outer["formal_search_max_replay_top_k"] == 5
    assert config["host02_job_xp_mutex"]["required"] is True
    assert config["workload_registry"]["immutable"] is True


def test_job_optimizer_config_passes_formal_loader() -> None:
    import sys

    sys.path.insert(0, str(PROJECT_ROOT))
    from mysql_parameter_optimizer import load_config

    config = load_config(CONFIG)
    assert config["scenario"] == "job_memory"
    assert config["benchmark"] == "job"
    assert config["target_resource"] == "memory"


def test_job_factory_build_contract_without_starting_services() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("job_production_services", FACTORY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    bundle = module.build(config)
    assert bundle["registration"]["id"] == "job_memory.algorithm_b.production_v1"
    deps = bundle["dependencies"]
    for name in (
        "lease_factory",
        "template_restore",
        "resource_fixed_readback",
        "cost_apply_fresh_readback",
        "drain_flush",
        "correctness",
        "plan_capture",
        "finalizer",
        "recovery",
        "root_cause_review",
    ):
        assert callable(getattr(deps, name))
    assert callable(deps.session_factories["job"])
    assert callable(bundle["r0_measurement"])
    assert callable(bundle["campaign_cleanup"])
    assert callable(bundle["host02_pressure_probe"])

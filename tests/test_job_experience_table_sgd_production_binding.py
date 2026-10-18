from __future__ import annotations

import hashlib
import json
from pathlib import Path
import py_compile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG = PROJECT_ROOT / "config/formal/mysql_sgd_production/job_memory.experience_table_sgd.optimizer.json"
BINDING = PROJECT_ROOT / "config/formal/mysql_sgd_production/job_memory.experience_table_sgd.factory_binding.json"
FACTORY = PROJECT_ROOT / "mysql_algorithm_b_production_services.py"


def test_job_c_binding_is_not_algorithm_b_or_grid() -> None:
    binding = json.loads(BINDING.read_text(encoding="utf-8"))
    assert binding["method"] == "experience_table_all_subset_sgd"
    assert binding["service_factory"] == "mysql_algorithm_b_production_services:build"
    assert binding["algorithm_contract"]["global_fixed_replay_top_k"] == 5
    assert binding["algorithm_contract"]["qps_role"] == "outer_acceptance_only"
    assert "mysql_double_loop_grid_runner.py" in binding["launch_gate"]["must_not_use"]
    assert "algorithm_b.optimizer.json" in binding["launch_gate"]["must_not_use"][1]


def test_job_c_config_binds_formal_method_t_table_and_template() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert config["method_id"] == "experience_table_sgd"
    assert config["profile"] == "config/formal/mysql_sgd_production/profiles/job_memory.json"
    assert config["method"] == "experience_table_all_subset_sgd"
    assert config["exp_id"] == "mysql_algorithm_c_job_memory_v3"
    assert config["output"]["directory"] == "history/mysql_sgd_algorithm_c/job_memory"
    assert "algorithm_b" not in config["schema_version"]
    assert "algorithm_b" not in config["exp_id"]
    assert config["method_contract"]["sha256"] == "ff224d59197789431cb04382e649ee451733a214113ab64386d8e504e5d9d96e"
    assert config["evidence"]["experience_table"]["sha256"] == "1bbcc4cc16ab785205ae1050320436a69ac0442256414fef8d16e2df6156a8f6"
    assert config["data_template"]["ready"] is True
    assert config["data_template"]["frozen_read_only"] is True


def test_job_c_factory_compiles_and_binding_sha_matches() -> None:
    py_compile.compile(str(FACTORY), doraise=True)
    binding = json.loads(BINDING.read_text(encoding="utf-8"))
    assert hashlib.sha256(FACTORY.read_bytes()).hexdigest() == binding["factory_source_sha256"]
    source = FACTORY.read_text(encoding="utf-8")
    assert "frozen production profile is missing" in source
    assert "frozen production profile is incomplete" in source
    assert "profile[\"cpuset\"]" in source
    assert "profile[\"buffer_pool_bytes\"]" in source
    assert binding["launch_gate"]["factory_profile_override_verified"] is True

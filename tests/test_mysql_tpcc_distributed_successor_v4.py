from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "shell/build_mysql_tpcc_distributed_successor_v4.py"


def builder():
    spec = importlib.util.spec_from_file_location("tpcc_v4_builder", BUILDER)
    assert spec is not None and spec.loader is not None
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_v4_is_deterministic_and_seals_client_sql_transport(tmp_path: Path) -> None:
    module = builder()
    first = module.build(tmp_path / "first")
    second = module.build(tmp_path / "second")
    assert first.name == second.name
    manifest = module.verify_published(first)
    assert manifest["schema_version"] == "mysql_tpcc_typed_distributed_successor_bundle_v4"
    assert manifest["formal_launch_authorized"] is False
    assert manifest["accepted_round_authorized"] is False
    assert manifest["tpcc_client_sql_transport_sha256"] == module.sha256(module.TPCC_CLIENT_SQL_TRANSPORT)
    assert module.BASE_ID in manifest["invalidated_bundle_ids"]
    runtime = json.loads((first / "distributed_runtime_contract.json").read_text())
    assert runtime["database_counter_transport"]["host_mysql_cli_prohibited"] is True
    assert runtime["database_counter_transport"]["mysql_binary_in_client"] == "/usr/bin/mysql"
    contract = json.loads((first / "qualification/qualification_execution_contract.json").read_text())
    assert contract["entrypoint"] == "qualification/mysql_tpcc_distributed_live_readback_qualification_v2.py"
    assert contract["tpcc_client_sql_transport"]["host_defaults_to_client_config_mount_mapping_required"] is True

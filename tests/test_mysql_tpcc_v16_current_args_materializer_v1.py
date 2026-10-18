from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest


HERE = Path(__file__).resolve()
ROOT = next(
    candidate for candidate in HERE.parents
    if (candidate / "artifacts/mysql_tpcc_prefault_successor_v16").is_dir()
)
SOURCE = (
    HERE.parents[1] / "materializer/mysql_tpcc_v16_current_args_materializer_v1.py"
    if (HERE.parents[1] / "materializer/mysql_tpcc_v16_current_args_materializer_v1.py").is_file()
    else ROOT / "shell/mysql_tpcc_v16_current_args_materializer_v1.py"
)
TPCC = ROOT / ("artifacts/mysql_tpcc_prefault_successor_v16/"
               "7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77")
TPCH = ROOT / ("artifacts/mysql_tpch_sql_native_percentile_successor_v1/"
               "ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4")
CORE = ROOT / ("artifacts/mysql_tpch_inventory_retry_support_v1/"
               "feb2e6ea9cd866a3fb82c5fbe9dea14a68cb2a6f73d97de0f4a615c410c8caff/"
               "qualification/mysql_distributed_live_readback_qualification_v1.py")


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


m = load("current_args_materializer", SOURCE)
core = load("current_args_collector_core", CORE)


def snapshot(now: float) -> dict:
    return {
        "schema_version": "mysql_tpcc_v16_same_live_readback_v1",
        "status": "PASS_READ_ONLY_LIVE_IDENTITY_NOT_CAPTURE_AUTHORITY",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "hostname": "node04",
        "collected_at_utc": datetime.fromtimestamp(now - 10, timezone.utc).isoformat(),
        "bundle_id": m.TPCC_ID,
        "attempt_id": m.TPCC_ATTEMPT,
        "database": {
            "container_id": "a" * 64, "process_start_ticks": 101,
            "cpuset_cpus": m.TPCC_DB_CPUSET, "device": "/dev/nvme3n1",
            "device_major_minor": "259:2", "mysql_port": 65106,
        },
        "client": {
            "container_id": "b" * 64, "process_start_ticks": 202,
            "cpuset_cpus": m.TPCC_CLIENT_CPUSET,
        },
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
    }


def test_snapshot_freshness_and_authority_boundary():
    now = 1_800_000_000.0
    database, client = m.validate_snapshot(snapshot(now), now)
    assert database["container_id"] == "a" * 64
    assert client["container_id"] == "b" * 64
    stale = snapshot(now)
    stale["collected_at_utc"] = datetime.fromtimestamp(now - 121, timezone.utc).isoformat()
    with pytest.raises(m.MaterializationError, match="stale"):
        m.validate_snapshot(stale, now)
    unauthorized = snapshot(now)
    unauthorized["accepted_round_authorized"] = True
    with pytest.raises(m.MaterializationError, match="authority boundary"):
        m.validate_snapshot(unauthorized, now)


def test_adopted_service_is_required_for_missing_snapshot_command_identity():
    now = 1_800_000_000.0
    database, client = m.validate_snapshot(snapshot(now), now)
    service = {
        "schema_version": "mysql_tpcc_node04_adopted_service_authority_v2",
        "status": "PASS", "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "bundle_id": m.TPCC_ID, "attempt_id": m.TPCC_ATTEMPT,
        "same_live_database_required": True, "container_recreated": False,
        "database_restarted": False, "datadir_replaced": False,
        "formal_launch_authorized": False, "accepted_round_authorized": False,
        "database": {"container_id": "a" * 64, "process_start_ticks": 101},
        "client": {"container_id": "b" * 64, "process_start_ticks": 202},
    }
    with pytest.raises(m.MaterializationError, match="immutable command binding"):
        m.validate_service(service, database, client)
    service["client"].update({
        "name": "current-tpcc-client", "image_id": "sha256:" + "c" * 64,
        "created": "2026-08-01T00:00:00Z", "inspect_config_sha256": "d" * 64,
    })
    assert m.validate_service(service, database, client)["name"] == "current-tpcc-client"


def test_authority_is_collector_bound_zero_credit_and_short_lived():
    command = {
        "bundle_id": m.TPCC_ID, "mode": "qualification_solo",
        "qualification_attempt_id": "x" * 40,
    }
    value = m.authority(command, "1" * 64, "2" * 64, m.COLLECTOR_MANIFEST_SHA,
                        1000.0, 1800)
    assert value["expires_epoch"] - value["not_before_epoch"] == 1800.0
    assert value["expires_epoch"] - 1000.0 == 1799.0
    assert value["collector_bundle_id"] == m.COLLECTOR_ID
    assert all(value[key] is False for key in m.FALSE_FLAGS)
    m.reject_static_pid(value)


def test_static_pid_and_token_reuse_guards(tmp_path: Path):
    with pytest.raises(m.MaterializationError, match="static PID"):
        m.reject_static_pid({"nested": {"client_pid": 123}})
    token = tmp_path / "token"
    token.write_text("z" * 48, encoding="ascii")
    os.chmod(token, 0o600)
    resolved, digest = m.token_digest(token, "fixture")
    assert resolved == token.resolve()
    assert digest == hashlib.sha256(("z" * 48).encode()).hexdigest()


def test_real_ff313_import_is_hash_bound_isolated_and_integrated():
    manifest = json.loads((TPCH / "bundle_manifest.json").read_text())
    module_path = TPCH / "mysql_tpch_distributed_v2_authority.py"
    helper_name = "mysql_tpch_client_immutable_identity_v1"
    original_path = list(sys.path)
    previous = sys.modules.get(helper_name)
    poisoned = types.ModuleType(helper_name)
    poisoned.__file__ = "/tmp/not-the-sealed-helper.py"
    sys.modules[helper_name] = poisoned
    try:
        module = m.load_module(module_path, manifest["files"])
        assert sys.path == original_path
        assert sys.modules[helper_name] is poisoned
        projection = module.client_container_immutable_projection({
            "Id": "a" * 64, "Name": "/client", "Image": "sha256:" + "b" * 64,
            "Config": {"Labels": {"role": "tpch"}},
            "HostConfig": {"SecurityOpt": ["no-new-privileges"], "CapDrop": ["ALL"]},
            "Mounts": [{"Type": "bind", "Source": "/source",
                        "Destination": "/destination", "RW": False}],
        })
        assert projection["id"] == "a" * 64
        assert projection["mounts"] == [{
            "type": "bind", "source": "/source",
            "destination": "/destination", "rw": False,
        }]
    finally:
        sys.path[:] = original_path
        if previous is None:
            sys.modules.pop(helper_name, None)
        else:
            sys.modules[helper_name] = previous


def test_ff313_import_rejects_unsealed_helper_hash(tmp_path: Path):
    manifest = json.loads((TPCH / "bundle_manifest.json").read_text())
    for basename in (
        "mysql_tpch_distributed_v2_authority.py",
        "mysql_tpch_client_immutable_identity_v1.py",
    ):
        (tmp_path / basename).write_bytes((TPCH / basename).read_bytes())
    (tmp_path / "mysql_tpch_client_immutable_identity_v1.py").write_text(
        "def client_container_immutable_projection(value): return value\n",
        encoding="utf-8",
    )
    with pytest.raises(m.MaterializationError, match="import source hash"):
        m.load_module(tmp_path / "mysql_tpch_distributed_v2_authority.py",
                      manifest["files"])


def test_tpcc_command_passes_final_shared_collector_static_validator(tmp_path: Path):
    manifest = json.loads((TPCC / "bundle_manifest.json").read_text())
    runtime = json.loads((TPCC / "distributed_runtime_contract.json").read_text())
    runtime = copy.deepcopy(runtime)
    jdk = tmp_path / "jdk"
    python = tmp_path / "python"
    config = tmp_path / "config"
    measurement = tmp_path / "measurement"
    for directory in (jdk / "bin", python, config, measurement):
        directory.mkdir(parents=True, exist_ok=True)
    java = jdk / "bin/java"
    java.write_bytes(b"fixture-java")
    defaults = config / "mysql.cnf"
    defaults.write_text("[client]\n", encoding="ascii")
    (config / "tpcc.xml").write_text("<parameters/>\n", encoding="ascii")
    runtime["client_runtime_mounts"]["jdk"]["source"] = str(jdk)
    runtime["client_runtime_mounts"]["python"]["source"] = str(python)
    runtime["toolchain"]["java_binary_sha256"] = m.sha256(java)
    client = {
        "name": "current-tpcc-client", "container_id": "b" * 64,
        "image_id": "sha256:" + "c" * 64, "created": "2026-08-01T00:00:00Z",
        "inspect_config_sha256": "d" * 64,
    }
    command = m.build_tpcc_command(
        TPCC, TPCC / "bundle_manifest.json", manifest, runtime, client, defaults,
        measurement, "qualification_solo", "fixture-" + "e" * 40, None)
    command_path = tmp_path / "command.json"
    m.write_sealed(command_path, command, 0o400)
    validated = core.validate_command_spec(
        command_path, m.TPCC_ID, "qualification_solo",
        runtime["client_resource_envelope"])
    assert validated["workload_metric_adapter"]["kind"] == "benchbase_tpcc"
    assert validated["paired_peer_workload"] is None
    m.reject_static_pid(validated)

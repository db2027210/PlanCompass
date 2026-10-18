from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import sys
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load(path: Path, stem: str):
    spec = importlib.util.spec_from_file_location(stem + uuid.uuid4().hex, path)
    module = importlib.util.module_from_spec(spec)
    old = list(sys.path); sys.path.insert(0, str(path.parent))
    try: spec.loader.exec_module(module)
    finally: sys.path[:] = old
    return module


@pytest.fixture(scope="session")
def collector():
    return load(ROOT / "launch/mysql_xp_fresh_r0_fingerprint_collector_v1.py", "xp_collector")


@pytest.fixture(scope="session")
def executor():
    return load(ROOT / "launch/mysql_xp_fresh_r0_executor_v2.py", "xp_executor")


def test_counter_identity_is_exact_and_has_no_caller_resource_input(collector):
    client = "a" * 64
    argv = collector.mysql_argv(client, collector.COUNTER_SQL)
    assert argv[:5] == ["docker", "--host", "unix:///run/ra-docker-mysql-xp-v2.sock",
                        "exec", "--user"]
    assert argv[5:8] == ["1008:1008", client, "/usr/bin/mysql"]
    assert "--defaults-extra-file=/run/mysql-xp-private/mysql-client.cnf" in argv
    assert "--port=65104" in argv
    assert argv[-2:] == ["--execute", collector.COUNTER_SQL]
    assert "qps" not in " ".join(argv).lower()
    with pytest.raises(collector.CollectorError, match="full client"):
        collector.mysql_argv("short", collector.COUNTER_SQL)


def test_cli_removes_caller_counter_fingerprint_and_client_inputs(executor):
    source = (ROOT / "launch/mysql_xp_fresh_r0_executor_v2.py").read_text()
    assert '"--counter-command"' not in source
    assert '"--fingerprint-observation"' not in source
    assert 'parser.add_argument("--client-container"' not in source
    assert "exact_adopted_client_docker_exec_stdout_v2" in source


def test_client_inspect_and_java_cgroup_are_exact(executor, monkeypatch):
    client = "b" * 64
    row = {"Id": client, "State": {"Running": True, "Pid": 2345},
           "HostConfig": {"CpuQuota": 1800000, "CpuPeriod": 100000,
                          "CpusetCpus": "3-11,39-47", "CpusetMems": "0",
                          "Memory": 8589934592, "MemorySwap": 8589934592,
                          "PidsLimit": 1024, "NetworkMode": "host",
                          "ReadonlyRootfs": True}}
    monkeypatch.setattr(executor, "docker", lambda *unused, **kwargs: json.dumps([row]))
    assert executor.inspect_client(client, 2345)["cpu_quota"] == 1800000
    paths = {name: "/docker/" + client for name in ("cpu", "cpuacct", "cpuset", "memory", "pids")}
    monkeypatch.setattr(executor, "cgroups", lambda unused: dict(paths))
    monkeypatch.setattr(executor, "status_lists", lambda unused: ("3-11,39-47", "0"))
    result = executor.verify_java_cgroup(2345, 3456)
    assert result["controllers"] == paths
    assert result["memory_swap_bytes"] == 8589934592
    row["HostConfig"]["CpusetMems"] = "1"
    with pytest.raises(executor.ExecutorError, match="resource envelope"):
        executor.inspect_client(client, 2345)


def _file(path: Path, value=b"x") -> Path:
    path.write_bytes(value); return path


def test_postbind_occurs_after_samples_and_rejects_forged_counter(
    tmp_path: Path, executor, collector,
):
    bundle = tmp_path / ("c" * 64); (bundle / "launch").mkdir(parents=True)
    producer = _file(bundle / "launch/mysql_xp_fresh_r0_fingerprint_collector_v1.py")
    adoption = _file(tmp_path / "adoption.json"); registry = _file(tmp_path / "registry.json")
    identity = {"client_container_id": "d" * 64}
    fingerprint = {"parameter_signature": "p", "l1": {"operator_types": ["table"]},
                   "l2": {"workers": 0},
                   "l3": {name: 0 for name in ("shared_read_blocks", "shared_write_blocks",
                       "local_read_blocks", "local_write_blocks", "temp_read_blocks",
                       "temp_write_blocks", "io_wait_ms")}}
    raw = {"schema_version": "mysql_xp_fresh_r0_raw_fingerprint_v1",
           "status": "PASS_CONCURRENT_RAW_OBSERVATION", "bundle_id": bundle.name,
           "producer_relative_path": "launch/mysql_xp_fresh_r0_fingerprint_collector_v1.py",
           "producer_sha256": executor.sha(producer),
           "adopted_service_sha256": executor.sha(adoption),
           "trace_registry_sha256": executor.sha(registry), "same_live_identity": identity,
           "client_container_id": identity["client_container_id"],
           "counter_sql": collector.COUNTER_SQL,
           "counter_sql_sha256": hashlib.sha256(collector.COUNTER_SQL.encode()).hexdigest(),
           "counter_transport": "exact_adopted_client_docker_exec_stdout_v2",
           "defaults_file_in_client": collector.DEFAULTS, "mysql_port": collector.PORT,
           "qps_resource_input_present": False, "fingerprint": fingerprint,
           "_sha256": "e" * 64}
    manifest = {"files": {"launch/mysql_xp_fresh_r0_fingerprint_collector_v1.py": executor.sha(producer)}}
    samples = [{"sequence": 0, "database_counters": {"x": 1}}]
    result = executor.validate_postbind(raw, bundle, manifest, adoption, registry,
                                        identity, samples, collector)
    assert result["counter_samples_sha256"] == executor.canonical_sha(samples)
    assert result["binding_order"] == "raw_observation_then_executor_sample_postbind"
    raw["counter_sql"] += " SELECT 1"
    with pytest.raises(executor.ExecutorError, match="fingerprint evidence"):
        executor.validate_postbind(raw, bundle, manifest, adoption, registry,
                                   identity, samples, collector)


def test_v25_manifest_is_immutable_zero_authority_and_binds_runner():
    manifest = json.loads((ROOT / "bundle_manifest.json").read_text())
    assert manifest["schema_version"] == "mysql_xp_node02_successor_bundle_v25"
    assert manifest["active_fresh_r0_executor"] == "launch/mysql_xp_fresh_r0_executor_v2.py"
    assert manifest["formal_launch_authorized"] is False
    assert manifest["accepted_round_authorized"] is False
    assert manifest["remote_actions"] == 0
    for relative in ("launch/mysql_xp_fresh_r0_executor_v2.py",
                     "launch/mysql_xp_fresh_r0_fingerprint_collector_v1.py"):
        assert manifest["files"][relative] == hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
    for path in (ROOT, *ROOT.rglob("*")):
        assert not path.is_symlink()
        assert stat.S_IMODE(path.stat().st_mode) & 0o222 == 0

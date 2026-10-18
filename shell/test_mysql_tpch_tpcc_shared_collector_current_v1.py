from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "shell/mysql_tpch_tpcc_shared_collector_current_v1/mysql_tpch_tpcc_shared_collector_entry_current_v1.py"
SPEC = importlib.util.spec_from_file_location("current_shared", ENTRY)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

TPCH = ROOT / "artifacts/mysql_tpch_sql_native_percentile_successor_v1/ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4"
TPCC = ROOT / "artifacts/mysql_tpcc_prefault_successor_v16/7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77"


def test_exact_current_targets_pass_and_cannot_be_interchanged():
    assert module.verify_current_target_seal(TPCH, "tpch_memory")[1]["scenario"] == "tpch_memory"
    assert module.verify_current_target_seal(TPCC, "tpcc_cpu")[1]["scenario"] == "tpcc_cpu"
    with pytest.raises(module.core.QualificationError):
        module.verify_current_target_seal(TPCC, "tpch_memory")
    with pytest.raises(module.core.QualificationError):
        module.verify_current_target_seal(TPCH, "tpcc_cpu")


def test_unsealed_old_or_unknown_bundle_fails_closed(tmp_path):
    old = tmp_path / ("0" * 64)
    old.mkdir()
    (old / "bundle_manifest.json").write_text("{}\n")
    (old / "distributed_runtime_contract.json").write_text("{}\n")
    with pytest.raises((module.core.QualificationError, OSError)):
        module.verify_current_target_seal(old)


@pytest.mark.parametrize("tamper", [
    {"database_pid": 1},
    {"nested": {"client_pid": 2}},
    {"argv": ["python", "--pid", "3"]},
])
def test_static_pid_tamper_fails_closed(tamper):
    with pytest.raises(module.core.QualificationError):
        module.reject_static_pid_authority(tamper)


def test_current_tpch_attempt_binding_rejects_stale_or_swapped_command():
    valid = {
        "client_container": {"name": module.CURRENT_TPCH_CLIENT_NAME, "container_id": "a" * 64},
        "measurement_output": {"host_tmpfs_directory": "/dev/shm/ra_mysql_tpch_v2/" + module.CURRENT_TPCH_ATTEMPT},
    }
    module.verify_current_tpch_command_binding(valid)
    stale = {**valid, "measurement_output": {"host_tmpfs_directory": "/dev/shm/ra_mysql_tpch_v2/stale"}}
    with pytest.raises(module.core.QualificationError):
        module.verify_current_tpch_command_binding(stale)


def test_shared_core_accepts_current_native_sql_completed_percentiles():
    text = (ENTRY.parent / "mysql_distributed_live_readback_qualification_v1.py").read_text()
    assert 'set(percentile_keys)' in text
    assert 'native SQL latency shape differs' in text
    assert ('native SQL workload requires ' + 'query duration') not in text


def test_no_obsolete_target_or_static_pid_literal_in_runtime_closure():
    source = ENTRY.parent
    forbidden = ("87ed" + "507", "4369" + "e9c", "tpcc-v4-" + "v13", "database_" + "pid\": 813" + "30")
    for path in source.glob("*.py"):
        text = path.read_text()
        assert not any(value in text for value in forbidden)

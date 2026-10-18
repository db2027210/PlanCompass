from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest


ROOT = Path(os.environ.get("XP_BUNDLE_UNDER_TEST", Path(__file__).resolve().parents[1]))
DEPLOYER = ROOT / "adoption/mysql_xp_compute00_daemon_adopt_v1.py"
STAT_FIXTURE = ROOT / "authority/mysql_xp_v11_socket_stat.stdout"
NET_FIXTURE = ROOT / "authority/mysql_xp_v10_proc_4149472_net_unix.stdout"
FAILURE = ROOT / "authority/mysql_xp_v11_socket_device_adoption_failure_20260803.json"
CONTRACT = ROOT / "authority/mysql_xp_compute00_socket_endpoint_contract_v3.json"
SCHEMA = "mysql_xp_compute00_daemon_adoption_successor_v12"
V11_ID = "59b782a4bcdba646635b36b9b5b1e9d349340ea62cbe2f28a9b8d8af68b4ae07"
V11_MANIFEST_SHA = "d0f05763f0e373f7641890eab05b66fc43b7f9f47ac5890dfea305cf88e16baa"
V11_ENTRYPOINT_SHA = "d521f870f3c379c86a98655d09540ef3beb4c6319ae1c149c5195ce4b412d288"
SOCKET = "/run/ra-docker-mysql-xp-v2.sock"


def load_deployer():
    spec = importlib.util.spec_from_file_location("xp_adopt_v12", DEPLOYER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode()


def test_manifest_closure_and_minimal_adoption_authority():
    manifest = json.loads((ROOT / "bundle_manifest.json").read_text())
    bundle_id = os.environ.get("XP_EXPECTED_BUNDLE_ID", ROOT.name)
    unsigned = dict(manifest)
    assert unsigned.pop("bundle_id") == bundle_id
    assert hashlib.sha256(canonical(unsigned)).hexdigest() == bundle_id
    if len(ROOT.name) == 64:
        assert ROOT.name == bundle_id
    assert manifest["schema_version"] == SCHEMA
    assert manifest["predecessor_bundle_id"] == V11_ID
    assert manifest["predecessor_entrypoint_sha256"] == V11_ENTRYPOINT_SHA
    assert manifest["v11_adoption_reuse_prohibited"] is True
    assert manifest["socket_device_python_st_dev_decimal"] == 25
    assert manifest["socket_device_gnu_stat_percent_D_hex"] == "19"
    assert manifest["v11_socket_endpoint_logic_unchanged"] is True
    assert manifest["daemon_adoption_authorized"] is True
    assert manifest["daemon_deployment_authorized"] is False
    assert manifest["systemd_run_authorized"] is False
    assert manifest["workload_launch_authorized"] is False
    assert manifest["accepted_rounds_credit"] == 0
    for relative, expected in manifest["files"].items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected
    mod = load_deployer()
    resolved, observed, _ = mod.verify_bundle(ROOT, bundle_id)
    assert resolved == ROOT.resolve() and observed["bundle_id"] == bundle_id


def test_v11_one_shot_failure_is_sealed_zero_credit_no_mutation():
    failure = json.loads(FAILURE.read_text())
    assert failure["predecessor_bundle_id"] == V11_ID
    assert failure["predecessor_manifest_sha256"] == V11_MANIFEST_SHA
    assert failure["observed_error"] == "adopted filesystem socket identity differs"
    assert failure["systemd_mutation"] is False
    assert failure["daemon_process_mutation"] is False
    assert failure["postgresql_mutation"] is False
    assert failure["deployment_receipt_created"] is False
    assert failure["container_count"] == 0
    assert failure["workload_started"] is False
    assert failure["accepted_rounds_credit"] == 0
    assert failure["authorization_reuse_prohibited"] is True


def test_gnu_stat_decimal_and_hex_device_conventions_are_distinct_and_exact():
    fields = STAT_FIXTURE.read_text().strip().split()
    assert fields == ["25", "19", "2915", "660", "0", "0", "socket"]
    decimal_device = int(fields[0], 10)
    percent_D_hex_device = int(fields[1], 16)
    assert decimal_device == 25
    assert percent_D_hex_device == 25
    assert format(decimal_device, "x") == fields[1] == "19"
    assert int(fields[1], 10) == 19  # The exact v11 category error.


def test_runtime_constants_use_python_decimal_and_record_gnu_hex_separately():
    mod = load_deployer()
    assert mod.ADOPTED_SOCKET_FILESYSTEM_DEVICE == 25
    assert mod.ADOPTED_SOCKET_FILESYSTEM_DEVICE_HEX == "19"
    assert mod.ADOPTED_SOCKET_FILESYSTEM_INODE == 2915
    assert mod.ADOPTED_SOCKET_ENDPOINT_INODE == 221659057
    source = DEPLOYER.read_text()
    assert 'format(state.st_dev, "x")' in source
    assert '"device_decimal": state.st_dev' in source
    assert '"device_hex": format(state.st_dev, "x")' in source


def test_v12_contract_preserves_v11_endpoint_and_corrects_only_device_notation():
    contract = json.loads(CONTRACT.read_text())
    assert contract["socket_path"] == SOCKET
    assert contract["python_st_dev_decimal"] == 25
    assert contract["gnu_stat_percent_d_decimal"] == 25
    assert contract["gnu_stat_percent_D_hex"] == "19"
    assert contract["filesystem_socket_inode"] == 2915
    assert contract["kernel_endpoint_inode"] == 221659057
    assert contract["observed_owner_fd"] == 3
    assert contract["v11_socket_endpoint_logic_unchanged"] is True
    assert contract["filesystem_inode_must_not_be_compared_to_endpoint_inode"] is True


def test_real_v11_endpoint_parser_result_is_unchanged():
    mod = load_deployer()
    endpoint = mod.parse_proc_net_unix(NET_FIXTURE.read_text())
    assert endpoint["path"] == SOCKET
    assert endpoint["kernel_endpoint_inode"] == 221659057
    assert endpoint["flags"] == 0x10000
    assert endpoint["type"] == 1 and endpoint["state"] == 1
    assert mod.endpoint_owner_fds({
        "3": "socket:[221659057]",
        "8": "socket:[221659069]",
        "9": "socket:[221659073]",
    }, endpoint["kernel_endpoint_inode"]) == [3]


def test_v11_endpoint_rejection_gates_remain():
    mod = load_deployer()
    raw = NET_FIXTURE.read_text()
    row = raw.splitlines()[1]
    with pytest.raises(mod.DeploymentError, match="duplicate"):
        mod.parse_proc_net_unix(raw + row + "\n")
    with pytest.raises(mod.DeploymentError, match="escape"):
        mod.parse_proc_net_unix(raw.replace(
            SOCKET, "/run/ignored/../ra-docker-mysql-xp-v2.sock"))
    with pytest.raises(mod.DeploymentError, match="LISTEN"):
        mod.parse_proc_net_unix(raw.replace("00010000", "00000000"))
    with pytest.raises(mod.DeploymentError, match="not owned"):
        mod.endpoint_owner_fds({"3": "socket:[221659057]"}, 999)


def test_source_and_runbook_are_strict_adoption_only():
    mod = load_deployer()
    runbook = (ROOT / "operator/mysql_xp_compute00_daemon_adoption_v12.md").read_text().lower()
    assert mod.ADOPT_EXISTING_ONLY is True
    assert mod.ADOPTED_MAIN_PID == 4149472
    assert mod.ADOPTED_STARTTICKS == 37998304
    assert "systemd-run" not in runbook
    assert "workload launch is prohibited" in runbook
    assert "never invoke v11" in runbook

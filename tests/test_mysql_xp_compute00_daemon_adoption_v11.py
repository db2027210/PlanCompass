from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest


ROOT = Path(os.environ.get("XP_BUNDLE_UNDER_TEST", Path(__file__).resolve().parents[1]))
DEPLOYER = ROOT / "adoption/mysql_xp_compute00_daemon_adopt_v1.py"
FIXTURE = ROOT / "authority/mysql_xp_v10_proc_4149472_net_unix.stdout"
FAILURE = ROOT / "authority/mysql_xp_v10_socket_inode_adoption_failure_20260803.json"
CONTRACT = ROOT / "authority/mysql_xp_compute00_socket_endpoint_contract_v2.json"
DAEMON_CONTRACT = ROOT / "authority/mysql_xp_compute00_daemon_deployment_contract_v1.json"
SCHEMA = "mysql_xp_compute00_daemon_adoption_successor_v11"
V10_ID = "671cbb6f98c9b223f46ad7f5499311c6aebd4a87ee7f7ce1e9fcca5169780b96"
V10_MANIFEST_SHA = "2fa6a0a5839006619e51cce8babb78bd5f6fb7971dbdabb1aee6aa082d38de23"
DAEMON_CONTRACT_SHA = "7fdb1c24e59743e81b70496dadcb1f5ea7f287b2a347ac2aa0f269fa5ce8afa3"
SOCKET = "/run/ra-docker-mysql-xp-v2.sock"


def load_deployer():
    spec = importlib.util.spec_from_file_location("xp_adopt_v11", DEPLOYER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode()


def test_manifest_closure_and_adoption_only_authority():
    manifest = json.loads((ROOT / "bundle_manifest.json").read_text())
    bundle_id = os.environ.get("XP_EXPECTED_BUNDLE_ID", ROOT.name)
    unsigned = dict(manifest)
    assert unsigned.pop("bundle_id") == bundle_id
    assert hashlib.sha256(canonical(unsigned)).hexdigest() == bundle_id
    if len(ROOT.name) == 64:
        assert ROOT.name == bundle_id
    assert manifest["schema_version"] == SCHEMA
    assert manifest["predecessor_bundle_id"] == V10_ID
    assert manifest["v10_adoption_reuse_prohibited"] is True
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


def test_v10_one_shot_failure_is_sealed_zero_credit_no_mutation():
    failure = json.loads(FAILURE.read_text())
    assert failure["predecessor_bundle_id"] == V10_ID
    assert failure["predecessor_manifest_sha256"] == V10_MANIFEST_SHA
    assert failure["observed_error"] == "dedicated socket is not owned by MainPID"
    assert failure["root_diagnosis"].startswith("filesystem socket inode")
    assert failure["systemd_mutation"] is False
    assert failure["deployment_receipt_created"] is False
    assert failure["container_count"] == 0
    assert failure["workload_started"] is False
    assert failure["accepted_rounds_credit"] == 0
    assert failure["authorization_reuse_prohibited"] is True
    assert failure["socket_filesystem"]["inode"] == 2915
    assert failure["socket_endpoint_inode"] == 221659057
    assert failure["socket_owner_fd"] == 3


def test_socket_endpoint_contract_and_fixture_are_exact():
    contract = json.loads(CONTRACT.read_text())
    assert contract["socket_path"] == SOCKET
    assert contract["filesystem_socket_inode"] == 2915
    assert contract["filesystem_socket_device"] == 19
    assert contract["kernel_endpoint_inode"] == 221659057
    assert contract["observed_owner_fd"] == 3
    assert contract["filesystem_inode_must_not_be_compared_to_endpoint_inode"] is True
    assert hashlib.sha256(DAEMON_CONTRACT.read_bytes()).hexdigest() == DAEMON_CONTRACT_SHA
    assert FIXTURE.read_text() == (
        "Num       RefCount Protocol Flags    Type St Inode Path\n"
        "ffff9eadc4f7d500: 00000002 00000000 00010000 0001 01 221659057 "
        "/run/ra-docker-mysql-xp-v2.sock\n"
    )


def test_real_proc_net_unix_fixture_resolves_unique_listen_endpoint():
    mod = load_deployer()
    observed = mod.parse_proc_net_unix(FIXTURE.read_text())
    assert observed == {
        "path": SOCKET,
        "kernel_endpoint_inode": 221659057,
        "ref_count": 2,
        "protocol": 0,
        "flags": 0x10000,
        "type": 1,
        "state": 1,
    }


def test_proc_net_unix_rejects_duplicate_exact_path():
    mod = load_deployer()
    raw = FIXTURE.read_text()
    row = raw.splitlines()[1]
    with pytest.raises(mod.DeploymentError, match="duplicate"):
        mod.parse_proc_net_unix(raw + row + "\n")


def test_proc_net_unix_rejects_lexical_path_escape():
    mod = load_deployer()
    escaped = FIXTURE.read_text().replace(
        SOCKET, "/run/ignored/../ra-docker-mysql-xp-v2.sock")
    with pytest.raises(mod.DeploymentError, match="escape"):
        mod.parse_proc_net_unix(escaped)


@pytest.mark.parametrize("replacement", [
    "00000000 0001 01 221659057",
    "00010000 0002 01 221659057",
    "00010000 0001 02 221659057",
])
def test_proc_net_unix_rejects_non_listen_endpoint(replacement):
    mod = load_deployer()
    changed = FIXTURE.read_text().replace(
        "00010000 0001 01 221659057", replacement)
    with pytest.raises(mod.DeploymentError, match="LISTEN"):
        mod.parse_proc_net_unix(changed)


@pytest.mark.parametrize("raw", [
    "bad header\n",
    "Num RefCount Protocol Flags Type St Inode Path\nnot-a-row\n",
    "Num RefCount Protocol Flags Type St Inode Path\n"
    "ffff: 00000002 00000000 00010000 0001 01 not_decimal " + SOCKET + "\n",
])
def test_proc_net_unix_rejects_malformed_inventory(raw):
    mod = load_deployer()
    with pytest.raises(mod.DeploymentError, match="malformed"):
        mod.parse_proc_net_unix(raw)


def test_proc_net_unix_rejects_missing_path():
    mod = load_deployer()
    changed = FIXTURE.read_text().replace(SOCKET, "/run/unrelated.sock")
    with pytest.raises(mod.DeploymentError, match="missing"):
        mod.parse_proc_net_unix(changed)


def test_fd_endpoint_parser_requires_kernel_inode_among_exact_pid_fds():
    mod = load_deployer()
    targets = {
        "3": "socket:[221659057]",
        "8": "socket:[221659069]",
        "9": "socket:[221659073]",
        "10": "/dev/null",
    }
    assert mod.endpoint_owner_fds(targets, 221659057) == [3]
    with pytest.raises(mod.DeploymentError, match="not owned"):
        mod.endpoint_owner_fds(targets, 999)
    targets["11"] = "socket:[malformed]"
    with pytest.raises(mod.DeploymentError, match="malformed"):
        mod.endpoint_owner_fds(targets, 221659057)


def test_source_and_runbook_are_adoption_only():
    mod = load_deployer()
    source = DEPLOYER.read_text().lower()
    runbook = (ROOT / "operator/mysql_xp_compute00_daemon_adoption_v11.md").read_text().lower()
    assert mod.ADOPT_EXISTING_ONLY is True
    assert mod.ADOPTED_MAIN_PID == 4149472
    assert mod.ADOPTED_STARTTICKS == 37998304
    assert mod.ADOPTED_SOCKET_FILESYSTEM_INODE == 2915
    assert mod.ADOPTED_SOCKET_ENDPOINT_INODE == 221659057
    assert "filesystem socket inode" in source
    assert "kernel_endpoint_inode" in source
    assert "systemd-run" not in runbook
    assert "workload launch is prohibited" in runbook
    assert "never invoke v10" in runbook

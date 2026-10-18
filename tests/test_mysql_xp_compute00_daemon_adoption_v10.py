from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest


ROOT = Path(os.environ.get("XP_BUNDLE_UNDER_TEST", Path(__file__).resolve().parents[1]))
DEPLOYER = ROOT / "adoption/mysql_xp_compute00_daemon_adopt_v1.py"
CONTRACT = ROOT / "authority/mysql_xp_compute00_daemon_deployment_contract_v1.json"
ADOPTION = ROOT / "authority/mysql_xp_compute00_daemon_adoption_contract_v1.json"
FAILURE = ROOT / "authority/mysql_xp_v9_partial_daemon_deployment_20260803.json"
FIXTURE = ROOT / "authority/mysql_xp_v9_systemd219_active.stdout"
SCHEMA = "mysql_xp_compute00_daemon_adoption_successor_v10"
V9_ID = "24d1324d264688fc8d4a737126b6a729b499688063ecf4cc22e24431833362ee"
V9_MANIFEST_SHA = "26fc54fb0bb856703280ae666902473d2621bb27cad72b7497e98affff9dd484"
CONTRACT_SHA = "7fdb1c24e59743e81b70496dadcb1f5ea7f287b2a347ac2aa0f269fa5ce8afa3"


def load_deployer():
    spec = importlib.util.spec_from_file_location("xp_adopt_v10", DEPLOYER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode()


def test_manifest_closure_and_actual_deployer_verify_bundle():
    manifest = json.loads((ROOT / "bundle_manifest.json").read_text())
    bundle_id = os.environ.get("XP_EXPECTED_BUNDLE_ID", ROOT.name)
    unsigned = dict(manifest); assert unsigned.pop("bundle_id") == bundle_id
    assert hashlib.sha256(canonical(unsigned)).hexdigest() == bundle_id
    if len(ROOT.name) == 64:
        assert ROOT.name == bundle_id
    assert manifest["schema_version"] == SCHEMA
    assert manifest["daemon_adoption_authorized"] is True
    assert manifest["daemon_deployment_authorized"] is False
    assert manifest["systemd_run_authorized"] is False
    assert manifest["workload_launch_authorized"] is False
    assert manifest["formal_launch_authorized"] is False
    assert manifest["accepted_round_authorized"] is False
    assert manifest["accepted_rounds_credit"] == 0
    for relative, expected in manifest["files"].items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected
    mod = load_deployer(); resolved, observed, _ = mod.verify_bundle(ROOT, bundle_id)
    assert resolved == ROOT.resolve() and observed["bundle_id"] == bundle_id


def test_v9_partial_failure_and_adoption_identity_are_sealed():
    failure = json.loads(FAILURE.read_text())
    adoption = json.loads(ADOPTION.read_text())
    assert failure["predecessor_bundle_id"] == V9_ID
    assert failure["systemd_mutation"] is True
    assert failure["unit_created"] is True and failure["daemon_started"] is True
    assert failure["main_pid"] == 4149472
    assert failure["process_startticks"] == 37998304
    assert failure["cmdline_raw_sha256"] == "eb036eba455e55f13d26da8355e7aa3f6c590d24fc2e95ba2e497ab8737ced27"
    assert failure["container_count"] == 0 and failure["workload_started"] is False
    assert failure["deployment_receipt_created"] is False
    assert failure["accepted_rounds_credit"] == 0
    assert adoption["adopted_bundle_id"] == V9_ID
    assert adoption["adopted_bundle_manifest_sha256"] == V9_MANIFEST_SHA
    assert adoption["main_pid"] == 4149472
    assert adoption["process_startticks"] == 37998304
    assert adoption["systemd_run_prohibited"] is True
    assert adoption["empty_container_inventory_required"] is True


def test_daemon_envelope_contract_is_byte_identical():
    assert hashlib.sha256(CONTRACT.read_bytes()).hexdigest() == CONTRACT_SHA
    contract = json.loads(CONTRACT.read_text())
    assert contract["daemon_cpuset"] == "192-193,448-449"
    assert contract["data_major_minor"] == "259:6"
    assert contract["socket"] == "/run/ra-docker-mysql-xp-v2.sock"
    assert contract["postgresql_cpu_overlap"] == []
    assert contract["workload_launch_authorized"] is False


def test_exact_active_fixture_normalizes_exactly_five_missing_hooks():
    mod = load_deployer(); raw = FIXTURE.read_text(encoding="utf-8")
    parsed = mod.parse_show(raw)
    assert parsed == mod.ADOPTED_ACTIVE_SYSTEMD_EXPECTED
    observed = {line.split("=", 1)[0] for line in raw.splitlines()}
    assert "ExecStart" in observed
    assert set(mod.OPTIONAL_EMPTY_EXEC_PROPERTIES_WHEN_ACTIVE).isdisjoint(observed)
    assert all(parsed[key] == "" for key in mod.OPTIONAL_EMPTY_EXEC_PROPERTIES_WHEN_ACTIVE)
    assert parsed["MainPID"] == "4149472"
    assert parsed["LoadState"] == "loaded"
    assert parsed["ActiveState"] == "active"
    assert parsed["SubState"] == "running"


def test_active_inventory_rejects_missing_execstart_or_other_field_and_extra_hook():
    mod = load_deployer(); lines = FIXTURE.read_text().splitlines()
    for key in ("ExecStart", "ControlGroup", "TasksMax", "FragmentPath"):
        changed = "\n".join(line for line in lines if not line.startswith(key + "=")) + "\n"
        with pytest.raises(mod.DeploymentError, match="inventory differs"):
            mod.parse_show(changed)
    changed = FIXTURE.read_text() + "ExecStartPre=\n"
    with pytest.raises(mod.DeploymentError, match="inventory differs"):
        mod.parse_show(changed)
    with pytest.raises(mod.DeploymentError, match="duplicate"):
        mod.parse_show(FIXTURE.read_text() + "MainPID=4149472\n")


def test_exact_adopted_execstart_and_live_argv_are_v9_bound():
    mod = load_deployer(); config = Path(mod.ADOPTED_CONFIG_PATH)
    assert V9_ID in str(config)
    parsed = mod.parse_show(FIXTURE.read_text())
    assert mod.parse_execstart(parsed["ExecStart"]) == mod.unit_exec_argv(config)
    argv = mod.dockerd_argv(config)
    assert argv[0] == "/usr/bin/dockerd"
    assert argv[1] == "--config-file=" + str(config)
    assert "--host=unix:///run/ra-docker-mysql-xp-v2.sock" in argv
    assert not any("postgres" in value.lower() or "/mnt/nvme2n1" in value for value in argv)
    mod.check_property_identity(parsed)


def test_adoption_only_rejects_every_systemd_run_mutation():
    mod = load_deployer(); config = Path(mod.ADOPTED_CONFIG_PATH)
    assert mod.ADOPT_EXISTING_ONLY is True
    with pytest.raises(mod.DeploymentError, match="prohibited"):
        mod.validate_command(mod.systemd_run_argv(config), mutation=True)
    with pytest.raises(mod.DeploymentError):
        mod.validate_command([mod.SYSTEMCTL, "start", mod.UNIT])


def test_create_action_cannot_enter_adoption(monkeypatch, tmp_path):
    mod = load_deployer()
    config = Path(mod.ADOPTED_CONFIG_PATH)
    monkeypatch.setattr(mod, "verify_bundle", lambda *_args: (ROOT, {}, config))
    monkeypatch.setattr(mod, "verify_adopted_config", lambda: config)
    monkeypatch.setattr(mod, "preflight", lambda _config: {
        "action": "CREATE_EXACT_TRANSIENT_UNIT",
        "postgresql_before": [],
    })
    calls = []
    monkeypatch.setattr(mod, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    with pytest.raises(mod.DeploymentError, match="cannot create"):
        mod.deploy(ROOT, ROOT.name, tmp_path / "receipt.json")
    assert calls == []


def test_adoption_receipt_carries_no_deployment_or_systemd_run_authority(
        monkeypatch, tmp_path):
    mod = load_deployer()
    config = Path(mod.ADOPTED_CONFIG_PATH)
    monkeypatch.setattr(mod, "verify_bundle", lambda *_args: (ROOT, {}, config))
    monkeypatch.setattr(mod, "verify_adopted_config", lambda: config)
    monkeypatch.setattr(mod, "preflight", lambda _config: {
        "action": "ADOPT_EXACT_EXISTING_ONLY",
        "postgresql_before": [],
    })
    monkeypatch.setattr(mod, "live_readback", lambda _config: {
        "process_startticks": mod.ADOPTED_STARTTICKS,
    })
    monkeypatch.setattr(mod, "pg_inventory", lambda: [])
    published = []
    monkeypatch.setattr(mod, "atomic_receipt",
                        lambda path, receipt: published.append((path, receipt)) or "TEST")
    receipt, mode = mod.deploy(ROOT, ROOT.name, tmp_path / "receipt.json")
    assert mode == "TEST" and published[0][1] is receipt
    assert receipt["status"] == "PASS_ADOPTED_EXISTING_XP_DAEMON_ONLY"
    assert receipt["daemon_deployment_authorized"] is False
    assert receipt["daemon_adoption_authorized"] is True
    assert receipt["systemd_run_authorized"] is False
    assert receipt["disposition"] == "ADOPTED_EXACT_EXISTING_TRANSIENT_UNIT"


def test_source_and_runbook_have_no_workload_or_lifecycle_authority():
    source = DEPLOYER.read_text()
    assert 'ADOPT_EXISTING_ONLY = True' in source
    assert 'daemon_adoption_authorized' in source
    assert "it may create and start" not in source.lower()
    runbook = (ROOT / "operator/mysql_xp_compute00_daemon_adoption_v10.md").read_text()
    assert "--authorize-exact-bundle-id" in runbook
    assert "adoption/mysql_xp_compute00_daemon_adopt_v1.py" in runbook
    assert "systemctl start" not in runbook
    assert "systemd-run" not in runbook.lower()
    assert "workload launch is prohibited" in runbook.lower()

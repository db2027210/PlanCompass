from __future__ import annotations

from datetime import datetime
import importlib.util
import json
from pathlib import Path
import shutil
import stat
import sys
import uuid

import pytest


CHECKOUT_ROOT = Path(__file__).resolve().parents[1]
V19_ID = "e91031f5af956af87904a240fcf6ce6673d2511dfc66f1a6337f36698133050d"


def load_module(path: Path, stem: str):
    name = "%s_%s" % (stem, uuid.uuid4().hex)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    old_path = list(sys.path)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = old_path
    return module


@pytest.fixture(scope="session")
def bundles():
    if (CHECKOUT_ROOT / "bundle_manifest.json").is_file():
        return None, CHECKOUT_ROOT
    builder = load_module(
        CHECKOUT_ROOT / "shell/build_mysql_xp_node02_materials_successor_v20.py",
        "xp_v20_builder",
    )
    v20 = builder.build()
    v19 = CHECKOUT_ROOT / "artifacts/mysql_xp_node02_successor_v19" / V19_ID
    return v19, v20


@pytest.fixture()
def materials(bundles):
    unused_v19, v20 = bundles
    return load_module(
        v20 / "test_support/mysql_xp_qualification_bootstrap_materials_v5.py",
        "xp_v20_materials",
    )


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_v19_and_v20_strict_identities_are_accepted(bundles, materials):
    v19, v20 = bundles
    if v19 is not None:
        unused_path, v19_manifest, unused_contract = materials.verified_bundle(v19)
        assert v19_manifest["bundle_id"] == V19_ID
        assert v19_manifest["schema_version"].endswith("_v19")
    unused_path, v20_manifest, unused_contract = materials.verified_bundle(v20)
    assert v20_manifest["bundle_id"] == v20.name
    assert v20_manifest["schema_version"].endswith("_v20")
    assert v20_manifest["predecessor_bundle_id"] == V19_ID
    assert v20_manifest["formal_scope"] == "mysql_only"
    assert v20_manifest["cross_engine_comparison_prohibited"] is True


def test_unmanifested_file_fails_closed(tmp_path, bundles, materials):
    unused_v19, v20 = bundles
    copied = tmp_path / v20.name
    shutil.copytree(v20, copied)
    copied.chmod(0o755)
    extra = copied / "unmanifested"
    extra.write_text("prohibited\n", encoding="utf-8")
    with pytest.raises(materials.MaterialError, match="exact file closure"):
        materials.verified_bundle(copied)


def test_short_ttl_attempt_has_complete_zero_credit_plan(
    tmp_path, bundles, materials
):
    unused_v19, v20 = bundles
    result = materials.create_materials(v20, tmp_path / "materials", 10)
    output = Path(result["materials_directory"])
    spec = load_json(output / "bootstrap_spec.json")
    plan = load_json(output / "command_plan.json")
    install = load_json(output / "authorization_install_plan.json")
    manifest = load_json(output / "materials_manifest.json")

    created = datetime.fromisoformat(manifest["created_at_utc"])
    expires = datetime.fromisoformat(manifest["expires_at_utc"])
    assert (expires - created).total_seconds() == 600
    assert spec["bundle"]["id"] == v20.name
    assert result["bundle_id"] == v20.name
    assert result["formal_r0_authorized"] is False
    assert result["accepted_round_authorized"] is False
    for value in (spec, plan, install, manifest):
        assert value["formal_scope"] == "mysql_only"
        assert value["cross_engine_comparison_prohibited"] is True
        assert value["postgresql_mutation_prohibited"] is True
        assert value.get("formal_r0_authorized", False) is False
        assert value.get("accepted_round_authorized", False) is False

    steps = [row["step"] for row in install["strict_install_steps"]]
    assert steps == list(range(1, 8))
    commands = plan["host_systemd_run_commands_after_separate_authorization"]
    polls = plan["controller_poll_after_each_systemd_run"]
    assert set(commands) == {"physical_copy", "plan_only", "bootstrap", "adopt_readback"}
    assert set(polls) == set(commands)
    for argv in commands.values():
        assert argv[0] == "systemd-run"
        assert "--service-type=oneshot" in argv
        assert "--remain-after-exit" in argv
        assert "--wait" not in argv
        assert "--collect" not in argv
    assert plan["main_agent_host_root_executor_required_for"] == [
        "physical_copy", "bootstrap",
    ]
    assert plan["fresh_attempt_root_gate"]["reuse_after_any_failed_step_prohibited"] is True
    assert install["status"] == "PLANNED_NOT_INSTALLED"

    for relative in (
        "bootstrap_spec.json", "command_plan.json",
        "authorization_install_plan.json", "materials_manifest.json",
    ):
        path = output / relative
        assert stat.S_IMODE(path.stat().st_mode) == 0o444
        assert stat.S_IMODE(Path(str(path) + ".sha256").stat().st_mode) == 0o444
    for relative in (
        "private_authority/main_agent_token",
        "private_credentials/mysql-client.cnf",
        "private_credentials/hybench_xp_qualification.properties",
    ):
        assert stat.S_IMODE((output / relative).stat().st_mode) == 0o600


def test_v20_keeps_600_second_exact_socket_readiness(bundles):
    unused_v19, v20 = bundles
    service = (v20 / "launch/mysql_xp_node02_adopted_service_v1.py").read_text()
    assert "DATABASE_SOCKET_READINESS_TIMEOUT_SECONDS = 600" in service
    assert '"--execute", "SELECT 1"' in service
    assert "database socket bootstrap readiness failed" in service

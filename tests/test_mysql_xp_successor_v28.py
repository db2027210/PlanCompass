from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
MATERIALS = Path("test_support/mysql_xp_qualification_bootstrap_materials_v5.py")
EXPECTED_PRIVATE = {
    "mysql-client.cnf",
    "hybench_xp_qualification.properties",
    "mysql-root-bootstrap.cnf",
}


def sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_materializer():
    path = ROOT / MATERIALS
    spec = importlib.util.spec_from_file_location("xp_v28_materials", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_binds_actual_materials_producer():
    manifest = json.loads((ROOT / "bundle_manifest.json").read_text())
    actual = sha(ROOT / MATERIALS)
    assert manifest["schema_version"] == "mysql_xp_node02_successor_bundle_v28"
    assert manifest["materials_generator_sha256"] == actual
    assert manifest["files"][MATERIALS.as_posix()] == actual
    assert manifest["predecessor_bundle_id"] == (
        "fb5f9a45066dc091112abad26028f98354a12dd98447a0874acad7838af343e6"
    )


def test_materials_exact_private_closure_and_modes(tmp_path):
    module = load_materializer()
    result = module.create_materials(
        ROOT, tmp_path, 10, "AUDIT_ONLY_ROOT_PASSWORD"
    )
    output = Path(result["materials_directory"])
    manifest = json.loads((output / "materials_manifest.json").read_text())
    assert set(manifest["private_credentials"]["files"]) == EXPECTED_PRIVATE
    assert {
        row.removeprefix("private_credentials/")
        for row in manifest["files"]
        if row.startswith("private_credentials/")
    } == EXPECTED_PRIVATE
    assert "private_authority/main_agent_token" in manifest["files"]
    assert stat.S_IMODE((output / "private_credentials").stat().st_mode) == 0o700
    assert stat.S_IMODE((output / "private_authority").stat().st_mode) == 0o700
    for name in EXPECTED_PRIVATE:
        assert stat.S_IMODE((output / "private_credentials" / name).stat().st_mode) == 0o600
    assert stat.S_IMODE((output / "private_authority/main_agent_token").stat().st_mode) == 0o600


def test_cli_requires_stdin_only_root_secret(tmp_path):
    command = [
        sys.executable, "-B", str(ROOT / MATERIALS),
        "--bundle", str(ROOT), "--output-root", str(tmp_path),
        "--valid-minutes", "10", "--root-password-stdin",
    ]
    secret = "AUDIT_ONLY_CLI_ROOT_PASSWORD"
    completed = subprocess.run(
        command, input=secret + "\n", text=True, capture_output=True,
        check=True,
    )
    assert secret not in " ".join(command)
    assert secret not in completed.stdout
    assert secret not in completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "SEALED_QUALIFICATION_ONLY_NOT_LAUNCHED"
    missing = subprocess.run(
        command[:-1], input="", text=True, capture_output=True,
        check=False,
    )
    assert missing.returncode != 0
    assert "--root-password-stdin is required" in missing.stderr


def test_cpu_io_and_postgresql_gates_are_unchanged():
    contract = json.loads((ROOT / "xp_executable_launch_contract_v3.json").read_text())
    db = contract["database_resource_envelope"]
    storage = contract["storage"]
    assert db["cpu_quota_cores"] == 5.3
    assert db["cpuset_cpus"] == "21-33,57-69"
    assert db["cpuset_mems"] == "1"
    assert db["io_read_bps"] == 29360128
    assert db["io_write_bps"] == 524288
    assert storage["data_partition"] == "/dev/nvme0n1p1"
    assert storage["io_throttle_parent_device"] == "/dev/nvme0n1"
    assert contract["postgresql_mutation_prohibited"] is True
    assert contract["cross_engine_comparison_prohibited"] is True


def test_bundle_is_exactly_sealed_and_read_only():
    manifest = json.loads((ROOT / "bundle_manifest.json").read_text())
    actual = {
        path.relative_to(ROOT).as_posix(): sha(path)
        for path in ROOT.rglob("*")
        if path.is_file()
        and path.relative_to(ROOT).as_posix()
        not in ("bundle_manifest.json", "bundle_manifest.json.sha256")
    }
    assert actual == manifest["files"]
    for path in (ROOT, *ROOT.rglob("*")):
        assert not path.is_symlink()
        assert stat.S_IMODE(path.stat().st_mode) & 0o222 == 0

#!/usr/bin/env python3
"""Build deterministic local-only XP v28 test/identity-closure successor."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "fb5f9a45066dc091112abad26028f98354a12dd98447a0874acad7838af343e6"
PARENT = ROOT / "artifacts/mysql_xp_node02_successor_v27" / PARENT_ID
OUTPUT_ROOT = ROOT / "artifacts/mysql_xp_node02_successor_v28"
BUILDER = Path("builder/build_mysql_xp_node02_successor_v28.py")
MATERIALS = Path("test_support/mysql_xp_qualification_bootstrap_materials_v5.py")
SERVICE = Path("launch/mysql_xp_node02_adopted_service_v1.py")
COLLECTOR = Path("qualification/mysql_distributed_live_readback_qualification_v1.py")
AUTHORITY = Path("qualification/mysql_xp_qualification_authority_v1.py")
FORMAL_MATERIALIZER = Path("launch/mysql_xp_node02_formal10_materializer_v1.py")
READINESS_TEST = Path("tests/test_mysql_xp_schema_readiness_v28.py")
V28_TEST = Path("tests/test_mysql_xp_successor_v28.py")


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def replace_exact(text, old, new, count, label):
    observed = text.count(old)
    if observed != count:
        raise BuildError("%s cardinality differs: %d" % (label, observed))
    return text.replace(old, new)


def patch_runtime(stage):
    service = stage / SERVICE
    text = service.read_text(encoding="utf-8")
    text = replace_exact(
        text,
        '        != "mysql_xp_node02_successor_bundle_v27"\n',
        '        != "mysql_xp_node02_successor_bundle_v28"\n',
        1, "service v28 schema",
    )
    service.write_text(text, encoding="utf-8")

    materials = stage / MATERIALS
    text = materials.read_text(encoding="utf-8")
    text = text.replace(
        "Create local-only, short-lived bootstrap materials for XP v19-v26.",
        "Create local-only, short-lived bootstrap materials for XP v19-v28.",
    )
    text = replace_exact(
        text,
        '    "mysql_xp_node02_successor_bundle_v27": '
        '"b8e574ea5d2a47faae7cbe1776c7407c6e4b6122b4567c8a9b7dde5513382dcc",\n',
        '    "mysql_xp_node02_successor_bundle_v27": '
        '"b8e574ea5d2a47faae7cbe1776c7407c6e4b6122b4567c8a9b7dde5513382dcc",\n'
        '    "mysql_xp_node02_successor_bundle_v28": "' + PARENT_ID + '",\n',
        1, "materials v28 schema",
    )
    nested = '''                "files": [
                    "mysql-client.cnf",
                    "hybench_xp_qualification.properties",
                ],
'''
    nested_v28 = '''                "files": [
                    "mysql-client.cnf",
                    "hybench_xp_qualification.properties",
                    "mysql-root-bootstrap.cnf",
                ],
'''
    text = replace_exact(
        text, nested, nested_v28, 1, "nested private credential closure",
    )
    materials.write_text(text, encoding="utf-8")

    for relative in (AUTHORITY, FORMAL_MATERIALIZER):
        path = stage / relative
        text = path.read_text(encoding="utf-8")
        needle = '            "mysql_xp_node02_successor_bundle_v27",\n'
        text = replace_exact(
            text, needle,
            needle + '            "mysql_xp_node02_successor_bundle_v28",\n',
            1, str(relative) + " v28 schema",
        )
        path.write_text(text, encoding="utf-8")

    collector = stage / COLLECTOR
    text = collector.read_text(encoding="utf-8")
    needle = '        "mysql_xp_node02_successor_bundle_v26",\n'
    text = replace_exact(
        text, needle,
        needle
        + '        "mysql_xp_node02_successor_bundle_v27",\n'
        + '        "mysql_xp_node02_successor_bundle_v28",\n',
        1, "collector v27/v28 schemas",
    )
    collector.write_text(text, encoding="utf-8")


def patch_private_fixture(path):
    text = path.read_text(encoding="utf-8")
    old = '''    mysql.chmod(0o600)
    properties.chmod(0o600)
    return directory, user, password
'''
    new = '''    root_defaults = directory / "mysql-root-bootstrap.cnf"
    root_defaults.write_text(
        "[client]\\nuser=root\\npassword=AUDIT_ONLY_ROOT_PASSWORD\\n",
        encoding="utf-8",
    )
    mysql.chmod(0o600)
    properties.chmod(0o600)
    root_defaults.chmod(0o600)
    return directory, user, password
'''
    text = replace_exact(text, old, new, 1, path.name + " root fixture")
    path.write_text(text, encoding="utf-8")


def patch_tests(stage):
    old_readiness = stage / "tests/test_mysql_xp_schema_readiness_v19.py"
    old_readiness.unlink()
    shutil.copy2(ROOT / READINESS_TEST, stage / READINESS_TEST)
    shutil.copy2(ROOT / V28_TEST, stage / V28_TEST)

    for name in (
        "test_mysql_xp_dedicated_daemon_v5.py",
        "test_mysql_xp_dedicated_daemon_v6.py",
        "test_mysql_xp_private_credentials_v3.py",
        "test_mysql_xp_private_credentials_v4.py",
    ):
        patch_private_fixture(stage / "tests" / name)

    replacements = {
        '"mysql_xp_node02_successor_bundle_v26"':
            '"mysql_xp_node02_successor_bundle_v28"',
        '"mysql_xp_node02_successor_bundle_v27"':
            '"mysql_xp_node02_successor_bundle_v28"',
        '.endswith("_v26")': '.endswith("_v28")',
    }
    for path in sorted((stage / "tests").glob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        for old, new in replacements.items():
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")

    call_replacements = {
        'generator.create_materials(ROOT, tmp_path / "materials", 10)':
            'generator.create_materials(ROOT, tmp_path / "materials", 10, "AUDIT_ONLY_ROOT_PASSWORD")',
        'materials.create_materials(v20, tmp_path / "materials", 10)':
            'materials.create_materials(v20, tmp_path / "materials", 10, "AUDIT_ONLY_ROOT_PASSWORD")',
        'materials.create_materials(bundle, tmp_path, 10)':
            'materials.create_materials(bundle, tmp_path, 10, "AUDIT_ONLY_ROOT_PASSWORD")',
        'mod.create_materials(mod.DEFAULT_BUNDLE, tmp_path)':
            'mod.create_materials(mod.DEFAULT_BUNDLE, tmp_path, 25, "AUDIT_ONLY_ROOT_PASSWORD")',
        'materials.create_materials(bundle, tmp_path, 60)':
            'materials.create_materials(bundle, tmp_path, 60, "AUDIT_ONLY_ROOT_PASSWORD")',
        'materials.create_materials(bundle, tmp_path / str(minutes), minutes)':
            'materials.create_materials(bundle, tmp_path / str(minutes), minutes, "AUDIT_ONLY_ROOT_PASSWORD")',
    }
    for path in sorted((stage / "tests").glob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        for old, new in call_replacements.items():
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")

    materials_test = stage / "tests/test_mysql_xp_materials_successor_v20.py"
    text = materials_test.read_text(encoding="utf-8")
    text = replace_exact(
        text,
        '    assert v20_manifest["predecessor_bundle_id"] == '
        '"9783caeb6e5e610f2fa008388d7f82e9e30ee302b7a29af41d5c549a0f8da0db"\n',
        '    assert v20_manifest["predecessor_bundle_id"] == "' + PARENT_ID + '"\n',
        1, "materials test v28 predecessor",
    )
    materials_test.write_text(text, encoding="utf-8")


def relative_files(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError("symlink prohibited: %s" % path)
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in (
            "bundle_manifest.json", "bundle_manifest.json.sha256",
        ):
            result[relative] = sha_file(path)
        elif not path.is_file() and not path.is_dir():
            raise BuildError("special file prohibited: %s" % path)
    return result


def seal_modes(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file():
            executable = bool(stat.S_IMODE(path.stat().st_mode) & 0o111)
            path.chmod(0o555 if executable else 0o444)
        elif path.is_dir():
            path.chmod(0o555)
    root.chmod(0o755)


def prepare(stage):
    shutil.copytree(PARENT, stage)
    stage.chmod(0o755)
    for path in stage.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
        elif path.is_file():
            path.chmod(stat.S_IMODE(path.stat().st_mode) | 0o200)
    (stage / "bundle_manifest.json").unlink()
    (stage / "bundle_manifest.json.sha256").unlink()
    target_builder = stage / BUILDER
    target_builder.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), target_builder)
    patch_runtime(stage)
    patch_tests(stage)

    parent_manifest = json.loads(
        (PARENT / "bundle_manifest.json").read_text(encoding="utf-8")
    )
    service_sha = sha_file(stage / SERVICE)
    contract_path = stage / "xp_executable_launch_contract_v3.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["adopted_service"]["sha256"] = service_sha
    contract_path.write_bytes(canonical(contract))
    materials_sha = sha_file(stage / MATERIALS)
    manifest = {
        key: value for key, value in parent_manifest.items()
        if key not in ("bundle_id", "files")
    }
    manifest.update({
        "schema_version": "mysql_xp_node02_successor_bundle_v28",
        "status": "SEALED_V28_TEST_AND_MATERIAL_IDENTITY_CLOSURE_NOT_LAUNCHED",
        "predecessor_bundle_id": PARENT_ID,
        "supersedes_bundle_id": PARENT_ID,
        "invalidated_incomplete_test_closure_bundle_id": PARENT_ID,
        "invalidated_predecessor_credit": 0,
        "materials_generator_sha256": materials_sha,
        "service_sha256": service_sha,
        "collector_sha256": sha_file(stage / COLLECTOR),
        "qualification_authority_sha256": sha_file(stage / AUTHORITY),
        "root_secret_transport": "private_stdin_only",
        "root_password_in_argv_environment_manifest_or_log": False,
        "private_credential_file_closure": [
            "mysql-client.cnf",
            "hybench_xp_qualification.properties",
            "mysql-root-bootstrap.cnf",
        ],
        "fresh_material_attempt_required": True,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions": 0,
    })
    manifest["files"] = relative_files(stage)
    bundle_id = sha_bytes(canonical(manifest))
    manifest["bundle_id"] = bundle_id
    manifest_path = stage / "bundle_manifest.json"
    manifest_path.write_bytes(canonical(manifest))
    (stage / "bundle_manifest.json.sha256").write_text(
        "%s  bundle_manifest.json\n" % sha_file(manifest_path),
        encoding="ascii",
    )
    return bundle_id


def build():
    if not PARENT.is_dir():
        raise BuildError("sealed v27 parent absent")
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="mysql-xp-v28-", dir=str(OUTPUT_ROOT)
    ) as raw:
        stage = Path(raw) / "stage"
        bundle_id = prepare(stage)
        destination = OUTPUT_ROOT / bundle_id
        seal_modes(stage)
        if destination.exists():
            if sha_file(destination / "bundle_manifest.json") != sha_file(
                stage / "bundle_manifest.json"
            ):
                raise BuildError("existing destination identity differs")
            return destination
        stage.parent.chmod(0o700)
        OUTPUT_ROOT.chmod(0o755)
        os.replace(str(stage), str(destination))
        destination.chmod(0o555)
        return destination


if __name__ == "__main__":
    result = build()
    print(json.dumps({
        "status": "SEALED_LOCAL_ONLY_NOT_LAUNCHED",
        "bundle": str(result),
        "bundle_id": result.name,
        "manifest_sha256": sha_file(result / "bundle_manifest.json"),
        "materials_generator_sha256": sha_file(result / MATERIALS),
    }, sort_keys=True))

#!/usr/bin/env python3
"""Build immutable XP v20 with strict v19/v20 material identity checks."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PARENT_ID = "e91031f5af956af87904a240fcf6ce6673d2511dfc66f1a6337f36698133050d"
PARENT_MANIFEST_SHA256 = "9e58aabf733881726755251a5b1b3f9a3e5e79ff2821b20cbe6ae56b7e91b872"
PARENT = ROOT / "artifacts/mysql_xp_node02_successor_v19" / PARENT_ID
OUTPUT_ROOT = ROOT / "artifacts/mysql_xp_node02_successor_v20"
SERVICE = "launch/mysql_xp_node02_adopted_service_v1.py"
AUTHORITY = "qualification/mysql_xp_qualification_authority_v1.py"
COLLECTOR = "qualification/mysql_distributed_live_readback_qualification_v1.py"
MATERIALIZER = "launch/mysql_xp_node02_formal10_materializer_v1.py"
MATERIALS = "test_support/mysql_xp_qualification_bootstrap_materials_v5.py"
COMPAT_TEST = "tests/test_mysql_xp_systemd_compatibility_v11.py"
READINESS_TEST = "tests/test_mysql_xp_schema_readiness_v19.py"
REGRESSION_TEST = "tests/test_mysql_xp_materials_successor_v20.py"
BUILDER = "build_mysql_xp_node02_materials_successor_v20.py"
REMEDIATION = "xp_node02_material_identity_remediation_v1.json"


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError(label + " is not an object")
    return value


def relative_files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError("symlink prohibited: " + str(path))
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in (
            "bundle_manifest.json", "bundle_manifest.json.sha256"
        ):
            result[relative] = sha_file(path)
        elif not path.is_file() and not path.is_dir():
            raise BuildError("special file prohibited: " + str(path))
    return result


def replace_exact(payload: bytes, old: str, new: str, count: int, label: str) -> bytes:
    text = payload.decode("utf-8")
    if text.count(old) != count:
        raise BuildError(label + " predecessor cardinality differs")
    return text.replace(old, new).encode("utf-8")


def patch_schema_list(payload: bytes, label: str) -> bytes:
    return replace_exact(
        payload,
        '            "mysql_xp_node02_successor_bundle_v19",\n',
        '            "mysql_xp_node02_successor_bundle_v19",\n'
        '            "mysql_xp_node02_successor_bundle_v20",\n',
        1, label,
    )


def patch_materials(payload: bytes) -> bytes:
    payload = replace_exact(
        payload,
        '"""Create local-only, short-lived bootstrap materials for the XP v8 successor.',
        '"""Create local-only, short-lived bootstrap materials for XP v19/v20.',
        1, "materials documentation",
    )
    payload = replace_exact(
        payload,
        '''EXPECTED_BUNDLE_ID = (
    "2f7824bf1b7f234ebd754bf6e3283420c6eaa789618a7a38f6d75472338b2087"
)
EXPECTED_MANIFEST_SHA256 = (
    "8cb77864c55b5b51d846abc736c5ec872cb0cdcf7883550e86652e5e190c6412"
)
''',
        '''V19_BUNDLE_ID = (
    "e91031f5af956af87904a240fcf6ce6673d2511dfc66f1a6337f36698133050d"
)
V19_MANIFEST_SHA256 = (
    "9e58aabf733881726755251a5b1b3f9a3e5e79ff2821b20cbe6ae56b7e91b872"
)
V19_PREDECESSOR_ID = (
    "f43f33b5d60cf6de2fe63f96852e6a46ea3b132b5d52039a1e60021771dc9262"
)
LEGACY_EXPIRED_MATERIAL_BUNDLE_ID = (
    "2f7824bf1b7f234ebd754bf6e3283420c6eaa789618a7a38f6d75472338b2087"
)
SUPPORTED_BUNDLE_SCHEMAS = {
    "mysql_xp_node02_successor_bundle_v19": V19_PREDECESSOR_ID,
    "mysql_xp_node02_successor_bundle_v20": V19_BUNDLE_ID,
}
SHA256_RE = re.compile(r"[0-9a-f]{64}$")
''',
        1, "current bundle constants",
    )
    marker = '''def load_sealed_json(path, label):
'''
    helpers = '''def verify_content_addressed_bundle(bundle, manifest):
    """Verify the exact regular-file closure and canonical directory identity."""
    files = manifest.get("files")
    if not isinstance(files, dict) or SHA256_RE.fullmatch(bundle.name) is None:
        raise MaterialError("bundle content identity metadata differs")
    actual = {}
    for path in sorted(bundle.rglob("*")):
        if path.is_symlink():
            raise MaterialError("bundle symlink prohibited")
        relative = path.relative_to(bundle).as_posix()
        if path.is_file():
            if relative not in (
                "bundle_manifest.json", "bundle_manifest.json.sha256"
            ):
                actual[relative] = sha256(path)
        elif not path.is_dir():
            raise MaterialError("bundle special file prohibited")
    if actual != files:
        raise MaterialError("bundle exact file closure differs")
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    if (
        bundle_id != bundle.name
        or hashlib.sha256(canonical(unsigned)).hexdigest() != bundle.name
    ):
        raise MaterialError("bundle canonical content identity differs")


'''
    payload = replace_exact(payload, marker, helpers + marker, 1,
                            "content identity helper")
    payload = replace_exact(
        payload,
        '''    manifest = load_sealed_json(bundle / "bundle_manifest.json", "bundle manifest")
    contract_path = bundle / "xp_executable_launch_contract_v3.json"
''',
        '''    manifest = load_sealed_json(bundle / "bundle_manifest.json", "bundle manifest")
    verify_content_addressed_bundle(bundle, manifest)
    schema = manifest.get("schema_version")
    expected_predecessor = SUPPORTED_BUNDLE_SCHEMAS.get(schema)
    if expected_predecessor is None:
        raise MaterialError("unsupported successor bundle schema")
    if schema == "mysql_xp_node02_successor_bundle_v19" and (
        bundle.name != V19_BUNDLE_ID
        or sha256(bundle / "bundle_manifest.json") != V19_MANIFEST_SHA256
    ):
        raise MaterialError("v19 sealed content identity differs")
    contract_path = bundle / "xp_executable_launch_contract_v3.json"
''',
        1, "verified bundle identity prelude",
    )
    payload = replace_exact(
        payload,
        '''        manifest.get("bundle_id") != bundle.name
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or manifest.get("supersedes_bundle_id")
        != "f43f33b5d60cf6de2fe63f96852e6a46ea3b132b5d52039a1e60021771dc9262"
''',
        '''        manifest.get("bundle_id") != bundle.name
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or manifest.get("predecessor_bundle_id") != expected_predecessor
        or manifest.get("supersedes_bundle_id") != expected_predecessor
''',
        1, "schema-specific predecessor binding",
    )
    payload = replace_exact(
        payload,
        '''        "status": "PLANNED_NOT_LAUNCHED",
        "spec_sha256": hashlib.sha256(canonical(spec)).hexdigest(),
''',
        '''        "status": "PLANNED_NOT_LAUNCHED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "spec_sha256": hashlib.sha256(canonical(spec)).hexdigest(),
''',
        1, "command plan explicit scope",
    )
    payload = replace_exact(
        payload,
        '''        "status": "PLANNED_NOT_INSTALLED",
        "host": HOST,
''',
        '''        "status": "PLANNED_NOT_INSTALLED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "host": HOST,
''',
        1, "authorization install explicit scope",
    )
    payload = replace_exact(
        payload,
        '''            "status": "SEALED_QUALIFICATION_ONLY_NOT_LAUNCHED",
            "bundle_id": bundle.name,
''',
        '''            "status": "SEALED_QUALIFICATION_ONLY_NOT_LAUNCHED",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "postgresql_mutation_prohibited": True,
            "bundle_id": bundle.name,
''',
        1, "materials manifest explicit scope",
    )
    return replace_exact(
        payload, "\"bundle_id\": EXPECTED_BUNDLE_ID,",
        "\"bundle_id\": LEGACY_EXPIRED_MATERIAL_BUNDLE_ID,",
        1, "legacy expired material identity label",
    )


def verify_parent() -> dict[str, Any]:
    manifest_path = PARENT / "bundle_manifest.json"
    manifest = load(manifest_path, "parent manifest")
    unsigned = dict(manifest)
    actual_id = unsigned.pop("bundle_id", None)
    if (
        actual_id != PARENT_ID
        or sha_file(manifest_path) != PARENT_MANIFEST_SHA256
        or sha_bytes(canonical(unsigned)) != PARENT_ID
        or relative_files(PARENT) != manifest.get("files")
        or manifest.get("schema_version") != "mysql_xp_node02_successor_bundle_v19"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("parent identity, closure, scope, or authority differs")
    return manifest


def freeze(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def build() -> Path:
    parent = verify_parent()
    content = {
        relative: (PARENT / relative).read_bytes()
        for relative in sorted(parent["files"])
    }
    content[SERVICE] = replace_exact(
        content[SERVICE],
        '        != "mysql_xp_node02_successor_bundle_v19"',
        '        != "mysql_xp_node02_successor_bundle_v20"',
        1, "service outer schema",
    )
    content[AUTHORITY] = patch_schema_list(content[AUTHORITY], "authority schemas")
    content[MATERIALIZER] = patch_schema_list(
        content[MATERIALIZER], "materializer schemas"
    )
    content[COLLECTOR] = replace_exact(
        content[COLLECTOR],
        '        "mysql_xp_node02_successor_bundle_v19",\n',
        '        "mysql_xp_node02_successor_bundle_v19",\n'
        '        "mysql_xp_node02_successor_bundle_v20",\n',
        1, "collector schemas",
    )
    content[MATERIALS] = patch_materials(content[MATERIALS])
    content[COMPAT_TEST] = replace_exact(
        content[COMPAT_TEST],
        'manifest["schema_version"]=="mysql_xp_node02_successor_bundle_v19"',
        'manifest["schema_version"]=="mysql_xp_node02_successor_bundle_v20"',
        1, "compatibility test schema",
    )
    content[READINESS_TEST] = replace_exact(
        content[READINESS_TEST],
        'cases = [(v19, "mysql_xp_node02_successor_bundle_v19")]',
        'cases = [(v19, "mysql_xp_node02_successor_bundle_v20")]',
        1, "readiness test current schema",
    )
    content[REGRESSION_TEST] = (ROOT / REGRESSION_TEST).read_bytes()
    content[BUILDER] = HERE.read_bytes()
    content[REMEDIATION] = canonical({
        "schema_version": "mysql_xp_node02_material_identity_remediation_v1",
        "status": "OFFLINE_STRICT_MATERIAL_IDENTITY_SUCCESSOR_NOT_LAUNCHED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "parent_bundle_id": PARENT_ID,
        "parent_manifest_sha256": PARENT_MANIFEST_SHA256,
        "accepted_material_generator_schemas": [
            "mysql_xp_node02_successor_bundle_v19",
            "mysql_xp_node02_successor_bundle_v20",
        ],
        "exact_regular_file_closure_required": True,
        "canonical_content_addressed_directory_identity_required": True,
        "v19_exact_bundle_and_manifest_identity_required": True,
        "legacy_expired_material_bundle_id_is_not_current_identity": True,
        "bootstrap_readiness_timeout_seconds": 600,
        "fresh_qualification_required": True,
        "fresh_r0_required": True,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions": 0,
    })

    files = {relative: sha_bytes(payload)
             for relative, payload in sorted(content.items())}
    identity = {key: value for key, value in parent.items()
                if key not in ("bundle_id", "files")}
    identity.update({
        "schema_version": "mysql_xp_node02_successor_bundle_v20",
        "status": "SEALED_STRICT_MATERIAL_IDENTITY_SUCCESSOR_NOT_LAUNCHED",
        "builder_revision": "v20_strict_material_identity_and_closure",
        "builder_sha256": sha_file(HERE),
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
        "supersedes_bundle_id": PARENT_ID,
        "service_sha256": files[SERVICE],
        "collector_sha256": files[COLLECTOR],
        "materials_generator_sha256": files[MATERIALS],
        "fresh_r0_required": True,
        "historical_accepted_round_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "offline_build_only": True,
        "remote_actions": 0,
        "files": files,
    })
    bundle_id = sha_bytes(canonical(identity))
    manifest = dict(identity, bundle_id=bundle_id)

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    destination = OUTPUT_ROOT / bundle_id
    if not destination.exists():
        stage = Path(tempfile.mkdtemp(prefix=".xp-v20.", dir=OUTPUT_ROOT))
        try:
            for relative, payload in sorted(content.items()):
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            raw = canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii"
            )
            freeze(stage)
            os.replace(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)

    if relative_files(destination) != files:
        raise BuildError("published successor closure differs")
    unsigned = dict(load(destination / "bundle_manifest.json", "manifest"))
    actual_id = unsigned.pop("bundle_id", None)
    if actual_id != destination.name or sha_bytes(canonical(unsigned)) != destination.name:
        raise BuildError("published successor content identity differs")
    for path in (destination, *destination.rglob("*")):
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError("published successor is mutable")
    print(json.dumps({
        "status": "PASS_IMMUTABLE_XP_V20_STRICT_MATERIAL_IDENTITY_SUCCESSOR",
        "bundle_id": bundle_id,
        "bundle": str(destination),
        "verified_files": len(files),
        "fresh_qualification_required": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions": 0,
    }, sort_keys=True))
    return destination


if __name__ == "__main__":
    try:
        build()
    except (BuildError, OSError, ValueError, KeyError) as exc:
        raise SystemExit("NOT_READY: " + str(exc))

#!/usr/bin/env python3
"""Build immutable XP v22 allowing qualification materials TTL through 60m."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

import build_mysql_xp_node02_systemd219_successor_v21 as base


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PARENT_ID = "f6528795e2eabc84c7d29ff8d387bc50ffd0bbb055e4fc0987da5aa8626717c2"
PARENT_MANIFEST_SHA256 = "8f0cde3c6f5266d2bcd716e84cf5e3ae57412baf4dd3fd0ea60f3a723829615e"
PARENT = ROOT / "artifacts/mysql_xp_node02_successor_v21" / PARENT_ID
OUTPUT_ROOT = ROOT / "artifacts/mysql_xp_node02_successor_v22"
SERVICE = "launch/mysql_xp_node02_adopted_service_v1.py"
AUTHORITY = "qualification/mysql_xp_qualification_authority_v1.py"
COLLECTOR = "qualification/mysql_distributed_live_readback_qualification_v1.py"
MATERIALIZER = "launch/mysql_xp_node02_formal10_materializer_v1.py"
MATERIALS = "test_support/mysql_xp_qualification_bootstrap_materials_v5.py"
COMPAT_TEST = "tests/test_mysql_xp_systemd_compatibility_v11.py"
READINESS_TEST = "tests/test_mysql_xp_schema_readiness_v19.py"
V20_TEST = "tests/test_mysql_xp_materials_successor_v20.py"
REGRESSION_TEST = "tests/test_mysql_xp_ttl_successor_v22.py"
BUILDER = "build_mysql_xp_node02_ttl_successor_v22.py"
REMEDIATION = "xp_node02_qualification_ttl_remediation_v1.json"


class BuildError(RuntimeError):
    pass


def replace(payload: bytes, old: str, new: str, count: int, label: str) -> bytes:
    try:
        return base.replace(payload, old, new, count, label)
    except base.BuildError as exc:
        raise BuildError(str(exc)) from exc


def patch_schema_list(payload: bytes, label: str) -> bytes:
    return replace(
        payload,
        '            "mysql_xp_node02_successor_bundle_v21",\n',
        '            "mysql_xp_node02_successor_bundle_v21",\n'
        '            "mysql_xp_node02_successor_bundle_v22",\n',
        1, label,
    )


def patch_materials(payload: bytes) -> bytes:
    payload = replace(
        payload,
        '"""Create local-only, short-lived bootstrap materials for XP v19-v21.',
        '"""Create local-only, short-lived bootstrap materials for XP v19-v22.',
        1, "materials documentation",
    )
    payload = replace(
        payload,
        '''V20_MANIFEST_SHA256 = (
    "3df7740447d613a653ef500100a54edf755ab05b8728336460ac96b5105ee17f"
)
LEGACY_EXPIRED_MATERIAL_BUNDLE_ID = (
''',
        '''V20_MANIFEST_SHA256 = (
    "3df7740447d613a653ef500100a54edf755ab05b8728336460ac96b5105ee17f"
)
V21_BUNDLE_ID = (
    "f6528795e2eabc84c7d29ff8d387bc50ffd0bbb055e4fc0987da5aa8626717c2"
)
V21_MANIFEST_SHA256 = (
    "8f0cde3c6f5266d2bcd716e84cf5e3ae57412baf4dd3fd0ea60f3a723829615e"
)
LEGACY_EXPIRED_MATERIAL_BUNDLE_ID = (
''',
        1, "v21 exact identity constants",
    )
    payload = replace(
        payload,
        '''    "mysql_xp_node02_successor_bundle_v21": V20_BUNDLE_ID,
}
''',
        '''    "mysql_xp_node02_successor_bundle_v21": V20_BUNDLE_ID,
    "mysql_xp_node02_successor_bundle_v22": V21_BUNDLE_ID,
}
''',
        1, "v22 schema policy",
    )
    payload = replace(
        payload,
        '''    if schema == "mysql_xp_node02_successor_bundle_v20" and (
        bundle.name != V20_BUNDLE_ID
        or sha256(bundle / "bundle_manifest.json") != V20_MANIFEST_SHA256
    ):
        raise MaterialError("v20 sealed content identity differs")
''',
        '''    if schema == "mysql_xp_node02_successor_bundle_v20" and (
        bundle.name != V20_BUNDLE_ID
        or sha256(bundle / "bundle_manifest.json") != V20_MANIFEST_SHA256
    ):
        raise MaterialError("v20 sealed content identity differs")
    if schema == "mysql_xp_node02_successor_bundle_v21" and (
        bundle.name != V21_BUNDLE_ID
        or sha256(bundle / "bundle_manifest.json") != V21_MANIFEST_SHA256
    ):
        raise MaterialError("v21 sealed content identity differs")
''',
        1, "v21 exact identity check",
    )
    return replace(
        payload,
        '''    if not isinstance(valid_minutes, int) or not 10 <= valid_minutes <= 30:
        raise MaterialError("valid minutes must be within 10..30")
''',
        '''    if not isinstance(valid_minutes, int) or not 10 <= valid_minutes <= 60:
        raise MaterialError("valid minutes must be within 10..60")
''',
        1, "qualification TTL ceiling",
    )


def verify_parent():
    manifest_path = PARENT / "bundle_manifest.json"
    manifest = base.base.load(manifest_path, "parent manifest")
    unsigned = dict(manifest)
    actual_id = unsigned.pop("bundle_id", None)
    if (
        actual_id != PARENT_ID
        or base.base.sha_file(manifest_path) != PARENT_MANIFEST_SHA256
        or base.base.sha_bytes(base.base.canonical(unsigned)) != PARENT_ID
        or base.base.relative_files(PARENT) != manifest.get("files")
        or manifest.get("schema_version") != "mysql_xp_node02_successor_bundle_v21"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("parent identity, closure, scope, or authority differs")
    return manifest


def build() -> Path:
    helper = base.base
    parent = verify_parent()
    content = {r: (PARENT / r).read_bytes() for r in sorted(parent["files"])}
    content[SERVICE] = replace(
        content[SERVICE],
        '        != "mysql_xp_node02_successor_bundle_v21"',
        '        != "mysql_xp_node02_successor_bundle_v22"',
        1, "service schema",
    )
    content[AUTHORITY] = patch_schema_list(content[AUTHORITY], "authority schemas")
    content[MATERIALIZER] = patch_schema_list(content[MATERIALIZER], "materializer schemas")
    content[COLLECTOR] = replace(
        content[COLLECTOR],
        '        "mysql_xp_node02_successor_bundle_v21",\n',
        '        "mysql_xp_node02_successor_bundle_v21",\n'
        '        "mysql_xp_node02_successor_bundle_v22",\n',
        1, "collector schemas",
    )
    content[MATERIALS] = patch_materials(content[MATERIALS])
    content[COMPAT_TEST] = replace(
        content[COMPAT_TEST],
        'manifest["schema_version"]=="mysql_xp_node02_successor_bundle_v21"',
        'manifest["schema_version"]=="mysql_xp_node02_successor_bundle_v22"',
        1, "compat test schema",
    )
    content[READINESS_TEST] = replace(
        content[READINESS_TEST],
        'cases = [(v19, "mysql_xp_node02_successor_bundle_v21")]',
        'cases = [(v19, "mysql_xp_node02_successor_bundle_v22")]',
        1, "readiness test schema",
    )
    content[V20_TEST] = replace(
        content[V20_TEST],
        'assert v20_manifest["schema_version"].endswith("_v21")',
        'assert v20_manifest["schema_version"].endswith("_v22")',
        1, "materials test current schema",
    )
    content[V20_TEST] = replace(
        content[V20_TEST],
        'assert v20_manifest["predecessor_bundle_id"] == "f2dd55ea6b5ea4db704aa533bddc9161b378c30735b9606a092efccc1315179f"',
        'assert v20_manifest["predecessor_bundle_id"] == "' + PARENT_ID + '"',
        1, "materials test predecessor",
    )
    content[REGRESSION_TEST] = (ROOT / REGRESSION_TEST).read_bytes()
    content[BUILDER] = HERE.read_bytes()
    content[REMEDIATION] = helper.canonical({
        "schema_version": "mysql_xp_node02_qualification_ttl_remediation_v1",
        "status": "SEALED_OFFLINE_TTL_SUCCESSOR_NOT_LAUNCHED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "parent_bundle_id": PARENT_ID,
        "only_functional_policy_change": "qualification_material_valid_minutes_max_30_to_60",
        "minimum_valid_minutes": 10,
        "maximum_valid_minutes": 60,
        "systemd219_empty_cgroup_audit_preserved": True,
        "required_nonempty_subsets": 127,
        "fixed_real_replay_top_k": 5,
        "database_cpu_quota_cores": 5.3,
        "database_io_read_bps": 29360128,
        "database_io_write_bps": 524288,
        "bootstrap_readiness_timeout_seconds": 600,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions": 0,
    })
    files = {r: helper.sha_bytes(p) for r, p in sorted(content.items())}
    identity = {k: v for k, v in parent.items() if k not in ("bundle_id", "files")}
    identity.update({
        "schema_version": "mysql_xp_node02_successor_bundle_v22",
        "status": "SEALED_60_MINUTE_MATERIAL_TTL_SUCCESSOR_NOT_LAUNCHED",
        "builder_revision": "v22_qualification_material_ttl_max_60",
        "builder_sha256": helper.sha_file(HERE),
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
        "supersedes_bundle_id": PARENT_ID,
        "service_sha256": files[SERVICE],
        "collector_sha256": files[COLLECTOR],
        "materials_generator_sha256": files[MATERIALS],
        "qualification_material_valid_minutes_min": 10,
        "qualification_material_valid_minutes_max": 60,
        "fresh_r0_required": True,
        "historical_accepted_round_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "offline_build_only": True,
        "remote_actions": 0,
        "files": files,
    })
    bundle_id = helper.sha_bytes(helper.canonical(identity))
    manifest = dict(identity, bundle_id=bundle_id)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    destination = OUTPUT_ROOT / bundle_id
    if not destination.exists():
        stage = Path(tempfile.mkdtemp(prefix=".xp-v22.", dir=OUTPUT_ROOT))
        try:
            for relative, payload in sorted(content.items()):
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            raw = helper.canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                helper.sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii"
            )
            helper.freeze(stage)
            os.replace(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    if helper.relative_files(destination) != files:
        raise BuildError("published successor closure differs")
    unsigned = dict(helper.load(destination / "bundle_manifest.json", "manifest"))
    if unsigned.pop("bundle_id", None) != destination.name or helper.sha_bytes(helper.canonical(unsigned)) != destination.name:
        raise BuildError("published content identity differs")
    for path in (destination, *destination.rglob("*")):
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError("published successor is mutable")
    print(json.dumps({
        "status": "PASS_IMMUTABLE_XP_V22_TTL_SUCCESSOR",
        "bundle_id": bundle_id,
        "bundle": str(destination),
        "verified_files": len(files),
        "valid_minutes_max": 60,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions": 0,
    }, sort_keys=True))
    return destination


if __name__ == "__main__":
    try:
        build()
    except (BuildError, base.BuildError, OSError, ValueError, KeyError) as exc:
        raise SystemExit("NOT_READY: " + str(exc))

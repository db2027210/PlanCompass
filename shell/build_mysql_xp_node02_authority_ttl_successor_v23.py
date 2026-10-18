#!/usr/bin/env python3
"""Build immutable XP v23 with generator/service 60-minute TTL parity."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

import build_mysql_xp_node02_ttl_successor_v22 as base


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PARENT_ID = "21d1a853548ebdff573d18a82c7a124b810b5656e3e3a330146f87d3e0b460ad"
PARENT_MANIFEST_SHA256 = "37863284416171c00a1a9f2e4b036455abf3667a183e3df3eb8aa2b01d36c115"
PARENT = ROOT / "artifacts/mysql_xp_node02_successor_v22" / PARENT_ID
OUTPUT_ROOT = ROOT / "artifacts/mysql_xp_node02_successor_v23"
SERVICE = "launch/mysql_xp_node02_adopted_service_v1.py"
AUTHORITY = "qualification/mysql_xp_qualification_authority_v1.py"
COLLECTOR = "qualification/mysql_distributed_live_readback_qualification_v1.py"
MATERIALIZER = "launch/mysql_xp_node02_formal10_materializer_v1.py"
MATERIALS = "test_support/mysql_xp_qualification_bootstrap_materials_v5.py"
COMPAT_TEST = "tests/test_mysql_xp_systemd_compatibility_v11.py"
READINESS_TEST = "tests/test_mysql_xp_schema_readiness_v19.py"
V20_TEST = "tests/test_mysql_xp_materials_successor_v20.py"
REGRESSION_TEST = "tests/test_mysql_xp_authority_ttl_successor_v23.py"
BUILDER = "build_mysql_xp_node02_authority_ttl_successor_v23.py"
REMEDIATION = "xp_node02_authority_ttl_parity_remediation_v1.json"


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
        '            "mysql_xp_node02_successor_bundle_v22",\n',
        '            "mysql_xp_node02_successor_bundle_v22",\n'
        '            "mysql_xp_node02_successor_bundle_v23",\n',
        1, label,
    )


def patch_materials(payload: bytes) -> bytes:
    payload = replace(
        payload,
        '"""Create local-only, short-lived bootstrap materials for XP v19-v22.',
        '"""Create local-only, short-lived bootstrap materials for XP v19-v23.',
        1, "materials documentation",
    )
    payload = replace(
        payload,
        '''V21_MANIFEST_SHA256 = (
    "8f0cde3c6f5266d2bcd716e84cf5e3ae57412baf4dd3fd0ea60f3a723829615e"
)
LEGACY_EXPIRED_MATERIAL_BUNDLE_ID = (
''',
        '''V21_MANIFEST_SHA256 = (
    "8f0cde3c6f5266d2bcd716e84cf5e3ae57412baf4dd3fd0ea60f3a723829615e"
)
V22_BUNDLE_ID = (
    "21d1a853548ebdff573d18a82c7a124b810b5656e3e3a330146f87d3e0b460ad"
)
V22_MANIFEST_SHA256 = (
    "37863284416171c00a1a9f2e4b036455abf3667a183e3df3eb8aa2b01d36c115"
)
LEGACY_EXPIRED_MATERIAL_BUNDLE_ID = (
''',
        1, "v22 exact identity constants",
    )
    payload = replace(
        payload,
        '''    "mysql_xp_node02_successor_bundle_v22": V21_BUNDLE_ID,
}
''',
        '''    "mysql_xp_node02_successor_bundle_v22": V21_BUNDLE_ID,
    "mysql_xp_node02_successor_bundle_v23": V22_BUNDLE_ID,
}
''',
        1, "v23 schema policy",
    )
    return replace(
        payload,
        '''    if schema == "mysql_xp_node02_successor_bundle_v21" and (
        bundle.name != V21_BUNDLE_ID
        or sha256(bundle / "bundle_manifest.json") != V21_MANIFEST_SHA256
    ):
        raise MaterialError("v21 sealed content identity differs")
''',
        '''    if schema == "mysql_xp_node02_successor_bundle_v21" and (
        bundle.name != V21_BUNDLE_ID
        or sha256(bundle / "bundle_manifest.json") != V21_MANIFEST_SHA256
    ):
        raise MaterialError("v21 sealed content identity differs")
    if schema == "mysql_xp_node02_successor_bundle_v22" and (
        bundle.name != V22_BUNDLE_ID
        or sha256(bundle / "bundle_manifest.json") != V22_MANIFEST_SHA256
    ):
        raise MaterialError("v22 sealed content identity differs")
''',
        1, "v22 exact identity check",
    )


def verify_parent():
    helper = base.base.base
    path = PARENT / "bundle_manifest.json"
    manifest = helper.load(path, "parent manifest")
    unsigned = dict(manifest)
    actual = unsigned.pop("bundle_id", None)
    if (
        actual != PARENT_ID
        or helper.sha_file(path) != PARENT_MANIFEST_SHA256
        or helper.sha_bytes(helper.canonical(unsigned)) != PARENT_ID
        or helper.relative_files(PARENT) != manifest.get("files")
        or manifest.get("schema_version") != "mysql_xp_node02_successor_bundle_v22"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("parent identity, closure, scope, or authority differs")
    return manifest


def build() -> Path:
    helper = base.base.base
    parent = verify_parent()
    content = {r: (PARENT / r).read_bytes() for r in sorted(parent["files"])}
    service = replace(
        content[SERVICE],
        '        != "mysql_xp_node02_successor_bundle_v22"',
        '        != "mysql_xp_node02_successor_bundle_v23"',
        1, "service schema",
    )
    service = replace(
        service,
        '''    if duration <= 0 or duration > 1800:
        raise ServiceError("authorization validity exceeds 30 minutes")
''',
        '''    if duration <= 0 or duration > 3600:
        raise ServiceError("authorization validity exceeds 60 minutes")
''',
        1, "sealed service authority TTL ceiling",
    )
    content[SERVICE] = service
    content[AUTHORITY] = patch_schema_list(content[AUTHORITY], "authority schemas")
    content[MATERIALIZER] = patch_schema_list(content[MATERIALIZER], "materializer schemas")
    content[COLLECTOR] = replace(
        content[COLLECTOR],
        '        "mysql_xp_node02_successor_bundle_v22",\n',
        '        "mysql_xp_node02_successor_bundle_v22",\n'
        '        "mysql_xp_node02_successor_bundle_v23",\n',
        1, "collector schemas",
    )
    content[MATERIALS] = patch_materials(content[MATERIALS])
    content[COMPAT_TEST] = replace(
        content[COMPAT_TEST],
        'manifest["schema_version"]=="mysql_xp_node02_successor_bundle_v22"',
        'manifest["schema_version"]=="mysql_xp_node02_successor_bundle_v23"',
        1, "compat test schema",
    )
    content[READINESS_TEST] = replace(
        content[READINESS_TEST],
        'cases = [(v19, "mysql_xp_node02_successor_bundle_v22")]',
        'cases = [(v19, "mysql_xp_node02_successor_bundle_v23")]',
        1, "readiness test schema",
    )
    content[V20_TEST] = replace(
        content[V20_TEST],
        'assert v20_manifest["schema_version"].endswith("_v22")',
        'assert v20_manifest["schema_version"].endswith("_v23")',
        1, "materials test schema",
    )
    content[V20_TEST] = replace(
        content[V20_TEST],
        'assert v20_manifest["predecessor_bundle_id"] == "' + PARENT_ID + '"',
        'assert v20_manifest["predecessor_bundle_id"] == "' + PARENT_ID + '"',
        0, "unused",
    ) if False else content[V20_TEST]
    content[V20_TEST] = replace(
        content[V20_TEST],
        'assert v20_manifest["predecessor_bundle_id"] == "f6528795e2eabc84c7d29ff8d387bc50ffd0bbb055e4fc0987da5aa8626717c2"',
        'assert v20_manifest["predecessor_bundle_id"] == "' + PARENT_ID + '"',
        1, "materials test predecessor",
    )
    content[REGRESSION_TEST] = (ROOT / REGRESSION_TEST).read_bytes()
    content[BUILDER] = HERE.read_bytes()
    content[REMEDIATION] = helper.canonical({
        "schema_version": "mysql_xp_node02_authority_ttl_parity_remediation_v1",
        "status": "SEALED_OFFLINE_AUTHORITY_TTL_PARITY_SUCCESSOR_NOT_LAUNCHED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "parent_bundle_id": PARENT_ID,
        "invalidated_v22_exact60_attempt_id": "xp-formal-qual-20260803T023154Z-02ec0b15b234f8e2dd162212",
        "invalidated_attempt_credit": 0,
        "generator_max_validity_seconds": 3600,
        "sealed_service_max_validity_seconds": 3600,
        "plan_and_bootstrap_same_authority_gate_required": True,
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
        "schema_version": "mysql_xp_node02_successor_bundle_v23",
        "status": "SEALED_GENERATOR_SERVICE_60_MINUTE_TTL_PARITY_NOT_LAUNCHED",
        "builder_revision": "v23_generator_service_authority_ttl_3600_parity",
        "builder_sha256": helper.sha_file(HERE),
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
        "supersedes_bundle_id": PARENT_ID,
        "service_sha256": files[SERVICE],
        "collector_sha256": files[COLLECTOR],
        "materials_generator_sha256": files[MATERIALS],
        "qualification_material_valid_minutes_min": 10,
        "qualification_material_valid_minutes_max": 60,
        "sealed_service_authority_valid_seconds_max": 3600,
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
        stage = Path(tempfile.mkdtemp(prefix=".xp-v23.", dir=OUTPUT_ROOT))
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
        "status": "PASS_IMMUTABLE_XP_V23_AUTHORITY_TTL_PARITY_SUCCESSOR",
        "bundle_id": bundle_id,
        "bundle": str(destination),
        "verified_files": len(files),
        "authority_seconds_max": 3600,
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

#!/usr/bin/env python3
"""Build immutable XP v24 with exact service and postcapture closure binding."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

import build_mysql_xp_node02_authority_ttl_successor_v23 as base


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PARENT_ID = "ba68d4a57f7ebb03f8e9c662250492aac7341757c66c56256665c41b5ee3a77e"
PARENT_MANIFEST_SHA256 = "e03f095d21876c687e9b916b8b2b5e4dca6999d5ece745d3b6261becfc5eecf0"
PARENT = ROOT / "artifacts/mysql_xp_node02_successor_v23" / PARENT_ID
OUTPUT_ROOT = ROOT / "artifacts/mysql_xp_node02_successor_v24"
POSTCAPTURE_ID = "e2b47734f1dcefab171b6ac8eefc3026ff9fac8adbe9d53832b0478084f2da98"
POSTCAPTURE_MANIFEST_SHA256 = "0a697e8be724d9d3e2c653a98051f586cffc105c1b65255dc6af7d324dc94595"
POSTCAPTURE = ROOT / "artifacts/mysql_xp_postcapture_tool_v1" / POSTCAPTURE_ID
SERVICE = "launch/mysql_xp_node02_adopted_service_v1.py"
CONTRACT = "xp_executable_launch_contract_v3.json"
MATERIALS = "test_support/mysql_xp_qualification_bootstrap_materials_v5.py"
AUTHORITY = "qualification/mysql_xp_qualification_authority_v1.py"
COLLECTOR = "qualification/mysql_distributed_live_readback_qualification_v1.py"
MATERIALIZER = "launch/mysql_xp_node02_formal10_materializer_v1.py"
READINESS_TEST = "tests/test_mysql_xp_schema_readiness_v19.py"
COMPAT_TEST = "tests/test_mysql_xp_systemd_compatibility_v11.py"
MATERIALS_TEST = "tests/test_mysql_xp_materials_successor_v20.py"
REGRESSION_TEST = "tests/test_mysql_xp_binding_closure_successor_v24.py"
BUILDER = "build_mysql_xp_node02_binding_closure_successor_v24.py"
REMEDIATION = "xp_binding_and_postcapture_closure_remediation_v1.json"
POSTCAPTURE_BINDING = "xp_postcapture_closure_binding_v1.json"


class BuildError(RuntimeError):
    pass


def replace(payload: bytes, old: str, new: str, count: int, label: str) -> bytes:
    before = payload.decode("utf-8")
    if before.count(old) != count:
        raise BuildError(f"{label} occurrence count differs")
    return before.replace(old, new).encode("utf-8")


def verify_parent():
    helper = base.base.base.base
    path = PARENT / "bundle_manifest.json"
    manifest = helper.load(path, "parent manifest")
    unsigned = dict(manifest)
    actual = unsigned.pop("bundle_id", None)
    if (
        actual != PARENT_ID
        or helper.sha_file(path) != PARENT_MANIFEST_SHA256
        or helper.sha_bytes(helper.canonical(unsigned)) != PARENT_ID
        or helper.relative_files(PARENT) != manifest.get("files")
        or manifest.get("schema_version") != "mysql_xp_node02_successor_bundle_v23"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("parent identity, closure, scope, or authority differs")
    return manifest


def verify_postcapture():
    helper = base.base.base.base
    manifest_path = POSTCAPTURE / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual = {
        path.relative_to(POSTCAPTURE).as_posix(): helper.sha_file(path)
        for path in sorted(POSTCAPTURE.rglob("*"))
        if path.is_file() and path != manifest_path
    }
    if (
        POSTCAPTURE.name != POSTCAPTURE_ID
        or helper.sha_file(manifest_path) != POSTCAPTURE_MANIFEST_SHA256
        or manifest.get("bundle_id") != POSTCAPTURE_ID
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("execution_authorized") is not False
        or manifest.get("data_plane_operations_authorized") is not False
        or manifest.get("supersession_reason")
        != "self_contained_source_runtime_and_contract_closure"
        or actual != manifest.get("payload_files")
    ):
        raise BuildError("postcapture exact self-contained closure differs")
    return manifest


def add_schema(payload: bytes, label: str) -> bytes:
    return replace(
        payload,
        '            "mysql_xp_node02_successor_bundle_v23",\n',
        '            "mysql_xp_node02_successor_bundle_v23",\n'
        '            "mysql_xp_node02_successor_bundle_v24",\n',
        1,
        label,
    )


def patch_materials(payload: bytes, service_sha: str) -> bytes:
    payload = replace(
        payload,
        "short-lived bootstrap materials for XP v19-v23.",
        "short-lived bootstrap materials for XP v19-v24.",
        1,
        "materials documentation",
    )
    payload = replace(
        payload,
        '''V22_MANIFEST_SHA256 = (
    "37863284416171c00a1a9f2e4b036455abf3667a183e3df3eb8aa2b01d36c115"
)
LEGACY_EXPIRED_MATERIAL_BUNDLE_ID = (
''',
        '''V22_MANIFEST_SHA256 = (
    "37863284416171c00a1a9f2e4b036455abf3667a183e3df3eb8aa2b01d36c115"
)
V23_BUNDLE_ID = (
    "ba68d4a57f7ebb03f8e9c662250492aac7341757c66c56256665c41b5ee3a77e"
)
V23_MANIFEST_SHA256 = (
    "e03f095d21876c687e9b916b8b2b5e4dca6999d5ece745d3b6261becfc5eecf0"
)
LEGACY_EXPIRED_MATERIAL_BUNDLE_ID = (
''',
        1,
        "v23 exact identity constants",
    )
    payload = replace(
        payload,
        '    "mysql_xp_node02_successor_bundle_v23": V22_BUNDLE_ID,\n',
        '    "mysql_xp_node02_successor_bundle_v23": V22_BUNDLE_ID,\n'
        '    "mysql_xp_node02_successor_bundle_v24": V23_BUNDLE_ID,\n',
        1,
        "v24 schema policy",
    )
    payload = replace(
        payload,
        '''    if schema == "mysql_xp_node02_successor_bundle_v22" and (
        bundle.name != V22_BUNDLE_ID
        or sha256(bundle / "bundle_manifest.json") != V22_MANIFEST_SHA256
    ):
        raise MaterialError("v22 sealed content identity differs")
    contract_path = bundle / "xp_executable_launch_contract_v3.json"
''',
        '''    if schema == "mysql_xp_node02_successor_bundle_v22" and (
        bundle.name != V22_BUNDLE_ID
        or sha256(bundle / "bundle_manifest.json") != V22_MANIFEST_SHA256
    ):
        raise MaterialError("v22 sealed content identity differs")
    if schema == "mysql_xp_node02_successor_bundle_v23" and (
        bundle.name != V23_BUNDLE_ID
        or sha256(bundle / "bundle_manifest.json") != V23_MANIFEST_SHA256
    ):
        raise MaterialError("v23 sealed content identity differs")
    contract_path = bundle / "xp_executable_launch_contract_v3.json"
''',
        1,
        "v23 exact identity check",
    )
    payload = replace(
        payload,
        '''    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(contract, dict):
        raise MaterialError("launch contract is not an object")
    runtime_path = bundle / "distributed_runtime_contract.json"
''',
        '''    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(contract, dict):
        raise MaterialError("launch contract is not an object")
    adopted = contract.get("adopted_service") or {}
    service_relative = adopted.get("path")
    service_path = bundle / str(service_relative)
    if (
        service_relative != "launch/mysql_xp_node02_adopted_service_v1.py"
        or service_path.is_symlink()
        or not service_path.is_file()
        or files.get(service_relative) != sha256(service_path)
        or adopted.get("sha256") != sha256(service_path)
        or manifest.get("service_sha256") != sha256(service_path)
    ):
        raise MaterialError("adopted-service manifest/contract/file identity differs")
    runtime_path = bundle / "distributed_runtime_contract.json"
''',
        1,
        "three-way service identity gate",
    )
    return payload


def build() -> Path:
    helper = base.base.base.base
    parent = verify_parent()
    postcapture = verify_postcapture()
    content = {relative: (PARENT / relative).read_bytes() for relative in sorted(parent["files"])}

    content[SERVICE] = replace(
        content[SERVICE],
        '        != "mysql_xp_node02_successor_bundle_v23"',
        '        != "mysql_xp_node02_successor_bundle_v24"',
        1,
        "service schema",
    )
    service_sha = helper.sha_bytes(content[SERVICE])
    contract = json.loads(content[CONTRACT].decode("utf-8"))
    contract["adopted_service"]["path"] = SERVICE
    contract["adopted_service"]["sha256"] = service_sha
    content[CONTRACT] = helper.canonical(contract)
    content[MATERIALS] = patch_materials(content[MATERIALS], service_sha)
    content[AUTHORITY] = add_schema(content[AUTHORITY], "authority schemas")
    content[MATERIALIZER] = add_schema(content[MATERIALIZER], "materializer schemas")
    content[COLLECTOR] = replace(
        content[COLLECTOR],
        '        "mysql_xp_node02_successor_bundle_v23",\n',
        '        "mysql_xp_node02_successor_bundle_v23",\n'
        '        "mysql_xp_node02_successor_bundle_v24",\n',
        1,
        "collector schemas",
    )
    content[READINESS_TEST] = replace(
        content[READINESS_TEST],
        'cases = [(v19, "mysql_xp_node02_successor_bundle_v23")]',
        'cases = [(v19, "mysql_xp_node02_successor_bundle_v24")]',
        1,
        "readiness test schema",
    )
    content[COMPAT_TEST] = replace(
        content[COMPAT_TEST],
        'manifest["schema_version"]=="mysql_xp_node02_successor_bundle_v23"',
        'manifest["schema_version"]=="mysql_xp_node02_successor_bundle_v24"',
        1,
        "compatibility test schema",
    )
    content[MATERIALS_TEST] = replace(
        content[MATERIALS_TEST],
        'assert v20_manifest["schema_version"].endswith("_v23")',
        'assert v20_manifest["schema_version"].endswith("_v24")',
        1,
        "materials test schema",
    )
    content[MATERIALS_TEST] = replace(
        content[MATERIALS_TEST],
        'assert v20_manifest["predecessor_bundle_id"] == "21d1a853548ebdff573d18a82c7a124b810b5656e3e3a330146f87d3e0b460ad"',
        'assert v20_manifest["predecessor_bundle_id"] == "' + PARENT_ID + '"',
        1,
        "materials test predecessor",
    )
    content[REGRESSION_TEST] = (ROOT / REGRESSION_TEST).read_bytes()
    content[BUILDER] = HERE.read_bytes()

    for relative, expected in sorted(postcapture["payload_files"].items()):
        source = POSTCAPTURE / relative
        payload = source.read_bytes()
        if helper.sha_bytes(payload) != expected:
            raise BuildError("postcapture payload changed: " + relative)
        content["postcapture_tool/" + relative] = payload
    content["postcapture_tool/manifest.json"] = (POSTCAPTURE / "manifest.json").read_bytes()
    content[POSTCAPTURE_BINDING] = helper.canonical({
        "schema_version": "mysql_xp_postcapture_closure_binding_v1",
        "status": "SEALED_SELF_CONTAINED_OFFLINE_TOOL_NOT_EXECUTED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "relative_root": "postcapture_tool",
        "postcapture_tool_bundle_id": POSTCAPTURE_ID,
        "postcapture_tool_manifest_sha256": POSTCAPTURE_MANIFEST_SHA256,
        "verify_source_relative_entrypoint":
            "postcapture_tool/shell/mysql_xp_postcapture_promotion_v1.py",
        "execution_authorized": False,
        "data_plane_operations_authorized": False,
    })
    content[REMEDIATION] = helper.canonical({
        "schema_version": "mysql_xp_binding_and_postcapture_closure_remediation_v1",
        "status": "SEALED_OFFLINE_SUCCESSOR_NOT_LAUNCHED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "parent_bundle_id": PARENT_ID,
        "invalidated_attempt_id":
            "xp-formal-qual-20260803T024013Z-30aee8558da14afacbb5ed88",
        "invalidated_attempt_credit": 0,
        "invalidated_offline_build_ids": [
            "52e5249136536779ea054e11b80381393754144d4f0dc60a83408b08df43c3bc",
            "41f819e9f2a99873e331b7ec56a8c53684ebd9eded9621f9b270d8a4cd1be6a8",
            "955fc5440091d2f06951476b3a365c72281ec5e129bf75af0ef0a2e96e3bf558",
            "f1b28651babde7c04f9e8f69042bc3a1f71db64539f17d4e30d6a95ff29ddfcf"
        ],
        "adopted_service_three_way_sha256": service_sha,
        "active_executable_launch_contract": CONTRACT,
        "embedded_postcapture_tool_bundle_id": POSTCAPTURE_ID,
        "postcapture_relative_path_closure": True,
        "fresh_material_attempt_required": True,
        "at1_at6_registry_still_required": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions": 0,
    })

    files = {relative: helper.sha_bytes(payload) for relative, payload in sorted(content.items())}
    identity = {key: value for key, value in parent.items() if key not in ("bundle_id", "files")}
    identity.update({
        "schema_version": "mysql_xp_node02_successor_bundle_v24",
        "status": "SEALED_EXACT_SERVICE_AND_POSTCAPTURE_CLOSURE_NOT_LAUNCHED",
        "builder_revision": "v24_exact_service_and_self_contained_postcapture_closure",
        "builder_sha256": helper.sha_file(HERE),
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
        "supersedes_bundle_id": PARENT_ID,
        "active_executable_launch_contract": CONTRACT,
        "active_executable_launch_contract_sha256": files[CONTRACT],
        "service_sha256": files[SERVICE],
        "collector_sha256": files[COLLECTOR],
        "materials_generator_sha256": files[MATERIALS],
        "embedded_postcapture_tool_bundle_id": POSTCAPTURE_ID,
        "embedded_postcapture_tool_manifest_sha256": POSTCAPTURE_MANIFEST_SHA256,
        "postcapture_tool_relative_root": "postcapture_tool",
        "invalidated_material_attempt_id":
            "xp-formal-qual-20260803T024013Z-30aee8558da14afacbb5ed88",
        "invalidated_offline_build_ids": [
            "52e5249136536779ea054e11b80381393754144d4f0dc60a83408b08df43c3bc",
            "41f819e9f2a99873e331b7ec56a8c53684ebd9eded9621f9b270d8a4cd1be6a8",
            "955fc5440091d2f06951476b3a365c72281ec5e129bf75af0ef0a2e96e3bf558",
            "f1b28651babde7c04f9e8f69042bc3a1f71db64539f17d4e30d6a95ff29ddfcf"
        ],
        "fresh_material_attempt_required": True,
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
        stage = Path(tempfile.mkdtemp(prefix=".xp-v24.", dir=OUTPUT_ROOT))
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
    if (
        unsigned.pop("bundle_id", None) != destination.name
        or helper.sha_bytes(helper.canonical(unsigned)) != destination.name
    ):
        raise BuildError("published content identity differs")
    for path in (destination, *destination.rglob("*")):
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError("published successor is mutable")
    print(json.dumps({
        "status": "PASS_IMMUTABLE_XP_V24_BINDING_CLOSURE_SUCCESSOR",
        "bundle_id": bundle_id,
        "bundle": str(destination),
        "verified_files": len(files),
        "adopted_service_sha256": files[SERVICE],
        "postcapture_tool_bundle_id": POSTCAPTURE_ID,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions": 0,
    }, sort_keys=True))
    return destination


if __name__ == "__main__":
    try:
        build()
    except (BuildError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise SystemExit("NOT_READY: " + str(exc))

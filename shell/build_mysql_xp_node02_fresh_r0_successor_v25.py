#!/usr/bin/env python3
"""Build immutable XP v25 with client-cgroup fresh-R0 evidence closure."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

import build_mysql_xp_node02_binding_closure_successor_v24 as base


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PARENT_ID = "156df4866ca2919e66086cfd0dd84f6093cbcc05716089b5a7cc70dcd2a829c4"
PARENT_MANIFEST_SHA256 = "5e47fe102a989e58e02259d53e1d4de4ad6303fcdc101d4a0e6386fd9719cd99"
PARENT = ROOT / "artifacts/mysql_xp_node02_successor_v24" / PARENT_ID
OUTPUT_ROOT = ROOT / "artifacts/mysql_xp_node02_successor_v25"
SERVICE = "launch/mysql_xp_node02_adopted_service_v1.py"
CONTRACT = "xp_executable_launch_contract_v3.json"
MATERIALS = "test_support/mysql_xp_qualification_bootstrap_materials_v5.py"
AUTHORITY = "qualification/mysql_xp_qualification_authority_v1.py"
COLLECTOR = "qualification/mysql_distributed_live_readback_qualification_v1.py"
MATERIALIZER = "launch/mysql_xp_node02_formal10_materializer_v1.py"
PRODUCER = "launch/mysql_xp_formal_evidence_producer_v1.py"
EXECUTOR = "launch/mysql_xp_fresh_r0_executor_v2.py"
FINGERPRINT = "launch/mysql_xp_fresh_r0_fingerprint_collector_v1.py"
READINESS_TEST = "tests/test_mysql_xp_schema_readiness_v19.py"
COMPAT_TEST = "tests/test_mysql_xp_systemd_compatibility_v11.py"
MATERIALS_TEST = "tests/test_mysql_xp_materials_successor_v20.py"
REGRESSION_TEST = "tests/test_mysql_xp_binding_closure_successor_v24.py"
V25_TEST = "tests/test_mysql_xp_fresh_r0_successor_v25.py"
BUILDER = "build_mysql_xp_node02_fresh_r0_successor_v25.py"
REMEDIATION = "xp_fresh_r0_client_cgroup_postbind_remediation_v1.json"


class BuildError(RuntimeError):
    pass


def replace(payload: bytes, old: str, new: str, count: int, label: str) -> bytes:
    text = payload.decode("utf-8")
    if text.count(old) != count:
        raise BuildError(label + " occurrence count differs")
    return text.replace(old, new).encode("utf-8")


def helper():
    return base.base.base.base.base


def verify_parent():
    h = helper()
    path = PARENT / "bundle_manifest.json"
    manifest = h.load(path, "parent manifest")
    unsigned = dict(manifest)
    actual = unsigned.pop("bundle_id", None)
    if (actual != PARENT_ID or h.sha_file(path) != PARENT_MANIFEST_SHA256
            or h.sha_bytes(h.canonical(unsigned)) != PARENT_ID
            or h.relative_files(PARENT) != manifest.get("files")
            or manifest.get("schema_version") != "mysql_xp_node02_successor_bundle_v24"
            or manifest.get("formal_scope") != "mysql_only"
            or manifest.get("cross_engine_comparison_prohibited") is not True
            or manifest.get("formal_launch_authorized") is not False
            or manifest.get("accepted_round_authorized") is not False):
        raise BuildError("parent identity, closure, scope, or authority differs")
    return manifest


def add_schema(payload: bytes, label: str, indent: str = "            ") -> bytes:
    old = indent + '"mysql_xp_node02_successor_bundle_v24",\n'
    return replace(payload, old, old + indent + '"mysql_xp_node02_successor_bundle_v25",\n', 1, label)


def patch_materials(payload: bytes) -> bytes:
    payload = replace(payload, "for XP v19-v24.", "for XP v19-v25.", 1,
                      "materials documentation")
    payload = replace(payload, '''V23_MANIFEST_SHA256 = (
    "e03f095d21876c687e9b916b8b2b5e4dca6999d5ece745d3b6261becfc5eecf0"
)
LEGACY_EXPIRED_MATERIAL_BUNDLE_ID = (
''', '''V23_MANIFEST_SHA256 = (
    "e03f095d21876c687e9b916b8b2b5e4dca6999d5ece745d3b6261becfc5eecf0"
)
V24_BUNDLE_ID = (
    "156df4866ca2919e66086cfd0dd84f6093cbcc05716089b5a7cc70dcd2a829c4"
)
V24_MANIFEST_SHA256 = (
    "5e47fe102a989e58e02259d53e1d4de4ad6303fcdc101d4a0e6386fd9719cd99"
)
LEGACY_EXPIRED_MATERIAL_BUNDLE_ID = (
''', 1, "v24 identity constants")
    payload = replace(payload,
        '    "mysql_xp_node02_successor_bundle_v24": V23_BUNDLE_ID,\n',
        '    "mysql_xp_node02_successor_bundle_v24": V23_BUNDLE_ID,\n'
        '    "mysql_xp_node02_successor_bundle_v25": V24_BUNDLE_ID,\n',
        1, "v25 schema policy")
    payload = replace(payload, '''    if schema == "mysql_xp_node02_successor_bundle_v23" and (
        bundle.name != V23_BUNDLE_ID
        or sha256(bundle / "bundle_manifest.json") != V23_MANIFEST_SHA256
    ):
        raise MaterialError("v23 sealed content identity differs")
    contract_path = bundle / "xp_executable_launch_contract_v3.json"
''', '''    if schema == "mysql_xp_node02_successor_bundle_v23" and (
        bundle.name != V23_BUNDLE_ID
        or sha256(bundle / "bundle_manifest.json") != V23_MANIFEST_SHA256
    ):
        raise MaterialError("v23 sealed content identity differs")
    if schema == "mysql_xp_node02_successor_bundle_v24" and (
        bundle.name != V24_BUNDLE_ID
        or sha256(bundle / "bundle_manifest.json") != V24_MANIFEST_SHA256
    ):
        raise MaterialError("v24 sealed content identity differs")
    contract_path = bundle / "xp_executable_launch_contract_v3.json"
''', 1, "v24 identity check")
    return payload


def patch_producer(payload: bytes) -> bytes:
    old = '''        ledger.get("schema_version") != "mysql_xp_raw_executor_run_v1"
        or ledger.get("status") != "PASS_RAW_RUN_ONLY"
        or ledger.get("mode") != "fresh-r0"
        or ledger.get("bundle_id") != manifest["bundle_id"]
        or ledger.get("formal_scope") != "mysql_only"
        or ledger.get("cross_engine_comparison_prohibited") is not True
        or ledger.get("formal_readback_sha256") != formal_readback_sha256
        or not isinstance(samples, list) or len(samples) != 180
        or not isinstance(evidence, dict) or evidence.get("observation_sha256") in (None, "")
        or evidence.get("counter_samples_sha256") in (None, "")
        or not _fingerprint_complete(fingerprint)
'''
    new = '''        ledger.get("schema_version") != "mysql_xp_raw_executor_run_v2"
        or ledger.get("status") != "PASS_RAW_RUN_ONLY"
        or ledger.get("mode") != "fresh-r0"
        or ledger.get("bundle_id") != manifest["bundle_id"]
        or ledger.get("formal_scope") != "mysql_only"
        or ledger.get("cross_engine_comparison_prohibited") is not True
        or ledger.get("adopted_service_sha256") != adoption_sha256
        or ledger.get("same_live_identity") != identity
        or ledger.get("trace_registry_sha256") != trace_sha256
        or ledger.get("formal_readback_sha256") != formal_readback_sha256
        or ledger.get("workload_execution") != "adopted_client_cgroup_docker_exec_v1"
        or not isinstance(samples, list) or len(samples) != 180
        or not isinstance(evidence, dict)
        or evidence.get("schema_version") != "mysql_xp_fresh_r0_fingerprint_postbind_v1"
        or evidence.get("raw_observation_sha256") in (None, "")
        or evidence.get("counter_samples_sha256") in (None, "")
        or evidence.get("binding_order") != "raw_observation_then_executor_sample_postbind"
        or not _fingerprint_complete(fingerprint)
        or not _v25_client_counter_closure(ledger, identity)
'''
    payload = replace(payload, old, new, 1, "fresh R0 executor schema closure")
    marker = '''def r0_raw_from_executor(ledger, manifest, adoption_sha256, identity,
'''
    closure = '''def _v25_client_counter_closure(ledger, identity):
    client = ledger.get("client_container_readback")
    cgroup = ledger.get("client_cgroup_readback")
    command = ledger.get("counter_command_identity")
    return (
        isinstance(client, dict)
        and client.get("container_id") == identity.get("client_container_id")
        and client.get("cpuset_cpus") == "3-11,39-47"
        and client.get("cpuset_mems") == "0"
        and client.get("memory_bytes") == 8589934592
        and client.get("memory_swap_bytes") == 8589934592
        and client.get("pids_max") == 1024
        and isinstance(cgroup, dict)
        and cgroup.get("cpu_quota_cores") == 18
        and cgroup.get("cpus_allowed_list") == "3-11,39-47"
        and cgroup.get("mems_allowed_list") == "0"
        and cgroup.get("memory_bytes") == 8589934592
        and cgroup.get("memory_swap_bytes") == 8589934592
        and cgroup.get("pids_max") == 1024
        and isinstance(command, dict)
        and command.get("kind") == "exact_adopted_client_docker_exec_stdout_v2"
        and command.get("client_container_id") == identity.get("client_container_id")
        and command.get("defaults_file_in_client")
        == "/run/mysql-xp-private/mysql-client.cnf"
        and command.get("mysql_port") == 65104
        and command.get("counter_sql_sha256") not in (None, "")
        and command.get("caller_supplied_argv") is False
        and command.get("qps_resource_input_present") is False
    )


def r0_raw_from_executor(ledger, manifest, adoption_sha256, identity,
'''
    return replace(payload, marker, closure, 1, "v25 client/counter closure")


def build() -> Path:
    h = helper(); parent = verify_parent()
    content = {name: (PARENT / name).read_bytes() for name in sorted(parent["files"])}
    content[SERVICE] = replace(content[SERVICE],
        '        != "mysql_xp_node02_successor_bundle_v24"',
        '        != "mysql_xp_node02_successor_bundle_v25"', 1, "service schema")
    service_sha = h.sha_bytes(content[SERVICE])
    contract = json.loads(content[CONTRACT].decode("utf-8"))
    contract["adopted_service"]["sha256"] = service_sha
    contract["fresh_r0_execution"] = {
        "executor": EXECUTOR,
        "fingerprint_collector": FINGERPRINT,
        "workload_execution": "adopted_client_cgroup_docker_exec_v1",
        "counter_transport": "exact_adopted_client_docker_exec_stdout_v2",
        "fingerprint_binding_order": "raw_observation_then_executor_sample_postbind",
        "caller_counter_command_prohibited": True,
        "caller_fingerprint_observation_prohibited": True,
        "qps_resource_input_prohibited": True,
    }
    content[MATERIALS] = patch_materials(content[MATERIALS])
    content[AUTHORITY] = add_schema(content[AUTHORITY], "authority schemas")
    content[MATERIALIZER] = add_schema(content[MATERIALIZER], "materializer schemas")
    content[COLLECTOR] = add_schema(content[COLLECTOR], "collector schemas", "        ")
    content[READINESS_TEST] = replace(content[READINESS_TEST],
        'cases = [(v19, "mysql_xp_node02_successor_bundle_v24")]',
        'cases = [(v19, "mysql_xp_node02_successor_bundle_v25")]', 1, "readiness test")
    content[COMPAT_TEST] = replace(content[COMPAT_TEST],
        'manifest["schema_version"]=="mysql_xp_node02_successor_bundle_v24"',
        'manifest["schema_version"]=="mysql_xp_node02_successor_bundle_v25"', 1,
        "compatibility test")
    content[MATERIALS_TEST] = replace(content[MATERIALS_TEST], 'endswith("_v24")',
                                      'endswith("_v25")', 1, "materials test schema")
    content[MATERIALS_TEST] = replace(content[MATERIALS_TEST],
        '== "ba68d4a57f7ebb03f8e9c662250492aac7341757c66c56256665c41b5ee3a77e"',
        '== "' + PARENT_ID + '"', 1, "materials test predecessor")
    content[REGRESSION_TEST] = replace(content[REGRESSION_TEST],
        '== "mysql_xp_node02_successor_bundle_v24"',
        '== "mysql_xp_node02_successor_bundle_v25"', 1, "binding regression schema")
    content[PRODUCER] = patch_producer(content[PRODUCER])
    content[EXECUTOR] = (ROOT / "shell/mysql_xp_fresh_r0_executor_v2.py").read_bytes()
    content[FINGERPRINT] = (ROOT / "shell/mysql_xp_fresh_r0_fingerprint_collector_v1.py").read_bytes()
    content[V25_TEST] = (ROOT / V25_TEST).read_bytes()
    content[BUILDER] = HERE.read_bytes()
    content[REMEDIATION] = h.canonical({
        "schema_version": "mysql_xp_fresh_r0_client_cgroup_postbind_remediation_v1",
        "status": "SEALED_OFFLINE_SUCCESSOR_NOT_LAUNCHED",
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True, "parent_bundle_id": PARENT_ID,
        "repairs": ["postbind_fingerprint_to_internal_180_samples",
                    "exact_adopted_client_counter_transport",
                    "java_workload_inside_adopted_client_resource_cgroups"],
        "formal_database_envelope": {"cpu_quota_cores": 5.3,
            "io_read_bps": 29360128, "io_write_bps": 524288},
        "capture_is_r0": False, "fresh_r0_required": True,
        "historical_accepted_round_credit": 0, "formal_launch_authorized": False,
        "accepted_round_authorized": False, "remote_actions": 0,
    })
    files = {name: h.sha_bytes(data) for name, data in sorted(content.items())}
    contract["fresh_r0_execution"]["executor_sha256"] = files[EXECUTOR]
    contract["fresh_r0_execution"]["fingerprint_collector_sha256"] = files[FINGERPRINT]
    content[CONTRACT] = h.canonical(contract)
    files[CONTRACT] = h.sha_bytes(content[CONTRACT])
    identity = {k: v for k, v in parent.items() if k not in ("bundle_id", "files")}
    identity.update({
        "schema_version": "mysql_xp_node02_successor_bundle_v25",
        "status": "SEALED_FRESH_R0_CLIENT_CGROUP_POSTBIND_NOT_LAUNCHED",
        "builder_revision": "v25_fresh_r0_client_cgroup_postbind_closure",
        "builder_sha256": h.sha_file(HERE), "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
        "supersedes_bundle_id": PARENT_ID,
        "active_executable_launch_contract_sha256": files[CONTRACT],
        "service_sha256": files[SERVICE], "collector_sha256": files[COLLECTOR],
        "materials_generator_sha256": files[MATERIALS],
        "active_fresh_r0_executor": EXECUTOR,
        "active_fresh_r0_executor_sha256": files[EXECUTOR],
        "fresh_r0_fingerprint_collector": FINGERPRINT,
        "fresh_r0_fingerprint_collector_sha256": files[FINGERPRINT],
        "fresh_r0_binding_order": "raw_observation_then_executor_sample_postbind",
        "fresh_material_attempt_required": True, "fresh_r0_required": True,
        "historical_accepted_round_credit": 0, "formal_launch_authorized": False,
        "accepted_round_authorized": False, "offline_build_only": True,
        "remote_actions": 0, "files": files,
    })
    bundle_id = h.sha_bytes(h.canonical(identity)); manifest = dict(identity, bundle_id=bundle_id)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True); destination = OUTPUT_ROOT / bundle_id
    if not destination.exists():
        stage = Path(tempfile.mkdtemp(prefix=".xp-v25.", dir=OUTPUT_ROOT))
        try:
            for name, data in sorted(content.items()):
                target = stage / name; target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            raw = h.canonical(manifest); (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                h.sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii")
            h.freeze(stage); os.replace(stage, destination)
        finally:
            if stage.exists(): shutil.rmtree(stage)
    if h.relative_files(destination) != files:
        raise BuildError("published successor closure differs")
    unsigned = dict(h.load(destination / "bundle_manifest.json", "manifest"))
    if unsigned.pop("bundle_id", None) != destination.name or h.sha_bytes(h.canonical(unsigned)) != destination.name:
        raise BuildError("published content identity differs")
    for path in (destination, *destination.rglob("*")):
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError("published successor is mutable")
    print(json.dumps({"status": "PASS_IMMUTABLE_XP_V25_FRESH_R0_SUCCESSOR",
        "bundle_id": bundle_id, "bundle": str(destination), "verified_files": len(files),
        "formal_launch_authorized": False, "accepted_round_authorized": False,
        "remote_actions": 0}, sort_keys=True))
    return destination


if __name__ == "__main__":
    try:
        build()
    except (BuildError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise SystemExit("NOT_READY: " + str(exc))

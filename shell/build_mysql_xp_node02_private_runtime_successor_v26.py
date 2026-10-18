#!/usr/bin/env python3
"""Build immutable XP v26 with private runtime credentials and file-only token."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

import build_mysql_xp_node02_fresh_r0_successor_v25 as base


ROOT = Path(__file__).resolve().parents[1]; HERE = Path(__file__).resolve()
PARENT_ID = "9783caeb6e5e610f2fa008388d7f82e9e30ee302b7a29af41d5c549a0f8da0db"
PARENT_SHA = "eda7acd3eff3f775fd924012dc77ea5d1dcf852b5d917fbdd7fce5255639d906"
PARENT = ROOT / "artifacts/mysql_xp_node02_successor_v25" / PARENT_ID
OUT = ROOT / "artifacts/mysql_xp_node02_successor_v26"
SERVICE = "launch/mysql_xp_node02_adopted_service_v1.py"
CONTRACT = "xp_executable_launch_contract_v3.json"
MATERIALS = "test_support/mysql_xp_qualification_bootstrap_materials_v5.py"
COLLECTOR = "qualification/mysql_distributed_live_readback_qualification_v1.py"
AUTHORITY = "qualification/mysql_xp_qualification_authority_v1.py"
MATERIALIZER = "launch/mysql_xp_node02_formal10_materializer_v1.py"
RAW = "launch/mysql_xp_raw_evidence_executor_v1.py"
FRESH = "launch/mysql_xp_fresh_r0_executor_v2.py"
PRODUCER = "launch/mysql_xp_formal_evidence_producer_v1.py"
BUILDER = "build_mysql_xp_node02_private_runtime_successor_v26.py"
TEST = "tests/test_mysql_xp_private_runtime_successor_v26.py"
PRIVATE_PATH = "/run/mysql-xp-private/hybench_xp_qualification.properties"


class BuildError(RuntimeError): pass


def h(): return base.helper()


def rep(data, old, new, count, label):
    text = data.decode()
    if text.count(old) != count: raise BuildError(label + " occurrence count differs")
    return text.replace(old, new).encode()


def parent():
    value = h().load(PARENT / "bundle_manifest.json", "v25 manifest")
    unsigned = dict(value); bundle_id = unsigned.pop("bundle_id", None)
    if (bundle_id != PARENT_ID or h().sha_file(PARENT / "bundle_manifest.json") != PARENT_SHA
            or h().sha_bytes(h().canonical(unsigned)) != PARENT_ID
            or h().relative_files(PARENT) != value.get("files")):
        raise BuildError("v25 parent differs")
    return value


def add_schema(data, label, indent="            "):
    old = indent + '"mysql_xp_node02_successor_bundle_v25",\n'
    return rep(data, old, old + indent + '"mysql_xp_node02_successor_bundle_v26",\n', 1, label)


def patch_materials(data):
    data = rep(data, "for XP v19-v25.", "for XP v19-v26.", 1, "materials doc")
    data = rep(data, '''V24_MANIFEST_SHA256 = (
    "5e47fe102a989e58e02259d53e1d4de4ad6303fcdc101d4a0e6386fd9719cd99"
)
LEGACY_EXPIRED_MATERIAL_BUNDLE_ID = (
''', '''V24_MANIFEST_SHA256 = (
    "5e47fe102a989e58e02259d53e1d4de4ad6303fcdc101d4a0e6386fd9719cd99"
)
V25_BUNDLE_ID = (
    "9783caeb6e5e610f2fa008388d7f82e9e30ee302b7a29af41d5c549a0f8da0db"
)
V25_MANIFEST_SHA256 = (
    "eda7acd3eff3f775fd924012dc77ea5d1dcf852b5d917fbdd7fce5255639d906"
)
LEGACY_EXPIRED_MATERIAL_BUNDLE_ID = (
''', 1, "v25 constants")
    data = rep(data, '    "mysql_xp_node02_successor_bundle_v25": V24_BUNDLE_ID,\n',
        '    "mysql_xp_node02_successor_bundle_v25": V24_BUNDLE_ID,\n'
        '    "mysql_xp_node02_successor_bundle_v26": V25_BUNDLE_ID,\n', 1, "v26 policy")
    data = rep(data, '''    if schema == "mysql_xp_node02_successor_bundle_v24" and (
        bundle.name != V24_BUNDLE_ID
        or sha256(bundle / "bundle_manifest.json") != V24_MANIFEST_SHA256
    ):
        raise MaterialError("v24 sealed content identity differs")
    contract_path = bundle / "xp_executable_launch_contract_v3.json"
''', '''    if schema == "mysql_xp_node02_successor_bundle_v24" and (
        bundle.name != V24_BUNDLE_ID
        or sha256(bundle / "bundle_manifest.json") != V24_MANIFEST_SHA256
    ):
        raise MaterialError("v24 sealed content identity differs")
    if schema == "mysql_xp_node02_successor_bundle_v25" and (
        bundle.name != V25_BUNDLE_ID
        or sha256(bundle / "bundle_manifest.json") != V25_MANIFEST_SHA256
    ):
        raise MaterialError("v25 sealed content identity differs")
    contract_path = bundle / "xp_executable_launch_contract_v3.json"
''', 1, "v25 identity")
    return data


def patch_collector(data):
    marker = '''def validate_main_agent_authority(
'''
    helper = '''def read_private_token(path):
    path = Path(path)
    if (path.is_symlink() or not path.is_file()
            or stat.S_IMODE(path.stat().st_mode) != 0o600):
        raise QualificationError("main-agent token file differs")
    token = path.read_text(encoding="utf-8").strip()
    if len(token) < 32 or len(token) > 512 or any(ch.isspace() for ch in token):
        raise QualificationError("main-agent token file content differs")
    return token


def validate_main_agent_authority(
'''
    data = rep(data, marker, helper, 1, "token reader")
    data = rep(data, '    parser.add_argument("--main-agent-token")\n',
               '    parser.add_argument("--main-agent-token-file", type=Path)\n', 1, "token CLI")
    data = rep(data, '                args.main_agent_token,\n',
               '                args.main_agent_token_file,\n', 2, "token option checks")
    data = rep(data, '            not args.main_agent_token\n',
               '            args.main_agent_token_file is None\n', 1, "token required")
    data = rep(data, '            args.main_agent_token,\n',
               '            read_private_token(args.main_agent_token_file),\n', 1, "token read")
    return data


def patch_raw_executor(data):
    data = rep(data, 'PROPERTIES = "qualification/config/hybench_xp_qualification.properties"\n',
        'PROPERTIES = "qualification/config/hybench_xp_qualification.properties"\n'
        'CLIENT_PRIVATE_PROPERTIES = "/run/mysql-xp-private/hybench_xp_qualification.properties"\n',
        1, "capture private constant")
    data = rep(data, '        CLIENT_BUNDLE + "/" + PROPERTIES,\n',
        '        CLIENT_PRIVATE_PROPERTIES,\n', 1, "capture private argv")
    data = rep(data, '\ndef _client_capture_argv(container, run_id):\n', '''
def _private_properties_readback(container):
    digest = subprocess.run(
        _docker("exec", "--user", "1008:1008", container,
                "sha256sum", CLIENT_PRIVATE_PROPERTIES),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
    )
    mode = subprocess.run(
        _docker("exec", "--user", "1008:1008", container,
                "stat", "-c", "%a", CLIENT_PRIVATE_PROPERTIES),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
    )
    fields = digest.stdout.strip().split()
    if (digest.returncode or mode.returncode or len(fields) != 2
            or fields[1] != CLIENT_PRIVATE_PROPERTIES
            or len(fields[0]) != 64 or mode.stdout.strip() != "600"):
        raise ExecutorError("client private workload properties readback differs")
    return {"path_in_client": CLIENT_PRIVATE_PROPERTIES,
            "observed_sha256": fields[0], "observed_mode": "600",
            "readback_transport": "exact_adopted_client_docker_exec_stdout_v1",
            "public_bundle_properties_prohibited": True}


def _client_capture_argv(container, run_id):
''', 1, "capture readback helper")
    data = rep(data, '''    if args.mode == "capture":
        argv, client_trace = _client_capture_argv(
            args.client_container, args.trace_run_id
        )
''', '''    private_properties_before = None
    if args.mode == "capture":
        argv, client_trace = _client_capture_argv(
            args.client_container, args.trace_run_id
        )
        private_properties_before = _private_properties_readback(args.client_container)
''', 1, "capture readback before")
    data = rep(data, '''    if rc != 0:
        raise ExecutorError("sealed HyBench workload exited nonzero")
    if trace:
''', '''    if rc != 0:
        raise ExecutorError("sealed HyBench workload exited nonzero")
    private_properties_after = (
        _private_properties_readback(args.client_container) if trace else None
    )
    if trace and private_properties_after != private_properties_before:
        raise ExecutorError("private workload properties changed during capture")
    if trace:
''', 1, "capture readback after")
    data = rep(data, '''            "capture_client_cgroup_execution": True if trace else None,
            "fingerprint_evidence": fingerprint_evidence,
''', '''            "capture_client_cgroup_execution": True if trace else None,
            "private_workload_properties": ({
                "pre_readback": private_properties_before,
                "post_readback": private_properties_after,
                "unchanged_during_workload": True,
            } if trace else None),
            "fingerprint_evidence": fingerprint_evidence,
''', 1, "capture ledger binding")
    return data


def patch_producer(data):
    data = rep(data, 'import argparse\n', 'import argparse\nimport hashlib\n',
               1, "producer hashlib import")
    data = rep(data, '\ndef capture_raw_from_executor(ledger, manifest, adoption_sha256, identity,\n', '''
def _private_properties_closure(ledger, adoption):
    properties = ledger.get("private_workload_properties")
    credentials = adoption.get("private_credentials") or {}
    expected_sha = (credentials.get("content_sha256") or {}).get(
        "hybench_xp_qualification.properties"
    )
    expected_user = "ra_xp_qual_" + hashlib.sha256(
        str(adoption.get("attempt_id") or "").encode()
    ).hexdigest()[:20]
    before = properties.get("pre_readback") if isinstance(properties, dict) else None
    after = properties.get("post_readback") if isinstance(properties, dict) else None
    return (
        credentials.get("destination") == "/run/mysql-xp-private"
        and credentials.get("qualification_config") == "hybench_xp_qualification.properties"
        and credentials.get("directory_mode") == "0700"
        and credentials.get("file_mode") == "0600"
        and credentials.get("user") == expected_user
        and isinstance(expected_sha, str) and len(expected_sha) == 64
        and isinstance(properties, dict)
        and properties.get("unchanged_during_workload") is True
        and isinstance(before, dict) and after == before
        and before.get("path_in_client")
        == "/run/mysql-xp-private/hybench_xp_qualification.properties"
        and before.get("observed_sha256") == expected_sha
        and before.get("observed_mode") == "600"
        and before.get("readback_transport")
        == "exact_adopted_client_docker_exec_stdout_v1"
        and before.get("public_bundle_properties_prohibited") is True
    )


def capture_raw_from_executor(ledger, manifest, adoption_sha256, identity, adoption,
''', 1, "producer private closure")
    data = rep(data, '''        or ledger.get("capture_client_container")
        != identity.get("client_container_id")
        or not isinstance(evidence, dict)
''', '''        or ledger.get("capture_client_container")
        != identity.get("client_container_id")
        or not _private_properties_closure(ledger, adoption)
        or not isinstance(evidence, dict)
''', 1, "capture closure check")
    data = rep(data, '''        "xapclient_independent_check_passed": True,
        "worker_future_count": len(workers), "worker_future_get_count": len(workers),
''', '''        "xapclient_independent_check_passed": True,
        "private_workload_properties_sha256":
        ledger["private_workload_properties"]["pre_readback"]["observed_sha256"],
        "worker_future_count": len(workers), "worker_future_get_count": len(workers),
''', 1, "capture projection")
    data = rep(data, 'def r0_raw_from_executor(ledger, manifest, adoption_sha256, identity,\n',
        'def r0_raw_from_executor(ledger, manifest, adoption_sha256, identity, adoption,\n',
        1, "r0 adoption signature")
    data = rep(data, '        or not _v25_client_counter_closure(ledger, identity)\n',
        '        or not _v25_client_counter_closure(ledger, identity)\n'
        '        or not _private_properties_closure(ledger, adoption)\n',
        1, "r0 closure check")
    data = rep(data, '''        "workload_pid_starttime_ticks": ledger.get("workload_pid_starttime_ticks"),
        "samples": samples, "fingerprint": fingerprint,
''', '''        "workload_pid_starttime_ticks": ledger.get("workload_pid_starttime_ticks"),
        "private_workload_properties_sha256":
        ledger["private_workload_properties"]["pre_readback"]["observed_sha256"],
        "samples": samples, "fingerprint": fingerprint,
''', 1, "r0 projection")
    data = rep(data, '''        ledger, manifest, sha256(args.adopted_service), identity, trace,
        readback["_evidence_sha256"],
''', '''        ledger, manifest, sha256(args.adopted_service), identity, adoption, trace,
        readback["_evidence_sha256"],
''', 1, "capture adoption call")
    data = rep(data, '''        ledger, manifest, sha256(args.adopted_service), identity,
        trace["_evidence_sha256"], gate["_evidence_sha256"],
''', '''        ledger, manifest, sha256(args.adopted_service), identity, adoption,
        trace["_evidence_sha256"], gate["_evidence_sha256"],
''', 1, "r0 adoption call")
    return data


def build():
    p = parent(); content = {name: (PARENT / name).read_bytes() for name in sorted(p["files"])}
    content[SERVICE] = rep(content[SERVICE],
        '        != "mysql_xp_node02_successor_bundle_v25"',
        '        != "mysql_xp_node02_successor_bundle_v26"', 1, "service schema")
    content[SERVICE] = rep(content[SERVICE], '''            "private_credentials": {
                key: value for key, value in credential.items()
                if not key.startswith("_")
            },
''', '''            "private_credentials": {
                **{key: value for key, value in credential.items()
                   if not key.startswith("_")},
                "content_sha256": dict(credential["_content_sha256"]),
            },
''', 1, "credential hash binding")
    service_sha = h().sha_bytes(content[SERVICE])
    contract = json.loads(content[CONTRACT])
    contract["adopted_service"]["sha256"] = service_sha
    contract["private_runtime_workload_properties"] = {
        "path_in_client": PRIVATE_PATH, "caller_selectable": False,
        "public_bundle_template_runtime_use_prohibited": True,
        "adoption_content_sha256_binding_required": True,
        "directory_mode": "0700", "file_mode": "0600"}
    contract["dynamic_gate_token_transport"] = {
        "kind": "private_file_only", "literal_argv_or_log_prohibited": True,
        "cli_option": "--main-agent-token-file"}
    content[MATERIALS] = patch_materials(content[MATERIALS])
    content[AUTHORITY] = add_schema(content[AUTHORITY], "authority")
    content[MATERIALIZER] = add_schema(content[MATERIALIZER], "materializer")
    content[COLLECTOR] = add_schema(content[COLLECTOR], "collector", "        ")
    content[COLLECTOR] = patch_collector(content[COLLECTOR])
    content[RAW] = patch_raw_executor(content[RAW])
    content[FRESH] = (ROOT / "shell/mysql_xp_fresh_r0_executor_v2.py").read_bytes()
    content[PRODUCER] = patch_producer(content[PRODUCER])
    # Retarget inherited root-bundle schema tests.
    for name in ("tests/test_mysql_xp_schema_readiness_v19.py",
                 "tests/test_mysql_xp_systemd_compatibility_v11.py",
                 "tests/test_mysql_xp_binding_closure_successor_v24.py",
                 "tests/test_mysql_xp_fresh_r0_successor_v25.py"):
        content[name] = content[name].replace(b"mysql_xp_node02_successor_bundle_v25",
                                              b"mysql_xp_node02_successor_bundle_v26")
    authority_test = "tests/test_mysql_xp_authority_ttl_successor_v23.py"
    content[authority_test] = rep(content[authority_test],
        '        "_password": "p", "_properties": {},\n',
        '        "_password": "p", "_properties": {},\n'
        '        "_content_sha256": {\n'
        '            "mysql-client.cnf": "a" * 64,\n'
        '            "hybench_xp_qualification.properties": "b" * 64,\n'
        '        },\n', 1, "authority empty credential hashes")
    content[authority_test] = rep(content[authority_test],
        '        "_password": "p", "_properties": public,\n',
        '        "_password": "p", "_properties": public,\n'
        '        "_content_sha256": {\n'
        '            "mysql-client.cnf": "a" * 64,\n'
        '            "hybench_xp_qualification.properties": "b" * 64,\n'
        '        },\n', 1, "authority credential hashes")
    mt = "tests/test_mysql_xp_materials_successor_v20.py"
    content[mt] = rep(content[mt], 'endswith("_v25")', 'endswith("_v26")', 1, "materials test schema")
    content[mt] = rep(content[mt],
        '== "156df4866ca2919e66086cfd0dd84f6093cbcc05716089b5a7cc70dcd2a829c4"',
        '== "' + PARENT_ID + '"', 1, "predecessor identity")
    content[TEST] = (ROOT / TEST).read_bytes(); content[BUILDER] = HERE.read_bytes()
    files = {name: h().sha_bytes(data) for name, data in sorted(content.items())}
    contract["private_runtime_workload_properties"]["fresh_executor_sha256"] = files[FRESH]
    contract["private_runtime_workload_properties"]["capture_executor_sha256"] = files[RAW]
    contract["dynamic_gate_token_transport"]["collector_sha256"] = files[COLLECTOR]
    content[CONTRACT] = h().canonical(contract); files[CONTRACT] = h().sha_bytes(content[CONTRACT])
    identity = {k: v for k, v in p.items() if k not in ("bundle_id", "files")}
    identity.update({"schema_version": "mysql_xp_node02_successor_bundle_v26",
        "status": "SEALED_PRIVATE_RUNTIME_AND_FILE_TOKEN_NOT_LAUNCHED",
        "builder_revision": "v26_private_runtime_properties_and_file_only_token",
        "builder_sha256": h().sha_file(HERE), "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_SHA, "supersedes_bundle_id": PARENT_ID,
        "service_sha256": files[SERVICE], "collector_sha256": files[COLLECTOR],
        "materials_generator_sha256": files[MATERIALS],
        "active_executable_launch_contract_sha256": files[CONTRACT],
        "active_fresh_r0_executor_sha256": files[FRESH],
        "private_workload_properties_path": PRIVATE_PATH,
        "dynamic_gate_token_transport": "private_file_only",
        "historical_accepted_round_credit": 0, "formal_launch_authorized": False,
        "accepted_round_authorized": False, "offline_build_only": True,
        "remote_actions": 0, "files": files})
    bundle_id = h().sha_bytes(h().canonical(identity)); manifest = dict(identity, bundle_id=bundle_id)
    OUT.mkdir(parents=True, exist_ok=True); dest = OUT / bundle_id
    if not dest.exists():
        stage = Path(tempfile.mkdtemp(prefix=".xp-v26.", dir=OUT))
        try:
            for name, data in sorted(content.items()):
                path = stage / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
            raw = h().canonical(manifest); (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(h().sha_bytes(raw) + "  bundle_manifest.json\n")
            h().freeze(stage); os.replace(stage, dest)
        finally:
            if stage.exists(): shutil.rmtree(stage)
    if h().relative_files(dest) != files: raise BuildError("v26 closure differs")
    for path in (dest, *dest.rglob("*")):
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222: raise BuildError("v26 mutable")
    print(json.dumps({"status":"PASS_IMMUTABLE_XP_V26","bundle_id":bundle_id,
        "bundle":str(dest),"files":len(files),"formal_launch_authorized":False,
        "accepted_round_authorized":False,"remote_actions":0}, sort_keys=True))
    return dest


if __name__ == "__main__":
    try: build()
    except (BuildError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise SystemExit("NOT_READY: " + str(exc))

#!/usr/bin/env python3
"""Seal an compute00-only XP successor; it deliberately cannot launch without SSD authority."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import stat


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "168f1505fa89a80dee9fb39533300b7e1e1a4ecce6e9d60af2704072e4cb67ac"
PARENT_SHA = "02c85d1bc350fbd8a95a0e5007581fd16721402c6faec59f48ee89dd2151ca26"
PARENT = ROOT / "artifacts/mysql_xp_formal10_launch_v9" / PARENT_ID
OUTPUT = ROOT / "artifacts/mysql_xp_compute00_successor_v10"
SCHEMA = "mysql_xp_compute00_successor_bundle_v10"
RUNNER = "predecessor/predecessor/tests/run_sealed_bundle_tests.py"
RUNTIME_MANIFEST = "predecessor/predecessor/manifest.json"
HOST_AUTHORITY = "authority/compute00_host_binding_v1.json"
STORAGE_AUTHORITY = "authority/compute00_xp_storage_authority_required_v1.json"
LAUNCH_CONTRACT = "xp_compute00_executable_launch_contract_v4.json"
TEST = "tests/test_mysql_xp_compute00_successor_v10.py"


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def verify_parent():
    manifest_path = PARENT / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        PARENT.name != PARENT_ID
        or manifest.get("bundle_id") != PARENT_ID
        or digest(manifest_path) != PARENT_SHA
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or not isinstance(manifest.get("files"), dict)
    ):
        raise BuildError("v9 parent identity/scope differs")
    for relative, expected in manifest["files"].items():
        path = PARENT / relative
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise BuildError("v9 parent closure differs: " + relative)
    return manifest


def host_authority():
    return {
        "schema_version": "mysql_xp_compute00_host_binding_v1",
        "host_id": "compute00",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "mysql_xp_cpuset": "184-199,440-455",
        "pg_cpuset_excluded": "32-51,288-307",
        "smt_sibling_sample": {"184": "184,440", "440": "184,440"},
        "cpu_overlap_with_pg": False,
        "formal_envelope": {
            "database_cpu_quota_cores": 5.3,
            "database_io_read_bps": 29360128,
            "database_io_write_bps": 524288,
            "graceful_quiescence_seconds": 120,
        },
        "capture_only_envelope": {
            "io_bps_per_direction": 1205862400,
            "is_formal_r0": False,
            "accepted_round_credit": 0,
        },
        "algorithm_c": {
            "active_parameter_count": 7,
            "required_nonempty_subsets": 127,
            "fixed_real_replay_top_k": 5,
            "required_trace_transactions": ["AT1", "AT2", "AT3", "AT4", "AT5", "AT6"],
            "sealed_registry_required_before_fresh_r0": True,
            "qps_resource_input_prohibited": True,
        },
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


def storage_authority():
    return {
        "schema_version": "mysql_xp_compute00_storage_authority_required_v1",
        "status": "FAIL_CLOSED_STORAGE_UNRESOLVED",
        "host_id": "compute00",
        "observed_block_mapping": {
            "/dev/nvme0n1": {"major_minor": "259:0", "mounted_partition": "/dev/nvme0n1p4@259:4", "mount": "/", "eligible": False, "reason": "rootfs_not_proven_dedicated"},
            "/dev/nvme1n1": {"major_minor": "259:6", "mount": "/disk1", "eligible": False, "reason": "no_live_pg_and_co_resident_device_readback_in_this_offline_bundle"},
            "/dev/nvme2n1": {"major_minor": "259:5", "mount": "/disk2", "eligible": False, "reason": "no_live_pg_and_co_resident_device_readback_in_this_offline_bundle"},
        },
        "prohibited": ["/dev/nvme0n1p1", "/mnt/mysql_nvme0n1p1", "any_unreadbacked_or_shared_pg_device"],
        "selection_rule": "main_agent_must_bind_one_non_pg_exclusive_ssd_by_live_major_minor_mount_graphdriver_and_interference_readback",
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


def launch_contract():
    return {
        "schema_version": "mysql_xp_compute00_executable_launch_contract_v4",
        "host_id": "compute00",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "host_authority": HOST_AUTHORITY,
        "storage_authority": STORAGE_AUTHORITY,
        "formal_envelope": host_authority()["formal_envelope"],
        "capture_only_envelope": host_authority()["capture_only_envelope"],
        "formal_launch_requires": [
            "selected_ssd_authority_resealed_by_main_agent",
            "live_pg_device_noninterference_readback",
            "AT1_AT6_real_trace_registry_sealed",
            "fresh_formal_R0",
            "127_candidates_and_fixed_global_Top5_real_replay",
        ],
        "inherited_node02_launch_chain_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


def patched_runtime(payload):
    source = payload.decode("utf-8")
    old = 'manifest.get("bundle_id") != BUNDLE.name'
    new = '(not isinstance(manifest.get("bundle_id"), str) or len(manifest.get("bundle_id")) != 64)'
    if source.count(old) != 1:
        raise BuildError("runtime runner identity patch cardinality differs")
    return source.replace(old, new).encode("utf-8")


def patched_runtime_manifest(payload, runner):
    value = json.loads(payload.decode("utf-8"))
    files = value.get("payload_files")
    if not isinstance(files, dict) or RUNNER.split("predecessor/predecessor/", 1)[1] not in files:
        raise BuildError("runtime manifest lacks sealed runner")
    files["tests/run_sealed_bundle_tests.py"] = sha(runner)
    return canonical(value)


def test_source():
    return b'''from __future__ import annotations\n\nimport hashlib\nimport json\nfrom pathlib import Path\n\nROOT = Path(__file__).resolve().parents[1]\n\ndef digest(path):\n    return hashlib.sha256(path.read_bytes()).hexdigest()\n\ndef test_compute00_contract_and_runtime_runner_are_sealed_fail_closed():\n    manifest = json.loads((ROOT / "bundle_manifest.json").read_text())\n    assert manifest["bundle_id"] == ROOT.name\n    assert manifest["formal_launch_authorized"] is False\n    assert manifest["accepted_round_authorized"] is False\n    for rel, expected in manifest["files"].items():\n        assert digest(ROOT / rel) == expected\n    host = json.loads((ROOT / "authority/compute00_host_binding_v1.json").read_text())\n    storage = json.loads((ROOT / "authority/compute00_xp_storage_authority_required_v1.json").read_text())\n    assert host["mysql_xp_cpuset"] == "184-199,440-455"\n    assert host["formal_envelope"]["database_cpu_quota_cores"] == 5.3\n    assert host["formal_envelope"]["database_io_read_bps"] == 29360128\n    assert host["formal_envelope"]["database_io_write_bps"] == 524288\n    assert host["capture_only_envelope"]["io_bps_per_direction"] == 1205862400\n    assert host["algorithm_c"]["required_nonempty_subsets"] == 127\n    assert storage["status"] == "FAIL_CLOSED_STORAGE_UNRESOLVED"\n    runner = (ROOT / "predecessor/predecessor/tests/run_sealed_bundle_tests.py").read_text()\n    assert 'manifest.get("bundle_id") != BUNDLE.name' not in runner\n'''


def freeze(root):
    for path in sorted(Path(root).rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink prohibited")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(Path(root).stat().st_mode) & ~0o222)


def verify_bundle(path, expected_id=None):
    manifest_path = path / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    identity = dict(manifest); identity.pop("bundle_id", None)
    actual = {}
    for candidate in path.rglob("*"):
        if candidate.is_symlink(): raise BuildError("symlink in successor")
        if candidate.is_file():
            relative = candidate.relative_to(path).as_posix()
            if relative not in {"bundle_manifest.json", "bundle_manifest.json.sha256"}:
                actual[relative] = digest(candidate)
    if (
        path.name != (expected_id or manifest.get("bundle_id"))
        or manifest.get("bundle_id") != path.name
        or manifest.get("schema_version") != SCHEMA
        or sha(canonical(identity)) != path.name
        or actual != manifest.get("files")
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or manifest.get("storage_authority_status") != "FAIL_CLOSED_STORAGE_UNRESOLVED"
    ): raise BuildError("compute00 successor identity differs")
    return manifest


def publish():
    parent = verify_parent()
    payload = {relative: (PARENT / relative).read_bytes() for relative in parent["files"]}
    runner = patched_runtime(payload[RUNNER])
    payload[RUNNER] = runner
    payload[RUNTIME_MANIFEST] = patched_runtime_manifest(payload[RUNTIME_MANIFEST], runner)
    payload[HOST_AUTHORITY] = canonical(host_authority())
    payload[STORAGE_AUTHORITY] = canonical(storage_authority())
    payload[LAUNCH_CONTRACT] = canonical(launch_contract())
    payload[TEST] = test_source()
    files = {relative: sha(value) for relative, value in sorted(payload.items())}
    identity = {key: value for key, value in parent.items() if key not in {"bundle_id", "files"}}
    identity.update({
        "schema_version": SCHEMA, "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_SHA, "host_id": "compute00",
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "formal_envelope": host_authority()["formal_envelope"],
        "capture_only_envelope": host_authority()["capture_only_envelope"],
        "algorithm_c_contract": host_authority()["algorithm_c"],
        "storage_authority_status": "FAIL_CLOSED_STORAGE_UNRESOLVED",
        "formal_launch_authorized": False, "accepted_round_authorized": False,
        "builder_revision": 2,
        "offline_build_only": True, "remote_actions": 0, "files": files,
    })
    bundle_id = sha(canonical(identity)); manifest = {**identity, "bundle_id": bundle_id}
    OUTPUT.mkdir(parents=True, exist_ok=True); final = OUTPUT / bundle_id
    if final.exists() or final.is_symlink(): freeze(final); verify_bundle(final, bundle_id); return final
    stage = OUTPUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6)); stage.mkdir(mode=0o700)
    for relative, value in payload.items():
        target = stage / relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(value); os.chmod(target, 0o444)
    raw = canonical(manifest); (stage / "bundle_manifest.json").write_bytes(raw); os.chmod(stage / "bundle_manifest.json", 0o444)
    (stage / "bundle_manifest.json.sha256").write_text("%s  bundle_manifest.json\\n" % sha(raw), encoding="ascii"); os.chmod(stage / "bundle_manifest.json.sha256", 0o444)
    os.rename(stage, final); freeze(final); verify_bundle(final, bundle_id); return final


if __name__ == "__main__":
    target = publish()
    print(json.dumps({"status":"SEALED_LOCAL_ONLY_STORAGE_UNRESOLVED", "bundle":str(target), "bundle_id":target.name, "bundle_manifest_sha256":digest(target / "bundle_manifest.json"), "formal_launch_authorized":False, "accepted_round_authorized":False}, sort_keys=True))

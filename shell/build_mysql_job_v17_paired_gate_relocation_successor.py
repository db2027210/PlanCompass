#!/usr/bin/env python3
"""Build the offline JOB v17 paired-gate and relocation successor."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "1a072b532de3af39bdc483aa0a5918cfe5fcc8a3e2a84a5c4c7c04ee834e650f"
PARENT_SHA = "1492632d598a0d56a0eba807c830713e908f55b9984f4fda0340446f422bfec6"
PARENT = ROOT / "artifacts/mysql_job_node04_live_disjoint_successor_v16" / PARENT_ID
OUT = ROOT / "artifacts/mysql_job_v17_paired_gate_relocation_successor"
SCHEMA = "mysql_job_v17_paired_gate_relocation_successor_bundle_v1"
OLD_SCHEMA = "mysql_job_node04_live_disjoint_successor_bundle_v16"
CONTRACT = "authority/mysql_algorithm_c_distributed_four_scene_successor_contract_v2_20260729.json"
RELOCATION = "authority/mysql_job_v17_node02_to_node04_tree_relocation_transaction_v1.json"
BUILDER = "builder/build_mysql_job_v17_paired_gate_relocation_successor.py"
SOURCES = {
    "controller/mysql_job_v17_qualification_paired_authority_v1.py":
        ROOT / "controller/mysql_job_v17_qualification_paired_authority_v1.py",
    "qualification/mysql_job_v17_three_pair_gate_aggregate_v1.py":
        ROOT / "qualification/mysql_job_v17_three_pair_gate_aggregate_v1.py",
    "qualification/mysql_job_v17_portable_tree_inventory_v1.py":
        ROOT / "qualification/mysql_job_v17_portable_tree_inventory_v1.py",
    "qualification/mysql_job_v17_atomic_tree_publish_v1.py":
        ROOT / "qualification/mysql_job_v17_atomic_tree_publish_v1.py",
    "tests/test_mysql_job_v17_producers.py": ROOT / "tests/test_mysql_job_v17_producers.py",
}


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha(value):
    return hashlib.sha256(value).hexdigest()


def digest(path):
    return sha(Path(path).read_bytes())


def verify_parent():
    path = PARENT / "bundle_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    unsigned = dict(manifest); unsigned.pop("bundle_id", None)
    if (manifest.get("bundle_id") != PARENT_ID or digest(path) != PARENT_SHA
            or sha(canonical(unsigned)) != PARENT_ID
            or manifest.get("schema_version") != OLD_SCHEMA):
        raise BuildError("JOB v16 parent identity differs")
    for relative, expected in manifest.get("files", {}).items():
        source = PARENT / relative
        if source.is_symlink() or not source.is_file() or digest(source) != expected:
            raise BuildError("JOB v16 closure differs: " + relative)
    return manifest


def patch_contract(payload):
    value = json.loads(payload.decode("utf-8"))
    policy = value["same_host_parallel_policy"]
    policy["sealed_qualification_paired_authority"] = {
        "schema_version": "mysql_job_v17_sealed_paired_authority_contract_v1",
        "producer": "controller/mysql_job_v17_qualification_paired_authority_v1.py",
        "mode": "qualification_paired",
        "paired_peer_workload_binding_required": True,
        "fresh_own_and_peer_live_readback_required": True,
        "sealed_unexpired_solo_authority_required": True,
        "unique_authorization_per_attempt_required": True,
        "qualification_seconds": 180,
        "formal_launch_authorized": False,
        "qualification_credit": 0,
    }
    policy["three_pair_dynamic_gate_aggregate"] = {
        "schema_version": "mysql_job_v17_three_pair_gate_aggregate_contract_v1",
        "producer": "qualification/mysql_job_v17_three_pair_gate_aggregate_v1.py",
        "required_pairs": [["job_memory", "tpch_memory"],
                           ["job_memory", "tpcc_cpu"],
                           ["tpch_memory", "tpcc_cpu"]],
        "each_direction_seconds": 180,
        "minimum_samples_per_stream": 181,
        "all_three_fresh_pass_required": True,
        "aggregate_is_launch_authority": False,
        "on_missing_stale_or_failed": "BLOCK_CONCURRENT_LAUNCH",
        "serial_fallback_authorized": False,
    }
    value["status"] = "OFFLINE_V17_PAIRED_AUTHORITY_AND_THREE_GATE_CONTRACT_NOT_LAUNCHED"
    return canonical(value)


def patch_runtime(payload):
    value = json.loads(payload.decode("utf-8"))
    value["v17_debug_qualification_entrypoints"] = {
        "paired_authority_producer": "controller/mysql_job_v17_qualification_paired_authority_v1.py",
        "three_pair_gate_aggregate_producer": "qualification/mysql_job_v17_three_pair_gate_aggregate_v1.py",
        "portable_tree_inventory_producer": "qualification/mysql_job_v17_portable_tree_inventory_v1.py",
        "atomic_tree_publisher": "qualification/mysql_job_v17_atomic_tree_publish_v1.py",
        "formal_launch_authorized": False,
    }
    return canonical(value)


def relocation_contract():
    runtime_bytes = 1378768600
    historical_database_bytes = 10943286975
    return {
        "schema_version": "mysql_job_v17_node02_to_node04_tree_relocation_transaction_v1",
        "status": "SEALED_PLAN_NOT_EXECUTED_SOURCE_INVENTORY_REQUIRED",
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True, "remote_actions": 0,
        "source_host": {"hostname": "node02", "address": "192.0.2.4"},
        "destination_host": {"hostname": "node04", "address": "192.0.2.7",
                             "device": "/dev/nvme2n1"},
        "trees": {
            "template": {
                "source": "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/template",
                "destination": "/mnt/nvme2n1/anonymous/resource_adaptive_mysql/job/template",
                "historical_database_size_bytes": historical_database_bytes,
                "historical_size_is_current_transfer_size": False,
                "evidence_sha256": "ce4fc5f5396904b82e4ae185f4f2a3d006f2cca502ac34cbc731e48f0a73acce",
                "full_checksum_manifest_sha256": "e9e0190c9bdffc191a02e8df500d8be217eb494b9921222ab794822736852933",
                "database_manifest_sha256": "aa430eceaa6fcc710769ae25ece93458fa0544cf2bc0b9600a0034982652a552",
                "ibd_checksum_manifest_sha256": "7ff455b6f0591207ea3cd259d12cf5306b623ebca18adb345c382aca2a7840bc",
            },
            "runtime": {
                "source": "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/runtime_algorithm_c_successor_20260729/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89",
                "destination": "/mnt/nvme2n1/anonymous/resource_adaptive_mysql/job/runtime_algorithm_c_successor_20260729/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89",
                "authoritative_entry_count": 40852,
                "authoritative_logical_file_bytes": runtime_bytes,
                "tree_content_sha256": "3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89",
                "python_executable_sha256": "05bd6d6d900bd990b8c00d17df3b3e1425d6c32f0408f07cd29bc495ac4aff39",
            },
        },
        "historical_database_plus_runtime_bytes": historical_database_bytes + runtime_bytes,
        "historical_sum_is_exact_current_transfer_size": False,
        "exact_size_authority": {
            "producer": "qualification/mysql_job_v17_portable_tree_inventory_v1.py",
            "required_fields": ["entry_count", "logical_unique_file_bytes",
                                "observed_allocated_bytes_unique_inodes"],
            "fresh_source_pre_copy_and_post_copy_inventory_required": True,
            "pre_post_source_identity_exact_match_required": True,
        },
        "copy": {"source_read_only": True, "direct_sibling_stage": True,
                 "preserve_numeric_owner_mode_mtime_hardlinks_symlinks_xattrs_acls": True,
                 "destination_final_must_be_absent": True,
                 "source_stage_exact_identity_required": True,
                 "publication": "renameat2_RENAME_NOREPLACE_then_parent_fsync"},
        "publication_producer": "qualification/mysql_job_v17_atomic_tree_publish_v1.py",
        "formal_launch_authorized": False, "accepted_round_authorized": False,
        "qualification_credit": 0, "r0_credit": 0, "candidate_credit": 0,
        "top5_replay_credit": 0, "accepted_rounds_credit": 0,
    }


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink in JOB v17 bundle")
        path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
    root.chmod(stat.S_IMODE(root.stat().st_mode) & ~0o222)


def main():
    parent = verify_parent()
    payload = {relative: (PARENT / relative).read_bytes()
               for relative in parent["files"] if relative != "closure.json"}
    modes = {relative: stat.S_IMODE((PARENT / relative).stat().st_mode)
             for relative in payload}
    old_contract_sha = sha(payload[CONTRACT])
    payload[CONTRACT] = patch_contract(payload[CONTRACT])
    payload["distributed_runtime_contract.json"] = patch_runtime(
        payload["distributed_runtime_contract.json"])
    new_contract_sha = sha(payload[CONTRACT])
    for relative in ("controller/render_mysql_job_v32_bootstrap_spec_tmpfs_v2.py",
                     "qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py"):
        payload[relative] = payload[relative].replace(
            old_contract_sha.encode("ascii"), new_contract_sha.encode("ascii"))
    for relative in list(payload):
        payload[relative] = payload[relative].replace(
            OLD_SCHEMA.encode("ascii"), SCHEMA.encode("ascii"))
    for relative, source in SOURCES.items():
        payload[relative] = source.read_bytes()
        modes[relative] = 0o555 if relative.endswith(".py") and not relative.startswith("tests/") else 0o444
    transaction = relocation_contract()
    payload[RELOCATION] = canonical(transaction); modes[RELOCATION] = 0o444
    payload[BUILDER] = Path(__file__).read_bytes(); modes[BUILDER] = 0o555
    closure = json.loads((PARENT / "closure.json").read_text(encoding="utf-8"))
    closure.update({"schema_version": "mysql_job_module_closure_v17",
                    "predecessor_bundle_id": PARENT_ID,
                    "predecessor_bundle_manifest_sha256": PARENT_SHA,
                    "relocation_transaction": transaction})
    closure["files"] = {relative: sha(data) for relative, data in sorted(payload.items())}
    payload["closure.json"] = canonical(closure); modes["closure.json"] = 0o444
    identity = dict(parent); identity.pop("bundle_id", None)
    identity.update({
        "schema_version": SCHEMA,
        "status": "SEALED_JOB_V17_PAIRED_GATE_RELOCATION_NOT_LAUNCHED",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_bundle_manifest_sha256": PARENT_SHA,
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True, "remote_actions": 0,
        "formal_launch_authorized": False, "formal10_authorized": False,
        "accepted_round_authorized": False, "qualification_credit": 0,
        "r0_credit": 0, "candidate_credit": 0, "top5_replay_credit": 0,
        "accepted_rounds_credit": 0,
        "paired_authority_producer": "controller/mysql_job_v17_qualification_paired_authority_v1.py",
        "three_pair_gate_aggregate_producer": "qualification/mysql_job_v17_three_pair_gate_aggregate_v1.py",
        "relocation_transaction_contract": RELOCATION,
        "relocation_transaction_contract_sha256": sha(payload[RELOCATION]),
        "distributed_runtime_contract_sha256": sha(payload["distributed_runtime_contract.json"]),
        "serial_fallback_authorized": False,
        "files": {relative: sha(data) for relative, data in sorted(payload.items())},
    })
    bundle_id = sha(canonical(identity)); manifest = dict(identity, bundle_id=bundle_id)
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / bundle_id
    if destination.exists():
        if json.loads((destination / "bundle_manifest.json").read_text()) != manifest:
            raise BuildError("existing JOB v17 identity differs")
        print(json.dumps({"status": manifest["status"], "bundle": str(destination),
                          "bundle_id": bundle_id,
                          "manifest_sha256": digest(destination / "bundle_manifest.json")}, sort_keys=True))
        return
    stage = OUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6))
    stage.mkdir(mode=0o700)
    try:
        for relative, data in payload.items():
            path = stage / relative; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data); path.chmod(modes.get(relative, 0o444) & ~0o222)
        raw = canonical(manifest); (stage / "bundle_manifest.json").write_bytes(raw)
        (stage / "bundle_manifest.json.sha256").write_text(
            sha(raw) + "  bundle_manifest.json\n", encoding="ascii")
        freeze(stage); os.rename(stage, destination)
    except BaseException:
        if stage.exists(): shutil.rmtree(stage)
        raise
    print(json.dumps({"status": manifest["status"], "bundle": str(destination),
                      "bundle_id": bundle_id,
                      "manifest_sha256": digest(destination / "bundle_manifest.json")}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (BuildError, OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit("NOT_READY: %s" % exc)

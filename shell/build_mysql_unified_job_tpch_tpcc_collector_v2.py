#!/usr/bin/env python3
"""Rebind the one-version JOB/TPC-H/TPC-C collector to disjoint JOB v18."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import stat


ROOT = Path(__file__).resolve().parents[1]
BASE_BUILDER = ROOT / "shell/build_mysql_unified_job_tpch_tpcc_collector_v1.py"
spec = importlib.util.spec_from_file_location("unified_v1_builder", BASE_BUILDER)
if spec is None or spec.loader is None:
    raise SystemExit("NOT_READY: cannot load verified v1 builder")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

PARENT_ID = "347c8a19bea3917147989ea2f545d98a71ce8b3c910d09ebb9a6bcc9eddf21f9"
PARENT_SHA = "da49ff027d4420c9e440375d8d62ad2aaac6abc532f9903542ca94d76464560b"
PARENT = ROOT / "artifacts/mysql_unified_job_tpch_tpcc_collector_v1" / PARENT_ID
JOB_ID = "44c6bda398c8eee5ba5fdf4bef95947cb497b27a3a161448fc084e344baa40ea"
JOB_SHA = "6d81666dd710a96af1fa8a9fb81493a348a5975ccf6e3ae980c9f6d33a597b8d"
JOB = ROOT / "artifacts/mysql_job_node04_disjoint_successor_v18" / JOB_ID
OUT = ROOT / "artifacts/mysql_unified_job_tpch_tpcc_collector_v2"
SCHEMA = "mysql_unified_job_tpch_tpcc_collector_bundle_v2"
JOB_SCHEMA = "mysql_job_node04_disjoint_successor_bundle_v18"
IDENTITY = "authority/mysql_unified_collector_identity_v2.json"
INVALIDATION = "authority/mysql_unified_collector_v1_job_v17_invalidation_20260803.json"
STALE_V2_INVALIDATION = "authority/mysql_unified_collector_v2_stale_job_contract_invalidation_20260803.json"
BUILDER = "builder/build_mysql_unified_job_tpch_tpcc_collector_v2.py"
SOLO = "controller/mysql_unified_qualification_solo_authority_v1.py"
PROCESS_INVENTORY = "qualification/mysql_unified_fresh_process_inventory_v1.py"


class BuildError(RuntimeError):
    pass


def replace_once(raw: bytes, old: bytes, new: bytes, label: str) -> bytes:
    if raw.count(old) != 1:
        raise BuildError(label + " anchor differs")
    return raw.replace(old, new)


def main():
    parent = base.verify(PARENT, PARENT_ID, PARENT_SHA,
                         "mysql_unified_job_tpch_tpcc_collector_bundle_v1")
    job = base.verify(JOB, JOB_ID, JOB_SHA, JOB_SCHEMA)
    excluded = {
        "authority/mysql_unified_collector_identity_v1.json",
        "builder/build_mysql_unified_job_tpch_tpcc_collector_v1.py",
        "tests/test_mysql_unified_job_tpch_tpcc_collector_v1.py",
        "tests/test_mysql_unified_collector_v1_behavior.py",
    }
    payload = {relative: (PARENT / relative).read_bytes()
               for relative in parent["files"] if relative not in excluded}
    modes = {relative: stat.S_IMODE((PARENT / relative).stat().st_mode)
             for relative in payload}

    entry = payload[base.ENTRY]
    entry = replace_once(
        entry,
        b'JOB_SCHEMA="mysql_job_v17_paired_gate_relocation_successor_bundle_v1"',
        b'JOB_SCHEMA="mysql_job_node04_disjoint_successor_bundle_v18"',
        "unified dispatch JOB v18",
    )
    payload[base.ENTRY] = entry
    payload[base.JOB_CORE] = base.patch_job_core(
        (JOB / "qualification/mysql_job_distributed_live_readback_qualification_v2.py").read_bytes())
    payload[base.JOB_ADAPTER] = (
        JOB / "qualification/mysql_distributed_workload_metric_adapter_v1.py").read_bytes()

    paired = (JOB / "controller/mysql_job_v18_qualification_paired_authority_v1.py").read_bytes()
    paired = paired.replace(b"mysql_job_v18_qualification_paired_authority_v1",
                            b"mysql_job_v17_qualification_paired_authority_v1")
    payload[base.PAIRED] = base.patch_paired(paired)
    aggregate = (JOB / "qualification/mysql_job_v18_three_pair_gate_aggregate_v1.py").read_bytes()
    aggregate = aggregate.replace(b"mysql_job_v18_three_pair_gate_aggregate_v1",
                                  b"mysql_job_v17_three_pair_gate_aggregate_v1")
    payload[base.AGGREGATE] = base.patch_aggregate(aggregate)
    payload[SOLO] = (ROOT / SOLO).read_bytes()
    payload[PROCESS_INVENTORY] = (ROOT / PROCESS_INVENTORY).read_bytes()

    identity = {
        "schema_version": "mysql_unified_job_tpch_tpcc_collector_identity_v2",
        "status": "SEALED_V18_REBIND_FRESH_LIVE_ZERO_CONFLICT_REQUIRED",
        "scenarios": ["job_memory", "tpch_memory", "tpcc_cpu"],
        "collector_identity_at_runtime": "sha256_of_this_bundle_manifest_raw_bytes",
        "parent_unified_bundle_id": PARENT_ID,
        "parent_unified_manifest_sha256": PARENT_SHA,
        "job_predecessor_bundle_id": JOB_ID,
        "job_predecessor_manifest_sha256": JOB_SHA,
        "job_static_cpuset": {
            "database": "25-27,61-63", "client": "28-31,64-67",
            "daemon": "16-17,52-53",
        },
        "tpch_observed_cpuset": {
            "database": "18-20,54-56", "client": "0-7,36-43",
            "daemon": "32-33,68-69",
        },
        "tpcc_observed_cpuset": {
            "database": "21-24,57-60", "client": "8-15,44-51",
            "daemon": "34-35,70-71",
        },
        "static_three_scene_disjoint": True,
        "fresh_current_full_process_zero_conflict_required": True,
        "fresh_current_full_process_zero_conflict_verified": False,
        "all_three_pairwise_dynamic_gates_required": True,
        "same_collector_sha_gate_preserved": True,
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True, "formal_launch_authorized": False,
        "qualification_credit": 0,
    }
    invalidation = {
        "schema_version": "mysql_unified_collector_binding_invalidation_v1",
        "status": "INVALIDATED_BY_LATEST_TPCH_CPU_READBACK",
        "invalidated_bundle_id": PARENT_ID,
        "invalidated_job_bundle_schema": "mysql_job_v17_paired_gate_relocation_successor_bundle_v1",
        "reason": "v17 JOB database cpuset 18-20,54-56 overlaps current TPC-H database",
        "replacement_job_bundle_id": JOB_ID,
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "formal_launch_authorized": False, "qualification_credit": 0,
    }
    payload[IDENTITY] = base.canonical(identity)
    payload[INVALIDATION] = base.canonical(invalidation)
    stale_v2 = {
        "schema_version": "mysql_unified_collector_v2_stale_job_contract_invalidation_v1",
        "status": "INVALIDATED_BY_CORRECTED_JOB_V18_RELOCATION_CONTRACT",
        "invalidated_bundle_id": "dd34adb64d4394fd3f696259a77c898ef122ec113da59faf0278b7ada4059980",
        "invalidated_manifest_sha256": "0ddd58f3289404c1d2878f0cadf55787be61d3315b47efa4668ddc1aa9287dfd",
        "reason": "collector bound the superseded JOB v18 identity with a stale embedded relocation contract",
        "launch_prohibited": True,
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True, "formal_launch_authorized": False,
        "qualification_credit": 0,
    }
    payload[STALE_V2_INVALIDATION] = base.canonical(stale_v2)

    test = (PARENT / "tests/test_mysql_unified_job_tpch_tpcc_collector_v1.py").read_bytes()
    test = test.replace(b"mysql_unified_job_tpch_tpcc_collector_bundle_v1", SCHEMA.encode())
    test = test.replace(b"mysql_job_v17_paired_gate_relocation_successor_bundle_v1",
                        JOB_SCHEMA.encode())
    test += (b'\ndef test_unique_solo_materializer_is_in_exact_closure():\n'
             b' source=(ROOT/"controller/mysql_unified_qualification_solo_authority_v1.py").read_text()\n'
             b' assert "SEALED_UNIQUE_SOLO_AUTHORITY_NOT_EXECUTED" in source\n'
             b' assert "source_solo_authority_sha256" in source\n'
             b' assert "collector_manifest_sha256" in source\n')
    test += (b'\ndef test_fresh_full_process_preflight_is_in_exact_closure():\n'
             b' source=(ROOT/"qualification/mysql_unified_fresh_process_inventory_v1.py").read_text()\n'
             b' assert "PASS_FRESH_FULL_PROCESS_ZERO_CONFLICT_NOT_LAUNCH_AUTHORITY" in source\n'
             b' assert "target_infrastructure_daemon_count" in source\n')
    payload["tests/test_mysql_unified_job_tpch_tpcc_collector_v2.py"] = test
    behavior = (PARENT / "tests/test_mysql_unified_collector_v1_behavior.py").read_bytes()
    replacements = {
        b'"job_memory": ("18-20,54-56", "25-27,61-63")': b'"job_memory": ("25-27,61-63", "28-31,64-67")',
        b'"tpch_memory": ("32-33,68-69", "0-7,36-43")': b'"tpch_memory": ("18-20,54-56", "0-7,36-43")',
        b'"workload_process_isolation": {"cpuset_cpus": "25-27,61-63"}': b'"workload_process_isolation": {"cpuset_cpus": "28-31,64-67"}',
        b'"job": ("18-20,54-56", "25-27,61-63", "16-17,52-53")': b'"job": ("25-27,61-63", "28-31,64-67", "16-17,52-53")',
        b'"tpch": ("32-33,68-69", "0-7,36-43", "32-33,68-69")': b'"tpch": ("18-20,54-56", "0-7,36-43", "32-33,68-69")',
        b'"historical_same_host_mutex": "PROHIBITED_AS_V16_PARALLEL_AUTHORITY"': b'"historical_same_host_mutex": "PROHIBITED_AS_V18_PARALLEL_AUTHORITY"',
    }
    for old, new in replacements.items():
        if old not in behavior:
            raise BuildError("behavior v18 anchor differs: " + old.decode())
        behavior = behavior.replace(old, new)
    payload["tests/test_mysql_unified_collector_v2_behavior.py"] = behavior
    payload[BUILDER] = Path(__file__).read_bytes()

    for relative in (base.JOB_CORE, base.JOB_ADAPTER, base.ENTRY,
                     base.PAIRED, base.AGGREGATE, SOLO, PROCESS_INVENTORY, BUILDER):
        modes[relative] = 0o555
    for relative in (IDENTITY, INVALIDATION, STALE_V2_INVALIDATION,
                     "tests/test_mysql_unified_job_tpch_tpcc_collector_v2.py",
                     "tests/test_mysql_unified_collector_v2_behavior.py"):
        modes[relative] = 0o444

    manifest_identity = {
        "schema_version": SCHEMA,
        "status": "SEALED_UNIFIED_V18_REBIND_NOT_LAUNCHED_FRESH_LIVE_ZERO_CONFLICT_REQUIRED",
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True, "remote_actions": 0,
        "formal_launch_authorized": False, "formal10_authorized": False,
        "accepted_round_authorized": False, "qualification_credit": 0,
        "r0_credit": 0, "candidate_credit": 0, "top5_replay_credit": 0,
        "accepted_rounds_credit": 0,
        "supported_scenarios": ["job_memory", "tpch_memory", "tpcc_cpu"],
        "collector_entry": base.ENTRY,
        "paired_authority_producer": base.PAIRED,
        "solo_authority_producer": SOLO,
        "fresh_full_process_inventory_producer": PROCESS_INVENTORY,
        "three_pair_gate_aggregate_producer": base.AGGREGATE,
        "collector_identity": IDENTITY,
        "same_collector_sha_gate_preserved": True,
        "fresh_current_full_process_zero_conflict_required": True,
        "fresh_current_full_process_zero_conflict_verified": False,
        "all_three_pairwise_dynamic_gates_required": True,
        "parent_unified_bundle_id": PARENT_ID,
        "parent_unified_manifest_sha256": PARENT_SHA,
        "job_predecessor_bundle_id": JOB_ID,
        "job_predecessor_manifest_sha256": JOB_SHA,
        "files": {relative: base.sha(data) for relative, data in sorted(payload.items())},
    }
    bundle_id = base.sha(base.canonical(manifest_identity))
    manifest = dict(manifest_identity, bundle_id=bundle_id)
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / bundle_id
    if destination.exists():
        if json.loads((destination / "bundle_manifest.json").read_text()) != manifest:
            raise BuildError("existing unified v2 identity differs")
    else:
        stage = OUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6))
        stage.mkdir(mode=0o700)
        try:
            for relative, data in payload.items():
                path = stage / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                path.chmod(modes.get(relative, 0o444) & ~0o222)
            raw = base.canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                base.sha(raw) + "  bundle_manifest.json\n", encoding="ascii")
            base.freeze(stage)
            os.rename(stage, destination)
        except BaseException:
            if stage.exists():
                shutil.rmtree(stage)
            raise
    print(json.dumps({"status": manifest["status"], "bundle": str(destination),
                      "bundle_id": bundle_id,
                      "manifest_sha256": base.digest(destination / "bundle_manifest.json")},
                     sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (BuildError, base.BuildError, OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit("NOT_READY: %s" % exc)

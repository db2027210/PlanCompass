#!/usr/bin/env python3
"""Build offline JOB v18 around the latest node04 CPU readback."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "1f5d221658221f1a8b39862543d35684acc5f1c1cb0ce10273bd4d6ac1aeac05"
PARENT_SHA = "5150d3cd0dd9625b591e60273a19e869e948e2fa0e44898fdec30cd024fa540a"
PARENT = ROOT / "artifacts/mysql_job_v17_paired_gate_relocation_successor" / PARENT_ID
OUT = ROOT / "artifacts/mysql_job_node04_disjoint_successor_v18"
OLD_SCHEMA = "mysql_job_v17_paired_gate_relocation_successor_bundle_v1"
SCHEMA = "mysql_job_node04_disjoint_successor_bundle_v18"
CONTRACT = "authority/mysql_algorithm_c_distributed_four_scene_successor_contract_v2_20260729.json"
RELOCATION_CONTRACT = "authority/mysql_job_node04_live_disjoint_relocation_contract_v2.json"
INVALIDATION = "authority/mysql_job_v17_node04_cpu_overlap_invalidation_20260803.json"
STALE_V18_INVALIDATION = "authority/mysql_job_v18_stale_relocation_contract_invalidation_20260803.json"
BUILDER = "builder/build_mysql_job_node04_disjoint_successor_v18.py"
TEST = "tests/test_mysql_job_node04_disjoint_successor_v18.py"
OLD_PAIRED = "controller/mysql_job_v17_qualification_paired_authority_v1.py"
PAIRED = "controller/mysql_job_v18_qualification_paired_authority_v1.py"
OLD_AGGREGATE = "qualification/mysql_job_v17_three_pair_gate_aggregate_v1.py"
AGGREGATE = "qualification/mysql_job_v18_three_pair_gate_aggregate_v1.py"
JOB_DAEMON = "16-17,52-53"
JOB_DB = "25-27,61-63"
JOB_CLIENT = "28-31,64-67"
TPCH_DB = "18-20,54-56"
TPCH_CLIENT = "0-7,36-43"
TPCH_DAEMON = "32-33,68-69"
TPCC_DB = "21-24,57-60"
TPCC_CLIENT = "8-15,44-51"
TPCC_DAEMON = "34-35,70-71"


class BuildError(RuntimeError): pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha(value): return hashlib.sha256(value).hexdigest()
def digest(path): return sha(Path(path).read_bytes())


def verify_parent():
    path = PARENT / "bundle_manifest.json"
    manifest = json.loads(path.read_text())
    unsigned = dict(manifest); unsigned.pop("bundle_id", None)
    if (manifest.get("bundle_id") != PARENT_ID or digest(path) != PARENT_SHA
            or sha(canonical(unsigned)) != PARENT_ID
            or manifest.get("schema_version") != OLD_SCHEMA):
        raise BuildError("JOB v17 parent identity differs")
    for relative, expected in manifest["files"].items():
        source = PARENT / relative
        if source.is_symlink() or not source.is_file() or digest(source) != expected:
            raise BuildError("JOB v17 closure differs: " + relative)
    return manifest


def patch_runtime(raw):
    value = json.loads(raw)
    value["resource_envelope"]["cpuset_cpus"] = JOB_DB
    value["client_resource_envelope"]["cpuset_cpus"] = JOB_CLIENT
    expected = value["live_cpu_authority"]["expected"]
    expected["job"] = {"database": JOB_DB, "client": JOB_CLIENT,
                       "dedicated_daemon": JOB_DAEMON}
    expected["tpch"] = {"database": TPCH_DB, "client": TPCH_CLIENT,
                        "dedicated_daemon": TPCH_DAEMON}
    value["live_cpu_authority"].update({
        "publication_input": "operator_supplied_latest_node04_inspect_20260803",
        "fresh_full_process_zero_conflict_readback_required": True,
        "candidate_client_zero_conflict_not_established_offline": True,
    })
    value["v18_cpu_rebind"] = {
        "status": "STATIC_DISJOINT_FRESH_LIVE_ZERO_CONFLICT_REQUIRED",
        "database": JOB_DB, "client": JOB_CLIENT, "daemon": JOB_DAEMON,
        "observed_tpch_database": TPCH_DB,
        "old_job_database_invalidated": TPCH_DB,
    }
    return canonical(value)


def patch_contract(raw):
    value = json.loads(raw)
    scenes = value["scenarios"]
    scenes["job"]["resource_envelope"]["cpuset_cpus"] = JOB_DB
    scenes["job"]["client_resource_envelope"]["cpuset_cpus"] = JOB_CLIENT
    scenes["job"]["target_infrastructure"]["cpuset_cpus"] = JOB_DAEMON
    scenes["tpch"]["resource_envelope"]["cpuset_cpus"] = TPCH_DB
    scenes["tpch"]["client_resource_envelope"]["cpuset_cpus"] = TPCH_CLIENT
    scenes["tpch"]["target_infrastructure"]["cpuset_cpus"] = TPCH_DAEMON
    policy = value["same_host_parallel_policy"]
    policy["policy_version"] = "mysql_algorithm_c_node04_v18_exact_live_disjoint_gate_v4"
    policy["historical_same_host_mutex"] = "PROHIBITED_AS_V18_PARALLEL_AUTHORITY"
    policy["fallback_decision"] = "BLOCK_CONCURRENT_LAUNCH_NO_SERIAL_SUBSTITUTION"
    policy["live_readback_authority"]["expected_cpu_sets"] = {
        "job": {"database": JOB_DB, "client": JOB_CLIENT, "daemon": JOB_DAEMON},
        "tpch": {"database": TPCH_DB, "client": TPCH_CLIENT, "daemon": TPCH_DAEMON},
        "tpcc": {"database": TPCC_DB, "client": TPCC_CLIENT, "daemon": TPCC_DAEMON},
    }
    policy["live_readback_authority"].update({
        "publication_input": "operator_supplied_latest_node04_inspect_20260803",
        "fresh_full_process_zero_conflict_readback_required": True,
        "candidate_job_client_cpuset": JOB_CLIENT,
        "candidate_job_client_offline_zero_conflict_is_launch_authority": False,
    })
    policy["sealed_qualification_paired_authority"]["producer"] = PAIRED
    policy["three_pair_dynamic_gate_aggregate"]["producer"] = AGGREGATE
    value["status"] = "OFFLINE_V18_STATIC_DISJOINT_FRESH_LIVE_ZERO_CONFLICT_REQUIRED"
    return canonical(value)


def patch_relocation_contract(raw):
    value = json.loads(raw)
    value["job"].update({
        "database_cpuset": JOB_DB,
        "client_cpuset": JOB_CLIENT,
        "daemon_cpuset": JOB_DAEMON,
    })
    value["node04_live_expected"]["tpch"].update({
        "database_cpuset": TPCH_DB,
        "client_cpuset": TPCH_CLIENT,
        "daemon_cpuset": TPCH_DAEMON,
    })
    value["node04_live_expected"]["tpcc"].update({
        "database_cpuset": TPCC_DB,
        "client_cpuset": TPCC_CLIENT,
        "daemon_cpuset": TPCC_DAEMON,
    })
    value.update({
        "status": "SEALED_V18_STATIC_DISJOINT_FRESH_LIVE_ZERO_CONFLICT_REQUIRED",
        "parallel_authority": "fresh_full_process_zero_conflict_and_all_pairwise_dynamic_gates",
        "fresh_current_full_process_zero_conflict_verified": False,
        "serial_fallback_authorized": False,
    })
    return canonical(value)


def patch_paired(raw):
    source = raw.decode()
    source = source.replace("mysql_job_v17_qualification_paired_authority_v1",
                            "mysql_job_v18_qualification_paired_authority_v1")
    old = '"job_memory": {"database": "18-20,54-56", "client": "25-27,61-63", "daemon": "16-17,52-53"}'
    new = '"job_memory": {"database": "25-27,61-63", "client": "28-31,64-67", "daemon": "16-17,52-53"}'
    old_tpch = '"tpch_memory": {"database": "32-33,68-69", "client": "0-7,36-43", "daemon": "32-33,68-69"}'
    new_tpch = '"tpch_memory": {"database": "18-20,54-56", "client": "0-7,36-43", "daemon": "32-33,68-69"}'
    if source.count(old) != 1 or source.count(old_tpch) != 1:
        raise BuildError("paired CPU anchor differs")
    return source.replace(old, new).replace(old_tpch, new_tpch).encode()


def patch_aggregate(raw):
    source = raw.decode().replace("mysql_job_v17_three_pair_gate_aggregate_v1",
                                  "mysql_job_v18_three_pair_gate_aggregate_v1")
    old_job = '"job_memory": ("18-20,54-56", "25-27,61-63", "16-17,52-53")'
    new_job = '"job_memory": ("25-27,61-63", "28-31,64-67", "16-17,52-53")'
    old_tpch = '"tpch_memory": ("32-33,68-69", "0-7,36-43", "32-33,68-69")'
    new_tpch = '"tpch_memory": ("18-20,54-56", "0-7,36-43", "32-33,68-69")'
    if source.count(old_job) != 1 or source.count(old_tpch) != 1:
        raise BuildError("aggregate CPU anchor differs")
    return (source.replace(old_job, new_job).replace(old_tpch, new_tpch)
            .replace("PROHIBITED_AS_V16_PARALLEL_AUTHORITY",
                     "PROHIBITED_AS_V18_PARALLEL_AUTHORITY").encode())


def patch_services(raw):
    source = raw.decode()
    for old, new in ((f'CPUS = "18-20,54-56"', f'CPUS = "{JOB_DB}"'),
                     (f'CLIENT_CPUS = "25-27,61-63"', f'CLIENT_CPUS = "{JOB_CLIENT}"')):
        if source.count(old) != 1: raise BuildError("service CPU anchor differs")
        source = source.replace(old, new)
    return source.encode()


def patch_authority(raw):
    source = raw.decode()
    old_db = '"cpuset_cpus": "18-20,54-56"'
    old_client = '"cpuset_cpus": "25-27,61-63"'
    if source.count(old_db) != 1 or source.count(old_client) != 1:
        raise BuildError("authority envelope anchor differs")
    return source.replace(old_db, f'"cpuset_cpus": "{JOB_DB}"').replace(
        old_client, f'"cpuset_cpus": "{JOB_CLIENT}"').encode()


def patch_campaign(raw):
    source = raw.decode()
    old = '"/dev/nvme2n1", "18-20,54-56", "1", 1.15, 4294967296, 512,'
    new = f'"/dev/nvme2n1", "{JOB_DB}", "1", 1.15, 4294967296, 512,'
    if source.count(old) != 1: raise BuildError("JOB campaign CPU anchor differs")
    return source.replace(old, new).encode()


TEST_SOURCE = r'''from __future__ import annotations
import hashlib,json,stat
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def cpus(value):
 out=set()
 for part in value.split(","):
  ends=[int(v) for v in part.split("-")];out.update(range(ends[0],ends[-1]+1))
 return out
def test_v18_identity_scope_zero_credit_closure():
 m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")
 raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
 assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()
 assert m["schema_version"]=="mysql_job_node04_disjoint_successor_bundle_v18"
 assert m["formal_scope"]=="mysql_only" and m["cross_engine_comparison_prohibited"] is True
 assert m["postgresql_mutation_prohibited"] is True and m["remote_actions"]==0
 for key in ("qualification_credit","r0_credit","candidate_credit","top5_replay_credit","accepted_rounds_credit"):assert m[key]==0
 actual={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.rglob("*") if p.is_file() and p.relative_to(ROOT).as_posix() not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
 assert actual==m["files"]
 for p in (ROOT,*ROOT.rglob("*")):assert not p.is_symlink() and stat.S_IMODE(p.stat().st_mode)&0o222==0
def test_v18_exact_static_cpu_disjoint_and_live_gate():
 runtime=json.loads((ROOT/"distributed_runtime_contract.json").read_text())
 assert runtime["resource_envelope"]["cpuset_cpus"]=="25-27,61-63"
 assert runtime["client_resource_envelope"]["cpuset_cpus"]=="28-31,64-67"
 assert runtime["target_infrastructure"]["cpuset_cpus"]=="16-17,52-53"
 assert runtime["live_cpu_authority"]["candidate_client_zero_conflict_not_established_offline"] is True
 c=json.loads((ROOT/"authority/mysql_algorithm_c_distributed_four_scene_successor_contract_v2_20260729.json").read_text())
 expected={"job":("25-27,61-63","28-31,64-67","16-17,52-53"),"tpch":("18-20,54-56","0-7,36-43","32-33,68-69"),"tpcc":("21-24,57-60","8-15,44-51","34-35,70-71")}
 unions={}
 for name,(db,client,daemon) in expected.items():
  row=c["scenarios"][name]
  assert row["resource_envelope"]["cpuset_cpus"]==db
  assert row["client_resource_envelope"]["cpuset_cpus"]==client
  assert row["target_infrastructure"]["cpuset_cpus"]==daemon
  unions[name]=cpus(db)|cpus(client)|cpus(daemon)
 for left,right in (("job","tpch"),("job","tpcc"),("tpch","tpcc")):assert unions[left].isdisjoint(unions[right])
 p=c["same_host_parallel_policy"]
 assert p["fallback_decision"]=="BLOCK_CONCURRENT_LAUNCH_NO_SERIAL_SUBSTITUTION"
 assert p["historical_same_host_mutex"]=="PROHIBITED_AS_V18_PARALLEL_AUTHORITY"
 assert p["live_readback_authority"]["fresh_full_process_zero_conflict_readback_required"] is True
 r=json.loads((ROOT/"authority/mysql_job_node04_live_disjoint_relocation_contract_v2.json").read_text())
 assert r["job"]["database_cpuset"]=="25-27,61-63"
 assert r["job"]["client_cpuset"]=="28-31,64-67"
 assert r["job"]["daemon_cpuset"]=="16-17,52-53"
 assert r["node04_live_expected"]["tpch"]=={"database_cpuset":"18-20,54-56","client_cpuset":"0-7,36-43","daemon_cpuset":"32-33,68-69"}
 assert r["node04_live_expected"]["tpcc"]=={"database_cpuset":"21-24,57-60","client_cpuset":"8-15,44-51","daemon_cpuset":"34-35,70-71"}
 assert r["fresh_current_full_process_zero_conflict_verified"] is False
def test_latest_inspect_invalidation_and_no_false_offline_live_claim():
 a=json.loads((ROOT/"authority/mysql_job_v17_node04_cpu_overlap_invalidation_20260803.json").read_text())
 assert a["latest_authoritative_inspect"]["tpch_database"]=={"host_pid":108534,"cpuset_cpus":"18-20,54-56"}
 assert a["latest_authoritative_inspect"]["tpch_client"]=={"host_pid":108739,"cpuset_cpus":"0-7,36-43"}
 assert a["latest_authoritative_inspect"]["tpcc_database"]=={"host_pid":96504,"cpuset_cpus":"21-24,57-60"}
 assert a["latest_authoritative_inspect"]["tpcc_client"]=={"host_pid":96615,"cpuset_cpus":"8-15,44-51"}
 assert a["v17_job_database_overlap_with_tpch"] is True
 assert a["candidate_static_disjoint"] is True
 assert a["fresh_current_full_process_zero_conflict_verified"] is False
 assert a["launch_blocked_until_fresh_readback"] is True
 stale=json.loads((ROOT/"authority/mysql_job_v18_stale_relocation_contract_invalidation_20260803.json").read_text())
 assert stale["invalidated_bundle_id"]=="6cd345185daf38c1b7e58d1abbfa6dd1a6261f9bd6cb53fbc974289ecb455972"
 assert stale["launch_prohibited"] is True
'''.encode()


def patch_tests(payload):
    replacements = {
        b'"job": ("18-20,54-56", "25-27,61-63", "16-17,52-53")': b'"job": ("25-27,61-63", "28-31,64-67", "16-17,52-53")',
        b'"tpch": ("32-33,68-69", "0-7,36-43", "32-33,68-69")': b'"tpch": ("18-20,54-56", "0-7,36-43", "32-33,68-69")',
        b'"job_memory": ("18-20,54-56", "25-27,61-63")': b'"job_memory": ("25-27,61-63", "28-31,64-67")',
        b'"tpch_memory": ("32-33,68-69", "0-7,36-43")': b'"tpch_memory": ("18-20,54-56", "0-7,36-43")',
        b'{"cpuset_cpus": "25-27,61-63"}': b'{"cpuset_cpus": "28-31,64-67"}',
        b'assert runtime["resource_envelope"]["cpuset_cpus"] == "18-20,54-56"': b'assert runtime["resource_envelope"]["cpuset_cpus"] == "25-27,61-63"',
        b'assert runtime["client_resource_envelope"]["cpuset_cpus"] == "25-27,61-63"': b'assert runtime["client_resource_envelope"]["cpuset_cpus"] == "28-31,64-67"',
        b'expected = {"job": ("18-20,54-56", "25-27,61-63", "16-17,52-53"), "tpch": ("32-33,68-69", "0-7,36-43", "32-33,68-69"),': b'expected = {"job": ("25-27,61-63", "28-31,64-67", "16-17,52-53"), "tpch": ("18-20,54-56", "0-7,36-43", "32-33,68-69"),',
        b'assert c["scenarios"]["tpch"]["resource_envelope"]["cpuset_cpus"] != "18-20,54-56"': b'assert c["scenarios"]["tpch"]["resource_envelope"]["cpuset_cpus"] == "18-20,54-56"',
        b'assert d["resource_envelope"]["cpuset_cpus"] == "18-20,54-56"': b'assert d["resource_envelope"]["cpuset_cpus"] == "25-27,61-63"',
        b'assert d["client_resource_envelope"]["cpuset_cpus"] == "25-27,61-63"': b'assert d["client_resource_envelope"]["cpuset_cpus"] == "28-31,64-67"',
        b'map(cpus, ("18-20,54-56", "25-27,61-63", "16-17,52-53"))': b'map(cpus, ("25-27,61-63", "28-31,64-67", "16-17,52-53"))',
        b'"/dev/nvme2n1", "18-20,54-56", "1", 1.15, 4294967296, 512': b'"/dev/nvme2n1", "25-27,61-63", "1", 1.15, 4294967296, 512',
        b'"historical_same_host_mutex": "PROHIBITED_AS_V16_PARALLEL_AUTHORITY"': b'"historical_same_host_mutex": "PROHIBITED_AS_V18_PARALLEL_AUTHORITY"',
        b'assert runtime["resource_envelope"]["cpuset_cpus"]=="18-20,54-56"': b'assert runtime["resource_envelope"]["cpuset_cpus"]=="25-27,61-63"',
        b'assert runtime["client_resource_envelope"]["cpuset_cpus"]=="25-27,61-63"': b'assert runtime["client_resource_envelope"]["cpuset_cpus"]=="28-31,64-67"',
        b'expected={"job":("18-20,54-56","25-27,61-63","16-17,52-53"),"tpch":("32-33,68-69","0-7,36-43","32-33,68-69"),': b'expected={"job":("25-27,61-63","28-31,64-67","16-17,52-53"),"tpch":("18-20,54-56","0-7,36-43","32-33,68-69"),',
        b'assert c["scenarios"]["tpch"]["resource_envelope"]["cpuset_cpus"]!="18-20,54-56"': b'assert c["scenarios"]["tpch"]["resource_envelope"]["cpuset_cpus"]=="18-20,54-56"',
        b'assert d["resource_envelope"]["cpuset_cpus"]=="18-20,54-56"': b'assert d["resource_envelope"]["cpuset_cpus"]=="25-27,61-63"',
        b'assert d["client_resource_envelope"]["cpuset_cpus"]=="25-27,61-63"': b'assert d["client_resource_envelope"]["cpuset_cpus"]=="28-31,64-67"',
        b'map(cpus,("18-20,54-56","25-27,61-63","16-17,52-53"))': b'map(cpus,("25-27,61-63","28-31,64-67","16-17,52-53"))',
        b'\'"/dev/nvme2n1", "18-20,54-56", "1", 1.15, 4294967296, 512\'': b'\'"/dev/nvme2n1", "25-27,61-63", "1", 1.15, 4294967296, 512\'',
    }
    for old, new in replacements.items(): payload = payload.replace(old, new)
    return payload.replace(OLD_SCHEMA.encode(), SCHEMA.encode()).replace(
        OLD_PAIRED.encode(), PAIRED.encode()).replace(OLD_AGGREGATE.encode(), AGGREGATE.encode())


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink(): raise BuildError("symlink in JOB v18")
        path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
    root.chmod(stat.S_IMODE(root.stat().st_mode) & ~0o222)


def main():
    parent = verify_parent()
    payload = {relative: (PARENT / relative).read_bytes()
               for relative in parent["files"] if relative != "closure.json"}
    modes = {relative: stat.S_IMODE((PARENT / relative).stat().st_mode)
             for relative in payload}
    old_contract_sha = sha(payload[CONTRACT])
    payload["distributed_runtime_contract.json"] = patch_runtime(payload["distributed_runtime_contract.json"])
    payload[CONTRACT] = patch_contract(payload[CONTRACT])
    payload[RELOCATION_CONTRACT] = patch_relocation_contract(payload[RELOCATION_CONTRACT])
    payload["qualification/mysql_job_distributed_v2_adopted_services.py"] = patch_services(payload["qualification/mysql_job_distributed_v2_adopted_services.py"])
    payload["qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py"] = patch_authority(payload["qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py"])
    payload["qualification/shell/manual_mysql_sgd_r0_campaign.py"] = patch_campaign(payload["qualification/shell/manual_mysql_sgd_r0_campaign.py"])
    payload[PAIRED] = patch_paired(payload.pop(OLD_PAIRED)); modes[PAIRED] = modes.pop(OLD_PAIRED)
    payload[AGGREGATE] = patch_aggregate(payload.pop(OLD_AGGREGATE)); modes[AGGREGATE] = modes.pop(OLD_AGGREGATE)
    new_contract_sha = sha(payload[CONTRACT])
    for relative in ("controller/render_mysql_job_v32_bootstrap_spec_tmpfs_v2.py",
                     "qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py"):
        payload[relative] = payload[relative].replace(old_contract_sha.encode(), new_contract_sha.encode())
    for relative in ("qualification/mysql_job_distributed_live_readback_qualification_v2.py",
                     "qualification/mysql_job_dedicated_daemon_materializer_v5.py"):
        payload[relative] = payload[relative].replace(OLD_SCHEMA.encode(), SCHEMA.encode())
    for relative in list(payload):
        if relative.startswith("tests/"): payload[relative] = patch_tests(payload[relative])
    invalidation = {
        "schema_version": "mysql_job_v17_node04_cpu_overlap_invalidation_v1",
        "status": "INVALIDATED_OVERLAPPING_CPU_ZERO_CREDIT",
        "invalidated_bundle_id": PARENT_ID,
        "latest_authoritative_inspect": {
            "tpch_database": {"host_pid": 108534, "cpuset_cpus": TPCH_DB},
            "tpch_client": {"host_pid": 108739, "cpuset_cpus": TPCH_CLIENT},
            "tpcc_database": {"host_pid": 96504, "cpuset_cpus": TPCC_DB},
            "tpcc_client": {"host_pid": 96615, "cpuset_cpus": TPCC_CLIENT},
        },
        "v17_planned_job_database_cpuset": TPCH_DB,
        "v17_job_database_overlap_with_tpch": True,
        "v18_candidate": {"daemon": JOB_DAEMON, "database": JOB_DB, "client": JOB_CLIENT},
        "candidate_static_disjoint": True,
        "candidate_disjoint_from_supplied_latest_container_inspect": True,
        "historical_full_inventory_support": {
            "path": "artifacts/mysql_tpch_sql_native_percentile_successor_v1/ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4/qualification_source/A_solo_v28a.evidence.json",
            "fresh_current_launch_authority": False,
            "fixed_affinity_rows_on_candidate_client_cpuset": 0,
            "broad_affinity_inert_pg_launcher_observed": True,
        },
        "fresh_current_full_process_zero_conflict_verified": False,
        "launch_blocked_until_fresh_readback": True,
        "required_fresh_checks": ["all_process_affinity", "all_dedicated_daemon_affinity",
                                  "descendant_closure", "device_writers", "ports",
                                  "numa_and_memory", "swap", "pids"],
        "serial_fallback_authorized": False,
        "postgresql_mutation_prohibited": True, "remote_actions": 0,
        "qualification_credit": 0, "r0_credit": 0, "accepted_rounds_credit": 0,
    }
    payload[INVALIDATION] = canonical(invalidation); modes[INVALIDATION] = 0o444
    stale_v18 = {
        "schema_version": "mysql_job_v18_stale_relocation_contract_invalidation_v1",
        "status": "INVALIDATED_STALE_CPU_RELOCATION_CONTRACT_ZERO_CREDIT",
        "invalidated_bundle_id": "6cd345185daf38c1b7e58d1abbfa6dd1a6261f9bd6cb53fbc974289ecb455972",
        "invalidated_manifest_sha256": "51dbf0b28bc092bbb8f11a04a89f9ad1fab544e22eb455d4f9af09c52e0bd8e0",
        "reason": "embedded relocation contract retained the superseded v17 CPU mapping",
        "launch_prohibited": True,
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True, "remote_actions": 0,
        "formal_launch_authorized": False, "qualification_credit": 0,
    }
    payload[STALE_V18_INVALIDATION] = canonical(stale_v18)
    modes[STALE_V18_INVALIDATION] = 0o444
    payload[BUILDER] = Path(__file__).read_bytes(); modes[BUILDER] = 0o555
    payload[TEST] = TEST_SOURCE; modes[TEST] = 0o444
    closure = json.loads((PARENT / "closure.json").read_text())
    closure.update({"schema_version": "mysql_job_module_closure_v18",
                    "predecessor_bundle_id": PARENT_ID,
                    "predecessor_bundle_manifest_sha256": PARENT_SHA,
                    "cpu_overlap_invalidation": invalidation})
    closure["files"] = {relative: sha(data) for relative, data in sorted(payload.items())}
    payload["closure.json"] = canonical(closure); modes["closure.json"] = 0o444
    identity = dict(parent); identity.pop("bundle_id", None)
    identity.update({
        "schema_version": SCHEMA,
        "status": "SEALED_JOB_V18_STATIC_DISJOINT_FRESH_LIVE_ZERO_CONFLICT_REQUIRED",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_bundle_manifest_sha256": PARENT_SHA,
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True, "remote_actions": 0,
        "formal_launch_authorized": False, "formal10_authorized": False,
        "accepted_round_authorized": False, "qualification_credit": 0,
        "r0_credit": 0, "candidate_credit": 0, "top5_replay_credit": 0,
        "accepted_rounds_credit": 0,
        "job_cpu_expected": {"daemon": JOB_DAEMON, "database": JOB_DB, "client": JOB_CLIENT},
        "fresh_current_full_process_zero_conflict_verified": False,
        "launch_blocked_until_fresh_readback": True,
        "cpu_overlap_invalidation": INVALIDATION,
        "paired_authority_producer": PAIRED,
        "three_pair_gate_aggregate_producer": AGGREGATE,
        "distributed_runtime_contract_sha256": sha(payload["distributed_runtime_contract.json"]),
        "files": {relative: sha(data) for relative, data in sorted(payload.items())},
    })
    bundle_id = sha(canonical(identity)); manifest = dict(identity, bundle_id=bundle_id)
    OUT.mkdir(parents=True, exist_ok=True); destination = OUT / bundle_id
    if destination.exists():
        if json.loads((destination / "bundle_manifest.json").read_text()) != manifest:
            raise BuildError("existing JOB v18 identity differs")
        print(json.dumps({"status":manifest["status"],"bundle":str(destination),"bundle_id":bundle_id,"manifest_sha256":digest(destination/"bundle_manifest.json")},sort_keys=True));return
    stage = OUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6)); stage.mkdir(mode=0o700)
    try:
        for relative, data in payload.items():
            path=stage/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);path.chmod(modes.get(relative,0o444)&~0o222)
        raw=canonical(manifest);(stage/"bundle_manifest.json").write_bytes(raw)
        (stage/"bundle_manifest.json.sha256").write_text(sha(raw)+"  bundle_manifest.json\n",encoding="ascii")
        freeze(stage);os.rename(stage,destination)
    except BaseException:
        if stage.exists():shutil.rmtree(stage)
        raise
    print(json.dumps({"status":manifest["status"],"bundle":str(destination),"bundle_id":bundle_id,"manifest_sha256":digest(destination/"bundle_manifest.json")},sort_keys=True))


if __name__=="__main__":
    try:main()
    except (BuildError,OSError,ValueError,KeyError,TypeError) as exc:raise SystemExit("NOT_READY: %s"%exc)

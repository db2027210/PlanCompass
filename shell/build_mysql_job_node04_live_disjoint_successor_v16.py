#!/usr/bin/env python3
"""Build the offline JOB v16 node04 live-disjoint successor."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "dc11765ffe539e9238107daab1dfee9fe370a9da1002d6cf66fdda6e6233a2bb"
PARENT_SHA = "8445f385cbd982cf99decc16f46260e964072532a3dd40ed2d411427ae334f65"
PARENT = ROOT / "artifacts/mysql_job_node04_nvme2_relocation_successor_v15" / PARENT_ID
OUT = ROOT / "artifacts/mysql_job_node04_live_disjoint_successor_v16"
SCHEMA = "mysql_job_node04_live_disjoint_successor_bundle_v16"
CONTRACT = "authority/mysql_algorithm_c_distributed_four_scene_successor_contract_v2_20260729.json"
OLD_RELOCATION = "authority/mysql_job_node04_nvme2_relocation_contract_v1.json"
RELOCATION = "authority/mysql_job_node04_live_disjoint_relocation_contract_v2.json"
INVALIDATION = "authority/mysql_job_v15_stale_node04_cpu_contract_invalidation_20260803.json"
BUILDER = "builder/build_mysql_job_node04_live_disjoint_successor_v16.py"
TEST = "tests/test_mysql_job_node04_live_disjoint_successor_v16.py"
JOB_DB = "18-20,54-56"
JOB_CLIENT = "25-27,61-63"
JOB_DAEMON = "16-17,52-53"
TPCH_DB_DAEMON = "32-33,68-69"
TPCH_CLIENT = "0-7,36-43"
TPCC_DB = "21-24,57-60"
TPCC_CLIENT = "8-15,44-51"
TPCC_DAEMON = "34-35,70-71"


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
            or manifest.get("schema_version")
            != "mysql_job_node04_nvme2_relocation_successor_bundle_v15"):
        raise BuildError("JOB v15 parent identity differs")
    for relative, expected in manifest["files"].items():
        path = PARENT / relative
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise BuildError("JOB v15 closure differs: " + relative)
    return manifest


def patch_runtime(payload):
    value = json.loads(payload.decode("utf-8"))
    value["client_resource_envelope"]["cpuset_cpus"] = JOB_CLIENT
    value["live_cpu_authority"] = {
        "host": "node04",
        "publication_input": "operator_supplied_live_readback_20260803",
        "fresh_prelaunch_readback_required": True,
        "exact_match_required": True,
        "pairwise_dynamic_gate_required": True,
        "expected": {
            "job": {"database": JOB_DB, "client": JOB_CLIENT,
                    "dedicated_daemon": JOB_DAEMON},
            "tpch": {"database": TPCH_DB_DAEMON, "client": TPCH_CLIENT,
                     "dedicated_daemon": TPCH_DB_DAEMON},
            "tpcc": {"database": TPCC_DB, "client": TPCC_CLIENT,
                     "dedicated_daemon": TPCC_DAEMON},
        },
    }
    return canonical(value)


def infrastructure(cpus, scenario, device):
    return {
        "kind": "dedicated_dockerd_live_readback",
        "scenario": scenario,
        "cpuset_cpus": cpus,
        "device": device,
        "exact_identity_and_descendant_closure_required": True,
        "fresh_live_readback_required": True,
        "foreign_writer": False,
    }


def patch_contract(payload):
    value = json.loads(payload.decode("utf-8"))
    scenes = value["scenarios"]
    scenes["job"]["client_resource_envelope"]["cpuset_cpus"] = JOB_CLIENT
    scenes["job"]["target_infrastructure"]["cpuset_cpus"] = JOB_DAEMON
    scenes["tpch"]["resource_envelope"]["cpuset_cpus"] = TPCH_DB_DAEMON
    scenes["tpch"]["client_resource_envelope"]["cpuset_cpus"] = TPCH_CLIENT
    scenes["tpch"]["target_infrastructure"] = infrastructure(
        TPCH_DB_DAEMON, "tpch", scenes["tpch"]["storage"]["device"])
    scenes["tpcc"]["resource_envelope"]["cpuset_cpus"] = TPCC_DB
    scenes["tpcc"]["client_resource_envelope"]["cpuset_cpus"] = TPCC_CLIENT
    scenes["tpcc"]["target_infrastructure"] = infrastructure(
        TPCC_DAEMON, "tpcc", scenes["tpcc"]["storage"]["device"])
    policy = value["same_host_parallel_policy"]
    policy["policy_version"] = "mysql_algorithm_c_node04_live_pairwise_disjoint_dynamic_gate_v3"
    policy["fallback_decision"] = "BLOCK_CONCURRENT_LAUNCH_NO_SERIAL_SUBSTITUTION"
    policy["historical_same_host_mutex"] = "PROHIBITED_AS_V16_PARALLEL_AUTHORITY"
    policy.pop("job_relocation_static_gate_note", None)
    pairs = [("job", "tpch"), ("job", "tpcc"), ("tpch", "tpcc")]
    policy["pairs"] = [
        {"host": "node04", "scenarios": list(pair),
         "static_expected_cpuset_disjoint": True,
         "fresh_actual_readback_exact_match_required": True,
         "dynamic_bidirectional_gate_required": True,
         "on_any_failure": "BLOCK_CONCURRENT_LAUNCH"}
        for pair in pairs
    ]
    policy["live_readback_authority"] = {
        "publication_input": "operator_supplied_node04_live_readback_20260803",
        "fresh_prelaunch_readback_required": True,
        "expected_cpu_sets": {
            "job": {"database": JOB_DB, "client": JOB_CLIENT,
                    "daemon": JOB_DAEMON},
            "tpch": {"database": TPCH_DB_DAEMON, "client": TPCH_CLIENT,
                     "daemon": TPCH_DB_DAEMON},
            "tpcc": {"database": TPCC_DB, "client": TPCC_CLIENT,
                     "daemon": TPCC_DAEMON},
        },
        "authority_rule": "actual_readback_plus_all_three_pairwise_dynamic_gates",
    }
    value["status"] = "OFFLINE_NODE04_LIVE_DISJOINT_CONTRACT_NOT_LAUNCH_AUTHORITY"
    return canonical(value)


def patch_campaign(payload):
    source = payload.decode("utf-8")
    old_tpch = '''    "tpch": Lane(
        "tpch", "tpch_memory", "tpch", "anonymous@192.0.2.3", "node01",
        "/mnt/nvme1n1/anonymous_resource_adaptive/tpch",
        "/mnt/nvme1n1/anonymous/resource_adaptive_mysql/tpch/template",
        "/dev/nvme1n1", "18-20,54-56", "1", 3, 6442450944, 512,
        1205862400, 1205862400, 65103, "tpch_sf10", "memory", "Com_select",
'''
    new_tpch = '''    "tpch": Lane(
        "tpch", "tpch_memory", "tpch", "anonymous@192.0.2.7", "node04",
        "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch",
        "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/template",
        "/dev/nvme0n1", "32-33,68-69", "1", 4, 5583454208, 512,
        100663296, 100663296, 65103, "tpch_sf10", "memory", "Com_select",
'''
    old_tpcc = '''    "tpcc": Lane(
        "tpcc", "tpcc_cpu", "tpcc", "anonymous@192.0.2.3", "node01",
        "/mnt/mysql_nvme0n1/anonymous/resource_adaptive_mysql/tpcc",
        "/mnt/mysql_nvme0n1/anonymous/resource_adaptive_mysql/tpcc/template/datadir",
        "/dev/nvme0n1", "21-24,57-60", "1", 4, 8589934592, 512,
'''
    new_tpcc = '''    "tpcc": Lane(
        "tpcc", "tpcc_cpu", "tpcc", "anonymous@192.0.2.7", "node04",
        "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc",
        "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/template",
        "/dev/nvme3n1", "21-24,57-60", "1", 4, 8589934592, 512,
'''
    if source.count(old_tpch) != 1 or source.count(old_tpcc) != 1:
        raise BuildError("TPCH/TPCC manual campaign live lane anchor differs")
    return source.replace(old_tpch, new_tpch).replace(old_tpcc, new_tpcc).encode("utf-8")


def cpuset(value):
    result = set()
    for part in value.split(","):
        ends = [int(item) for item in part.split("-")]
        result.update(range(ends[0], ends[-1] + 1))
    return result


TEST_SOURCE = r'''from __future__ import annotations
import hashlib,json,stat
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def cpus(value):
 out=set()
 for part in value.split(","):
  ends=[int(v) for v in part.split("-")];out.update(range(ends[0],ends[-1]+1))
 return out
def test_identity_scope_zero_credit_and_exact_closure():
 m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")
 raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
 assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()
 assert m["schema_version"]=="mysql_job_node04_live_disjoint_successor_bundle_v16"
 assert m["formal_scope"]=="mysql_only" and m["cross_engine_comparison_prohibited"] is True
 assert m["postgresql_mutation_prohibited"] is True and m["remote_actions"]==0
 for key in ("qualification_credit","r0_credit","candidate_credit","top5_replay_credit","accepted_rounds_credit"):assert m[key]==0
 actual={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.rglob("*") if p.is_file() and p.relative_to(ROOT).as_posix() not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
 assert actual==m["files"] and not list(ROOT.rglob("__pycache__")) and not list(ROOT.rglob("*.pyc"))
 for p in (ROOT,*ROOT.rglob("*")):assert not p.is_symlink() and stat.S_IMODE(p.stat().st_mode)&0o222==0
def test_job_runtime_and_three_scene_live_cpu_authority():
 runtime=json.loads((ROOT/"distributed_runtime_contract.json").read_text())
 assert runtime["resource_envelope"]["cpuset_cpus"]=="18-20,54-56"
 assert runtime["client_resource_envelope"]["cpuset_cpus"]=="25-27,61-63"
 assert runtime["client_resource_envelope"]["cpu_quota_cores"]==6
 assert runtime["target_infrastructure"]["cpuset_cpus"]=="16-17,52-53"
 assert runtime["storage"]["device"]=="/dev/nvme2n1" and runtime["storage"]["expected_live_major_minor"]=="259:3"
 assert runtime["resource_envelope"]["mysql_port"]==65102 and runtime["resource_envelope"]["cpu_quota_cores"]==1.15
 c=json.loads((ROOT/"authority/mysql_algorithm_c_distributed_four_scene_successor_contract_v2_20260729.json").read_text())
 expected={"job":("18-20,54-56","25-27,61-63","16-17,52-53"),"tpch":("32-33,68-69","0-7,36-43","32-33,68-69"),"tpcc":("21-24,57-60","8-15,44-51","34-35,70-71")}
 unions={}
 for name,(db,client,daemon) in expected.items():
  s=c["scenarios"][name];assert s["resource_envelope"]["cpuset_cpus"]==db
  assert s["client_resource_envelope"]["cpuset_cpus"]==client
  assert s["target_infrastructure"]["cpuset_cpus"]==daemon
  unions[name]=cpus(db)|cpus(client)|cpus(daemon)
 for left,right in (("job","tpch"),("job","tpcc"),("tpch","tpcc")):assert unions[left].isdisjoint(unions[right])
 p=c["same_host_parallel_policy"]
 assert p["fallback_decision"]=="BLOCK_CONCURRENT_LAUNCH_NO_SERIAL_SUBSTITUTION"
 assert len(p["pairs"])==3 and all(x["dynamic_bidirectional_gate_required"] for x in p["pairs"])
 assert p["live_readback_authority"]["authority_rule"]=="actual_readback_plus_all_three_pairwise_dynamic_gates"
def test_algorithm_closure_and_no_stale_tpch_cpu_authority():
 c=json.loads((ROOT/"authority/mysql_algorithm_c_distributed_four_scene_successor_contract_v2_20260729.json").read_text())
 a=c["algorithm_invariants"]
 assert a["enumerate_all_nonempty_subspaces"] is True and a["fixed_top_k_replay"]==5
 assert a["gradient_formula"]=="g_j = sum_r(dScore/dU_r * T[j,r] * confidence)"
 assert c["scenarios"]["tpch"]["resource_envelope"]["cpuset_cpus"]!="18-20,54-56"
 assert c["formal_scope"]=="mysql_only" and c["postgresql_mutation_prohibited"] is True
 campaign=(ROOT/"qualification/shell/manual_mysql_sgd_r0_campaign.py").read_text()
 assert '"/dev/nvme0n1", "32-33,68-69", "1", 4, 5583454208, 512' in campaign
 assert '"/dev/nvme3n1", "21-24,57-60", "1", 4, 8589934592, 512' in campaign
 assert campaign.count('"anonymous@192.0.2.7", "node04"')==3
'''.encode("utf-8")


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink in JOB v16")
        path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
    root.chmod(stat.S_IMODE(root.stat().st_mode) & ~0o222)


def main():
    parent = verify_parent()
    payload = {relative: (PARENT / relative).read_bytes()
               for relative in parent["files"] if relative != "closure.json"}
    modes = {relative: stat.S_IMODE((PARENT / relative).stat().st_mode)
             for relative in payload}
    payload.pop(OLD_RELOCATION, None)
    old_contract_sha = sha(payload[CONTRACT])
    payload["distributed_runtime_contract.json"] = patch_runtime(
        payload["distributed_runtime_contract.json"])
    payload[CONTRACT] = patch_contract(payload[CONTRACT])
    payload["qualification/shell/manual_mysql_sgd_r0_campaign.py"] = patch_campaign(
        payload["qualification/shell/manual_mysql_sgd_r0_campaign.py"])
    new_contract_sha = sha(payload[CONTRACT])
    for relative in (
        "controller/render_mysql_job_v32_bootstrap_spec_tmpfs_v2.py",
        "qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py",
    ):
        payload[relative] = payload[relative].replace(
            old_contract_sha.encode("ascii"), new_contract_sha.encode("ascii"))
    for relative in (
        "qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py",
        "qualification/mysql_job_distributed_v2_adopted_services.py",
    ):
        payload[relative] = payload[relative].replace(
            b"21-23,57-59", JOB_CLIENT.encode("ascii"))
    for relative in (
        "qualification/mysql_job_distributed_live_readback_qualification_v2.py",
        "qualification/mysql_job_dedicated_daemon_materializer_v5.py",
    ):
        payload[relative] = payload[relative].replace(
            b"mysql_job_node04_nvme2_relocation_successor_bundle_v15",
            SCHEMA.encode("ascii"))
    for relative in list(payload):
        if relative.startswith("tests/"):
            payload[relative] = (payload[relative]
                .replace(b"mysql_job_node04_nvme2_relocation_successor_bundle_v15", SCHEMA.encode("ascii"))
                .replace(b"21-23,57-59", JOB_CLIENT.encode("ascii")))
    relocation = {
        "schema_version": "mysql_job_node04_live_disjoint_relocation_contract_v2",
        "status": "SEALED_OFFLINE_NOT_LAUNCHED", "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True, "remote_actions": 0,
        "host": {"hostname": "node04", "address": "192.0.2.7"},
        "job": {"database_cpuset": JOB_DB, "database_cpu_quota_cores": 1.15,
                "client_cpuset": JOB_CLIENT, "client_cpu_quota_cores": 6,
                "daemon_cpuset": JOB_DAEMON, "device": "/dev/nvme2n1",
                "device_major_minor": "259:3", "mysql_port": 65102},
        "node04_live_expected": {
            "tpch": {"database_cpuset": TPCH_DB_DAEMON,
                     "client_cpuset": TPCH_CLIENT, "daemon_cpuset": TPCH_DB_DAEMON},
            "tpcc": {"database_cpuset": TPCC_DB,
                     "client_cpuset": TPCC_CLIENT, "daemon_cpuset": TPCC_DAEMON}},
        "parallel_authority": "fresh_exact_readback_and_all_pairwise_dynamic_gates",
        "serial_fallback_authorized": False, "formal_launch_authorized": False,
        "qualification_credit": 0, "r0_credit": 0, "candidate_credit": 0,
        "top5_replay_credit": 0, "accepted_rounds_credit": 0,
    }
    payload[RELOCATION] = canonical(relocation); modes[RELOCATION] = 0o444
    payload[INVALIDATION] = canonical({
        "schema_version": "mysql_job_v15_stale_node04_cpu_contract_invalidation_v1",
        "status": "INVALIDATED_ZERO_CREDIT",
        "invalidated_bundle_id": PARENT_ID,
        "reason": "TPCH DB cpuset was stale and JOB client overlapped live TPCC DB",
        "replacement_contract": RELOCATION, "serial_fallback_authorized": False,
        "postgresql_mutation_prohibited": True, "remote_actions": 0,
        "qualification_credit": 0, "r0_credit": 0, "candidate_credit": 0,
        "top5_replay_credit": 0, "accepted_rounds_credit": 0,
    }); modes[INVALIDATION] = 0o444
    payload[BUILDER] = Path(__file__).read_bytes(); modes[BUILDER] = 0o555
    payload[TEST] = TEST_SOURCE; modes[TEST] = 0o444
    closure = json.loads((PARENT / "closure.json").read_text(encoding="utf-8"))
    closure.update({"schema_version": "mysql_job_module_closure_v16",
                    "predecessor_bundle_id": PARENT_ID,
                    "predecessor_bundle_manifest_sha256": PARENT_SHA,
                    "relocation": relocation})
    closure["files"] = {relative: sha(data) for relative, data in sorted(payload.items())}
    payload["closure.json"] = canonical(closure); modes["closure.json"] = 0o444
    identity = dict(parent); identity.pop("bundle_id", None)
    identity.update({
        "schema_version": SCHEMA,
        "status": "SEALED_JOB_V16_NODE04_LIVE_DISJOINT_NOT_LAUNCHED",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_bundle_manifest_sha256": PARENT_SHA,
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True, "remote_actions": 0,
        "formal_launch_authorized": False, "formal10_authorized": False,
        "accepted_round_authorized": False, "qualification_credit": 0,
        "r0_credit": 0, "candidate_credit": 0, "top5_replay_credit": 0,
        "accepted_rounds_credit": 0,
        "relocation_contract": RELOCATION,
        "relocation_contract_sha256": sha(payload[RELOCATION]),
        "distributed_runtime_contract_sha256": sha(payload["distributed_runtime_contract.json"]),
        "node04_live_cpu_expected": relocation["node04_live_expected"] | {"job": relocation["job"]},
        "parallel_authority": relocation["parallel_authority"],
        "serial_fallback_authorized": False,
        "files": {relative: sha(data) for relative, data in sorted(payload.items())},
    })
    bundle_id = sha(canonical(identity)); manifest = dict(identity, bundle_id=bundle_id)
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / bundle_id
    if destination.exists():
        if json.loads((destination / "bundle_manifest.json").read_text()) != manifest:
            raise BuildError("existing JOB v16 identity differs")
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

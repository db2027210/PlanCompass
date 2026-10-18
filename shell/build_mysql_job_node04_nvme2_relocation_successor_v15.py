#!/usr/bin/env python3
"""Build the offline JOB v15 node04/nvme2n1 relocation successor."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "659b121c32f1866d4fe2a07ec2aa5857c81578f8b75cfafedf04654cb3b4bcef"
PARENT_SHA = "f6018261584f06ea9a8968357db26b2c3da6ec019a31bb295530a2979f14b406"
PARENT = ROOT / "artifacts/mysql_job_native_duration_successor_v14" / PARENT_ID
OUT = ROOT / "artifacts/mysql_job_node04_nvme2_relocation_successor_v15"
SCHEMA = "mysql_job_node04_nvme2_relocation_successor_bundle_v15"
MOUNT = "/mnt/nvme2n1"
PROJECT = MOUNT + "/anonymous/resource_adaptive_mysql/job"
RUNTIME_ROOT = PROJECT + "/runtime_algorithm_c_successor_20260729"
DAEMON_ROOT = MOUNT + "/anonymous/resource_adaptive_mysql/docker_ra_job_v1"
DB_CPUS = "18-20,54-56"
CLIENT_CPUS = "21-23,57-59"
DAEMON_CPUS = "16-17,52-53"
DEVICE = "/dev/nvme2n1"
MAJOR_MINOR = "259:3"
BUILDER = "builder/build_mysql_job_node04_nvme2_relocation_successor_v15.py"
TEST = "tests/test_mysql_job_node04_nvme2_relocation_v15.py"
INVALIDATION = "authority/mysql_job_node02_pg_conflict_relocation_20260803.json"
RELOCATION = "authority/mysql_job_node04_nvme2_relocation_contract_v1.json"
CONTRACT = "authority/mysql_algorithm_c_distributed_four_scene_successor_contract_v2_20260729.json"


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def digest(path):
    return sha_bytes(Path(path).read_bytes())


def replace_tree(value):
    if isinstance(value, str):
        return (value.replace("anonymous@192.0.2.4", "anonymous@192.0.2.7")
                .replace("192.0.2.4", "192.0.2.7")
                .replace("node02", "node04")
                .replace("/mnt/mysql_nvme1n1", MOUNT)
                .replace("/dev/nvme1n1", DEVICE)
                .replace("0-2,36-38", CLIENT_CPUS)
                .replace("34-35,70-71", DAEMON_CPUS))
    if isinstance(value, list):
        return [replace_tree(item) for item in value]
    if isinstance(value, dict):
        return {replace_tree(key): replace_tree(item) for key, item in value.items()}
    return value


def text_patch(payload):
    return (payload.decode("utf-8")
            .replace("anonymous@192.0.2.4", "anonymous@192.0.2.7")
            .replace("192.0.2.4", "192.0.2.7")
            .replace("node02", "node04")
            .replace("/mnt/mysql_nvme1n1", MOUNT)
            .replace("/dev/nvme1n1", DEVICE)
            .replace("0-2,36-38", CLIENT_CPUS)
            .replace("34-35,70-71", DAEMON_CPUS)
            .replace("mysql_job_native_duration_successor_bundle_v14", SCHEMA)
            .encode("utf-8"))


def verify_parent():
    manifest_path = PARENT / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    unsigned = dict(manifest); unsigned.pop("bundle_id", None)
    if (manifest.get("bundle_id") != PARENT_ID or digest(manifest_path) != PARENT_SHA
            or sha_bytes(canonical(unsigned)) != PARENT_ID
            or manifest.get("schema_version") != "mysql_job_native_duration_successor_bundle_v14"):
        raise BuildError("JOB v14 parent identity differs")
    for relative, expected in manifest["files"].items():
        path = PARENT / relative
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise BuildError("JOB v14 closure differs: " + relative)
    return manifest


def patch_runtime(payload):
    value = json.loads(payload.decode("utf-8"))
    value["host"] = {"hostname": "node04", "address": "192.0.2.7"}
    value["bundle_remote_path_rule"] = RUNTIME_ROOT + "/bundles/<bundle_id>"
    value["storage"].update({"device": DEVICE, "expected_live_major_minor": MAJOR_MINOR,
                             "mount": MOUNT, "runtime_root": RUNTIME_ROOT,
                             "template_path": PROJECT + "/template"})
    value["resource_envelope"].update({"cpu_quota_cores": 1.15,
                                        "cpuset_cpus": DB_CPUS, "cpuset_mems": "1",
                                        "mysql_port": 65102})
    value["client_resource_envelope"].update({"cpu_quota_cores": 6,
                                               "cpuset_cpus": CLIENT_CPUS,
                                               "cpuset_mems": "1"})
    tree = value["toolchain"]["python_tree_content_sha256"]
    pyroot = RUNTIME_ROOT + "/runtime/python311-" + tree
    value["toolchain"].update({"python_runtime_root": pyroot,
                                "python_runtime": pyroot + "/bin/python3.11"})
    value["qualification_policy"]["target_infrastructure_dockerd_is_foreign_writer"] = False
    value["qualification_policy"]["unrelated_foreign_target_device_writer_bytes_max"] = 0
    value["target_infrastructure"] = {
        "role": "target_infrastructure", "unit": "ra-docker-mysql-job-v1.service",
        "socket": "/run/ra-docker-mysql-job-v1.sock", "data_root": DAEMON_ROOT,
        "device": DEVICE, "device_major_minor": MAJOR_MINOR,
        "cpuset_cpus": DAEMON_CPUS, "cpuset_mems_effective_readback_required": True,
        "exact_pid_starttime_cmdline_cgroup_identity_required": True,
        "descendants_inherit_target_infrastructure_role": True,
        "writable_device_fds_allowed_only_for_exact_infrastructure_tree": True,
    }
    return canonical(value)


def patch_contract(payload):
    value = json.loads(payload.decode("utf-8"))
    job = value["scenarios"]["job"]
    job["host"] = {"hostname": "node04", "address": "192.0.2.7"}
    job["storage"].update({"device": DEVICE, "expected_live_major_minor": MAJOR_MINOR,
                           "mount": MOUNT, "runtime_root": RUNTIME_ROOT,
                           "template_path": PROJECT + "/template"})
    job["resource_envelope"].update({"cpu_quota_cores": 1.15,
                                     "cpuset_cpus": DB_CPUS, "cpuset_mems": "1",
                                     "mysql_port": 65102})
    job["client_resource_envelope"].update({"cpu_quota_cores": 6,
                                            "cpuset_cpus": CLIENT_CPUS,
                                            "cpuset_mems": "1"})
    job["target_infrastructure"] = {
        "kind": "dedicated_dockerd", "cpuset_cpus": DAEMON_CPUS,
        "data_root": DAEMON_ROOT, "device": DEVICE,
        "device_major_minor": MAJOR_MINOR, "foreign_writer": False,
        "exact_identity_and_descendant_closure_required": True,
    }
    same_host = value["same_host_parallel_policy"]
    same_host["policy_version"] = "mysql_algorithm_c_same_host_bidirectional_interference_gate_v2_job_node04_relocation"
    same_host["pairs"] = [
        {"host": "node04", "scenarios": ["job", "tpch"],
         "serial_fallback_lock": MOUNT + "/anonymous/resource_adaptive_mysql/host_control/job_tpch_serial_fallback.lock"},
        {"host": "node04", "scenarios": ["job", "tpcc"],
         "serial_fallback_lock": MOUNT + "/anonymous/resource_adaptive_mysql/host_control/job_tpcc_serial_fallback.lock"},
        {"host": "node04", "scenarios": ["tpch", "tpcc"],
         "serial_fallback_lock": "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/host_control/tpch_tpcc_serial_fallback.lock"},
    ]
    same_host["job_relocation_static_gate_note"] = (
        "JOB shares node04 with TPCH/TPCC; any CPU-sibling or client-lane overlap "
        "fails the static gate and requires the pair-specific serial fallback lock")
    value["status"] = "OFFLINE_JOB_NODE04_NVME2_RELOCATION_NOT_LAUNCH_AUTHORITY"
    return canonical(value)


def patch_campaign(payload):
    source = payload.decode("utf-8")
    old = '''    "job": Lane(
        "job", "job_memory", "job", "anonymous@192.0.2.4", "node02",
        "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job",
        "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/template",
        "/dev/nvme1n1", "0-2,36-38", "0", 3, 6442450944, 512,
        1205862400, 1205862400, 65102, "imdb_job", "memory", "Com_select",
        (str(HISTORY / "job/job_plan_effect_t_20260723T003507Z/T.mysql_job_physical_plan_effect_v1.json"),),
    ),
'''
    new = '''    "job": Lane(
        "job", "job_memory", "job", "anonymous@192.0.2.7", "node04",
        "/mnt/nvme2n1/anonymous/resource_adaptive_mysql/job",
        "/mnt/nvme2n1/anonymous/resource_adaptive_mysql/job/template",
        "/dev/nvme2n1", "18-20,54-56", "1", 1.15, 4294967296, 512,
        7340032, 7340032, 65102, "imdb_job", "memory", "Com_select",
        (str(HISTORY / "job/job_plan_effect_t_20260723T003507Z/T.mysql_job_physical_plan_effect_v1.json"),),
        3221225472,
    ),
'''
    if source.count(old) != 1:
        raise BuildError("manual JOB campaign lane anchor differs")
    return source.replace(old, new).encode("utf-8")


def patch_collector(payload):
    source = payload.decode("utf-8")
    anchor = '''CORESIDENT_PROCESS_PATTERN = re.compile(
    r"(postgres|mysqld|mariadbd|mysql|pgbench|sysbench|benchbase|"
    r"hybench|tpch|tpcc|algorithm[_-]?[abc]|benchmark)",
    re.IGNORECASE,
)
'''
    addition = anchor + '''TARGET_INFRASTRUCTURE_DAEMON_CPUS = "16-17,52-53"
TARGET_INFRASTRUCTURE_DAEMON_COMMAND = (
    "/usr/bin/dockerd --host=unix:///run/ra-docker-mysql-job-v1.sock "
    "--data-root=/mnt/nvme2n1/anonymous/resource_adaptive_mysql/docker_ra_job_v1 "
    "--iptables=false --bridge=none"
)
TARGET_INFRASTRUCTURE_DAEMON_UNIT_SUFFIX = "/ra-docker-mysql-job-v1.service"

'''
    if source.count(anchor) != 1:
        raise BuildError("collector constants anchor differs")
    source = source.replace(anchor, addition)
    anchor = '''    mysql_cpus = expand_cpuset(mysql_cpuset)
    client_cpus = expand_cpuset(workload_cpuset)
    expected_mems = expand_cpuset(workload_mems)
    rows = []
    conflicts = []
    permission_failures = 0
    foreign_writer_count = 0
'''
    replacement = '''    mysql_cpus = expand_cpuset(mysql_cpuset)
    client_cpus = expand_cpuset(workload_cpuset)
    expected_mems = expand_cpuset(workload_mems)
    daemon_cpus = expand_cpuset(TARGET_INFRASTRUCTURE_DAEMON_CPUS)
    infrastructure_roots = {
        pid for pid, row in all_processes.items()
        if row["comm"] == "dockerd"
        and row["_command"] == TARGET_INFRASTRUCTURE_DAEMON_COMMAND
        and expand_cpuset(row["cpus_allowed_list"]) == daemon_cpus
        and row["container_identity"] is None
        and any(path.endswith(TARGET_INFRASTRUCTURE_DAEMON_UNIT_SUFFIX)
                for path in row["_cgroup_paths"])
    }
    infrastructure_tree = set(infrastructure_roots)
    changed = True
    while changed:
        changed = False
        for pid, row in all_processes.items():
            if (row["ppid"] in infrastructure_tree
                    and row["container_identity"] is None
                    and pid not in infrastructure_tree):
                infrastructure_tree.add(pid)
                changed = True
    rows = []
    conflicts = [] if len(infrastructure_roots) == 1 else ["target_infrastructure_daemon_identity"]
    permission_failures = 0
    foreign_writer_count = 0
    target_infrastructure_writer_count = 0
'''
    if source.count(anchor) != 1:
        raise BuildError("collector inventory setup anchor differs")
    source = source.replace(anchor, replacement)
    source = source.replace('''            or pid in control_plane
            or container_row is not None
''', '''            or pid in control_plane
            or pid in infrastructure_tree
            or container_row is not None
''', 1)
    source = source.replace('''        elif pid in control_plane:
            role = "qualification_control_plane"
        else:
            role = "co_resident"
''', '''        elif pid in control_plane:
            role = "qualification_control_plane"
        elif pid in infrastructure_tree:
            role = "target_infrastructure"
        else:
            role = "co_resident"
''', 1)
    source = source.replace('''        elif role == "qualification_workload":
            if actual_cpus != client_cpus or actual_mems != expected_mems:
                conflicts.append("workload_affinity_or_mems")
        if role == "co_resident" and (
''', '''        elif role == "qualification_workload":
            if actual_cpus != client_cpus or actual_mems != expected_mems:
                conflicts.append("workload_affinity_or_mems")
        elif role == "target_infrastructure":
            if actual_cpus != daemon_cpus:
                conflicts.append("target_infrastructure_affinity")
            if writable_fds > 0:
                target_infrastructure_writer_count += 1
        if role == "co_resident" and (
''', 1)
    source = source.replace('''        "foreign_writer_count": foreign_writer_count,
        "docker_container_count": len(docker_containers),
''', '''        "foreign_writer_count": foreign_writer_count,
        "target_infrastructure_daemon_count": len(infrastructure_roots),
        "target_infrastructure_tree_pids": sorted(infrastructure_tree),
        "target_infrastructure_writer_count": target_infrastructure_writer_count,
        "target_infrastructure_is_foreign_writer": False,
        "docker_container_count": len(docker_containers),
''', 1)
    source = source.replace('''            or rolling.get("foreign_writer_count") != 0
        ):
''', '''            or rolling.get("foreign_writer_count") != 0
            or rolling.get("target_infrastructure_daemon_count") != 1
            or rolling.get("target_infrastructure_is_foreign_writer") is not False
        ):
''', 1)
    source = source.replace('''            "foreign_writer_count": 0,
            "target_cgroup_paths_sha256": baseline_target_cgroup,
''', '''            "foreign_writer_count": 0,
            "target_infrastructure_daemon_count": 1,
            "target_infrastructure_is_foreign_writer": False,
            "target_cgroup_paths_sha256": baseline_target_cgroup,
''', 1)
    return source.encode("utf-8")


def patch_monitor(payload):
    source = payload.decode("utf-8")
    anchor = '''        or rolling.get("foreign_writer_count") != 0
    ):
'''
    replacement = '''        or rolling.get("foreign_writer_count") != 0
        or rolling.get("target_infrastructure_daemon_count") != 1
        or rolling.get("target_infrastructure_is_foreign_writer") is not False
    ):
'''
    if source.count(anchor) != 1:
        raise BuildError("rolling monitor inventory gate anchor differs")
    return source.replace(anchor, replacement).encode("utf-8")


TEST_SOURCE = r'''from __future__ import annotations
import hashlib,json,stat
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def cpus(value):
 out=set()
 for part in value.split(","):
  ends=part.split("-");out.update(range(int(ends[0]),int(ends[-1])+1))
 return out
def test_relocation_identity_envelopes_and_zero_credit():
 m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")
 raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
 assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()
 assert m["schema_version"]=="mysql_job_node04_nvme2_relocation_successor_bundle_v15"
 assert m["formal_scope"]=="mysql_only" and m["cross_engine_comparison_prohibited"] is True
 assert m["postgresql_mutation_prohibited"] is True and m["remote_actions"]==0
 for key in ("qualification_credit","r0_credit","candidate_credit","top5_replay_credit","accepted_rounds_credit"):assert m[key]==0
 assert m["formal_launch_authorized"] is False and m["accepted_round_authorized"] is False
 d=json.loads((ROOT/"distributed_runtime_contract.json").read_text())
 assert d["host"]=={"address":"192.0.2.7","hostname":"node04"}
 assert d["storage"]=={**d["storage"],"device":"/dev/nvme2n1","expected_live_major_minor":"259:3","mount":"/mnt/nvme2n1"}
 assert d["resource_envelope"]["cpuset_cpus"]=="18-20,54-56" and d["resource_envelope"]["cpu_quota_cores"]==1.15
 assert d["client_resource_envelope"]["cpuset_cpus"]=="21-23,57-59" and d["client_resource_envelope"]["cpu_quota_cores"]==6
 infra=d["target_infrastructure"];assert infra["cpuset_cpus"]=="16-17,52-53" and infra["role"]=="target_infrastructure"
 cfg=json.loads((ROOT/"config.json").read_text())
 assert cfg["schema_version"]=="mysql_job_distributed_v2_bootstrap_config_v1"
 assert cfg["fresh_r0_authorized"] is False and cfg["formal10_authorized"] is False
 assert all(a.isdisjoint(b) for i,a in enumerate(map(cpus,("18-20,54-56","21-23,57-59","16-17,52-53"))) for b in list(map(cpus,("18-20,54-56","21-23,57-59","16-17,52-53")))[i+1:])
def test_exact_closure_readonly_and_no_bytecode():
 m=json.loads((ROOT/"bundle_manifest.json").read_text())
 actual={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.rglob("*") if p.is_file() and p.relative_to(ROOT).as_posix() not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
 assert actual==m["files"] and not list(ROOT.rglob("__pycache__")) and not list(ROOT.rglob("*.pyc"))
 for p in (ROOT,*ROOT.rglob("*")):assert not p.is_symlink() and stat.S_IMODE(p.stat().st_mode)&0o222==0
def test_dedicated_dockerd_is_target_infrastructure_not_foreign():
 s=(ROOT/"qualification/mysql_job_distributed_live_readback_qualification_v2.py").read_text()
 assert 'role = "target_infrastructure"' in s
 assert '"target_infrastructure_is_foreign_writer": False' in s
 assert 'target_infrastructure_writer_count += 1' in s
 assert 'TARGET_INFRASTRUCTURE_DAEMON_UNIT_SUFFIX' in s
 assert 'row["container_identity"] is None' in s
 monitor=(ROOT/"qualification/mysql_job_distributed_v2_rolling_monitor.py").read_text()
 assert 'rolling.get("target_infrastructure_daemon_count") != 1' in monitor
 assert 'rolling.get("target_infrastructure_is_foreign_writer") is not False' in monitor
 assert 'TARGET_INFRASTRUCTURE_DAEMON_CPUS = "16-17,52-53"' in s
 assert '/mnt/nvme2n1/anonymous/resource_adaptive_mysql/docker_ra_job_v1' in s
def test_algorithm_closure_unchanged():
 c=json.loads((ROOT/"authority/mysql_algorithm_c_distributed_four_scene_successor_contract_v2_20260729.json").read_text())
 a=c["algorithm_invariants"]
 assert a["gradient_formula"]=="g_j = sum_r(dScore/dU_r * T[j,r] * confidence)"
 assert a["enumerate_all_nonempty_subspaces"] is True and a["fixed_top_k_replay"]==5
 pairs=c["same_host_parallel_policy"]["pairs"]
 assert {(tuple(p["scenarios"])) for p in pairs}=={("job","tpch"),("job","tpcc"),("tpch","tpcc")}
 campaign=(ROOT/"qualification/shell/manual_mysql_sgd_r0_campaign.py").read_text()
 assert '"anonymous@192.0.2.7", "node04"' in campaign
 assert '"/dev/nvme2n1", "18-20,54-56", "1", 1.15, 4294967296, 512' in campaign
'''.encode("utf-8")


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink in JOB v15")
        path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
    root.chmod(stat.S_IMODE(root.stat().st_mode) & ~0o222)


def main():
    parent = verify_parent()
    payload = {relative: (PARENT / relative).read_bytes()
               for relative in parent["files"] if relative != "closure.json"}
    modes = {relative: stat.S_IMODE((PARENT / relative).stat().st_mode)
             for relative in payload}
    active_text = (
        "controller/render_mysql_job_v32_bootstrap_spec_tmpfs_v2.py",
        "controller/render_mysql_job_v32_qualification_solo_tmpfs_v2.py",
        "qualification/mysql_job_dedicated_daemon_contract_v1.py",
        "authority/mysql_job_dedicated_daemon_contract_v1.py",
        "qualification/mysql_job_dedicated_daemon_materializer_v5.py",
        "qualification/mysql_job_distributed_v2_adopted_services.py",
        "qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py",
        "qualification/mysql_job_distributed_v2_rolling_monitor.py",
        "qualification/mysql_sql_debug_metric_adapter_v1.py",
    )
    payload["distributed_runtime_contract.json"] = patch_runtime(payload["distributed_runtime_contract.json"])
    payload[CONTRACT] = patch_contract(payload[CONTRACT])
    old_contract_sha = digest(PARENT / CONTRACT)
    new_contract_sha = sha_bytes(payload[CONTRACT])
    for relative in active_text:
        payload[relative] = text_patch(payload[relative]).replace(
            old_contract_sha.encode("ascii"), new_contract_sha.encode("ascii"))
    payload["qualification/mysql_job_distributed_live_readback_qualification_v2.py"] = patch_collector(
        text_patch(payload["qualification/mysql_job_distributed_live_readback_qualification_v2.py"]))
    monitor = "qualification/mysql_job_distributed_v2_rolling_monitor.py"
    payload[monitor] = patch_monitor(payload[monitor])
    campaign = "qualification/shell/manual_mysql_sgd_r0_campaign.py"
    payload[campaign] = patch_campaign(payload[campaign])
    fixture = replace_tree(json.loads(payload["qualification/fixtures/mysql_job_dedicated_daemon_readback_fixture_v1.json"].decode("utf-8")))
    payload["qualification/fixtures/mysql_job_dedicated_daemon_readback_fixture_v1.json"] = canonical(fixture)
    config = json.loads((PARENT / "config.json").read_text(encoding="utf-8"))
    config.update({"formal_scope":"mysql_only", "cross_engine_comparison_prohibited":True,
                   "postgresql_mutation_prohibited":True, "fresh_r0_authorized":False,
                   "formal10_authorized":False, "accepted_round_authorized":False})
    payload["config.json"] = canonical(config); modes["config.json"] = 0o444
    for relative in list(payload):
        if relative.startswith("tests/"):
            payload[relative] = text_patch(payload[relative])
    disjoint_test = "tests/test_mysql_job_xp_daemon_lane_disjoint_v13.py"
    payload[disjoint_test] = payload[disjoint_test].replace(
        b'assert m["mysql_xp_dedicated_daemon_cpuset"]=="16-17,52-53"',
        b'assert m["mysql_xp_dedicated_daemon_cpuset"]=="34-35,70-71"').replace(
        b'assert "16-17,52-53" not in s',
        b'assert "34-35,70-71" not in s')
    payload[BUILDER] = Path(__file__).read_bytes(); modes[BUILDER] = 0o555
    payload[TEST] = TEST_SOURCE; modes[TEST] = 0o444
    payload[INVALIDATION] = canonical({
        "schema_version":"mysql_job_node02_pg_conflict_relocation_v1",
        "status":"INVALIDATED_ZERO_CREDIT_DO_NOT_TOUCH_POSTGRESQL",
        "predecessor_bundle_id":PARENT_ID,
        "reason":"node02 PostgreSQL formal Java is active with cpuset 0-111; JOB gate conflicts",
        "replacement_host":"node04", "replacement_device":DEVICE,
        "postgresql_mutation_prohibited":True, "remote_actions":0,
        "qualification_credit":0,"r0_credit":0,"candidate_credit":0,
        "top5_replay_credit":0,"accepted_rounds_credit":0,
    }); modes[INVALIDATION] = 0o444
    relocation = {
        "schema_version":"mysql_job_node04_nvme2_relocation_contract_v1",
        "status":"SEALED_OFFLINE_NOT_LAUNCHED", "formal_scope":"mysql_only",
        "cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,
        "host":{"hostname":"node04","address":"192.0.2.7"},
        "storage":{"mount":MOUNT,"device":DEVICE,"major_minor":MAJOR_MINOR,
                   "project":PROJECT,"runtime_root":RUNTIME_ROOT,"daemon_root":DAEMON_ROOT},
        "database":{"cpuset_cpus":DB_CPUS,"cpuset_mems":"1","cpu_quota_cores":1.15,"mysql_port":65102},
        "client":{"cpuset_cpus":CLIENT_CPUS,"cpuset_mems":"1","cpu_quota_cores":6},
        "dedicated_dockerd":{"cpuset_cpus":DAEMON_CPUS,"role":"target_infrastructure",
                             "foreign_writer":False,"exact_identity_required":True},
        "algorithm":{"all_nonempty_subsets":15,"fixed_top_k":5,"zero_credit":True},
        "formal_launch_authorized":False,"accepted_round_authorized":False,"remote_actions":0,
    }
    payload[RELOCATION] = canonical(relocation); modes[RELOCATION] = 0o444
    closure = replace_tree(json.loads((PARENT / "closure.json").read_text(encoding="utf-8")))
    closure.update({"schema_version":"mysql_job_module_closure_v15",
                    "predecessor_bundle_id":PARENT_ID,
                    "predecessor_bundle_manifest_sha256":PARENT_SHA,
                    "active_config":"config.json","relocation":relocation})
    closure["files"] = {relative: sha_bytes(data) for relative, data in sorted(payload.items())}
    payload["closure.json"] = canonical(closure); modes["closure.json"] = 0o444
    identity = dict(parent); identity.pop("bundle_id", None)
    identity.update({
        "schema_version":SCHEMA,
        "status":"SEALED_JOB_V15_NODE04_NVME2_RELOCATION_NOT_LAUNCHED",
        "predecessor_bundle_id":PARENT_ID,"predecessor_bundle_manifest_sha256":PARENT_SHA,
        "formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,
        "postgresql_mutation_prohibited":True,"formal_launch_authorized":False,
        "formal10_authorized":False,"accepted_round_authorized":False,
        "qualification_credit":0,"r0_credit":0,"candidate_credit":0,
        "top5_replay_credit":0,"accepted_rounds_credit":0,"remote_actions":0,
        "relocation_contract_sha256":sha_bytes(payload[RELOCATION]),
        "distributed_runtime_contract_sha256":sha_bytes(payload["distributed_runtime_contract.json"]),
        "active_config":"config.json",
        "dedicated_daemon_contract":{
            "socket":"/run/ra-docker-mysql-job-v1.sock","daemon_root":DAEMON_ROOT,
            "device_major_minor":MAJOR_MINOR,"daemon_cpuset_cpus":DAEMON_CPUS,
            "target_infrastructure":True,"foreign_writer":False,
            "db_client_network":"host","mysql_server_port":65102,
            "docker_argv_prefix":["docker","--host","unix:///run/ra-docker-mysql-job-v1.sock"]},
        "files":{relative: sha_bytes(data) for relative, data in sorted(payload.items())},
    })
    bundle_id = sha_bytes(canonical(identity)); manifest = dict(identity, bundle_id=bundle_id)
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / bundle_id
    if destination.exists():
        if json.loads((destination / "bundle_manifest.json").read_text()) != manifest:
            raise BuildError("existing JOB v15 identity differs")
        print(json.dumps({"status":manifest["status"],"bundle":str(destination),
                          "bundle_id":bundle_id,"manifest_sha256":digest(destination/"bundle_manifest.json")},sort_keys=True))
        return
    stage = OUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6)); stage.mkdir(mode=0o700)
    try:
        for relative, data in payload.items():
            path = stage / relative; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data); path.chmod(modes.get(relative, 0o444) & ~0o222)
        raw = canonical(manifest); (stage/"bundle_manifest.json").write_bytes(raw)
        (stage/"bundle_manifest.json.sha256").write_text(sha_bytes(raw)+"  bundle_manifest.json\n",encoding="ascii")
        freeze(stage); os.rename(stage, destination)
    except BaseException:
        if stage.exists(): shutil.rmtree(stage)
        raise
    print(json.dumps({"status":manifest["status"],"bundle":str(destination),
                      "bundle_id":bundle_id,"manifest_sha256":digest(destination/"bundle_manifest.json")},sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (BuildError, OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit("NOT_READY: %s" % exc)

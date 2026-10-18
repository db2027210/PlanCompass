#!/usr/bin/env python3
"""Build the sealed JOB/mysql-only node01 deployment successor.

This builder is deliberately offline.  It neither connects to node01 nor
formats, mounts, publishes, or launches anything.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "4b000c2f0ab3a322db7baf407a4bab03798b504b2986ff684c885938f5b12697"
PARENT_SHA = "f1e784a1b9d902f7ed6735f3d7a0101746a25910017b3b60740e268ff0c9cf23"
PARENT = ROOT / "artifacts/mysql_job_raw_all_subset_three_level_successor_v14" / PARENT_ID
OUT = ROOT / "artifacts/mysql_job_node01_deployment_successor_v15"
SCHEMA = "mysql_job_node01_deployment_successor_bundle_v15"

HOST = "node01"
ADDRESS = "192.0.2.3"
SSH_TARGET = "anonymous@192.0.2.3"
DEVICE = "/dev/nvme2n1"
MAJOR_MINOR = "259:0"
SERIAL = "20395B803410"
WWN = "eui.e8238fa6bf530001001b448b49c4281c"
MOUNT = "/mnt/nvme2n1"
PROJECT = MOUNT + "/anonymous/resource_adaptive_mysql/job"
RUNTIME = PROJECT + "/runtime_algorithm_c_successor_20260803"
DAEMON_ROOT = MOUNT + "/anonymous/resource_adaptive_mysql/docker_ra_job_v1"
DB_CPUS = "18-20,54-56"
CLIENT_CPUS = "21-23,57-59"
DAEMON_CPUS = "16-17,52-53"
MEMS = "1"
PLANNER_PORT = 65102
REPLAY_PORT = 65202


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(path):
    return sha(Path(path).read_bytes())


def verify_parent():
    path = PARENT / "bundle_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    unsigned = dict(manifest); unsigned.pop("bundle_id", None)
    if (manifest.get("bundle_id") != PARENT_ID or digest(path) != PARENT_SHA
            or sha(canonical(unsigned)) != PARENT_ID
            or manifest.get("schema_version")
            != "mysql_job_raw_all_subset_three_level_successor_bundle_v14"):
        raise BuildError("JOB algorithm v14 identity differs")
    for relative, expected in manifest["files"].items():
        path = PARENT / relative
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise BuildError("JOB algorithm v14 closure differs: " + relative)
    return manifest


def replace_active_text(data):
    return (data.decode("utf-8")
            .replace("anonymous@192.0.2.4", SSH_TARGET)
            .replace("192.0.2.4", ADDRESS)
            .replace("node02", HOST)
            .replace("/mnt/mysql_nvme1n1", MOUNT)
            .replace("/dev/nvme1n1", DEVICE)
            .replace("0-2,36-38", CLIENT_CPUS)
            .replace("34-35,70-71", DAEMON_CPUS)
            .encode("utf-8"))


def patch_runtime(data):
    value = json.loads(data)
    value["host"] = {"hostname": HOST, "address": ADDRESS}
    value["bundle_remote_path_rule"] = RUNTIME + "/bundles/<bundle_id>"
    value["storage"].update({
        "device": DEVICE, "expected_live_major_minor": MAJOR_MINOR,
        "mount": MOUNT, "runtime_root": RUNTIME,
        "template_path": PROJECT + "/template",
        "expected_serial": SERIAL, "expected_wwn": WWN,
        "required_filesystem": "xfs", "format_authority_required": True,
    })
    value["resource_envelope"].update({
        "cpu_quota_cores": 1.15, "cpuset_cpus": DB_CPUS,
        "cpuset_mems": MEMS, "memory_bytes": 4294967296,
        "memory_swap_bytes": 4294967296, "pids_max": 512,
        "io_read_bps": 7340032, "io_write_bps": 7340032,
        "innodb_buffer_pool_bytes": 3221225472, "mysql_port": PLANNER_PORT,
    })
    value["client_resource_envelope"].update({
        "cpu_quota_cores": 6, "cpuset_cpus": CLIENT_CPUS,
        "cpuset_mems": MEMS,
    })
    tree = value["toolchain"]["python_tree_content_sha256"]
    pyroot = RUNTIME + "/runtime/python311-" + tree
    value["toolchain"].update({
        "python_runtime_root": pyroot,
        "python_runtime": pyroot + "/bin/python3.11",
    })
    value["target_infrastructure"] = {
        "kind": "dedicated_dockerd", "role": "target_infrastructure",
        "unit": "ra-docker-mysql-job-v1.service",
        "socket": "/run/ra-docker-mysql-job-v1.sock",
        "data_root": DAEMON_ROOT, "device": DEVICE,
        "device_major_minor": MAJOR_MINOR, "cpuset_cpus": DAEMON_CPUS,
        "cpuset_mems": "0", "foreign_writer": False,
        "exact_pid_starttime_cmdline_cgroup_identity_required": True,
    }
    value["qualification_policy"].update({
        "dynamic_interference_gate_version":
            "mysql_only_rolling_noninterference_v1",
        "all_co_resident_database_and_benchmark_processes_required": True,
        "postgresql_inventory_read_only": True,
        "postgresql_mutation_prohibited": True,
    })
    value["formal_launch_authorized"] = False
    value["accepted_round_authorized"] = False
    return canonical(value)


def patch_four_scene_contract(data):
    value = json.loads(data)
    job = value["scenarios"]["job"]
    job["host"] = {"hostname": HOST, "address": ADDRESS}
    job["storage"].update({
        "device": DEVICE, "expected_live_major_minor": MAJOR_MINOR,
        "mount": MOUNT, "runtime_root": RUNTIME,
        "template_path": PROJECT + "/template",
        "expected_serial": SERIAL, "expected_wwn": WWN,
        "required_filesystem": "xfs", "format_authority_required": True,
    })
    job["resource_envelope"].update({
        "cpu_quota_cores": 1.15, "cpuset_cpus": DB_CPUS,
        "cpuset_mems": MEMS, "memory_bytes": 4294967296,
        "memory_swap_bytes": 4294967296, "pids_max": 512,
        "io_read_bps": 7340032, "io_write_bps": 7340032,
        "innodb_buffer_pool_bytes": 3221225472, "mysql_port": PLANNER_PORT,
    })
    job["client_resource_envelope"].update({
        "cpuset_cpus": CLIENT_CPUS, "cpuset_mems": MEMS,
        "cpu_quota_cores": 6,
    })
    job["target_infrastructure"] = {
        "kind":"dedicated_dockerd", "cpuset_cpus":DAEMON_CPUS,
        "cpuset_mems":"0", "data_root":DAEMON_ROOT,
        "device":DEVICE, "device_major_minor":MAJOR_MINOR,
        "foreign_writer":False, "exact_identity_required":True,
    }
    job["formal_launch_authorized"] = False
    policy = value["same_host_parallel_policy"]
    policy["policy_version"] = "mysql_algorithm_c_mysql_only_job_node01_noninterference_v1"
    policy["pairs"] = [p for p in policy["pairs"] if "job" not in p["scenarios"]]
    policy["job_node01_policy"] = {
        "postgresql_container":"pg5_tpch_ap_slot_node01_20260726",
        "postgresql_cpuset":"0-15,36-51", "postgresql_port":63301,
        "postgresql_mutation_prohibited":True,
        "logical_cpuset_intersection_required_empty":True,
        "rolling_dynamic_noninterference_gate_required":True,
        "cross_engine_comparison_prohibited":True,
    }
    value["status"] = "OFFLINE_JOB_NODE01_DEPLOYMENT_NOT_LAUNCH_AUTHORITY"
    return canonical(value)


PUBLISHER = r'''#!/usr/bin/env python3
"""Add one manifest-bound bundle transactionally; never delete or overwrite."""
import argparse,hashlib,json,os,secrets,shutil,stat
from pathlib import Path
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def fail(msg):raise SystemExit("NOT_PUBLISHED: "+msg)
p=argparse.ArgumentParser();p.add_argument("--source",type=Path,required=True);p.add_argument("--destination-root",type=Path,required=True);p.add_argument("--apply",action="store_true");a=p.parse_args()
mfile=a.source/"bundle_manifest.json"
if a.source.is_symlink() or not mfile.is_file():fail("source manifest missing")
m=json.loads(mfile.read_text());bid=m.get("bundle_id")
if not isinstance(bid,str) or len(bid)!=64:fail("bundle id invalid")
u=dict(m);u.pop("bundle_id",None)
raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
if hashlib.sha256(raw).hexdigest()!=bid:fail("manifest identity mismatch")
for rel,want in m.get("files",{}).items():
 q=a.source/rel
 if q.is_symlink() or not q.is_file() or digest(q)!=want:fail("closure mismatch: "+rel)
dst=a.destination_root/bid
if dst.exists():
 if dst.is_symlink() or not (dst/"bundle_manifest.json").is_file() or digest(dst/"bundle_manifest.json")!=digest(mfile):fail("existing destination differs")
 print(json.dumps({"status":"ALREADY_PRESENT_IDENTICAL","bundle":str(dst),"bundle_id":bid},sort_keys=True));raise SystemExit
if not a.apply:
 print(json.dumps({"status":"DRY_RUN_NOT_PUBLISHED","destination":str(dst),"bundle_id":bid},sort_keys=True));raise SystemExit
a.destination_root.mkdir(parents=True,exist_ok=True)
stage=a.destination_root/(".stage-"+bid+"-"+secrets.token_hex(6));stage.mkdir(mode=0o700)
try:
 for rel in sorted(m["files"]):
  src=a.source/rel;out=stage/rel;out.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,out);out.chmod(stat.S_IMODE(src.stat().st_mode)&~0o222)
 shutil.copyfile(mfile,stage/"bundle_manifest.json")
 if (a.source/"bundle_manifest.json.sha256").is_file():shutil.copyfile(a.source/"bundle_manifest.json.sha256",stage/"bundle_manifest.json.sha256")
 for rel,want in m["files"].items():
  if digest(stage/rel)!=want:fail("staged verification failed: "+rel)
 os.rename(stage,dst)
except BaseException:
 if stage.exists():shutil.rmtree(stage)
 raise
print(json.dumps({"status":"PUBLISHED_ATOMIC_NEW_CONTENT_ADDRESS","bundle":str(dst),"bundle_id":bid},sort_keys=True))
'''.encode()


GATE = r'''#!/usr/bin/env python3
"""Fail-closed offline verifier for a node01 JOB launch snapshot."""
import argparse,hashlib,json,re,sys
from pathlib import Path
EXPECTED_BUNDLE=Path(__file__).resolve().parents[1].name
ACTUAL_MANIFEST_SHA=hashlib.sha256((Path(__file__).resolve().parents[1]/"bundle_manifest.json").read_bytes()).hexdigest()
HEX=re.compile(r"^[0-9a-f]{64}$")
def cpus(s):
 out=set()
 if not isinstance(s,str) or not s:return out
 for part in s.split(","):
  try:
   ends=part.split("-");out.update(range(int(ends[0]),int(ends[-1])+1))
  except (TypeError,ValueError):return set()
 return out
def check(ok,msg,errors):
 if not ok:errors.append(msg)
p=argparse.ArgumentParser();p.add_argument("snapshot",type=Path);a=p.parse_args();d=json.loads(a.snapshot.read_text());e=[]
check(d.get("schema_version")=="mysql_job_node01_prelaunch_snapshot_v1","schema",e)
check(d.get("formal_scope")=="mysql_only","scope",e);check(d.get("cross_engine_comparison_prohibited") is True,"cross-engine prohibition",e)
check(d.get("postgresql_mutation_prohibited") is True,"PG mutation prohibition",e)
check(d.get("host")=={"hostname":"node01","address":"192.0.2.3"},"host",e)
b=d.get("bundle",{});check(b.get("bundle_id")==EXPECTED_BUNDLE and b.get("manifest_sha256")==ACTUAL_MANIFEST_SHA and b.get("published_verified") is True,"published bundle",e)
s=d.get("storage",{});check((s.get("device"),s.get("major_minor"),s.get("serial"),s.get("wwn"))==("/dev/nvme2n1","259:0","20395B803410","eui.e8238fa6bf530001001b448b49c4281c"),"device identity",e)
root=s.get("root_readonly_probe",{});check(root.get("wipefs_signatures")==[] and root.get("blkid_signatures")==[] and root.get("fuser_pids")==[] and root.get("openers")==[],"root pre-format signature/openers proof",e)
auth=s.get("format_authority",{});check(auth.get("authorized") is True and isinstance(auth.get("authority_id"),str) and bool(auth.get("authority_id")),"separate format authority",e)
mount=s.get("post_format_mount_readback",{});check(mount.get("target")=="/mnt/nvme2n1" and mount.get("source_major_minor")=="259:0" and mount.get("fstype")=="xfs" and mount.get("rw") is True,"post-format mount readback",e)
r=d.get("resource_spec",{});check(r=={"cpuset_cpus":"18-20,54-56","cpuset_mems":"1","cpu_quota_cores":1.15,"memory_bytes":4294967296,"memory_swap_bytes":4294967296,"pids_max":512,"io_read_bps":7340032,"io_write_bps":7340032,"innodb_buffer_pool_bytes":3221225472,"mysql_port":65102,"replay_port":65202},"resource spec",e)
top=d.get("topology",{});check(all(str(c) in top.get("cpu_to_node",{}) and top["cpu_to_node"][str(c)]==1 for c in cpus("18-20,54-56")),"DB NUMA topology",e)
check(set(d.get("free_ports",[]))>={65102,65202},"ports not free",e)
inv=d.get("process_inventory",{});check(inv.get("complete") is True and inv.get("real_pids_only") is True,"complete PID inventory",e)
records=inv.get("records",[]);check(bool(records) and all(isinstance(x.get("pid"),int) and x["pid"]>0 and isinstance(x.get("start_ticks"),int) and x["start_ticks"]>0 and bool(x.get("cmdline")) and bool(x.get("cpuset_cpus")) for x in records),"real PID/start/cmdline/cpuset records",e)
pg=inv.get("postgresql",[]);check(bool(pg) and all(isinstance(x.get("pid"),int) and x["pid"]>0 and bool(x.get("cpuset_cpus")) and x.get("mutation_performed") is False for x in pg),"PG read-only inventory",e)
ours=cpus("18-20,54-56")|cpus("21-23,57-59")|cpus("16-17,52-53")
check(all(ours.isdisjoint(cpus(x.get("cpuset_cpus",""))) for x in pg),"PG CPU overlap",e)
check(all(ours.isdisjoint(cpus(x.get("cpuset_cpus",""))) for x in records),"co-resident database/benchmark CPU overlap",e)
dyn=d.get("dynamic_interference_gate",{});check(dyn.get("version")=="mysql_only_rolling_noninterference_v1" and dyn.get("duration_seconds",0)>=180 and dyn.get("sample_count",0)>=181 and dyn.get("passed") is True,"dynamic interference gate",e)
check(d.get("launch_spec_hash") and HEX.fullmatch(d["launch_spec_hash"]),"launch spec hash",e)
out={"schema_version":"mysql_job_node01_prelaunch_gate_result_v1","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"status":"PASS_LAUNCH_MAY_BE_SEPARATELY_AUTHORIZED" if not e else "BLOCKED_FAIL_CLOSED","errors":e,"formal_launch_authorized":not e,"accepted_rounds_credit":0}
print(json.dumps(out,sort_keys=True));raise SystemExit(0 if not e else 2)
'''.encode()


ROOT_PROBE = r'''#!/bin/sh
# Read-only commands only.  Supply sudo credentials interactively on node01.
set -eu
sudo /sbin/wipefs -n /dev/nvme2n1
sudo /sbin/blkid -p /dev/nvme2n1 || test "$?" -eq 2
sudo /usr/sbin/fuser -v /dev/nvme2n1 || test "$?" -eq 1
if test -x /usr/sbin/lsof; then
  sudo /usr/sbin/lsof /dev/nvme2n1 || test "$?" -eq 1
else
  sudo find /proc/[0-9]*/fd -lname '/dev/nvme2n1' -print
fi
'''.encode()


TEST_SOURCE = r'''import hashlib,json,stat,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_identity_and_fail_closed_contract():
 m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")
 raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
 assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()
 assert m["schema_version"]=="mysql_job_node01_deployment_successor_bundle_v15"
 assert m["formal_scope"]=="mysql_only" and m["cross_engine_comparison_prohibited"] is True
 assert m["postgresql_mutation_prohibited"] is True and m["formal_launch_authorized"] is False
 assert m["accepted_rounds_credit"]==0 and m["remote_actions"]==0
 c=json.loads((ROOT/"deployment/mysql_job_node01_contract_v1.json").read_text())
 assert c["storage"]["format_authorized"] is False and c["storage"]["ready"] is False
 assert c["database"]["cpuset_cpus"]=="18-20,54-56" and c["database"]["mysql_port"]==65102
 assert c["replay_port"]==65202 and c["database"]["io_read_bps"]==7340032
def test_exact_readonly_closure():
 m=json.loads((ROOT/"bundle_manifest.json").read_text())
 actual={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.rglob("*") if p.is_file() and p.relative_to(ROOT).as_posix() not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
 assert actual==m["files"] and not list(ROOT.rglob("*.pyc"))
 for p in (ROOT,*ROOT.rglob("*")):assert not p.is_symlink() and stat.S_IMODE(p.stat().st_mode)&0o222==0
def test_gate_rejects_unformatted_snapshot(tmp_path):
 c=json.loads((ROOT/"deployment/mysql_job_node01_contract_v1.json").read_text())
 s=c["snapshot_template"];q=tmp_path/"s.json";q.write_text(json.dumps(s))
 p=subprocess.run([sys.executable,str(ROOT/"gate/mysql_job_node01_prelaunch_gate_v1.py"),str(q)],capture_output=True,text=True)
 assert p.returncode==2 and json.loads(p.stdout)["status"]=="BLOCKED_FAIL_CLOSED"
def test_publisher_defaults_to_dry_run(tmp_path):
 p=subprocess.run([sys.executable,str(ROOT/"transaction/publish_content_addressed_bundle_v1.py"),"--source",str(ROOT),"--destination-root",str(tmp_path)],capture_output=True,text=True,check=True)
 assert json.loads(p.stdout)["status"]=="DRY_RUN_NOT_PUBLISHED" and not list(tmp_path.iterdir())
'''.encode()


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink in successor")
        path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
    root.chmod(stat.S_IMODE(root.stat().st_mode) & ~0o222)


def main():
    parent = verify_parent()
    payload = {rel: (PARENT / rel).read_bytes() for rel in parent["files"]
               if rel != "closure.json"}
    modes = {rel: stat.S_IMODE((PARENT / rel).stat().st_mode) for rel in payload}
    active = (
        "authority/build_mysql_job_authorization_sidecar_successor_v6.py",
        "authority/build_mysql_job_graphdriver_readback_successor_v9.py",
        "authority/mysql_job_dedicated_daemon_contract_v1.py",
        "controller/render_mysql_job_v32_bootstrap_spec_tmpfs_v2.py",
        "controller/render_mysql_job_v32_qualification_solo_tmpfs_v2.py",
        "qualification/mysql_job_dedicated_daemon_contract_v1.py",
        "qualification/mysql_job_dedicated_daemon_materializer_v5.py",
        "qualification/mysql_job_distributed_live_readback_qualification_v2.py",
        "qualification/mysql_job_distributed_v2_adopted_services.py",
        "qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py",
        "qualification/mysql_job_distributed_v2_rolling_monitor.py",
        "qualification/mysql_sql_debug_metric_adapter_v1.py",
        "qualification/shell/manual_mysql_sgd_r0_campaign.py",
    )
    for rel in active:
        payload[rel] = replace_active_text(payload[rel])
    fixture = "qualification/fixtures/mysql_job_dedicated_daemon_readback_fixture_v1.json"
    payload[fixture] = replace_active_text(payload[fixture])
    four_scene = "authority/mysql_algorithm_c_distributed_four_scene_successor_contract_v2_20260729.json"
    old_contract_sha = sha(payload[four_scene])
    payload[four_scene] = patch_four_scene_contract(payload[four_scene])
    new_contract_sha = sha(payload[four_scene])
    for rel in ("controller/render_mysql_job_v32_bootstrap_spec_tmpfs_v2.py",
                "qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py"):
        payload[rel] = payload[rel].replace(old_contract_sha.encode(), new_contract_sha.encode())
    payload["distributed_runtime_contract.json"] = patch_runtime(
        payload["distributed_runtime_contract.json"])
    config = json.loads(payload["config.json"])
    config.update({"formal_scope":"mysql_only",
                   "cross_engine_comparison_prohibited":True,
                   "postgresql_mutation_prohibited":True,
                   "bootstrap_authorized":False,"qualification_authorized":False,
                   "fresh_r0_authorized":False,"formal10_authorized":False,
                   "accepted_round_authorized":False})
    payload["config.json"] = canonical(config)

    contract = {
        "schema_version":"mysql_job_node01_deployment_contract_v1",
        "status":"SEALED_OFFLINE_STORAGE_PROBE_AND_FORMAT_AUTHORITY_REQUIRED",
        "formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,
        "postgresql_mutation_prohibited":True,"remote_actions":0,
        "host":{"hostname":HOST,"address":ADDRESS,"ssh_target":SSH_TARGET},
        "parent_algorithm":{"bundle_id":PARENT_ID,"manifest_sha256":PARENT_SHA},
        "storage":{"device":DEVICE,"major_minor":MAJOR_MINOR,"serial":SERIAL,
                   "wwn":WWN,"mount":MOUNT,"filesystem":"xfs","project":PROJECT,
                   "runtime_root":RUNTIME,"daemon_root":DAEMON_ROOT,
                   "root_readonly_probe_required":True,"format_authorized":False,
                   "ready":False},
        "database":{"cpuset_cpus":DB_CPUS,"cpuset_mems":MEMS,
                    "cpu_quota_cores":1.15,"memory_bytes":4294967296,
                    "memory_swap_bytes":4294967296,"pids_max":512,
                    "io_read_bps":7340032,"io_write_bps":7340032,
                    "innodb_buffer_pool_bytes":3221225472,"mysql_port":PLANNER_PORT},
        "client":{"cpuset_cpus":CLIENT_CPUS,"cpuset_mems":MEMS,"cpu_quota_cores":6},
        "dedicated_dockerd":{"cpuset_cpus":DAEMON_CPUS,"cpuset_mems":"0",
                             "socket":"/run/ra-docker-mysql-job-v1.sock",
                             "data_root":DAEMON_ROOT},
        "replay_port":REPLAY_PORT,
        "known_read_only_inventory":{"postgresql_container":"pg5_tpch_ap_slot_node01_20260726",
          "postgresql_cpuset":"0-15,36-51","postgresql_port":63301,
          "mysql_job_cpu_overlap":False,"ports_65102_65202_free":True},
        "required_gates":["root read-only signature/openers scan","separate explicit format authority",
          "post-format xfs mount major:minor readback","transactional content-addressed publication",
          "complete real-PID process inventory","CPU/NUMA/port static isolation",
          "versioned 180-second rolling dynamic non-interference gate"],
        "formal_launch_authorized":False,"accepted_round_authorized":False,
        "accepted_rounds_credit":0,
    }
    snapshot = {
        "schema_version":"mysql_job_node01_prelaunch_snapshot_v1",
        "formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,
        "postgresql_mutation_prohibited":True,
        "host":{"hostname":HOST,"address":ADDRESS},
        "bundle":{"bundle_id":"__BUNDLE_ID__","manifest_sha256":"__MANIFEST_SHA__",
                  "published_verified":False},
        "storage":{"device":DEVICE,"major_minor":MAJOR_MINOR,"serial":SERIAL,"wwn":WWN,
          "root_readonly_probe":{"wipefs_signatures":None,"blkid_signatures":None,
                                 "fuser_pids":None,"openers":None},
          "format_authority":{"authorized":False,"authority_id":None},
          "post_format_mount_readback":{"target":MOUNT,"source_major_minor":None,
                                         "fstype":None,"rw":None}},
        "resource_spec":{**contract["database"],"replay_port":REPLAY_PORT},
        "topology":{"cpu_to_node":{}},"free_ports":[],
        "process_inventory":{"complete":False,"real_pids_only":False,"records":[],"postgresql":[]},
        "dynamic_interference_gate":{"version":"mysql_only_rolling_noninterference_v1",
                                     "duration_seconds":0,"sample_count":0,"passed":False},
        "launch_spec_hash":None,
    }
    contract["snapshot_template"] = snapshot
    payload["deployment/mysql_job_node01_contract_v1.json"] = canonical(contract)
    payload["transaction/publish_content_addressed_bundle_v1.py"] = PUBLISHER
    payload["probe/node01_root_readonly_storage_probe.sh"] = ROOT_PROBE
    payload["tests/test_mysql_job_node01_deployment_v15.py"] = TEST_SOURCE
    inherited_test = "tests/test_mysql_job_algorithm_repair_successor_v14.py"
    payload[inherited_test] = payload[inherited_test].replace(
        b'SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v14"',
        b'SCHEMA = "mysql_job_node01_deployment_successor_bundle_v15"')
    modes.update({"transaction/publish_content_addressed_bundle_v1.py":0o555,
                  "probe/node01_root_readonly_storage_probe.sh":0o555,
                  "tests/test_mysql_job_node01_deployment_v15.py":0o444,
                  "deployment/mysql_job_node01_contract_v1.json":0o444})
    payload["builder/build_mysql_job_node01_deployment_successor_v15.py"] = Path(__file__).read_bytes()
    modes["builder/build_mysql_job_node01_deployment_successor_v15.py"] = 0o555

    # The gate derives the expected content identity from its immutable parent
    # directory, avoiding a circular embedded bundle hash.
    payload["gate/mysql_job_node01_prelaunch_gate_v1.py"] = GATE
    modes["gate/mysql_job_node01_prelaunch_gate_v1.py"] = 0o555
    closure = {
        "schema_version":"mysql_job_node01_module_closure_v15",
        "parent_algorithm_bundle_id":PARENT_ID,
        "parent_algorithm_manifest_sha256":PARENT_SHA,
        "files":{rel:sha(data) for rel,data in sorted(payload.items())},
    }
    payload["closure.json"] = canonical(closure); modes["closure.json"] = 0o444
    identity = dict(parent)
    identity.pop("bundle_id", None)
    identity.update({
        "schema_version":SCHEMA,
        "status":"SEALED_JOB_NODE01_DEPLOYMENT_NOT_LAUNCH_AUTHORITY",
        "predecessor_bundle_id":PARENT_ID,
        "predecessor_bundle_manifest_sha256":PARENT_SHA,
        "formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,
        "postgresql_mutation_prohibited":True,"storage_format_authorized":False,
        "formal_launch_authorized":False,"formal10_authorized":False,
        "accepted_round_authorized":False,"accepted_rounds_credit":0,
        "qualification_credit":0,"r0_credit":0,"candidate_credit":0,
        "top5_replay_credit":0,"remote_actions":0,
        "active_runtime_contract":"distributed_runtime_contract.json",
        "deployment_contract":"deployment/mysql_job_node01_contract_v1.json",
        "prelaunch_gate":"gate/mysql_job_node01_prelaunch_gate_v1.py",
        "files":{rel:sha(data) for rel,data in sorted(payload.items())},
    })
    bundle_id = sha(canonical(identity)); manifest = dict(identity,bundle_id=bundle_id)
    OUT.mkdir(parents=True,exist_ok=True);destination=OUT/bundle_id
    if destination.exists():
        if json.loads((destination/"bundle_manifest.json").read_text())!=manifest:
            raise BuildError("existing successor differs")
        print(json.dumps({"status":manifest["status"],"bundle":str(destination),
                          "bundle_id":bundle_id,"manifest_sha256":digest(destination/"bundle_manifest.json")},sort_keys=True));return
    stage=OUT/(".stage-"+bundle_id+"-"+secrets.token_hex(6));stage.mkdir(mode=0o700)
    try:
        for rel,data in payload.items():
            p=stage/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);p.chmod(modes.get(rel,0o444)&~0o222)
        raw=canonical(manifest);(stage/"bundle_manifest.json").write_bytes(raw)
        (stage/"bundle_manifest.json.sha256").write_text(sha(raw)+"  bundle_manifest.json\n")
        freeze(stage);os.rename(stage,destination)
    except BaseException:
        if stage.exists():shutil.rmtree(stage)
        raise
    print(json.dumps({"status":manifest["status"],"bundle":str(destination),
                      "bundle_id":bundle_id,"manifest_sha256":digest(destination/"bundle_manifest.json")},sort_keys=True))


if __name__ == "__main__":
    try: main()
    except (BuildError,OSError,ValueError,KeyError,TypeError) as exc:
        raise SystemExit("NOT_READY: "+str(exc))

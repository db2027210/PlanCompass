#!/usr/bin/env python3
"""Build a sealed, offline JOB node04/nvme1 launch-handoff successor."""
from __future__ import annotations

import hashlib,json,os,secrets,shutil,stat
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PARENT_ID="3f8fa66aafab1cd4d7032c54aff8473426dfff9f9d763cb4062eef3ab5fcabed"
PARENT_SHA="d018f5378063fbb50d5e50fae4cc9e95085c72cd1bbb6b01942230422d526767"
PARENT=ROOT/"artifacts/mysql_job_node04_nvme1_deployment_successor_v16"/PARENT_ID
OUT=ROOT/"artifacts/mysql_job_node04_nvme1_launch_handoff_successor_v17"
SCHEMA="mysql_job_node04_nvme1_launch_handoff_successor_bundle_v17"
BUILDER="builder/build_mysql_job_node04_nvme1_launch_handoff_successor_v17.py"
TEST="tests/test_mysql_job_node04_nvme1_launch_handoff_v17.py"
RUNTIME="/mnt/mysql_job_nvme1n1/anonymous/resource_adaptive_mysql/job/runtime_algorithm_c_successor_20260804"
INVENTORY_SOURCE=ROOT/"artifacts/mysql_job_node04_disjoint_successor_v18/44c6bda398c8eee5ba5fdf4bef95947cb497b27a3a161448fc084e344baa40ea/qualification/mysql_job_v17_portable_tree_inventory_v1.py"
INVENTORY_SHA="d2e5eaffb808e5ab302c0776e23ff4fe51c268b3670c49786a1614b3f47dbb9a"

class BuildError(RuntimeError):pass
def canonical(v):return (json.dumps(v,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
def sb(v):return hashlib.sha256(v).hexdigest()
def sf(p):return sb(Path(p).read_bytes())

def verify_parent():
 m=json.loads((PARENT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id",None)
 if m.get("bundle_id")!=PARENT_ID or sf(PARENT/"bundle_manifest.json")!=PARENT_SHA or sb(canonical(u))!=PARENT_ID:raise BuildError("parent identity differs")
 for rel,want in m["files"].items():
  p=PARENT/rel
  if p.is_symlink() or not p.is_file() or sf(p)!=want:raise BuildError("parent closure differs: "+rel)
 return m

ACTIVE_RUNTIME=(
 "controller/render_mysql_job_v32_bootstrap_spec_tmpfs_v2.py",
 "controller/render_mysql_job_v32_qualification_solo_tmpfs_v2.py",
 "qualification/mysql_job_dedicated_daemon_materializer_v5.py",
 "qualification/mysql_job_distributed_live_readback_qualification_v2.py",
 "qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py",
 "qualification/mysql_job_distributed_v2_adopted_services.py",
 "qualification/mysql_job_distributed_v2_rolling_monitor.py",
 "qualification/mysql_job_dedicated_daemon_contract_v1.py",
 "authority/mysql_job_dedicated_daemon_contract_v1.py",
 "qualification/fixtures/mysql_job_dedicated_daemon_readback_fixture_v1.json",
)

RENAME_TOOL=r'''#!/usr/bin/env python3
"""Publish one exact JOB stage with renameat2(RENAME_NOREPLACE)."""
from __future__ import annotations
import argparse,ctypes,json,os
from pathlib import Path
ALLOWED={
 "template":("/mnt/mysql_job_nvme1n1/anonymous/resource_adaptive_mysql/job/.template.stage-job-handoff-v17-20260804","/mnt/mysql_job_nvme1n1/anonymous/resource_adaptive_mysql/job/template"),
 "runtime":("/mnt/mysql_job_nvme1n1/anonymous/resource_adaptive_mysql/job/runtime_algorithm_c_successor_20260804/runtime/.python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89.stage-job-handoff-v17-20260804","/mnt/mysql_job_nvme1n1/anonymous/resource_adaptive_mysql/job/runtime_algorithm_c_successor_20260804/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89")}
p=argparse.ArgumentParser();p.add_argument("--kind",choices=sorted(ALLOWED),required=True);p.add_argument("--source",required=True);p.add_argument("--destination",required=True);a=p.parse_args()
if (a.source,a.destination)!=ALLOWED[a.kind]:raise SystemExit("NOT_PUBLISHED: exact source/destination differs")
s=Path(a.source);d=Path(a.destination)
if s.is_symlink() or not s.is_dir() or d.exists() or d.is_symlink():raise SystemExit("NOT_PUBLISHED: source/destination state differs")
libc=ctypes.CDLL(None,use_errno=True);fn=libc.syscall;fn.restype=ctypes.c_long
rc=fn(316,-100,os.fsencode(a.source),-100,os.fsencode(a.destination),1)
if rc!=0:raise OSError(ctypes.get_errno(),os.strerror(ctypes.get_errno()))
print(json.dumps({"status":"PUBLISHED_RENAME_NOREPLACE","kind":a.kind,"destination":a.destination,"formal_launch_authorized":False,"accepted_rounds_credit":0},sort_keys=True))
'''.encode()

def runbook():
 source="/mnt/nvme2n1/anonymous/resource_adaptive_mysql/job/.relocation_v21/job-v21-recovery-20260803t105116z-776fe047"
 return {
  "schema_version":"mysql_job_node04_nvme1_launch_handoff_v1","status":"AUDITED_NOT_EXECUTED_EXPLICIT_PER_DEVICE_AUTHORITY_REQUIRED",
  "formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,
  "host":{"hostname":"node04","address":"192.0.2.7","os":"CentOS Linux 7.9.2009","systemd":"219","docker":"26.1.4","cgroup":"v1","selinux":"Permissive"},
  "only_final_storage":{"device":"/dev/nvme1n1","major_minor":"259:0","serial":"S5LFNA0RA00592","wwn":"eui.354c463052a005920025384100000002","filesystem":"xfs","mount":"/mnt/mysql_job_nvme1n1","xfs_ftype":1},
  "authority":{"required_before_destructive_step":True,"scope":"one host+device+serial+wwn+major_minor+filesystem+mount","reuse_prohibited":True,"format_authorized":False},
  "ordered_steps":[
   {"step":1,"name":"fresh_pre_format_readonly_identity","authority":"read_only","argv":[["/usr/bin/lsblk","--nodeps","--output","NAME,MAJ:MIN,SERIAL,WWN,FSTYPE,MOUNTPOINT,RO","/dev/nvme1n1"],["/usr/bin/sudo","-n","/sbin/wipefs","-n","/dev/nvme1n1"],["/usr/bin/sudo","-n","/sbin/blkid","-p","/dev/nvme1n1"],["/usr/bin/sudo","-n","/usr/sbin/fuser","-v","/dev/nvme1n1"],["/usr/bin/sudo","-n","/usr/sbin/lsof","/dev/nvme1n1"],["/usr/bin/findmnt","-rn","-S","/dev/nvme1n1"],["/usr/bin/grep","-F","/dev/nvme1n1","/proc/swaps"]],"output":"pre_format_identity.json","pass":"exact identity; no signatures, mount, swap, holders, openers"},
   {"step":2,"name":"consume_single_device_format_authority","authority":"main_agent explicit per-device document","argv":[],"output":"format_authority_consumption.json","pass":"unexpired, unused, exact identity; record authority SHA256"},
   {"step":3,"name":"format_xfs_ftype1","authority":"consumes step2","argv":[["/usr/bin/sudo","-n","/sbin/mkfs.xfs","-f","-n","ftype=1","/dev/nvme1n1"]],"output":"mkfs.stdout+stderr+rc","pass":"rc=0; never retry under same authority"},
   {"step":4,"name":"mount_exact_target","authority":"same consumed transaction","argv":[["/usr/bin/sudo","-n","/usr/bin/mkdir","-p","/mnt/mysql_job_nvme1n1"],["/usr/bin/sudo","-n","/bin/mount","-t","xfs","-o","rw,nosuid,nodev","/dev/nvme1n1","/mnt/mysql_job_nvme1n1"]],"output":"mount_action.json","pass":"no alternate source,target,fstype"},
   {"step":5,"name":"post_format_readback","authority":"read_only","argv":[["/usr/bin/findmnt","-rn","-o","SOURCE,TARGET,FSTYPE,OPTIONS","-T","/mnt/mysql_job_nvme1n1"],["/usr/bin/lsblk","--nodeps","--output","NAME,MAJ:MIN,SERIAL,WWN,FSTYPE,MOUNTPOINT,RO","/dev/nvme1n1"],["/usr/bin/sudo","-n","/usr/bin/env","PATH=/usr/sbin:/usr/bin:/sbin:/bin","/sbin/xfs_info","/mnt/mysql_job_nvme1n1"]],"output":"post_format_mount_readback.json","pass":"source=/dev/nvme1n1,259:0,xfs,rw,ftype=1"},
   {"step":6,"name":"create_exact_parents","authority":"post-format transaction","argv":[["/usr/bin/sudo","-n","/usr/bin/install","-d","-o","1008","-g","1008","-m","0755","/mnt/mysql_job_nvme1n1/anonymous/resource_adaptive_mysql/job"],["/usr/bin/sudo","-n","/usr/bin/install","-d","-o","1008","-g","1008","-m","0775",RUNTIME],["/usr/bin/sudo","-n","/usr/bin/install","-d","-o","1008","-g","1008","-m","0775",RUNTIME+"/runtime"],["/usr/bin/sudo","-n","/usr/bin/install","-d","-o","1008","-g","1008","-m","0755",RUNTIME+"/bundles"]],"output":"parent_stat_readback.json","pass":"exact owner/mode and all resolved devices 259:0"},
   {"step":7,"name":"publish_control_bundle","authority":"transactional publish only","argv":[["/usr/bin/python3","{LOCAL_OR_STAGED_BUNDLE}/transaction/publish_content_addressed_bundle_v1.py","--source","{LOCAL_OR_STAGED_BUNDLE}","--destination-root",RUNTIME+"/bundles","--apply"]],"output":"publish_result.json","pass":"PUBLISHED_ATOMIC_NEW_CONTENT_ADDRESS or ALREADY_PRESENT_IDENTICAL; verify every manifest member"},
   {"step":8,"name":"materialize_template_and_python311","authority":"exact sealed archive read + new nvme1 stages only","source":source,"source_identities":{"template":{"segments_sha256":["382fb541707a12df87c37e0c9a7a92bd15afc1ba4bf41b6301ff46608d83b5dd","49f3b31c8373a862fb2c68cc4893c8f095f2d5a87082ecaca84796353ce7c7bf","fd1887e3fcbd12ae7c288f54d7b9f67faaa008e5e61ed0dda88825542b02cf23","ab14ded0117af4eeed6cd93640bcb8d80339b66546c5e405330530eaeb51756b"],"tree_identity":"0cfee28185ae9e0444026f90746f6147e668594ed0a80b172a47474405ee9af8","entries":193},"runtime":{"segments_sha256":["bb982b141da8c089bed8e4682b3808975965c40a9f9ee814ad1be8df19f0cfdc"],"tree_identity":"2345fe3a7345746b796793e770a411b6b0b8bb368151bb89797ea8a167436c12","entries":40852}},"argv_contract":"verify fixed segment SHA256; root-extract tar with --xattrs --xattrs-include=* --numeric-owner into the two exact absent stage paths; generate portable inventories with sealed Python3.11; require exact source-post identity; invoke transaction/rename_noreplace_exact_v1.py once per kind","output":"template_runtime_materialization_readback.json","pass":"template and python runtime final paths resolve only to 259:0; Python binary SHA256 05bd...aff39"},
   {"step":9,"name":"install_start_dedicated_dockerd","authority":"separate infrastructure start authority","argv_contract":"install exact unit_text from qualification/mysql_job_dedicated_daemon_contract_v1.py as /etc/systemd/system/ra-docker-mysql-job-v1.service; systemctl daemon-reload; systemctl start; never modify docker.service","output":"dedicated_daemon_readback_v3.json","pass":"direct systemd child, socket /run/ra-docker-mysql-job-v1.sock, root on259:0, overlay2, CPU30,66, no PG ancestry/device"},
   {"step":10,"name":"fresh_prelaunch_and_dynamic_gate","authority":"read_only inventory/monitor","argv":[[RUNTIME+"/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89/bin/python3.11","{BUNDLE}/gate/mysql_job_node04_nvme1_prelaunch_gate_v1.py","{FRESH_SNAPSHOT_JSON}"]],"output":"mysql_job_node04_nvme1_prelaunch_gate_result_v1","pass":"all static gates plus 180 seconds/181 samples; fresh real PIDs; PG inventory or verified absence"},
   {"step":11,"name":"issue_short_lived_bootstrap_materials","authority":"main-agent token+exact image IDs","argv":[[RUNTIME+"/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89/bin/python3.11","{BUNDLE}/qualification/mysql_job_dedicated_daemon_materializer_v5.py","--bundle-manifest","{BUNDLE}/bundle_manifest.json","--mysql-image-reference","{MYSQL_IMAGE_REFERENCE}","--mysql-image-id","{MYSQL_IMAGE_SHA256_ID}","--client-image-reference","{CLIENT_IMAGE_REFERENCE}","--client-image-id","{CLIENT_IMAGE_SHA256_ID}","--main-agent-token-file","{MODE0600_TOKEN}","--output-root",RUNTIME+"/materials","--valid-minutes","45"]],"output":"ISSUED_NOT_EXECUTED materials directory","pass":"fresh attempt, authority outside attempt, no formal/R0 credit"},
   {"step":12,"name":"qualification_start_line","authority":"fresh unexpired qualification-only materials","argv_contract":"install authorization plan with exclusive create; execute command_plan bootstrap; then run collector --qualification-solo with exact command spec, authority and 180-second duration","output":"mysql_algorithm_c_debug_qualification_evidence_v2","pass":"qualification may start only after steps1-11 pass; it remains capture/R0/formal/accepted credit zero"}
  ],
  "qualification_start_condition":"all steps 1..11 PASS and fresh; no missing PID/device/port/topology/closure/authority evidence",
  "formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,
 }

TEST_SOURCE=r'''import hashlib,json,stat,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
ACTIVE=("controller/render_mysql_job_v32_bootstrap_spec_tmpfs_v2.py","controller/render_mysql_job_v32_qualification_solo_tmpfs_v2.py","qualification/mysql_job_dedicated_daemon_materializer_v5.py","qualification/mysql_job_distributed_live_readback_qualification_v2.py","qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py","qualification/mysql_job_distributed_v2_adopted_services.py","qualification/mysql_job_distributed_v2_rolling_monitor.py","qualification/mysql_job_dedicated_daemon_contract_v1.py","authority/mysql_job_dedicated_daemon_contract_v1.py","qualification/fixtures/mysql_job_dedicated_daemon_readback_fixture_v1.json")
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def test_identity_closure_and_frozen_literals():
 m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id");raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode();assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest();assert m["schema_version"]=="mysql_job_node04_nvme1_launch_handoff_successor_bundle_v17"
 for r in ACTIVE:
  t=(ROOT/r).read_text();assert "runtime_algorithm_c_successor_20260729" not in t and '259:3' not in t
 assert 'CLIENT_MEMS = "1"' in (ROOT/"qualification/mysql_job_distributed_v2_adopted_services.py").read_text();assert '"cpuset_mems": "1"' in (ROOT/"qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py").read_text()
 unit=(ROOT/"qualification/mysql_job_dedicated_daemon_contract_v1.py").read_text();assert "/usr/bin/taskset -c %s /usr/bin/dockerd" in unit and "--exec-root=/run/ra-docker-mysql-job-v1" in unit and "--pidfile=/run/ra-docker-mysql-job-v1.pid" in unit and '"CPUAffinity=%s"' not in unit
 assert 'mysql_job_node04_nvme1_launch_handoff_successor_bundle_v17' in (ROOT/"qualification/mysql_job_distributed_live_readback_qualification_v2.py").read_text()
 files=m["files"];actual={p.relative_to(ROOT).as_posix():sha(p) for p in ROOT.rglob("*") if p.is_file() and p.relative_to(ROOT).as_posix() not in ("bundle_manifest.json","bundle_manifest.json.sha256")};assert actual==files;assert all(not p.is_symlink() and stat.S_IMODE(p.stat().st_mode)&0o222==0 for p in (ROOT,*ROOT.rglob("*")))
def test_manifest_active_hashes_and_runbook():
 m=json.loads((ROOT/"bundle_manifest.json").read_text());pairs={"collector_sha256":"qualification/mysql_job_distributed_live_readback_qualification_v2.py","adopted_services_sha256":"qualification/mysql_job_distributed_v2_adopted_services.py","rolling_monitor_sha256":"qualification/mysql_job_distributed_v2_rolling_monitor.py","bootstrap_spec_renderer_sha256":"controller/render_mysql_job_v32_bootstrap_spec_tmpfs_v2.py","qualification_spec_renderer_sha256":"controller/render_mysql_job_v32_qualification_solo_tmpfs_v2.py","manual_campaign_sha256":"qualification/shell/manual_mysql_sgd_r0_campaign.py","dedicated_daemon_module_sha256":"qualification/mysql_job_dedicated_daemon_contract_v1.py","bootstrap_handoff_authority_sha256":"qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py","distributed_v2_contract_sha256":"authority/mysql_algorithm_c_distributed_four_scene_successor_contract_v2_20260729.json","sql_metric_adapter_sha256":"qualification/mysql_sql_debug_metric_adapter_v1.py","host_root_mount_inventory_helper_sha256":"qualification/mysql_job_host_root_inventory_v1.py"};assert all(m[k]==sha(ROOT/r) for k,r in pairs.items())
 h=json.loads((ROOT/"handoff/mysql_job_node04_nvme1_launch_handoff_v1.json").read_text());s=json.dumps(h);assert h["qualification_start_condition"].startswith("all steps 1..11 PASS") and h["formal_launch_authorized"] is False and "/dev/nvme1n1" in s and "/mnt/mysql_job_nvme1n1" in s and "ftype=1" in s
 assert sha(ROOT/"qualification/mysql_job_v17_portable_tree_inventory_v1.py")=="d2e5eaffb808e5ab302c0776e23ff4fe51c268b3670c49786a1614b3f47dbb9a"
def test_python_syntax_and_gate_fail_closed(tmp_path):
 for r in ACTIVE:
  if r.endswith(".py"):subprocess.run([sys.executable,"-B","-m","py_compile",str(ROOT/r)],check=True,env={**dict(__import__('os').environ),"PYTHONPYCACHEPREFIX":str(tmp_path/"pyc")})
 d=json.loads((ROOT/"deployment/mysql_job_node04_nvme1_contract_v1.json").read_text());q=tmp_path/"s.json";q.write_text(json.dumps(d["snapshot_template"]));p=subprocess.run([sys.executable,str(ROOT/"gate/mysql_job_node04_nvme1_prelaunch_gate_v1.py"),str(q)],text=True,capture_output=True);assert p.returncode==2 and json.loads(p.stdout)["status"]=="BLOCKED_FAIL_CLOSED"
def test_offline_materializer_accepts_v17_closure(tmp_path):
 token=tmp_path/"token";token.write_text("offline-test-token\n");token.chmod(0o600);out=tmp_path/"materials";fake="sha256:"+("a"*64);p=subprocess.run([sys.executable,"-B",str(ROOT/"qualification/mysql_job_dedicated_daemon_materializer_v5.py"),"--bundle-manifest",str(ROOT/"bundle_manifest.json"),"--mysql-image-reference","mysql:8.0","--mysql-image-id",fake,"--client-image-reference","client:sealed","--client-image-id",fake,"--main-agent-token-file",str(token),"--output-root",str(out),"--valid-minutes","45"],text=True,capture_output=True);assert p.returncode==0,p.stderr;v=json.loads(p.stdout);assert v["status"]=="ISSUED_NOT_EXECUTED" and v["formal_launch_authorized"] is False and Path(v["materials"]).is_dir()
def test_transactional_publisher_is_idempotent_and_world_readable(tmp_path):
 dest=tmp_path/"published";argv=[sys.executable,"-B",str(ROOT/"transaction/publish_content_addressed_bundle_v1.py"),"--source",str(ROOT),"--destination-root",str(dest),"--apply"];a=subprocess.run(argv,text=True,capture_output=True);assert a.returncode==0,a.stderr;v=json.loads(a.stdout);published=Path(v["bundle"]);assert v["status"]=="PUBLISHED_ATOMIC_NEW_CONTENT_ADDRESS" and stat.S_IMODE(published.stat().st_mode)==0o555 and (published/"bundle_manifest.json").stat().st_mode&0o004
 b=subprocess.run(argv,text=True,capture_output=True);assert b.returncode==0 and json.loads(b.stdout)["status"]=="ALREADY_PRESENT_IDENTICAL"
'''.encode()

def freeze(root):
 for p in sorted(root.rglob("*"),reverse=True):
  if p.is_symlink():raise BuildError("symlink")
  p.chmod(stat.S_IMODE(p.stat().st_mode)&~0o222)
 root.chmod(stat.S_IMODE(root.stat().st_mode)&~0o222)

def main():
 parent=verify_parent();skip={"closure.json","tests/test_mysql_job_node04_nvme1_deployment_v16.py","builder/build_mysql_job_node04_nvme1_deployment_successor_v16.py"}
 payload={r:(PARENT/r).read_bytes() for r in parent["files"] if r not in skip};modes={r:stat.S_IMODE((PARENT/r).stat().st_mode) for r in payload}
 publisher="transaction/publish_content_addressed_bundle_v1.py";pt=payload[publisher].decode();needle=' if (a.source/"bundle_manifest.json.sha256").is_file():shutil.copyfile(a.source/"bundle_manifest.json.sha256",stage/"bundle_manifest.json.sha256")\n for rel,want in m["files"].items():\n  if digest(stage/rel)!=want:fail("staged verification failed: "+rel)\n os.rename(stage,dst)';replacement=' if (a.source/"bundle_manifest.json.sha256").is_file():shutil.copyfile(a.source/"bundle_manifest.json.sha256",stage/"bundle_manifest.json.sha256")\n (stage/"bundle_manifest.json").chmod(0o444)\n if (stage/"bundle_manifest.json.sha256").is_file():(stage/"bundle_manifest.json.sha256").chmod(0o444)\n for rel,want in m["files"].items():\n  if digest(stage/rel)!=want:fail("staged verification failed: "+rel)\n for directory in sorted((x for x in stage.rglob("*") if x.is_dir()),reverse=True):directory.chmod(0o555)\n stage.chmod(0o555)\n os.rename(stage,dst)'
 if needle not in pt:raise BuildError("publisher patch point absent")
 payload[publisher]=pt.replace(needle,replacement).encode()
 algtest="tests/test_mysql_job_algorithm_repair_successor_v14.py";payload[algtest]=payload[algtest].replace(b'SCHEMA = "mysql_job_node04_nvme1_deployment_successor_bundle_v16"',f'SCHEMA = "{SCHEMA}"'.encode())
 for r in ACTIVE_RUNTIME:
  text=payload[r].decode().replace("runtime_algorithm_c_successor_20260729","runtime_algorithm_c_successor_20260804").replace('259:3','259:0')
  if r=="qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py":text=text.replace('"cpuset_mems": "0"','"cpuset_mems": "1"',1)
  if r=="qualification/mysql_job_distributed_v2_adopted_services.py":text=text.replace('CLIENT_MEMS = "0"','CLIENT_MEMS = "1"')
  if r in ("qualification/mysql_job_dedicated_daemon_contract_v1.py","authority/mysql_job_dedicated_daemon_contract_v1.py"):
   old='def unit_text():return "\\n".join(("[Unit]","Description=Dedicated Docker daemon for MySQL JOB","[Service]","ExecStart=/usr/bin/dockerd --host=unix:%s --data-root=%s --iptables=false --bridge=none"%(SOCKET,ROOT),"CPUAffinity=%s"%CPUS,"Delegate=yes","[Install]","WantedBy=multi-user.target",""))'
   new='def unit_text():return "\\n".join(("[Unit]","Description=Dedicated Docker daemon for MySQL JOB","[Service]","ExecStart=/usr/bin/taskset -c %s /usr/bin/dockerd --host=unix:%s --data-root=%s --exec-root=/run/ra-docker-mysql-job-v1 --pidfile=/run/ra-docker-mysql-job-v1.pid --storage-driver=overlay2 --bridge=none --iptables=false --ip-masq=false --ip-forward=false --userland-proxy=false --log-driver=none"%(CPUS,SOCKET,ROOT),"Delegate=yes","TasksMax=512","[Install]","WantedBy=multi-user.target",""))'
   if old not in text:raise BuildError("old daemon unit literal absent: "+r)
   text=text.replace(old,new)
  if r=="qualification/mysql_job_dedicated_daemon_materializer_v5.py":text=text.replace('SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v13"',f'SCHEMA = "{SCHEMA}"')
  if r=="qualification/mysql_job_distributed_live_readback_qualification_v2.py":text=text.replace('"mysql_job_raw_all_subset_three_level_successor_bundle_v13",','"mysql_job_raw_all_subset_three_level_successor_bundle_v13",\n        "'+SCHEMA+'",')
  payload[r]=text.encode()
 contract=json.loads(payload["deployment/mysql_job_node04_nvme1_contract_v1.json"]);contract["schema_version"]="mysql_job_node04_nvme1_launch_handoff_contract_v2";contract["status"]="SEALED_OFFLINE_HANDOFF_AUDITED_EXPLICIT_FORMAT_AUTHORITY_REQUIRED";contract["launch_handoff"]="handoff/mysql_job_node04_nvme1_launch_handoff_v1.json";payload["deployment/mysql_job_node04_nvme1_contract_v1.json"]=canonical(contract)
 payload["handoff/mysql_job_node04_nvme1_launch_handoff_v1.json"]=canonical(runbook());modes["handoff/mysql_job_node04_nvme1_launch_handoff_v1.json"]=0o444
 if not INVENTORY_SOURCE.is_file() or sf(INVENTORY_SOURCE)!=INVENTORY_SHA:raise BuildError("portable inventory source identity differs")
 payload["qualification/mysql_job_v17_portable_tree_inventory_v1.py"]=INVENTORY_SOURCE.read_bytes();modes["qualification/mysql_job_v17_portable_tree_inventory_v1.py"]=0o555
 payload["transaction/rename_noreplace_exact_v1.py"]=RENAME_TOOL;modes["transaction/rename_noreplace_exact_v1.py"]=0o555
 payload[TEST]=TEST_SOURCE;modes[TEST]=0o444;payload[BUILDER]=Path(__file__).read_bytes();modes[BUILDER]=0o555
 closure={"schema_version":"mysql_job_node04_nvme1_launch_handoff_module_closure_v17","predecessor_bundle_id":PARENT_ID,"predecessor_manifest_sha256":PARENT_SHA,"files":{r:sb(v) for r,v in sorted(payload.items())}};payload["closure.json"]=canonical(closure);modes["closure.json"]=0o444
 ident=dict(parent);ident.pop("bundle_id",None);ident.update(schema_version=SCHEMA,status="SEALED_JOB_NODE04_NVME1_HANDOFF_AUDITED_NOT_LAUNCH_AUTHORITY",predecessor_bundle_id=PARENT_ID,predecessor_bundle_manifest_sha256=PARENT_SHA,storage_format_authorized=False,formal_launch_authorized=False,formal10_authorized=False,accepted_round_authorized=False,accepted_rounds_credit=0,qualification_credit=0,r0_credit=0,candidate_credit=0,top5_replay_credit=0,remote_actions=0,launch_handoff="handoff/mysql_job_node04_nvme1_launch_handoff_v1.json",builder_sha256=sb(payload[BUILDER]),collector_sha256=sb(payload["qualification/mysql_job_distributed_live_readback_qualification_v2.py"]),adopted_services_sha256=sb(payload["qualification/mysql_job_distributed_v2_adopted_services.py"]),rolling_monitor_sha256=sb(payload["qualification/mysql_job_distributed_v2_rolling_monitor.py"]),bootstrap_spec_renderer_sha256=sb(payload["controller/render_mysql_job_v32_bootstrap_spec_tmpfs_v2.py"]),qualification_spec_renderer_sha256=sb(payload["controller/render_mysql_job_v32_qualification_solo_tmpfs_v2.py"]),manual_campaign_sha256=sb(payload["qualification/shell/manual_mysql_sgd_r0_campaign.py"]),dedicated_daemon_module_sha256=sb(payload["qualification/mysql_job_dedicated_daemon_contract_v1.py"]),bootstrap_handoff_authority_sha256=sb(payload["qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py"]),distributed_v2_contract_sha256=sb(payload["authority/mysql_algorithm_c_distributed_four_scene_successor_contract_v2_20260729.json"]),sql_metric_adapter_sha256=sb(payload["qualification/mysql_sql_debug_metric_adapter_v1.py"]),host_root_mount_inventory_helper_sha256=sb(payload["qualification/mysql_job_host_root_inventory_v1.py"]),files={r:sb(v) for r,v in sorted(payload.items())})
 bid=sb(canonical(ident));manifest=dict(ident,bundle_id=bid);OUT.mkdir(parents=True,exist_ok=True);dst=OUT/bid
 if dst.exists():
  if json.loads((dst/"bundle_manifest.json").read_text())!=manifest:raise BuildError("existing differs")
  print(json.dumps({"bundle":str(dst),"bundle_id":bid,"manifest_sha256":sf(dst/"bundle_manifest.json"),"status":manifest["status"]},sort_keys=True));return
 stage=OUT/(".stage-"+bid+"-"+secrets.token_hex(6));stage.mkdir(mode=0o700)
 try:
  for r,v in payload.items():p=stage/r;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v);p.chmod(modes.get(r,0o444)&~0o222)
  (stage/"bundle_manifest.json").write_bytes(canonical(manifest));(stage/"bundle_manifest.json").chmod(0o444)
  (stage/"bundle_manifest.json.sha256").write_text(sb(canonical(manifest))+"  bundle_manifest.json\n");(stage/"bundle_manifest.json.sha256").chmod(0o444)
  freeze(stage);os.rename(stage,dst)
 except BaseException:
  if stage.exists():shutil.rmtree(stage)
  raise
 print(json.dumps({"bundle":str(dst),"bundle_id":bid,"manifest_sha256":sf(dst/"bundle_manifest.json"),"status":manifest["status"]},sort_keys=True))
if __name__=="__main__":main()

#!/usr/bin/env python3
"""Build the offline JOB/node04/nvme1 mysql-only deployment successor."""
from __future__ import annotations

import hashlib,json,os,secrets,shutil,stat
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PARENT_ID="25732a7f94725dcb36b7f0b02cb2e98234221774dcd2894a724db3bd6aec9821"
PARENT_SHA="5c728d6ab091adb7af7a5e5cf8e74f5f10b86ece2b0dbf5e4dfa2fb87e90b273"
PARENT=ROOT/"artifacts/mysql_job_node01_deployment_successor_v15"/PARENT_ID
OUT=ROOT/"artifacts/mysql_job_node04_nvme1_deployment_successor_v16"
SCHEMA="mysql_job_node04_nvme1_deployment_successor_bundle_v16"
HOST="node04";ADDRESS="192.0.2.7";SSH="anonymous@192.0.2.7"
DEVICE="/dev/nvme1n1";MM="259:0";SERIAL="S5LFNA0RA00592";WWN="eui.354c463052a005920025384100000002"
MOUNT="/mnt/mysql_job_nvme1n1";PROJECT=MOUNT+"/anonymous/resource_adaptive_mysql/job";RUNTIME=PROJECT+"/runtime_algorithm_c_successor_20260804";DAEMON_ROOT=MOUNT+"/anonymous/resource_adaptive_mysql/docker_ra_job_v1"
DB="25-26,61-62";CLIENT="27-29,63-65";DAEMON="30,66";CONTROLLER="31,67";MEMS="1"
BUILDER="builder/build_mysql_job_node04_nvme1_deployment_successor_v16.py"
TEST="tests/test_mysql_job_node04_nvme1_deployment_v16.py"

class BuildError(RuntimeError):pass
def canonical(v):return (json.dumps(v,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
def sb(v):return hashlib.sha256(v).hexdigest()
def sf(p):return sb(Path(p).read_bytes())
def verify_parent():
 m=json.loads((PARENT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id",None)
 if m.get("bundle_id")!=PARENT_ID or sf(PARENT/"bundle_manifest.json")!=PARENT_SHA or sb(canonical(u))!=PARENT_ID or m.get("schema_version")!="mysql_job_node01_deployment_successor_bundle_v15":raise BuildError("parent identity differs")
 for rel,want in m["files"].items():
  p=PARENT/rel
  if p.is_symlink() or not p.is_file() or sf(p)!=want:raise BuildError("parent closure differs: "+rel)
 return m
def patch_text(data):
 return (data.decode().replace("anonymous@192.0.2.3",SSH).replace("192.0.2.3",ADDRESS).replace("node01",HOST)
  .replace("/mnt/nvme2n1",MOUNT).replace("/dev/nvme2n1",DEVICE)
  .replace("18-20,54-56",DB).replace("21-23,57-59",CLIENT).replace("16-17,52-53",DAEMON)
  .replace("20395B803410",SERIAL).replace("eui.e8238fa6bf530001001b448b49c4281c",WWN).encode())
def patch_runtime(data):
 v=json.loads(data);v["host"]={"hostname":HOST,"address":ADDRESS};v["bundle_remote_path_rule"]=RUNTIME+"/bundles/<bundle_id>"
 v["storage"].update(device=DEVICE,expected_live_major_minor=MM,mount=MOUNT,runtime_root=RUNTIME,template_path=PROJECT+"/template",expected_serial=SERIAL,expected_wwn=WWN,required_filesystem="xfs",format_authority_required=True)
 v["resource_envelope"].update(cpu_quota_cores=1.15,cpuset_cpus=DB,cpuset_mems=MEMS,memory_bytes=4294967296,memory_swap_bytes=4294967296,pids_max=512,io_read_bps=7340032,io_write_bps=7340032,innodb_buffer_pool_bytes=3221225472,mysql_port=65102)
 v["client_resource_envelope"].update(cpu_quota_cores=6,cpuset_cpus=CLIENT,cpuset_mems=MEMS)
 v["controller_resource_envelope"]={"cpuset_cpus":CONTROLLER,"cpuset_mems":MEMS,"physical_core_exclusive":True}
 tree=v["toolchain"]["python_tree_content_sha256"];py=RUNTIME+"/runtime/python311-"+tree;v["toolchain"].update(python_runtime_root=py,python_runtime=py+"/bin/python3.11")
 v["target_infrastructure"].update(data_root=DAEMON_ROOT,device=DEVICE,device_major_minor=MM,cpuset_cpus=DAEMON,cpuset_mems=MEMS)
 v["known_parallel_layout"]={"tpch":{"client":"0-7,36-43","controller":"16-17,52-53","database":"18-20,54-56","daemon":"32-33,68-69","device":"/dev/nvme0n1","mysql_port":65103},"tpcc":{"client":"8-15,44-51","database":"21-24,57-60","daemon":"34-35,70-71","device":"/dev/nvme3n1","mysql_port":65106},"job":{"database":DB,"client":CLIENT,"daemon":DAEMON,"controller":CONTROLLER,"device":DEVICE,"mysql_port":65102,"replay_port":65202},"physical_core_sibling_intersection_empty":True}
 v["formal_launch_authorized"]=False;v["accepted_round_authorized"]=False
 return canonical(v)
def patch_four(data):
 v=json.loads(data);j=v["scenarios"]["job"];j["host"]={"hostname":HOST,"address":ADDRESS};j["storage"].update(device=DEVICE,expected_live_major_minor=MM,mount=MOUNT,runtime_root=RUNTIME,template_path=PROJECT+"/template",expected_serial=SERIAL,expected_wwn=WWN,required_filesystem="xfs",format_authority_required=True)
 j["resource_envelope"].update(cpu_quota_cores=1.15,cpuset_cpus=DB,cpuset_mems=MEMS,memory_bytes=4294967296,memory_swap_bytes=4294967296,pids_max=512,io_read_bps=7340032,io_write_bps=7340032,innodb_buffer_pool_bytes=3221225472,mysql_port=65102)
 j["client_resource_envelope"].update(cpuset_cpus=CLIENT,cpuset_mems=MEMS,cpu_quota_cores=6);j["controller_resource_envelope"]={"cpuset_cpus":CONTROLLER,"cpuset_mems":MEMS,"physical_core_exclusive":True};j["target_infrastructure"]={"kind":"dedicated_dockerd","cpuset_cpus":DAEMON,"cpuset_mems":MEMS,"data_root":DAEMON_ROOT,"device":DEVICE,"device_major_minor":MM,"foreign_writer":False,"exact_identity_required":True};j["formal_launch_authorized"]=False
 p=v["same_host_parallel_policy"];p["policy_version"]="mysql_algorithm_c_mysql_only_job_node04_nvme1_noninterference_v1";p["pairs"]=[{"host":"node04","scenarios":["job","tpch"],"serial_fallback_lock":MOUNT+"/anonymous/resource_adaptive_mysql/host_control/job_tpch_serial_fallback.lock"},{"host":"node04","scenarios":["job","tpcc"],"serial_fallback_lock":MOUNT+"/anonymous/resource_adaptive_mysql/host_control/job_tpcc_serial_fallback.lock"},{"host":"node04","scenarios":["tpch","tpcc"],"serial_fallback_lock":"/mnt/nvme0n1/anonymous/resource_adaptive_mysql/host_control/tpch_tpcc_serial_fallback.lock"}]
 p.pop("job_node01_policy",None);p["job_node04_policy"]={"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"physical_core_sibling_intersection_required_empty":True,"resolved_device_required_distinct":True,"ports_required_distinct":True,"rolling_dynamic_noninterference_gate_required":True}
 v["status"]="OFFLINE_JOB_NODE04_NVME1_NOT_LAUNCH_AUTHORITY";return canonical(v)

TEST_SOURCE=r'''import hashlib,json,stat,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def C(s):
 o=set()
 for p in s.split(","):
  a=p.split("-");o.update(range(int(a[0]),int(a[-1])+1))
 return o
def test_identity_scope_and_layout():
 m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id");raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode();assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest();assert m["schema_version"]=="mysql_job_node04_nvme1_deployment_successor_bundle_v16";assert m["formal_scope"]=="mysql_only" and m["cross_engine_comparison_prohibited"] is True and m["postgresql_mutation_prohibited"] is True and m["formal_launch_authorized"] is False and m["accepted_rounds_credit"]==0
 d=json.loads((ROOT/"deployment/mysql_job_node04_nvme1_contract_v1.json").read_text());assert d["storage"]["format_authorized"] is False and d["storage"]["ready"] is False and d["storage"]["readonly_root_probe_passed"] is True;assert d["database"]["cpuset_cpus"]=="25-26,61-62" and d["client"]["cpuset_cpus"]=="27-29,63-65" and d["dedicated_dockerd"]["cpuset_cpus"]=="30,66" and d["controller"]["cpuset_cpus"]=="31,67"
 lanes=list(map(C,("0-7,36-43","8-15,44-51","16-17,52-53","18-20,54-56","21-24,57-60","32-33,68-69","34-35,70-71","25-26,61-62","27-29,63-65","30,66","31,67")));assert all(a.isdisjoint(b) for i,a in enumerate(lanes) for b in lanes[i+1:])
 a=m["algorithm_c_contract"];assert a["original_gradient_confidence_required"] and a["formal_raw_all_nonempty_subsets_before_top5"] and a["fixed_top5_only"] and a["canonical_plan_sha_required_at_checkpoint"] and a["complete_l1_l2_l3_required_for_outer_acceptance"]
def test_gate_blocks_without_format_authority(tmp_path):
 d=json.loads((ROOT/"deployment/mysql_job_node04_nvme1_contract_v1.json").read_text());q=tmp_path/"s.json";q.write_text(json.dumps(d["snapshot_template"]));p=subprocess.run([sys.executable,str(ROOT/"gate/mysql_job_node04_nvme1_prelaunch_gate_v1.py"),str(q)],text=True,capture_output=True);assert p.returncode==2 and json.loads(p.stdout)["status"]=="BLOCKED_FAIL_CLOSED"
 g=(ROOT/"gate/mysql_job_node04_nvme1_prelaunch_gate_v1.py").read_text();assert 'ours=cpus("25-26,61-62")|cpus("27-29,63-65")|cpus("30,66")|cpus("31,67")' in g;assert "postgresql_absence_verified" in g;assert 'mysql_job_node04_nvme1_prelaunch_gate_result_v1' in g
def test_closure_readonly():
 m=json.loads((ROOT/"bundle_manifest.json").read_text());actual={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.rglob("*") if p.is_file() and p.relative_to(ROOT).as_posix() not in ("bundle_manifest.json","bundle_manifest.json.sha256")};assert actual==m["files"] and not list(ROOT.rglob("*.pyc"));assert all(not p.is_symlink() and stat.S_IMODE(p.stat().st_mode)&0o222==0 for p in (ROOT,*ROOT.rglob("*")))
'''.encode()

def freeze(root):
 for p in sorted(root.rglob("*"),reverse=True):
  if p.is_symlink():raise BuildError("symlink")
  p.chmod(stat.S_IMODE(p.stat().st_mode)&~0o222)
 root.chmod(stat.S_IMODE(root.stat().st_mode)&~0o222)
def main():
 parent=verify_parent();skip=("closure.json",BUILDER,TEST,"tests/test_mysql_job_node01_deployment_v15.py","deployment/mysql_job_node01_contract_v1.json","probe/node01_root_readonly_storage_probe.sh")
 payload={r:(PARENT/r).read_bytes() for r in parent["files"] if r not in skip};modes={r:stat.S_IMODE((PARENT/r).stat().st_mode) for r in payload}
 active=("authority/build_mysql_job_authorization_sidecar_successor_v6.py","authority/build_mysql_job_graphdriver_readback_successor_v9.py","authority/mysql_job_dedicated_daemon_contract_v1.py","controller/render_mysql_job_v32_bootstrap_spec_tmpfs_v2.py","controller/render_mysql_job_v32_qualification_solo_tmpfs_v2.py","qualification/mysql_job_dedicated_daemon_contract_v1.py","qualification/mysql_job_dedicated_daemon_materializer_v5.py","qualification/mysql_job_distributed_live_readback_qualification_v2.py","qualification/mysql_job_distributed_v2_adopted_services.py","qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py","qualification/mysql_job_distributed_v2_rolling_monitor.py","qualification/mysql_sql_debug_metric_adapter_v1.py","qualification/shell/manual_mysql_sgd_r0_campaign.py","qualification/fixtures/mysql_job_dedicated_daemon_readback_fixture_v1.json")
 for r in active:payload[r]=patch_text(payload[r])
 four="authority/mysql_algorithm_c_distributed_four_scene_successor_contract_v2_20260729.json";oldsha=sb(payload[four]);payload[four]=patch_four(payload[four]);newsha=sb(payload[four])
 for r in ("controller/render_mysql_job_v32_bootstrap_spec_tmpfs_v2.py","qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py"):payload[r]=payload[r].replace(oldsha.encode(),newsha.encode())
 payload["distributed_runtime_contract.json"]=patch_runtime(payload["distributed_runtime_contract.json"])
 cfg=json.loads(payload["config.json"]);cfg.update(bootstrap_authorized=False,qualification_authorized=False,fresh_r0_authorized=False,formal10_authorized=False,accepted_round_authorized=False,formal_scope="mysql_only",cross_engine_comparison_prohibited=True,postgresql_mutation_prohibited=True);payload["config.json"]=canonical(cfg)
 contract={"schema_version":"mysql_job_node04_nvme1_deployment_contract_v1","status":"SEALED_OFFLINE_EXPLICIT_FORMAT_AUTHORITY_REQUIRED","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"host":{"hostname":HOST,"address":ADDRESS,"ssh_target":SSH},"storage":{"device":DEVICE,"major_minor":MM,"serial":SERIAL,"wwn":WWN,"size":"2.9T","mount":MOUNT,"filesystem":"xfs","project":PROJECT,"runtime_root":RUNTIME,"daemon_root":DAEMON_ROOT,"readonly_root_probe_passed":True,"wipefs_signatures":[],"blkid_signatures":[],"fuser_pids":[],"lsof_openers":[],"swap":False,"mounted":False,"format_authorized":False,"format_authority_scope":"explicit_per_device_main_agent_only","ready":False},"database":{"cpuset_cpus":DB,"cpuset_mems":MEMS,"cpu_quota_cores":1.15,"memory_bytes":4294967296,"memory_swap_bytes":4294967296,"pids_max":512,"io_read_bps":7340032,"io_write_bps":7340032,"innodb_buffer_pool_bytes":3221225472,"mysql_port":65102},"client":{"cpuset_cpus":CLIENT,"cpuset_mems":MEMS,"cpu_quota_cores":6},"dedicated_dockerd":{"cpuset_cpus":DAEMON,"cpuset_mems":MEMS,"socket":"/run/ra-docker-mysql-job-v1.sock","data_root":DAEMON_ROOT},"controller":{"cpuset_cpus":CONTROLLER,"cpuset_mems":MEMS},"replay_port":65202,"known_parallel_layout":json.loads(payload["distributed_runtime_contract.json"])["known_parallel_layout"],"required_algorithm_gates":{"original_raw_gradient":True,"all_nonempty_subsets":15,"fixed_top5_real_replay":5,"journal_reconciliation_before_rerun":True,"complete_l1_l2_l3":True,"qps_outer_acceptance_only":True},"required_prelaunch_gates":["fresh real-PID inventory","physical sibling and NUMA static isolation","device and port isolation","explicit per-device format authority","post-format xfs major:minor readback","transactional content-addressed publication","180-second 181-sample rolling dynamic non-interference"],"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0}
 snap={"schema_version":"mysql_job_node04_nvme1_prelaunch_snapshot_v1","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"host":{"hostname":HOST,"address":ADDRESS},"bundle":{"bundle_id":None,"manifest_sha256":None,"published_verified":False},"storage":{"device":DEVICE,"major_minor":MM,"serial":SERIAL,"wwn":WWN,"root_readonly_probe":{"wipefs_signatures":[],"blkid_signatures":[],"fuser_pids":[],"openers":[]},"format_authority":{"authorized":False,"authority_id":None,"device":DEVICE},"post_format_mount_readback":{"target":MOUNT,"source_major_minor":None,"fstype":None,"rw":None}},"resource_spec":{**contract["database"],"replay_port":65202},"controller_spec":contract["controller"],"topology":{"cpu_to_node":{}},"free_ports":[],"process_inventory":{"complete":False,"real_pids_only":False,"postgresql_absence_verified":False,"postgresql":[],"records":[]},"dynamic_interference_gate":{"version":"mysql_only_rolling_noninterference_v1","duration_seconds":0,"sample_count":0,"passed":False},"launch_spec_hash":None};contract["snapshot_template"]=snap
 payload["deployment/mysql_job_node04_nvme1_contract_v1.json"]=canonical(contract);modes["deployment/mysql_job_node04_nvme1_contract_v1.json"]=0o444
 gate=patch_text(payload.pop("gate/mysql_job_node01_prelaunch_gate_v1.py")).decode().replace("mysql_job_node04_prelaunch_snapshot_v1","mysql_job_node04_nvme1_prelaunch_snapshot_v1").replace('check(d.get("launch_spec_hash")',f'check(d.get("controller_spec")=={{"cpuset_cpus":"{CONTROLLER}","cpuset_mems":"1"}},"controller spec",e)\ncheck(d.get("launch_spec_hash")')
 gate=gate.replace('for c in cpus("25-26,61-62"))', 'for c in (cpus("25-26,61-62")|cpus("27-29,63-65")|cpus("30,66")|cpus("31,67")))')
 gate=gate.replace('check(bool(pg) and all(', 'check((bool(pg) and all(').replace('for x in pg),"PG read-only inventory",e)', 'for x in pg)) or (not pg and inv.get("postgresql_absence_verified") is True),"PG read-only inventory or verified absence",e)')
 gate=gate.replace('ours=cpus("25-26,61-62")|cpus("27-29,63-65")|cpus("30,66")', 'ours=cpus("25-26,61-62")|cpus("27-29,63-65")|cpus("30,66")|cpus("31,67")')
 gate=gate.replace('mysql_job_node04_prelaunch_gate_result_v1', 'mysql_job_node04_nvme1_prelaunch_gate_result_v1')
 payload["gate/mysql_job_node04_nvme1_prelaunch_gate_v1.py"]=gate.encode();modes["gate/mysql_job_node04_nvme1_prelaunch_gate_v1.py"]=0o555
 probe='''#!/bin/sh\n# Read-only only; formatting and mounting are intentionally absent.\nset -eu\nsudo /sbin/wipefs -n /dev/nvme1n1\nsudo /sbin/blkid -p /dev/nvme1n1 || test "$?" -eq 2\nsudo /usr/sbin/fuser -v /dev/nvme1n1 || test "$?" -eq 1\nsudo /usr/sbin/lsof /dev/nvme1n1 || test "$?" -eq 1\n'''.encode();payload["probe/node04_nvme1_root_readonly_storage_probe.sh"]=probe;modes["probe/node04_nvme1_root_readonly_storage_probe.sh"]=0o555
 algtest="tests/test_mysql_job_algorithm_repair_successor_v14.py";payload[algtest]=payload[algtest].replace(b'SCHEMA = "mysql_job_node01_deployment_successor_bundle_v15"',f'SCHEMA = "{SCHEMA}"'.encode())
 payload[TEST]=TEST_SOURCE;modes[TEST]=0o444;payload[BUILDER]=Path(__file__).read_bytes();modes[BUILDER]=0o555
 closure={"schema_version":"mysql_job_node04_nvme1_module_closure_v16","predecessor_bundle_id":PARENT_ID,"predecessor_manifest_sha256":PARENT_SHA,"files":{r:sb(v) for r,v in sorted(payload.items())}};payload["closure.json"]=canonical(closure);modes["closure.json"]=0o444
 ident=dict(parent);ident.pop("bundle_id",None);ident.update(schema_version=SCHEMA,status="SEALED_JOB_NODE04_NVME1_NOT_LAUNCH_AUTHORITY",predecessor_bundle_id=PARENT_ID,predecessor_bundle_manifest_sha256=PARENT_SHA,formal_scope="mysql_only",cross_engine_comparison_prohibited=True,postgresql_mutation_prohibited=True,storage_format_authorized=False,formal_launch_authorized=False,formal10_authorized=False,accepted_round_authorized=False,accepted_rounds_credit=0,qualification_credit=0,r0_credit=0,candidate_credit=0,top5_replay_credit=0,remote_actions=0,deployment_contract="deployment/mysql_job_node04_nvme1_contract_v1.json",prelaunch_gate="gate/mysql_job_node04_nvme1_prelaunch_gate_v1.py",files={r:sb(v) for r,v in sorted(payload.items())})
 bid=sb(canonical(ident));manifest=dict(ident,bundle_id=bid);OUT.mkdir(parents=True,exist_ok=True);dst=OUT/bid
 if dst.exists():
  if json.loads((dst/"bundle_manifest.json").read_text())!=manifest:raise BuildError("existing differs")
  print(json.dumps({"bundle":str(dst),"bundle_id":bid,"manifest_sha256":sf(dst/"bundle_manifest.json"),"status":manifest["status"]},sort_keys=True));return
 stage=OUT/(".stage-"+bid+"-"+secrets.token_hex(6));stage.mkdir(mode=0o700)
 try:
  for r,v in payload.items():p=stage/r;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v);p.chmod(modes.get(r,0o444)&~0o222)
  raw=canonical(manifest);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(sb(raw)+"  bundle_manifest.json\n");freeze(stage);os.rename(stage,dst)
 except BaseException:
  if stage.exists():shutil.rmtree(stage)
  raise
 print(json.dumps({"bundle":str(dst),"bundle_id":bid,"manifest_sha256":sf(dst/"bundle_manifest.json"),"status":manifest["status"]},sort_keys=True))
if __name__=="__main__":
 try:main()
 except (OSError,ValueError,KeyError,TypeError,RuntimeError) as e:raise SystemExit("NOT_READY: "+str(e))

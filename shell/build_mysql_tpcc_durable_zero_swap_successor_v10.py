#!/usr/bin/env python3
"""Build an immutable, durable swappiness=0 TPC-C recommission successor."""
from __future__ import annotations
import hashlib,json,os,shutil,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve();PARENT_ID="64499c4745979a2df22b96dca6db2344fe6799a6ddc1f145da63efc1d84e4bd8";PARENT_SHA="02eba3eb55e9ee411e0ccae0ccb65e6ef88068d39cdb8e837f63dae04437fa25";PARENT=ROOT/"artifacts/mysql_tpcc_idle_python_successor_v7"/PARENT_ID;OUT=ROOT/"artifacts/mysql_tpcc_durable_zero_swap_successor_v10";CONTROL="control/mysql_tpcc_durable_zero_swap_recommission_v10.py"
SWAP_HELPER="control/docker_api_set_memory_swappiness_v10.py"
ADOPTED_SHA="8f73143b1ded2d344e7b6a577d0c438ab8705eec64a76605f214b99b93d8246e";FRESH_SHA="0c4af692177b9857ecb95b219fcbb62d0e5db5b4ce3451fc8b2fbac798508b01";REAPPLY_SHA="10192d1effad48dc9d4b06b257f9a33669a291d054273bdbf84d410a9610f1c3"
def can(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def hs(v):return hashlib.sha256(v).hexdigest()
def sha(p):return hs(Path(p).read_bytes())
def verify():
 raw=(PARENT/"bundle_manifest.json").read_bytes();m=json.loads(raw);u=dict(m);bid=u.pop("bundle_id",None)
 if sha(PARENT/"bundle_manifest.json")!=PARENT_SHA or bid!=PARENT_ID or hs(can(u))!=PARENT_ID:raise RuntimeError("parent differs")
 for r,w in m["files"].items():
  if sha(PARENT/r)!=w:raise RuntimeError("parent closure differs: "+r)
 return m
def freeze(root):
 for p in sorted(root.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(root,0o555)

CONSTANTS='''BASELINE_ADOPTED=ATTEMPT/"adopted_service.zero_swap_restart_v1.json";BASELINE_ADOPTED_SHA="'''+ADOPTED_SHA+'''"\nBASELINE_FRESH=ATTEMPT/"blkio_throttle_authority.zero_swap_restart_v1.json";BASELINE_FRESH_SHA="'''+FRESH_SHA+'''"\nBASELINE_REAPPLY=ATTEMPT/"blkio_throttle_reapply.zero_swap_restart_v1.json";BASELINE_REAPPLY_SHA="'''+REAPPLY_SHA+'''"\nBASELINE_DB_PID=20831;BASELINE_CLIENT_PID=21060;BASELINE_DB_START=262525334;BASELINE_CLIENT_START=262526895\nBASELINE_DB_CGROUP_SHA="a62bc7b27379d559ccfe49a6fd1af239814d5748416e60a5e9cf4e982b37c5cd";BASELINE_CLIENT_CGROUP_SHA="75a81987dfeef61643388a3b47dbfb230d2848893a61f70df275516e71061cf6"\n'''

EXTRA=r'''def exact_baseline(path,want,label):
 path=Path(path);value=load_sealed(path)
 if sha(path)!=want:raise E(label+" exact SHA differs")
 return value
def preflight():
 before=snapshot();db=before["database"];client=before["client"]
 if db["pid"]!=BASELINE_DB_PID or db["process_start_ticks"]!=BASELINE_DB_START or db["process_cgroup_sha256"]!=BASELINE_DB_CGROUP_SHA or client["pid"]!=BASELINE_CLIENT_PID or client["process_start_ticks"]!=BASELINE_CLIENT_START or client["process_cgroup_sha256"]!=BASELINE_CLIENT_CGROUP_SHA:raise E("current process epoch differs from sealed v10 baseline")
 if db["docker_memory_swappiness"] is not None or client["docker_memory_swappiness"] is not None or db["memory_swappiness"]!=60 or client["memory_swappiness"]!=60:raise E("baseline inherited swappiness diagnosis differs")
 adopted=exact_baseline(BASELINE_ADOPTED,BASELINE_ADOPTED_SHA,"baseline adopted service");fresh=exact_baseline(BASELINE_FRESH,BASELINE_FRESH_SHA,"baseline fresh blkio");reapply=exact_baseline(BASELINE_REAPPLY,BASELINE_REAPPLY_SHA,"baseline reapply")
 if (adopted.get("database") or {}).get("pid")!=BASELINE_DB_PID or (adopted.get("client") or {}).get("pid")!=BASELINE_CLIENT_PID or fresh.get("database_pid")!=BASELINE_DB_PID or reapply.get("database_pid")!=BASELINE_DB_PID or reapply.get("client_pid")!=BASELINE_CLIENT_PID:raise E("baseline authority epoch differs")
 idle=sealed_idle_python_evidence(client,Path("/proc"),private_socket_inspects());pg=postgres_blkio_cgroups(Path("/proc"))
 if "/docker/"+DB_ID in pg:raise E("database target is a PostgreSQL cgroup")
 for target in (TARGET,REAPPLY_RECEIPT,FRESH_BLKIO):
  if target.exists() or target.is_symlink() or Path(str(target)+".sha256").exists():raise E("fresh durable successor output required: "+str(target))
 before.update(schema_version="mysql_tpcc_durable_zero_swap_preflight_v10",status="PASS_READ_ONLY_DURABLE_ZERO_SWAP_RECOMMISSION_READY",baseline_adopted_sha256=BASELINE_ADOPTED_SHA,baseline_fresh_blkio_sha256=BASELINE_FRESH_SHA,baseline_reapply_sha256=BASELINE_REAPPLY_SHA,client_idle_evidence=idle,observed_postgresql_blkio_cgroups=pg,diagnosis={"database_total_swap_bytes":db["total_swap_bytes"],"database_vmswap_observed_bytes":40960,"inherited_cgroup_swappiness":60,"docker_hostconfig_memory_swappiness":None,"root_cause":"memory and memsw equality does not independently prohibit anonymous swap while combined usage is below both limits"},required_durable_memory_swappiness=0,mutation_performed=False,accepted_rounds_credit=0)
 return before
def verify_update(before_path):
 before=load_sealed(before_path)
 if before.get("schema_version")!="mysql_tpcc_durable_zero_swap_preflight_v10" or before.get("status")!="PASS_READ_ONLY_DURABLE_ZERO_SWAP_RECOMMISSION_READY" or before.get("baseline_adopted_sha256")!=BASELINE_ADOPTED_SHA:raise E("durable preflight differs")
 current=snapshot()
 for role in ("database","client"):
  a=current[role];b=before[role]
  if a["container_id"]!=b["container_id"] or a["image_id"]!=b["image_id"] or a["created"]!=b["created"] or a["mounts_sha256"]!=b["mounts_sha256"] or a["pid"]!=b["pid"] or a["process_start_ticks"]!=b["process_start_ticks"]:raise E(role+" changed before swappiness update receipt")
  if a["docker_memory_swappiness"]!=0 or a["memory_swappiness"]!=0:raise E(role+" durable swappiness update readback differs")
 idle=sealed_idle_python_evidence(current["client"],Path("/proc"),private_socket_inspects())
 return {"schema_version":"mysql_tpcc_durable_swappiness_update_receipt_v10","status":"PASS_DOCKER_HOSTCONFIG_AND_CGROUP_SWAPPINESS_ZERO_BEFORE_RESTART","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"preflight_sha256":sha(before_path),"database":{"container_id":DB_ID,"pid":current["database"]["pid"],"process_start_ticks":current["database"]["process_start_ticks"],"docker_memory_swappiness":0,"memory_swappiness":0,"total_swap_bytes_before_restart":current["database"]["total_swap_bytes"]},"client":{"container_id":CLIENT_ID,"pid":current["client"]["pid"],"process_start_ticks":current["client"]["process_start_ticks"],"docker_memory_swappiness":0,"memory_swappiness":0,"total_swap_bytes_before_restart":current["client"]["total_swap_bytes"]},"client_idle_evidence":idle,"restart_performed":False,"postgresql_mutation_performed":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0}
def verify_update_receipt(update,before_path):
 before=load_sealed(before_path)
 required={"schema_version":"mysql_tpcc_durable_swappiness_update_receipt_v10","status":"PASS_DOCKER_HOSTCONFIG_AND_CGROUP_SWAPPINESS_ZERO_BEFORE_RESTART","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"preflight_sha256":sha(before_path),"restart_performed":False,"postgresql_mutation_performed":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0}
 for key,want in required.items():
  if update.get(key)!=want:raise E("swappiness update receipt field differs: "+key)
 for role,cid in (("database",DB_ID),("client",CLIENT_ID)):
  row=update.get(role) or {};baseline=before.get(role) or {}
  if row.get("container_id")!=cid or row.get("pid")!=baseline.get("pid") or row.get("process_start_ticks")!=baseline.get("process_start_ticks"):raise E(role+" swappiness update receipt identity differs")
  if row.get("docker_memory_swappiness")!=0 or row.get("memory_swappiness")!=0:raise E(role+" swappiness update receipt zero readback differs")
  if not isinstance(row.get("total_swap_bytes_before_restart"),int) or row["total_swap_bytes_before_restart"]<0:raise E(role+" swappiness update receipt swap field differs")
 idle=update.get("client_idle_evidence") or {}
 if idle.get("schema_version")!="mysql_tpcc_idle_python_identity_v1" or idle.get("client_container_id")!=CLIENT_ID or idle.get("client_pid")!=before["client"]["pid"] or idle.get("process_start_ticks")!=before["client"]["process_start_ticks"] or idle.get("workload_absent") is not True or idle.get("forbidden_dbworkload_java_mysql_query_processes")!=0:raise E("swappiness update receipt idle evidence differs")
'''

TEST=r'''import hashlib,importlib.util,json
from pathlib import Path
B=Path(__file__).resolve().parents[1];C=B/"control/mysql_tpcc_durable_zero_swap_recommission_v10.py";R=B/"runbook/transaction.json";E=B/"evidence/mysql_tpcc_swap_root_cause_v1.json"
def test_content_address_scope():
 m=json.loads((B/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id");assert hashlib.sha256((json.dumps(u,sort_keys=True,indent=2)+"\n").encode()).hexdigest()==m["bundle_id"]==B.name;assert m["formal_scope"]=="mysql_only" and m["postgresql_mutation_prohibited"] is True and m["accepted_rounds_credit"]==0
def test_root_cause_is_real_swap_not_noise():
 e=json.loads(E.read_text());assert e["database_cgroup_total_swap_bytes"]==36864 and e["mysqld_vmswap_bytes"]==40960 and e["cgroup_memory_swappiness"]==60 and e["docker_hostconfig_memory_swappiness"] is None and e["global_swap_used_bytes"]>0
def test_zero_threshold_not_relaxed_and_durable_readback_required():
 s=C.read_text();assert 'after["total_swap_bytes"]!=0' in s and 'a["docker_memory_swappiness"]!=0 or a["memory_swappiness"]!=0' in s and 'after["docker_memory_swappiness"]!=0 or after["memory_swappiness"]!=0' in s;assert "<=36864" not in s and "swap_tolerance" not in s
def test_transaction_updates_hostconfig_before_restart():
 r=json.loads(R.read_text());steps=r["ordered_steps"];assert steps[0]["action"]=="preflight" and steps[1]["argv"][-2:]==["--memory-swappiness","0"] and steps[2]["argv"][-2:]==["--memory-swappiness","0"] and steps[3]["action"]=="verify_update"
 assert all("docker_api_set_memory_swappiness_v10.py" in x["argv"][2] for x in steps[1:3])
 assert [x["action"] for x in steps[4:8]]==["stop_client","stop_database","start_database","start_client"] and steps[8]["action"]=="adopt_and_reseal"
def test_same_container_fresh_authority_chain_and_pg_prohibition():
 s=C.read_text();assert 'adopted_service.zero_swap_swappiness0_restart_v2.json' in s and 'blkio_throttle_reapply.zero_swap_swappiness0_restart_v2.json' in s and 'blkio_throttle_authority.zero_swap_swappiness0_restart_v2.json' in s
 for required in ('same_container_ids":True','legacy_blkio_authority_overwritten":False','postgresql_mutation_performed":False','database target is a PostgreSQL cgroup','client_idle_evidence'):
  assert required in s
def test_partial_update_is_fail_closed_before_restart():
 s=C.read_text();assert 'changed before swappiness update receipt' in s and 'durable swappiness update readback differs' in s and 'verify_update_receipt(update,a.before)' in s and 'update_receipt_sha256' in s
def test_restart_and_cgroup_epoch_are_exact():
 s=C.read_text();assert 'after["restart_count"]!=before["restart_count"]' in s and 'after["memory_cgroup_path"]!=before["memory_cgroup_path"]' in s and 'after["pid"]==before["pid"]' in s
def test_generated_control_parses():
 import ast;ast.parse(C.read_text());ast.parse((B/"control/docker_api_set_memory_swappiness_v10.py").read_text())
def test_builder_declares_no_remote_execution():
 m=json.loads((B/"bundle_manifest.json").read_text());assert m["remote_actions_performed"]==0 and m["formal_launch_authorized"] is False and m["accepted_round_authorized"] is False
'''

SWAP_HELPER_SOURCE=r'''#!/usr/bin/env python3
"""Set MemorySwappiness through the pinned private Docker Engine API."""
import argparse,json,os,socket,sys
SOCKET="/run/ra-docker-mysql-tpcc-v1.sock"
ALLOWED={"5cdcc870ef04435bad3b44eb89135fd00b5d4825179887e2a51862712f1e3db6","445c4e1732fe6cf8cb07e8f32159aac5cf0aa10bcd1fd51a9db3e5a725a0a1e6"}
def fail(message):
 print("NOT_APPLIED: "+message,file=sys.stderr);raise SystemExit(2)
def main():
 if socket.gethostname().split(".")[0]!="node04" or os.geteuid()!=0:fail("node04 root required")
 p=argparse.ArgumentParser();p.add_argument("container_id");p.add_argument("--memory-swappiness",type=int,required=True);a=p.parse_args()
 if a.container_id not in ALLOWED or a.memory_swappiness!=0:fail("exact allowlist and zero swappiness required")
 body=json.dumps({"MemorySwappiness":0},separators=(",",":")).encode();path="/v1.45/containers/"+a.container_id+"/update"
 request=("POST "+path+" HTTP/1.1\r\nHost: docker\r\nContent-Type: application/json\r\nContent-Length: "+str(len(body))+"\r\nConnection: close\r\n\r\n").encode()+body
 client=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);client.settimeout(15)
 try:
  client.connect(SOCKET);client.sendall(request);chunks=[]
  while True:
   part=client.recv(65536)
   if not part:break
   chunks.append(part)
 finally:client.close()
 raw=b"".join(chunks)
 try:head,payload=raw.split(b"\r\n\r\n",1);status=int(head.split(b"\r\n",1)[0].split()[1]);value=json.loads(payload or b"{}")
 except (ValueError,IndexError,json.JSONDecodeError):fail("invalid Docker Engine response")
 if status!=200:fail("Docker Engine update rejected status="+str(status)+" body="+payload.decode("utf-8","replace")[:500])
 warnings=value.get("Warnings")
 if warnings not in (None,[]):fail("Docker Engine update warning: "+json.dumps(warnings,sort_keys=True))
 print(json.dumps({"status":"APPLIED_PENDING_SEALED_READBACK","container_id":a.container_id,"memory_swappiness":0,"api":"v1.45","postgresql_mutation_performed":False},sort_keys=True))
if __name__=="__main__":main()
'''

def main():
 verify();source=(PARENT/"control/mysql_tpcc_zero_swap_recommission_idle_python_v7.py").read_text()
 patches=(('ADOPT_MODULE_SHA="f0d149fd3d9fc9a3b201ef29ea11b7a3d66632c5c1fbbda931fddc89031ab398";TARGET=ATTEMPT/"adopted_service.zero_swap_restart_v1.json"','ADOPT_MODULE_SHA="f0d149fd3d9fc9a3b201ef29ea11b7a3d66632c5c1fbbda931fddc89031ab398";TARGET=ATTEMPT/"adopted_service.zero_swap_swappiness0_restart_v2.json"\n'+CONSTANTS),(' d=Path("/proc")/str(pid);raw=(d/"stat").read_text();end=raw.rfind(") ");start=int(raw[end+2:].split()[19]);cg=[]',' d=Path("/proc")/str(pid);raw=(d/"stat").read_text();end=raw.rfind(") ");start=int(raw[end+2:].split()[19]);raw_cgroup=(d/"cgroup").read_text();cg=[]'),(' for line in (d/"cgroup").read_text().splitlines():',' for line in raw_cgroup.splitlines():'),(' return {"pid":pid,"process_start_ticks":start,"memory_cgroup_path":cg[0],"total_swap_bytes":rows.get("total_swap"),"memory_limit_bytes":int((root/"memory.limit_in_bytes").read_text()),"memory_swap_limit_bytes":int((root/"memory.memsw.limit_in_bytes").read_text())}',' return {"pid":pid,"process_start_ticks":start,"process_cgroup_sha256":hashlib.sha256(raw_cgroup.encode()).hexdigest(),"memory_cgroup_path":cg[0],"memory_swappiness":int((root/"memory.swappiness").read_text()),"total_swap_bytes":rows.get("total_swap"),"memory_limit_bytes":int((root/"memory.limit_in_bytes").read_text()),"memory_swap_limit_bytes":int((root/"memory.memsw.limit_in_bytes").read_text())}'),(' return {"role":role,"container_id":cid,"container_name":name,"image_id":row.get("Image"),"created":row.get("Created"),"restart_count":int(row.get("RestartCount") or 0),"mounts":mounts,"mounts_sha256":mounts_sha256(mounts),**proc(pid)}',' return {"role":role,"container_id":cid,"container_name":name,"image_id":row.get("Image"),"created":row.get("Created"),"restart_count":int(row.get("RestartCount") or 0),"docker_memory_swappiness":h.get("MemorySwappiness"),"mounts":mounts,"mounts_sha256":mounts_sha256(mounts),**proc(pid)}'),(' if after["total_swap_bytes"]!=0 or after["memory_limit_bytes"]!=after["memory_swap_limit_bytes"]:raise E(role+" zero-swap postcondition failed")',' if after["total_swap_bytes"]!=0 or after["memory_limit_bytes"]!=after["memory_swap_limit_bytes"]:raise E(role+" zero-swap postcondition failed")\n if after["docker_memory_swappiness"]!=0 or after["memory_swappiness"]!=0:raise E(role+" durable swappiness zero postcondition failed")'),('REAPPLY_RECEIPT=ATTEMPT/"blkio_throttle_reapply.zero_swap_restart_v1.json"','REAPPLY_RECEIPT=ATTEMPT/"blkio_throttle_reapply.zero_swap_swappiness0_restart_v2.json"'),('FRESH_BLKIO=ATTEMPT/"blkio_throttle_authority.zero_swap_restart_v1.json"','FRESH_BLKIO=ATTEMPT/"blkio_throttle_authority.zero_swap_swappiness0_restart_v2.json"'),('def main():',EXTRA+'def main():'),('p.add_argument("action",choices=("snapshot","adopt"));p.add_argument("--before",type=Path);p.add_argument("--output",type=Path,required=True);a=p.parse_args()','p.add_argument("action",choices=("preflight","verify-update","adopt"));p.add_argument("--before",type=Path);p.add_argument("--update",type=Path);p.add_argument("--output",type=Path,required=True);a=p.parse_args()'),(' if a.action=="snapshot":value=snapshot()\n else:\n  if a.before is None or a.output.resolve(strict=False)!=TARGET:raise E("adopt path differs")\n  value=adopt(a.before)',' if a.action=="preflight":value=preflight()\n elif a.action=="verify-update":\n  if a.before is None:value=None;raise E("preflight path required")\n  value=verify_update(a.before)\n else:\n  if a.before is None or a.update is None or a.output.resolve(strict=False)!=TARGET:raise E("durable adopt inputs/path differ")\n  update=load_sealed(a.update)\n  if update.get("schema_version")!="mysql_tpcc_durable_swappiness_update_receipt_v10" or update.get("preflight_sha256")!=sha(a.before):raise E("swappiness update receipt differs")\n  value=adopt(a.before);value["durable_zero_swap"]={"schema_version":"mysql_tpcc_durable_zero_swap_epoch_v2","required_memory_swappiness":0,"docker_hostconfig_memory_swappiness":0,"cgroup_memory_swappiness":0,"database_total_swap_bytes":0,"client_total_swap_bytes":0,"update_receipt_path":str(a.update),"update_receipt_sha256":sha(a.update),"baseline_adopted_sha256":BASELINE_ADOPTED_SHA,"postgresql_mutation_performed":False,"accepted_rounds_credit":0}\n  value["pre_authority_zero_swap_recommission"]["schema_version"]="mysql_tpcc_durable_zero_swap_recommission_receipt_v2"\n  value["pre_authority_zero_swap_recommission"]["update_receipt_sha256"]=sha(a.update)'))
 for old,new in patches:
  if source.count(old)!=1:raise RuntimeError("patch anchor differs: "+old[:100])
  source=source.replace(old,new)
 continuity_old=' if after["total_swap_bytes"]!=0 or after["memory_limit_bytes"]!=after["memory_swap_limit_bytes"]:raise E(role+" zero-swap postcondition failed")\n if after["docker_memory_swappiness"]!=0 or after["memory_swappiness"]!=0:raise E(role+" durable swappiness zero postcondition failed")'
 continuity_new=' if after["memory_cgroup_path"]!=before["memory_cgroup_path"]:raise E(role+" cgroup continuity differs")\n if after["restart_count"]!=before["restart_count"]:raise E(role+" unexpected automatic restart count change")\n'+continuity_old
 if source.count(continuity_old)!=1:raise RuntimeError("continuity hardening anchor differs")
 source=source.replace(continuity_old,continuity_new)
 source=source.replace('  if a.before is None:value=None;raise E("preflight path required")','  if a.before is None:raise E("preflight path required")')
 receipt_old='  if update.get("schema_version")!="mysql_tpcc_durable_swappiness_update_receipt_v10" or update.get("preflight_sha256")!=sha(a.before):raise E("swappiness update receipt differs")'
 receipt_new='  verify_update_receipt(update,a.before)'
 if source.count(receipt_old)!=1:raise RuntimeError("update receipt hardening anchor differs")
 source=source.replace(receipt_old,receipt_new)
 evidence={"schema_version":"mysql_tpcc_swap_root_cause_v1","captured_read_only_at":"2026-08-04T15:23:11+08:00","database_pid":20831,"client_pid":21060,"docker_hostconfig_memory_swappiness":None,"cgroup_memory_swappiness":60,"host_vm_swappiness":60,"database_cgroup_total_swap_bytes":36864,"database_memsw_minus_memory_usage_bytes":36864,"mysqld_vmswap_bytes":40960,"mysqld_smaps_swap_bytes":40960,"swapped_vmas":["libcrypto private","libssl private","ld-linux private","anonymous","stack"],"client_vmswap_bytes":0,"global_swap_used_bytes":16323969024,"memory_limit_bytes":34359738368,"memsw_limit_bytes":34359738368,"root_cause":"cgroup swappiness inherited 60; equal memory/memsw upper limits do not create an independent zero-swap constraint while combined usage remains below both limits","swap_noise_or_rounding_explanation_rejected":True,"zero_swap_threshold_bytes":0,"postgresql_mutation_performed":False}
 names={"db":"ra_mysql_tpcc_v2_db_tpcc-v16-7e2e13aa-20260801t121500p08","client":"ra_mysql_tpcc_v2_client_tpcc-v16-7e2e13aa-20260801t121500p08"};docker=["/usr/bin/docker","--host","unix:///run/ra-docker-mysql-tpcc-v1.sock"]
 runbook={"schema_version":"mysql_tpcc_durable_zero_swap_transaction_v10","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"execution_authority":"main_agent_serial_only_after_explicit_launch_decision","rollback_boundary":"After either update or stop, do not restore swappiness=60 and do not use old authorities; reconcile this transaction or remain blocked.","ordered_steps":[{"step":0,"action":"preflight","argv":["[PINNED_PYTHON]","-B","[THIS_BUNDLE]/"+CONTROL,"preflight","--output","[FRESH_PREFLIGHT_JSON]"]},{"step":1,"action":"update_client_swappiness","argv":docker+["update",names["client"],"--memory-swappiness","0"]},{"step":2,"action":"update_database_swappiness","argv":docker+["update",names["db"],"--memory-swappiness","0"]},{"step":3,"action":"verify_update","argv":["[PINNED_PYTHON]","-B","[THIS_BUNDLE]/"+CONTROL,"verify-update","--before","[FRESH_PREFLIGHT_JSON]","--output","[FRESH_UPDATE_RECEIPT_JSON]"]},{"step":4,"action":"stop_client","argv":docker+["stop","--time","30",names["client"]]},{"step":5,"action":"stop_database","graceful_timeout_seconds":120,"argv":docker+["stop","--time","120",names["db"]]},{"step":6,"action":"start_database","argv":docker+["start",names["db"]]},{"step":7,"action":"start_client","argv":docker+["start",names["client"]]},{"step":8,"action":"adopt_and_reseal","argv":["[PINNED_PYTHON]","-B","[THIS_BUNDLE]/"+CONTROL,"adopt","--before","[FRESH_PREFLIGHT_JSON]","--update","[FRESH_UPDATE_RECEIPT_JSON]","--output",str(Path("/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/tpcc-v16-7e2e13aa-20260801t121500p08/adopted_service.zero_swap_swappiness0_restart_v2.json"))]}],"postconditions":["same container IDs/image/Created/datadir/mount projections","new DB and client PID/start ticks","Docker HostConfig MemorySwappiness exactly 0","both cgroup memory.swappiness exactly 0","both total_swap exactly 0","fresh blkio read/write 259:2 1205862400 re-applied and read back","new non-overwriting reapply/fresh/adopted authority chain","no PostgreSQL mutation"],"accepted_rounds_credit":0}
 runbook["ordered_steps"][1]["argv"]=["[PINNED_PYTHON]","-B","[THIS_BUNDLE]/"+SWAP_HELPER,"445c4e1732fe6cf8cb07e8f32159aac5cf0aa10bcd1fd51a9db3e5a725a0a1e6","--memory-swappiness","0"]
 runbook["ordered_steps"][2]["argv"]=["[PINNED_PYTHON]","-B","[THIS_BUNDLE]/"+SWAP_HELPER,"5cdcc870ef04435bad3b44eb89135fd00b5d4825179887e2a51862712f1e3db6","--memory-swappiness","0"]
 runbook["postconditions"].insert(2,"manual stop/start gives new PID/start ticks while automatic RestartCount stays unchanged")
 contents={CONTROL:source.encode(),SWAP_HELPER:SWAP_HELPER_SOURCE.encode(),"runbook/transaction.json":can(runbook),"evidence/mysql_tpcc_swap_root_cause_v1.json":can(evidence),"tests/test_mysql_tpcc_durable_zero_swap_successor_v10.py":TEST.encode(),"builder/build_mysql_tpcc_durable_zero_swap_successor_v10.py":HERE.read_bytes()};files={r:hs(v) for r,v in sorted(contents.items())};unsigned={"schema_version":"mysql_tpcc_durable_zero_swap_successor_bundle_v10","status":"SEALED_NOT_EXECUTED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"parent_bundle_id":PARENT_ID,"parent_manifest_sha256":PARENT_SHA,"root_cause":"inherited_cgroup_swappiness_60_allowed_real_mysqld_swap","zero_swap_threshold_bytes":0,"docker_hostconfig_memory_swappiness_required":0,"cgroup_memory_swappiness_required":0,"same_container_restart_required":True,"datadir_replacement_prohibited":True,"new_authority_epoch_required":True,"old_authority_reuse_prohibited":True,"docker_engine_api_version":"v1.45","docker_cli_update_memory_swappiness_available":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions_performed":0,"files":files};bid=hs(can(unsigned));manifest=dict(unsigned,bundle_id=bid);raw=can(manifest);OUT.mkdir(parents=True,exist_ok=True);dest=OUT/bid
 if dest.exists():
  if (dest/"bundle_manifest.json").read_bytes()!=raw:raise RuntimeError("existing manifest differs")
  for r,v in contents.items():
   if (dest/r).read_bytes()!=v:raise RuntimeError("existing closure differs: "+r)
 else:
  stage=Path(tempfile.mkdtemp(prefix=".durable-zero-swap-",dir=OUT))
  try:
   for r,v in contents.items():p=stage/r;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
   (stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(hs(raw)+"  bundle_manifest.json\n");freeze(stage);os.rename(stage,dest)
  finally:
   if stage.exists():shutil.rmtree(stage)
 print(bid);print(sha(dest/"bundle_manifest.json"));print(dest)
if __name__=="__main__":main()

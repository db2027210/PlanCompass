#!/usr/bin/env python3
"""Build the immutable DB+client cgroup recommission transaction/runbook."""
from __future__ import annotations
import hashlib,json,os,shutil,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve();OUT=ROOT/"artifacts/mysql_tpcc_zero_swap_recommission_runbook_v1"
def can(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def hs(v):return hashlib.sha256(v).hexdigest()
SCRIPT=r'''#!/usr/bin/env python3
import argparse,hashlib,importlib.util,json,os,socket,stat,subprocess,sys
from pathlib import Path
ROOT=Path("/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729")
ATTEMPT=ROOT/"tpcc-v16-7e2e13aa-20260801t121500p08";BUNDLE=ROOT/"bundles/7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77";SOCKET="unix:///run/ra-docker-mysql-tpcc-v1.sock"
DB="ra_mysql_tpcc_v2_db_tpcc-v16-7e2e13aa-20260801t121500p08";CLIENT="ra_mysql_tpcc_v2_client_tpcc-v16-7e2e13aa-20260801t121500p08";DB_ID="5cdcc870ef04435bad3b44eb89135fd00b5d4825179887e2a51862712f1e3db6";CLIENT_ID="445c4e1732fe6cf8cb07e8f32159aac5cf0aa10bcd1fd51a9db3e5a725a0a1e6"
ADOPT_MODULE_SHA="f0d149fd3d9fc9a3b201ef29ea11b7a3d66632c5c1fbbda931fddc89031ab398";TARGET=ATTEMPT/"adopted_service.zero_swap_restart_v1.json"
class E(RuntimeError):pass
def can(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def atomic(p,v):
 p=Path(p);raw=can(v);p.parent.mkdir(parents=True,exist_ok=True);fd=os.open(str(p),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o400)
 try:
  with os.fdopen(fd,"wb") as f:f.write(raw);f.flush();os.fsync(f.fileno())
 except BaseException:
  try:os.unlink(p)
  except OSError:pass
  raise
 s=Path(str(p)+".sha256");s.write_text(hashlib.sha256(raw).hexdigest()+"  "+p.name+"\n");os.chmod(s,0o400)
def inspect(name):
 r=subprocess.run(["/usr/bin/docker","--host",SOCKET,"inspect",name],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=30)
 if r.returncode:raise E("container inspect failed: "+name)
 v=json.loads(r.stdout)
 if len(v)!=1 or (v[0].get("State") or {}).get("Running") is not True:raise E("container is not running: "+name)
 return v[0]
def proc(pid):
 d=Path("/proc")/str(pid);raw=(d/"stat").read_text();end=raw.rfind(") ");start=int(raw[end+2:].split()[19]);cg=[]
 for line in (d/"cgroup").read_text().splitlines():
  p=line.split(":",2)
  if len(p)==3 and "memory" in p[1].split(","):cg.append(p[2])
 if len(cg)!=1:raise E("memory cgroup differs")
 root=Path("/sys/fs/cgroup/memory")/cg[0].lstrip("/");rows={}
 for line in (root/"memory.stat").read_text().splitlines():
  x=line.split()
  if len(x)==2:rows[x[0]]=int(x[1])
 return {"pid":pid,"process_start_ticks":start,"memory_cgroup_path":cg[0],"total_swap_bytes":rows.get("total_swap"),"memory_limit_bytes":int((root/"memory.limit_in_bytes").read_text()),"memory_swap_limit_bytes":int((root/"memory.memsw.limit_in_bytes").read_text())}
def project(row,role):
 cid=DB_ID if role=="database" else CLIENT_ID;want=34359738368 if role=="database" else 8589934592;name=DB if role=="database" else CLIENT
 if row.get("Id")!=cid or str(row.get("Name") or "").lstrip("/")!=name:raise E(role+" immutable container differs")
 h=row.get("HostConfig") or {};pid=int((row.get("State") or {}).get("Pid") or 0)
 if int(h.get("Memory") or 0)!=want or int(h.get("MemorySwap") or 0)!=want:raise E(role+" memory envelope differs")
 mounts=[{"source":m.get("Source"),"destination":m.get("Destination"),"rw":m.get("RW")} for m in row.get("Mounts") or []]
 return {"role":role,"container_id":cid,"container_name":name,"image_id":row.get("Image"),"created":row.get("Created"),"restart_count":int(row.get("RestartCount") or 0),"mounts":mounts,"mounts_sha256":hashlib.sha256(can(mounts)).hexdigest(),**proc(pid)}
def snapshot():
 db=project(inspect(DB),"database");client=project(inspect(CLIENT),"client");datadir=[m for m in db["mounts"] if m["destination"]=="/var/lib/mysql" and m["rw"] is True]
 if len(datadir)!=1 or datadir[0]["source"]!=str(ATTEMPT/"datadir/datadir"):raise E("database datadir continuity differs")
 return {"schema_version":"mysql_tpcc_zero_swap_recommission_snapshot_v1","status":"PASS_READ_ONLY_PRE_RESTART","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"database":db,"client":client,"datadir":datadir[0],"database_restarted":False,"client_restarted":False,"mutation_performed":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0}
def load_sealed(p):
 p=Path(p);s=Path(str(p)+".sha256")
 if s.read_text()!=sha(p)+"  "+p.name+"\n":raise E("sealed input differs")
 return json.loads(p.read_text())
def adopt(before_path):
 before=load_sealed(before_path);after=snapshot()
 for role in ("database","client"):
  a=after[role];b=before[role]
  if a["container_id"]!=b["container_id"] or a["image_id"]!=b["image_id"] or a["created"]!=b["created"] or a["mounts_sha256"]!=b["mounts_sha256"]:raise E(role+" immutable continuity differs")
  if a["pid"]==b["pid"] or a["process_start_ticks"]==b["process_start_ticks"]:raise E(role+" cgroup was not recommissioned")
  if a["total_swap_bytes"]!=0 or a["memory_limit_bytes"]!=a["memory_swap_limit_bytes"]:raise E(role+" zero-swap postcondition failed")
 module=BUNDLE/"authority/mysql_tpcc_node04_adopted_service_v2.py"
 if sha(module)!=ADOPT_MODULE_SHA:raise E("sealed adopt module differs")
 spec=importlib.util.spec_from_file_location("tpcc_recommission_adopt",module);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);plan=json.loads((ATTEMPT/"plan.json").read_text());value=m.adopt(plan)
 if value.get("schema_version")!="mysql_tpcc_node04_adopted_service_authority_v2" or value.get("status")!="PASS":raise E("fresh adopted service differs")
 if value["database"]["pid"]!=after["database"]["pid"] or value["client"]["pid"]!=after["client"]["pid"]:raise E("fresh adopted PID differs")
 value["authority_epoch_semantics"]="database_restarted_false_means_no_restart_after_this_fresh_authority_epoch"
 value["pre_authority_zero_swap_recommission"]={"schema_version":"mysql_tpcc_zero_swap_recommission_receipt_v1","before_sha256":sha(before_path),"database":{"old_pid":before["database"]["pid"],"new_pid":after["database"]["pid"],"total_swap_bytes":0},"client":{"old_pid":before["client"]["pid"],"new_pid":after["client"]["pid"],"total_swap_bytes":0},"same_container_ids":True,"same_datadir_mount":True,"postgresql_mutation_performed":False}
 return value
def main():
 if socket.gethostname().split(".")[0]!="node04" or os.geteuid()!=0:raise E("node04 root required")
 p=argparse.ArgumentParser();p.add_argument("action",choices=("snapshot","adopt"));p.add_argument("--before",type=Path);p.add_argument("--output",type=Path,required=True);a=p.parse_args()
 if a.action=="snapshot":value=snapshot()
 else:
  if a.before is None or a.output.resolve(strict=False)!=TARGET:raise E("adopt path differs")
  value=adopt(a.before)
 atomic(a.output,value);print(json.dumps({"status":"PASS","output":str(a.output)},sort_keys=True))
if __name__=="__main__":
 try:main()
 except (E,OSError,ValueError,KeyError,subprocess.SubprocessError) as e:print("NOT_READY: "+str(e),file=sys.stderr);raise SystemExit(2)
'''
TEST=r'''import hashlib,json
from pathlib import Path
B=Path(__file__).resolve().parents[1]
def test_id_scope():
 m=json.loads((B/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id");assert hashlib.sha256((json.dumps(u,sort_keys=True,indent=2)+"\n").encode()).hexdigest()==m["bundle_id"]==B.name;assert m["formal_scope"]=="mysql_only" and m["postgresql_mutation_prohibited"] is True
def test_transaction_is_same_container_same_datadir_and_zero_swap():
 s=(B/"control/mysql_tpcc_zero_swap_recommission_v1.py").read_text();assert "same_container_ids" in s and "same_datadir_mount" in s and 'a["total_swap_bytes"]!=0' in s and "cgroup was not recommissioned" in s and "postgresql_mutation_performed\":False" in s
def test_runbook_stops_client_then_graceful_db_and_never_pg():
 r=json.loads((B/"runbook/transaction.json").read_text());assert r["ordered_mutations"][0]["role"]=="client" and r["ordered_mutations"][1]["role"]=="database" and r["ordered_mutations"][1]["graceful_timeout_seconds"]==120 and r["postgresql_mutation_prohibited"] is True
'''
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)
def main():
 runbook={"schema_version":"mysql_tpcc_zero_swap_recommission_transaction_v1","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"preconditions":["all TPC-C qualification units terminal and no DBWorkload PID","TPC-H private socket container set stable","seal read-only before snapshot","database and client container IDs/mount projections equal frozen values"],"ordered_mutations":[{"step":1,"role":"client","command":["/usr/bin/docker","--host","unix:///run/ra-docker-mysql-tpcc-v1.sock","stop","--time","30","ra_mysql_tpcc_v2_client_tpcc-v16-7e2e13aa-20260801t121500p08"]},{"step":2,"role":"database","graceful_timeout_seconds":120,"command":["/usr/bin/docker","--host","unix:///run/ra-docker-mysql-tpcc-v1.sock","stop","--time","120","ra_mysql_tpcc_v2_db_tpcc-v16-7e2e13aa-20260801t121500p08"]},{"step":3,"role":"database","command":["/usr/bin/docker","--host","unix:///run/ra-docker-mysql-tpcc-v1.sock","start","ra_mysql_tpcc_v2_db_tpcc-v16-7e2e13aa-20260801t121500p08"]},{"step":4,"role":"client","command":["/usr/bin/docker","--host","unix:///run/ra-docker-mysql-tpcc-v1.sock","start","ra_mysql_tpcc_v2_client_tpcc-v16-7e2e13aa-20260801t121500p08"]}],"postconditions":["same container IDs, image IDs, Created values and mount hashes","same /var/lib/mysql datadir source on 259:2","both PID/start/cgroup identities changed","both total_swap exactly zero","MySQL health/adopt v2 checks pass","new adopted_service.zero_swap_restart_v1.json sealed","fresh attempt only; prior attempts zero credit"],"rollback_boundary":"If either stop succeeds, do not restore old authority. Complete same-container restart and fresh adoption or remain blocked; never delete/replace datadir.","accepted_rounds_credit":0};c={"control/mysql_tpcc_zero_swap_recommission_v1.py":SCRIPT.encode(),"tests/test_mysql_tpcc_zero_swap_recommission_runbook_v1.py":TEST.encode(),"runbook/transaction.json":can(runbook),"builder/build_mysql_tpcc_zero_swap_recommission_runbook_v1.py":HERE.read_bytes()};files={r:hs(v) for r,v in sorted(c.items())};u={"schema_version":"mysql_tpcc_zero_swap_recommission_runbook_bundle_v1","status":"SEALED_NOT_EXECUTED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"database_container_restart_authorized_by_user":True,"client_container_restart_authorized_by_user":True,"datadir_replacement_prohibited":True,"accepted_round_authorized":False,"accepted_rounds_credit":0,"files":files};bid=hs(can(u));m=dict(u,bundle_id=bid);OUT.mkdir(parents=True,exist_ok=True);d=OUT/bid
 if d.exists():print(bid);print(hs((d/"bundle_manifest.json").read_bytes()));print(d);return
 st=Path(tempfile.mkdtemp(prefix=".recommission-",dir=OUT))
 try:
  for r,v in c.items():p=st/r;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
  raw=can(m);(st/"bundle_manifest.json").write_bytes(raw);(st/"bundle_manifest.json.sha256").write_text(hs(raw)+"  bundle_manifest.json\n");freeze(st);os.rename(st,d)
 finally:
  if st.exists():shutil.rmtree(st)
 print(bid);print(hs((d/"bundle_manifest.json").read_bytes()));print(d)
if __name__=="__main__":main()

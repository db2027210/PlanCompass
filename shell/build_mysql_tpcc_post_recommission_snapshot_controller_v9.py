#!/usr/bin/env python3
"""Build TPC-C v9 with a post-recommission same-live snapshot authority."""
from __future__ import annotations
import hashlib,json,os,shutil,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve();PARENT_ID="5876f07d1e32e33e1ee17142c6b85a47d8d729698de0426009731754357bc413";PARENT_SHA="5a2097e075408f1a4b1c2c3af0d3f108d0ee8eaa4bc44a77bf1a8d8dcc971cfc";PARENT=ROOT/"artifacts/mysql_tpcc_post_recommission_controller_v8"/PARENT_ID;OUT=ROOT/"artifacts/mysql_tpcc_post_recommission_snapshot_controller_v9";CTL="controller/mysql_tpcc_v16_independent_parallel_one_click_v1.py";SNAP="control/mysql_tpcc_v16_post_recommission_snapshot_v2.py"
ADOPTED_SHA="8f73143b1ded2d344e7b6a577d0c438ab8705eec64a76605f214b99b93d8246e";FRESH_SHA="0c4af692177b9857ecb95b219fcbb62d0e5db5b4ce3451fc8b2fbac798508b01";REAPPLY_SHA="10192d1effad48dc9d4b06b257f9a33669a291d054273bdbf84d410a9610f1c3"
def can(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def hs(v):return hashlib.sha256(v).hexdigest()
def sha(p):return hs(Path(p).read_bytes())
def verify():
 raw=(PARENT/"bundle_manifest.json").read_bytes();m=json.loads(raw);u=dict(m);bid=u.pop("bundle_id",None)
 if sha(PARENT/"bundle_manifest.json")!=PARENT_SHA or bid!=PARENT_ID or hs(can(u))!=PARENT_ID:raise RuntimeError("parent identity differs")
 for r,w in m["files"].items():
  if sha(PARENT/r)!=w:raise RuntimeError("parent closure differs: "+r)
 return m
def freeze(root):
 for p in sorted(root.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(root,0o555)

SNAPSHOT=r'''#!/usr/bin/env python3
"""Read-only post-recommission TPC-C v16 snapshot authority."""
from __future__ import annotations
import argparse,hashlib,json,os,re,secrets,shutil,socket,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
BASE_ID="7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77";BASE_SHA="e9d5e2213a14d2289c07063c0b24dfb68d6059f068c5c91388862f8a873b4cba";ATTEMPT="tpcc-v16-7e2e13aa-20260801t121500p08";ADOPTED_SHA="8f73143b1ded2d344e7b6a577d0c438ab8705eec64a76605f214b99b93d8246e";FRESH_SHA="0c4af692177b9857ecb95b219fcbb62d0e5db5b4ce3451fc8b2fbac798508b01";REAPPLY_SHA="10192d1effad48dc9d4b06b257f9a33669a291d054273bdbf84d410a9610f1c3";DB_ID="5cdcc870ef04435bad3b44eb89135fd00b5d4825179887e2a51862712f1e3db6";CLIENT_ID="445c4e1732fe6cf8cb07e8f32159aac5cf0aa10bcd1fd51a9db3e5a725a0a1e6";DB_PID=20831;CLIENT_PID=21060;DB_START=262525334;CLIENT_START=262526895;DB_CGROUP_SHA="a62bc7b27379d559ccfe49a6fd1af239814d5748416e60a5e9cf4e982b37c5cd";CLIENT_CGROUP_SHA="75a81987dfeef61643388a3b47dbfb230d2848893a61f70df275516e71061cf6";DB_CPUS="21-24,57-60";CLIENT_CPUS="8-15,44-51";DB_MEMS="1";CLIENT_MEMS="0";DB_MEMORY=34359738368;CLIENT_MEMORY=8589934592;MM="259:2";BPS=1205862400;PORT=65106;SOCKET="unix:///run/ra-docker-mysql-tpcc-v1.sock";HEX=re.compile(r"^[0-9a-f]{64}$")
class E(RuntimeError):pass
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def digest(v):return hashlib.sha256(v).hexdigest()
def sha(p):return digest(Path(p).read_bytes())
def regular(p,label):
 p=Path(p)
 if p.is_symlink() or not p.is_file():raise E(label+" regular file required")
 return p.resolve(strict=True)
def sealed(p,label):
 p=regular(p,label);s=regular(Path(str(p)+".sha256"),label+" sidecar")
 if s.read_text(encoding="ascii")!=sha(p)+"  "+p.name+"\n":raise E(label+" sidecar differs")
 try:v=json.loads(p.read_text())
 except json.JSONDecodeError as x:raise E(label+" JSON differs") from x
 if not isinstance(v,dict):raise E(label+" object required")
 return p,v
def bundle(root):
 root=Path(root).resolve(strict=True);p,m=sealed(root/"bundle_manifest.json","base manifest");u=dict(m);u.pop("bundle_id",None)
 if root.name!=BASE_ID or m.get("bundle_id")!=BASE_ID or sha(p)!=BASE_SHA or digest(canonical(u))!=BASE_ID or m.get("formal_scope")!="mysql_only" or m.get("postgresql_mutation_prohibited") is not True:raise E("base bundle differs")
 for r,w in (m.get("files") or {}).items():
  q=root/r
  if q.is_symlink() or not q.is_file() or sha(q)!=w:raise E("base closure differs: "+r)
def cpus(raw):
 out=set()
 for token in str(raw).split(","):
  if "-" in token:a,b=map(int,token.split("-",1));out.update(range(a,b+1))
  elif token:out.add(int(token))
 return out
def docker(cid):
 r=subprocess.run(["/usr/bin/docker","--host",SOCKET,"inspect",cid],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=20)
 try:v=json.loads(r.stdout)
 except json.JSONDecodeError as x:raise E("Docker inspect differs") from x
 if r.returncode or not isinstance(v,list) or len(v)!=1 or not isinstance(v[0],dict):raise E("Docker inspect cardinality differs")
 return v[0]
def proc(pid):
 d=Path("/proc")/str(pid);statraw=(d/"stat").read_text();start=int(statraw[statraw.rfind(")")+2:].split()[19]);cg=(d/"cgroup").read_text();paths=[]
 for line in cg.splitlines():
  fields=line.split(":",2)
  if len(fields)!=3:raise E("process cgroup row differs")
  paths.append(fields[2])
 if not paths or any(path!="/docker/"+(DB_ID if pid==DB_PID else CLIENT_ID) for path in paths):raise E("process left exact container cgroups")
 memory=Path("/sys/fs/cgroup/memory")/paths[0].lstrip("/");stats={}
 for line in (memory/"memory.stat").read_text().splitlines():
  f=line.split()
  if len(f)==2:stats[f[0]]=int(f[1])
 return {"pid":pid,"process_start_ticks":start,"cgroup_sha256":digest(cg.encode()),"memory_cgroup_path":paths[0],"total_swap_bytes":stats.get("total_swap"),"memory_limit_bytes":int((memory/"memory.limit_in_bytes").read_text()),"memory_swap_limit_bytes":int((memory/"memory.memsw.limit_in_bytes").read_text())}
def project(row,authority,role):
 db=role=="database";pid=DB_PID if db else CLIENT_PID;start=DB_START if db else CLIENT_START;cgsha=DB_CGROUP_SHA if db else CLIENT_CGROUP_SHA;want_cpus=DB_CPUS if db else CLIENT_CPUS;want_mems=DB_MEMS if db else CLIENT_MEMS;want_memory=DB_MEMORY if db else CLIENT_MEMORY;state=row.get("State") or {};host=row.get("HostConfig") or {};actual=proc(pid)
 if row.get("Id")!=authority.get("container_id") or str(row.get("Name") or "").lstrip("/")!=authority.get("name") or row.get("Image")!=authority.get("image_id") or row.get("Created")!=authority.get("created") or state.get("Running") is not True or int(state.get("Pid") or 0)!=pid or authority.get("pid")!=pid or authority.get("process_start_ticks")!=start or actual["process_start_ticks"]!=start or actual["cgroup_sha256"]!=cgsha or host.get("CpusetCpus")!=want_cpus or host.get("CpusetMems")!=want_mems or int(host.get("Memory") or 0)!=want_memory or int(host.get("MemorySwap") or 0)!=want_memory or actual["total_swap_bytes"]!=0 or actual["memory_limit_bytes"]!=want_memory or actual["memory_swap_limit_bytes"]!=want_memory or int(row.get("RestartCount") or 0)!=int(authority.get("restart_count") or 0):raise E(role+" exact post-recommission epoch differs")
 return {"container_id":row["Id"],"pid":pid,"process_start_ticks":start,"started_at":state.get("StartedAt"),"cpuset_cpus":want_cpus,"cgroup_sha256":cgsha,"inspect_sha256":digest(canonical(row)),"swap_capacity_bytes":0,"total_swap_bytes":0,"memory_limit_bytes":want_memory,"memory_swap_limit_bytes":want_memory}
def validate_ledger(value,db,epoch):
 phase=((value.get("phases") or {}).get("qualification") or {});old=int((epoch.get("database") or {}).get("old_pid") or 0)
 if value.get("schema_version")!="mysql_tpcc_same_live_container_formal10_handoff_v2" or value.get("status")!="INCOMPLETE" or value.get("bundle_id")!=BASE_ID or value.get("attempt_id")!=ATTEMPT or value.get("accepted_round_authorized") is not False or phase.get("status")!="PASS" or value.get("db_container_id")!=db.get("container_id") or phase.get("db_container_id")!=db.get("container_id") or value.get("db_datadir")!=db.get("datadir") or phase.get("db_datadir")!=db.get("datadir") or int(value.get("db_pid") or 0)!=old or int(phase.get("db_pid") or 0)!=old:raise E("pre-recommission qualification ledger continuity differs")
 return phase
def rate(path):
 found=[]
 for line in regular(path,"blkio live throttle").read_text().splitlines():
  f=line.split()
  if len(f)==2 and f[0]==MM:found.append(int(f[1]))
 if found!=[BPS]:raise E("live blkio target rate differs")
 return BPS
def epoch(service_path,service):
 if sha(service_path)!=ADOPTED_SHA:raise E("adopted exact SHA differs")
 db=service.get("database") or {};client=service.get("client") or {};e=service.get("pre_authority_zero_swap_recommission") or {};root=Path(str(db.get("datadir") or "")).parent.parent;fresh=root/"blkio_throttle_authority.zero_swap_restart_v1.json";reapply=root/"blkio_throttle_reapply.zero_swap_restart_v1.json";fp,fv=sealed(fresh,"fresh blkio authority");rp,rv=sealed(reapply,"reapply receipt")
 if sha(fp)!=FRESH_SHA or sha(rp)!=REAPPLY_SHA or e.get("fresh_blkio_authority_sha256")!=FRESH_SHA or e.get("blkio_reapply_receipt_sha256")!=REAPPLY_SHA or Path(str(e.get("fresh_blkio_authority_path") or ""))!=fresh or Path(str(e.get("blkio_reapply_receipt_path") or ""))!=reapply or (e.get("database") or {}).get("new_pid")!=DB_PID or (e.get("client") or {}).get("new_pid")!=CLIENT_PID or (e.get("database") or {}).get("total_swap_bytes")!=0 or (e.get("client") or {}).get("total_swap_bytes")!=0 or e.get("same_container_ids") is not True or e.get("same_datadir_mount") is not True or e.get("postgresql_mutation_performed") is not False:raise E("sealed recommission epoch differs")
 if fv.get("database_container_id")!=DB_ID or fv.get("database_pid")!=DB_PID or fv.get("device_major_minor")!=MM or fv.get("read_bps")!=BPS or fv.get("write_bps")!=BPS or rv.get("database_container_id")!=DB_ID or rv.get("database_pid")!=DB_PID or rv.get("client_container_id")!=CLIENT_ID or rv.get("client_pid")!=CLIENT_PID or rv.get("postgresql_mutation_performed") is not False or rv.get("workload_started") is not False:raise E("recommission blkio identities differ")
 cgroup=Path("/sys/fs/cgroup/blkio/docker")/DB_ID
 if rate(cgroup/"blkio.throttle.read_bps_device")!=BPS or rate(cgroup/"blkio.throttle.write_bps_device")!=BPS:raise E("live blkio readback differs")
 return e
def publish(root,value):
 identity=digest(canonical(value));root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True);target=root/identity
 if target.exists():p,v=sealed(target/"same_live_snapshot.json","existing snapshot");
 else:
  stage=root/(".stage-"+identity+"-"+secrets.token_hex(5));stage.mkdir(mode=0o700);p=stage/"same_live_snapshot.json";p.write_bytes(canonical(value));os.chmod(p,0o444);s=Path(str(p)+".sha256");s.write_text(sha(p)+"  "+p.name+"\n");os.chmod(s,0o444);os.rename(stage,target);p=target/p.name
 return {"status":value["status"],"authority":str(p),"authority_id":identity,"accepted_rounds_credit":0}
def snapshot(a):
 bundle(a.bundle);sp,service=sealed(a.adopted_service,"adopted service");lp,ledger=sealed(a.phase_ledger,"phase ledger")
 if service.get("schema_version")!="mysql_tpcc_node04_adopted_service_authority_v2" or service.get("status")!="PASS" or service.get("bundle_id")!=BASE_ID or service.get("attempt_id")!=ATTEMPT or service.get("formal_scope")!="mysql_only" or service.get("cross_engine_comparison_prohibited") is not True or service.get("formal_launch_authorized") is not False or service.get("accepted_round_authorized") is not False:raise E("adopted service scope differs")
 db=service.get("database") or {};client=service.get("client") or {};ep=epoch(sp,service);qualification=validate_ledger(ledger,db,ep);dbrow=docker(DB_ID);clrow=docker(CLIENT_ID);database=project(dbrow,db,"database");cl=project(clrow,client,"client")
 mounts=[m for m in dbrow.get("Mounts") or [] if m.get("Destination")=="/var/lib/mysql"]
 if len(mounts)!=1 or mounts[0].get("Source")!=db.get("datadir") or mounts[0].get("RW") is not True or (dbrow.get("HostConfig") or {}).get("NetworkMode")!="host" or "--port=65106" not in ((dbrow.get("Config") or {}).get("Cmd") or []):raise E("database immutable datadir/network differs")
 source=Path(db["datadir"]).resolve(strict=True);mm=str(os.major(source.stat().st_dev))+":"+str(os.minor(source.stat().st_dev))
 if mm!=MM:raise E("database datadir device differs")
 probe=socket.socket();probe.settimeout(1)
 try:reachable=probe.connect_ex(("127.0.0.1",PORT))
 finally:probe.close()
 if reachable:raise E("MySQL port is not reachable")
 value={"schema_version":"mysql_tpcc_v16_same_live_readback_v1","status":"PASS_READ_ONLY_LIVE_IDENTITY_NOT_CAPTURE_AUTHORITY","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"hostname":socket.gethostname(),"collected_at_utc":datetime.now(timezone.utc).isoformat(),"attempt_id":ATTEMPT,"bundle_id":BASE_ID,"database":dict(database,datadir=str(source),device="/dev/nvme3n1",device_major_minor=MM,mysql_port=PORT),"client":cl,"qualification_observed_epoch_ms":qualification["observed_epoch_ms"],"post_recommission_epoch":{"adopted_service_sha256":ADOPTED_SHA,"fresh_blkio_authority_sha256":FRESH_SHA,"reapply_receipt_sha256":REAPPLY_SHA,"pre_recommission_qualification_database_pid":ep["database"]["old_pid"],"database_pid":DB_PID,"client_pid":CLIENT_PID,"subsequent_pid_change_prohibited":True,"zero_swap_verified":True,"live_blkio_verified":True},"inputs":{"adopted_service_sha256":sha(sp),"phase_ledger_sha256":sha(lp)},"capture_authorized":False,"formal_r0_authorized":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0}
 return publish(a.output_root,value)
def main():
 p=argparse.ArgumentParser();sp=p.add_subparsers(dest="mode",required=True);s=sp.add_parser("snapshot");s.add_argument("--bundle",type=Path,required=True);s.add_argument("--adopted-service",type=Path,required=True);s.add_argument("--phase-ledger",type=Path,required=True);s.add_argument("--output-root",type=Path,required=True);a=p.parse_args();print(json.dumps(snapshot(a),sort_keys=True));return 0
if __name__=="__main__":
 try:raise SystemExit(main())
 except (E,OSError,UnicodeError,ValueError,KeyError,subprocess.SubprocessError) as x:print("NOT_READY: "+str(x),file=sys.stderr);raise SystemExit(2)
'''

REAL_FIXTURE={"schema_version":"mysql_tpcc_post_recommission_real_fixture_v1","captured_read_only_from":"node04","captured_at_local":"2026-08-03T23:45:00+08:00","adopted_service_sha256":ADOPTED_SHA,"fresh_blkio_authority_sha256":FRESH_SHA,"reapply_receipt_sha256":REAPPLY_SHA,"database":{"container_id":"5cdcc870ef04435bad3b44eb89135fd00b5d4825179887e2a51862712f1e3db6","pid":20831,"process_start_ticks":262525334,"cgroup_sha256":"a62bc7b27379d559ccfe49a6fd1af239814d5748416e60a5e9cf4e982b37c5cd","image_id":"sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172","created":"2026-08-01T04:21:27.903456191Z","started_at":"2026-08-03T14:44:03.962974411Z","datadir":"/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/tpcc-v16-7e2e13aa-20260801t121500p08/datadir/datadir","cpuset_cpus":"21-24,57-60","cpuset_mems":"1","memory_bytes":34359738368,"memory_swap_bytes":34359738368,"total_swap_bytes":0},"client":{"container_id":"445c4e1732fe6cf8cb07e8f32159aac5cf0aa10bcd1fd51a9db3e5a725a0a1e6","pid":21060,"process_start_ticks":262526895,"cgroup_sha256":"75a81987dfeef61643388a3b47dbfb230d2848893a61f70df275516e71061cf6","image_id":"sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172","created":"2026-08-01T04:21:28.268652157Z","started_at":"2026-08-03T14:44:19.57489951Z","cpuset_cpus":"8-15,44-51","cpuset_mems":"0","memory_bytes":8589934592,"memory_swap_bytes":8589934592,"total_swap_bytes":0},"pre_recommission":{"database_pid":96504,"client_pid":96615},"device_major_minor":"259:2","read_bps":1205862400,"write_bps":1205862400,"postgresql_mutation_performed":False}

TEST=r'''import hashlib,importlib.util,json
from pathlib import Path
B=Path(__file__).resolve().parents[1];C=B/"controller/mysql_tpcc_v16_independent_parallel_one_click_v1.py";S=B/"control/mysql_tpcc_v16_post_recommission_snapshot_v2.py";F=B/"fixtures/node04_post_recommission_epoch_projection_v1.json"
def test_content_address_scope():
 m=json.loads((B/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id");assert hashlib.sha256((json.dumps(u,sort_keys=True,indent=2)+"\n").encode()).hexdigest()==m["bundle_id"]==B.name;assert m["formal_scope"]=="mysql_only" and m["postgresql_mutation_prohibited"] is True and m["accepted_rounds_credit"]==0
def test_real_fixture_exact_epoch_regression():
 f=json.loads(F.read_text());assert f["adopted_service_sha256"]=="8f73143b1ded2d344e7b6a577d0c438ab8705eec64a76605f214b99b93d8246e" and f["database"]["pid"]==20831 and f["client"]["pid"]==21060 and f["database"]["process_start_ticks"]==262525334 and f["client"]["process_start_ticks"]==262526895
 assert f["database"]["total_swap_bytes"]==f["client"]["total_swap_bytes"]==0 and f["database"]["cgroup_sha256"]=="a62bc7b27379d559ccfe49a6fd1af239814d5748416e60a5e9cf4e982b37c5cd" and f["client"]["cgroup_sha256"]=="75a81987dfeef61643388a3b47dbfb230d2848893a61f70df275516e71061cf6"
def test_snapshot_accepts_only_frozen_new_epoch_and_rejects_next_restart():
 s=S.read_text();assert 'DB_PID=20831;CLIENT_PID=21060;DB_START=262525334;CLIENT_START=262526895' in s and 'actual["process_start_ticks"]!=start' in s and 'actual["cgroup_sha256"]!=cgsha' in s and 'row.get("Image")!=authority.get("image_id")' in s and 'row.get("Created")!=authority.get("created")' in s
 assert 'actual["total_swap_bytes"]!=0' in s and 'mounts[0].get("Source")!=db.get("datadir")' in s and 'rate(cgroup/"blkio.throttle.read_bps_device")' in s and 'sha(service_path)!=ADOPTED_SHA' in s
def test_old_qualification_is_explicit_pre_recommission_not_current_pid():
 s=S.read_text();assert 'pre-recommission qualification ledger continuity differs' in s and 'int(value.get("db_pid") or 0)!=old' in s and '"pre_recommission_qualification_database_pid":ep["database"]["old_pid"]' in s
def test_controller_uses_local_v9_snapshot_and_v8_gates_remain():
 s=C.read_text();assert 'POST_RECOMMISSION_SNAPSHOT_REL="control/mysql_tpcc_v16_post_recommission_snapshot_v2.py"' in s and 'str(root/POST_RECOMMISSION_SNAPSHOT_REL)' in s and 'resolve(*A336' not in s
 for required in ('mysql_tpcc_v16_fresh_blkio_live_sealer_v2.py','mysql_only_pg_artifact_http_dynamic_noninterference_v1','zero_swap_readback(','inv.get("foreign_writer_count")!=0','accepted_rounds_credit":0'):
  assert required in s
def test_typed_l2_l3_qps_manifest_contract():
 m=json.loads((B/"bundle_manifest.json").read_text());assert m["typed_binding_count"]==34 and m["transaction_types"]==["Delivery","NewOrder","OrderStatus","Payment","StockLevel"] and m["typed_l2_l3_retained"] is True and m["qps_resource_input_prohibited"] is True
'''

def main():
 pm=verify();contents={}
 for r,w in sorted(pm["files"].items()):
  if not r.startswith("tests/") and not r.startswith("builder/"):contents[r]=(PARENT/r).read_bytes()
 ctl=contents[CTL].decode();patches=((('A336=("a336aef82ec2a8098840ff10c6046a18eca73480c5905a96373d265caaa5f723","a7e7717b9e7ddec7a6f11a4e6c97b68116d60d17e767aeb469ed6fc46219e6e0")'),'POST_RECOMMISSION_SNAPSHOT_REL="control/mysql_tpcc_v16_post_recommission_snapshot_v2.py"'),('a336,_,_=resolve(*A336,"a336");tpcc,tpcc_mp,tpcc_m=resolve','tpcc,tpcc_mp,tpcc_m=resolve'),('str(a336/"control/mysql_tpcc_v16_same_live_gate_control_v1.py")','str(root/POST_RECOMMISSION_SNAPSHOT_REL)'))
 for old,new in patches:
  if ctl.count(old)!=1:raise RuntimeError("controller patch differs: "+old[:80])
  ctl=ctl.replace(old,new)
 contents[CTL]=ctl.encode();contents[SNAP]=SNAPSHOT.encode();contents["fixtures/node04_post_recommission_epoch_projection_v1.json"]=can(REAL_FIXTURE);contents["tests/test_mysql_tpcc_post_recommission_snapshot_controller_v9.py"]=TEST.encode();contents["builder/build_mysql_tpcc_post_recommission_snapshot_controller_v9.py"]=HERE.read_bytes();files={r:hs(v) for r,v in sorted(contents.items())}
 unsigned={"schema_version":"mysql_tpcc_post_recommission_snapshot_controller_bundle_v9","status":"SEALED_NOT_EXECUTED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"parent_bundle_id":PARENT_ID,"parent_manifest_sha256":PARENT_SHA,"adopted_service_sha256":ADOPTED_SHA,"fresh_blkio_authority_sha256":FRESH_SHA,"blkio_reapply_receipt_sha256":REAPPLY_SHA,"post_recommission_database_pid":20831,"post_recommission_client_pid":21060,"post_recommission_database_start_ticks":262525334,"post_recommission_client_start_ticks":262526895,"same_container_image_created_datadir_required":True,"zero_swap_required":True,"subsequent_pid_or_start_change_rejected":True,"real_node04_fixture_included":True,"typed_binding_count":34,"transaction_types":["Delivery","NewOrder","OrderStatus","Payment","StockLevel"],"typed_l2_l3_retained":True,"qps_resource_input_prohibited":True,"dynamic_noninterference_gate_preserved":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions_performed":0,"files":files};bid=hs(can(unsigned));manifest=dict(unsigned,bundle_id=bid);raw=can(manifest);OUT.mkdir(parents=True,exist_ok=True);dest=OUT/bid
 if dest.exists():
  if (dest/"bundle_manifest.json").read_bytes()!=raw:raise RuntimeError("existing manifest differs")
  for r,v in contents.items():
   if (dest/r).read_bytes()!=v:raise RuntimeError("existing closure differs: "+r)
 else:
  stage=Path(tempfile.mkdtemp(prefix=".tpcc-v9-",dir=OUT))
  try:
   for r,v in contents.items():p=stage/r;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
   (stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(hs(raw)+"  bundle_manifest.json\n");freeze(stage);os.rename(stage,dest)
  finally:
   if stage.exists():shutil.rmtree(stage)
 print(bid);print(sha(dest/"bundle_manifest.json"));print(dest)
if __name__=="__main__":main()

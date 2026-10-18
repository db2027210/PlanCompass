#!/usr/bin/env python3
"""Build a TPC-C-only qualification controller with no TPC-H success dependency."""
from __future__ import annotations
import hashlib,json,os,shutil,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ARGS=ROOT/"artifacts/mysql_tpcc_v16_da455_current_args_successor_v1/8007386a577296be2b9510c7b512399137e2131ad16c1b7f427967062844f888/materializer/mysql_tpcc_v16_current_args_materializer_v1.py"
OUT=ROOT/"artifacts/mysql_tpcc_v16_independent_parallel_one_click_v1"
SEALED_FIXTURE=ROOT/"artifacts/tpcc_remote_evidence_import_20260731/extracted"
REAL_INSPECT=ROOT/"history/mysql_sgd_env_agents_20260723/tpcc/tpcc_envelope_probe_20260722T223216Z"
ACBD_REAL=ROOT/"preflight/tpcc_acbd_real_snapshot"
PREFIX=ARGS.read_text().split("def main(argv: list[str] | None = None) -> int:",1)[0]
PHASE_COLLECTOR_ID="6953a554fc57508d6685e95581935ce7eb7f0969c144265f2d1da034b9c7bf41"
PHASE_COLLECTOR_SHA="f2dba3ad086bf60ba5a930c46410558adde46e472a0c3143424e1a70eb8c025f"
PREFIX=PREFIX.replace('COLLECTOR_ID = "da455246652a82bb13d63f5f1ba6ebeb2e92fd08389e3f06707e5897e12646b6"','COLLECTOR_ID = "'+PHASE_COLLECTOR_ID+'"').replace('COLLECTOR_MANIFEST_SHA = "0f835d084459a2c66729327a9a4b9ed6ec070116e9873631874ac667039b34c4"','COLLECTOR_MANIFEST_SHA = "'+PHASE_COLLECTOR_SHA+'"')

BODY=r'''
import socket,subprocess

TPCH_ROOT=Path("/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729")
TPCC_ROOT=Path("/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729")
CUR=TPCC_ROOT/TPCC_ATTEMPT
PINNED=TPCH_ROOT/"runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89/bin/python3.11"
ADOPTED=CUR/"adopted_service.json";LEDGER=CUR/"phase_ledger_v2.json";TPCC_SOCKET="unix:///run/ra-docker-mysql-tpcc-v1.sock"
A336=("a336aef82ec2a8098840ff10c6046a18eca73480c5905a96373d265caaa5f723","a7e7717b9e7ddec7a6f11a4e6c97b68116d60d17e767aeb469ed6fc46219e6e0")
BLKIO=("a75e23ef75351a03266b38790ec2c6d745ea40e4eab3742dd281b0a55c543c7b","bfdc060610122778896f8debcdbe031395f02e38b8ba012a0b5484a40e782d65")
BLKIO_AUTH_SHA="6822e90cc8e6980b6b0030f02f9a2b53a7b5fc23eeb58f214ea26de5a585661d"
DB_CPUS="21-24,57-60";CLIENT_CPUS="8-15,44-51";TARGET_CPUS=set(range(8,16))|set(range(21,25))|set(range(44,52))|set(range(57,61))
MARKERS=("mysqld","postgres","benchbase","dbworkload")

def verify_self():
 p=Path(__file__).resolve(strict=True);root=p.parents[1];mp,m=load_sealed(root/"bundle_manifest.json","self manifest");u=dict(m);identity=u.pop("bundle_id",None)
 if root.name!=identity or sha_bytes(canonical(u))!=identity or (m.get("files") or {}).get(p.relative_to(root).as_posix())!=sha256(p) or m.get("formal_scope")!="mysql_only" or m.get("cross_engine_comparison_prohibited") is not True or m.get("postgresql_mutation_prohibited") is not True or m.get("accepted_round_authorized") is not False:raise MaterializationError("self identity/scope differs")
 for rel,want in (m.get("files") or {}).items():
  member=(root/rel).resolve(strict=True)
  if root not in member.parents or member.is_symlink() or not member.is_file() or sha256(member)!=want:raise MaterializationError("self closure differs: "+str(rel))
 return root

def resolve(identity,manifest_sha,label):
 matches=[]
 for base in (TPCH_ROOT/"bundles",TPCH_ROOT/"support_bundles",TPCC_ROOT/"bundles",TPCC_ROOT/"support_bundles"):
  p=base/identity
  if p.is_dir() and not p.is_symlink():matches.append(p)
 if len(matches)!=1:raise MaterializationError(label+" publication cardinality="+str(len(matches)))
 return verify_bundle(matches[0],label,identity,manifest_sha)

def parse_cpus(raw):
 out=set()
 for item in str(raw).split(","):
  if "-" in item:
   a,b=map(int,item.split("-",1));out.update(range(a,b+1))
  elif item:out.add(int(item))
 return out

def docker_live(identity,role,want):
 if HEX64.fullmatch(str(identity or "")) is None:raise MaterializationError(role+" container ID differs")
 r=subprocess.run(["/usr/bin/docker","--host",TPCC_SOCKET,"inspect","--format","{{json .}}",identity],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=20)
 try:v=json.loads(r.stdout)
 except json.JSONDecodeError as e:raise MaterializationError(role+" inspect differs") from e
 h=v.get("HostConfig") or {};s=v.get("State") or {};pid=int(s.get("Pid") or 0)
 if r.returncode or v.get("Id")!=identity or s.get("Running") is not True or pid<=1 or h.get("CpusetCpus")!=want:raise MaterializationError(role+" live resource identity differs")
 return pid,v

def arg_value(argv,name):
 try:i=argv.index(name)
 except ValueError as e:raise MaterializationError("argument absent: "+name) from e
 if i+1>=len(argv):raise MaterializationError("argument value absent: "+name)
 return argv[i+1]

def adopted_projection_sha256(container_row):
 return sha_bytes(canonical({"Config":container_row.get("Config"),"HostConfig":container_row.get("HostConfig"),"Mounts":container_row.get("Mounts"),"NetworkSettings":container_row.get("NetworkSettings")}))

def collector_projection_sha256(collector,collector_manifest,container_row):
 rel="qualification/mysql_tpch_client_immutable_identity_v1.py";source=regular(Path(collector)/rel,"collector immutable identity helper")
 if (collector_manifest.get("files") or {}).get(rel)!=sha256(source):raise MaterializationError("collector immutable identity helper closure differs")
 spec=importlib.util.spec_from_file_location("tpcc_collector_immutable_identity_"+sha256(source)[:16],str(source))
 if spec is None or spec.loader is None:raise MaterializationError("collector immutable identity helper cannot be loaded")
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 try:projection=module.client_container_immutable_projection(container_row)
 except (AttributeError,TypeError,ValueError) as e:raise MaterializationError("collector immutable identity projection differs") from e
 return sha_bytes(canonical(projection))

def process_identity(pid):
 d=Path("/proc")/str(pid);raw=(d/"stat").read_text(encoding="ascii");ticks=int(raw[raw.rfind(")")+2:].split()[19]);cgroup=(d/"cgroup").read_text(encoding="utf-8")
 return {"pid":pid,"process_start_ticks":ticks,"cgroup_sha256":sha_bytes(cgroup.encode("utf-8"))}

def identity_field_diff(left,right,fields):
 return {field:{"left":left.get(field),"right":right.get(field)} for field in fields if left.get(field)!=right.get(field)}

def snapshot_bound_client_identity(container_row,pid,adopted_client,snapshot_client,collector,collector_manifest,expected_collector_projection_sha256=None):
 actual_name=str(container_row.get("Name") or "").lstrip("/")
 if container_row.get("Id")!=adopted_client.get("container_id") or actual_name!=adopted_client.get("name") or container_row.get("Image")!=adopted_client.get("image_id") or container_row.get("Created")!=adopted_client.get("created"):raise MaterializationError("TPC-C client direct immutable identity differs")
 proc=process_identity(pid);snapshot=dict(snapshot_client)
 stable_live={"container_id":container_row.get("Id"),"pid":pid,"process_start_ticks":proc["process_start_ticks"],"cpuset_cpus":(container_row.get("HostConfig") or {}).get("CpusetCpus"),"cgroup_sha256":proc["cgroup_sha256"],"started_at":(container_row.get("State") or {}).get("StartedAt")}
 stable_diff=identity_field_diff(snapshot,stable_live,("container_id","pid","process_start_ticks","cpuset_cpus","cgroup_sha256","started_at"))
 if stable_diff:raise MaterializationError("TPC-C client fresh snapshot stable identity differs fields="+",".join(sorted(stable_diff)))
 if adopted_client.get("pid")!=pid or adopted_client.get("process_start_ticks")!=proc["process_start_ticks"] or adopted_client.get("cpuset_cpus")!=(container_row.get("HostConfig") or {}).get("CpusetCpus") or int(container_row.get("RestartCount") or 0)!=int(adopted_client.get("restart_count") or 0):raise MaterializationError("TPC-C client adopted process/resource identity differs")
 new=collector_projection_sha256(collector,collector_manifest,container_row)
 if HEX64.fullmatch(new) is None or expected_collector_projection_sha256 not in (None,new):raise MaterializationError("TPC-C client normalized collector projection identity differs")
 return {"schema_version":"mysql_tpcc_client_fresh_snapshot_projection_bridge_v3","container_id":container_row["Id"],"container_name":actual_name,"image_id":container_row["Image"],"created":container_row["Created"],"pid":pid,"process_start_ticks":proc["process_start_ticks"],"adopted_legacy_projection_sha256":adopted_client.get("inspect_config_sha256"),"fresh_snapshot_identity":snapshot,"collector_projection_sha256":new,"snapshot_full_inspect_sha256_provenance":snapshot.get("inspect_sha256"),"dynamic_state_networksettings_compared_by_collector_projection":True,"legacy_projection_cross_time_comparison_prohibited":True,"same_live_stable_identity_and_cgroup_required":True,"identity_relaxation":False}

def proc_row(pid):
 d=Path("/proc")/str(pid);raw=(d/"stat").read_text();end=raw.rfind(")");ticks=int(raw[end+2:].split()[19]);status={}
 for line in (d/"status").read_text().splitlines():
  if ":" in line:k,v=line.split(":",1);status[k]=v.strip()
 cmd=(d/"cmdline").read_bytes().replace(b"\0",b" ").decode("utf-8","replace")
 cgroup=(d/"cgroup").read_text(errors="replace")
 writable=0
 fdroot=d/"fd"
 for fd in fdroot.iterdir():
  try:
   target=os.readlink(fd);flags=[x for x in (d/"fdinfo"/fd.name).read_text().splitlines() if x.startswith("flags:")]
   if target.startswith("/mnt/mysql_tpcc_nvme3n1/") and flags and (int(flags[0].split()[1],8)&3):writable+=1
  except OSError:pass
 return {"pid":pid,"process_start_ticks":ticks,"cpus_allowed_list":status.get("Cpus_allowed_list"),"mems_allowed_list":status.get("Mems_allowed_list"),"cmdline_sha256":hashlib.sha256(cmd.encode()).hexdigest(),"cmdline":cmd[:512],"cgroup_sha256":hashlib.sha256(cgroup.encode()).hexdigest(),"tpcc_device_writable_fds":writable}

def host_inventory(allowed):
 rows=[];pg=[]
 for d in sorted(Path("/proc").iterdir(),key=lambda p:p.name):
  if not d.name.isdigit():continue
  try:r=proc_row(int(d.name))
  except (OSError,ValueError):continue
  low=r["cmdline"].lower()
  if not any(x in low for x in MARKERS):continue
  is_allowed=r["pid"] in allowed
  if "postgres" in low:pg.append(r["pid"])
  if not is_allowed and (parse_cpus(r["cpus_allowed_list"])&TARGET_CPUS or r["tpcc_device_writable_fds"]):raise MaterializationError("foreign DB/benchmark overlaps TPC-C resources: "+json.dumps(r,sort_keys=True))
  r["allowed_tpcc_service"]=is_allowed;rows.append(r)
 sp=Path("/run/ra-docker-mysql-tpcc-v1.sock").lstat()
 if not stat.S_ISSOCK(sp.st_mode):raise MaterializationError("TPC-C docker socket differs")
 th=Path("/run/ra-docker-mysql-tpch-v1.sock");tpch_socket=None
 if th.exists():
  sh=th.lstat()
  if not stat.S_ISSOCK(sh.st_mode) or (sh.st_dev,sh.st_ino)==(sp.st_dev,sp.st_ino):raise MaterializationError("TPC-H/TPC-C socket isolation differs")
  tpch_socket={"device":sh.st_dev,"inode":sh.st_ino}
 dev=os.stat("/dev/nvme3n1");mm=f"{os.major(dev.st_rdev)}:{os.minor(dev.st_rdev)}"
 if not stat.S_ISBLK(dev.st_mode) or mm!="259:2":raise MaterializationError("TPC-C device identity differs")
 return {"schema_version":"mysql_tpcc_independent_full_host_inventory_v1","inventory_complete":True,"rows":rows,"postgresql_pids":pg,"tpcc_socket":{"device":sp.st_dev,"inode":sp.st_ino},"tpch_socket_if_present":tpch_socket,"device":"/dev/nvme3n1","device_major_minor":mm,"mutation_performed":False}

def foreign_signature(inv):
 return sorted((r["pid"],r["process_start_ticks"],r["cpus_allowed_list"],r["mems_allowed_list"],r["cmdline_sha256"],r["cgroup_sha256"]) for r in inv["rows"] if not r["allowed_tpcc_service"])

def validate_evidence(v):
 if v.get("schema_version")!="mysql_algorithm_c_debug_qualification_evidence_v2" or v.get("status")!="PASS_COMPLETE_8_STREAM_DEBUG_QUALIFICATION_NOT_GATE_AUTHORITY" or v.get("mode")!="qualification_solo" or v.get("direction")!="solo_baseline" or v.get("bundle_id")!=TPCC_ID or v.get("scenario")!="tpcc_cpu" or v.get("collector_sha256")!=COLLECTOR_MANIFEST_SHA or v.get("duration_seconds")!=180 or v.get("debug_only") is not True or v.get("accepted_round_authorized") is not False:raise MaterializationError("TPC-C evidence contract differs")
 samples=v.get("samples")
 if not isinstance(samples,list) or len(samples)<181:raise MaterializationError("TPC-C sample coverage incomplete")
 counts=[]
 for sample in samples:
  inv=((((sample.get("required_streams") or {}).get("process_cpu_affinity_and_device_inventory") or {}).get("rolling_full_host_inventory") or {}))
  if inv.get("inventory_complete") is not True or inv.get("conflicts")!=[] or inv.get("permission_failure_count")!=0 or inv.get("race_count")!=0 or inv.get("foreign_writer_count")!=0:raise MaterializationError("rolling dynamic non-interference gate differs")
  counts.append(len(inv.get("processes") or []))
 return {"sample_count":len(samples),"rolling_inventory_process_counts":counts,"inventory_complete_all_samples":True,"conflicts":[],"permission_failure_count":0,"race_count":0,"foreign_writer_count":0}

def workload_rows(value):
 out=[]
 def visit(v):
  if isinstance(v,dict):
   if v.get("role")=="qualification_workload" and int(v.get("pid") or 0)>1:out.append({k:v.get(k) for k in ("pid","ppid","comm","cmdline_sha256","cpus_allowed_list","mems_allowed_list","container_identity","role")})
   for x in v.values():visit(x)
  elif isinstance(v,list):
   for x in v:visit(x)
 visit(value);return out

def controller_gate(planp,authp,tokenp):
 pp,p=load_sealed(planp,"plan");_,a=load_sealed(authp,"controller authority");t=regular(tokenp,"controller token");token=t.read_text().strip()
 if stat.S_IMODE(t.stat().st_mode)!=0o600 or a.get("plan_sha256")!=sha256(pp) or a.get("token_sha256")!=hashlib.sha256(token.encode()).hexdigest() or not float(a.get("not_before_epoch") or 0)<=time.time()<float(a.get("expires_epoch") or 0):raise MaterializationError("controller authority differs")
 return pp,p

def execute(a):
 root=verify_self();pp,p=controller_gate(a.plan,a.authority,a.token);collector,_,collector_m=verify_bundle(Path(p["collector_bundle"]),"collector",COLLECTOR_ID,COLLECTOR_MANIFEST_SHA);entry=regular(collector/"qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py","collector entry");adopted=load_sealed(Path(p["adopted_service"]),"adopted service")[1];db=adopted["database"];client=adopted["client"]
 dbpid,_=docker_live(db["container_id"],"database",DB_CPUS);cpid,client_row=docker_live(client["container_id"],"client",CLIENT_CPUS);actual_name=str(client_row.get("Name") or "").lstrip("/");transport=p.get("counter_client_identity") or {};q=p["qualification_args"]
 command_path=Path(arg_value(q,"--command-spec"));_,command=load_sealed(command_path,"qualification command");command_client=command.get("client_container") or {}
 expected_bridge=p.get("client_identity_projection_bridge") or {};bridge=snapshot_bound_client_identity(client_row,cpid,client,(expected_bridge.get("fresh_snapshot_identity") or {}),collector,collector_m,expected_bridge.get("collector_projection_sha256"))
 if actual_name!=client.get("name") or transport!={"container_name":actual_name,"container_id":client["container_id"]} or arg_value(q,"--mysql-client-container")!=actual_name or command_client.get("name")!=actual_name or command_client.get("container_id")!=client["container_id"] or command_client.get("inspect_config_sha256")!=bridge["collector_projection_sha256"] or p.get("client_identity_projection_bridge")!=bridge:raise MaterializationError("TPC-C counter client name/ID/dual-projection command binding differs")
 allowed={dbpid,cpid};runroot=Path(p["run_root"]);runroot.mkdir(parents=True,mode=0o700)
 try:
  pre=host_inventory(allowed);write_sealed(runroot/"pre.inventory.json",pre,0o400);evp=runroot/"tpcc_independent.evidence.json"
  with (runroot/"workload.stdout").open("xb") as so,(runroot/"workload.stderr").open("xb") as se:r=subprocess.run([str(PINNED),"-B",str(entry),"--qualification-solo",*q,"--output",str(evp)],stdout=so,stderr=se,timeout=900)
  if r.returncode:raise MaterializationError("TPC-C independent qualification failed rc="+str(r.returncode))
  _,ev=load_sealed(evp,"TPC-C evidence");dynamic=validate_evidence(ev);post=host_inventory(allowed);write_sealed(runroot/"post.inventory.json",post,0o400)
  if foreign_signature(pre)!=foreign_signature(post):raise MaterializationError("co-resident DB/benchmark identity changed across window")
  gate={"schema_version":"mysql_tpcc_independent_dynamic_noninterference_gate_v1","status":"PASS_FULL_HOST_ROLLING_DYNAMIC_NONINTERFERENCE","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"pre_inventory_sha256":sha256(runroot/"pre.inventory.json"),"post_inventory_sha256":sha256(runroot/"post.inventory.json"),"postgresql_pids_pre":pre["postgresql_pids"],"postgresql_pids_post":post["postgresql_pids"],**dynamic,"capture_authorized":True,"formal_r0_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0};gp=runroot/"dynamic_gate.json";write_sealed(gp,gate,0o400)
  rows=workload_rows(ev)
  if not rows:raise MaterializationError("real workload PID evidence absent")
  done={"schema_version":"mysql_tpcc_independent_parallel_completion_v1","status":"PASS_TPCC_INDEPENDENT_QUALIFICATION_ZERO_CREDIT","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"plan_sha256":sha256(pp),"evidence_sha256":sha256(evp),"dynamic_gate_sha256":sha256(gp),"real_workload_pids":rows,"tpcc_workloads_started":1,"tpch_workloads_started":0,"tpch_success_dependency_used":False,"capture_authorized":True,"formal_r0_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0};d=runroot/"completion";d.mkdir(mode=0o700);write_sealed(d/"tpcc_independent.completion.json",done,0o400);return 0
 except BaseException as e:
  d=runroot/"completion";d.mkdir(mode=0o700,exist_ok=True);write_sealed(d/"tpcc_independent.failure.json",{"status":"FAIL_CLOSED_ZERO_CREDIT","error":str(e),"accepted_rounds_credit":0},0o400);raise

def run(a):
 root=verify_self()
 if socket.gethostname().split(".")[0]!="node04" or os.geteuid()!=0 or Path(sys.executable).resolve()!=PINNED.resolve(strict=True):raise MaterializationError("node04 root pinned-Python execution required")
 a336,_,_=resolve(*A336,"a336");blkio,_,_=resolve(*BLKIO,"blkio sealer");tpcc,tpcc_mp,tpcc_m=resolve(TPCC_ID,TPCC_MANIFEST_SHA,"TPC-C v16");collector,collector_mp,collector_m=resolve(COLLECTOR_ID,COLLECTOR_MANIFEST_SHA,"collector")
 adopted_path,adopted=load_sealed(ADOPTED,"adopted service");ledger_path,ledger=load_sealed(LEDGER,"phase ledger")
 if adopted.get("formal_scope")!="mysql_only" or adopted.get("cross_engine_comparison_prohibited") is not True or adopted.get("formal_launch_authorized") is not False or adopted.get("accepted_round_authorized") is not False or ledger.get("status")!="INCOMPLETE" or ((ledger.get("phases") or {}).get("qualification") or {}).get("status")!="PASS":raise MaterializationError("TPC-C adopted/ledger authority differs")
 db=adopted.get("database") or {};client=adopted.get("client") or {}
 if db.get("cpuset_cpus")!=DB_CPUS or client.get("cpuset_cpus")!=CLIENT_CPUS or db.get("device")!="/dev/nvme3n1" or db.get("device_major_minor")!="259:2" or db.get("mysql_port")!=65106:raise MaterializationError("TPC-C resource envelope differs")
 docker_live(db.get("container_id"),"database",DB_CPUS);_,client_row=docker_live(client.get("container_id"),"client",CLIENT_CPUS);actual_client_name=str(client_row.get("Name") or "").lstrip("/");pre_snapshot_collector_sha=collector_projection_sha256(collector,collector_m,client_row)
 measurement=Path((adopted.get("measurement_bridge") or {}).get("host_path","")).resolve(strict=True);fm=subprocess.run(["/usr/bin/findmnt","-rn","-o","FSTYPE","--target",str(measurement)],stdout=subprocess.PIPE,text=True,timeout=10)
 if fm.returncode or fm.stdout.strip()!="tmpfs":raise MaterializationError("measurement output is not tmpfs")
 defaults=regular(CUR/"config/mysql.cnf","TPC-C defaults");xml=regular(CUR/"config/tpcc.xml","TPC-C XML");private=(adopted.get("client_private_config") or {}).get("files") or {}
 if stat.S_IMODE(defaults.stat().st_mode)!=0o600 or sha256(defaults)!=((private.get("mysql.cnf") or {}).get("sha256")) or sha256(xml)!=((private.get("tpcc.xml") or {}).get("sha256")):raise MaterializationError("TPC-C private config differs")
 tag="independent-"+time.strftime("%Y%m%dT%H%M%SZ",time.gmtime())+"-"+secrets.token_hex(8);control=measurement/(tag+"-control");control.mkdir(mode=0o700);snapshot_root=measurement/(tag+"-snapshot")
 snaprun=subprocess.run([str(PINNED),"-B",str(a336/"control/mysql_tpcc_v16_same_live_gate_control_v1.py"),"snapshot","--bundle",str(tpcc),"--adopted-service",str(adopted_path),"--phase-ledger",str(ledger_path),"--output-root",str(snapshot_root)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=120)
 if snaprun.returncode:raise MaterializationError("a336 snapshot failed: "+snaprun.stderr[-800:])
 snapout=json.loads(snaprun.stdout);snapshot_path,snapshot=load_sealed(Path(snapout["authority"]),"a336 snapshot");database,snapshot_client=validate_snapshot(snapshot,time.time());service_client=validate_service(adopted,database,snapshot_client)
 if not actual_client_name or actual_client_name!=client.get("name") or service_client.get("name")!=actual_client_name or service_client.get("container_id")!=client.get("container_id") or snapshot_client.get("container_id")!=client.get("container_id"):raise MaterializationError("TPC-C adopted/snapshot/live client name and ID binding differs")
 blkio_receipt=control/"blkio.receipt.json";br=subprocess.run([str(PINNED),"-B",str(blkio/"control/mysql_tpcc_v16_blkio_authority_sidecar_sealer_v1.py"),"--v16-bundle",str(tpcc),"--adopted-service",str(adopted_path),"--authority",str(CUR/"blkio_throttle_authority.json"),"--output",str(blkio_receipt)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=120)
 if br.returncode:raise MaterializationError("blkio readback failed: "+br.stderr[-800:])
 _,bv=load_sealed(blkio_receipt,"blkio receipt")
 if bv.get("status")!="PASS_VALIDATED_LIVE_BLKIO_AUTHORITY_SIDECAR" or bv.get("authority_sha256")!=BLKIO_AUTH_SHA:raise MaterializationError("blkio authority differs")
 cpid,client_row=docker_live(client.get("container_id"),"client",CLIENT_CPUS);bridge=snapshot_bound_client_identity(client_row,cpid,client,snapshot_client,collector,collector_m,pre_snapshot_collector_sha)
 _,runtime=load_bundle_json(tpcc,tpcc_m,"distributed_runtime_contract.json","TPC-C runtime");attempt=TPCC_ATTEMPT+"-qualification_solo-independent-"+secrets.token_hex(12);command=build_tpcc_command(tpcc,tpcc_mp,tpcc_m,runtime,service_client,defaults,measurement,"qualification_solo",attempt,None);command["client_container"]["inspect_config_sha256"]=bridge["collector_projection_sha256"];false_boundary(command,"command");reject_static_pid(command)
 metric=Path(command["workload_metric_stream"]["path"]);producer=Path(str(metric)+".producer-complete-v1.json")
 if metric.exists() or metric.is_symlink() or producer.exists() or producer.is_symlink():raise MaterializationError("fresh metric/completion path required")
 workload_token=control/"workload.token";workload_token.write_text(secrets.token_hex(48)+"\n");os.chmod(workload_token,0o600);command_path=control/"B_solo.command.json";command_sha=write_sealed(command_path,command,0o400);auth=authority(command,command_sha,hashlib.sha256(workload_token.read_text().strip().encode()).hexdigest(),sha256(collector_mp),time.time(),1800);auth_path=control/"B_solo.authority.json";write_sealed(auth_path,auth,0o600)
 q=base_args(tpcc,db["name"],actual_client_name,defaults)+["--main-agent-token-file",str(workload_token),"--main-agent-authority",str(auth_path),"--command-spec",str(command_path)]
 if arg_value(q,"--mysql-client-container")!=command["client_container"]["name"] or command["client_container"]["container_id"]!=client["container_id"]:raise MaterializationError("counter transport name and immutable command ID binding differs")
 runroot=measurement/(tag+"-run");unit="ra-mysql-tpcc-independent-"+secrets.token_hex(8)+".service";plan={"schema_version":"mysql_tpcc_independent_parallel_plan_v1","status":"SEALED_NOT_EXECUTED","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"collector_bundle":str(collector),"adopted_service":str(adopted_path),"qualification_args":q,"counter_client_identity":{"container_name":actual_client_name,"container_id":client["container_id"]},"client_identity_projection_bridge":bridge,"run_root":str(runroot),"unit":unit,"resource_envelope":{"database_cpuset":DB_CPUS,"client_cpuset":CLIENT_CPUS,"device":"/dev/nvme3n1","device_major_minor":"259:2","mysql_port":65106},"dynamic_noninterference_gate_required":True,"tpch_success_dependency_used":False,"capture_authorized":False,"formal_r0_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0};planp=control/"plan.json";write_sealed(planp,plan,0o400)
 controller_token=control/"controller.token";controller_token.write_text(secrets.token_hex(48)+"\n");os.chmod(controller_token,0o600);ca={"plan_sha256":sha256(planp),"token_sha256":hashlib.sha256(controller_token.read_text().strip().encode()).hexdigest(),"not_before_epoch":time.time()-1,"expires_epoch":time.time()+1900};cap=control/"controller.authority.json";write_sealed(cap,ca,0o400)
 sr=subprocess.run(["/usr/bin/systemd-run","--unit",unit,"--service-type=oneshot","--remain-after-exit",str(PINNED),"-B",str(Path(__file__).resolve()),"execute","--plan",str(planp),"--authority",str(cap),"--token",str(controller_token)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=60)
 if sr.returncode:raise MaterializationError("systemd launch failed: "+sr.stderr[-800:])
 completion=runroot/"completion/tpcc_independent.completion.json";failure=runroot/"completion/tpcc_independent.failure.json";deadline=time.time()+1900
 while time.time()<deadline:
  if completion.is_file():
   _,done=load_sealed(completion,"completion");print(json.dumps({**done,"completion_path":str(completion)},sort_keys=True));return 0
  if failure.is_file():raise MaterializationError("independent workload failed: "+failure.read_text()[-800:])
  time.sleep(2)
 raise MaterializationError("independent workload timeout")

def main(argv=None):
 p=argparse.ArgumentParser();sp=p.add_subparsers(dest="mode",required=True);sp.add_parser("run");e=sp.add_parser("execute");e.add_argument("--plan",type=Path,required=True);e.add_argument("--authority",type=Path,required=True);e.add_argument("--token",type=Path,required=True);a=p.parse_args(argv);return run(a) if a.mode=="run" else execute(a)

if __name__=="__main__":
 try:raise SystemExit(main())
 except (MaterializationError,OSError,UnicodeError,ValueError,KeyError,subprocess.SubprocessError) as e:print("NOT_READY: "+str(e),file=sys.stderr);raise SystemExit(2)
'''

TEST=r'''import copy,hashlib,importlib.util,json,time,pytest
from pathlib import Path
B=Path(__file__).resolve().parents[1];ROOT=B.parents[2];S=B/"controller/mysql_tpcc_v16_independent_parallel_one_click_v1.py";q=importlib.util.spec_from_file_location("m",S);M=importlib.util.module_from_spec(q);q.loader.exec_module(M);C=ROOT/"artifacts/mysql_tpcc_phase_prime_support_successor_v1"/M.COLLECTOR_ID
def test_content_address_and_scope():
 m=json.loads((B/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id");assert hashlib.sha256((json.dumps(u,sort_keys=True,indent=2)+"\n").encode()).hexdigest()==m["bundle_id"]==B.name;assert m["formal_scope"]=="mysql_only" and m["cross_engine_comparison_prohibited"] is True and m["postgresql_mutation_prohibited"] is True
def test_no_tpch_success_or_mutex_dependency():
 s=S.read_text();run=s[s.index("def run(a):"):];assert "tpch-retry9-completion" not in s and "tpch-bootstrap-spec" not in run and "fourflow.lock" not in s and '"tpch_success_dependency_used":False' in s
def test_resources_and_dynamic_fail_closed_gate():
 s=S.read_text();assert M.DB_CPUS=="21-24,57-60" and M.CLIENT_CPUS=="8-15,44-51";assert '"259:2"' in s and "65106" in s and 'inv.get("conflicts")!=[]' in s and 'inv.get("permission_failure_count")!=0' in s and 'inv.get("race_count")!=0' in s and 'inv.get("foreign_writer_count")!=0' in s and "postgresql_pids" in s
def test_cpu_sets_are_disjoint_from_historical_tpch_sets():
 tpcc=M.parse_cpus(M.DB_CPUS)|M.parse_cpus(M.CLIENT_CPUS);tpch=M.parse_cpus("18-20,54-56")|M.parse_cpus("0-7,36-43");assert tpcc.isdisjoint(tpch)
def test_exact_collector_transport_requires_name_and_preserves_full_id(tmp_path):
 path=C/"qualification/mysql_shared_tpcc_counter_transport_v1.py";spec=importlib.util.spec_from_file_location("transport",path);T=importlib.util.module_from_spec(spec);spec.loader.exec_module(T)
 name="ra_mysql_tpcc_v2_client_tpcc-v16-7e2e13aa-20260801t121500p08";identity="a"*64
 class Q(RuntimeError):pass
 class Shared:
  QualificationError=Q;DOCKER_CLI=["docker"];DB_COUNTERS=[]
  def __init__(self):self.inspect_args=[]
  def inspect_container(self,value):self.inspect_args.append(value);return {"Id":identity,"Name":"/"+name,"Image":"sha256:image","Created":"now","Mounts":[],"State":{"Pid":42}}
  def _full_container_id(self,row,label):return row["Id"]
  def _container_name(self,row,label):return row["Name"].lstrip("/")
  def _client_container_config_identity(self,row):return "b"*64
  def validate_client_container_readback(self,row,envelope,binding,dest):
   assert binding["container_id"]==identity and binding["container_name"]==name;return {"container_id":identity,"container_short_id":identity[:12],"pid":42,"process_start_ticks":99,"inspect_config_sha256":"b"*64}
  def run(self,*args,**kwargs):return "mysql  Ver 8.0"
  def process_start_ticks(self,pid):return 99
 shared=Shared();T.install(shared);runtime={"database_counter_transport":T.TRANSPORT_CONTRACT,"client_runtime_mounts":{"python":{"source":"/python"}},"client_resource_envelope":{}}
 got=shared.prepare_database_counter_transport(runtime,tmp_path,"ra_mysql_tpcc_v2_db_attempt",{"Mounts":[]},tmp_path/"config/mysql.cnf",name,"/usr/bin/mysql")
 assert got["client_container_name"]==name and got["client_container_id"]==identity and shared.inspect_args[-1]==name
 try:shared.prepare_database_counter_transport(runtime,tmp_path,"ra_mysql_tpcc_v2_db_attempt",{"Mounts":[]},tmp_path/"config/mysql.cnf",identity,"/usr/bin/mysql")
 except Q as e:assert str(e)=="TPC-C counter client name differs"
 else:raise AssertionError("full ID was incorrectly accepted as transport name")
def test_successor_binds_transport_name_to_command_full_id():
 s=S.read_text();assert 'base_args(tpcc,db["name"],actual_client_name,defaults)' in s and 'command["client_container"]["container_id"]!=client["container_id"]' in s and 'counter_client_identity' in s
def test_snapshot_bound_projection_bridge_is_exact_and_fail_closed(monkeypatch):
 collector,_,manifest=M.verify_bundle(C,"fixture collector",M.COLLECTOR_ID,M.COLLECTOR_MANIFEST_SHA)
 row={"Id":"a"*64,"Name":"/ra_mysql_tpcc_v2_client_tpcc-v16-7e2e13aa-20260801t121500p08","Image":"sha256:"+"b"*64,"Created":"2026-08-01T04:15:00.000000000Z","Config":{"Image":"benchbase:tpcc-v2","Entrypoint":["/entrypoint.sh"],"Cmd":["sleep","infinity"],"User":"1000:1000","Labels":{"com.example.role":"tpcc-client"},"Env":["TZ=Asia/Shanghai"]},"HostConfig":{"CpuPeriod":100000,"CpuQuota":800000,"CpusetCpus":"8-15,44-51","CpusetMems":"0","Memory":17179869184,"MemorySwap":17179869184,"PidsLimit":4096,"ReadonlyRootfs":False,"NetworkMode":"none","SecurityOpt":["no-new-privileges"],"CapDrop":["ALL"],"LogConfig":{"Type":"json-file","Config":{}},"RestartPolicy":{"Name":"no","MaximumRetryCount":0}},"Mounts":[{"Type":"bind","Source":"/mnt/mysql_tpcc_nvme3n1/anonymous/tpcc","Destination":"/work","RW":True}],"NetworkSettings":{"Networks":{},"Ports":{}},"State":{"Running":True,"Pid":4242,"ExecIDs":["transient"]}}
 adopted={"container_id":row["Id"],"name":row["Name"].lstrip("/"),"image_id":row["Image"],"created":row["Created"],"inspect_config_sha256":"c"*64,"pid":4242,"process_start_ticks":99,"cpuset_cpus":"8-15,44-51"};monkeypatch.setattr(M,"process_identity",lambda pid:{"pid":pid,"process_start_ticks":99,"cgroup_sha256":"d"*64})
 snapshot={"container_id":row["Id"],"pid":4242,"process_start_ticks":99,"cpuset_cpus":"8-15,44-51","cgroup_sha256":"d"*64,"inspect_sha256":M.sha_bytes(M.canonical(row))}
 bridge=M.snapshot_bound_client_identity(row,4242,adopted,snapshot,collector,manifest);assert bridge["adopted_legacy_projection_sha256"]==adopted["inspect_config_sha256"] and bridge["collector_projection_sha256"]!=adopted["inspect_config_sha256"] and bridge["identity_relaxation"] is False
 transient=copy.deepcopy(row);transient["State"]["ExecIDs"]=["changed"];assert M.snapshot_bound_client_identity(transient,4242,adopted,snapshot,collector,manifest,bridge["collector_projection_sha256"])["collector_projection_sha256"]==bridge["collector_projection_sha256"]
 changed=copy.deepcopy(row);changed["Config"]["Cmd"]=["sleep","1"]
 with pytest.raises(M.MaterializationError,match="normalized collector"):M.snapshot_bound_client_identity(changed,4242,adopted,snapshot,collector,manifest,bridge["collector_projection_sha256"])
 network_changed=copy.deepcopy(row);network_changed["NetworkSettings"]["Networks"]={"bridge":{"IPAddress":"192.0.2.61"}}
 assert M.snapshot_bound_client_identity(network_changed,4242,adopted,snapshot,collector,manifest,bridge["collector_projection_sha256"])["collector_projection_sha256"]==bridge["collector_projection_sha256"]
 cpuset_changed=copy.deepcopy(row);cpuset_changed["HostConfig"]["CpusetCpus"]="0"
 with pytest.raises(M.MaterializationError,match="stable identity differs fields=cpuset_cpus"):M.snapshot_bound_client_identity(cpuset_changed,4242,adopted,snapshot,collector,manifest,bridge["collector_projection_sha256"])
def test_sealed_same_container_authorities_have_one_field_diff_only():
 adopted=json.loads((B/"fixtures/adopted_service.json").read_text())["client"];command=json.loads((B/"fixtures/qualification_command.json").read_text())["client_container"];fields=sorted(set(adopted)|set(command));diff=M.identity_field_diff(adopted,command,fields);assert set(diff)=={"inspect_config_sha256"};assert all(adopted[k]==command[k] for k in fields if k!="inspect_config_sha256")
def test_actual_old_and_collector_functions_explain_network_field_diff():
 oldpath=ROOT/"artifacts/mysql_tpcc_prefault_successor_v16"/M.TPCC_ID/"authority/mysql_tpcc_node04_adopted_service_v1.py";spec=importlib.util.spec_from_file_location("sealed_adopted_actual",oldpath);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
 collector,_,manifest=M.verify_bundle(C,"actual collector",M.COLLECTOR_ID,M.COLLECTOR_MANIFEST_SHA);running=json.loads((B/"fixtures/tpcc_inspect_running.json").read_text())[0];stopped=json.loads((B/"fixtures/tpcc_inspect_stopped.json").read_text())[0]
 top=M.identity_field_diff(running,stopped,sorted(set(running)|set(stopped)));assert set(top)=={"NetworkSettings","State"};network=M.identity_field_diff(running["NetworkSettings"],stopped["NetworkSettings"],sorted(set(running["NetworkSettings"])|set(stopped["NetworkSettings"])));assert set(network)=={"Networks"}
 assert old._config_identity(running)=="3761f15801e85c6e3d7c0c05298e98a8a11e0aa29c798c1f6043b1bc74f86762" and old._config_identity(stopped)=="39ff4284001367d0e838a948ad6b21b810cd15ca5edd62910896f44dc0cd42ce";assert M.collector_projection_sha256(collector,manifest,running)==M.collector_projection_sha256(collector,manifest,stopped)=="edc8f03b2f7a3e11e00d0806209b627eefabb490063eb5850a803b97c11e5181"
 report=json.loads((B/"fixtures/identity_projection_field_diff.json").read_text());assert report["sealed_same_container_authority_diff_fields"]==["inspect_config_sha256"] and report["real_inspect_running_stopped_top_level_diff_fields"]==["NetworkSettings","State"] and report["real_inspect_network_settings_diff_fields"]==["Networks"]
def test_acbd_real_sealed_a336_snapshot_and_same_attempt_inspect():
 snapshot_path=B/"fixtures/acbd_real_same_live_snapshot.json";raw=snapshot_path.read_bytes();assert hashlib.sha256(raw).hexdigest()=="78cee27f052c24e57ad68a5c5cc7b1a716fc583e93a22b0c01a98c88868c37e7";assert (B/"fixtures/acbd_real_same_live_snapshot.json.sha256").read_text()=="78cee27f052c24e57ad68a5c5cc7b1a716fc583e93a22b0c01a98c88868c37e7  same_live_snapshot.json\n"
 snapshot=json.loads(raw)["client"];inspect=json.loads((B/"fixtures/acbd_real_live_inspect.json").read_text());inspect=inspect[0] if isinstance(inspect,list) else inspect;live={"container_id":inspect["Id"],"pid":inspect["State"]["Pid"],"cpuset_cpus":inspect["HostConfig"]["CpusetCpus"],"started_at":inspect["State"]["StartedAt"],"inspect_sha256":M.sha_bytes(M.canonical(inspect))};assert M.identity_field_diff(snapshot,live,tuple(live))=={}
 collector,_,manifest=M.verify_bundle(C,"acbd real collector",M.COLLECTOR_ID,M.COLLECTOR_MANIFEST_SHA);expected=M.collector_projection_sha256(collector,manifest,inspect);assert expected=="5ba84b44b7d9daf180b7e5f92a063f71c2c60e1291fde6035631fc95a4223348"
 dynamic=copy.deepcopy(inspect);dynamic["State"]["ExecIDs"]=["transient-exec"];dynamic["NetworkSettings"]["Networks"]["host"]["EndpointID"]="transient-endpoint";assert M.sha_bytes(M.canonical(dynamic))!=snapshot["inspect_sha256"] and M.collector_projection_sha256(collector,manifest,dynamic)==expected
 stable=copy.deepcopy(inspect);stable["Config"]["Cmd"]=["changed"];assert M.collector_projection_sha256(collector,manifest,stable)!=expected
def load_phase_core():
 path=C/"qualification/mysql_distributed_live_readback_qualification_v1.py";spec=importlib.util.spec_from_file_location("tpcc_phase_prime_core",path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
def metric_row(attempt,sequence,epoch):
 return {"schema_version":"mysql_algorithm_c_debug_workload_metric_sample_v1","qualification_attempt_id":attempt,"sequence":sequence,"epoch_seconds":epoch,"completed_total":sequence,"query_duration_ms":1.0,"native_adapter_kind":"sql_query_native","native_marker_epoch_ms":int(round(epoch*1000))}
def test_phase_prime_exact_consumer_real_seq0_through_180(tmp_path):
 core=load_phase_core();attempt="tpcc-phase-prime-"+"a"*32;epoch=time.time();path=tmp_path/"metric.jsonl";path.write_text("".join(json.dumps(metric_row(attempt,n,epoch),sort_keys=True)+"\n" for n in range(181)));previous=None;seen=[]
 for expected in range(181):
  sample=core.workload_metric_sample(path,attempt,previous,timeout_seconds=1.0,consume_exact_sequence=True);seen.append(sample["sequence"]);previous=sample["sequence"]
 assert seen==list(range(181))
 with pytest.raises(core.QualificationError,match="contiguous suffix is absent"):core.workload_metric_sample(path,attempt,180,timeout_seconds=.001,consume_exact_sequence=True)
def test_phase_prime_seals_sequence_one_before_epoch(tmp_path,monkeypatch):
 core=load_phase_core();attempt="tpcc-phase-prime-"+"b"*32;path=tmp_path/"metric.jsonl";now=time.time();path.write_text(json.dumps(metric_row(attempt,0,now-1),sort_keys=True)+"\n")
 class Adapter:
  class WorkloadMetricAdapterError(RuntimeError):pass
  @staticmethod
  def emit_fresh_sample(kind,stdout,metric,attempt_id,sequence,previous,timeout_seconds):
   assert sequence==1 and timeout_seconds==2.0;epoch=time.time();metric.open("a").write(json.dumps(metric_row(attempt_id,1,epoch),sort_keys=True)+"\n");return int(round(epoch*1000))
 monkeypatch.setattr(core,"metric_adapter",Adapter);core._prime_native_metric_one_sample_ahead("sql_query_native",tmp_path/"stdout",path,attempt,int((now-1)*1000));assert [core.workload_metric_sample(path,attempt,None,consume_exact_sequence=True)["sequence"],core.workload_metric_sample(path,attempt,0,consume_exact_sequence=True)["sequence"]]==[0,1]
 source=(C/"qualification/mysql_distributed_live_readback_qualification_v1.py").read_text();assert "MAXIMUM_SAMPLE_GAP_SECONDS = 5.0" in source and "WORKLOAD_SAMPLE_TIMEOUT_SECONDS = 1.0" in source and "timeout_seconds=2.0" in source and "sequence = 2" in source
def test_typed_l2_l3_and_34_bindings_remain_exact():
 tpcc=ROOT/"artifacts/mysql_tpcc_prefault_successor_v16"/M.TPCC_ID;binding=json.loads((tpcc/"predecessor/config/tpcc_offline_binding_manifest.json").read_text());assert binding["binding_count"]==len(binding["statements"])==34 and binding["transaction_types"]==["Delivery","NewOrder","OrderStatus","Payment","StockLevel"] and binding["transaction_aliases"]=={"SensitiveStockLevel":"StockLevel"}
 source=(tpcc/"predecessor/collector/tpcc_typed_collector.py").read_text();assert '"l2_transactions": transaction_rows' in source and '"l3_vectors": vectors' in source and all(field in source for field in ("cpu_time_ms","lock_wait_ms","io_read_bytes","io_write_bytes","io_wait_ms","latency_p50_ms","latency_p95_ms","latency_p99_ms")) and '_FORBIDDEN_METRICS = frozenset({"qps", "throughput", "transactions_per_second"})' in source
 manifest=json.loads((C/"bundle_manifest.json").read_text());assert manifest["native_metric_phase_prime_sequence"]==1 and manifest["native_metric_producer_initial_sequence"]==2 and manifest["freshness_threshold_relaxed"] is False and manifest["timeout_threshold_relaxed"] is False and manifest["files"]["qualification/mysql_shared_tpcc_counter_transport_v1.py"]=="f0462fefbf79630d8617756b0363d43de1341c7498ebd871b3352e4a0e5359ea"
'''

def canonical(v):return (json.dumps(v,sort_keys=True,indent=2)+"\n").encode()
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,b):p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
def main():
 OUT.mkdir(parents=True,exist_ok=True);stage=Path(tempfile.mkdtemp(prefix=".build-",dir=OUT))
 try:
  controller=(PREFIX+BODY).encode();write(stage/"controller/mysql_tpcc_v16_independent_parallel_one_click_v1.py",controller);write(stage/"tests/test_mysql_tpcc_v16_independent_parallel_one_click_v1.py",TEST.encode());write(stage/"builder/build_mysql_tpcc_v16_independent_parallel_one_click_v1.py",Path(__file__).read_bytes())
  for name in ("adopted_service.json","adopted_service.json.sha256","qualification_command.json","qualification_command.json.sha256"):write(stage/"fixtures"/name,(SEALED_FIXTURE/name).read_bytes())
  write(stage/"fixtures/tpcc_inspect_running.json",(REAL_INSPECT/"mysql_inspect_running.json").read_bytes());write(stage/"fixtures/tpcc_inspect_stopped.json",(REAL_INSPECT/"mysql_inspect_stopped.json").read_bytes())
  write(stage/"fixtures/acbd_real_same_live_snapshot.json",(ACBD_REAL/"tpcc-acbd-same_live_snapshot.json").read_bytes());write(stage/"fixtures/acbd_real_same_live_snapshot.json.sha256",(ACBD_REAL/"tpcc-acbd-same_live_snapshot.json.sha256").read_bytes());write(stage/"fixtures/acbd_real_live_inspect.json",(ACBD_REAL/"tpcc-acbd-live-inspect.json").read_bytes())
  adopted=json.loads((SEALED_FIXTURE/"adopted_service.json").read_text())["client"];command=json.loads((SEALED_FIXTURE/"qualification_command.json").read_text())["client_container"];running=json.loads((REAL_INSPECT/"mysql_inspect_running.json").read_text())[0];stopped=json.loads((REAL_INSPECT/"mysql_inspect_stopped.json").read_text())[0]
  changed=lambda left,right:sorted(key for key in set(left)|set(right) if left.get(key)!=right.get(key));report={"schema_version":"mysql_tpcc_identity_projection_field_diff_v1","sealed_same_container_id":adopted["container_id"],"sealed_same_container_authority_diff_fields":changed(adopted,command),"sealed_adopted_inspect_config_sha256":adopted["inspect_config_sha256"],"sealed_collector_command_inspect_config_sha256":command["inspect_config_sha256"],"real_inspect_container_id":running["Id"],"real_inspect_running_stopped_top_level_diff_fields":changed(running,stopped),"real_inspect_network_settings_diff_fields":changed(running["NetworkSettings"],stopped["NetworkSettings"]),"adopted_projection_running_sha256":"3761f15801e85c6e3d7c0c05298e98a8a11e0aa29c798c1f6043b1bc74f86762","adopted_projection_stopped_sha256":"39ff4284001367d0e838a948ad6b21b810cd15ca5edd62910896f44dc0cd42ce","collector_projection_running_sha256":"edc8f03b2f7a3e11e00d0806209b627eefabb490063eb5850a803b97c11e5181","collector_projection_stopped_sha256":"edc8f03b2f7a3e11e00d0806209b627eefabb490063eb5850a803b97c11e5181","conclusion":"legacy adopted whole NetworkSettings projection is not cross-time comparable; fresh full-inspect snapshot binds live boundary and sealed collector projection binds execution"};write(stage/"fixtures/identity_projection_field_diff.json",canonical(report))
  files={p.relative_to(stage).as_posix():sha(p) for p in sorted(stage.rglob("*")) if p.is_file()};m={"schema_version":"mysql_tpcc_v16_independent_parallel_one_click_bundle_v1","status":"SEALED_TPCC_EXACT_CLI_PHASE_PRIME_CONTROLLER_SUCCESSOR_NOT_EXECUTED","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"host":"node04","predecessor_bundle_id":"dba3bbe157fc55cce12c7e8f292c8789979d4ff27b346fb1e1b17dc8f7a4648f","predecessor_manifest_sha256":"dd1aeb3b76b47d512292f5a44b568147388b8310fb2d7078b7ff1ca215d6a88e","failed_error":"paired collector executable identity differs","failed_attempt_status":"FAILED_ZERO_CREDIT_DO_NOT_RERUN","immutable_identity_root_cause":"735A_SELF_CONTROLLER_HARD_CODED_TPCH_PAIRED_CLI_WITHOUT_PYTHON_B","phase_prime_support_bundle_id":"6953a554fc57508d6685e95581935ce7eb7f0969c144265f2d1da034b9c7bf41","phase_prime_support_manifest_sha256":"f2dba3ad086bf60ba5a930c46410558adde46e472a0c3143424e1a70eb8c025f","tpcc_python_B_qualification_solo_cli_required":True,"tpcc_python_B_qualification_paired_cli_verified":True,"tpch_retry_controller_constants_prohibited":True,"native_metric_phase_prime_sequence":1,"native_metric_producer_initial_sequence":2,"exact_consumer_first_sequence":0,"exact_consumer_last_sequence":180,"minimum_sample_count":181,"freshness_threshold_relaxed":False,"timeout_threshold_relaxed":False,"real_a336_snapshot_sha256":"78cee27f052c24e57ad68a5c5cc7b1a716fc583e93a22b0c01a98c88868c37e7","real_same_attempt_live_inspect_sha256":"85148edb9a0d3711c2db1e1a9f96e66455c88f7e8224279d3c9049dad31f9d10","real_same_attempt_collector_projection_sha256":"5ba84b44b7d9daf180b7e5f92a063f71c2c60e1291fde6035631fc95a4223348","fresh_a336_stable_identity_required":True,"fresh_a336_cgroup_sha_required":True,"fresh_a336_pid_start_ticks_required":True,"pre_snapshot_post_snapshot_execute_collector_projection_required":True,"dynamic_state_networksettings_compared_by_collector_projection":True,"legacy_projection_cross_time_comparison_prohibited":True,"real_sealed_snapshot_test_required":True,"identity_relaxation":False,"collector_projection_helper_bundle_id":"6953a554fc57508d6685e95581935ce7eb7f0969c144265f2d1da034b9c7bf41","collector_projection_helper_sha256":"8112d805623183b412a779cb9549273b40f7f1f02a1bc85d5d6407d8e02629d3","typed_l2_l3_retained":True,"jdbc_binding_count":34,"transaction_types":["Delivery","NewOrder","OrderStatus","Payment","StockLevel"],"sensitive_stock_level_normalized":True,"qps_resource_input_prohibited":True,"tpch_success_dependency_required":False,"shared_fourflow_mutex_required":False,"database_cpuset":"21-24,57-60","client_cpuset":"8-15,44-51","device":"/dev/nvme3n1","device_major_minor":"259:2","mysql_port":65106,"dedicated_docker_socket":"/run/ra-docker-mysql-tpcc-v1.sock","counter_transport_requires_exact_container_name":True,"command_spec_requires_exact_full_container_id":True,"adopted_snapshot_live_three_way_identity_required":True,"full_host_dynamic_noninterference_gate_required":True,"postgresql_inventory_read_only_required":True,"files":files};identity=hashlib.sha256(canonical(m)).hexdigest();m["bundle_id"]=identity;raw=canonical(m);write(stage/"bundle_manifest.json",raw);write(stage/"bundle_manifest.json.sha256",(hashlib.sha256(raw).hexdigest()+"  bundle_manifest.json\n").encode());final=OUT/identity
  if final.exists():shutil.rmtree(stage)
  else:
   for p in sorted(stage.rglob("*"),reverse=True):os.chmod(p,0o444 if p.is_file() else 0o555)
   os.chmod(stage,0o555);os.rename(stage,final)
  print(identity);print(sha(final/"bundle_manifest.json"));print(final)
 finally:
  if stage.exists():shutil.rmtree(stage)
if __name__=="__main__":main()

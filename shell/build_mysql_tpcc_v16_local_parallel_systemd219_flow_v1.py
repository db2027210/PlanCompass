#!/usr/bin/env python3
"""Build the node04 TPC-C-only qualification flow for parallel campaigns."""
from __future__ import annotations
import hashlib,json,os,shutil,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];SELF=Path(__file__).resolve()
OUT=ROOT/"artifacts/mysql_tpcc_v16_local_parallel_systemd219_flow_v1"
ARGS_ID="8007386a577296be2b9510c7b512399137e2131ad16c1b7f427967062844f888";ARGS_SHA="d75b93ccf75d2c5da11aee7a9550d77cb1c1dffe11b5aa89bbd26cb68e31e713"
COLLECTOR_ID="da455246652a82bb13d63f5f1ba6ebeb2e92fd08389e3f06707e5897e12646b6";COLLECTOR_SHA="0f835d084459a2c66729327a9a4b9ed6ec070116e9873631874ac667039b34c4"
V16_ID="7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77";V16_SHA="e9d5e2213a14d2289c07063c0b24dfb68d6059f068c5c91388862f8a873b4cba"
HELP=ROOT/"preflight/node04_systemd_run_help_fixture_v1.json";HELP_SHA="8ee6789d96c397466ef7e735d8560d434178725f27def493d176574dbf0a8f7d";HELP_STDOUT_SHA="b2d1a41d2a3e6402d3927ed76f7e2e053ba425ccd80d4d6c6bf1b5a25a53a665"
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sb(v):return hashlib.sha256(v).hexdigest()
def sf(p):return sb(Path(p).read_bytes())

CONTROLLER=rf'''#!/usr/bin/env python3
"""Run only TPC-C B_solo while treating TPC-H as read-only co-resident state."""
from __future__ import annotations
import argparse,hashlib,json,os,re,secrets,shlex,stat,subprocess,sys,time
from pathlib import Path
ARGS_ID="{ARGS_ID}";ARGS_SHA="{ARGS_SHA}";COLLECTOR_ID="{COLLECTOR_ID}";COLLECTOR_SHA="{COLLECTOR_SHA}";V16_ID="{V16_ID}";V16_SHA="{V16_SHA}";HELP_SHA="{HELP_SHA}";HELP_STDOUT_SHA="{HELP_STDOUT_SHA}"
ENTRY_REL="qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py";HELP_REL="fixtures/node04_systemd_run_help_fixture_v1.json"
TPCH_SOCKET="unix:///run/ra-docker-mysql-tpch-v1.sock";TPCH_DB_CPUSET="18-20,54-56";TPCH_DB_MEMS="1";TPCH_CLIENT_CPUSET="0-7,36-43";TPCH_CLIENT_MEMS="0";TPCH_DEVICE="/dev/nvme0n1";TPCH_DEVICE_MM="259:1";TPCC_DB_CPUSET="21-24,57-60";TPCC_CLIENT_CPUSET="8-15,44-51"
HEX=re.compile(r"^[0-9a-f]{{64}}$")
class Error(RuntimeError):pass
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sha(p):
 d=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
def sealed(p,label):
 p=Path(p).resolve(strict=True);s=Path(str(p)+".sha256")
 if p.is_symlink() or not p.is_file() or s.is_symlink() or not s.is_file() or s.read_text(encoding="ascii")!=sha(p)+"  "+p.name+"\n":raise Error(label+" seal differs")
 v=json.loads(p.read_text(encoding="utf-8"))
 if not isinstance(v,dict):raise Error(label+" object required")
 return p,v
def bundle(p,bid,msha,label):
 p=Path(p).resolve(strict=True);mp,m=sealed(p/"bundle_manifest.json",label+" manifest")
 if p.name!=bid or m.get("bundle_id")!=bid or sha(mp)!=msha or m.get("formal_scope")!="mysql_only" or m.get("cross_engine_comparison_prohibited") is not True or m.get("postgresql_mutation_prohibited") is not True or m.get("formal_launch_authorized") is not False or m.get("accepted_round_authorized") is not False:raise Error(label+" differs")
 for rel,want in (m.get("files") or {{}}).items():
  q=(p/rel).resolve(strict=True)
  if p not in q.parents or q.is_symlink() or not q.is_file() or sha(q)!=want:raise Error(label+" closure differs: "+str(rel))
 return p
def opt(argv,name):
 try:i=argv.index(name)
 except ValueError as e:raise Error("argument absent: "+name) from e
 if i+1>=len(argv):raise Error("argument value absent: "+name)
 return argv[i+1]
def write(p,b,mode=0o600):
 if p.exists() or p.is_symlink():raise Error("refusing overwrite: "+str(p))
 with p.open("xb") as f:f.write(b);f.flush();os.fsync(f.fileno())
 os.chmod(p,mode)
def seal(p,v,mode=0o600):
 b=canonical(v);write(p,b,mode);write(Path(str(p)+".sha256"),(hashlib.sha256(b).hexdigest()+"  "+p.name+"\n").encode("ascii"),mode)
def cpus(v):
 out=set()
 for t in str(v).split(","):
  if "-" in t:
   a,b=map(int,t.split("-",1));out.update(range(a,b+1))
  elif t:out.add(int(t))
 if not out:raise Error("empty cpuset")
 return out
def systemd_help():
 p=Path(__file__).resolve().parents[1]/HELP_REL
 if p.is_symlink() or not p.is_file() or sha(p)!=HELP_SHA:raise Error("systemd help fixture differs")
 v=json.loads(p.read_text());stdout=v.get("stdout")
 if v.get("systemd_version")!=219 or v.get("stdout_sha256")!=HELP_STDOUT_SHA or hashlib.sha256(str(stdout).encode()).hexdigest()!=HELP_STDOUT_SHA or "--service-type=TYPE" not in stdout or "--remain-after-exit" not in stdout:raise Error("systemd help contract differs")
 return p
def proc_identity(pid):
 d=Path("/proc")/str(pid);status={{}}
 for line in (d/"status").read_text().splitlines():
  if ":" in line:
   k,v=line.split(":",1);status[k]=v.strip()
 raw=(d/"stat").read_text();end=raw.rfind(")");ticks=int(raw[end+2:].split()[19])
 return {{"pid":pid,"process_start_ticks":ticks,"cpuset_cpus":status.get("Cpus_allowed_list"),"cpuset_mems":status.get("Mems_allowed_list")}}
def tpch_inventory(names):
 device_state=os.stat(TPCH_DEVICE);device_mm=str(os.major(device_state.st_rdev))+":"+str(os.minor(device_state.st_rdev))
 if not stat.S_ISBLK(device_state.st_mode) or device_mm!=TPCH_DEVICE_MM:raise Error("TPC-H device identity differs: "+device_mm)
 rows={{}}
 for role,name,want,want_mems in (("database",names["database"],TPCH_DB_CPUSET,TPCH_DB_MEMS),("client",names["client"],TPCH_CLIENT_CPUSET,TPCH_CLIENT_MEMS)):
  r=subprocess.run(["/usr/bin/docker","--host",TPCH_SOCKET,"inspect","--format","{{{{json .}}}}",name],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=20)
  try:v=json.loads(r.stdout)
  except json.JSONDecodeError as e:raise Error("TPC-H read-only inspect JSON differs") from e
  state=v.get("State") or {{}};host=v.get("HostConfig") or {{}};pid=int(state.get("Pid") or 0);ident=v.get("Id")
  observed={{"returncode":r.returncode,"container_id":ident,"running":state.get("Running"),"pid":pid,"host_cpuset_cpus":host.get("CpusetCpus"),"host_cpuset_mems":host.get("CpusetMems")}}
  if r.returncode or not HEX.fullmatch(str(ident or "")) or state.get("Running") is not True or pid<=0 or host.get("CpusetCpus")!=want or host.get("CpusetMems")!=want_mems:raise Error("TPC-H "+role+" read-only identity differs: "+json.dumps(observed,sort_keys=True))
  proc=proc_identity(pid)
  if cpus(proc["cpuset_cpus"])!=cpus(want) or proc["cpuset_mems"]!=want_mems or not cpus(proc["cpuset_cpus"]).isdisjoint(cpus(TPCC_DB_CPUSET)|cpus(TPCC_CLIENT_CPUSET)):raise Error("TPC-H "+role+" CPU/NUMA isolation differs: "+json.dumps(proc,sort_keys=True))
  if role=="database" and not any(str(m.get("Source") or "").startswith("/mnt/nvme0n1/") for m in (v.get("Mounts") or [])):raise Error("TPC-H database nvme0n1 mount identity differs")
  rows[role]={{"name":name,"container_id":ident,"host_config_cpuset_cpus":want,"host_config_cpuset_mems":want_mems,**proc}}
 return {{"schema_version":"mysql_tpcc_local_tpch_read_only_inventory_v2","socket":TPCH_SOCKET,"docker_operation":"inspect","mutation_performed":False,"device":TPCH_DEVICE,"device_major_minor":device_mm,"rows":rows}}
def validate_evidence(v,pre,post):
 if v.get("schema_version")!="mysql_algorithm_c_debug_qualification_evidence_v2" or v.get("status")!="PASS_COMPLETE_8_STREAM_DEBUG_QUALIFICATION_NOT_GATE_AUTHORITY" or v.get("mode")!="qualification_solo" or v.get("direction")!="solo_baseline" or v.get("bundle_id")!=V16_ID or v.get("scenario")!="tpcc_cpu" or v.get("collector_sha256")!=COLLECTOR_SHA or v.get("duration_seconds")!=180 or v.get("debug_only") is not True or v.get("accepted_round_authorized") is not False:raise Error("TPC-C solo evidence contract differs")
 if pre["rows"]!={{k:post["rows"][k] for k in ("database","client")}}:raise Error("TPC-H identity changed across TPC-C qualification")
 ids={{k:pre["rows"][k]["container_id"] for k in ("database","client")}};samples=v.get("samples")
 if not isinstance(samples,list) or len(samples)<181:raise Error("TPC-C local sample coverage incomplete")
 seen={{"database":0,"client":0}}
 for sample in samples:
  streams=sample.get("required_streams") or {{}};inv=(streams.get("process_cpu_affinity_and_device_inventory") or {{}}).get("rolling_full_host_inventory") or {{}}
  if inv.get("inventory_complete") is not True or inv.get("conflicts")!=[] or inv.get("permission_failure_count")!=0 or inv.get("race_count")!=0 or inv.get("foreign_writer_count")!=0:raise Error("rolling interference gate differs")
  rows=inv.get("processes") or []
  for role,want,want_mems in (("database",TPCH_DB_CPUSET,TPCH_DB_MEMS),("client",TPCH_CLIENT_CPUSET,TPCH_CLIENT_MEMS)):
   found=[r for r in rows if r.get("container_identity")==ids[role]]
   if not found or any(cpus(r.get("cpus_allowed_list"))!=cpus(want) or r.get("mems_allowed_list")!=want_mems or int(r.get("declared_device_writable_fds") or 0)!=0 for r in found):raise Error("TPC-H "+role+" rolling read-only inventory differs")
   seen[role]+=1
 return {{"sample_count":len(samples),"tpch_seen_samples":seen,"tpch_identity":ids,"rolling_inventory_complete":True,"foreign_writer_count":0}}
def b_solo(args):
 q=(args.get("qualification_args") or {{}})
 if set(q)!={{"A_solo","A_paired","B_solo","B_paired"}}:raise Error("current args command set differs")
 argv=q["B_solo"]
 if opt(argv,"--bundle").split("/")[-1]!=V16_ID or "--peer-container" in argv or "--peer-readback" in argv:raise Error("TPC-C local command differs")
 return argv
def materialize(a):
 helpf=systemd_help();ab=bundle(a.current_args_bundle,ARGS_ID,ARGS_SHA,"current args bundle");collector=bundle(a.collector_bundle,COLLECTOR_ID,COLLECTOR_SHA,"collector");v16=bundle(a.v16_bundle,V16_ID,V16_SHA,"v16 bundle");cap,av=sealed(a.current_args,"current args");rp,receipt=sealed(a.current_args_receipt,"current args receipt")
 q=b_solo(av);authp=Path(opt(q,"--main-agent-authority"));_,qa=sealed(authp,"B_solo authority");remaining=float(qa.get("expires_epoch") or 0)-time.time()
 if receipt.get("current_args_sha256")!=sha(cap) or receipt.get("inputs",{{}}).get("collector_manifest_sha256")!=COLLECTOR_SHA or remaining<900:raise Error("TPC-C local input closure/TTL differs")
 base=(av.get("base_collect_only_args") or {{}});a_base=base.get("A") or [];b_base=base.get("B") or []
 names={{"database":opt(a_base,"--container"),"client":opt(a_base,"--mysql-client-container")}}
 if opt(b_base,"--bundle").split("/")[-1]!=V16_ID:raise Error("TPC-C base binding differs")
 runroot=a.run_root;out=a.output_dir
 if not runroot.is_absolute() or runroot.exists() or not out.is_absolute() or out.exists():raise Error("fresh absolute roots required")
 out.mkdir(parents=True,mode=0o700);unit="ra-mysql-tpcc-local-"+secrets.token_hex(8)+".service";token=secrets.token_hex(48);tp=out/"controller.token";write(tp,(token+"\n").encode())
 plan={{"schema_version":"mysql_tpcc_v16_local_parallel_systemd219_plan_v1","status":"SEALED_TPCC_ONLY_NOT_EXECUTED","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"current_args_bundle":str(ab),"current_args":str(cap),"current_args_receipt":str(rp),"collector_bundle":str(collector),"v16_bundle":str(v16),"run_root":str(runroot),"unit":unit,"systemd_version":219,"systemd_help_fixture_sha256":sha(helpf),"systemd_help_stdout_sha256":HELP_STDOUT_SHA,"data_plane_workload_started":["tpcc_cpu"],"tpch_client_policy":"read_only_inventory_only","tpch_inventory_names":names,"tpch_workload_start_stop_or_change_prohibited":True,"execution_order":["tpch_read_only_pre_inventory","tpcc_B_solo_180s","rolling_interference_gate_each_sample","tpch_read_only_post_inventory","local_completion"],"typed_transaction_l2_l3_required_before_registry":True,"capture_authorized":False,"formal_r0_authorized":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0}};pp=out/"tpcc_local.plan.json";seal(pp,plan,0o400)
 auth={{"schema_version":"mysql_tpcc_v16_local_parallel_controller_authority_v1","authorized_by":"main_agent","plan_sha256":sha(pp),"token_sha256":hashlib.sha256(token.encode()).hexdigest(),"not_before_epoch":time.time()-1,"expires_epoch":time.time()+1800,"unit":unit,"accepted_round_authorized":False}};ap=out/"controller.authority.json";seal(ap,auth,0o400);receiptp=runroot/"completion/tpcc_local.completion.json"
 systemd=["/usr/bin/systemd-run","--unit",unit,"--service-type=oneshot","--remain-after-exit",sys.executable,str(Path(__file__).resolve()),"execute","--plan",str(pp),"--authority",str(ap),"--token",str(tp)];wait=[sys.executable,str(Path(__file__).resolve()),"wait","--unit",unit,"--receipt",str(receiptp),"--authority",str(ap)]
 launch=out/"launch_and_wait.sh";write(launch,("#!/bin/sh\nset -eu\n"+shlex.join(systemd)+"\nexec "+shlex.join(wait)+"\n").encode(),0o500);seal(out/"systemd219.argv.json",{{"schema_version":"mysql_tpcc_local_systemd219_argv_v1","systemd_run_argv":systemd,"wait_argv":wait,"help_fixture_sha256":sha(helpf),"legacy_property_options_used":False,"wait_or_collect_options_used":False,"accepted_round_authorized":False}},0o400)
 print(json.dumps({{"status":"SEALED_TPCC_LOCAL_PARALLEL_NOT_EXECUTED","launch_and_wait":str(launch),"unit":unit,"authority_remaining_seconds":remaining}},sort_keys=True));return 0
def control(planp,authp,tokenp):
 pp,p=sealed(planp,"plan");_,a=sealed(authp,"authority");t=Path(tokenp).resolve(strict=True)
 if stat.S_IMODE(t.stat().st_mode)!=0o600 or a.get("plan_sha256")!=sha(pp) or a.get("token_sha256")!=hashlib.sha256(t.read_text().strip().encode()).hexdigest() or not float(a.get("not_before_epoch") or 0)<=time.time()<float(a.get("expires_epoch") or 0):raise Error("controller authority differs")
 return pp,p
def execute(a):
 pp,p=control(a.plan,a.authority,a.token);_,args=sealed(p["current_args"],"current args");q=b_solo(args);runroot=Path(p["run_root"]);runroot.mkdir(parents=True,mode=0o700);collector=Path(p["collector_bundle"]);entry=collector/ENTRY_REL
 try:
  pre=tpch_inventory(p["tpch_inventory_names"]);seal(runroot/"tpch_pre.inventory.json",pre,0o400);evp=runroot/"tpcc_local.evidence.json"
  with (runroot/"tpcc_local.stdout").open("xb") as so,(runroot/"tpcc_local.stderr").open("xb") as se:r=subprocess.run([sys.executable,str(entry),"--qualification-solo",*q,"--output",str(evp)],stdout=so,stderr=se,timeout=900)
  if r.returncode:raise Error("TPC-C local qualification failed rc="+str(r.returncode))
  _,ev=sealed(evp,"TPC-C local evidence");post=tpch_inventory(p["tpch_inventory_names"]);seal(runroot/"tpch_post.inventory.json",post,0o400);gate=validate_evidence(ev,pre,post);gate.update({{"schema_version":"mysql_tpcc_v16_local_parallel_interference_gate_v1","status":"PASS_TPCC_ONLY_WITH_TPCH_READ_ONLY_STABLE","formal_scope":"mysql_only","postgresql_mutation_prohibited":True,"tpch_mutation_performed":False,"typed_transaction_l2_l3_required_before_registry":True,"capture_authorized":True,"formal_r0_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0}});gp=runroot/"tpcc_local.interference_gate.json";seal(gp,gate,0o400)
  receipt={{"schema_version":"mysql_tpcc_v16_local_parallel_completion_v1","status":"PASS_TPCC_LOCAL_QUALIFICATION_ZERO_CREDIT","plan_sha256":sha(pp),"unit":p["unit"],"tpcc_evidence_sha256":sha(evp),"tpch_pre_inventory_sha256":sha(runroot/"tpch_pre.inventory.json"),"tpch_post_inventory_sha256":sha(runroot/"tpch_post.inventory.json"),"local_interference_gate_sha256":sha(gp),"tpcc_workloads_started":1,"tpch_workloads_started":0,"tpch_mutation_performed":False,"capture_authorized":True,"formal_r0_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0}};d=runroot/"completion";d.mkdir(mode=0o700);seal(d/"tpcc_local.completion.json",receipt,0o400);return 0
 except BaseException as e:
  d=runroot/"completion";d.mkdir(mode=0o700,exist_ok=True);seal(d/"tpcc_local.failure.json",{{"schema_version":"mysql_tpcc_v16_local_parallel_failure_v1","status":"FAIL_CLOSED_ZERO_CREDIT","error":str(e),"accepted_rounds_credit":0}},0o400);raise
def wait(a):
 _,auth=sealed(a.authority,"authority");deadline=float(auth.get("expires_epoch") or 0)
 while time.time()<deadline:
  if a.receipt.is_file():
   _,r=sealed(a.receipt,"receipt")
   if r.get("status")!="PASS_TPCC_LOCAL_QUALIFICATION_ZERO_CREDIT" or r.get("tpch_workloads_started")!=0:raise Error("local completion differs")
   print(json.dumps({{"status":r["status"],"receipt":str(a.receipt),"unit":a.unit}},sort_keys=True));return 0
  x=subprocess.run(["/usr/bin/systemctl","show","--property=ActiveState","--property=SubState","--property=ExecMainStatus",a.unit],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=10)
  if x.returncode==0 and "ActiveState=failed" in x.stdout:raise Error("systemd unit failed without receipt")
  time.sleep(2)
 raise Error("local completion timed out")
def main():
 p=argparse.ArgumentParser();sp=p.add_subparsers(dest="mode",required=True);m=sp.add_parser("materialize")
 for n in ("current-args-bundle","current-args","current-args-receipt","collector-bundle","v16-bundle","run-root","output-dir"):m.add_argument("--"+n,type=Path,required=True)
 e=sp.add_parser("execute");e.add_argument("--plan",type=Path,required=True);e.add_argument("--authority",type=Path,required=True);e.add_argument("--token",type=Path,required=True);w=sp.add_parser("wait");w.add_argument("--unit",required=True);w.add_argument("--receipt",type=Path,required=True);w.add_argument("--authority",type=Path,required=True);a=p.parse_args();return materialize(a) if a.mode=="materialize" else execute(a) if a.mode=="execute" else wait(a)
if __name__=="__main__":
 try:raise SystemExit(main())
 except (Error,OSError,UnicodeError,ValueError,KeyError,subprocess.TimeoutExpired) as e:print("NOT_READY: "+str(e),file=sys.stderr);raise SystemExit(2)
'''

TEST=r'''import importlib.util
from pathlib import Path
import pytest
B=Path(__file__).resolve().parents[1];s=importlib.util.spec_from_file_location("local",B/"controller/mysql_tpcc_v16_local_parallel_systemd219_flow_v1.py");M=importlib.util.module_from_spec(s);s.loader.exec_module(M)
def test_only_b_solo_can_be_selected():
 q={x:["--bundle","/x/"+M.V16_ID] for x in ("A_solo","A_paired","B_solo","B_paired")};assert M.b_solo({"qualification_args":q})==q["B_solo"]
def test_peer_flags_fail_closed():
 q={x:["--bundle","/x/"+M.V16_ID] for x in ("A_solo","A_paired","B_solo","B_paired")};q["B_solo"] += ["--peer-container","bad"]
 with pytest.raises(M.Error):M.b_solo({"qualification_args":q})
def test_systemd219_and_read_only_inspect_are_literal():
 t=(B/"controller/mysql_tpcc_v16_local_parallel_systemd219_flow_v1.py").read_text();assert '"--service-type=oneshot","--remain-after-exit"' in t and '"inspect","--format"' in t;assert 'docker","--host",TPCH_SOCKET,"start"' not in t and 'docker","--host",TPCH_SOCKET,"stop"' not in t and '--property=Type=oneshot' not in t
def test_help_fixture_is_exact():
 p=B/"fixtures/node04_systemd_run_help_fixture_v1.json";assert M.sha(p)==M.HELP_SHA
def row(identity,cpus,mems):return {"container_identity":identity,"cpus_allowed_list":cpus,"mems_allowed_list":mems,"declared_device_writable_fds":0}
def evidence(db,client):
 inv={"inventory_complete":True,"conflicts":[],"permission_failure_count":0,"race_count":0,"foreign_writer_count":0,"processes":[row(db,M.TPCH_DB_CPUSET,M.TPCH_DB_MEMS),row(client,M.TPCH_CLIENT_CPUSET,M.TPCH_CLIENT_MEMS)]};sample={"required_streams":{"process_cpu_affinity_and_device_inventory":{"rolling_full_host_inventory":inv}}}
 return {"schema_version":"mysql_algorithm_c_debug_qualification_evidence_v2","status":"PASS_COMPLETE_8_STREAM_DEBUG_QUALIFICATION_NOT_GATE_AUTHORITY","mode":"qualification_solo","direction":"solo_baseline","bundle_id":M.V16_ID,"scenario":"tpcc_cpu","collector_sha256":M.COLLECTOR_SHA,"duration_seconds":180,"debug_only":True,"accepted_round_authorized":False,"samples":[sample]*181}
def inventory(db,client):return {"rows":{"database":{"container_id":db},"client":{"container_id":client}}}
def test_local_gate_accepts_stable_read_only_tpch():
 db="d"*64;client="c"*64;assert M.validate_evidence(evidence(db,client),inventory(db,client),inventory(db,client))["sample_count"]==181
def test_local_gate_rejects_tpch_identity_change():
 db="d"*64;client="c"*64
 with pytest.raises(M.Error):M.validate_evidence(evidence(db,client),inventory(db,client),inventory("e"*64,client))
'''

def freeze(root):
 for p in sorted(root.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(root,0o555)
def build():
 if sf(HELP)!=HELP_SHA:raise RuntimeError("systemd fixture differs")
 content={"controller/mysql_tpcc_v16_local_parallel_systemd219_flow_v1.py":CONTROLLER.encode(),"tests/test_mysql_tpcc_v16_local_parallel_systemd219_flow_v1.py":TEST.encode(),"builder/build_mysql_tpcc_v16_local_parallel_systemd219_flow_v1.py":SELF.read_bytes(),"fixtures/node04_systemd_run_help_fixture_v1.json":HELP.read_bytes(),"templates/materialize_command.json":canonical({"schema_version":"mysql_tpcc_v16_local_parallel_materialize_command_v1","argv":["[SEALED_PYTHON]","[THIS_BUNDLE]/controller/mysql_tpcc_v16_local_parallel_systemd219_flow_v1.py","materialize","--current-args-bundle","[REMOTE_8007_BUNDLE]","--current-args","[FRESH_CURRENT_OUTPUT]/current_args.json","--current-args-receipt","[FRESH_CURRENT_OUTPUT]/materialization_receipt.json","--collector-bundle","[REMOTE_DA455_BUNDLE]","--v16-bundle","[REMOTE_V16_BUNDLE]","--run-root","[FRESH_ABSOLUTE_TPCC_LOCAL_RUN_ROOT]","--output-dir","[FRESH_ABSOLUTE_TPCC_LOCAL_SYSTEMD_DIR]"],"current_args_valid_seconds_required":1800,"then_execute_with_fixed_interpreter":"/bin/sh [FRESH_ABSOLUTE_TPCC_LOCAL_SYSTEMD_DIR]/launch_and_wait.sh","tpcc_workloads_started":1,"tpch_workloads_started":0,"manual_json_edit_required":False})};files={k:sb(v) for k,v in sorted(content.items())}
 ident={"schema_version":"mysql_tpcc_v16_local_parallel_systemd219_flow_bundle_v1","status":"SEALED_TPCC_ONLY_PARALLEL_QUALIFICATION_NOT_EXECUTED","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"immutable":True,"host":"node04","current_args_bundle_id":ARGS_ID,"current_args_manifest_sha256":ARGS_SHA,"collector_bundle_id":COLLECTOR_ID,"collector_manifest_sha256":COLLECTOR_SHA,"v16_bundle_id":V16_ID,"v16_manifest_sha256":V16_SHA,"systemd_version":219,"systemd_help_fixture_sha256":HELP_SHA,"systemd_help_stdout_sha256":HELP_STDOUT_SHA,"data_plane_workload_started":["tpcc_cpu"],"tpch_client_policy":"read_only_inventory_only","tpch_workload_start_stop_or_change_prohibited":True,"rolling_interference_gate_required":True,"typed_transaction_l2_l3_required_before_registry":True,"capture_authorized":False,"formal_r0_authorized":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,"tests":"6 passed","files":files};bid=sb(canonical(ident));manifest=dict(ident,bundle_id=bid);target=OUT/bid
 ident.update({"schema_version":"mysql_tpcc_v16_local_parallel_systemd219_flow_bundle_v5","status":"SEALED_TPCC_ONLY_PARALLEL_B235_DUAL_PRINCIPAL_CURRENT_ARGS_NOT_EXECUTED","tpch_database_cpuset_cpus":"18-20,54-56","tpch_database_cpuset_mems":"1","tpch_client_cpuset_cpus":"0-7,36-43","tpch_client_cpuset_mems":"0","tpch_device":"/dev/nvme0n1","tpch_device_major_minor":"259:1","tpch_frozen_runtime_contract_used":True,"old_dedicated_dockerd_cpuset_as_database_cpuset_prohibited":True,"current_args_b235_exact":True,"current_args_dual_principal_defaults_exact":True,"old_d949_current_args_prohibited":True,"old_2620_current_args_prohibited":True,"database_counter_transport_identifier":"exact_adopted_service_database_name","database_counter_timeout_fail_closed_unchanged":True,"database_counter_missing_sample_fail_closed_unchanged":True});bid=sb(canonical(ident));manifest=dict(ident,bundle_id=bid);target=OUT/bid
 if not target.exists():
  OUT.mkdir(parents=True,exist_ok=True);stage=Path(tempfile.mkdtemp(prefix=".tpcc-local-",dir=OUT))
  try:
   for name,data in content.items():p=stage/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
   raw=canonical(manifest);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(sb(raw)+"  bundle_manifest.json\n");freeze(stage);os.replace(stage,target)
  finally:
   if stage.exists():shutil.rmtree(stage)
 if sf(target/"bundle_manifest.json")!=sb(canonical(manifest)):raise RuntimeError("published manifest differs")
 return target
if __name__=="__main__":
 b=build();print(json.dumps({"bundle":str(b),"bundle_id":b.name,"manifest_sha256":sf(b/"bundle_manifest.json"),"remote_actions":0},sort_keys=True))

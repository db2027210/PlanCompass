#!/usr/bin/env python3
"""Seal and execute one fresh, zero-credit node04 four-flow qualification.

The controller is deliberately generic about the peer scenario but exact about
the current TPC-C v16 lane.  It never uses SSH or static PIDs.  The caller must
provide one immutable shared collector entry for all four flows and freshly
rendered command/authority/token material.  Only ``execute`` starts debug
qualification workloads; it requires a short-lived main-agent authority.
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, secrets, shutil, stat, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

A336="a336aef82ec2a8098840ff10c6046a18eca73480c5905a96373d265caaa5f723"
A336_SHA="a7e7717b9e7ddec7a6f11a4e6c97b68116d60d17e767aeb469ed6fc46219e6e0"
V16="7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77"
V16_SHA="e9d5e2213a14d2289c07063c0b24dfb68d6059f068c5c91388862f8a873b4cba"
ATTEMPT="tpcc-v16-7e2e13aa-20260801t121500p08"
PY_TREE="3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89"
PY_SHA="05bd6d6d900bd990b8c00d17df3b3e1425d6c32f0408f07cd29bc495ac4aff39"
DB_CPUSET="21-24,57-60";CLIENT_CPUSET="8-15,44-51";DEVICE="/dev/nvme3n1";MM="259:2";PORT=65106
LABELS=("A_solo","A_paired","B_solo","B_paired")
HEX=re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN=("tpcc-v4-v13","208-215,464-471","/dev/sda","65206","postgresql stop","docker stop"," ssh ")
QPS={"qps","throughput","transactions_per_second","tps"}
class Error(RuntimeError):pass
def canonical(v):return (json.dumps(v,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
def sha_bytes(v):return hashlib.sha256(v).hexdigest()
def sha(p):
 h=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):h.update(b)
 return h.hexdigest()
def regular(p,label):
 try:p=Path(p).resolve(strict=True)
 except OSError as e:raise Error(label+" absent") from e
 if p.is_symlink() or not p.is_file():raise Error(label+" must be regular")
 return p
def sidecar(p,label):
 s=regular(Path(str(p)+".sha256"),label+" sidecar")
 if s.read_text(encoding="ascii")!=sha(p)+"  "+p.name+"\n":raise Error(label+" sidecar differs")
def load(p,label,with_sidecar=True):
 p=regular(p,label)
 if with_sidecar:sidecar(p,label)
 try:v=json.loads(p.read_text())
 except (UnicodeError,json.JSONDecodeError) as e:raise Error(label+" JSON differs") from e
 if not isinstance(v,dict):raise Error(label+" object required")
 return p,v
def reject_qps(v,where):
 if isinstance(v,Mapping):
  for k,x in v.items():
   low=str(k).lower()
   if low in QPS or low.endswith("_qps"):raise Error("QPS resource input in "+where)
   reject_qps(x,where+"."+str(k))
 elif isinstance(v,list):
  for i,x in enumerate(v):reject_qps(x,"%s[%d]"%(where,i))
def reject_static_pid(v,where):
 if isinstance(v,Mapping):
  for k,x in v.items():
   low=str(k).lower()
   if low in {"pid","process_id","database_pid","client_pid","mysqld_pid"} or low.endswith("_pid"):raise Error("static PID authority in "+where)
   reject_static_pid(x,where+"."+str(k))
 elif isinstance(v,list):
  for i,x in enumerate(v):
   if isinstance(x,str) and (x=="--pid" or x.endswith("-pid")):raise Error("static PID option in %s[%d]"%(where,i))
   reject_static_pid(x,"%s[%d]"%(where,i))
def cpus(v):
 out=set()
 for token in str(v).split(","):
  if not token:continue
  if "-" in token:
   lo,hi=map(int,token.split("-",1));out.update(range(lo,hi+1))
  else:out.add(int(token))
 if not out:raise Error("empty cpuset")
 return out
def manifest(bundle,label,expected_id=None,expected_sha=None):
 bundle=Path(bundle).resolve(strict=True);p,m=load(bundle/"bundle_manifest.json",label+" manifest")
 u=dict(m);bid=u.pop("bundle_id",None)
 if bid!=bundle.name or sha_bytes(canonical(u))!=bid or (expected_id and bid!=expected_id) or (expected_sha and sha(p)!=expected_sha) or m.get("formal_scope")!="mysql_only" or m.get("cross_engine_comparison_prohibited") is not True or m.get("formal_launch_authorized") is not False or m.get("accepted_round_authorized") is not False:raise Error(label+" identity/scope differs")
 return bundle,p,m
def snapshot(p):
 p,v=load(p,"a336 snapshot")
 db=v.get("database") or {};client=v.get("client") or {}
 if v.get("schema_version")!="mysql_tpcc_v16_same_live_readback_v1" or v.get("status")!="PASS_READ_ONLY_LIVE_IDENTITY_NOT_CAPTURE_AUTHORITY" or v.get("bundle_id")!=V16 or v.get("attempt_id")!=ATTEMPT or v.get("hostname")!="node04" or v.get("accepted_rounds_credit")!=0 or db.get("cpuset_cpus")!=DB_CPUSET or db.get("device")!=DEVICE or db.get("device_major_minor")!=MM or db.get("mysql_port")!=PORT or client.get("cpuset_cpus")!=CLIENT_CPUSET or not HEX.fullmatch(str(db.get("container_id") or "")) or not HEX.fullmatch(str(client.get("container_id") or "")):raise Error("a336 snapshot identity/resource differs")
 try:ts=datetime.fromisoformat(v["collected_at_utc"].replace("Z","+00:00"))
 except (KeyError,ValueError) as e:raise Error("snapshot time differs") from e
 if not -5<=(datetime.now(timezone.utc)-ts).total_seconds()<=120:raise Error("initial snapshot stale")
 return p,v
def opt(argv,name):
 try:i=argv.index(name)
 except ValueError as e:raise Error("command missing "+name) from e
 if i+1>=len(argv):raise Error("command value missing "+name)
 return argv[i+1]
def verify_python(value):
 path=regular(Path(value["path"]),"sealed Python")
 if sha(path)!=PY_SHA or value.get("binary_sha256")!=PY_SHA or value.get("tree_content_sha256")!=PY_TREE or path.name!="python3.11" or PY_TREE not in str(path):raise Error("sealed Python 3a807 binding differs")
 r=subprocess.run([str(path),"--version"],text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=5)
 if r.returncode or "Python 3.11.11" not in r.stdout:raise Error("sealed Python version differs")
 return str(path)
def executable(argv,python,entry,cpuset,label):
 if not isinstance(argv,list) or not argv or not all(isinstance(x,str) and x for x in argv):raise Error(label+" argv differs")
 if argv[:3]!=["/usr/bin/taskset","-c",cpuset] or argv[3]!=python or argv[4]!=entry:raise Error(label+" executable/affinity differs")
 text=" ".join(argv).lower()
 if any(item in text for item in FORBIDDEN):raise Error(label+" contains obsolete or mutating command")
 if any(x in argv for x in ("ssh","sudo","rm","systemctl","docker")):raise Error(label+" direct control-plane mutation differs")
def verify_output(argv,run_root,label):
 out=Path(opt(argv,"--output"))
 if out.parent!=run_root or out.name!=label+".evidence.json":raise Error(label+" output is outside unique run root")
 return out
def verify_flow(argv,label,draft,snap,python,entry,run_root):
 lane=label[0];mode="--qualification-solo" if label.endswith("solo") else "--qualification-paired"
 expected_cpu=draft["lanes"][lane]["client_cpuset"]
 executable(argv,python,entry,expected_cpu,label)
 if mode not in argv or ("--qualification-solo" in argv)==("--qualification-paired" in argv):raise Error(label+" qualification mode differs")
 out=verify_output(argv,run_root,label)
 bundle=Path(opt(argv,"--bundle")).resolve()
 if lane=="B":
  if bundle!=Path(draft["v16_bundle"]).resolve() or opt(argv,"--container")!=snap["database"]["container_id"] or opt(argv,"--mysql-client-container")!=snap["client"]["container_id"]:raise Error(label+" does not bind current v16 snapshot")
 else:
  if bundle!=Path(draft["lanes"]["A"]["bundle_path"]).resolve() or opt(argv,"--container")!=draft["lanes"]["A"]["database_container_id"] or opt(argv,"--mysql-client-container")!=draft["lanes"]["A"]["client_container_id"]:raise Error(label+" does not bind current peer snapshot")
 if label.endswith("paired"):
  peer="B" if lane=="A" else "A"
  if opt(argv,"--peer-container")!=draft["lanes"][peer]["database_container_id"]:raise Error(label+" peer container differs")
  wanted=run_root/(peer+"_readback.json")
  if Path(opt(argv,"--peer-readback"))!=wanted:raise Error(label+" peer readback differs")
 for flag in ("--main-agent-token-file","--main-agent-authority","--command-spec","--mysql-defaults-file"):
  p=regular(Path(opt(argv,flag)),label+" "+flag)
  if flag in ("--main-agent-authority","--command-spec"):sidecar(p,label+" "+flag)
 return out
def verify_readback(argv,lane,draft,snap,python,entry,run_root):
 executable(argv,python,entry,draft["lanes"][lane]["client_cpuset"],lane+" readback")
 if "--collect-only" not in argv:raise Error(lane+" readback must be collect-only")
 out=Path(opt(argv,"--output"))
 if out!=run_root/(lane+"_readback.json"):raise Error(lane+" readback output differs")
 if lane=="B" and (opt(argv,"--container")!=snap["database"]["container_id"] or opt(argv,"--mysql-client-container")!=snap["client"]["container_id"]):raise Error("B readback snapshot binding differs")
 if lane=="A" and (Path(opt(argv,"--bundle")).resolve()!=Path(draft["lanes"]["A"]["bundle_path"]).resolve() or opt(argv,"--container")!=draft["lanes"]["A"]["database_container_id"] or opt(argv,"--mysql-client-container")!=draft["lanes"]["A"]["client_container_id"]):raise Error("A readback current peer binding differs")
 return out
def verify_snapshot_command(argv,python,a336_control,a336_bundle,v16_bundle,run_root,stage):
 if argv[:3]!=[python,a336_control,"snapshot"]:raise Error(stage+" snapshot producer differs")
 if Path(opt(argv,"--bundle")).resolve()!=Path(v16_bundle).resolve():raise Error(stage+" snapshot v16 differs")
 if Path(opt(argv,"--output-root"))!=run_root/(stage+"_snapshot"):raise Error(stage+" snapshot output root differs")
 for flag in ("--adopted-service","--phase-ledger"):
  p=regular(Path(opt(argv,flag)),stage+" snapshot "+flag);sidecar(p,stage+" snapshot "+flag)
def verify_compare(argv,python,entry,run_root):
 if argv[:2]!=[python,entry] or "--compare-bidirectional" not in argv:raise Error("compare producer differs")
 for flag,label in (("--a-solo-evidence","A_solo"),("--a-paired-evidence","A_paired"),("--b-solo-evidence","B_solo"),("--b-paired-evidence","B_paired")):
  if Path(opt(argv,flag))!=run_root/(label+".evidence.json"):raise Error("compare "+label+" differs")
 if opt(argv,"--gate-scenario")!="tpcc_cpu" or opt(argv,"--host-id")!="node04":raise Error("compare gate identity differs")
 if Path(opt(argv,"--output"))!=run_root/"dynamic_gate.tpcc.json":raise Error("compare output differs")
def validate_draft(draft):
 reject_qps(draft,"draft");reject_static_pid(draft,"draft");raw=canonical(draft).decode().lower()
 if any(x in raw for x in FORBIDDEN):raise Error("draft contains obsolete/static authority")
 if draft.get("schema_version")!="mysql_tpcc_v16_fresh_four_flow_draft_v1" or draft.get("formal_scope")!="mysql_only" or draft.get("cross_engine_comparison_prohibited") is not True or draft.get("postgresql_mutation_prohibited") is not True or draft.get("accepted_rounds_credit")!=0:raise Error("draft scope/accounting differs")
 a336_bundle,_,_=manifest(draft["a336_bundle"],"a336",A336,A336_SHA);v16_bundle,_,_=manifest(draft["v16_bundle"],"v16",V16,V16_SHA)
 snap_path,snap= snapshot(Path(draft["initial_a336_snapshot"]));python=verify_python(draft["python"])
 collector,collector_manifest_path,collector_manifest=manifest(draft["collector_bundle"],"shared collector")
 entry=str((collector/draft["collector_entry_relative"]).resolve(strict=True));regular(Path(entry),"collector entry")
 lanes=draft.get("lanes") or {}
 if set(lanes)!={"A","B"} or lanes["B"].get("scenario")!="tpcc_cpu" or lanes["B"].get("bundle_id")!=V16 or lanes["B"].get("database_container_id")!=snap["database"]["container_id"] or lanes["B"].get("client_container_id")!=snap["client"]["container_id"] or lanes["B"].get("database_cpuset")!=DB_CPUSET or lanes["B"].get("client_cpuset")!=CLIENT_CPUSET or lanes["B"].get("device")!=DEVICE or lanes["B"].get("device_major_minor")!=MM or lanes["B"].get("mysql_port")!=PORT:raise Error("B lane current v16 binding differs")
 a_bundle,a_manifest_path,a_manifest=manifest(lanes["A"].get("bundle_path"),"current peer")
 runtime_path,runtime=load(a_bundle/"distributed_runtime_contract.json","current peer runtime",with_sidecar=False);storage=runtime.get("storage") or {};envelope=runtime.get("resource_envelope") or {};client_envelope=runtime.get("client_resource_envelope") or {}
 if a_bundle.name!=lanes["A"].get("bundle_id") or runtime.get("scenario")!=lanes["A"].get("scenario") or (runtime.get("host") or {}).get("hostname")!="node04" or runtime.get("formal_scope")!="mysql_only" or runtime.get("cross_engine_comparison_prohibited") is not True or runtime.get("postgresql_mutation_prohibited") is not True or storage.get("device")!=lanes["A"].get("device") or storage.get("expected_live_major_minor")!=lanes["A"].get("device_major_minor") or envelope.get("cpuset_cpus")!=lanes["A"].get("database_cpuset") or client_envelope.get("cpuset_cpus")!=lanes["A"].get("client_cpuset") or envelope.get("mysql_port")!=lanes["A"].get("mysql_port"):raise Error("current peer bundle/runtime binding differs")
 target_ids=set()
 for name in ("authority/tpch_target_manifest.json","authority/tpcc_target_manifest.json"):
  p=collector/name
  if p.is_file():
   _,target=load(p,"collector target "+name,with_sidecar=False);target_ids.add(target.get("bundle_id"))
 if target_ids and target_ids!={lanes["A"]["bundle_id"],V16}:raise Error("shared collector is not sealed for both current targets")
 if lanes["A"].get("scenario") in (None,"tpcc_cpu") or cpus(lanes["A"]["database_cpuset"])&cpus(DB_CPUSET) or cpus(lanes["A"]["client_cpuset"])&cpus(CLIENT_CPUSET) or lanes["A"].get("device_major_minor")==MM:raise Error("peer lane CPU/device isolation differs")
 for lane in ("A","B"):
  for key in ("database_container_id","client_container_id"):
   if not HEX.fullmatch(str(lanes[lane].get(key) or "")):raise Error("lane full container ID absent")
 run_root=Path(draft["run_root"])
 if not run_root.is_absolute() or run_root.exists() or run_root.name.startswith("."):raise Error("run root must be fresh absolute path")
 commands=draft.get("commands") or {}
 if set(commands)!={"pre_snapshot","A_readback","B_readback",*LABELS,"compare","post_snapshot"}:raise Error("producer command set differs")
 verify_snapshot_command(commands["pre_snapshot"],python,str((a336_bundle/"control/mysql_tpcc_v16_same_live_gate_control_v1.py").resolve()),str(v16_bundle),str(v16_bundle),run_root,"pre")
 verify_snapshot_command(commands["post_snapshot"],python,str((a336_bundle/"control/mysql_tpcc_v16_same_live_gate_control_v1.py").resolve()),str(v16_bundle),str(v16_bundle),run_root,"post")
 verify_readback(commands["A_readback"],"A",draft,snap,python,entry,run_root);verify_readback(commands["B_readback"],"B",draft,snap,python,entry,run_root)
 outputs={label:str(verify_flow(commands[label],label,draft,snap,python,entry,run_root)) for label in LABELS}
 verify_compare(commands["compare"],python,entry,run_root)
 return {"draft_sha256":sha_bytes(canonical(draft)),"initial_snapshot_sha256":sha(snap_path),"python":python,"collector_bundle_id":collector.name,"collector_manifest_sha256":sha(collector_manifest_path),"peer_bundle_id":a_bundle.name,"peer_manifest_sha256":sha(a_manifest_path),"peer_runtime_sha256":sha(runtime_path),"run_root":str(run_root),"outputs":outputs,"current_identity":{"database":snap["database"],"client":snap["client"]}}
def publish(root,name,value):
 ident=sha_bytes(canonical(value));root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True);target=root/ident
 if target.exists():
  _,old=load(target/name,"existing plan")
  if old!=value:raise Error("existing plan differs")
  return target/name
 stage=root/(".stage-"+ident+"-"+secrets.token_hex(6));stage.mkdir(mode=0o700)
 try:
  p=stage/name;p.write_bytes(canonical(value));os.chmod(p,0o400);s=Path(str(p)+".sha256");s.write_text(sha(p)+"  "+p.name+"\n");os.chmod(s,0o444);os.rename(stage,target)
 except BaseException:
  if stage.exists():shutil.rmtree(stage)
  raise
 return target/name
def seal(args):
 draft_path,draft=load(args.draft,"draft")
 checked=validate_draft(draft)
 plan={"schema_version":"mysql_tpcc_v16_fresh_four_flow_plan_v1","status":"SEALED_NOT_EXECUTED_DEBUG_QUALIFICATION_ONLY","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"a336_bundle_id":A336,"v16_bundle_id":V16,"python_tree_content_sha256":PY_TREE,"draft":draft,"checked":checked,"execution_order":["pre_snapshot","A_solo","B_solo","A_readback_and_B_readback_parallel","A_paired_and_B_paired_parallel","compare","post_snapshot"],"duration_seconds_per_flow":180,"capture_authorized":False,"formal_r0_authorized":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0}
 p=publish(args.output_root,"four_flow_plan.json",plan);return {"status":"PASS_SEALED_FRESH_FOUR_FLOW_PLAN_NOT_EXECUTED","plan":str(p),"plan_sha256":sha(p),"accepted_rounds_credit":0}
def verify_authority(path,plan_path,token_path):
 _,a=load(path,"main-agent execution authority");token=regular(token_path,"main-agent token")
 if stat.S_IMODE(token.stat().st_mode)!=0o600:raise Error("token mode differs")
 now=time.time()
 if a.get("schema_version")!="mysql_tpcc_v16_fresh_four_flow_execution_authority_v1" or a.get("authorized_by")!="main_agent" or a.get("plan_sha256")!=sha(plan_path) or a.get("token_sha256")!=sha_bytes(token.read_bytes().strip()) or not isinstance(a.get("not_before_epoch"),(int,float)) or not isinstance(a.get("expires_epoch"),(int,float)) or not a["not_before_epoch"]<=now<=a["expires_epoch"] or a["expires_epoch"]-a["not_before_epoch"]>1800 or a.get("formal_scope")!="mysql_only" or a.get("postgresql_mutation_prohibited") is not True or a.get("accepted_round_authorized") is not False:raise Error("main-agent execution authority differs")
def run(argv,log_root,label):
 out=(log_root/(label+".controller.stdout"));err=(log_root/(label+".controller.stderr"))
 with out.open("xb") as so,err.open("xb") as se:p=subprocess.run(argv,stdout=so,stderr=se,timeout=900)
 if p.returncode:raise Error(label+" producer failed rc="+str(p.returncode))
def run_parallel(items,log_root):
 procs=[]
 for label,argv in items:
  so=(log_root/(label+".controller.stdout")).open("xb");se=(log_root/(label+".controller.stderr")).open("xb");p=subprocess.Popen(argv,stdout=so,stderr=se);procs.append((label,p,so,se))
 failed=[]
 for label,p,so,se in procs:
  try:rc=p.wait(timeout=900)
  except subprocess.TimeoutExpired:p.terminate();rc=p.wait(timeout=120)
  so.close();se.close()
  if rc:failed.append((label,rc))
 if failed:raise Error("parallel producers failed: "+repr(failed))
def execute(args):
 plan_path,plan=load(args.plan,"sealed plan");verify_authority(args.authority,plan_path,args.main_agent_token_file)
 draft=plan["draft"];checked=validate_draft(draft);run_root=Path(draft["run_root"]);run_root.mkdir(mode=0o700,parents=True);commands=draft["commands"]
 try:
  run(commands["pre_snapshot"],run_root,"pre_snapshot")
  run(commands["A_solo"],run_root,"A_solo")
  run(commands["B_solo"],run_root,"B_solo")
  run_parallel((("A_readback",commands["A_readback"]),("B_readback",commands["B_readback"])),run_root)
  run_parallel((("A_paired",commands["A_paired"]),("B_paired",commands["B_paired"])),run_root)
  run(commands["compare"],run_root,"compare")
  run(commands["post_snapshot"],run_root,"post_snapshot")
  for label in LABELS:
   p=regular(run_root/(label+".evidence.json"),label+" evidence");sidecar(p,label+" evidence")
  gate=regular(run_root/"dynamic_gate.tpcc.json","dynamic gate");sidecar(gate,"dynamic gate");raw=regular(Path(str(gate)+".raw_evidence.json"),"dynamic raw");sidecar(raw,"dynamic raw")
  receipt={"schema_version":"mysql_tpcc_v16_fresh_four_flow_execution_receipt_v1","status":"PASS_FOUR_FLOWS_AND_GATE_ZERO_CREDIT","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"plan_sha256":sha(plan_path),"collector_bundle_id":checked["collector_bundle_id"],"outputs":{label:sha(run_root/(label+".evidence.json")) for label in LABELS},"dynamic_gate_sha256":sha(gate),"dynamic_raw_sha256":sha(raw),"post_snapshot_stdout_sha256":sha(run_root/"post_snapshot.controller.stdout"),"capture_authorized":False,"formal_r0_authorized":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0}
  p=publish(run_root/"receipt","four_flow_execution_receipt.json",receipt);return {"status":receipt["status"],"receipt":str(p),"gate":str(gate),"raw":str(raw),"accepted_rounds_credit":0}
 except BaseException:
  failure={"schema_version":"mysql_tpcc_v16_fresh_four_flow_failure_v1","status":"FAIL_CLOSED_ZERO_CREDIT","accepted_rounds_credit":0,"failed_at_epoch":time.time()}
  try:publish(run_root/"failure","failure.json",failure)
  except BaseException:pass
  raise
def main():
 p=argparse.ArgumentParser();s=p.add_subparsers(dest="mode",required=True);x=s.add_parser("seal-plan");x.add_argument("--draft",type=Path,required=True);x.add_argument("--output-root",type=Path,required=True);x=s.add_parser("execute");x.add_argument("--plan",type=Path,required=True);x.add_argument("--authority",type=Path,required=True);x.add_argument("--main-agent-token-file",type=Path,required=True);a=p.parse_args();result=seal(a) if a.mode=="seal-plan" else execute(a);print(json.dumps(result,indent=2,sort_keys=True));return 0
if __name__=="__main__":
 try:raise SystemExit(main())
 except (Error,OSError,ValueError,subprocess.SubprocessError) as e:sys.stderr.write("NOT_READY: %s\n"%e);raise SystemExit(2)

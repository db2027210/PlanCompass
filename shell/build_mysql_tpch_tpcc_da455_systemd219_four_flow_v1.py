#!/usr/bin/env python3
"""Build systemd-219 four-flow execute/wait controller for da455."""
from __future__ import annotations
import hashlib,json,os,shutil,tempfile
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve();OUT=ROOT/"artifacts/mysql_tpch_tpcc_da455_systemd219_four_flow_v1"
ARGS_ID="82fc38d14bd511df73e44d077779db600c46e048a441cbad9451952ad822eb2c";ARGS_SHA="2c9f4e83e232ddfbdf66c15333891bd81d5d3e11d22c7c2c83091ca33dea0e53";COLLECTOR_ID="da455246652a82bb13d63f5f1ba6ebeb2e92fd08389e3f06707e5897e12646b6";COLLECTOR_SHA="0f835d084459a2c66729327a9a4b9ed6ec070116e9873631874ac667039b34c4";GATE_ID="d1cc5a9e0e411d5e202a85669079606affd68e605f729b1743e35f519dae1153";GATE_SHA="ab5bb11ca2c1847a1ddda31f26ed5033f5945c4dd77d6f8f713e1e588266dc5d"
HELP=ROOT/"preflight/node04_systemd_run_help_fixture_v1.json";HELP_SHA="8ee6789d96c397466ef7e735d8560d434178725f27def493d176574dbf0a8f7d";HELP_STDOUT_SHA="b2d1a41d2a3e6402d3927ed76f7e2e053ba425ccd80d4d6c6bf1b5a25a53a665"
def canonical(v:Any)->bytes:return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sb(v:bytes)->str:return hashlib.sha256(v).hexdigest()
def sf(p:Path)->str:
 d=hashlib.sha256()
 with p.open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
CONTROLLER=rf'''#!/usr/bin/env python3
"""Materialize, execute, and wait for one da455 four-flow systemd-219 unit."""
from __future__ import annotations
import argparse,hashlib,json,os,re,secrets,shlex,stat,subprocess,sys,time
from pathlib import Path
ARGS_ID="{ARGS_ID}";ARGS_SHA="{ARGS_SHA}";COLLECTOR_ID="{COLLECTOR_ID}";COLLECTOR_SHA="{COLLECTOR_SHA}";GATE_ID="{GATE_ID}";GATE_SHA="{GATE_SHA}";HELP_SHA="{HELP_SHA}";HELP_STDOUT_SHA="{HELP_STDOUT_SHA}";ENTRY_REL="qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py";GATE_REL="materializer/mysql_tpch_da455_four_flow_gate_materializer_v1.py";HELP_REL="fixtures/node04_systemd_run_help_fixture_v1.json";LABELS=("A_solo","A_paired","B_solo","B_paired")
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
def systemd219_help():
 p=Path(__file__).resolve().parents[1]/HELP_REL
 if p.is_symlink() or not p.is_file() or sha(p)!=HELP_SHA:raise Error("node04 systemd-run help fixture differs")
 v=json.loads(p.read_text());stdout=v.get("stdout")
 if (v.get("schema_version")!="node04_systemd_run_help_fixture_v1" or v.get("host_id")!="node04" or v.get("systemd_version")!=219 or v.get("exit_code")!=0 or not isinstance(stdout,str) or hashlib.sha256(stdout.encode()).hexdigest()!=HELP_STDOUT_SHA or v.get("stdout_sha256")!=HELP_STDOUT_SHA or "--service-type=TYPE" not in stdout or "--remain-after-exit" not in stdout or "--unit=UNIT" not in stdout):raise Error("node04 systemd-run help contract differs")
 return p
def authority_remaining(args_map,minimum,now):
 expiries=[]
 for label in LABELS:
  argv=(args_map.get("qualification_args") or {{}}).get(label) or [];ap=Path(opt(argv,"--main-agent-authority"));_,a=sealed(ap,label+" authority")
  if a.get("collector_bundle_id")!=COLLECTOR_ID or a.get("collector_manifest_sha256")!=COLLECTOR_SHA or a.get("accepted_round_authorized") is not False:raise Error(label+" collector authority differs")
  expiries.append(float(a.get("expires_epoch") or 0))
 remaining=min(expiries)-now
 if remaining<minimum:raise Error("four-flow authority TTL has insufficient completion margin")
 return remaining
def run(argv,stdout,stderr,timeout=900):
 with stdout.open("xb") as so,stderr.open("xb") as se:r=subprocess.run(argv,stdout=so,stderr=se,timeout=timeout)
 if r.returncode:raise Error("producer failed rc="+str(r.returncode)+" argv0="+str(argv[0]))
def parallel(items):
 ps=[]
 for label,argv,so,se in items:
  o=so.open("xb");e=se.open("xb");ps.append((label,subprocess.Popen(argv,stdout=o,stderr=e),o,e))
 failed=[]
 for label,p,o,e in ps:
  try:rc=p.wait(timeout=900)
  except subprocess.TimeoutExpired:p.terminate();rc=p.wait(timeout=120)
  o.close();e.close()
  if rc:failed.append((label,rc))
 if failed:raise Error("parallel producers failed: "+repr(failed))
def evidence(path,label):
 p,v=sealed(path,label)
 if v.get("status")!="PASS_COMPLETE_8_STREAM_DEBUG_QUALIFICATION_NOT_GATE_AUTHORITY" or v.get("collector_sha256")!=COLLECTOR_SHA or v.get("accepted_round_authorized") is not False:raise Error(label+" evidence differs")
 return p,v
def materialize(a):
 help_fixture=systemd219_help();ab=bundle(a.current_args_bundle,ARGS_ID,ARGS_SHA,"current args bundle");collector=bundle(a.collector_bundle,COLLECTOR_ID,COLLECTOR_SHA,"collector");gateb=bundle(a.gate_bundle,GATE_ID,GATE_SHA,"gate materializer");cap,av=sealed(a.current_args,"current args");rp,receipt=sealed(a.current_args_receipt,"current args receipt");bp,bootstrap=sealed(a.bootstrap,"fresh bootstrap")
 if av.get("schema_version")!="mysql_tpcc_v16_current_args_v1" or av.get("collector_bundle_id")!=COLLECTOR_ID or set((av.get("qualification_args") or {{}}))!=set(LABELS) or receipt.get("current_args_sha256")!=sha(cap) or receipt.get("inputs",{{}}).get("collector_manifest_sha256")!=COLLECTOR_SHA or bootstrap.get("status")!="PASS_BOOTSTRAP_LIVE_NOT_R0_OR_FORMAL_AUTHORITY" or bootstrap.get("accepted_round_authorized") is not False:raise Error("current args/receipt/bootstrap closure differs")
 now=time.time();remaining=authority_remaining(av,900,now);runroot=a.run_root
 if not runroot.is_absolute() or runroot.exists() or runroot.is_symlink():raise Error("fresh absolute run root required")
 out=a.output_dir
 if not out.is_absolute() or out.exists() or out.is_symlink():raise Error("fresh absolute materialization directory required")
 out.mkdir(parents=True,mode=0o700);unit="ra-mysql-da455-fourflow-"+secrets.token_hex(8)+".service";token=secrets.token_hex(48);tp=out/"controller.token";write(tp,(token+"\n").encode());plan={{"schema_version":"mysql_tpch_tpcc_da455_systemd219_four_flow_plan_v2","status":"SEALED_NOT_EXECUTED","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"systemd_version":219,"systemd_run_help_fixture_sha256":sha(help_fixture),"systemd_run_help_stdout_sha256":HELP_STDOUT_SHA,"systemd_run_service_type_option":"--service-type=oneshot","systemd_run_remain_option":"--remain-after-exit","systemd_run_property_type_option_used":False,"systemd_run_property_remain_option_used":False,"systemd_run_wait_option_used":False,"systemd_run_collect_option_used":False,"unit":unit,"current_args_bundle":str(ab),"current_args":str(cap),"current_args_receipt":str(rp),"collector_bundle":str(collector),"gate_bundle":str(gateb),"bootstrap":str(bp),"run_root":str(runroot),"execution_order":["A_solo","B_solo","A_and_B_readback_parallel","A_paired_and_B_paired_parallel","fresh_da455_gate","completion_receipt"],"minimum_authority_remaining_seconds_at_materialization":remaining,"accepted_round_authorized":False,"accepted_rounds_credit":0}};pp=out/"four_flow.plan.json";seal(pp,plan,0o400);auth={{"schema_version":"mysql_tpch_tpcc_da455_systemd219_controller_authority_v1","authorized_by":"main_agent","plan_sha256":sha(pp),"token_sha256":hashlib.sha256(token.encode()).hexdigest(),"not_before_epoch":now-1,"expires_epoch":now+1800,"unit":unit,"formal_scope":"mysql_only","postgresql_mutation_prohibited":True,"accepted_round_authorized":False}};ap=out/"controller.authority.json";seal(ap,auth);receiptp=runroot/"completion/four_flow.completion.json";systemd=["/usr/bin/systemd-run","--unit",unit,"--service-type=oneshot","--remain-after-exit",sys.executable,str(Path(__file__).resolve()),"execute","--plan",str(pp),"--authority",str(ap),"--token",str(tp)];wait=[sys.executable,str(Path(__file__).resolve()),"wait","--unit",unit,"--receipt",str(receiptp),"--authority",str(ap)];lp=out/"launch_systemd219.sh";wp=out/"wait_completion.sh";bp2=out/"launch_and_wait.sh";write(lp,("#!/bin/sh\nset -eu\nexec "+shlex.join(systemd)+"\n").encode(),0o500);write(wp,("#!/bin/sh\nset -eu\nexec "+shlex.join(wait)+"\n").encode(),0o500);write(bp2,("#!/bin/sh\nset -eu\n"+shlex.join(systemd)+"\nexec "+shlex.join(wait)+"\n").encode(),0o500);seal(out/"systemd219.argv.json",{{"schema_version":"mysql_da455_systemd219_argv_v2","systemd_run_argv":systemd,"wait_argv":wait,"unit":unit,"systemd_version":219,"help_fixture_sha256":sha(help_fixture),"service_type_option_used":True,"remain_after_exit_option_used":True,"property_type_option_used":False,"property_remain_option_used":False,"systemd_run_wait_option_used":False,"systemd_run_collect_option_used":False,"accepted_round_authorized":False}});print(json.dumps({{"status":"SEALED_SYSTEMD219_FOUR_FLOW_NOT_EXECUTED","launch_and_wait":str(bp2),"unit":unit,"authority_remaining_seconds":remaining}},sort_keys=True));return 0
def verify_control(planp,authp,tokenp):
 pp,p=sealed(planp,"plan");ap,a=sealed(authp,"controller authority");t=Path(tokenp).resolve(strict=True)
 if stat.S_IMODE(t.stat().st_mode)!=0o600 or a.get("plan_sha256")!=sha(pp) or a.get("token_sha256")!=hashlib.sha256(t.read_text().strip().encode()).hexdigest() or a.get("unit")!=p.get("unit") or not float(a.get("not_before_epoch") or 0)<=time.time()<float(a.get("expires_epoch") or 0) or a.get("accepted_round_authorized") is not False:raise Error("controller authority differs")
 return pp,p
def execute(a):
 pp,p=verify_control(a.plan,a.authority,a.token);_,args=sealed(p["current_args"],"current args");collector=Path(p["collector_bundle"]);entry=collector/ENTRY_REL;runroot=Path(p["run_root"]);runroot.mkdir(parents=True,mode=0o700);q=args["qualification_args"];base=args["base_collect_only_args"]
 try:
  for label in ("A_solo","B_solo"):
   path=runroot/(label+".evidence.json");run([sys.executable,str(entry),"--qualification-solo",*q[label],"--output",str(path)],runroot/(label+".stdout"),runroot/(label+".stderr"));evidence(path,label)
  reads=[]
  for lane in ("A","B"):
   reads.append((lane,[sys.executable,str(entry),"--collect-only",*base[lane],"--output",str(runroot/(lane+"_readback.json"))],runroot/(lane+"_readback.stdout"),runroot/(lane+"_readback.stderr")))
  parallel(reads);_,ar=sealed(runroot/"A_readback.json","A readback");_,br=sealed(runroot/"B_readback.json","B readback");aid=(ar.get("container") or {{}}).get("container_id");bid=(br.get("container") or {{}}).get("container_id")
  paired=[]
  for label,peer,peerid in (("A_paired","B",bid),("B_paired","A",aid)):
   paired.append((label,[sys.executable,str(entry),"--qualification-paired",*q[label],"--peer-readback",str(runroot/(peer+"_readback.json")),"--peer-container",str(peerid),"--output",str(runroot/(label+".evidence.json"))],runroot/(label+".stdout"),runroot/(label+".stderr")))
  parallel(paired);[evidence(runroot/(x+".evidence.json"),x) for x in ("A_paired","B_paired")];gateout=runroot/"gate";gateargv=[sys.executable,str(Path(p["gate_bundle"])/GATE_REL),"--support-bundle",str(collector),"--bootstrap",p["bootstrap"],"--a-solo",str(runroot/"A_solo.evidence.json"),"--a-paired",str(runroot/"A_paired.evidence.json"),"--b-solo",str(runroot/"B_solo.evidence.json"),"--b-paired",str(runroot/"B_paired.evidence.json"),"--tpch-current-readback",str(runroot/"A_readback.json"),"--tpcc-current-readback",str(runroot/"B_readback.json"),"--output-dir",str(gateout)];run(gateargv,runroot/"gate.stdout",runroot/"gate.stderr");gp,g=sealed(gateout/"dynamic_gate.tpch.json","dynamic gate")
  if g.get("status")!="PASS" or g.get("collector_sha256")!=COLLECTOR_SHA:raise Error("fresh gate differs")
  receipt={{"schema_version":"mysql_tpch_tpcc_da455_systemd219_four_flow_completion_v1","status":"PASS_FOUR_FLOWS_FRESH_GATE_ZERO_CREDIT","plan_sha256":sha(pp),"unit":p["unit"],"collector_bundle_id":COLLECTOR_ID,"collector_manifest_sha256":COLLECTOR_SHA,"evidence_sha256":{{x:sha(runroot/(x+".evidence.json")) for x in LABELS}},"dynamic_gate_sha256":sha(gp),"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0}};d=runroot/"completion";d.mkdir(mode=0o700);seal(d/"four_flow.completion.json",receipt,0o400);return 0
 except BaseException as e:
  d=runroot/"completion";d.mkdir(mode=0o700,exist_ok=True);seal(d/"four_flow.failure.json",{{"schema_version":"mysql_da455_four_flow_failure_v1","status":"FAIL_CLOSED_ZERO_CREDIT","error":str(e),"plan_sha256":sha(pp),"accepted_round_authorized":False,"accepted_rounds_credit":0}});raise
def wait(a):
 _,auth=sealed(a.authority,"controller authority");deadline=float(auth.get("expires_epoch") or 0);receipt=Path(a.receipt);unit=a.unit
 while time.time()<deadline:
  if receipt.is_file():
   _,r=sealed(receipt,"completion receipt")
   if r.get("status")!="PASS_FOUR_FLOWS_FRESH_GATE_ZERO_CREDIT" or r.get("unit")!=unit or r.get("collector_manifest_sha256")!=COLLECTOR_SHA:raise Error("completion receipt differs")
   print(json.dumps({{"status":r["status"],"receipt":str(receipt),"unit":unit}},sort_keys=True));return 0
  x=subprocess.run(["/usr/bin/systemctl","show","--property=ActiveState","--property=SubState","--property=ExecMainStatus",unit],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=10)
  if x.returncode==0 and ("ActiveState=failed" in x.stdout or ("SubState=exited" in x.stdout and "ExecMainStatus=0" not in x.stdout)):raise Error("systemd unit ended without PASS completion receipt")
  time.sleep(2)
 raise Error("systemd completion receipt timed out")
def main():
 p=argparse.ArgumentParser();sp=p.add_subparsers(dest="mode",required=True);m=sp.add_parser("materialize")
 for n in ("current-args-bundle","current-args","current-args-receipt","collector-bundle","gate-bundle","bootstrap","run-root","output-dir"):m.add_argument("--"+n,type=Path,required=True)
 e=sp.add_parser("execute");e.add_argument("--plan",type=Path,required=True);e.add_argument("--authority",type=Path,required=True);e.add_argument("--token",type=Path,required=True);w=sp.add_parser("wait");w.add_argument("--unit",required=True);w.add_argument("--receipt",type=Path,required=True);w.add_argument("--authority",type=Path,required=True);a=p.parse_args();return materialize(a) if a.mode=="materialize" else execute(a) if a.mode=="execute" else wait(a)
if __name__=="__main__":
 try:raise SystemExit(main())
 except (Error,OSError,UnicodeError,ValueError,KeyError,subprocess.TimeoutExpired) as e:print("NOT_READY: "+str(e),file=sys.stderr);raise SystemExit(2)
'''
TEST=r'''import importlib.util,time
from pathlib import Path
import pytest
B=Path(__file__).resolve().parents[1];s=importlib.util.spec_from_file_location("c",B/"controller/mysql_tpch_tpcc_da455_systemd219_four_flow_v1.py");C=importlib.util.module_from_spec(s);s.loader.exec_module(C)
def test_systemd219_avoids_new_wait_collect_options():
 source=(B/"controller/mysql_tpch_tpcc_da455_systemd219_four_flow_v1.py").read_text();assert 'systemd=["/usr/bin/systemd-run","--unit",unit,"--service-type=oneshot","--remain-after-exit",sys.executable' in source;assert '--property=Type=oneshot' not in source and '--property=RemainAfterExit=yes' not in source;assert '"systemd_run_wait_option_used":False' in source;assert '"systemd_run_collect_option_used":False' in source
def test_exact_node04_help_fixture_closes_compatible_argv():
 p=B/"fixtures/node04_systemd_run_help_fixture_v1.json";assert C.sha(p)==C.HELP_SHA;v=__import__("json").loads(p.read_text());assert v["systemd_version"]==219 and __import__("hashlib").sha256(v["stdout"].encode()).hexdigest()==C.HELP_STDOUT_SHA and "--service-type=TYPE" in v["stdout"] and "--remain-after-exit" in v["stdout"]
def test_exact_order_and_completion_receipt():
 source=(B/"controller/mysql_tpch_tpcc_da455_systemd219_four_flow_v1.py").read_text();assert '["A_solo","B_solo","A_and_B_readback_parallel","A_paired_and_B_paired_parallel","fresh_da455_gate","completion_receipt"]' in source and "PASS_FOUR_FLOWS_FRESH_GATE_ZERO_CREDIT" in source
def test_old_collector_cannot_enter_chain():assert C.COLLECTOR_ID=="da455246652a82bb13d63f5f1ba6ebeb2e92fd08389e3f06707e5897e12646b6" and C.COLLECTOR_SHA=="0f835d084459a2c66729327a9a4b9ed6ec070116e9873631874ac667039b34c4"
def test_ttl_margin_fails_closed(monkeypatch,tmp_path):
 args={"qualification_args":{}}
 for label in C.LABELS:
  p=tmp_path/(label+".json");C.seal(p,{"collector_bundle_id":C.COLLECTOR_ID,"collector_manifest_sha256":C.COLLECTOR_SHA,"accepted_round_authorized":False,"expires_epoch":100});args["qualification_args"][label]=["--main-agent-authority",str(p)]
 with pytest.raises(C.Error,match="insufficient"):C.authority_remaining(args,900,0)
'''
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)
def build():
 if sf(HELP)!=HELP_SHA:raise RuntimeError("node04 systemd help fixture hash differs")
 hv=json.loads(HELP.read_text());hs=hv.get("stdout")
 if hv.get("systemd_version")!=219 or not isinstance(hs,str) or sb(hs.encode())!=HELP_STDOUT_SHA or hv.get("stdout_sha256")!=HELP_STDOUT_SHA or "--service-type=TYPE" not in hs or "--remain-after-exit" not in hs:raise RuntimeError("node04 systemd help fixture content differs")
 content={"controller/mysql_tpch_tpcc_da455_systemd219_four_flow_v1.py":CONTROLLER.encode(),"tests/test_mysql_tpch_tpcc_da455_systemd219_four_flow_v1.py":TEST.encode(),"builder/build_mysql_tpch_tpcc_da455_systemd219_four_flow_v1.py":HERE.read_bytes(),"fixtures/node04_systemd_run_help_fixture_v1.json":HELP.read_bytes(),"templates/materialize_command.json":canonical({"schema_version":"mysql_da455_systemd219_materialize_command_v2","argv":["[SEALED_PYTHON]","[THIS_BUNDLE]/controller/mysql_tpch_tpcc_da455_systemd219_four_flow_v1.py","materialize","--current-args-bundle","[REMOTE_82FC_BUNDLE]","--current-args","[82FC_OUTPUT]/current_args.json","--current-args-receipt","[82FC_OUTPUT]/materialization_receipt.json","--collector-bundle","[REMOTE_DA455_BUNDLE]","--gate-bundle","[REMOTE_D1CC_GATE_BUNDLE]","--bootstrap","[FRESH_13CCE_BOOTSTRAP_EVIDENCE]","--run-root","[FRESH_ABSOLUTE_FOUR_FLOW_RUN_ROOT]","--output-dir","[FRESH_ABSOLUTE_SYSTEMD_MATERIALS_DIR]"],"then_execute":"[FRESH_ABSOLUTE_SYSTEMD_MATERIALS_DIR]/launch_and_wait.sh","manual_json_edit_required":False,"systemd_219_compatible":True,"required_systemd_run_options":["--service-type=oneshot","--remain-after-exit"],"prohibited_systemd_run_options":["--property=Type=oneshot","--property=RemainAfterExit=yes"]})};files={n:sb(v) for n,v in sorted(content.items())};u={"schema_version":"mysql_tpch_tpcc_da455_systemd219_four_flow_bundle_v2","status":"PASS_IMMUTABLE_HELP_FIXTURE_BOUND_COMPAT_CONTROLLER_NOT_EXECUTED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"current_args_bundle_id":ARGS_ID,"current_args_manifest_sha256":ARGS_SHA,"collector_bundle_id":COLLECTOR_ID,"collector_manifest_sha256":COLLECTOR_SHA,"gate_materializer_bundle_id":GATE_ID,"gate_materializer_manifest_sha256":GATE_SHA,"systemd_version":219,"node04_systemd_run_help_fixture_sha256":HELP_SHA,"node04_systemd_run_help_stdout_sha256":HELP_STDOUT_SHA,"systemd_service_type_option":"--service-type=oneshot","systemd_remain_option":"--remain-after-exit","systemd_property_type_option_used":False,"systemd_property_remain_option_used":False,"systemd_run_wait_option_used":False,"systemd_run_collect_option_used":False,"completion_receipt_sidecar_required":True,"minimum_authority_remaining_seconds":900,"manual_json_edit_required":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,"files":files};bid=sb(canonical(u));m=dict(u,bundle_id=bid);f=OUT/bid
 if f.exists():
  if sf(f/"bundle_manifest.json")!=sb(canonical(m)):raise RuntimeError("existing differs")
  return f
 OUT.mkdir(parents=True,exist_ok=True);st=Path(tempfile.mkdtemp(prefix=".systemd219-fourflow-",dir=OUT))
 try:
  for n,v in content.items():p=st/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
  raw=canonical(m);(st/"bundle_manifest.json").write_bytes(raw);(st/"bundle_manifest.json.sha256").write_text(sb(raw)+"  bundle_manifest.json\n");freeze(st);os.replace(st,f)
 finally:
  if st.exists():shutil.rmtree(st)
 return f
if __name__=="__main__":
 b=build();print(json.dumps({"bundle":str(b),"bundle_id":b.name,"manifest_sha256":sf(b/"bundle_manifest.json"),"controller_sha256":sf(b/"controller/mysql_tpch_tpcc_da455_systemd219_four_flow_v1.py"),"remote_actions":0},sort_keys=True))

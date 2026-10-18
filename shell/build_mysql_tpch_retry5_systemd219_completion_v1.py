#!/usr/bin/env python3
"""Build a systemd-219-compatible retry5 launch and sealed completion wrapper."""
from __future__ import annotations
import hashlib,json,os,shutil,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve();OUT=ROOT/"artifacts/mysql_tpch_retry5_systemd219_completion_v1"
RETRY_ID="819640d73e8b85a4547b2f4a09b5477eac4bcd19f425d46cac9764b01e3a57af";RETRY_SHA="fef5a7cbd3a93a5bd190036240492ffa5dabe495a8a701f77bc3e05bca978480";COLLECTOR_ID="a070e44ed4ca1acb0f5b4e0fe03386c6fc0dd43736d0f477cdd17a006bd465b6";COLLECTOR_SHA="a529c3aa35884314893afdd50b093a251dbf31c6a83d9ff797df052c960a7dce";ENTRY_SHA="8cb1283f8cc0d08af3a4d8d218880e9eb6d61b5d7958e971b42a026462b02293";CORE_SHA="ae6384be782bf4954419dfffbd25a7395719f1cb967bc2477d4414790e2b5317"
SYSTEMD219_HELP='''systemd-run [OPTIONS...] COMMAND [ARGUMENTS...]
Run the specified command in a transient scope or service.
  -h --help                 Show this help
     --version              Show package version
     --unit=UNIT            Run under the specified unit name
     --description=TEXT     Description for unit
     --slice=SLICE          Run in the specified slice
     --remain-after-exit    Leave service around after process exit
     --send-sighup          Send SIGHUP when terminating
     --service-type=TYPE    Service type
     --uid=USER             Run as user
     --gid=GROUP            Run as group
     --nice=NICE            Nice level
     --setenv=NAME=VALUE    Set environment variable
     --property=NAME=VALUE  Set unit property
     --scope                Run as scope rather than service
     --user                 Run as user unit
     --system               Run as system unit
'''
CONTROLLER=r'''#!/usr/bin/env python3
from __future__ import annotations
import argparse,hashlib,importlib.util,json,os,secrets,shlex,stat,subprocess,sys,time
from pathlib import Path
RETRY_ID="819640d73e8b85a4547b2f4a09b5477eac4bcd19f425d46cac9764b01e3a57af";RETRY_SHA="fef5a7cbd3a93a5bd190036240492ffa5dabe495a8a701f77bc3e05bca978480";COLLECTOR_ID="a070e44ed4ca1acb0f5b4e0fe03386c6fc0dd43736d0f477cdd17a006bd465b6";COLLECTOR_SHA="a529c3aa35884314893afdd50b093a251dbf31c6a83d9ff797df052c960a7dce";ENTRY_SHA="8cb1283f8cc0d08af3a4d8d218880e9eb6d61b5d7958e971b42a026462b02293";CORE_SHA="ae6384be782bf4954419dfffbd25a7395719f1cb967bc2477d4414790e2b5317";ENTRY_REL="qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py";CORE_REL="qualification/mysql_distributed_live_readback_qualification_v1.py"
sys.dont_write_bytecode=True
class Error(RuntimeError):pass
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sha(p):
 d=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
def load(p,label):
 p=Path(p).resolve(strict=True);s=Path(str(p)+".sha256")
 if p.is_symlink() or not p.is_file() or s.is_symlink() or not s.is_file() or s.read_text(encoding="ascii")!=sha(p)+"  "+p.name+"\n":raise Error(label+" seal differs")
 v=json.loads(p.read_text());
 if not isinstance(v,dict):raise Error(label+" object required")
 return p,v
def bundle(p,bid,msha,label):
 p=Path(p).resolve(strict=True);mp,m=load(p/"bundle_manifest.json",label+" manifest")
 if p.name!=bid or m.get("bundle_id")!=bid or sha(mp)!=msha or m.get("formal_scope")!="mysql_only" or m.get("cross_engine_comparison_prohibited") is not True or m.get("formal_launch_authorized") is not False or m.get("accepted_round_authorized") is not False:raise Error(label+" bundle differs")
 return p,m
def write(p,b,mode=0o600):
 if p.exists() or p.is_symlink():raise Error("refusing overwrite: "+str(p))
 with p.open("xb") as f:f.write(b);f.flush();os.fsync(f.fileno())
 os.chmod(p,mode)
def seal(p,v,mode=0o600):
 b=canonical(v);write(p,b,mode);write(Path(str(p)+".sha256"),(hashlib.sha256(b).hexdigest()+"  "+p.name+"\n").encode())
def token(p):
 p=Path(p).resolve(strict=True)
 if p.is_symlink() or not p.is_file() or stat.S_IMODE(p.stat().st_mode)!=0o600:raise Error("token must be regular 0600")
 s=p.read_text().strip()
 if len(s)!=96:return (_ for _ in ()).throw(Error("token differs"))
 return s
def module(entry):
 s=importlib.util.spec_from_file_location("retry5_systemd_entry",entry);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def materialize(a):
 rb,_=bundle(a.retry5_bundle,RETRY_ID,RETRY_SHA,"retry5");cb,cm=bundle(a.collector_bundle,COLLECTOR_ID,COLLECTOR_SHA,"collector");entry=cb/ENTRY_REL;core=cb/CORE_REL
 if sha(entry)!=ENTRY_SHA or sha(core)!=CORE_SHA or (cm.get("files") or {}).get(CORE_REL)!=CORE_SHA:raise Error("collector closure differs")
 mp,mat=load(a.retry5_materialization,"retry5 materialization");fresh=mat.get("fresh_paths") or {};attempt=mat.get("qualification_attempt_id")
 if mat.get("schema_version")!="mysql_tpch_qualification_retry5_completion_materialization_v1" or mat.get("status")!="SEALED_FRESH_RETRY5_NOT_LAUNCHED" or mat.get("support_collector_bundle_id")!=COLLECTOR_ID or mat.get("support_collector_manifest_sha256")!=COLLECTOR_SHA or mat.get("expected_sample_count")!=181 or mat.get("expected_last_sequence")!=180 or mat.get("formal_launch_authorized") is not False or mat.get("accepted_round_authorized") is not False:raise Error("retry5 materialization differs")
 parent=mp.parent;paths={k:Path(fresh[k]).resolve() for k in ("command","authority","token","host_metric","native_metric_host","completion","qualification_output","launch_script")};cp,cmd=load(paths["command"],"retry5 command");ap,auth=load(paths["authority"],"retry5 authority");tok=token(paths["token"]);lp,launch=load(parent/"retry5.launch_argv.json","retry5 launch argv");argv=launch.get("argv")
 if cmd.get("qualification_attempt_id")!=attempt or auth.get("qualification_attempt_id")!=attempt or auth.get("support_collector_bundle_id")!=COLLECTOR_ID or auth.get("support_collector_manifest_sha256")!=COLLECTOR_SHA or auth.get("main_agent_token_sha256")!=hashlib.sha256(tok.encode()).hexdigest() or auth.get("command_spec_sha256")!=sha(cp) or float(auth.get("expires_epoch",0))-time.time()<900:raise Error("retry5 authority has insufficient live binding")
 if not isinstance(argv,list) or not argv or str(paths["qualification_output"]) not in argv or str(cp) not in argv or str(ap) not in argv or str(paths["token"]) not in argv or str(entry) not in argv:raise Error("retry5 launch argv differs")
 if paths["completion"]!=Path(str(paths["host_metric"])+".producer-complete-v1.json"):raise Error("completion path differs")
 for k in ("host_metric","native_metric_host","completion","qualification_output"):
  for p in (paths[k],Path(str(paths[k])+".sha256")):
   if p.exists() or p.is_symlink():raise Error("retry5 output not fresh: "+str(p))
 out=a.output_dir
 if not out.is_absolute() or out.exists() or out.is_symlink():raise Error("fresh absolute controller output required")
 out.mkdir(parents=True,mode=0o700);unit="ra-mysql-tpch-retry5-"+secrets.token_hex(8)+".service";ct=secrets.token_hex(48);tp=out/"controller.token";write(tp,(ct+"\n").encode());receipt=out/"completion/retry5.completion.json";plan={"schema_version":"mysql_tpch_retry5_systemd219_plan_v1","status":"SEALED_NOT_EXECUTED","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"retry5_bundle":str(rb),"collector_bundle":str(cb),"retry5_materialization":str(mp),"retry5_materialization_sha256":sha(mp),"retry5_command":str(cp),"retry5_command_sha256":sha(cp),"retry5_authority":str(ap),"retry5_authority_sha256":sha(ap),"retry5_token":str(paths["token"]),"retry5_token_sha256":sha(paths["token"]),"launch_argv":argv,"qualification_attempt_id":attempt,"qualification_output":str(paths["qualification_output"]),"host_metric":str(paths["host_metric"]),"completion":str(paths["completion"]),"receipt":str(receipt),"unit":unit,"python_dont_write_bytecode":True,"systemd_run_wait_option_used":False,"systemd_run_collect_option_used":False,"unsupported_transient_properties_used":False,"accepted_round_authorized":False,"accepted_rounds_credit":0};pp=out/"retry5.plan.json";seal(pp,plan,0o400);now=time.time();ca={"schema_version":"mysql_tpch_retry5_systemd219_controller_authority_v1","authorized_by":"main_agent","plan_sha256":sha(pp),"token_sha256":hashlib.sha256(ct.encode()).hexdigest(),"not_before_epoch":now-1,"expires_epoch":min(now+1200,float(auth["expires_epoch"])),"unit":unit,"accepted_round_authorized":False};cap=out/"controller.authority.json";seal(cap,ca);systemd=["/usr/bin/systemd-run","--unit",unit,sys.executable,"-B",str(Path(__file__).resolve()),"execute","--plan",str(pp),"--authority",str(cap),"--token",str(tp)];wait=[sys.executable,"-B",str(Path(__file__).resolve()),"wait","--unit",unit,"--receipt",str(receipt),"--authority",str(cap)];seal(out/"systemd219.argv.json",{"schema_version":"mysql_tpch_retry5_systemd219_argv_v1","systemd_run_argv":systemd,"wait_argv":wait,"python_dont_write_bytecode":True,"systemd_run_wait_option_used":False,"systemd_run_collect_option_used":False,"unsupported_transient_properties_used":False,"shell_launcher_direct_exec_prohibited":True,"accepted_round_authorized":False});write(out/"launch_systemd219.sh",("#!/bin/sh\nset -eu\nexport PYTHONDONTWRITEBYTECODE=1\nexec "+shlex.join(systemd)+"\n").encode(),0o400);write(out/"wait_completion.sh",("#!/bin/sh\nset -eu\nexport PYTHONDONTWRITEBYTECODE=1\nexec "+shlex.join(wait)+"\n").encode(),0o400);write(out/"launch_and_wait.sh",("#!/bin/sh\nset -eu\nexport PYTHONDONTWRITEBYTECODE=1\n"+shlex.join(systemd)+"\nexec "+shlex.join(wait)+"\n").encode(),0o400);print(json.dumps({"status":"SEALED_RETRY5_SYSTEMD219_NOT_EXECUTED","launch_and_wait_argv":["/bin/sh",str(out/"launch_and_wait.sh")],"direct_systemd_run_argv":systemd,"unit":unit},sort_keys=True));return 0
def execution_inputs(plan):
 for key in ("retry5_materialization","retry5_command","retry5_authority","retry5_token"):
  p=Path(plan[key]).resolve(strict=True)
  if sha(p)!=plan[key+"_sha256"]:raise Error(key+" changed after plan")
 rb,_=bundle(plan["retry5_bundle"],RETRY_ID,RETRY_SHA,"retry5");cb,cm=bundle(plan["collector_bundle"],COLLECTOR_ID,COLLECTOR_SHA,"collector")
 if sha(cb/ENTRY_REL)!=ENTRY_SHA or sha(cb/CORE_REL)!=CORE_SHA or (cm.get("files") or {}).get(CORE_REL)!=CORE_SHA:raise Error("collector changed after plan")
 return cb,Path(plan["qualification_output"]),Path(plan["host_metric"]),Path(plan["completion"])
def execute(a):
 pp,plan=load(a.plan,"plan");ap,auth=load(a.authority,"controller authority");ct=token(a.token);now=time.time()
 if auth.get("plan_sha256")!=sha(pp) or auth.get("token_sha256")!=hashlib.sha256(ct.encode()).hexdigest() or auth.get("unit")!=plan.get("unit") or not float(auth.get("not_before_epoch",0))<=now<float(auth.get("expires_epoch",0)):raise Error("controller authority differs or expired")
 cb,out,host,completion=execution_inputs(plan);receipt=Path(plan["receipt"]);receipt.parent.mkdir(mode=0o700)
 for p in (out,Path(str(out)+".sha256"),host,completion,Path(str(completion)+".sha256")):
  if p.exists() or p.is_symlink():raise Error("pre-launch output not fresh: "+str(p))
 try:
  r=subprocess.run(plan["launch_argv"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=600,env=dict(os.environ,PYTHONDONTWRITEBYTECODE="1"))
  if r.returncode:raise Error("retry5 launch failed rc="+str(r.returncode)+" stderr="+r.stderr[-1000:])
  mod=module(cb/ENTRY_REL);e=mod.core.validate_qualification_evidence(out,"qualification_solo");c=mod.core.validate_native_metric_producer_completion(host,plan["qualification_attempt_id"])
  if e.get("status")!="PASS_COMPLETE_8_STREAM_DEBUG_QUALIFICATION_NOT_GATE_AUTHORITY" or e.get("collector_sha256")!=COLLECTOR_SHA or e.get("qualification_attempt_id")!=plan["qualification_attempt_id"] or c.get("sample_count")!=181 or c.get("last_sequence")!=180:raise Error("retry5 PASS/completion differs")
  seal(receipt,{"schema_version":"mysql_tpch_retry5_systemd219_completion_v1","status":"PASS_RETRY5_PACED_181_SAMPLES_COMPLETED_ZERO_CREDIT","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"unit":plan["unit"],"qualification_attempt_id":plan["qualification_attempt_id"],"qualification_output":str(out),"qualification_output_sha256":sha(out),"host_metric_sha256":sha(host),"producer_completion_sha256":sha(completion),"collector_bundle_id":COLLECTOR_ID,"collector_manifest_sha256":COLLECTOR_SHA,"accepted_round_authorized":False,"accepted_rounds_credit":0});return 0
 except BaseException as exc:
  tomb=receipt.parent/"retry5.failure.json"
  if not tomb.exists():seal(tomb,{"schema_version":"mysql_tpch_retry5_systemd219_failure_v1","status":"FAILED_ZERO_CREDIT","error":str(exc),"unit":plan.get("unit"),"qualification_attempt_id":plan.get("qualification_attempt_id"),"accepted_round_authorized":False,"accepted_rounds_credit":0})
  raise
def wait(a):
 _,auth=load(a.authority,"controller authority");deadline=float(auth.get("expires_epoch",0))+30
 while time.time()<deadline:
  if a.receipt.exists():
   _,v=load(a.receipt,"completion receipt")
   if v.get("status")!="PASS_RETRY5_PACED_181_SAMPLES_COMPLETED_ZERO_CREDIT" or v.get("unit")!=a.unit:raise Error("completion receipt differs")
   print(json.dumps(v,sort_keys=True));return 0
  failure=a.receipt.parent/"retry5.failure.json"
  if failure.exists():
   _,v=load(failure,"failure receipt");raise Error("sealed retry5 failure: "+str(v.get("error")))
  r=subprocess.run(["/usr/bin/systemctl","show","--property=LoadState","--property=ActiveState","--property=SubState","--property=ExecMainStatus",a.unit],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=20,env=dict(os.environ,PYTHONDONTWRITEBYTECODE="1"))
  state=dict(x.split("=",1) for x in r.stdout.splitlines() if "=" in x)
  if r.returncode or state.get("LoadState")=="not-found" or state.get("ActiveState") in ("failed","inactive") or (state.get("SubState")=="exited" and state.get("ExecMainStatus") not in (None,"0")):raise Error("old systemd unit ended/disappeared without sealed retry5 completion: "+json.dumps(state,sort_keys=True))
  time.sleep(2)
 raise Error("timed out waiting for sealed retry5 completion")
def main():
 p=argparse.ArgumentParser();sp=p.add_subparsers(dest="mode",required=True);m=sp.add_parser("materialize")
 for n in ("retry5-bundle","retry5-materialization","collector-bundle","output-dir"):m.add_argument("--"+n,type=Path,required=True)
 e=sp.add_parser("execute");e.add_argument("--plan",type=Path,required=True);e.add_argument("--authority",type=Path,required=True);e.add_argument("--token",type=Path,required=True);w=sp.add_parser("wait");w.add_argument("--unit",required=True);w.add_argument("--receipt",type=Path,required=True);w.add_argument("--authority",type=Path,required=True);a=p.parse_args();return materialize(a) if a.mode=="materialize" else execute(a) if a.mode=="execute" else wait(a)
if __name__=="__main__":
 try:raise SystemExit(main())
 except (Error,OSError,UnicodeError,ValueError,KeyError,subprocess.TimeoutExpired) as e:print("NOT_READY: "+str(e),file=sys.stderr);raise SystemExit(2)
'''
TEST=r'''from pathlib import Path
B=Path(__file__).resolve().parents[1];S=(B/"controller/mysql_tpch_retry5_systemd219_completion_v1.py").read_text();H=(B/"fixtures/systemd-run-219.help.txt").read_text()
def test_systemd219_exact_old_compatible_argv():
 assert "--unit=UNIT" in H and "--property=NAME=VALUE" in H
 assert 'systemd=["/usr/bin/systemd-run","--unit",unit,sys.executable,"-B"' in S
 assert '"--property=Type=oneshot"' not in S and '"--property=RemainAfterExit=yes"' not in S
 assert '"systemd_run_wait_option_used":False' in S and '"systemd_run_collect_option_used":False' in S and '"unsupported_transient_properties_used":False' in S
def test_noexec_launcher_contract():
 assert '"launch_and_wait_argv":["/bin/sh",str(out/"launch_and_wait.sh")]' in S and 'export PYTHONDONTWRITEBYTECODE=1' in S
 assert '"direct_systemd_run_argv":systemd' in S and 'shell_launcher_direct_exec_prohibited":True' in S
def test_pass_requires_paced_evidence_and_181_completion():
 assert "validate_qualification_evidence" in S and "validate_native_metric_producer_completion" in S
 assert 'c.get("sample_count")!=181' in S and 'c.get("last_sequence")!=180' in S
def test_completion_receipt_is_only_wait_success():
 assert "PASS_RETRY5_PACED_181_SAMPLES_COMPLETED_ZERO_CREDIT" in S and "old systemd unit ended/disappeared without sealed retry5 completion" in S and 'failure=a.receipt.parent/"retry5.failure.json"' in S
'''
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sb(b):return hashlib.sha256(b).hexdigest()
def sf(p):
 d=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)
def build():
 content={
  "controller/mysql_tpch_retry5_systemd219_completion_v1.py":CONTROLLER.encode(),
  "tests/test_mysql_tpch_retry5_systemd219_completion_v1.py":TEST.encode(),
  "builder/build_mysql_tpch_retry5_systemd219_completion_v1.py":HERE.read_bytes(),
  "fixtures/systemd-run-219.help.txt":SYSTEMD219_HELP.encode(),
  "templates/materialize_command.json":canonical({"schema_version":"mysql_tpch_retry5_systemd219_materialize_command_v1","argv":["[SEALED_PYTHON]","[THIS_BUNDLE]/controller/mysql_tpch_retry5_systemd219_completion_v1.py","materialize","--retry5-bundle","[REMOTE_1E1906_BUNDLE]","--retry5-materialization","[1E1906_OUTPUT]/retry5.materialization.json","--collector-bundle","[REMOTE_EF511_BUNDLE]","--output-dir","[FRESH_ABSOLUTE_SYSTEMD_MATERIALS_DIR]"],"then_execute_argv":["/bin/sh","[FRESH_ABSOLUTE_SYSTEMD_MATERIALS_DIR]/launch_and_wait.sh"],"direct_systemd_run_argv_available_in":"[FRESH_ABSOLUTE_SYSTEMD_MATERIALS_DIR]/systemd219.argv.json","manual_json_edit_required":False,"systemd_219_compatible":True,"shell_direct_exec_prohibited_on_noexec_tmpfs":True})
 }
 files={n:sb(v) for n,v in sorted(content.items())}
 u={"schema_version":"mysql_tpch_retry5_systemd219_completion_bundle_v1","status":"PASS_IMMUTABLE_LOCAL_CONTROLLER_NOT_EXECUTED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"retry5_materializer_bundle_id":RETRY_ID,"retry5_materializer_manifest_sha256":RETRY_SHA,"collector_bundle_id":COLLECTOR_ID,"collector_manifest_sha256":COLLECTOR_SHA,"collector_entry_sha256":ENTRY_SHA,"collector_core_sha256":CORE_SHA,"systemd_version_floor":219,"systemd219_help_fixture_included":True,"systemd_run_wait_option_used":False,"systemd_run_collect_option_used":False,"unsupported_type_or_remain_after_exit_properties_used":False,"transient_service_direct_json_argv":True,"shell_launcher_invocation":"/bin/sh script","shell_direct_exec_prohibited_on_noexec_tmpfs":True,"old_unit_lifecycle_fail_closed":True,"completion_receipt_sidecar_required":True,"minimum_retry5_authority_remaining_seconds":900,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,"files":files};bid=sb(canonical(u));m=dict(u,bundle_id=bid);dst=OUT/bid
 if dst.exists():
  if sf(dst/"bundle_manifest.json")!=sb(canonical(m)):raise RuntimeError("existing differs")
  return dst
 OUT.mkdir(parents=True,exist_ok=True);stage=Path(tempfile.mkdtemp(prefix=".retry5-systemd219-",dir=OUT))
 try:
  for n,v in content.items():p=stage/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
  raw=canonical(m);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(sb(raw)+"  bundle_manifest.json\n");freeze(stage);os.replace(stage,dst)
 finally:
  if stage.exists():shutil.rmtree(stage)
 return dst
if __name__=="__main__":
 b=build();print(json.dumps({"bundle":str(b),"bundle_id":b.name,"manifest_sha256":sf(b/"bundle_manifest.json"),"controller_sha256":sf(b/"controller/mysql_tpch_retry5_systemd219_completion_v1.py"),"remote_actions":0},sort_keys=True))

#!/usr/bin/env python3
"""Build immutable retry4-PASS to fresh-R0 handoff materializer."""
from __future__ import annotations
import hashlib, json, os, shutil, tempfile
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]; HERE=Path(__file__).resolve()
OUT=ROOT/"artifacts/mysql_tpch_qualification_pass_fresh_r0_handoff_v1"
TARGET_ID="ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4"; TARGET_SHA="af1d0facca8b33e2638c0a2af6a751bececc407d483055ca064a34308d27bb52"
SUPPORT_ID="86232f80b4e274aadf1cdbd8acc19033786acfda8dafb6dc18f7bace4bdb61a3"; SUPPORT_SHA="2ef63e1ad38dc0217011317c1ed3177c47e6fb46380587482739f87eee761cf7"; ENTRY_SHA="5545dcb6277fd8979a4b6e1994bf7941aa8db19b4a369498bf22ae4b8c2412b1"; CORE_SHA="6cc0958fe0c485f0b5efa494b4e9b4868488a4fad07f06e4b11c7978f9f2594f"
POST_ID="6b3457e74f11119cd20c9afc23089da5c27177ccd4290df0eff13619e1df47b9"; POST_SHA="96a98a43405b914502851c66eadef4f16586517e6a83991fce5b0975c0efb97e"; RUNNER_SHA="3d3db6e4e178ec747a25e74e1ee4524ed4daefe7fbe9070f93b6ff0c2c6de50e"
ATTEMPT="tpch-v2-20260803t022500z-afbe6badff313374deadbeef"; DB="ra_mysql_tpch_v2_tpch_v2_20260803t022500z_afbe6badff313374deadbee_db"; CLIENT="ra_mysql_tpch_v2_tpch_v2_20260803t022500z_afbe6badff313374deadbee_client"
def canonical(v:Any)->bytes:return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sb(v:bytes)->str:return hashlib.sha256(v).hexdigest()
def sf(p:Path)->str:
 d=hashlib.sha256()
 with p.open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()

MATERIALIZER=rf'''#!/usr/bin/env python3
"""Materialize a one-use fresh R0 handoff; never execute the workload."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, os, secrets, shlex, stat, sys, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
TARGET_ID="{TARGET_ID}"; TARGET_SHA="{TARGET_SHA}"; SUPPORT_ID="{SUPPORT_ID}"; SUPPORT_SHA="{SUPPORT_SHA}"; ENTRY_SHA="{ENTRY_SHA}"; CORE_SHA="{CORE_SHA}"
POST_ID="{POST_ID}"; POST_SHA="{POST_SHA}"; RUNNER_SHA="{RUNNER_SHA}"; BOOTSTRAP_ATTEMPT="{ATTEMPT}"; DB="{DB}"; CLIENT="{CLIENT}"
ENTRY_REL="qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py"; CORE_REL="qualification/mysql_distributed_live_readback_qualification_v1.py"; RUNNER_REL="qualification/mysql_tpch_v2_fresh_r0_runner_v2.py"
class HandoffError(RuntimeError):pass
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sha256(p):
 d=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
def load(p,label):
 p=Path(p).resolve(strict=True);s=Path(str(p)+".sha256")
 if p.is_symlink() or not p.is_file() or s.is_symlink() or not s.is_file() or s.read_text(encoding="ascii",errors="strict")!=f"{{sha256(p)}}  {{p.name}}\n":raise HandoffError(label+" seal differs")
 v=json.loads(p.read_text(encoding="utf-8",errors="strict"))
 if not isinstance(v,dict):raise HandoffError(label+" must be object")
 return v
def verify_bundle(path,bid,msha,label):
 p=Path(path).resolve(strict=True);m=load(p/"bundle_manifest.json",label+" manifest")
 if p.name!=bid or m.get("bundle_id")!=bid or sha256(p/"bundle_manifest.json")!=msha or m.get("formal_scope")!="mysql_only" or m.get("cross_engine_comparison_prohibited") is not True or m.get("formal_launch_authorized") is not False:raise HandoffError(label+" differs")
 return p,m
def utc(v):
 try:x=datetime.fromisoformat(str(v).replace("Z","+00:00"))
 except ValueError as e:raise HandoffError("dynamic gate timestamp differs") from e
 if x.tzinfo is None:raise HandoffError("dynamic gate timezone differs")
 return x.astimezone(timezone.utc)
def gate_remaining(g,minimum,now=None):
 c,e=utc(g.get("captured_at_utc")),utc(g.get("expires_at_utc"));n=now or datetime.now(timezone.utc)
 if (g.get("schema_version")!="mysql_algorithm_c_distributed_dynamic_gate_evidence_v1" or g.get("status")!="PASS" or g.get("scenario")!="tpch_memory" or g.get("host_id")!="node04" or g.get("attempt_token")!=BOOTSTRAP_ATTEMPT or g.get("collector_sha256")!=SUPPORT_SHA or g.get("database_counter_missing_samples")!=0 or g.get("database_counter_timeout_count")!=0 or g.get("all_tolerances_passed") is not True or g.get("serial_fallback_required") is not False or g.get("accepted_round_authorized") is not False):raise HandoffError("dynamic gate authority differs")
 lifetime=(e-c).total_seconds();remaining=(e-n).total_seconds()
 if not 0<lifetime<=900 or n<c or remaining<minimum:raise HandoffError("dynamic gate is expired or has insufficient remaining lifetime")
 return remaining
def write_file(path,payload,mode=0o600):
 if path.exists() or path.is_symlink():raise HandoffError("refusing overwrite: "+str(path))
 with path.open("xb") as f:f.write(payload);f.flush();os.fsync(f.fileno())
 os.chmod(path,mode)
def seal(path,v):
 payload=canonical(v);write_file(path,payload);write_file(Path(str(path)+".sha256"),f"{{hashlib.sha256(payload).hexdigest()}}  {{path.name}}\n".encode("ascii"))
def fresh_attempt():return "tpch-fresh-r0-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+secrets.token_hex(16)
def amend(base,attempt,support_sha,gate_sha,root):
 value=json.loads(json.dumps(base));old=value.get("qualification_attempt_id");argv=list(value.get("argv") or []);ai=[i for i,x in enumerate(argv) if isinstance(x,str) and x.startswith("--attempt-id=")];ms=[i for i,x in enumerate(argv) if x=="--metric-stream"]
 if not isinstance(old,str) or len(ai)!=1 or len(ms)!=1 or ms[0]+1>=len(argv):raise HandoffError("retry4 command contract differs")
 host=root/(attempt+".host_metrics.jsonl");native_host=Path(value["measurement_output"]["host_bind_source"])/(attempt+".native.jsonl");native=str(Path(value["measurement_output"]["container_tmpfs_destination"])/(attempt+".native.jsonl"))
 for p in (host,native_host):
  if p.exists() or p.is_symlink():raise HandoffError("fresh R0 metric output is not fresh")
 argv[ai[0]]="--attempt-id="+attempt;argv[ms[0]+1]=native;value["argv"]=argv;value["qualification_attempt_id"]=attempt;value["workload_metric_stream"]={{"schema_version":"mysql_algorithm_c_debug_workload_metric_stream_v1","path":str(host)}};value.pop("retry4_materializer_binding",None)
 value["fresh_r0_handoff_binding"]={{"schema_version":"mysql_tpch_fresh_r0_handoff_binding_v1","predecessor_retry4_attempt_id":old,"predecessor_authority_token_and_outputs_reuse_prohibited":True,"support_collector_bundle_id":SUPPORT_ID,"support_collector_manifest_sha256":SUPPORT_SHA,"support_collector_entry_sha256":ENTRY_SHA,"support_collector_core_sha256":CORE_SHA,"dynamic_gate_sha256":gate_sha,"qualification_attempt_id":attempt,"accepted_round_authorized":False,"formal_r0_authorized":False}}
 return value,old,host,native_host
def import_entry(path):
 s=importlib.util.spec_from_file_location("handoff_support_entry",path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def main():
 p=argparse.ArgumentParser(description=__doc__)
 for n in ("bundle","support-bundle","postqualification-bundle","bootstrap","dynamic-gate","retry4-materialization","retry4-command","retry4-output","mysql-defaults-file","output-dir"):p.add_argument("--"+n,type=Path,required=True)
 p.add_argument("--valid-seconds",type=int,default=1200);p.add_argument("--minimum-gate-remaining-seconds",type=int,default=480);a=p.parse_args()
 if not 300<=a.valid_seconds<=3600 or not 240<=a.minimum_gate_remaining_seconds<=800:raise HandoffError("TTL policy differs")
 target,_=verify_bundle(a.bundle,TARGET_ID,TARGET_SHA,"target");support,sm=verify_bundle(a.support_bundle,SUPPORT_ID,SUPPORT_SHA,"support");post,_=verify_bundle(a.postqualification_bundle,POST_ID,POST_SHA,"postqualification")
 entry=support/ENTRY_REL;core=support/CORE_REL;runner=post/RUNNER_REL
 if sha256(entry)!=ENTRY_SHA or sha256(core)!=CORE_SHA or sha256(runner)!=RUNNER_SHA or (sm.get("files") or {{}}).get(ENTRY_REL)!=ENTRY_SHA:raise HandoffError("sealed execution closure differs")
 module=import_entry(entry)
 bootstrap=load(a.bootstrap,"bootstrap");gate=load(a.dynamic_gate,"dynamic gate");remaining=gate_remaining(gate,a.minimum_gate_remaining_seconds)
 if bootstrap.get("attempt_token")!=BOOTSTRAP_ATTEMPT or bootstrap.get("status")!="PASS_BOOTSTRAP_LIVE_NOT_R0_OR_FORMAL_AUTHORITY" or not isinstance(bootstrap.get("same_live_identity"),dict):raise HandoffError("bootstrap authority differs")
 material=load(a.retry4_materialization,"retry4 materialization");oldcmd=load(a.retry4_command,"retry4 command");passed=load(a.retry4_output,"retry4 PASS output");module.core.validate_qualification_evidence(a.retry4_output.resolve(),"qualification_solo");old=material.get("qualification_attempt_id")
 if (material.get("schema_version")!="mysql_tpch_qualification_retry4_materialization_v1" or material.get("status")!="SEALED_FRESH_RETRY4_NOT_LAUNCHED" or material.get("support_collector_bundle_id")!=SUPPORT_ID or material.get("formal_launch_authorized") is not False or material.get("accepted_rounds_credit")!=0 or oldcmd.get("qualification_attempt_id")!=old or Path((material.get("fresh_paths") or {{}}).get("command","")).resolve()!=a.retry4_command.resolve()):raise HandoffError("retry4 material binding differs")
 identity=bootstrap["same_live_identity"];initial=passed.get("initial_live_readback") or {{}};container=initial.get("container") or {{}};transport=initial.get("database_counter_transport") or {{}}
 if (passed.get("schema_version")!="mysql_algorithm_c_debug_qualification_evidence_v2" or passed.get("status")!="PASS_COMPLETE_8_STREAM_DEBUG_QUALIFICATION_NOT_GATE_AUTHORITY" or passed.get("mode")!="qualification_solo" or passed.get("bundle_id")!=TARGET_ID or passed.get("scenario")!="tpch_memory" or passed.get("hostname")!="node04" or passed.get("qualification_attempt_id")!=old or passed.get("collector_sha256")!=SUPPORT_SHA or passed.get("accepted_round_authorized") is not False or container.get("container_id")!=identity.get("database_container_id") or transport.get("client_container_id")!=identity.get("client_container_id")):raise HandoffError("retry4 PASS/same-live evidence differs")
 defaults=a.mysql_defaults_file.resolve(strict=True)
 if defaults.is_symlink() or not defaults.is_file() or stat.S_IMODE(defaults.stat().st_mode)&0o077:raise HandoffError("MySQL defaults file is not private")
 out=a.output_dir
 if not out.is_absolute() or out.exists() or out.is_symlink():raise HandoffError("fresh handoff output directory required")
 out.mkdir(parents=True,mode=0o700);os.chmod(out,0o700);attempt=fresh_attempt();metric_root=Path(oldcmd["measurement_output"]["host_tmpfs_directory"]);command,predecessor,host,native=amend(oldcmd,attempt,sha256(support/"bundle_manifest.json"),sha256(a.dynamic_gate),metric_root)
 command_path=out/"fresh_r0.command.json";authority_path=out/"fresh_r0.authority.json";token_path=out/"fresh_r0.token";r0_output=out/"tpch_fresh_r0.json";script_path=out/"run_fresh_r0.sh";launch_path=out/"fresh_r0.launch_argv.json";handoff_path=out/"fresh_r0.handoff.json"
 token=secrets.token_hex(48);write_file(token_path,(token+"\n").encode());seal(command_path,command);runtime=json.loads((target/"distributed_runtime_contract.json").read_text());module.validate_command_spec_without_static_pid(command_path,TARGET_ID,"qualification_solo",runtime["client_resource_envelope"])
 now=time.time();authority={{"schema_version":"mysql_algorithm_c_main_agent_qualification_authority_v1","authorized_by":"main_agent","bundle_id":TARGET_ID,"mode":"qualification_solo","qualification_attempt_id":attempt,"main_agent_token_sha256":hashlib.sha256(token.encode()).hexdigest(),"command_spec_sha256":sha256(command_path),"debug_only":True,"capture_authorized":False,"formal_r0_authorized":False,"formal_authorized":False,"accepted_round_authorized":False,"not_before_epoch":now-1.0,"expires_epoch":now+float(a.valid_seconds),"target_manifest_sha256":TARGET_SHA,"support_collector_bundle_id":SUPPORT_ID,"support_collector_manifest_sha256":SUPPORT_SHA,"dynamic_gate_sha256":sha256(a.dynamic_gate),"retry4_pass_sha256":sha256(a.retry4_output)}};seal(authority_path,authority);module.validate_main_agent_authority_without_static_pid(authority_path,token,TARGET_ID,"qualification_solo",command_path)
 launch=[sys.executable,str(runner),"--bundle",str(target),"--support-bundle",str(support),"--bootstrap",str(a.bootstrap.resolve()),"--dynamic-gate",str(a.dynamic_gate.resolve()),"--command-spec",str(command_path),"--main-agent-authority",str(authority_path),"--main-agent-token-file",str(token_path),"--database-container",DB,"--client-container",CLIENT,"--mysql-defaults-file",str(defaults),"--output",str(r0_output)]
 seal(launch_path,{{"schema_version":"mysql_tpch_fresh_r0_launch_argv_v1","argv":launch,"qualification_attempt_id":attempt,"formal_launch_authorized":False,"accepted_round_authorized":False}});write_file(script_path,("#!/bin/sh\nset -eu\nexec "+shlex.join(launch)+"\n").encode(),0o500)
 handoff={{"schema_version":"mysql_tpch_qualification_pass_fresh_r0_handoff_v1","status":"SEALED_FRESH_R0_HANDOFF_NOT_LAUNCHED","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"target_bundle_id":TARGET_ID,"support_collector_bundle_id":SUPPORT_ID,"support_collector_core_sha256":CORE_SHA,"postqualification_bundle_id":POST_ID,"qualification_attempt_id":attempt,"predecessor_retry4_attempt_id":predecessor,"retry4_pass_sha256":sha256(a.retry4_output),"dynamic_gate_sha256":sha256(a.dynamic_gate),"dynamic_gate_remaining_seconds_at_materialization":remaining,"fresh_paths":{{"command":str(command_path),"authority":str(authority_path),"token":str(token_path),"host_metric":str(host),"native_metric_host":str(native),"r0_output":str(r0_output),"r0_raw_output":str(r0_output)+".raw_qualification.json","launch_script":str(script_path)}},"retry4_authority_token_and_output_reuse_prohibited":True,"formal_launch_authorized":False,"formal_r0_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_execution_performed":False}};seal(handoff_path,handoff);print(json.dumps({{"status":handoff["status"],"qualification_attempt_id":attempt,"handoff":str(handoff_path),"launch_script":str(script_path),"gate_remaining_seconds":remaining,"remote_execution_performed":False}},sort_keys=True));return 0
if __name__=="__main__":
 try:raise SystemExit(main())
 except (HandoffError,OSError,UnicodeError,ValueError,KeyError) as e:print("NOT_READY: "+str(e),file=sys.stderr);raise SystemExit(2)
'''

TEST=r'''from datetime import datetime,timezone
import importlib.util
from pathlib import Path
import pytest
B=Path(__file__).resolve().parents[1];s=importlib.util.spec_from_file_location("h",B/"materializer/mysql_tpch_qualification_pass_fresh_r0_handoff_v1.py");H=importlib.util.module_from_spec(s);s.loader.exec_module(H)
def gate():return {"schema_version":"mysql_algorithm_c_distributed_dynamic_gate_evidence_v1","status":"PASS","scenario":"tpch_memory","host_id":"node04","attempt_token":H.BOOTSTRAP_ATTEMPT,"collector_sha256":H.SUPPORT_SHA,"database_counter_missing_samples":0,"database_counter_timeout_count":0,"all_tolerances_passed":True,"serial_fallback_required":False,"accepted_round_authorized":False,"captured_at_utc":"2026-08-03T00:00:00Z","expires_at_utc":"2026-08-03T00:15:00Z"}
def test_gate_requires_core_hash_and_remaining_lifetime():
 assert H.gate_remaining(gate(),480,datetime(2026,8,3,0,5,tzinfo=timezone.utc))==600
 bad=gate();bad["collector_sha256"]="x"*64
 with pytest.raises(H.HandoffError):H.gate_remaining(bad,480,datetime(2026,8,3,0,5,tzinfo=timezone.utc))
def test_fresh_command_drops_retry4_binding(tmp_path):
 root=tmp_path/"m";bind=root/"client_measurement";bind.mkdir(parents=True);base={"qualification_attempt_id":"retry4-"+"a"*40,"argv":["x","--attempt-id=old","--metric-stream","/measurement/old"],"measurement_output":{"host_bind_source":str(bind),"container_tmpfs_destination":"/measurement"},"retry4_materializer_binding":{}}
 value,old,host,native=H.amend(base,"fresh-"+"b"*40,"s"*64,"g"*64,root);assert old!=value["qualification_attempt_id"] and "retry4_materializer_binding" not in value and value["fresh_r0_handoff_binding"]["predecessor_authority_token_and_outputs_reuse_prohibited"] is True and not host.exists() and not native.exists()
def test_materializer_never_executes_remote_workload():
 source=(B/"materializer/mysql_tpch_qualification_pass_fresh_r0_handoff_v1.py").read_text();assert "subprocess" not in source and '"remote_execution_performed":False' in source and "run_fresh_r0.sh" in source
def test_exact_closure_bindings():
 source=(B/"materializer/mysql_tpch_qualification_pass_fresh_r0_handoff_v1.py").read_text();assert H.SUPPORT_ID in source and H.CORE_SHA in source and H.POST_ID in source and H.RUNNER_SHA in source
'''
def freeze(root:Path):
 for p in sorted(root.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(root,0o555)
def build()->Path:
 content={"materializer/mysql_tpch_qualification_pass_fresh_r0_handoff_v1.py":MATERIALIZER.encode(),"tests/test_mysql_tpch_qualification_pass_fresh_r0_handoff_v1.py":TEST.encode(),"builder/build_mysql_tpch_qualification_pass_fresh_r0_handoff_v1.py":HERE.read_bytes(),"templates/remote_command.json":canonical({"schema_version":"mysql_tpch_qualification_pass_fresh_r0_remote_command_v1","materialize_argv":["[SEALED_PYTHON]","[THIS_BUNDLE]/materializer/mysql_tpch_qualification_pass_fresh_r0_handoff_v1.py","--bundle","[REMOTE_FF313]","--support-bundle","[REMOTE_86232]","--postqualification-bundle","[REMOTE_BE747]","--bootstrap","[SEALED_BOOTSTRAP]","--dynamic-gate","[UNEXPIRED_86232_DYNAMIC_GATE]","--retry4-materialization","[RETRY4_MATERIALIZATION]","--retry4-command","[RETRY4_COMMAND]","--retry4-output","[RETRY4_PASS_OUTPUT]","--mysql-defaults-file","[PRIVATE_MYSQL_CNF]","--output-dir","[FRESH_HANDOFF_DIR]","--valid-seconds","1200","--minimum-gate-remaining-seconds","480"],"then_execute":"[FRESH_HANDOFF_DIR]/run_fresh_r0.sh","manual_json_edit_required":False,"materializer_executes_workload":False,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True})}
 files={n:sb(v) for n,v in sorted(content.items())};u={"schema_version":"mysql_tpch_qualification_pass_fresh_r0_handoff_bundle_v1","status":"PASS_IMMUTABLE_LOCAL_HANDOFF_NOT_LAUNCHED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"target_bundle_id":TARGET_ID,"target_manifest_sha256":TARGET_SHA,"support_collector_bundle_id":SUPPORT_ID,"support_collector_manifest_sha256":SUPPORT_SHA,"support_collector_entry_sha256":ENTRY_SHA,"support_collector_core_sha256":CORE_SHA,"postqualification_bundle_id":POST_ID,"postqualification_manifest_sha256":POST_SHA,"fresh_r0_runner_sha256":RUNNER_SHA,"retry4_pass_required":True,"unexpired_dynamic_gate_required":True,"manual_json_edit_required":False,"generated_launch_script":True,"materializer_executes_workload":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,"files":files};bid=sb(canonical(u));m=dict(u,bundle_id=bid);final=OUT/bid
 if final.exists():
  if sf(final/"bundle_manifest.json")!=sb(canonical(m)):raise RuntimeError("existing differs")
  return final
 OUT.mkdir(parents=True,exist_ok=True);stage=Path(tempfile.mkdtemp(prefix=".r0-handoff-",dir=OUT))
 try:
  for n,v in content.items():p=stage/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
  payload=canonical(m);(stage/"bundle_manifest.json").write_bytes(payload);(stage/"bundle_manifest.json.sha256").write_text(sb(payload)+"  bundle_manifest.json\n",encoding="ascii");freeze(stage);os.replace(stage,final)
 finally:
  if stage.exists():shutil.rmtree(stage)
 return final
if __name__=="__main__":
 b=build();print(json.dumps({"bundle":str(b),"bundle_id":b.name,"manifest_sha256":sf(b/"bundle_manifest.json"),"manual_json_edit_required":False,"remote_actions":0},sort_keys=True))

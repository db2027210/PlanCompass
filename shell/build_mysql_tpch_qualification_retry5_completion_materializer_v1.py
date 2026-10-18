#!/usr/bin/env python3
"""Build a non-executing retry5 materializer for completion-sealed support."""
from __future__ import annotations
import hashlib, json, os, shutil, tempfile
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]; HERE=Path(__file__).resolve()
OUT=ROOT/"artifacts/mysql_tpch_qualification_retry5_completion_materializer_v1"
TARGET_ID="b235d1ca300e5ad02fe77005096fc511bfbdc5d91b9601233a66a10502be5a47"; TARGET_SHA="386f59b23f20eb7be59665c42c44fe2453512bf2112d850e12df6b398a16f439"
SUPPORT_ID="a070e44ed4ca1acb0f5b4e0fe03386c6fc0dd43736d0f477cdd17a006bd465b6"; SUPPORT_SHA="a529c3aa35884314893afdd50b093a251dbf31c6a83d9ff797df052c960a7dce"; ENTRY_SHA="8cb1283f8cc0d08af3a4d8d218880e9eb6d61b5d7958e971b42a026462b02293"; CORE_SHA="ae6384be782bf4954419dfffbd25a7395719f1cb967bc2477d4414790e2b5317"

def canonical(v:Any)->bytes:return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sb(v:bytes)->str:return hashlib.sha256(v).hexdigest()
def sf(p:Path)->str:
 d=hashlib.sha256()
 with p.open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()

MATERIALIZER=rf'''#!/usr/bin/env python3
"""Materialize retry5 with finite 181-sample completion; never launch it."""
from __future__ import annotations
import argparse,hashlib,importlib.util,json,os,secrets,shlex,stat,sys,time
from datetime import datetime,timezone
from pathlib import Path
TARGET_ID="{TARGET_ID}";TARGET_SHA="{TARGET_SHA}";SUPPORT_ID="{SUPPORT_ID}";SUPPORT_SHA="{SUPPORT_SHA}";ENTRY_SHA="{ENTRY_SHA}";CORE_SHA="{CORE_SHA}"
ENTRY_REL="qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py";CORE_REL="qualification/mysql_distributed_live_readback_qualification_v1.py"
RETRY8_FAILURE={{"accepted_round_authorized":False,"accepted_rounds_credit":0,"error":"retry5 launch failed rc=2 stderr=NOT_READY: debug qualification workload did not exit gracefully within 120s\n","qualification_attempt_id":"tpch-qualification-retry5-20260803T062320Z-9b149cc799b77818cb102a7016525096","schema_version":"mysql_tpch_retry5_systemd219_failure_v1","status":"FAILED_ZERO_CREDIT","unit":"ra-mysql-tpch-retry5-971eda5d6b76480d.service"}}
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
 v=json.loads(p.read_text(encoding="utf-8"))
 if not isinstance(v,dict):raise Error(label+" object required")
 return v
def bundle(p,bid,msha,label):
 p=Path(p).resolve(strict=True);m=load(p/"bundle_manifest.json",label+" manifest")
 if p.name!=bid or m.get("bundle_id")!=bid or sha(p/"bundle_manifest.json")!=msha or m.get("formal_scope")!="mysql_only" or m.get("cross_engine_comparison_prohibited") is not True or m.get("formal_launch_authorized") is not False or m.get("accepted_round_authorized") is not False:raise Error(label+" differs")
 return p,m
def validate_retry8_failure(v):
 if v!=RETRY8_FAILURE:raise Error("predecessor failure evidence differs")
def write(p,b,mode=0o600):
 if p.exists() or p.is_symlink():raise Error("refusing overwrite: "+str(p))
 with p.open("xb") as f:f.write(b);f.flush();os.fsync(f.fileno())
 os.chmod(p,mode)
def seal(p,v):
 b=canonical(v);write(p,b);write(Path(str(p)+".sha256"),(hashlib.sha256(b).hexdigest()+"  "+p.name+"\n").encode("ascii"))
def attempt():return "tpch-qualification-retry5-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+secrets.token_hex(16)
def amend(base,new,support,metric_root):
 v=json.loads(json.dumps(base));old=v.get("qualification_attempt_id");argv=list(v.get("argv") or []);ai=[i for i,x in enumerate(argv) if isinstance(x,str) and x.startswith("--attempt-id=")];mi=[i for i,x in enumerate(argv) if x=="--metric-stream"];di=[i for i,x in enumerate(argv) if isinstance(x,str) and x.startswith("--duration-seconds=")]
 if not isinstance(old,str) or len(ai)!=1 or len(mi)!=1 or mi[0]+1>=len(argv) or len(di)!=1:raise Error("retry4 command contract differs")
 old_duration=argv[di[0]]
 if old_duration not in ("--duration-seconds=181","--duration-seconds=240"):raise Error("retry4 native duration is outside audited values")
 host=metric_root/(new+".host_metrics.jsonl");native_host=Path(v["measurement_output"]["host_bind_source"])/(new+".native.jsonl");native=str(Path(v["measurement_output"]["container_tmpfs_destination"])/(new+".native.jsonl"))
 for p in (host,native_host,Path(str(host)+".producer-complete-v1.json"),Path(str(host)+".producer-complete-v1.json.sha256")):
  if p.exists() or p.is_symlink():raise Error("retry5 output is not fresh: "+str(p))
 argv[ai[0]]="--attempt-id="+new;argv[mi[0]+1]=native;argv[di[0]]="--duration-seconds=240";v["argv"]=argv;v["qualification_attempt_id"]=new;v["workload_metric_stream"]={{"schema_version":"mysql_algorithm_c_debug_workload_metric_stream_v1","path":str(host)}};v.pop("retry4_materializer_binding",None)
 v["retry5_completion_binding"]={{"schema_version":"mysql_tpch_retry5_completion_binding_v1","predecessor_attempt_id":old,"predecessor_reuse_prohibited":True,"support_collector_bundle_id":SUPPORT_ID,"support_collector_manifest_sha256":SUPPORT_SHA,"support_collector_entry_sha256":ENTRY_SHA,"support_collector_core_sha256":CORE_SHA,"support_bundle_path":str(support),"native_duration_before":old_duration,"native_duration_after":"--duration-seconds=240","expected_sample_count":181,"first_sequence":0,"last_sequence":180,"producer_sequence_181_requested":False,"completion_sidecar_required":True,"completion_metric_stream_sha256_required":True,"missing_sample_threshold_relaxed":False,"timeout_threshold_relaxed":False,"freshness_threshold_relaxed":False,"accepted_round_authorized":False}}
 return v,old,host,native_host
def import_entry(p):s=importlib.util.spec_from_file_location("retry5_entry",p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def main():
 p=argparse.ArgumentParser(description=__doc__)
 for n in ("bundle","support-bundle","successor-command","retry8-failure-evidence","supplement","mysql-defaults-file","output-dir"):p.add_argument("--"+n,type=Path,required=True)
 p.add_argument("--valid-seconds",type=int,default=1800);a=p.parse_args()
 if not 300<=a.valid_seconds<=3600:raise Error("TTL outside 300..3600")
 target,_=bundle(a.bundle,TARGET_ID,TARGET_SHA,"target");support,sm=bundle(a.support_bundle,SUPPORT_ID,SUPPORT_SHA,"support");entry=support/ENTRY_REL;core=support/CORE_REL
 if sha(entry)!=ENTRY_SHA or sha(core)!=CORE_SHA or (sm.get("files") or {{}}).get(CORE_REL)!=CORE_SHA:raise Error("completion support closure differs")
 base=load(a.successor_command,"fresh successor command");failure=load(a.retry8_failure_evidence,"retry8 failure evidence");old=base.get("qualification_attempt_id");validate_retry8_failure(failure)
 supplement=load(a.supplement,"legacy blkio supplement")
 if supplement.get("status")!="PASS_LEGACY_AUTHORITY_PLUS_LIVE_READ_ONLY_SUPPLEMENT" or supplement.get("bundle_id")!=TARGET_ID or supplement.get("accepted_round_authorized") is not False:raise Error("supplement differs")
 bootstrap_attempt=supplement.get("attempt_token");db=supplement.get("database_container_name");client=(base.get("client_container") or {{}}).get("name")
 if not isinstance(bootstrap_attempt,str) or old!=bootstrap_attempt+"-qualification_solo" or not isinstance(db,str) or not isinstance(client,str) or db==client:raise Error("fresh bootstrap container binding differs")
 defaults=a.mysql_defaults_file.resolve(strict=True)
 if defaults.is_symlink() or not defaults.is_file() or stat.S_IMODE(defaults.stat().st_mode)&0o077:raise Error("MySQL defaults file must be private")
 out=a.output_dir
 if not out.is_absolute() or out.exists() or out.is_symlink():raise Error("fresh absolute retry5 output required")
 out.mkdir(parents=True,mode=0o700);new=attempt();metric_root=Path(base["measurement_output"]["host_tmpfs_directory"]);command,predecessor,host,native=amend(base,new,support,metric_root)
 cp=out/"retry5.command.json";ap=out/"retry5.authority.json";tp=out/"retry5.token";result=out/(new+".qualification.json");script=out/"run_retry5.sh";launchp=out/"retry5.launch_argv.json";materialp=out/"retry5.materialization.json"
 token=secrets.token_hex(48);write(tp,(token+"\n").encode());seal(cp,command);module=import_entry(entry);runtime=json.loads((target/"distributed_runtime_contract.json").read_text());module.validate_command_spec_without_static_pid(cp,TARGET_ID,"qualification_solo",runtime["client_resource_envelope"])
 now=time.time();authority={{"schema_version":"mysql_algorithm_c_main_agent_qualification_authority_v1","authorized_by":"main_agent","bundle_id":TARGET_ID,"mode":"qualification_solo","qualification_attempt_id":new,"main_agent_token_sha256":hashlib.sha256(token.encode()).hexdigest(),"command_spec_sha256":sha(cp),"debug_only":True,"capture_authorized":False,"formal_r0_authorized":False,"formal_authorized":False,"accepted_round_authorized":False,"not_before_epoch":now-1.0,"expires_epoch":now+a.valid_seconds,"target_manifest_sha256":TARGET_SHA,"support_collector_bundle_id":SUPPORT_ID,"support_collector_manifest_sha256":SUPPORT_SHA,"support_collector_entry_sha256":ENTRY_SHA,"support_collector_core_sha256":CORE_SHA,"retry4_failure_sha256":sha(a.retry8_failure_evidence)}};seal(ap,authority);module.validate_main_agent_authority_without_static_pid(ap,token,TARGET_ID,"qualification_solo",cp)
 launch=[sys.executable,"-B",str(entry),"--qualification-solo","--bundle",str(target),"--container",db,"--mysql-binary","/usr/bin/mysql","--mysql-client-container",client,"--mysql-binary-in-client","/usr/bin/mysql","--mysql-defaults-file",str(defaults),"--output",str(result),"--main-agent-token-file",str(tp),"--main-agent-authority",str(ap),"--command-spec",str(cp)];seal(launchp,{{"schema_version":"mysql_tpch_retry5_launch_argv_v1","argv":launch,"qualification_attempt_id":new,"bootstrap_attempt_token":bootstrap_attempt,"database_container":db,"client_container":client,"python_dont_write_bytecode":True,"formal_launch_authorized":False,"accepted_round_authorized":False}});write(script,("#!/bin/sh\nset -eu\nexport PYTHONDONTWRITEBYTECODE=1\nexec "+shlex.join(launch)+"\n").encode(),0o500)
 material={{"schema_version":"mysql_tpch_qualification_retry5_completion_materialization_v1","status":"SEALED_FRESH_RETRY5_NOT_LAUNCHED","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"target_bundle_id":TARGET_ID,"support_collector_bundle_id":SUPPORT_ID,"support_collector_manifest_sha256":SUPPORT_SHA,"support_collector_core_sha256":CORE_SHA,"qualification_attempt_id":new,"predecessor_retry4_attempt_id":predecessor,"retry4_failure_sha256":sha(a.retry8_failure_evidence),"expected_sample_count":181,"expected_last_sequence":180,"completion_sidecar_required":True,"completion_metric_stream_sha256_required":True,"missing_sample_threshold_relaxed":False,"timeout_threshold_relaxed":False,"freshness_threshold_relaxed":False,"fresh_paths":{{"command":str(cp),"authority":str(ap),"token":str(tp),"host_metric":str(host),"native_metric_host":str(native),"completion":str(host)+".producer-complete-v1.json","qualification_output":str(result),"launch_script":str(script)}},"predecessor_reuse_prohibited":True,"formal_launch_authorized":False,"formal_r0_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_execution_performed":False}};seal(materialp,material);print(json.dumps({{"status":material["status"],"qualification_attempt_id":new,"materialization":str(materialp),"launch_script":str(script),"remote_execution_performed":False}},sort_keys=True));return 0
if __name__=="__main__":
 try:raise SystemExit(main())
 except (Error,OSError,UnicodeError,ValueError,KeyError) as e:print("NOT_READY: "+str(e),file=sys.stderr);raise SystemExit(2)
'''

TEST=r'''import importlib.util
from pathlib import Path
import pytest
B=Path(__file__).resolve().parents[1];s=importlib.util.spec_from_file_location("m",B/"materializer/mysql_tpch_qualification_retry5_completion_materializer_v1.py");M=importlib.util.module_from_spec(s);s.loader.exec_module(M)
def base(tmp,duration=181):
 root=tmp/"measure";bind=root/"client_measurement";bind.mkdir(parents=True);return {"qualification_attempt_id":"retry4-"+"a"*40,"argv":["/readonly/adapter.py","--attempt-id=old","--metric-stream","/measurement/old.native.jsonl","--duration-seconds="+str(duration)],"measurement_output":{"host_tmpfs_directory":str(root),"host_bind_source":str(bind),"container_tmpfs_destination":"/measurement"},"retry4_materializer_binding":{}}
def test_amend_is_fresh_finite_completion_contract(tmp_path):
 v,old,host,native=M.amend(base(tmp_path),"retry5-"+"b"*40,tmp_path/"support",tmp_path/"measure");assert old!=v["qualification_attempt_id"] and "--duration-seconds=240" in v["argv"] and v["retry5_completion_binding"]["last_sequence"]==180 and v["retry5_completion_binding"]["producer_sequence_181_requested"] is False and "retry4_materializer_binding" not in v
def test_unreviewed_duration_fails_closed(tmp_path):
 with pytest.raises(M.Error,match="audited values"):M.amend(base(tmp_path,300),"retry5-"+"b"*40,tmp_path/"support",tmp_path/"measure")
def test_no_threshold_relaxation_and_no_execution():
 source=(B/"materializer/mysql_tpch_qualification_retry5_completion_materializer_v1.py").read_text();assert '"missing_sample_threshold_relaxed":False' in source and '"timeout_threshold_relaxed":False' in source and '"freshness_threshold_relaxed":False' in source and "subprocess" not in source and '"remote_execution_performed":False' in source and '[sys.executable,"-B",str(entry)' in source and 'export PYTHONDONTWRITEBYTECODE=1' in source
def test_exact_successor_closure():
 assert M.TARGET_ID=="b235d1ca300e5ad02fe77005096fc511bfbdc5d91b9601233a66a10502be5a47" and M.SUPPORT_ID=="a070e44ed4ca1acb0f5b4e0fe03386c6fc0dd43736d0f477cdd17a006bd465b6" and M.CORE_SHA=="ae6384be782bf4954419dfffbd25a7395719f1cb967bc2477d4414790e2b5317"
def test_exact_retry8_failure_is_accepted():
 M.validate_retry8_failure(dict(M.RETRY8_FAILURE))
@pytest.mark.parametrize("field,value",[("schema_version","other"),("status","PASS"),("qualification_attempt_id","other"),("unit","other.service"),("error","debug qualification workload did not exit gracefully within 120s"),("accepted_rounds_credit",1),("accepted_round_authorized",True)])
def test_retry8_failure_near_misses_are_rejected(field,value):
 failure=dict(M.RETRY8_FAILURE);failure[field]=value
 with pytest.raises(M.Error,match="predecessor failure evidence differs"):M.validate_retry8_failure(failure)
def test_retry8_failure_extra_authority_is_rejected():
 failure=dict(M.RETRY8_FAILURE);failure["formal_launch_authorized"]=False
 with pytest.raises(M.Error,match="predecessor failure evidence differs"):M.validate_retry8_failure(failure)
'''

def freeze(root:Path):
 for p in sorted(root.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(root,0o555)
def build()->Path:
 content={"materializer/mysql_tpch_qualification_retry5_completion_materializer_v1.py":MATERIALIZER.encode(),"tests/test_mysql_tpch_qualification_retry5_completion_materializer_v1.py":TEST.encode(),"builder/build_mysql_tpch_qualification_retry5_completion_materializer_v1.py":HERE.read_bytes(),"templates/remote_commands.json":canonical({"schema_version":"mysql_tpch_retry5_completion_remote_commands_v1","materialize_argv":["[SEALED_PYTHON]","[THIS_BUNDLE]/materializer/mysql_tpch_qualification_retry5_completion_materializer_v1.py","--bundle","[REMOTE_B235_DEADLINE_TARGET_BUNDLE]","--support-bundle","[REMOTE_ED11_DEADLINE_COLLECTOR_BUNDLE]","--successor-command","[FRESH_B235_BOOTSTRAP_QUALIFICATION_COMMAND_JSON]","--retry8-failure-evidence","[SEALED_RETRY8_FAILURE_EVIDENCE_JSON]","--supplement","[FRESH_B235_BOOTSTRAP_ATTEMPT_ROOT]/blkio_throttle_authority.supplement.json","--mysql-defaults-file","[FRESH_B235_BOOTSTRAP_ATTEMPT_ROOT]/credentials/mysql.cnf","--output-dir","[FRESH_ABSOLUTE_SUCCESSOR_DIRECTORY]","--valid-seconds","1800"],"then_execute":"[FRESH_ABSOLUTE_SUCCESSOR_DIRECTORY]/run_retry5.sh","fresh_successor_target_command_required":True,"failed_retry8_command_reuse_prohibited":True,"manual_json_edit_required":False,"materializer_executes_workload":False,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True})}
 files={n:sb(v) for n,v in sorted(content.items())};u={"schema_version":"mysql_tpch_qualification_retry5_completion_materializer_bundle_v1","status":"PASS_IMMUTABLE_LOCAL_MATERIALIZER_NOT_LAUNCHED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"target_bundle_id":TARGET_ID,"target_manifest_sha256":TARGET_SHA,"support_collector_bundle_id":SUPPORT_ID,"support_collector_manifest_sha256":SUPPORT_SHA,"support_collector_entry_sha256":ENTRY_SHA,"support_collector_core_sha256":CORE_SHA,"accepted_predecessor_failure_schema":"mysql_tpch_retry5_systemd219_failure_v1","accepted_predecessor_failure_status":"FAILED_ZERO_CREDIT","accepted_predecessor_failure_attempt":"tpch-qualification-retry5-20260803T062320Z-9b149cc799b77818cb102a7016525096","accepted_predecessor_failure_unit":"ra-mysql-tpch-retry5-971eda5d6b76480d.service","accepted_predecessor_failure_error":"retry5 launch failed rc=2 stderr=NOT_READY: debug qualification workload did not exit gracefully within 120s\n","predecessor_failure_exact_object_required":True,"predecessor_failure_input_sha256_bound_at_materialization":True,"fresh_successor_target_command_required":True,"failed_retry8_command_reuse_prohibited":True,"python_dont_write_bytecode":True,"python_interpreter_B_flag_required":True,"expected_sample_count":181,"expected_last_sequence":180,"producer_sequence_181_requested":False,"completion_sidecar_required":True,"completion_metric_stream_sha256_required":True,"missing_sample_threshold_relaxed":False,"timeout_threshold_relaxed":False,"freshness_threshold_relaxed":False,"manual_json_edit_required":False,"materializer_executes_workload":False,"generated_launch_script":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,"files":files};bid=sb(canonical(u));m=dict(u,bundle_id=bid);final=OUT/bid
 if final.exists():
  if sf(final/"bundle_manifest.json")!=sb(canonical(m)):raise RuntimeError("existing differs")
  return final
 OUT.mkdir(parents=True,exist_ok=True);stage=Path(tempfile.mkdtemp(prefix=".retry5-completion-",dir=OUT))
 try:
  for n,v in content.items():p=stage/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
  raw=canonical(m);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(sb(raw)+"  bundle_manifest.json\n",encoding="ascii");freeze(stage);os.replace(stage,final)
 finally:
  if stage.exists():shutil.rmtree(stage)
 return final
if __name__=="__main__":
 b=build();print(json.dumps({"bundle":str(b),"bundle_id":b.name,"manifest_sha256":sf(b/"bundle_manifest.json"),"manual_json_edit_required":False,"remote_actions":0},sort_keys=True))

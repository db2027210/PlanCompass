#!/usr/bin/env python3
"""Build exact da455 four-flow evidence to fresh TPC-H gate materializer."""
from __future__ import annotations
import hashlib,json,os,shutil,tempfile
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve();OUT=ROOT/"artifacts/mysql_tpch_da455_four_flow_gate_materializer_v1"
SUPPORT_ID="da455246652a82bb13d63f5f1ba6ebeb2e92fd08389e3f06707e5897e12646b6";SUPPORT_SHA="0f835d084459a2c66729327a9a4b9ed6ec070116e9873631874ac667039b34c4";ENTRY_SHA="5545dcb6277fd8979a4b6e1994bf7941aa8db19b4a369498bf22ae4b8c2412b1";CORE_SHA="ae8f22b8c4dab4f4e02e023122c72f21b7ebb244ba3d57e136b91468d7542863"
TPCH_ID="ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4";TPCC_ID="7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77";BOOTSTRAP_ATTEMPT="tpch-v2-20260803t022500z-afbe6badff313374deadbeef"
def canonical(v:Any)->bytes:return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sb(v:bytes)->str:return hashlib.sha256(v).hexdigest()
def sf(p:Path)->str:
 d=hashlib.sha256()
 with p.open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
MATERIALIZER=rf'''#!/usr/bin/env python3
"""Seal a fresh TPC-H dynamic gate from one exact da455 four-flow closure."""
from __future__ import annotations
import argparse,hashlib,importlib.util,json,os,secrets,shutil,time
from datetime import datetime,timedelta,timezone
from pathlib import Path
SUPPORT_ID="{SUPPORT_ID}";SUPPORT_SHA="{SUPPORT_SHA}";ENTRY_SHA="{ENTRY_SHA}";CORE_SHA="{CORE_SHA}";TPCH_ID="{TPCH_ID}";TPCC_ID="{TPCC_ID}";BOOTSTRAP_ATTEMPT="{BOOTSTRAP_ATTEMPT}";OLD_COLLECTOR_SHA="2ef63e1ad38dc0217011317c1ed3177c47e6fb46380587482739f87eee761cf7"
ENTRY_REL="qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py";CORE_REL="qualification/mysql_distributed_live_readback_qualification_v1.py";ROLES=(("A_solo","qualification_solo","tpch_memory",None),("A_paired","qualification_paired","tpch_memory","tpcc_cpu"),("B_solo","qualification_solo","tpcc_cpu",None),("B_paired","qualification_paired","tpcc_cpu","tpch_memory"))
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
def bundle(p):
 p=Path(p).resolve(strict=True);mp,m=sealed(p/"bundle_manifest.json","support manifest")
 if p.name!=SUPPORT_ID or m.get("bundle_id")!=SUPPORT_ID or sha(mp)!=SUPPORT_SHA or m.get("formal_scope")!="mysql_only" or m.get("cross_engine_comparison_prohibited") is not True or m.get("postgresql_mutation_prohibited") is not True or m.get("formal_launch_authorized") is not False or m.get("accepted_round_authorized") is not False:raise Error("support bundle differs")
 if sha(p/ENTRY_REL)!=ENTRY_SHA or sha(p/CORE_REL)!=CORE_SHA:raise Error("support execution closure differs")
 return p
def import_entry(p):s=importlib.util.spec_from_file_location("da455_gate_entry",p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def identity(v):
 c=v.get("container") or {{}};t=v.get("database_counter_transport") or {{}};db=c.get("container_id");client=t.get("client_container_id")
 if not isinstance(db,str) or len(db)!=64 or not isinstance(client,str) or len(client)!=64:raise Error("live identity absent")
 return {{"database_container_id":db,"client_container_id":client}}
def evidence_epoch(v):
 try:x=v["samples"][-1]["required_streams"]["workload_throughput_or_completion_rate"]["epoch_seconds"]
 except (KeyError,IndexError,TypeError) as e:raise Error("evidence latest epoch absent") from e
 if isinstance(x,bool) or not isinstance(x,(int,float)):raise Error("evidence latest epoch differs")
 return float(x)
def audit_shapes(rows,now,max_age):
 if set(rows)!=set(x[0] for x in ROLES):raise Error("exact four-flow closure required; solo is not a gate")
 epochs=[]
 for label,mode,scenario,peer in ROLES:
  v=rows[label]
  if v.get("status")!="PASS_COMPLETE_8_STREAM_DEBUG_QUALIFICATION_NOT_GATE_AUTHORITY" or v.get("mode")!=mode or v.get("scenario")!=scenario or v.get("peer_scenario")!=peer or v.get("hostname")!="node04" or v.get("collector_sha256")!=SUPPORT_SHA or v.get("collector_sha256")==OLD_COLLECTOR_SHA or v.get("accepted_round_authorized") is not False:raise Error(label+" role/collector/status differs")
  epochs.append(evidence_epoch(v))
 if len({{v.get("attempt_token_sha256") for v in rows.values()}})!=4:raise Error("four fresh authorizations required")
 ap=rows["A_paired"].get("paired_peer_workload_proof") or {{}};bp=rows["B_paired"].get("paired_peer_workload_proof") or {{}}
 if ap.get("qualification_attempt_id")!=rows["B_paired"].get("qualification_attempt_id") or bp.get("qualification_attempt_id")!=rows["A_paired"].get("qualification_attempt_id"):raise Error("paired mutual attempt binding differs")
 if max(epochs)-min(epochs)>900 or any(x>now+5 or now-x>max_age for x in epochs):raise Error("four-flow evidence is stale or outside one rolling window")
 ai=identity(rows["A_solo"]["initial_live_readback"]);bi=identity(rows["B_solo"]["initial_live_readback"])
 if identity(rows["A_paired"]["initial_live_readback"])!=ai or identity(rows["B_paired"]["initial_live_readback"])!=bi:raise Error("solo/paired same-live identity differs")
 if identity(rows["A_paired"].get("peer_live_readback") or {{}})!=bi or identity(rows["B_paired"].get("peer_live_readback") or {{}})!=ai:raise Error("paired peer live identity differs")
 return ai,bi,epochs
def fresh_readback(v,scenario,bid,want,now):
 if v.get("schema_version")!="mysql_algorithm_c_distributed_live_readback_v1" or v.get("status")!="PASS_LIVE_READBACK_NOT_LAUNCH_AUTHORITY" or v.get("scenario")!=scenario or v.get("bundle_id")!=bid or v.get("hostname")!="node04" or v.get("formal_scope")!="mysql_only" or v.get("cross_engine_comparison_prohibited") is not True or v.get("formal_launch_authorized") is not False or v.get("accepted_round_authorized") is not False or identity(v)!=want:raise Error(scenario+" current readback differs")
 epoch=v.get("collected_epoch")
 if isinstance(epoch,bool) or not isinstance(epoch,(int,float)) or not -5<=now-float(epoch)<=120:raise Error(scenario+" current readback stale")
def write(p,b,mode=0o600):
 if p.exists() or p.is_symlink():raise Error("refusing overwrite: "+str(p))
 with p.open("xb") as f:f.write(b);f.flush();os.fsync(f.fileno())
 os.chmod(p,mode)
def seal(p,v):
 b=canonical(v);write(p,b);write(Path(str(p)+".sha256"),(hashlib.sha256(b).hexdigest()+"  "+p.name+"\n").encode("ascii"))
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument("--support-bundle",type=Path,required=True);p.add_argument("--bootstrap",type=Path,required=True)
 for n in ("a-solo","a-paired","b-solo","b-paired","tpch-current-readback","tpcc-current-readback","output-dir"):p.add_argument("--"+n,type=Path,required=True)
 p.add_argument("--maximum-evidence-age-seconds",type=int,default=900);a=p.parse_args()
 if not 300<=a.maximum_evidence_age_seconds<=900:raise Error("evidence age policy differs")
 support=bundle(a.support_bundle);module=import_entry(support/ENTRY_REL);bp,bootstrap=sealed(a.bootstrap,"bootstrap")
 if bootstrap.get("status")!="PASS_BOOTSTRAP_LIVE_NOT_R0_OR_FORMAL_AUTHORITY" or bootstrap.get("attempt_token")!=BOOTSTRAP_ATTEMPT or not isinstance(bootstrap.get("same_live_identity"),dict):raise Error("bootstrap differs")
 paths={{"A_solo":a.a_solo.resolve(),"A_paired":a.a_paired.resolve(),"B_solo":a.b_solo.resolve(),"B_paired":a.b_paired.resolve()}}
 if len({{x.parent for x in paths.values()}})!=1 or {{x.name for x in paths.values()}}!={{k+".evidence.json" for k in paths}}:raise Error("four evidence files must be one exact run root")
 rows={{}}
 for label,mode,scenario,peer in ROLES:
  sealed(paths[label],label);rows[label]=module.core.validate_qualification_evidence(paths[label],mode)
 now=time.time();ai,bi,epochs=audit_shapes(rows,now,a.maximum_evidence_age_seconds);trp,tr=sealed(a.tpch_current_readback,"TPC-H current readback");crp,cr=sealed(a.tpcc_current_readback,"TPC-C current readback");fresh_readback(tr,"tpch_memory",TPCH_ID,ai,now);fresh_readback(cr,"tpcc_cpu",TPCC_ID,bi,now)
 bootid=bootstrap["same_live_identity"]
 if ai!={{"database_container_id":bootid.get("database_container_id"),"client_container_id":bootid.get("client_container_id")}}:raise Error("TPC-H four-flow identity differs from bootstrap")
 out=a.output_dir
 if not out.is_absolute() or out.exists() or out.is_symlink():raise Error("fresh absolute output directory required")
 stage=Path(tempfile.mkdtemp(prefix=".da455-gate-",dir=out.parent));final=out
 try:
  audit={{"schema_version":"mysql_tpch_da455_four_flow_gate_audit_v1","status":"PASS_EXACT_FOUR_FLOW_AND_CURRENT_LIVE_REUSE","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"support_collector_bundle_id":SUPPORT_ID,"support_collector_manifest_sha256":SUPPORT_SHA,"support_collector_core_sha256":CORE_SHA,"bootstrap_attempt_token":BOOTSTRAP_ATTEMPT,"four_evidence_sha256":{{k:sha(v) for k,v in paths.items()}},"four_latest_epoch_seconds":{{k:evidence_epoch(rows[k]) for k in rows}},"tpch_current_readback_sha256":sha(trp),"tpcc_current_readback_sha256":sha(crp),"current_tpcc_paired_flow_reusable":True,"solo_evidence_is_dynamic_gate":False,"old_86232_collector_accepted":False,"failed_evidence_accepted":False,"formal_launch_authorized":False,"formal_r0_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0}}
  raw=stage/"dynamic_gate.tpch.json.raw_evidence.json";seal(raw,audit);captured=datetime.now(timezone.utc);gate=module.core.compare_bidirectional_gate(paths["A_solo"],paths["A_paired"],paths["B_solo"],paths["B_paired"],primary_scenario="tpch_memory",host_id="node04",attempt_token=BOOTSTRAP_ATTEMPT,captured_at_utc=captured,expires_at_utc=captured+timedelta(minutes=15),raw_artifacts={{raw.name:sha(raw)}})
  if gate.get("status")!="PASS" or gate.get("collector_sha256")!=SUPPORT_SHA or gate.get("scenario")!="tpch_memory" or gate.get("attempt_token")!=BOOTSTRAP_ATTEMPT or gate.get("serial_fallback_required") is not False:raise Error("four-flow comparison did not produce PASS gate")
  gate["da455_four_flow_audit_sha256"]=sha(raw);gate["current_tpcc_paired_flow_reusable"]=True;gate["solo_evidence_is_dynamic_gate"]=False;seal(stage/"dynamic_gate.tpch.json",gate);os.chmod(stage,0o700);os.rename(stage,final)
 except BaseException:
  if stage.exists():shutil.rmtree(stage)
  raise
 print(json.dumps({{"status":"PASS_FRESH_DA455_FOUR_FLOW_DYNAMIC_GATE","gate":str(final/"dynamic_gate.tpch.json"),"gate_sha256":sha(final/"dynamic_gate.tpch.json"),"current_tpcc_paired_flow_reusable":True,"remote_actions":0}},sort_keys=True));return 0
if __name__=="__main__":
 try:raise SystemExit(main())
 except (Error,OSError,UnicodeError,ValueError,KeyError) as e:print("NOT_READY: "+str(e),file=__import__("sys").stderr);raise SystemExit(2)
'''
TEST=r'''import importlib.util
from pathlib import Path
import pytest
B=Path(__file__).resolve().parents[1];s=importlib.util.spec_from_file_location("g",B/"materializer/mysql_tpch_da455_four_flow_gate_materializer_v1.py");G=importlib.util.module_from_spec(s);s.loader.exec_module(G)
def ident(x):return {"container":{"container_id":x*64},"database_counter_transport":{"client_container_id":x.upper()*64}}
def rows(now=1000):
 out={};attempts={"A_paired":"apa","B_paired":"bpa"}
 for label,mode,scenario,peer in G.ROLES:
  v={"status":"PASS_COMPLETE_8_STREAM_DEBUG_QUALIFICATION_NOT_GATE_AUTHORITY","mode":mode,"scenario":scenario,"peer_scenario":peer,"hostname":"node04","collector_sha256":G.SUPPORT_SHA,"attempt_token_sha256":label[0].lower()*63+str(len(label)),"qualification_attempt_id":attempts.get(label,label+"x"*32),"accepted_round_authorized":False,"samples":[{"required_streams":{"workload_throughput_or_completion_rate":{"epoch_seconds":now}}}],"initial_live_readback":ident("a" if label.startswith("A") else "b")}
  if peer:v["peer_live_readback"]=ident("b" if label.startswith("A") else "a");v["paired_peer_workload_proof"]={"qualification_attempt_id":attempts["B_paired" if label=="A_paired" else "A_paired"]}
  out[label]=v
 return out
def test_exact_four_passes_shape_audit():assert G.audit_shapes(rows(),1000,900)[0]["database_container_id"]=="a"*64
def test_solo_alone_is_never_gate():
 with pytest.raises(G.Error,match="solo is not a gate"):G.audit_shapes({"A_solo":rows()["A_solo"]},1000,900)
def test_old_collector_and_failed_evidence_rejected():
 for mutate in (lambda r:r["A_solo"].update(collector_sha256=G.OLD_COLLECTOR_SHA),lambda r:r["A_solo"].update(status="FAIL")):
  r=rows();mutate(r)
  with pytest.raises(G.Error,match="role/collector/status"):G.audit_shapes(r,1000,900)
def test_paired_mutual_binding_required():
 r=rows();r["A_paired"]["paired_peer_workload_proof"]["qualification_attempt_id"]="wrong"
 with pytest.raises(G.Error,match="mutual"):G.audit_shapes(r,1000,900)
def test_stale_evidence_rejected():
 with pytest.raises(G.Error,match="stale"):G.audit_shapes(rows(1),1000,900)
def test_materializer_no_remote_or_workload_execution():
 source=(B/"materializer/mysql_tpch_da455_four_flow_gate_materializer_v1.py").read_text();assert "subprocess" not in source and "remote_actions\":0" in source and "solo_evidence_is_dynamic_gate\":False" in source
'''
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)
def build():
 content={"materializer/mysql_tpch_da455_four_flow_gate_materializer_v1.py":MATERIALIZER.encode(),"tests/test_mysql_tpch_da455_four_flow_gate_materializer_v1.py":TEST.encode(),"builder/build_mysql_tpch_da455_four_flow_gate_materializer_v1.py":HERE.read_bytes(),"templates/remote_command.json":canonical({"schema_version":"mysql_tpch_da455_four_flow_gate_remote_command_v1","materialize_argv":["[SEALED_PYTHON]","[THIS_BUNDLE]/materializer/mysql_tpch_da455_four_flow_gate_materializer_v1.py","--support-bundle","[REMOTE_DA455_BUNDLE]","--bootstrap","[SEALED_TPCH_BOOTSTRAP]","--a-solo","[ONE_RUN_ROOT]/A_solo.evidence.json","--a-paired","[ONE_RUN_ROOT]/A_paired.evidence.json","--b-solo","[ONE_RUN_ROOT]/B_solo.evidence.json","--b-paired","[ONE_RUN_ROOT]/B_paired.evidence.json","--tpch-current-readback","[FRESH_TPCH_COLLECT_ONLY_JSON]","--tpcc-current-readback","[FRESH_TPCC_COLLECT_ONLY_JSON]","--output-dir","[FRESH_ABSOLUTE_GATE_DIR]"],"manual_json_edit_required":False,"solo_is_not_gate":True,"remote_actions":0})};files={n:sb(v) for n,v in sorted(content.items())};u={"schema_version":"mysql_tpch_da455_four_flow_gate_materializer_bundle_v1","status":"PASS_IMMUTABLE_LOCAL_GATE_MATERIALIZER_NOT_EXECUTED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"support_collector_bundle_id":SUPPORT_ID,"support_collector_manifest_sha256":SUPPORT_SHA,"support_collector_entry_sha256":ENTRY_SHA,"support_collector_core_sha256":CORE_SHA,"exact_four_flow_required":True,"fresh_current_tpch_and_tpcc_readbacks_required":True,"old_86232_collector_accepted":False,"failed_evidence_accepted":False,"solo_evidence_is_dynamic_gate":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,"files":files};bid=sb(canonical(u));m=dict(u,bundle_id=bid);f=OUT/bid
 if f.exists():
  if sf(f/"bundle_manifest.json")!=sb(canonical(m)):raise RuntimeError("existing differs")
  return f
 OUT.mkdir(parents=True,exist_ok=True);st=Path(tempfile.mkdtemp(prefix=".da455-gate-",dir=OUT))
 try:
  for n,v in content.items():p=st/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
  raw=canonical(m);(st/"bundle_manifest.json").write_bytes(raw);(st/"bundle_manifest.json.sha256").write_text(sb(raw)+"  bundle_manifest.json\n");freeze(st);os.replace(st,f)
 finally:
  if st.exists():shutil.rmtree(st)
 return f
if __name__=="__main__":
 b=build();print(json.dumps({"bundle":str(b),"bundle_id":b.name,"manifest_sha256":sf(b/"bundle_manifest.json"),"remote_actions":0},sort_keys=True))

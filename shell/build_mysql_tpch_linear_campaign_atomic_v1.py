#!/usr/bin/env python3
"""Build the sealed raw-15/fixed-Top5/formal10 linear campaign closure."""
from __future__ import annotations
import hashlib,json,os,tempfile,shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve()
OUT=ROOT/"artifacts/mysql_tpch_linear_campaign_atomic_v1"
FF_ID="b235d1ca300e5ad02fe77005096fc511bfbdc5d91b9601233a66a10502be5a47";FF_SHA="386f59b23f20eb7be59665c42c44fe2453512bf2112d850e12df6b398a16f439"
LINEAR_ID="7c9a04c62e6d5d7ff056e1f2f23dd8971b34169b7d1e64d7aa42c1dc06d8e18d";LINEAR_SHA="55f8b763c7e30eb1a96a53e77e736f62edf54a0e5143d426e5d0e52cc1a14e35"
SUPPORT_ID="a070e44ed4ca1acb0f5b4e0fe03386c6fc0dd43736d0f477cdd17a006bd465b6";SUPPORT_SHA="a529c3aa35884314893afdd50b093a251dbf31c6a83d9ff797df052c960a7dce";SUPPORT_CORE="ae6384be782bf4954419dfffbd25a7395719f1cb967bc2477d4414790e2b5317"
MATERIALIZER_ID="e421ba0af5bb1568264196a6df5fd870bbbd7f4690d4f78e95298ae03353aea4";MATERIALIZER_SHA="b9c64fb674c78f5ffc983a2cb61ba4a811bb81a158fec0a4f1c7e507b0fa3d3e"
WRAPPER_ID="471be5c39dfa4bcda64245f5f3feda092f20235f099f4f969bbfc9843d3d75ed";WRAPPER_SHA="ded17dfd37f8c0d5b5db36dcbe70a09b985252565d8b43f00dd98945f02c681c"
ATTEMPT="tpch-v2-20260803t022500z-afbe6badff313374deadbeef"
OLD=ROOT/"artifacts/mysql_tpch_linear_formal_closure_v1"/LINEAR_ID

def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sb(b):return hashlib.sha256(b).hexdigest()
def sf(p):
 d=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()

COMMON=r'''from __future__ import annotations
import hashlib,json,math,os
from pathlib import Path
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
def atomic(p,v):
 p=Path(p);s=Path(str(p)+".sha256")
 if p.exists() or p.is_symlink() or s.exists() or s.is_symlink():raise Error("refusing overwrite: "+str(p))
 b=canonical(v);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open("xb") as f:f.write(b);f.flush();os.fsync(f.fileno())
 with s.open("xb") as f:f.write((hashlib.sha256(b).hexdigest()+"  "+p.name+"\n").encode());f.flush();os.fsync(f.fileno())
 os.chmod(p,0o600);os.chmod(s,0o600)
def finite(v,label):
 if isinstance(v,bool):raise Error(label+" finite required")
 try:x=float(v)
 except (TypeError,ValueError) as e:raise Error(label+" finite required") from e
 if not math.isfinite(x):raise Error(label+" finite required")
 return x
def has_qps(v):
 if isinstance(v,dict):return any("qps" in str(k).lower() or has_qps(x) for k,x in v.items())
 if isinstance(v,list):return any(has_qps(x) for x in v)
 return False
'''

EXECUTOR=COMMON+r'''
import argparse,importlib.util,itertools,subprocess,sys,time
FF_ID="__FF_ID__";FF_SHA="__FF_SHA__";PROTOCOL_ID="__PROTOCOL_ID__";PROTOCOL_SHA="__PROTOCOL_SHA__";SUPPORT_ID="__SUPPORT_ID__";SUPPORT_SHA="__SUPPORT_SHA__";SUPPORT_CORE="__SUPPORT_CORE__";MATERIALIZER_ID="__MATERIALIZER_ID__";MATERIALIZER_SHA="__MATERIALIZER_SHA__";WRAPPER_ID="__WRAPPER_ID__";WRAPPER_SHA="__WRAPPER_SHA__";ATTEMPT="__ATTEMPT__"
PARAMETERS=("io_block_read_cost","memory_block_read_cost","row_evaluate_cost","disk_temptable_row_cost")
def bundle(p,bid,msha,label):
 p=Path(p).resolve(strict=True);mp,m=load(p/"bundle_manifest.json",label+" manifest")
 if label=="protocol":
  unsigned=dict(m);unsigned.pop("bundle_id",None)
  if p.name!=m.get("bundle_id") or hashlib.sha256(canonical(unsigned)).hexdigest()!=m.get("bundle_id") or m.get("protocol_content_closure_sha256")!=bid or m.get("formal_scope")!="mysql_only" or m.get("formal_launch_authorized") is not False or m.get("accepted_round_authorized") is not False:raise Error("protocol bundle differs")
  return p,m
 if p.name!=bid or m.get("bundle_id")!=bid or sha(mp)!=msha or m.get("formal_scope")!="mysql_only" or m.get("cross_engine_comparison_prohibited") is not True or m.get("formal_launch_authorized") is not False or m.get("accepted_round_authorized") is not False:raise Error(label+" bundle differs")
 return p,m
def subsets():return [tuple(x) for n in range(1,5) for x in itertools.combinations(PARAMETERS,n)]
def subset_id(index,active):return "subset-%02d-%s"%(index,hashlib.sha256("\0".join(active).encode()).hexdigest()[:12])
def template(p,label,required):
 _,v=load(p,label);argv=v.get("argv");timeout=v.get("timeout_seconds")
 if v.get("schema_version")!="mysql_tpch_sealed_worker_template_v1" or not isinstance(argv,list) or not argv or not all(isinstance(x,str) and x for x in argv) or not isinstance(timeout,int) or not 1<=timeout<=7200:raise Error(label+" differs")
 joined="\0".join(argv)
 for key in required:
  if ("{"+key+"}") not in joined:raise Error(label+" misses "+key)
 return v
def render(t,values):
 try:argv=[x.format_map(values) for x in t["argv"]]
 except (KeyError,ValueError) as e:raise Error("worker template placeholder differs") from e
 if any("{" in x or "}" in x for x in argv):raise Error("unresolved worker placeholder")
 return argv
def verify_chain(plan):
 ff,_=bundle(plan["ff313_bundle"],FF_ID,FF_SHA,"ff313");pb,_=bundle(plan["protocol_bundle"],PROTOCOL_ID,PROTOCOL_SHA,"protocol");sb,sm=bundle(plan["support_bundle"],SUPPORT_ID,SUPPORT_SHA,"support");mb,mm=bundle(plan["materializer_bundle"],MATERIALIZER_ID,MATERIALIZER_SHA,"materializer");wb,wm=bundle(plan["wrapper_bundle"],WRAPPER_ID,WRAPPER_SHA,"wrapper")
 if sha(sb/"qualification/mysql_distributed_live_readback_qualification_v1.py")!=SUPPORT_CORE or mm.get("support_collector_bundle_id")!=SUPPORT_ID or wm.get("collector_bundle_id")!=SUPPORT_ID or wm.get("retry5_materializer_bundle_id")!=MATERIALIZER_ID:raise Error("1Hz support launch chain differs")
 return ff,pb,sb,mb,wb
def run_worker(t,values,label):
 output=Path(values["output"])
 if output.exists() or Path(str(output)+".sha256").exists():raise Error(label+" output not fresh")
 try:r=subprocess.run(render(t,values),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=t["timeout_seconds"])
 except subprocess.TimeoutExpired as e:raise Error(label+" technical timeout") from e
 if r.returncode:raise Error(label+" technical failure rc="+str(r.returncode)+" "+r.stderr[-500:])
 return load(output,label)[1]
def materialize(a):
 ff,_=bundle(a.ff313_bundle,FF_ID,FF_SHA,"ff313");pb,_=bundle(a.protocol_bundle,PROTOCOL_ID,PROTOCOL_SHA,"protocol");sb,sm=bundle(a.support_bundle,SUPPORT_ID,SUPPORT_SHA,"support");mb,mm=bundle(a.materializer_bundle,MATERIALIZER_ID,MATERIALIZER_SHA,"materializer");wb,wm=bundle(a.wrapper_bundle,WRAPPER_ID,WRAPPER_SHA,"wrapper")
 if sha(sb/"qualification/mysql_distributed_live_readback_qualification_v1.py")!=SUPPORT_CORE or mm.get("support_collector_bundle_id")!=SUPPORT_ID or wm.get("collector_bundle_id")!=SUPPORT_ID or wm.get("retry5_materializer_bundle_id")!=MATERIALIZER_ID:raise Error("1Hz chain differs")
 bp,b=load(a.bootstrap,"bootstrap");fp,f=load(a.fresh_r0,"fresh R0");ct=template(a.candidate_template,"candidate template",("subset_id","active_subset_json","output"));rt=template(a.replay_template,"replay template",("rank","subset_id","candidate_id","active_subset_json","output"))
 identity=b.get("same_live_identity")
 if b.get("attempt_token")!=ATTEMPT or f.get("attempt_token")!=ATTEMPT or f.get("same_live_identity")!=identity or f.get("support_collector_bundle_id")!=SUPPORT_ID or f.get("support_collector_manifest_sha256")!=SUPPORT_SHA or f.get("database_counter_missing_samples")!=0 or f.get("database_counter_timeout_count")!=0 or f.get("accepted_rounds_credit")!=0:raise Error("fresh same-live 1Hz R0 differs")
 out=a.output_dir
 if not out.is_absolute() or out.exists() or out.is_symlink():raise Error("fresh absolute output directory required")
 out.mkdir(parents=True,mode=0o700);run=out/"run"
 plan={"schema_version":"mysql_tpch_raw15_fixed_top5_plan_v1","status":"SEALED_NOT_EXECUTED","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"ff313_bundle":str(ff),"protocol_bundle":str(pb),"support_bundle":str(sb),"materializer_bundle":str(mb),"wrapper_bundle":str(wb),"bootstrap":str(bp),"bootstrap_sha256":sha(bp),"fresh_r0":str(fp),"fresh_r0_sha256":sha(fp),"candidate_template":str(Path(a.candidate_template).resolve()),"candidate_template_sha256":sha(a.candidate_template),"replay_template":str(Path(a.replay_template).resolve()),"replay_template_sha256":sha(a.replay_template),"run_directory":str(run),"attempt_token":ATTEMPT,"same_live_identity":identity,"subset_count":15,"fixed_top5_count":5,"linear_update_formula":"proposed=before*(1+eta*g_j)","qps_role":"outer_acceptance_after_real_replay_only","technical_failure_commit_credit":0,"accepted_round_authorized":False,"formal_launch_authorized":False};atomic(out/"raw15_top5.plan.json",plan);print(json.dumps({"status":plan["status"],"plan":str(out/"raw15_top5.plan.json")},sort_keys=True));return 0
def execute(a):
 pp,plan=load(a.plan,"plan");ff,pb,*_=verify_chain(plan)
 if plan.get("schema_version")!="mysql_tpch_raw15_fixed_top5_plan_v1" or plan.get("subset_count")!=15 or plan.get("fixed_top5_count")!=5 or plan.get("technical_failure_commit_credit")!=0:raise Error("raw plan differs")
 for key in ("bootstrap","fresh_r0","candidate_template","replay_template"):
  if sha(Path(plan[key]).resolve(strict=True))!=plan[key+"_sha256"]:raise Error(key+" changed after plan")
 _,bootstrap=load(plan["bootstrap"],"bootstrap");_,fresh=load(plan["fresh_r0"],"fresh R0");_,gate=load(a.dynamic_gate,"current dynamic gate");ct=template(plan["candidate_template"],"candidate template",("subset_id","active_subset_json","output"));rt=template(plan["replay_template"],"replay template",("rank","subset_id","candidate_id","active_subset_json","output"))
 run=Path(plan["run_directory"])
 if run.exists() or run.is_symlink():raise Error("run directory is not fresh")
 run.mkdir(parents=True,mode=0o700);identity=plan["same_live_identity"]
 try:
  raw=[]
  for index,active in enumerate(subsets(),1):
   sid=subset_id(index,active);out=run/"candidate"/(sid+".json");out.parent.mkdir(parents=True,exist_ok=True)
   row=run_worker(ct,{"subset_id":sid,"active_subset_json":json.dumps(list(active),separators=(",",":")),"output":str(out)},"candidate "+sid)
   if row.get("schema_version")!="mysql_tpch_raw_linear_candidate_v1" or row.get("attempt_token")!=ATTEMPT or row.get("same_live_identity")!=identity or row.get("subset_id")!=sid or tuple(row.get("active_subset") or ())!=active or row.get("accepted_round_authorized") is not False or has_qps(row):raise Error("candidate authority/QPS differs")
   raw.append(dict(row,_source_sha256=sha(out)))
  ranked=sorted(raw,key=lambda x:(-finite(x.get("candidate_score"),"candidate score"),str(x.get("candidate_id"))))
  if len({x.get("candidate_id") for x in ranked})!=15 or any(not isinstance(x.get("candidate_id"),str) or not x["candidate_id"] for x in ranked):raise Error("candidate identity differs")
  candidates=[]
  for rank,row in enumerate(ranked,1):
   clean={k:v for k,v in row.items() if not k.startswith("_")};clean["rank"]=rank;candidates.append(clean)
  pred={"schema_version":"mysql_tpch_raw15_linear_predictions_v1","attempt_token":ATTEMPT,"same_live_identity":identity,"qps_role":"outer_acceptance_after_real_replay_only","accepted_round_authorized":False,"candidates":candidates};predp=run/"predictions.json";atomic(predp,pred)
  replays=[]
  for rank,predrow in enumerate(candidates[:5],1):
   out=run/"replay"/("rank-%02d.json"%rank);out.parent.mkdir(parents=True,exist_ok=True)
   row=run_worker(rt,{"rank":str(rank),"subset_id":predrow["subset_id"],"candidate_id":predrow["candidate_id"],"active_subset_json":json.dumps(predrow["active_subset"],separators=(",",":")),"output":str(out)},"replay rank "+str(rank))
   if row.get("schema_version")!="mysql_tpch_fixed_top5_real_replay_v1" or row.get("rank")!=rank or row.get("subset_id")!=predrow["subset_id"] or row.get("candidate_id")!=predrow["candidate_id"] or row.get("active_subset")!=predrow["active_subset"] or row.get("same_live_identity")!=identity:raise Error("fixed Top5 identity differs")
   replays.append(row)
  real_order=sorted(range(5),key=lambda i:-finite((replays[i].get("outer_acceptance") or {}).get("qps_after"),"replay qps_after"));classification="no_issue" if real_order==list(range(5)) else "estimation"
  replaydoc={"schema_version":"mysql_tpch_fixed_top5_replays_v1","attempt_token":ATTEMPT,"same_live_identity":identity,"qps_role":"outer_acceptance_after_real_replay_only","accepted_round_authorized":False,"replays":replays,"prediction_vs_real_analysis":{"predicted_top5_count":5,"real_top5_count":5,"real_order_complete":True,"classification":classification}};replayp=run/"replays.json";atomic(replayp,replaydoc)
  ps=importlib.util.spec_from_file_location("campaign_producer",pb/"producer/mysql_tpch_linear_formal_debug_producer_v1.py");pm=importlib.util.module_from_spec(ps);ps.loader.exec_module(pm);debug=pm.build(ff,bootstrap,gate,fresh,pred,replaydoc);debugp=run/"debug_round.json";atomic(debugp,debug)
  receipt={"schema_version":"mysql_tpch_raw15_fixed_top5_completion_v1","status":"PASS_RAW15_FIXED_TOP5_DEBUG_ZERO_CREDIT","plan_sha256":sha(pp),"predictions":str(predp),"predictions_sha256":sha(predp),"replays":str(replayp),"replays_sha256":sha(replayp),"debug_round":str(debugp),"debug_round_sha256":sha(debugp),"support_bundle_id":SUPPORT_ID,"materializer_bundle_id":MATERIALIZER_ID,"wrapper_bundle_id":WRAPPER_ID,"same_live_identity":identity,"completed_nonempty_subsets":15,"fixed_top5_replayed":5,"technical_failure":False,"accepted_rounds_credit":0,"accepted_round_authorized":False,"formal_launch_authorized":False};atomic(run/"debug.completion.json",receipt);print(json.dumps({"status":receipt["status"],"receipt":str(run/"debug.completion.json")},sort_keys=True));return 0
 except BaseException as exc:
  tomb=run/"technical_failure.zero_commit.json"
  if not tomb.exists():atomic(tomb,{"schema_version":"mysql_tpch_campaign_technical_failure_v1","status":"FAILED_ZERO_COMMIT","error":str(exc),"accepted_rounds_committed":0,"accepted_round_authorized":False})
  raise
def main():
 p=argparse.ArgumentParser();sp=p.add_subparsers(dest="mode",required=True);m=sp.add_parser("materialize")
 for n in ("ff313-bundle","protocol-bundle","support-bundle","materializer-bundle","wrapper-bundle","bootstrap","fresh-r0","candidate-template","replay-template","output-dir"):m.add_argument("--"+n,type=Path,required=True)
 e=sp.add_parser("execute");e.add_argument("--plan",type=Path,required=True);e.add_argument("--dynamic-gate",type=Path,required=True);a=p.parse_args();return materialize(a) if a.mode=="materialize" else execute(a)
if __name__=="__main__":
 try:raise SystemExit(main())
 except (Error,OSError,UnicodeError,ValueError,KeyError,subprocess.TimeoutExpired) as e:print("NOT_READY: "+str(e),file=sys.stderr);raise SystemExit(2)
'''

CONTROLLER=COMMON+r'''
import argparse,importlib.util,subprocess,sys
from datetime import datetime,timezone
FF_ID="__FF_ID__";FF_SHA="__FF_SHA__";PROTOCOL_ID="__PROTOCOL_ID__";PROTOCOL_SHA="__PROTOCOL_SHA__";SUPPORT_ID="__SUPPORT_ID__";SUPPORT_SHA="__SUPPORT_SHA__";MATERIALIZER_ID="__MATERIALIZER_ID__";MATERIALIZER_SHA="__MATERIALIZER_SHA__";WRAPPER_ID="__WRAPPER_ID__";WRAPPER_SHA="__WRAPPER_SHA__";ATTEMPT="__ATTEMPT__"
PARAMETERS=("io_block_read_cost","memory_block_read_cost","row_evaluate_cost","disk_temptable_row_cost");RANGES={"io_block_read_cost":(.1,10.),"memory_block_read_cost":(.025,2.5),"row_evaluate_cost":(.01,1.),"disk_temptable_row_cost":(.05,5.)}
def bundle(p,bid,msha,label):
 p=Path(p).resolve(strict=True);mp,m=load(p/"bundle_manifest.json",label+" manifest")
 if label=="protocol":
  unsigned=dict(m);unsigned.pop("bundle_id",None)
  if p.name!=m.get("bundle_id") or hashlib.sha256(canonical(unsigned)).hexdigest()!=m.get("bundle_id") or m.get("protocol_content_closure_sha256")!=bid or m.get("formal_scope")!="mysql_only" or m.get("formal_launch_authorized") is not False or m.get("accepted_round_authorized") is not False:raise Error("protocol bundle differs")
  return p,m
 if p.name!=bid or m.get("bundle_id")!=bid or sha(mp)!=msha or m.get("formal_scope")!="mysql_only" or m.get("cross_engine_comparison_prohibited") is not True or m.get("formal_launch_authorized") is not False or m.get("accepted_round_authorized") is not False:raise Error(label+" bundle differs")
 return p,m
def template(p):
 _,v=load(p,"formal attempt template");argv=v.get("argv");joined="\0".join(argv or [])
 if v.get("schema_version")!="mysql_tpch_sealed_worker_template_v1" or not isinstance(argv,list) or not all(isinstance(x,str) and x for x in argv) or not isinstance(v.get("timeout_seconds"),int) or not 1<=v["timeout_seconds"]<=7200 or any(("{"+x+"}") not in joined for x in ("attempt_index","accepted_round","output")):raise Error("formal attempt template differs")
 return v
def render(t,v):
 try:a=[x.format_map(v) for x in t["argv"]]
 except (KeyError,ValueError) as e:raise Error("formal template placeholder differs") from e
 if any("{" in x or "}" in x for x in a):raise Error("unresolved formal placeholder")
 return a
def imports(pb):
 vp=pb/"verifier/tpch_formal10_linear_readiness_v1.py";before=list(sys.path);sys.path.insert(0,str(vp.parent))
 try:s=importlib.util.spec_from_file_location("campaign_linear_verifier",vp);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
 finally:sys.path[:]=before
def technical(run,message,accepted=0):
 tomb=Path(run)/"technical_failure.zero_commit.json"
 if not tomb.exists():atomic(tomb,{"schema_version":"mysql_tpch_formal10_technical_failure_v1","status":"FAILED_CURRENT_ATTEMPT_ZERO_COMMIT","error":str(message),"accepted_rounds_already_committed":accepted,"technical_failure_commit_credit":0,"accepted_round_authorized":False})
 return Error(str(message))
def state(v,label):
 if not isinstance(v,dict) or set(v)!=set(PARAMETERS):raise Error(label+" parameter set differs")
 out={k:finite(v[k],label+"."+k) for k in PARAMETERS}
 if any(not RANGES[k][0]<=out[k]<=RANGES[k][1] for k in PARAMETERS):raise Error(label+" range differs")
 return out
def validate_attempt(v,identity,verifier,expected_state):
 outer=v.get("outer_acceptance");without=dict(v);without.pop("outer_acceptance",None)
 if v.get("schema_version")!="mysql_tpch_linear_formal_attempt_v1" or v.get("status")!="PASS_FORMAL_ATTEMPT" or v.get("attempt_token")!=ATTEMPT or v.get("same_live_identity")!=identity or v.get("technical_failure") is not False or v.get("database_counter_missing_samples")!=0 or v.get("database_counter_timeout_count")!=0 or not isinstance(v.get("database_counter_sample_count"),int) or v["database_counter_sample_count"]<181 or has_qps(without) or not isinstance(outer,dict) or set(outer)!={"qps_before","qps_after"}:raise Error("formal attempt authority/QPS/counter differs")
 verifier.verify_gradient_event(v.get("gradient_event") or {},"formal_attempt.gradient_event");verifier._verify_fingerprint(v.get("three_level_fingerprint") or {},"formal_attempt.fingerprint")
 before_state=state(v.get("parameter_state_before"),"parameter_state_before");after_state=state(v.get("parameter_state_after"),"parameter_state_after")
 if before_state!=expected_state:raise Error("formal attempt is not chained to committed state")
 deltas=(v.get("gradient_event") or {}).get("delta_param") or {}
 for name in PARAMETERS:
  if name in deltas:
   if finite(deltas[name].get("before"),"delta before")!=before_state[name] or finite(deltas[name].get("after_clip"),"delta after")!=after_state[name]:raise Error("formal parameter transition differs")
  elif after_state[name]!=before_state[name]:raise Error("inactive formal parameter changed")
 gate=v.get("rolling_gate") or {}
 if gate.get("status")!="PASS" or gate.get("attempt_token")!=ATTEMPT or gate.get("collector_sha256")!=SUPPORT_SHA or gate.get("database_counter_missing_samples")!=0 or gate.get("database_counter_timeout_count")!=0 or gate.get("accepted_round_authorized") is not False:raise Error("formal attempt rolling gate differs")
 try:c=datetime.fromisoformat(str(gate["captured_at_utc"]).replace("Z","+00:00"));e=datetime.fromisoformat(str(gate["expires_at_utc"]).replace("Z","+00:00"));start=datetime.fromisoformat(str(v["measurement_started_at_utc"]).replace("Z","+00:00"));end=datetime.fromisoformat(str(v["measurement_ended_at_utc"]).replace("Z","+00:00"))
 except (KeyError,ValueError) as x:raise Error("formal attempt gate timestamp differs") from x
 if any(x.tzinfo is None for x in (c,e,start,end)) or not 0<(e-c).total_seconds()<=900 or not c<=start<end<=e or not end<=datetime.now(timezone.utc)<e:raise Error("formal attempt rolling gate window differs")
 before=finite(outer["qps_before"],"qps_before");after=finite(outer["qps_after"],"qps_after");accepted=after>before
 if v.get("accepted_round_authorized") is not False:raise Error("outer acceptance authority differs")
 return accepted,before,after,after_state
def materialize(a):
 ff,_=bundle(a.ff313_bundle,FF_ID,FF_SHA,"ff313");pb,_=bundle(a.protocol_bundle,PROTOCOL_ID,PROTOCOL_SHA,"protocol");sb,_=bundle(a.support_bundle,SUPPORT_ID,SUPPORT_SHA,"support");mb,mm=bundle(a.materializer_bundle,MATERIALIZER_ID,MATERIALIZER_SHA,"materializer");wb,wm=bundle(a.wrapper_bundle,WRAPPER_ID,WRAPPER_SHA,"wrapper")
 if mm.get("support_collector_bundle_id")!=SUPPORT_ID or wm.get("collector_bundle_id")!=SUPPORT_ID or wm.get("retry5_materializer_bundle_id")!=MATERIALIZER_ID:raise Error("launch chain differs")
 paths={};vals={}
 for key,path in (("spec",a.spec),("bootstrap",a.bootstrap),("r0_gate",a.r0_gate),("fresh_r0",a.fresh_r0),("debug_receipt",a.debug_receipt),("initial_parameters",a.initial_parameters)):
  p,v=load(path,key);paths[key]=str(p);paths[key+"_sha256"]=sha(p);vals[key]=v
 t=template(a.attempt_template);tp=Path(a.attempt_template).resolve();identity=vals["bootstrap"].get("same_live_identity");receipt=vals["debug_receipt"]
 if receipt.get("status")!="PASS_RAW15_FIXED_TOP5_DEBUG_ZERO_CREDIT" or receipt.get("completed_nonempty_subsets")!=15 or receipt.get("fixed_top5_replayed")!=5 or receipt.get("support_bundle_id")!=SUPPORT_ID or receipt.get("same_live_identity")!=identity:raise Error("debug receipt differs")
 initial=vals["initial_parameters"]
 if initial.get("schema_version")!="mysql_tpch_linear_initial_parameter_state_v1" or initial.get("attempt_token")!=ATTEMPT or initial.get("same_live_identity")!=identity or initial.get("fresh_r0_sha256")!=sha(fp) or initial.get("accepted_round_authorized") is not False:raise Error("initial parameter authority differs")
 state(initial.get("parameters"),"initial parameters")
 out=a.output_dir
 if not out.is_absolute() or out.exists() or out.is_symlink():raise Error("fresh absolute formal output required")
 out.mkdir(parents=True,mode=0o700);plan={"schema_version":"mysql_tpch_linear_formal10_atomic_plan_v1","status":"SEALED_NOT_EXECUTED","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"ff313_bundle":str(ff),"protocol_bundle":str(pb),"support_bundle":str(sb),"materializer_bundle":str(mb),"wrapper_bundle":str(wb),**paths,"attempt_template":str(tp),"attempt_template_sha256":sha(tp),"output_directory":str(out/"run"),"same_live_identity":identity,"attempt_token":ATTEMPT,"target_accepted_rounds":10,"max_attempts":a.max_attempts,"linear_update_formula":"proposed=before*(1+eta*g_j)","qps_role":"outer_acceptance_after_real_replay_only","technical_failure_commit_credit":0,"accepted_round_authorized":False,"formal_launch_authorized":False};atomic(out/"formal10.plan.json",plan);print(json.dumps({"status":plan["status"],"plan":str(out/"formal10.plan.json")},sort_keys=True));return 0
def execute(a):
 pp,p=load(a.plan,"formal plan");ff,_=bundle(p["ff313_bundle"],FF_ID,FF_SHA,"ff313");pb,_=bundle(p["protocol_bundle"],PROTOCOL_ID,PROTOCOL_SHA,"protocol");bundle(p["support_bundle"],SUPPORT_ID,SUPPORT_SHA,"support");bundle(p["materializer_bundle"],MATERIALIZER_ID,MATERIALIZER_SHA,"materializer");bundle(p["wrapper_bundle"],WRAPPER_ID,WRAPPER_SHA,"wrapper")
 for key in ("spec","bootstrap","r0_gate","fresh_r0","debug_receipt","initial_parameters","attempt_template"):
  if sha(Path(p[key]).resolve(strict=True))!=p[key+"_sha256"]:raise Error(key+" changed after plan")
 _,current=load(a.dynamic_gate,"current gate");run=Path(p["output_directory"])
 if run.exists() or run.is_symlink():raise Error("formal run directory is not fresh")
 run.mkdir(parents=True,mode=0o700);debug=load(load(p["debug_receipt"],"debug receipt")[1]["debug_round"],"debug round")[0];handoff=run/"formal10.handoff.json";authority=pb/"authority/mysql_tpch_linear_same_live_formal10_authority_v1.py"
 argv=[sys.executable,str(authority),"--bundle",str(ff),"--spec",p["spec"],"--bootstrap",p["bootstrap"],"--r0-gate",p["r0_gate"],"--dynamic-gate",str(Path(a.dynamic_gate).resolve()),"--fresh-r0",p["fresh_r0"],"--debug-round",str(debug),"--output",str(handoff)];r=subprocess.run(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=120)
 if r.returncode:raise technical(run,"formal handoff technical failure "+r.stderr[-500:])
 _,h=load(handoff,"formal handoff")
 if h.get("status")!="READY_FORMAL10_SAME_LIVE_LINEAR_ORIGINAL_SEARCH" or h.get("formal10_authorized") is not True or h.get("accepted_round_authorized") is not False or h.get("same_live_identity")!=p["same_live_identity"]:raise technical(run,"formal handoff differs")
 committed_state=state(load(p["initial_parameters"],"initial parameters")[1].get("parameters"),"initial parameters");accepted=0
 try:
  t=template(p["attempt_template"]);verifier=imports(pb)
  for attempt_index in range(1,p["max_attempts"]+1):
   if accepted==10:break
   out=run/"attempts"/("attempt-%03d.json"%attempt_index);out.parent.mkdir(parents=True,exist_ok=True)
   try:r=subprocess.run(render(t,{"attempt_index":str(attempt_index),"accepted_round":str(accepted+1),"output":str(out)}),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=t["timeout_seconds"])
   except subprocess.TimeoutExpired as e:raise Error("formal attempt technical timeout") from e
   if r.returncode:raise Error("formal attempt technical failure rc="+str(r.returncode)+" "+r.stderr[-500:])
   _,v=load(out,"formal attempt");is_accepted,before,after,next_state=validate_attempt(v,p["same_live_identity"],verifier,committed_state)
   if not is_accepted:
    atomic(run/"rejections"/("attempt-%03d.json"%attempt_index),{"schema_version":"mysql_tpch_linear_formal_rejection_v1","status":"REJECTED_OUTER_QPS_ZERO_COMMIT","attempt_index":attempt_index,"attempt_sha256":sha(out),"outer_acceptance":{"qps_before":before,"qps_after":after},"accepted_rounds_credit":0,"accepted_round_authorized":False});continue
   accepted+=1;atomic(run/"commits"/("accepted-round-%02d.json"%accepted),{"schema_version":"mysql_tpch_linear_formal_atomic_commit_v1","status":"COMMITTED_ACCEPTED_ROUND","accepted_round":accepted,"attempt_index":attempt_index,"attempt_sha256":sha(out),"linear_update_formula":"proposed=before*(1+eta*g_j)","parameter_state_before":committed_state,"parameter_state_after":next_state,"outer_acceptance":{"qps_before":before,"qps_after":after},"commit_reason":"outer_acceptance_strict_improvement_after_real_replay","database_counter_missing_samples":0,"database_counter_timeout_count":0,"technical_failure":False,"accepted_round_authorized":True});committed_state=next_state
  if accepted!=10:raise Error("max attempts exhausted before 10 accepted rounds")
  final={"schema_version":"mysql_tpch_linear_formal10_atomic_completion_v1","status":"PASS_FORMAL10_ATOMIC_10_ACCEPTED","handoff_sha256":sha(handoff),"accepted_rounds":10,"target_accepted_rounds":10,"technical_failure_commits":0,"support_bundle_id":SUPPORT_ID,"materializer_bundle_id":MATERIALIZER_ID,"wrapper_bundle_id":WRAPPER_ID,"linear_update_formula":"proposed=before*(1+eta*g_j)","qps_role":"outer_acceptance_after_real_replay_only","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True};atomic(run/"formal10.completion.json",final);print(json.dumps({"status":final["status"],"completion":str(run/"formal10.completion.json")},sort_keys=True));return 0
 except BaseException as exc:
  tomb=run/"technical_failure.zero_commit.json"
  if not tomb.exists():technical(run,exc,accepted)
  raise
def main():
 p=argparse.ArgumentParser();sp=p.add_subparsers(dest="mode",required=True);m=sp.add_parser("materialize")
 for n in ("ff313-bundle","protocol-bundle","support-bundle","materializer-bundle","wrapper-bundle","spec","bootstrap","r0-gate","fresh-r0","debug-receipt","initial-parameters","attempt-template","output-dir"):m.add_argument("--"+n,type=Path,required=True)
 m.add_argument("--max-attempts",type=int,default=40,choices=range(10,101));e=sp.add_parser("execute");e.add_argument("--plan",type=Path,required=True);e.add_argument("--dynamic-gate",type=Path,required=True);a=p.parse_args();return materialize(a) if a.mode=="materialize" else execute(a)
if __name__=="__main__":
 try:raise SystemExit(main())
 except (Error,OSError,UnicodeError,ValueError,KeyError,subprocess.TimeoutExpired) as e:print("NOT_READY: "+str(e),file=sys.stderr);raise SystemExit(2)
'''

AUTH=(OLD/"authority/mysql_tpch_linear_same_live_formal10_authority_v1.py").read_text()
AUTH=AUTH.replace('LINEAR_VERIFIER_SHA="25cacfb7c04dd648d849f664dfea65912d857f39953025a4128b029f46ce27bd"','LINEAR_VERIFIER_SHA="25cacfb7c04dd648d849f664dfea65912d857f39953025a4128b029f46ce27bd";SUPPORT_SHA="'+SUPPORT_SHA+'"')
AUTH=AUTH.replace('for n in ("bundle","spec","bootstrap","dynamic-gate","fresh-r0","debug-round","output")','for n in ("bundle","spec","bootstrap","r0-gate","dynamic-gate","fresh-r0","debug-round","output")')
AUTH=AUTH.replace('_,spec=load(a.spec,"spec");_,bootstrap=load(a.bootstrap,"bootstrap");_,gate=load(a.dynamic_gate,"gate");_,fresh=load(a.fresh_r0,"fresh R0");_,debug=load(a.debug_round,"debug")','_,spec=load(a.spec,"spec");_,bootstrap=load(a.bootstrap,"bootstrap");_,r0gate=load(a.r0_gate,"R0 gate");_,gate=load(a.dynamic_gate,"current gate");_,fresh=load(a.fresh_r0,"fresh R0");_,debug=load(a.debug_round,"debug")')
AUTH=AUTH.replace('base=authority.build_handoff(spec,bootstrap,gate,fresh,None,False);verifier=', 'base=authority.build_handoff(spec,bootstrap,r0gate,fresh,None,False);producer=import_file(protocol/"producer/mysql_tpch_linear_formal_debug_producer_v1.py","linear_current_gate");producer.gate(gate);verifier=')
AUTH=AUTH.replace('"debug_round_sha256":hashlib.sha256(canonical(debug)).hexdigest(),','"debug_round_sha256":hashlib.sha256(canonical(debug)).hexdigest(),"r0_dynamic_gate_sha256":hashlib.sha256(canonical(r0gate)).hexdigest(),"dynamic_gate_sha256":hashlib.sha256(canonical(gate)).hexdigest(),"support_collector_manifest_sha256":SUPPORT_SHA,')

TEST=r'''import importlib.util,json,math
from datetime import datetime,timezone,timedelta
from pathlib import Path
import pytest
B=Path(__file__).resolve().parents[1]
def mod(rel,name):s=importlib.util.spec_from_file_location(name,B/rel);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
E=mod("executor/mysql_tpch_raw15_fixed_top5_executor_v1.py","e");C=mod("controller/mysql_tpch_linear_formal10_atomic_controller_v1.py","c")
def event():return {"gradient_source":"original_dScore_dU_times_T_times_confidence","feature_ratio":{"cpu":1},"predicted_resource":{"cpu":.7,"memory":.75,"io":.6},"utilization":{"cpu":.7,"memory":.75,"io":.6},"dScore/dU":{"io_block_read_cost":{"cpu":3}},"T":{"io_block_read_cost":{"cpu":2}},"confidence":{"io_block_read_cost":{"cpu":.5}},"g_j":{"io_block_read_cost":3},"eta":.1,"delta_param":{"io_block_read_cost":{"before":1,"proposed_before_clip":1.3,"after_clip":1.3,"absolute":.3,"relative":.3,"clipped":False}},"candidate_score":3.0}
def fp():return {"parameter_signature":"x","l1":{"plan":"x"},"l2":{"workers":1},"l3":{"memory_peak":1,"dynamic_execution_memory":0,"spill":{}}}
def test_exact_2k_minus_1_subspaces():assert len(E.subsets())==15 and len(set(E.subsets()))==15 and E.subsets()[-1]==E.PARAMETERS
def test_subset_ids_are_deterministic():assert E.subset_id(1,E.subsets()[0])==E.subset_id(1,E.subsets()[0]) and len({E.subset_id(i,x) for i,x in enumerate(E.subsets(),1)})==15
def test_template_unknown_placeholder_fails():
 with pytest.raises(E.Error):E.render({"argv":["x","{unknown}"]},{"output":"x"})
def test_formal_validation_is_linear_fingerprint_and_outer_qps_only():
 verifier=C.imports(B);now=datetime.now(timezone.utc);stamp=lambda x:x.isoformat().replace("+00:00","Z");before={"io_block_read_cost":1.,"memory_block_read_cost":1.,"row_evaluate_cost":.1,"disk_temptable_row_cost":1.};after=dict(before,io_block_read_cost=1.3);v={"schema_version":"mysql_tpch_linear_formal_attempt_v1","status":"PASS_FORMAL_ATTEMPT","attempt_token":C.ATTEMPT,"same_live_identity":{"x":1},"technical_failure":False,"database_counter_missing_samples":0,"database_counter_timeout_count":0,"database_counter_sample_count":181,"gradient_event":event(),"three_level_fingerprint":fp(),"parameter_state_before":before,"parameter_state_after":after,"measurement_started_at_utc":stamp(now-timedelta(seconds=10)),"measurement_ended_at_utc":stamp(now-timedelta(seconds=1)),"rolling_gate":{"status":"PASS","attempt_token":C.ATTEMPT,"collector_sha256":C.SUPPORT_SHA,"database_counter_missing_samples":0,"database_counter_timeout_count":0,"accepted_round_authorized":False,"captured_at_utc":stamp(now-timedelta(seconds=20)),"expires_at_utc":stamp(now+timedelta(seconds=100))},"outer_acceptance":{"qps_before":1,"qps_after":2},"accepted_round_authorized":False};assert C.validate_attempt(v,{"x":1},verifier,before)[0] is True
 v["gradient_event"]["qps_prediction"]=9
 with pytest.raises(C.Error):C.validate_attempt(v,{"x":1},verifier,before)
def test_atomic_seal_and_technical_zero_commit(tmp_path):
 p=tmp_path/"x.json";E.atomic(p,{"a":1});assert p.exists() and Path(str(p)+".sha256").read_text()==E.sha(p)+"  x.json\n"
 with pytest.raises(E.Error):E.atomic(p,{"a":2})
 err=C.technical(tmp_path/"formal","boom",3);_,v=C.load(tmp_path/"formal/technical_failure.zero_commit.json","tomb");assert isinstance(err,C.Error) and v["technical_failure_commit_credit"]==0 and v["accepted_rounds_already_committed"]==3
def test_manifest_binds_entire_launch_chain_and_zero_commit():
 m=json.loads((B/"bundle_manifest.json").read_text());assert m["support_bundle_id"]==E.SUPPORT_ID and m["materializer_bundle_id"]==E.MATERIALIZER_ID and m["wrapper_bundle_id"]==E.WRAPPER_ID and m["technical_failure_commit_credit"]==0 and m["subset_count"]==15 and m["fixed_top5_count"]==5
'''

def replace(s):
 for a,b in {"__FF_ID__":FF_ID,"__FF_SHA__":FF_SHA,"__PROTOCOL_ID__":"__BUNDLE_ID__","__PROTOCOL_SHA__":"__MANIFEST_SHA__","__SUPPORT_ID__":SUPPORT_ID,"__SUPPORT_SHA__":SUPPORT_SHA,"__SUPPORT_CORE__":SUPPORT_CORE,"__MATERIALIZER_ID__":MATERIALIZER_ID,"__MATERIALIZER_SHA__":MATERIALIZER_SHA,"__WRAPPER_ID__":WRAPPER_ID,"__WRAPPER_SHA__":WRAPPER_SHA,"__ATTEMPT__":ATTEMPT}.items():s=s.replace(a,b)
 return s
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)
def build():
 if sf(OLD/"bundle_manifest.json")!=LINEAR_SHA:raise RuntimeError("7c9 closure differs")
 producer=(OLD/"producer/mysql_tpch_linear_formal_debug_producer_v1.py").read_text().replace('SUPPORT_ID="da455246652a82bb13d63f5f1ba6ebeb2e92fd08389e3f06707e5897e12646b6";SUPPORT_SHA="0f835d084459a2c66729327a9a4b9ed6ec070116e9873631874ac667039b34c4"','SUPPORT_ID="'+SUPPORT_ID+'";SUPPORT_SHA="'+SUPPORT_SHA+'"').encode()
 base={"verifier/tpch_formal10_linear_readiness_v1.py":(OLD/"verifier/tpch_formal10_linear_readiness_v1.py").read_bytes(),"verifier/mysql_tpch_client_immutable_identity_v1.py":(OLD/"verifier/mysql_tpch_client_immutable_identity_v1.py").read_bytes(),"producer/mysql_tpch_linear_formal_debug_producer_v1.py":producer,"authority/mysql_tpch_linear_same_live_formal10_authority_v1.py":AUTH.encode(),"tests/test_mysql_tpch_linear_campaign_atomic_v1.py":TEST.encode(),"builder/build_mysql_tpch_linear_campaign_atomic_v1.py":HERE.read_bytes(),"templates/worker_template.schema.json":canonical({"schema_version":"mysql_tpch_sealed_worker_template_v1","argv":["[EXECUTABLE]","[WORKER]","--output","{output}"],"timeout_seconds":7200,"note":"candidate templates additionally require subset_id and active_subset_json; replay templates require rank/subset_id/candidate_id/active_subset_json; formal templates require attempt_index and accepted_round"})}
 # The protocol ID is a hash of the unsigned manifest, while executors must
 # embed it. Resolve this self-reference by declaring the executable protocol
 # identity as its content-closure hash, which is independently manifest-bound.
 provisional_exec=replace(EXECUTOR).replace("__BUNDLE_ID__","CONTENT_CLOSURE").replace("__MANIFEST_SHA__","MANIFEST_SIDECAR")
 provisional_ctl=replace(CONTROLLER).replace("__BUNDLE_ID__","CONTENT_CLOSURE").replace("__MANIFEST_SHA__","MANIFEST_SIDECAR")
 base["executor/mysql_tpch_raw15_fixed_top5_executor_v1.py"]=provisional_exec.encode();base["controller/mysql_tpch_linear_formal10_atomic_controller_v1.py"]=provisional_ctl.encode()
 files={n:sb(v) for n,v in sorted(base.items())};closure=sb(canonical(files))
 execb=provisional_exec.replace("CONTENT_CLOSURE",closure);ctlb=provisional_ctl.replace("CONTENT_CLOSURE",closure);base["executor/mysql_tpch_raw15_fixed_top5_executor_v1.py"]=execb.encode();base["controller/mysql_tpch_linear_formal10_atomic_controller_v1.py"]=ctlb.encode();files={n:sb(v) for n,v in sorted(base.items())}
 u={"schema_version":"mysql_tpch_linear_campaign_atomic_bundle_v1","status":"PASS_IMMUTABLE_RAW15_TOP5_FORMAL10_CONTROLLERS_NOT_EXECUTED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"ff313_bundle_id":FF_ID,"linear_predecessor_bundle_id":LINEAR_ID,"support_bundle_id":SUPPORT_ID,"support_manifest_sha256":SUPPORT_SHA,"support_core_sha256":SUPPORT_CORE,"materializer_bundle_id":MATERIALIZER_ID,"materializer_manifest_sha256":MATERIALIZER_SHA,"wrapper_bundle_id":WRAPPER_ID,"wrapper_manifest_sha256":WRAPPER_SHA,"protocol_content_closure_sha256":closure,"subset_count":15,"subset_rule":"2^4-1_exact_nonempty","fixed_top5_count":5,"linear_update_formula":"proposed=before*(1+eta*g_j)","replacement_search_algorithm_used":False,"three_level_fingerprint_required":True,"qps_role":"outer_acceptance_after_real_replay_only","database_counter_timeout_fail_closed":True,"initial_parameter_state_fresh_r0_binding_required":True,"accepted_parameter_state_chain_required":True,"technical_failure_commit_credit":0,"formal10_target_accepted_rounds":10,"raw_executor_included":True,"formal10_atomic_controller_included":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"remote_actions":0,"files":files};bid=sb(canonical(u));m=dict(u,bundle_id=bid);dst=OUT/bid
 if dst.exists():
  if sf(dst/"bundle_manifest.json")!=sb(canonical(m)):raise RuntimeError("existing differs")
  return dst
 OUT.mkdir(parents=True,exist_ok=True);st=Path(tempfile.mkdtemp(prefix=".linear-campaign-",dir=OUT))
 try:
  for n,v in base.items():p=st/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
  raw=canonical(m);(st/"bundle_manifest.json").write_bytes(raw);(st/"bundle_manifest.json.sha256").write_text(sb(raw)+"  bundle_manifest.json\n");freeze(st);os.replace(st,dst)
 finally:
  if st.exists():shutil.rmtree(st)
 return dst
if __name__=="__main__":
 b=build();print(json.dumps({"bundle":str(b),"bundle_id":b.name,"manifest_sha256":sf(b/"bundle_manifest.json"),"remote_actions":0},sort_keys=True))

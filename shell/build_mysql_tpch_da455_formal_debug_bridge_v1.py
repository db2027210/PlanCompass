#!/usr/bin/env python3
"""Build a strict raw-journal to ff313 formal debug evidence bridge."""
from __future__ import annotations
import hashlib,json,os,shutil,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve();OUT=ROOT/"artifacts/mysql_tpch_da455_formal_debug_bridge_v1"
FF_ID="ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4";FF_SHA="af1d0facca8b33e2638c0a2af6a751bececc407d483055ca064a34308d27bb52";FF=ROOT/"artifacts/mysql_tpch_sql_native_percentile_successor_v1"/FF_ID;VERIFIER_SHA="2e9ffa8710e0eea5d63d6865cfe1c848e5606463497fe45f5d7c8e81416cf7c7";IDENT_SHA="8112d805623183b412a779cb9549273b40f7f1f02a1bc85d5d6407d8e02629d3"
BRIDGE=r'''#!/usr/bin/env python3
from __future__ import annotations
import argparse,hashlib,importlib.util,itertools,json,math,os,sys
from datetime import datetime,timezone
from pathlib import Path
from typing import Any,Mapping
FF_ID="ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4";FF_SHA="af1d0facca8b33e2638c0a2af6a751bececc407d483055ca064a34308d27bb52";VERIFIER_SHA="2e9ffa8710e0eea5d63d6865cfe1c848e5606463497fe45f5d7c8e81416cf7c7";IDENT_SHA="8112d805623183b412a779cb9549273b40f7f1f02a1bc85d5d6407d8e02629d3";SUPPORT_ID="da455246652a82bb13d63f5f1ba6ebeb2e92fd08389e3f06707e5897e12646b6";SUPPORT_SHA="0f835d084459a2c66729327a9a4b9ed6ec070116e9873631874ac667039b34c4";ATTEMPT="tpch-v2-20260803t022500z-afbe6badff313374deadbeef";PARAMETERS=("io_block_read_cost","memory_block_read_cost","row_evaluate_cost","disk_temptable_row_cost");QPS_ROLE="outer_acceptance_after_real_replay_only"
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
 v=json.loads(p.read_text())
 if not isinstance(v,dict):raise Error(label+" object required")
 return p,v
def finite(v,label):
 if isinstance(v,bool):raise Error(label+" finite number required")
 try:x=float(v)
 except (TypeError,ValueError) as e:raise Error(label+" finite number required") from e
 if not math.isfinite(x):raise Error(label+" finite number required")
 return x
def utc(v):
 try:x=datetime.fromisoformat(str(v).replace("Z","+00:00"))
 except ValueError as e:raise Error("timestamp differs") from e
 if x.tzinfo is None:raise Error("timezone required")
 return x.astimezone(timezone.utc)
def gate(v,now=None):
 c,e=utc(v.get("captured_at_utc")),utc(v.get("expires_at_utc"));n=now or datetime.now(timezone.utc)
 if not 0<(e-c).total_seconds()<=900 or not c<=n<e or v.get("status")!="PASS" or v.get("attempt_token")!=ATTEMPT or v.get("collector_sha256")!=SUPPORT_SHA or v.get("database_counter_missing_samples")!=0 or v.get("database_counter_timeout_count")!=0 or v.get("accepted_round_authorized") is not False:raise Error("rolling da455 gate differs")
def has_qps(v):
 if isinstance(v,Mapping):return any("qps" in str(k).lower() or has_qps(x) for k,x in v.items())
 if isinstance(v,list):return any(has_qps(x) for x in v)
 return False
def expected_subsets():return {tuple(x) for width in range(1,5) for x in itertools.combinations(PARAMETERS,width)}
def import_verifier(bundle):
 vp=bundle/"tpch_formal10_readiness_v2.py";ip=bundle/"mysql_tpch_client_immutable_identity_v1.py"
 if sha(vp)!=VERIFIER_SHA or sha(ip)!=IDENT_SHA:raise Error("ff313 verifier import closure differs")
 before=list(sys.path);name="mysql_tpch_client_immutable_identity_v1";previous=sys.modules.pop(name,None);sys.path.insert(0,str(bundle))
 try:
  s=importlib.util.spec_from_file_location("ff313_formal_debug_verifier",vp);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
 finally:
  sys.path[:]=before;sys.modules.pop(name,None)
  if previous is not None:sys.modules[name]=previous
def verify_bundle(p):
 p=Path(p).resolve(strict=True);mp,m=load(p/"bundle_manifest.json","ff313 manifest")
 if p.name!=FF_ID or m.get("bundle_id")!=FF_ID or sha(mp)!=FF_SHA or (m.get("files") or {}).get("tpch_formal10_readiness_v2.py")!=VERIFIER_SHA or m.get("formal_scope")!="mysql_only" or m.get("cross_engine_comparison_prohibited") is not True:raise Error("ff313 bundle differs")
 return p
def prediction_rows(doc,identity):
 rows=doc.get("candidates")
 if doc.get("attempt_token")!=ATTEMPT or doc.get("same_live_identity")!=identity or doc.get("qps_role")!=QPS_ROLE or doc.get("accepted_round_authorized") is not False or not isinstance(rows,list) or len(rows)!=15:raise Error("prediction journal authority differs")
 seen={};events=[];ranking=[];fps=[];last=math.inf
 for rank,row in enumerate(rows,1):
  if not isinstance(row,Mapping) or has_qps(row):raise Error("QPS is prohibited from prediction/gradient")
  active=tuple(row.get("active_subset") or ());subset=row.get("subset_id");candidate=row.get("candidate_id");score=finite(row.get("candidate_score"),"candidate score");gradient=row.get("gradient") or {};fp=row.get("three_level_fingerprint") or {}
  if row.get("rank")!=rank or not isinstance(subset,str) or not subset or not isinstance(candidate,str) or not candidate or active not in expected_subsets() or subset in seen or score>last or gradient.get("gradient_source")!="original_dScore_dU_times_T_times_confidence":raise Error("prediction ranking/subset differs")
  seen[subset]=active;last=score;event={"subset_id":subset,"active_subset":list(active),**dict(gradient)};events.append(event);ranking.append({"rank":rank,"subset_id":subset,"candidate_score":score});fps.append({"stage":"predicted_candidate","candidate_id":candidate,"source_subset_id":subset,**dict(fp)})
 if set(seen.values())!=expected_subsets():raise Error("exact 15 nonempty subspaces required")
 return rows,events,ranking,fps
def replay_rows(doc,predictions,identity,fps,now=None):
 rows=doc.get("replays")
 if doc.get("attempt_token")!=ATTEMPT or doc.get("same_live_identity")!=identity or doc.get("qps_role")!=QPS_ROLE or doc.get("accepted_round_authorized") is not False or not isinstance(rows,list) or len(rows)!=5:raise Error("replay journal authority differs")
 journals=[];acceptance=[]
 for rank,row in enumerate(rows,1):
  pred=predictions[rank-1];without=dict(row);outer=without.pop("outer_acceptance",None)
  if has_qps(without) or not isinstance(outer,Mapping) or not has_qps(outer):raise Error("QPS must exist only inside real-replay outer acceptance")
  if row.get("rank")!=rank or row.get("subset_id")!=pred.get("subset_id") or row.get("candidate_id")!=pred.get("candidate_id") or row.get("active_subset")!=pred.get("active_subset") or row.get("same_live_identity")!=identity or row.get("database_counter_missing_samples")!=0 or row.get("database_counter_timeout_count")!=0 or not isinstance(row.get("database_counter_sample_count"),int) or row["database_counter_sample_count"]<181 or row.get("rolling_dynamic_gate_passed") is not True or row.get("failed_replay") is not False or row.get("accepted_rounds_credit")!=0 or row.get("accepted_round_authorized") is not False:raise Error("fixed Top5 replay/counter closure differs")
  rg=row.get("rolling_gate") or {};start,end=utc(row.get("measurement_started_at_utc")),utc(row.get("measurement_ended_at_utc"));c,e=utc(rg.get("captured_at_utc")),utc(rg.get("expires_at_utc"))
  if not 0<(e-c).total_seconds()<=900 or not c<=start<end<=e or rg.get("status")!="PASS" or rg.get("attempt_token")!=ATTEMPT or rg.get("collector_sha256")!=SUPPORT_SHA or rg.get("database_counter_missing_samples")!=0 or rg.get("database_counter_timeout_count")!=0:raise Error("per-replay rolling gate differs")
  journals.append({"rank":rank,"subset_id":row["subset_id"],"sealed_candidate_identity":row["candidate_id"],"attempt_token":ATTEMPT,"started":True,"measurement_complete":True,"completion_recorded":True,"real_replay":True,"failed_replay":False,"accepted_rounds_credit":0});fps.append({"stage":"real_top5_replay","candidate_id":row["candidate_id"],"source_subset_id":row["subset_id"],**dict(row.get("three_level_fingerprint") or {})});acceptance.append({"rank":rank,"subset_id":row["subset_id"],"outer_acceptance":dict(outer),"database_counter_sample_count":row["database_counter_sample_count"],"database_counter_missing_samples":0,"database_counter_timeout_count":0})
 return journals,acceptance
def build(bundle,bootstrap,dynamic,fresh,pred,replay,now=None):
 gate(dynamic,now);identity=bootstrap.get("same_live_identity")
 if bootstrap.get("attempt_token")!=ATTEMPT or fresh.get("attempt_token")!=ATTEMPT or fresh.get("same_live_identity")!=identity or fresh.get("support_collector_bundle_id")!=SUPPORT_ID or fresh.get("support_collector_manifest_sha256")!=SUPPORT_SHA or fresh.get("database_counter_missing_samples")!=0 or fresh.get("database_counter_timeout_count")!=0 or fresh.get("accepted_rounds_credit")!=0:raise Error("fresh R0/same-live/counter closure differs")
 predictions,events,ranking,fps=prediction_rows(pred,identity);journals,acceptance=replay_rows(replay,predictions,identity,fps,now);analysis=replay.get("prediction_vs_real_analysis") or {}
 if analysis.get("predicted_top5_count")!=5 or analysis.get("real_top5_count")!=5 or analysis.get("real_order_complete") is not True or analysis.get("classification") not in ("gradient","estimation","fingerprint","technical_sampling","no_issue"):raise Error("prediction versus real analysis differs")
 result={"schema_version":"mysql_tpch_distributed_v2_debug_round_evidence_v1","status":"PASS_DEBUG_ROUND","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"bundle_id":FF_ID,"bundle_manifest_sha256":FF_SHA,"support_collector_bundle_id":SUPPORT_ID,"support_collector_manifest_sha256":SUPPORT_SHA,"attempt_token":ATTEMPT,"same_live_identity":identity,"completed_nonempty_subsets":15,"sealed_top5_ranks":[1,2,3,4,5],"replayed_top5_ranks":[1,2,3,4,5],"fixed_top5_only":True,"candidate_order":"global_descending_final_candidate_score","algorithm_formula":"g_j = sum_r(dScore/dU_r * T[j,r] * confidence)","parameter_update_source":"g_j_and_eta_only","qps_role":QPS_ROLE,"fingerprint_duplicate_rule":"exact_parameter_signature_and_equal_l1_and_tolerant_l2_l3","missing_fingerprint_level_policy":"independent_candidate","gradient_events":events,"global_candidate_ranking":ranking,"top5_point_journals":journals,"candidate_fingerprints":fps,"prediction_vs_real_analysis":dict(analysis),"real_replay_acceptance_evidence":acceptance,"database_counter_missing_samples":0,"database_counter_timeout_count":0,"accepted_rounds_credit":0,"accepted_round_authorized":False,"formal_launch_authorized":False}
 verifier=import_verifier(bundle);verifier.verify_debug_round_document(result,bundle_id=FF_ID,attempt_token=ATTEMPT,identity=identity);return result
def atomic(p,v):
 p=Path(p);s=Path(str(p)+".sha256")
 if p.exists() or s.exists() or p.is_symlink() or s.is_symlink():raise Error("refusing overwrite")
 b=canonical(v);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open("xb") as f:f.write(b);f.flush();os.fsync(f.fileno())
 s.write_text(hashlib.sha256(b).hexdigest()+"  "+p.name+"\n",encoding="ascii");os.chmod(p,0o600);os.chmod(s,0o600)
def main():
 p=argparse.ArgumentParser()
 for n in ("bundle","bootstrap","dynamic-gate","fresh-r0","predictions","replays","output"):p.add_argument("--"+n,type=Path,required=True)
 a=p.parse_args();bundle=verify_bundle(a.bundle);vals=[load(getattr(a,n.replace("-","_")),n)[1] for n in ("bootstrap","dynamic-gate","fresh-r0","predictions","replays")];result=build(bundle,*vals);atomic(a.output,result);print(json.dumps({"status":result["status"],"output":str(a.output),"formal_launch_authorized":False,"accepted_rounds_credit":0},sort_keys=True));return 0
if __name__=="__main__":
 try:raise SystemExit(main())
 except (Error,OSError,UnicodeError,ValueError,KeyError) as e:print("NOT_READY: "+str(e),file=sys.stderr);raise SystemExit(2)
'''
TEST=r'''import importlib.util,math
from datetime import datetime,timezone
from pathlib import Path
import pytest
B=Path(__file__).resolve().parents[1];s=importlib.util.spec_from_file_location("x",B/"bridge/mysql_tpch_da455_formal_debug_bridge_v1.py");X=importlib.util.module_from_spec(s);s.loader.exec_module(X);FF=B/"fixtures/ff313"
def gate():return {"status":"PASS","attempt_token":X.ATTEMPT,"collector_sha256":X.SUPPORT_SHA,"database_counter_missing_samples":0,"database_counter_timeout_count":0,"accepted_round_authorized":False,"captured_at_utc":"2026-08-03T00:00:00Z","expires_at_utc":"2026-08-03T00:15:00Z"}
def fp(sig):return {"parameter_signature":sig,"l1":{"plan":"x"},"l2":{"worker":1},"l3":{"memory_peak":1,"dynamic_execution_memory":0,"spill":{}}}
def docs():
 ident={"database_container_id":"d","client_container_id":"c"};subs=sorted(X.expected_subsets(),key=lambda z:(len(z),z));rows=[]
 for rank,active in enumerate(subs,1):
  t={p:{"cpu":2.0} for p in active};sl={p:{"cpu":3.0} for p in active};cf={p:{"cpu":0.5} for p in active};g={p:3.0 for p in active};delta={p:{"before":1.0,"proposed_before_clip":math.exp(.3),"after_clip":math.exp(.3),"absolute":math.exp(.3)-1,"relative":math.exp(.3)-1,"clipped":False} for p in active};gradient={"gradient_source":"original_dScore_dU_times_T_times_confidence","feature_ratio":{"cpu":1.0},"predicted_resource":{"cpu":1.0},"utilization":{"cpu":1.0},"dScore/dU":sl,"T":t,"confidence":cf,"g_j":g,"eta":.1,"delta_param":delta,"candidate_score":16-rank};rows.append({"rank":rank,"subset_id":"s"+str(rank),"candidate_id":"c"+str(rank),"active_subset":list(active),"candidate_score":16-rank,"gradient":gradient,"three_level_fingerprint":fp("p"+str(rank))})
 pred={"attempt_token":X.ATTEMPT,"same_live_identity":ident,"qps_role":X.QPS_ROLE,"accepted_round_authorized":False,"candidates":rows};replays=[]
 for rank,row in enumerate(rows[:5],1):replays.append({"rank":rank,"subset_id":row["subset_id"],"candidate_id":row["candidate_id"],"active_subset":row["active_subset"],"same_live_identity":ident,"database_counter_missing_samples":0,"database_counter_timeout_count":0,"database_counter_sample_count":181,"rolling_dynamic_gate_passed":True,"rolling_gate":gate(),"measurement_started_at_utc":"2026-08-03T00:00:01Z","measurement_ended_at_utc":"2026-08-03T00:14:59Z","failed_replay":False,"accepted_rounds_credit":0,"accepted_round_authorized":False,"outer_acceptance":{"qps_before":1,"qps_after":2},"three_level_fingerprint":fp("r"+str(rank))})
 replay={"attempt_token":X.ATTEMPT,"same_live_identity":ident,"qps_role":X.QPS_ROLE,"accepted_round_authorized":False,"replays":replays,"prediction_vs_real_analysis":{"predicted_top5_count":5,"real_top5_count":5,"real_order_complete":True,"classification":"no_issue"}};bootstrap={"attempt_token":X.ATTEMPT,"same_live_identity":ident};fresh={"attempt_token":X.ATTEMPT,"same_live_identity":ident,"support_collector_bundle_id":X.SUPPORT_ID,"support_collector_manifest_sha256":X.SUPPORT_SHA,"database_counter_missing_samples":0,"database_counter_timeout_count":0,"accepted_rounds_credit":0};return bootstrap,fresh,pred,replay
def test_exact_formal_verifier_consumes_bridge_output():
 b,f,p,r=docs();v=X.build(FF,b,gate(),f,p,r,datetime(2026,8,3,0,10,tzinfo=timezone.utc));assert v["status"]=="PASS_DEBUG_ROUND" and len(v["gradient_events"])==15 and len(v["candidate_fingerprints"])==20
def test_raw_gradient_is_recomputed_fail_closed():
 b,f,p,r=docs();p["candidates"][0]["gradient"]["g_j"][p["candidates"][0]["active_subset"][0]]=9
 with pytest.raises(Exception):X.build(FF,b,gate(),f,p,r,datetime(2026,8,3,0,10,tzinfo=timezone.utc))
def test_qps_is_prediction_prohibited_and_replay_acceptance_only():
 b,f,p,r=docs();p["candidates"][0]["qps"]=3
 with pytest.raises(X.Error,match="QPS"):X.build(FF,b,gate(),f,p,r,datetime(2026,8,3,0,10,tzinfo=timezone.utc))
def test_counter_timeout_rejected():
 b,f,p,r=docs();r["replays"][0]["database_counter_timeout_count"]=1
 with pytest.raises(X.Error):X.build(FF,b,gate(),f,p,r,datetime(2026,8,3,0,10,tzinfo=timezone.utc))
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
 fm=json.loads((FF/"bundle_manifest.json").read_text())
 if fm.get("bundle_id")!=FF_ID or sf(FF/"bundle_manifest.json")!=FF_SHA or sf(FF/"tpch_formal10_readiness_v2.py")!=VERIFIER_SHA or sf(FF/"mysql_tpch_client_immutable_identity_v1.py")!=IDENT_SHA:raise RuntimeError("ff313 verifier closure differs")
 content={"bridge/mysql_tpch_da455_formal_debug_bridge_v1.py":BRIDGE.encode(),"tests/test_mysql_tpch_da455_formal_debug_bridge_v1.py":TEST.encode(),"fixtures/ff313/tpch_formal10_readiness_v2.py":(FF/"tpch_formal10_readiness_v2.py").read_bytes(),"fixtures/ff313/mysql_tpch_client_immutable_identity_v1.py":(FF/"mysql_tpch_client_immutable_identity_v1.py").read_bytes(),"fixtures/ff313/bundle_manifest.json":(FF/"bundle_manifest.json").read_bytes(),"fixtures/ff313/bundle_manifest.json.sha256":(FF/"bundle_manifest.json.sha256").read_bytes(),"builder/build_mysql_tpch_da455_formal_debug_bridge_v1.py":HERE.read_bytes(),"templates/command.json":canonical({"argv":["[SEALED_PYTHON]","[THIS_BUNDLE]/bridge/mysql_tpch_da455_formal_debug_bridge_v1.py","--bundle","[EXACT_FF313_BUNDLE]","--bootstrap","[FRESH_BOOTSTRAP_EVIDENCE]","--dynamic-gate","[FRESH_FINAL_DA455_GATE]","--fresh-r0","[FRESH_R0]","--predictions","[SEALED_RAW_15_SUBSPACE_JOURNAL]","--replays","[SEALED_RAW_FIXED_TOP5_REPLAY_JOURNAL]","--output","[NONEXISTENT_FORMAL_DEBUG_EVIDENCE]"],"actual_debug_campaign_producer_included":False,"formal10_launch_authorized":False})};files={n:sb(v) for n,v in sorted(content.items())};u={"schema_version":"mysql_tpch_da455_formal_debug_bridge_bundle_v1","status":"PASS_STRICT_FORMAL_DEBUG_BRIDGE_NOT_EXECUTED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"ff313_bundle_id":FF_ID,"ff313_manifest_sha256":FF_SHA,"ff313_formal_verifier_sha256":VERIFIER_SHA,"raw_gradient_formula_recomputed":True,"three_level_fingerprint_required":True,"qps_role":"outer_acceptance_after_real_replay_only","database_counter_timeout_fail_closed":True,"exact_15_subspaces_required":True,"fixed_top5_required":True,"actual_debug_campaign_producer_included":False,"same_live_formal10_controller_included":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,"files":files};bid=sb(canonical(u));m=dict(u,bundle_id=bid);dst=OUT/bid
 if dst.exists():
  if sf(dst/"bundle_manifest.json")!=sb(canonical(m)):raise RuntimeError("existing differs")
  return dst
 OUT.mkdir(parents=True,exist_ok=True);st=Path(tempfile.mkdtemp(prefix=".formal-debug-bridge-",dir=OUT))
 try:
  for n,v in content.items():p=st/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
  raw=canonical(m);(st/"bundle_manifest.json").write_bytes(raw);(st/"bundle_manifest.json.sha256").write_text(sb(raw)+"  bundle_manifest.json\n");freeze(st);os.replace(st,dst)
 finally:
  if st.exists():shutil.rmtree(st)
 return dst
if __name__=="__main__":
 b=build();print(json.dumps({"bundle":str(b),"bundle_id":b.name,"manifest_sha256":sf(b/"bundle_manifest.json"),"bridge_sha256":sf(b/"bridge/mysql_tpch_da455_formal_debug_bridge_v1.py"),"remote_actions":0},sort_keys=True))

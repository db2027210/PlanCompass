#!/usr/bin/env python3
"""Build the paired linear-gradient verifier, producer and handoff closure."""
from __future__ import annotations
import hashlib,json,os,shutil,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve();OUT=ROOT/"artifacts/mysql_tpch_linear_formal_closure_v1"
FF_ID="ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4";FF_SHA="af1d0facca8b33e2638c0a2af6a751bececc407d483055ca064a34308d27bb52";FF=ROOT/"artifacts/mysql_tpch_sql_native_percentile_successor_v1"/FF_ID;OLD_VERIFIER_SHA="2e9ffa8710e0eea5d63d6865cfe1c848e5606463497fe45f5d7c8e81416cf7c7";AUTH_SHA="120a848c63c6aaa5ecce24f55ed33d6b678d7a21e30ed2050b7a2efdd7a7b408";ALGO_SHA="c1e5c0d7c681094c541562a2dc36f4f98016d72d6d49fa631961dcf4fc55bcee";BRIDGE_OLD=ROOT/"artifacts/mysql_tpch_da455_formal_debug_bridge_v1/9769e0b8e6b46ef2d79ec662ed5c4df8a841f7a886b8ca8a9b663d2f9c7ea518"
RANGES={"io_block_read_cost":[0.1,10.0],"memory_block_read_cost":[0.025,2.5],"row_evaluate_cost":[0.01,1.0],"disk_temptable_row_cost":[0.05,5.0]}
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sb(b):return hashlib.sha256(b).hexdigest()
def sf(p):
 d=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
def patch_verifier(raw):
 s=raw.decode();old="        expected_proposed = before * math.exp(eta * observed)";new="        expected_proposed = before * (1.0 + eta * observed)"
 if s.count(old)!=1:raise RuntimeError("exp verifier anchor differs")
 s=s.replace(old,new)
 old2='        if delta.get("clipped") is False and not _close(after, proposed):\n            raise ReadinessError(f"{label}.unclipped update differs")'
 new2='''        bounds = {"io_block_read_cost": (0.1, 10.0), "memory_block_read_cost": (0.025, 2.5), "row_evaluate_cost": (0.01, 1.0), "disk_temptable_row_cost": (0.05, 5.0)}[parameter]
        expected_after = min(max(proposed, bounds[0]), bounds[1])
        expected_clipped = not _close(expected_after, proposed)
        if not _close(after, expected_after) or delta.get("clipped") is not expected_clipped:
            raise ReadinessError(f"{label}.linear clipping differs")'''
 if s.count(old2)!=1:raise RuntimeError("clipping verifier anchor differs")
 s=s.replace(old2,new2)
 old3='    finite(event.get("candidate_score"), f"{label}.candidate_score")\n    for parameter in sorted(t_matrix):'
 new3='''    candidate_score = finite(event.get("candidate_score"), f"{label}.candidate_score")
    utilization = event.get("utilization")
    targets = {"cpu": (0.65, 0.80, 0.90), "memory": (0.70, 0.80, 0.90), "io": (0.50, 0.75, 0.90)}
    if not isinstance(utilization, Mapping) or set(utilization) != set(targets):
        raise ReadinessError(f"{label}.utilization must contain exact cpu/memory/io")
    expected_score = 0.0
    for resource, (lower, upper, hard) in targets.items():
        value = max(0.0, finite(utilization[resource], f"{label}.utilization.{resource}"))
        if value < lower: expected_score += 1.0 - ((lower-value)/lower)**2
        elif value <= upper: expected_score += 1.0
        else:
            span=max(hard-upper,0.05);expected_score += 1.0-((value-upper)/span)**2-4.0*max(0.0,value-hard)/span
    if not _close(candidate_score, expected_score):
        raise ReadinessError(f"{label}.candidate_score is not frozen band-target utility")
    for parameter in sorted(t_matrix):'''
 if s.count(old3)!=1:raise RuntimeError("candidate score anchor differs")
 return s.replace(old3,new3).encode()
AUTHORITY=r'''#!/usr/bin/env python3
from __future__ import annotations
import argparse,hashlib,importlib.util,json,os,sys
from datetime import datetime,timezone
from pathlib import Path
FF_ID="ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4";FF_SHA="af1d0facca8b33e2638c0a2af6a751bececc407d483055ca064a34308d27bb52";AUTH_SHA="120a848c63c6aaa5ecce24f55ed33d6b678d7a21e30ed2050b7a2efdd7a7b408";LINEAR_VERIFIER_SHA="__LINEAR_SHA__"
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
def import_file(path,name,sibling=True):
 path=Path(path).resolve(strict=True);before=list(sys.path);old=sys.modules.pop("mysql_tpch_client_immutable_identity_v1",None);sys.path.insert(0,str(path.parent))
 try:s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
 finally:
  sys.path[:]=before;sys.modules.pop("mysql_tpch_client_immutable_identity_v1",None)
  if old is not None:sys.modules["mysql_tpch_client_immutable_identity_v1"]=old
def atomic(p,v):
 p=Path(p);side=Path(str(p)+".sha256")
 if p.exists() or side.exists() or p.is_symlink() or side.is_symlink():raise Error("refusing overwrite")
 b=canonical(v);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open("xb") as f:f.write(b);f.flush();os.fsync(f.fileno())
 side.write_text(hashlib.sha256(b).hexdigest()+"  "+p.name+"\n",encoding="ascii")
def main():
 p=argparse.ArgumentParser()
 for n in ("bundle","spec","bootstrap","dynamic-gate","fresh-r0","debug-round","output"):p.add_argument("--"+n,type=Path,required=True)
 a=p.parse_args();bundle=a.bundle.resolve(strict=True);mp,m=load(bundle/"bundle_manifest.json","ff313 manifest")
 if bundle.name!=FF_ID or m.get("bundle_id")!=FF_ID or sha(mp)!=FF_SHA or sha(bundle/"mysql_tpch_distributed_v2_authority.py")!=AUTH_SHA:raise Error("ff313 authority closure differs")
 protocol=Path(__file__).resolve().parents[1];vp=protocol/"verifier/tpch_formal10_linear_readiness_v1.py"
 if sha(vp)!=LINEAR_VERIFIER_SHA:raise Error("linear verifier differs")
 _,spec=load(a.spec,"spec");_,bootstrap=load(a.bootstrap,"bootstrap");_,gate=load(a.dynamic_gate,"gate");_,fresh=load(a.fresh_r0,"fresh R0");_,debug=load(a.debug_round,"debug")
 authority=import_file(bundle/"mysql_tpch_distributed_v2_authority.py","ff313_linear_authority_base");base=authority.build_handoff(spec,bootstrap,gate,fresh,None,False);verifier=import_file(vp,"linear_formal_verifier");verifier.verify_debug_round_document(debug,bundle_id=FF_ID,attempt_token=spec["attempt_token"],identity=base["same_live_identity"])
 c=datetime.fromisoformat(str(gate["captured_at_utc"]).replace("Z","+00:00"));e=datetime.fromisoformat(str(gate["expires_at_utc"]).replace("Z","+00:00"));now=datetime.now(timezone.utc)
 if not c<=now<e or (e-c).total_seconds()>900:raise Error("dynamic gate expired")
 out=dict(base);out.update({"schema_version":"mysql_tpch_linear_same_live_formal10_handoff_v1","status":"READY_FORMAL10_SAME_LIVE_LINEAR_ORIGINAL_SEARCH","linear_update_formula":"proposed=before*(1+eta*g_j)","replacement_search_algorithm_used":False,"linear_verifier_sha256":LINEAR_VERIFIER_SHA,"debug_round_sha256":hashlib.sha256(canonical(debug)).hexdigest(),"formal10_authorized":True,"target_accepted_rounds":10,"next_accepted_round":1,"accepted_round_authorized":False});atomic(a.output,out);print(json.dumps({"status":out["status"],"output":str(a.output),"formal10_authorized":True,"accepted_round_authorized":False},sort_keys=True));return 0
if __name__=="__main__":
 try:raise SystemExit(main())
 except (Error,OSError,UnicodeError,ValueError,KeyError) as e:print("NOT_READY: "+str(e),file=sys.stderr);raise SystemExit(2)
'''
def patch_bridge(raw,linear_sha):
 s=raw.decode().replace(OLD_VERIFIER_SHA,linear_sha)
 old='vp=bundle/"tpch_formal10_readiness_v2.py";ip=bundle/"mysql_tpch_client_immutable_identity_v1.py"'
 new='protocol=Path(__file__).resolve().parents[1];vp=protocol/"verifier/tpch_formal10_linear_readiness_v1.py";ip=protocol/"verifier/mysql_tpch_client_immutable_identity_v1.py"'
 if s.count(old)!=1:raise RuntimeError("bridge verifier anchor differs")
 return s.replace(old,new).encode()
TEST=r'''import importlib.util,math,sys
from pathlib import Path
import pytest
B=Path(__file__).resolve().parents[1];sys.path.insert(0,str(B/"verifier"));s=importlib.util.spec_from_file_location("v",B/"verifier/tpch_formal10_linear_readiness_v1.py");V=importlib.util.module_from_spec(s);s.loader.exec_module(V)
def event(g=3.0,after=1.3,clipped=False,score=3.0):return {"gradient_source":"original_dScore_dU_times_T_times_confidence","feature_ratio":{"cpu":1},"predicted_resource":{"cpu":.7,"memory":.75,"io":.6},"utilization":{"cpu":.7,"memory":.75,"io":.6},"dScore/dU":{"io_block_read_cost":{"cpu":3}},"T":{"io_block_read_cost":{"cpu":2}},"confidence":{"io_block_read_cost":{"cpu":.5}},"g_j":{"io_block_read_cost":g},"eta":.1,"delta_param":{"io_block_read_cost":{"before":1,"proposed_before_clip":1.3,"after_clip":after,"absolute":after-1,"relative":after-1,"clipped":clipped}},"candidate_score":score}
def test_frozen_linear_original_update_passes():V.verify_gradient_event(event(),"e")
def test_exp_update_is_rejected():
 e=event(after=math.exp(.3));e["delta_param"]["io_block_read_cost"].update({"proposed_before_clip":math.exp(.3),"absolute":math.exp(.3)-1,"relative":math.exp(.3)-1})
 with pytest.raises(V.ReadinessError):V.verify_gradient_event(e,"e")
def test_raw_g_clipping_and_candidate_score_fail_closed():
 for change in (lambda e:e["g_j"].update(io_block_read_cost=4),lambda e:e["delta_param"]["io_block_read_cost"].update(after_clip=2,absolute=1,relative=1),lambda e:e.update(candidate_score=2)):
  e=event();change(e)
  with pytest.raises(V.ReadinessError):V.verify_gradient_event(e,"e")
def test_manifest_declares_no_replacement_search():
 import json;m=json.loads((B/"bundle_manifest.json").read_text());assert m["replacement_search_algorithm_used"] is False and m["linear_update_formula"]=="proposed=before*(1+eta*g_j)"
'''
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)
def build():
 fm=json.loads((FF/"bundle_manifest.json").read_text());algo=FF/"algorithm_source/gradient_descent_optimizer.py";auth=FF/"mysql_tpch_distributed_v2_authority.py";oldv=FF/"tpch_formal10_readiness_v2.py";ident=FF/"mysql_tpch_client_immutable_identity_v1.py"
 if fm.get("bundle_id")!=FF_ID or sf(FF/"bundle_manifest.json")!=FF_SHA or sf(algo)!=ALGO_SHA or sf(auth)!=AUTH_SHA or sf(oldv)!=OLD_VERIFIER_SHA:raise RuntimeError("ff313 original closure differs")
 verifier=patch_verifier(oldv.read_bytes());linear_sha=sb(verifier);bridge=patch_bridge((BRIDGE_OLD/"bridge/mysql_tpch_da455_formal_debug_bridge_v1.py").read_bytes(),linear_sha);authority=AUTHORITY.replace("__LINEAR_SHA__",linear_sha).encode();bridge_test=(BRIDGE_OLD/"tests/test_mysql_tpch_da455_formal_debug_bridge_v1.py").read_text().replace('B/"bridge/mysql_tpch_da455_formal_debug_bridge_v1.py"','B/"producer/mysql_tpch_linear_formal_debug_producer_v1.py"').replace('FF=B/"fixtures/ff313"','FF=B').replace('math.exp(.3)','1.3').replace('1.3-1','.13-.1').replace('1.3','.13').replace('"before":1.0','"before":.1').replace('"relative":.13-.1','"relative":(.13-.1)/.1').replace('"predicted_resource":{"cpu":1.0},"utilization":{"cpu":1.0}','"predicted_resource":{"cpu":.7,"memory":.75,"io":.6},"utilization":{"cpu":.7,"memory":.75,"io":.6}').replace('"candidate_score":16-rank','"candidate_score":3.0');content={"verifier/tpch_formal10_linear_readiness_v1.py":verifier,"verifier/mysql_tpch_client_immutable_identity_v1.py":ident.read_bytes(),"producer/mysql_tpch_linear_formal_debug_producer_v1.py":bridge,"authority/mysql_tpch_linear_same_live_formal10_authority_v1.py":authority,"tests/test_mysql_tpch_linear_formal_closure_v1.py":TEST.encode(),"tests/test_mysql_tpch_linear_formal_debug_producer_v1.py":bridge_test.encode(),"builder/build_mysql_tpch_linear_formal_closure_v1.py":HERE.read_bytes(),"audit/original_algorithm_binding.json":canonical({"schema_version":"mysql_tpch_linear_original_algorithm_binding_v1","ff313_bundle_id":FF_ID,"ff313_manifest_sha256":FF_SHA,"gradient_descent_optimizer_sha256":ALGO_SHA,"frozen_source_expression":"current_value * (1.0 + current_eta * directions[name])","linear_update_formula":"proposed=before*(1+eta*g_j)","replacement_search_algorithm_used":False,"raw_g_j_recomputed":True,"clipping_recomputed":True,"delta_recomputed":True,"candidate_score_recomputed":True,"qps_role":"outer_acceptance_after_real_replay_only","database_counter_timeout_fail_closed":True,"formal_scope":"mysql_only","accepted_rounds_credit":0}),"templates/commands.json":canonical({"debug_producer":["[SEALED_PYTHON]","[THIS_BUNDLE]/producer/mysql_tpch_linear_formal_debug_producer_v1.py","--bundle","[EXACT_FF313]","--bootstrap","[BOOTSTRAP]","--dynamic-gate","[FRESH_GATE]","--fresh-r0","[FRESH_R0]","--predictions","[RAW_15]","--replays","[RAW_TOP5]","--output","[DEBUG]"],"formal_handoff":["[SEALED_PYTHON]","[THIS_BUNDLE]/authority/mysql_tpch_linear_same_live_formal10_authority_v1.py","--bundle","[EXACT_FF313]","--spec","[FRESH_SPEC]","--bootstrap","[BOOTSTRAP]","--dynamic-gate","[FRESH_GATE]","--fresh-r0","[FRESH_R0]","--debug-round","[DEBUG]","--output","[HANDOFF]"],"actual_raw_campaign_executor_included":False,"formal10_controller_included":False})};files={n:sb(v) for n,v in sorted(content.items())};u={"schema_version":"mysql_tpch_linear_formal_closure_bundle_v1","status":"PASS_PAIRED_LINEAR_VERIFIER_PRODUCER_AUTHORITY_NOT_EXECUTED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"ff313_bundle_id":FF_ID,"ff313_manifest_sha256":FF_SHA,"original_algorithm_sha256":ALGO_SHA,"linear_verifier_sha256":linear_sha,"linear_update_formula":"proposed=before*(1+eta*g_j)","replacement_search_algorithm_used":False,"raw_gradient_recomputed":True,"clipping_and_delta_recomputed":True,"candidate_score_recomputed":True,"three_level_fingerprint_required":True,"qps_role":"outer_acceptance_after_real_replay_only","database_counter_timeout_fail_closed":True,"debug_producer_included":True,"same_live_handoff_authority_included":True,"actual_raw_campaign_executor_included":False,"formal10_controller_included":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,"files":files};bid=sb(canonical(u));m=dict(u,bundle_id=bid);dst=OUT/bid
 if dst.exists():
  if sf(dst/"bundle_manifest.json")!=sb(canonical(m)):raise RuntimeError("existing differs")
  return dst
 OUT.mkdir(parents=True,exist_ok=True);st=Path(tempfile.mkdtemp(prefix=".linear-formal-",dir=OUT))
 try:
  for n,v in content.items():p=st/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
  raw=canonical(m);(st/"bundle_manifest.json").write_bytes(raw);(st/"bundle_manifest.json.sha256").write_text(sb(raw)+"  bundle_manifest.json\n");freeze(st);os.replace(st,dst)
 finally:
  if st.exists():shutil.rmtree(st)
 return dst
if __name__=="__main__":
 b=build();m=json.loads((b/"bundle_manifest.json").read_text());print(json.dumps({"bundle":str(b),"bundle_id":b.name,"manifest_sha256":sf(b/"bundle_manifest.json"),"linear_verifier_sha256":m["linear_verifier_sha256"],"producer_sha256":sf(b/"producer/mysql_tpch_linear_formal_debug_producer_v1.py"),"authority_sha256":sf(b/"authority/mysql_tpch_linear_same_live_formal10_authority_v1.py"),"remote_actions":0},sort_keys=True))

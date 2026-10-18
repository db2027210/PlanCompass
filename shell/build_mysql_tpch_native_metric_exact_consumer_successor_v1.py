#!/usr/bin/env python3
"""Build exact one-sequence-per-index consumption on top of 4f2 pacing."""
from __future__ import annotations
import hashlib,json,os,shutil,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve()
PARENT_ID="4f2bbda6b350c9551f133840b7a350cbd7b77743004a6824a73a01df739073b1";PARENT_SHA="41e1bcc0acd25baf0fa7edb7d17e407e898e1785eafb7ef3b1fcc1a8b077c863";PARENT_CORE="1d9cd8a5a07a0c04eaa2e01dca3adbde3ff66708b4652bee47846245db9f96f1"
PARENT=ROOT/"artifacts/mysql_tpch_native_metric_pacing_successor_v1"/PARENT_ID;OUT=ROOT/"artifacts/mysql_tpch_native_metric_exact_consumer_successor_v1";CORE="qualification/mysql_distributed_live_readback_qualification_v1.py"
class BuildError(RuntimeError):pass
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sb(b):return hashlib.sha256(b).hexdigest()
def sf(p):
 d=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
def fmap(root):
 return {p.relative_to(root).as_posix():sf(p) for p in sorted(root.rglob("*")) if p.is_file() and p.name not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
def patch_core(payload):
 text=payload.decode()
 old='''    timeout_seconds=WORKLOAD_SAMPLE_TIMEOUT_SECONDS,
    allow_fresh_repeat=False,
):''';new='''    timeout_seconds=WORKLOAD_SAMPLE_TIMEOUT_SECONDS,
    allow_fresh_repeat=False,
    consume_exact_sequence=False,
):'''
 if text.count(old)!=1:raise BuildError("metric signature differs")
 text=text.replace(old,new)
 old='''            if previous_sequence is None:
                value = values[-1]
            else:
''';new='''            if previous_sequence is None:
                if consume_exact_sequence:
                    exact = [
                        row for row in values
                        if isinstance(row, dict)
                        and row.get("sequence") == 0
                        and row.get("schema_version")
                        == "mysql_algorithm_c_debug_workload_metric_sample_v1"
                        and row.get("qualification_attempt_id") == attempt_id
                    ]
                    if len(exact) != 1:
                        raise QualificationError(
                            "workload metric exact initial sequence is absent"
                        )
                    value = exact[0]
                else:
                    value = values[-1]
            else:
'''
 if text.count(old)!=1:raise BuildError("initial newest consumer anchor differs")
 text=text.replace(old,new)
 old='''                    value = forward[-1]
            if not isinstance(value, dict):''';new='''                    if consume_exact_sequence:
                        value = next(
                            row for row in forward
                            if row["sequence"] == expected_sequence
                        )
                    else:
                        value = forward[-1]
            if not isinstance(value, dict):'''
 if text.count(old)!=1:raise BuildError("forward newest consumer anchor differs")
 text=text.replace(old,new)
 old='''    workload = workload_metric_sample(
        metric_path, attempt_id, previous_sequence
    )''';new='''    workload = workload_metric_sample(
        metric_path,
        attempt_id,
        previous_sequence,
        consume_exact_sequence=True,
    )'''
 if text.count(old)!=1:raise BuildError("own stream call anchor differs")
 return text.replace(old,new).encode()
TEST=r'''from __future__ import annotations
import importlib.util,json,time
from pathlib import Path
import pytest
B=Path(__file__).resolve().parents[1];CORE=B/"qualification/mysql_distributed_live_readback_qualification_v1.py";s=importlib.util.spec_from_file_location("x",CORE);M=importlib.util.module_from_spec(s);s.loader.exec_module(M)
def row(a,n,epoch=None):
 e=time.time() if epoch is None else epoch;return {"schema_version":"mysql_algorithm_c_debug_workload_metric_sample_v1","qualification_attempt_id":a,"sequence":n,"epoch_seconds":e,"completed_total":n,"query_duration_ms":1.,"native_adapter_kind":"sql_query_native","native_marker_epoch_ms":int(e*1000)}
def stream(tmp,seqs,epoch=None):
 a="retry6-"+"a"*40;p=tmp/"m.jsonl";p.write_text("".join(json.dumps(row(a,n,epoch))+"\n" for n in seqs));return p,a
def test_exact_consumer_never_skips_visible_future_rows(tmp_path):
 p,a=stream(tmp_path,[0,1,2]);x=M.workload_metric_sample(p,a,None,consume_exact_sequence=True);y=M.workload_metric_sample(p,a,0,consume_exact_sequence=True);z=M.workload_metric_sample(p,a,1,consume_exact_sequence=True);assert [x["sequence"],y["sequence"],z["sequence"]]==[0,1,2]
def test_old_newest_behavior_reproduces_retry6_exhaustion(tmp_path):
 p,a=stream(tmp_path,[0,1,2]);assert M.workload_metric_sample(p,a,None)["sequence"]==2
 with pytest.raises(M.QualificationError,match="contiguous suffix is absent"):M.workload_metric_sample(p,a,2,timeout_seconds=.001,consume_exact_sequence=True)
def test_gap_still_fails_closed(tmp_path):
 p,a=stream(tmp_path,[0,2])
 with pytest.raises(M.QualificationError,match="not unique and contiguous"):M.workload_metric_sample(p,a,0,timeout_seconds=.001,consume_exact_sequence=True)
def test_stale_expected_row_still_fails_closed(tmp_path):
 p,a=stream(tmp_path,[0,1],time.time()-10)
 with pytest.raises(M.QualificationError,match="sample age exceeded"):M.workload_metric_sample(p,a,None,timeout_seconds=.001,consume_exact_sequence=True)
def test_only_own_stream_enables_exact_consumption():
 source=CORE.read_text();block=source[source.index("def collect_dynamic_sample("):source.index("def _delta(")];assert "consume_exact_sequence=True" in block
 assert "allow_fresh_repeat=True" in source and '"freshness_threshold_relaxed": False' in source and '"timeout_threshold_relaxed": False' in source and "NATIVE_METRIC_EMISSION_LEAD_SECONDS = 0.25" in source
'''
AUDIT={"schema_version":"mysql_tpch_retry6_complete181_contiguous_suffix_root_cause_v1","observation":"metric file contains exact 181 rows sequence 0..180 while collector reports contiguous suffix absent","root_cause":"own collector selected newest visible row for initial and forward reads, so one sampling index could consume multiple sequences and exhaust sequence 180 before all 181 collector indexes","producer_defect":False,"consumer_defect":True,"fix":"own stream consumes exactly sequence 0 then previous+1 while continuing to validate the entire visible suffix as unique and contiguous","minimum_sample_count":181,"first_sequence":0,"last_sequence":180,"missing_samples_allowed":0,"stale_repeat_allowed_for_own_stream":False,"freshness_threshold_relaxed":False,"timeout_threshold_relaxed":False,"producer_schedule":"shared_monotonic_epoch_fixed_1hz_with_0.25s_lead","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0}
def verify():
 m=json.loads((PARENT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id",None)
 if m.get("bundle_id")!=PARENT_ID or sf(PARENT/"bundle_manifest.json")!=PARENT_SHA or sb(canonical(u))!=PARENT_ID or sf(PARENT/CORE)!=PARENT_CORE or fmap(PARENT)!=(m.get("files") or {}) or m.get("formal_launch_authorized") is not False:raise BuildError("4f2 parent closure differs")
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)
def build():
 verify();content={}
 for p in sorted(PARENT.rglob("*")):
  rel=p.relative_to(PARENT).as_posix()
  if p.is_file() and rel not in ("bundle_manifest.json","bundle_manifest.json.sha256"):content[rel]=p.read_bytes()
 content.update({CORE:patch_core((PARENT/CORE).read_bytes()),"tests/test_mysql_tpch_native_metric_exact_consumer_successor_v1.py":TEST.encode(),"authority/mysql_tpch_retry6_complete181_contiguous_suffix_root_cause_v1.json":canonical(AUDIT),"authority/build_mysql_tpch_native_metric_exact_consumer_successor_v1.py":HERE.read_bytes()});files={n:sb(v) for n,v in sorted(content.items())};u={"schema_version":"mysql_tpch_native_metric_exact_consumer_successor_bundle_v1","status":"SEALED_LOCAL_FAIL_CLOSED_EXACT_CONSUMER_NOT_EXECUTED","immutable":True,"parent_bundle_id":PARENT_ID,"parent_manifest_sha256":PARENT_SHA,"root_cause":"newest-row consumption skipped sequence-to-index mapping despite exact complete producer stream","producer_schedule":"shared_monotonic_epoch_fixed_1hz_with_0.25s_lead","own_consumer_policy":"exact_sequence_0_then_previous_plus_1","visible_suffix_unique_contiguous_validation_required":True,"minimum_sample_count":181,"first_sequence":0,"last_sequence":180,"missing_samples_allowed":0,"stale_repeat_allowed_for_own_stream":False,"freshness_threshold_relaxed":False,"timeout_threshold_relaxed":False,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,"files":files};bid=sb(canonical(u));m=dict(u,bundle_id=bid);dst=OUT/bid
 if dst.exists():
  if sf(dst/"bundle_manifest.json")!=sb(canonical(m)):raise BuildError("existing differs")
  return dst
 OUT.mkdir(parents=True,exist_ok=True);st=Path(tempfile.mkdtemp(prefix=".exact-consumer-",dir=OUT))
 try:
  for n,v in content.items():p=st/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
  raw=canonical(m);(st/"bundle_manifest.json").write_bytes(raw);(st/"bundle_manifest.json.sha256").write_text(sb(raw)+"  bundle_manifest.json\n");freeze(st);os.replace(st,dst)
 finally:
  if st.exists():shutil.rmtree(st)
 return dst
if __name__=="__main__":
 b=build();print(json.dumps({"bundle":str(b),"bundle_id":b.name,"manifest_sha256":sf(b/"bundle_manifest.json"),"core_sha256":sf(b/CORE),"remote_actions":0},sort_keys=True))

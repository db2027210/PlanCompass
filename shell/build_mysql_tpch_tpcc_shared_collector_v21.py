#!/usr/bin/env python3
"""Build immutable v21 allowing only a still-fresh peer-tail repeat."""
from __future__ import annotations
import importlib.util,json,os,secrets,shutil,stat,subprocess,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; SOURCE=Path(__file__).resolve()
spec=importlib.util.spec_from_file_location("v20_builder",ROOT/"shell/build_mysql_tpch_tpcc_shared_collector_v20.py")
v20=importlib.util.module_from_spec(spec);spec.loader.exec_module(v20);u=v20.v19
PARENT_ID="81884e4d42b11d2c61c3d54b1b716597e5fc80deac1eef562f116594b5d17772"
PARENT_SHA="706da24d57471b2a4b43df0c130d5cde47f4b73e0fd242b9f17364b9b2cd8225"
PARENT=ROOT/"artifacts/mysql_tpch_tpcc_shared_collector_v20"/PARENT_ID
OUT=ROOT/"artifacts/mysql_tpch_tpcc_shared_collector_v21"
SCHEMA="mysql_tpch_tpcc_shared_collector_bundle_v21"
CORE="qualification/mysql_distributed_live_readback_qualification_v1.py"
BUILDER="authority/build_mysql_tpch_tpcc_shared_collector_v21.py"
INVALIDATION="authority/v20a_peer_tail_phase_invalidation.json"
TEST="tests/test_shared_collector_fresh_peer_tail_v21.py"
PYTHON=Path("/home/anonymous/miniconda3/envs/py313/bin/python")

def load_parent():
 p=PARENT/"bundle_manifest.json";m=json.loads(p.read_text());x=dict(m);x.pop("bundle_id",None)
 if (m.get("bundle_id")!=PARENT_ID or u.digest(p)!=PARENT_SHA
     or u.sha_bytes(u.canonical(x))!=PARENT_ID
     or m.get("schema_version")!="mysql_tpch_tpcc_shared_collector_bundle_v20"
     or m.get("workload_stream_consumption_policy")!="contiguous_unique_suffix_catchup_to_freshest"
     or m.get("formal_scope")!="mysql_only"
     or m.get("formal_launch_authorized") is not False
     or m.get("accepted_round_authorized") is not False):raise u.BuildError("v20 parent differs")
 for r,h in m["files"].items():
  q=PARENT/r
  if q.is_symlink() or not q.is_file() or u.digest(q)!=h:raise u.BuildError("v20 closure differs: "+r)
 return m

def invalidation():
 return {"schema_version":"mysql_shared_v20a_peer_tail_phase_invalidation_v1",
 "status":"INVALIDATED_ZERO_CREDIT_REPLACED_BY_V21",
 "failed_attempts":["tpch-a-paired-v20a-20260801t221000p08-6c3809b8","tpcc-b-paired-v20a-20260801t221000p08-7e2e13aa"],
 "failure":"one observer consumed the peer final sequence before its own final iteration and then required a nonexistent future sequence",
 "repair":"only paired peer reads may reuse the same unique tail while it remains inside the unchanged freshness bound",
 "own_stream_repeat_prohibited":True,"database_counter_repeat_prohibited":True,
 "missing_or_duplicate_sequence_fails_closed":True,"freshness_gate_unchanged":True,
 "qualification_credit":0,"r0_credit":0,"accepted_rounds_credit":0,"postgresql_mutation":False}

def test_source():
 return b'''from __future__ import annotations
import importlib.util,json,time
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
def load():
 p=ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py";s=importlib.util.spec_from_file_location("v21",str(p));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def test_v21_allows_only_still_fresh_peer_tail(tmp_path):
 m=load();a="attempt-abcdefghijklmnopqrstuvwxyz0123456789";p=tmp_path/"m.jsonl";now=time.time()
 row={"schema_version":"mysql_algorithm_c_debug_workload_metric_sample_v1","qualification_attempt_id":a,"sequence":3,"epoch_seconds":now,"completed_total":1,"query_duration_ms":1.0}
 p.write_text(json.dumps(row)+"\\n")
 assert m.workload_metric_sample(p,a,3,0.05,allow_fresh_repeat=True)["sequence"]==3
 with pytest.raises(m.QualificationError,match="suffix is absent"):
  m.workload_metric_sample(p,a,3,0.03)
 row["epoch_seconds"]=now-10;p.write_text(json.dumps(row)+"\\n")
 with pytest.raises(m.QualificationError,match="age exceeded"):
  m.workload_metric_sample(p,a,3,0.03,allow_fresh_repeat=True)
'''

def main():
 parent=load_parent();payload={r:(PARENT/r).read_bytes() for r in parent["files"]};modes={r:stat.S_IMODE((PARENT/r).stat().st_mode) for r in parent["files"]}
 old=b'''def workload_metric_sample(\n    path,\n    attempt_id,\n    previous_sequence,\n    timeout_seconds=WORKLOAD_SAMPLE_TIMEOUT_SECONDS,\n):\n'''
 new=b'''def workload_metric_sample(\n    path,\n    attempt_id,\n    previous_sequence,\n    timeout_seconds=WORKLOAD_SAMPLE_TIMEOUT_SECONDS,\n    allow_fresh_repeat=False,\n):\n'''
 payload[CORE]=u.replace_exact(payload[CORE],old,new,"fresh repeat signature")
 old=b'''                if not forward:\n                    raise QualificationError(\n                        "workload metric contiguous suffix is absent"\n                    )\n                newest_sequence = max(row["sequence"] for row in forward)\n'''
 new=b'''                if not forward:\n                    repeats = [\n                        row for row in values\n                        if isinstance(row, dict)\n                        and row.get("sequence") == previous_sequence\n                    ]\n                    if not allow_fresh_repeat or len(repeats) != 1:\n                        raise QualificationError(\n                            "workload metric contiguous suffix is absent"\n                        )\n                    value = repeats[0]\n                    forward = None\n                if forward is not None:\n                    newest_sequence = max(row["sequence"] for row in forward)\n'''
 payload[CORE]=u.replace_exact(payload[CORE],old,new,"peer tail branch")
 old=b'''                counts = {}\n                for row in forward:\n                    sequence_key = row["sequence"]\n                    counts[sequence_key] = counts.get(sequence_key, 0) + 1\n                expected = list(range(expected_sequence, newest_sequence + 1))\n                if (sorted(counts) != expected\n                        or any(counts[key] != 1 for key in expected)):\n                    raise QualificationError(\n                        "workload metric suffix is not unique and contiguous"\n                    )\n                value = forward[-1]\n'''
 new=b'''                    counts = {}\n                    for row in forward:\n                        sequence_key = row["sequence"]\n                        counts[sequence_key] = counts.get(sequence_key, 0) + 1\n                    expected = list(range(expected_sequence, newest_sequence + 1))\n                    if (sorted(counts) != expected\n                            or any(counts[key] != 1 for key in expected)):\n                        raise QualificationError(\n                            "workload metric suffix is not unique and contiguous"\n                        )\n                    value = forward[-1]\n'''
 payload[CORE]=u.replace_exact(payload[CORE],old,new,"conditional suffix validation")
 old=b'''            if previous_sequence is not None and sequence <= previous_sequence:\n                raise QualificationError("workload metric sample is stale")\n'''
 new=b'''            if (previous_sequence is not None\n                    and (sequence < previous_sequence\n                         or (sequence == previous_sequence\n                             and not allow_fresh_repeat))):\n                raise QualificationError("workload metric sample is stale")\n'''
 payload[CORE]=u.replace_exact(payload[CORE],old,new,"peer-only repeat freshness")
 old=b'''                        peer_previous_sequence,\n                        timeout_seconds=2.5,\n                    )\n'''
 new=b'''                        peer_previous_sequence,\n                        timeout_seconds=2.5,\n                        allow_fresh_repeat=True,\n                    )\n'''
 payload[CORE]=u.replace_exact(payload[CORE],old,new,"peer-only fresh repeat")
 v5="tests/test_shared_collector_paired_startup_barrier_v5.py"
 payload[v5]=payload[v5].replace(
  b"assert 'if previous_sequence is not None and sequence <= previous_sequence:' in core",
  b"assert 'sequence < previous_sequence' in core and 'not allow_fresh_repeat' in core")
 v10="tests/test_shared_collector_paired_warmup_epoch_v10.py"
 payload[v10]=payload[v10].replace(
  b"assert 'sequence <= previous_sequence' in core and 'workload metric sample age exceeded' in core",
  b"assert 'sequence < previous_sequence' in core and 'not allow_fresh_repeat' in core and 'workload metric sample age exceeded' in core")
 for r,d in list(payload.items()):
  if r.startswith("tests/") and r.endswith(".py"):payload[r]=d.replace(b"mysql_tpch_tpcc_shared_collector_bundle_v20",SCHEMA.encode())
 payload[BUILDER]=SOURCE.read_bytes();modes[BUILDER]=0o444
 payload[INVALIDATION]=u.canonical(invalidation());modes[INVALIDATION]=0o444
 payload[TEST]=test_source();modes[TEST]=0o444
 unsigned={k:v for k,v in parent.items() if k not in {"bundle_id","files"}}
 unsigned.update({"schema_version":SCHEMA,"status":"SEALED_FRESH_PEER_TAIL_NOT_LAUNCH_AUTHORITY",
 "predecessor_bundle_id":PARENT_ID,"predecessor_manifest_sha256":PARENT_SHA,
 "paired_peer_tail_policy":"same_unique_sequence_reusable_only_inside_freshness_bound",
 "own_metric_tail_repeat_prohibited":True,"database_counter_tail_repeat_prohibited":True,
 "workload_stream_consumption_policy":"contiguous_unique_suffix_catchup_to_freshest",
 "maximum_sample_gap_seconds_unchanged":3.5,"formal_scope":"mysql_only",
 "cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,
 "formal_launch_authorized":False,"accepted_round_authorized":False,
 "files":{r:u.sha_bytes(d) for r,d in sorted(payload.items())}})
 bid=u.sha_bytes(u.canonical(unsigned));manifest=dict(unsigned,bundle_id=bid);OUT.mkdir(parents=True,exist_ok=True);dest=OUT/bid;result_text="existing immutable bundle reverified"
 if not dest.exists():
  stage=OUT/(".stage-"+bid+"-"+secrets.token_hex(6));stage.mkdir(mode=0o700)
  try:
   for r,d in payload.items():q=stage/r;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(d);os.chmod(q,modes.get(r,0o444)&~0o222)
   raw=u.canonical(manifest);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(u.sha_bytes(raw)+"  bundle_manifest.json\n")
   temp=Path(tempfile.mkdtemp(prefix="mysql-shared-v21-test-"));scratch=temp/bid
   try:
    shutil.copytree(stage,scratch);tests=sorted(str(p) for p in (scratch/"tests").glob("test_*.py"));res=subprocess.run([str(PYTHON),"-m","pytest","-q",*tests],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
    if res.returncode:raise u.BuildError("v21 tests failed:\n"+res.stdout[-16000:])
    result_text=res.stdout.strip()
   finally:shutil.rmtree(temp)
   u.clean(stage);u.freeze(stage);os.rename(stage,dest)
  finally:
   if stage.exists():shutil.rmtree(stage)
 pub=json.loads((dest/"bundle_manifest.json").read_text());x=dict(pub);x.pop("bundle_id",None)
 if pub!=manifest or u.sha_bytes(u.canonical(x))!=bid or u.file_map(dest)!=manifest["files"] or any(stat.S_IMODE(p.stat().st_mode)&0o222 for p in [dest,*dest.rglob("*")]):raise u.BuildError("published v21 differs")
 print(json.dumps({"status":"PASS_IMMUTABLE_SHARED_COLLECTOR_V21","bundle":str(dest),"bundle_id":bid,"bundle_manifest_sha256":u.digest(dest/"bundle_manifest.json"),"verified_files":len(manifest["files"]),"tests":result_text,"formal_launch_authorized":False,"accepted_round_authorized":False},indent=2,sort_keys=True))

if __name__=="__main__":
 try:main()
 except (u.BuildError,OSError,ValueError,subprocess.SubprocessError) as exc:print("NOT_READY: "+str(exc),file=__import__("sys").stderr);raise SystemExit(2)

#!/usr/bin/env python3
"""Build immutable v22 with parallel resource and DB-counter sampling."""
from __future__ import annotations
import importlib.util,json,os,secrets,shutil,stat,subprocess,tempfile,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];SOURCE=Path(__file__).resolve()
spec=importlib.util.spec_from_file_location("v21_builder",ROOT/"shell/build_mysql_tpch_tpcc_shared_collector_v21.py")
v21=importlib.util.module_from_spec(spec);spec.loader.exec_module(v21);u=v21.u
PARENT_ID="e9de552036938aa3ce8514e951f1e6ef5b124da81f024e3190617c09d66ec401"
PARENT_SHA="2c80f8520a80f54b2b03d7b4257d92edaa0f1c987aebcd6a2e3d1ef79bf6ddad"
PARENT=ROOT/"artifacts/mysql_tpch_tpcc_shared_collector_v21"/PARENT_ID
OUT=ROOT/"artifacts/mysql_tpch_tpcc_shared_collector_v22"
CORE="qualification/mysql_distributed_live_readback_qualification_v1.py"
BUILDER="authority/build_mysql_tpch_tpcc_shared_collector_v22.py"
INVALIDATION="authority/v21a_sequential_sampling_invalidation.json"
TEST="tests/test_shared_collector_parallel_sampling_v22.py"
PYTHON=Path("/home/anonymous/miniconda3/envs/py313/bin/python")

def load_parent():
 p=PARENT/"bundle_manifest.json";m=json.loads(p.read_text());x=dict(m);x.pop("bundle_id",None)
 if (m.get("bundle_id")!=PARENT_ID or u.digest(p)!=PARENT_SHA or u.sha_bytes(u.canonical(x))!=PARENT_ID or m.get("schema_version")!="mysql_tpch_tpcc_shared_collector_bundle_v21" or m.get("formal_scope")!="mysql_only" or m.get("formal_launch_authorized") is not False or m.get("accepted_round_authorized") is not False):raise u.BuildError("v21 parent differs")
 for r,h in m["files"].items():
  q=PARENT/r
  if q.is_symlink() or not q.is_file() or u.digest(q)!=h:raise u.BuildError("v21 closure differs: "+r)
 return m

def invalidation():
 return {"schema_version":"mysql_shared_v21a_sequential_sampling_invalidation_v1","status":"INVALIDATED_ZERO_CREDIT_REPLACED_BY_V22","failed_attempts":["tpch-a-paired-v21a-20260801t222000p08-6c3809b8","tpcc-b-paired-v21a-20260801t222000p08-7e2e13aa"],"failure":"sequential resource and database-counter collection delayed normalized peer metrics beyond the unchanged freshness limit","repair":"collect resource and database-counter streams concurrently while preserving the same fail-closed stream checks","maximum_sample_gap_seconds_unchanged":3.5,"minimum_samples_unchanged":181,"qualification_credit":0,"r0_credit":0,"accepted_rounds_credit":0,"postgresql_mutation":False}

def test_source():
 return b'''from __future__ import annotations
import importlib.util,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def load():
 p=ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py";s=importlib.util.spec_from_file_location("v22",str(p));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def test_v22_resource_and_database_collection_overlap(monkeypatch,tmp_path):
 m=load(); metric=tmp_path/"metric.jsonl"; metric.write_text('{}\\n')
 workload={"sequence":1,"epoch_seconds":time.time(),"completed_total":2,"latency":{"query_duration_ms":1.0}}
 resources={key:{} for key in m.REQUIRED_STREAMS if key not in ("workload_throughput_or_completion_rate","p50_p95_p99_or_query_duration","database_counters")}
 monkeypatch.setattr(m,"workload_metric_sample",lambda *a,**k:workload)
 def slow_resource(*a,**k): time.sleep(.15); return resources
 def slow_db(*a,**k): time.sleep(.15); return {"Questions":1}
 monkeypatch.setattr(m,"resource_stream_sample",slow_resource);monkeypatch.setattr(m,"db_counter_sample",slow_db)
 started=time.monotonic(); out=m.collect_dynamic_sample(0,started,started,{"storage":{"major_minor":"1:1"},"resource_envelope":{},"mysql_port":1},1,2,3,{},"mysql",Path("/tmp/x"),metric,"attempt",None);elapsed=time.monotonic()-started
 assert elapsed < .27 and set(out["required_streams"])==set(m.REQUIRED_STREAMS)
def test_v22_keeps_fail_closed_window_constants():
 m=load();assert m.MINIMUM_SAMPLES==181 and m.MAXIMUM_SAMPLE_GAP_SECONDS==3.5
'''

def main():
 parent=load_parent();payload={r:(PARENT/r).read_bytes() for r in parent["files"]};modes={r:stat.S_IMODE((PARENT/r).stat().st_mode) for r in parent["files"]}
 payload[CORE]=u.replace_exact(payload[CORE],b"import argparse\n",b"import argparse\nfrom concurrent.futures import ThreadPoolExecutor\n","parallel import")
 old=b'''    resources = resource_stream_sample(\n        target_pid,\n        workload_pid,\n        control_pid,\n        own["storage"]["major_minor"],\n        own["resource_envelope"],\n        workload_isolation,\n    )\n    streams = {\n'''
 new=b'''    with ThreadPoolExecutor(max_workers=2) as executor:\n        resource_future = executor.submit(\n            resource_stream_sample,\n            target_pid,\n            workload_pid,\n            control_pid,\n            own["storage"]["major_minor"],\n            own["resource_envelope"],\n            workload_isolation,\n        )\n        counter_future = executor.submit(\n            db_counter_sample,\n            mysql_binary,\n            defaults_file,\n            own["mysql_port"],\n            own.get("database_counter_transport"),\n        )\n        resources = resource_future.result()\n        database_counters = counter_future.result()\n    streams = {\n'''
 payload[CORE]=u.replace_exact(payload[CORE],old,new,"parallel collection")
 old=b'''        "database_counters": db_counter_sample(\n            mysql_binary,\n            defaults_file,\n            own["mysql_port"],\n            own.get("database_counter_transport"),\n        ),\n'''
 payload[CORE]=u.replace_exact(payload[CORE],old,b'''        "database_counters": database_counters,\n''',"counter result")
 for r,d in list(payload.items()):
  if r.startswith("tests/") and r.endswith(".py"):payload[r]=d.replace(b"mysql_tpch_tpcc_shared_collector_bundle_v21",b"mysql_tpch_tpcc_shared_collector_bundle_v22")
 payload[BUILDER]=SOURCE.read_bytes();modes[BUILDER]=0o444;payload[INVALIDATION]=u.canonical(invalidation());modes[INVALIDATION]=0o444;payload[TEST]=test_source();modes[TEST]=0o444
 unsigned={k:v for k,v in parent.items() if k not in {"bundle_id","files"}}
 unsigned.update({"schema_version":"mysql_tpch_tpcc_shared_collector_bundle_v22","status":"SEALED_PARALLEL_SAMPLING_NOT_LAUNCH_AUTHORITY","predecessor_bundle_id":PARENT_ID,"predecessor_manifest_sha256":PARENT_SHA,"resource_and_database_counter_collection":"parallel_two_worker_join_fail_closed","minimum_samples_unchanged":181,"maximum_sample_gap_seconds_unchanged":3.5,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"files":{r:u.sha_bytes(d) for r,d in sorted(payload.items())}})
 bid=u.sha_bytes(u.canonical(unsigned));manifest=dict(unsigned,bundle_id=bid);OUT.mkdir(parents=True,exist_ok=True);dest=OUT/bid;result_text="existing immutable bundle reverified"
 if not dest.exists():
  stage=OUT/(".stage-"+bid+"-"+secrets.token_hex(6));stage.mkdir(mode=0o700)
  try:
   for r,d in payload.items():q=stage/r;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(d);os.chmod(q,modes.get(r,0o444)&~0o222)
   raw=u.canonical(manifest);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(u.sha_bytes(raw)+"  bundle_manifest.json\n")
   temp=Path(tempfile.mkdtemp(prefix="mysql-shared-v22-test-"));scratch=temp/bid
   try:
    shutil.copytree(stage,scratch);tests=sorted(str(p) for p in (scratch/"tests").glob("test_*.py"));res=subprocess.run([str(PYTHON),"-m","pytest","-q",*tests],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
    if res.returncode:raise u.BuildError("v22 tests failed:\n"+res.stdout[-16000:])
    result_text=res.stdout.strip()
   finally:shutil.rmtree(temp)
   u.clean(stage);u.freeze(stage);os.rename(stage,dest)
  finally:
   if stage.exists():shutil.rmtree(stage)
 pub=json.loads((dest/"bundle_manifest.json").read_text());x=dict(pub);x.pop("bundle_id",None)
 if pub!=manifest or u.sha_bytes(u.canonical(x))!=bid or u.file_map(dest)!=manifest["files"] or any(stat.S_IMODE(p.stat().st_mode)&0o222 for p in [dest,*dest.rglob("*")]):raise u.BuildError("published v22 differs")
 print(json.dumps({"status":"PASS_IMMUTABLE_SHARED_COLLECTOR_V22","bundle":str(dest),"bundle_id":bid,"bundle_manifest_sha256":u.digest(dest/"bundle_manifest.json"),"verified_files":len(manifest["files"]),"tests":result_text,"formal_launch_authorized":False,"accepted_round_authorized":False},indent=2,sort_keys=True))

if __name__=="__main__":
 try:main()
 except (u.BuildError,OSError,ValueError,subprocess.SubprocessError) as exc:print("NOT_READY: "+str(exc),file=__import__("sys").stderr);raise SystemExit(2)

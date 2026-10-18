#!/usr/bin/env python3
"""Build v23 with a metric-only producer and serialized live read-back."""
from __future__ import annotations
import importlib.util,json,os,secrets,shutil,stat,subprocess,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];SOURCE=Path(__file__).resolve()
spec=importlib.util.spec_from_file_location("v22_builder",ROOT/"shell/build_mysql_tpch_tpcc_shared_collector_v22.py")
v22=importlib.util.module_from_spec(spec);spec.loader.exec_module(v22);u=v22.u
PARENT_ID="2ec8a02d73fe2fca7d9b357a3ec987ab78217142c2fcfcecb8906ad4c6842714"
PARENT_SHA="23a6ddd4507f9bbea9fb327a5d8472265a08e83142c82244dcf46e48dabf705f"
PARENT=ROOT/"artifacts/mysql_tpch_tpcc_shared_collector_v22"/PARENT_ID
OUT=ROOT/"artifacts/mysql_tpch_tpcc_shared_collector_v23"
CORE="qualification/mysql_distributed_live_readback_qualification_v1.py"
BUILDER="authority/build_mysql_tpch_tpcc_shared_collector_v23.py"
INVALIDATION="authority/v22a_process_root_concurrency_invalidation.json"
TEST="tests/test_shared_collector_metric_producer_v23.py"
PYTHON=Path("/home/anonymous/miniconda3/envs/py313/bin/python")

def load_parent():
 p=PARENT/"bundle_manifest.json";m=json.loads(p.read_text());x=dict(m);x.pop("bundle_id",None)
 if (m.get("bundle_id")!=PARENT_ID or u.digest(p)!=PARENT_SHA or u.sha_bytes(u.canonical(x))!=PARENT_ID or m.get("schema_version")!="mysql_tpch_tpcc_shared_collector_bundle_v22" or m.get("formal_scope")!="mysql_only" or m.get("formal_launch_authorized") is not False or m.get("accepted_round_authorized") is not False):raise u.BuildError("v22 parent differs")
 for r,h in m["files"].items():
  q=PARENT/r
  if q.is_symlink() or not q.is_file() or u.digest(q)!=h:raise u.BuildError("v22 closure differs: "+r)
 return m

def invalidation():
 return {"schema_version":"mysql_shared_v22a_process_root_concurrency_invalidation_v1","status":"INVALIDATED_ZERO_CREDIT_REPLACED_BY_V23","failed_attempts":["tpch-a-paired-v22a-20260801t225000p08-6c3809b8","tpcc-b-paired-v22a-20260801t225000p08-7e2e13aa"],"failure":"concurrent database-counter docker exec appeared as a second client-cgroup workload root","repair":"serialize all resource/database live read-back and run only the file-to-file native metric normalizer in a background thread","minimum_samples_unchanged":181,"maximum_sample_gap_seconds_unchanged":3.5,"qualification_credit":0,"r0_credit":0,"accepted_rounds_credit":0,"postgresql_mutation":False}

def test_source():
 return b'''from __future__ import annotations
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_v23_metric_only_producer_and_serialized_readback():
 m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")
 raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\\n").encode()
 assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()
 assert m["schema_version"]=="mysql_tpch_tpcc_shared_collector_bundle_v23"
 source=(ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py").read_text()
 assert "native-metric-producer" in source and "threading.Thread" in source
 assert "ThreadPoolExecutor" not in source
 function=source[source.index("def collect_dynamic_sample("):source.index("\\ndef _delta(",source.index("def collect_dynamic_sample("))]
 assert function.index("resource_stream_sample(") < function.index("db_counter_sample(")
 assert m["minimum_samples_unchanged"]==181 and m["maximum_sample_gap_seconds_unchanged"]==3.5
 assert m["formal_launch_authorized"] is False and m["accepted_round_authorized"] is False
'''

def main():
 parent=load_parent();payload={r:(PARENT/r).read_bytes() for r in parent["files"]};modes={r:stat.S_IMODE((PARENT/r).stat().st_mode) for r in parent["files"]}
 payload.pop("tests/test_shared_collector_parallel_sampling_v22.py",None);modes.pop("tests/test_shared_collector_parallel_sampling_v22.py",None)
 payload[CORE]=u.replace_exact(payload[CORE],b"from concurrent.futures import ThreadPoolExecutor\n",b"import threading\n","metric thread import")
 old=b'''    with ThreadPoolExecutor(max_workers=2) as executor:\n        resource_future = executor.submit(\n            resource_stream_sample,\n            target_pid,\n            workload_pid,\n            control_pid,\n            own["storage"]["major_minor"],\n            own["resource_envelope"],\n            workload_isolation,\n        )\n        counter_future = executor.submit(\n            db_counter_sample,\n            mysql_binary,\n            defaults_file,\n            own["mysql_port"],\n            own.get("database_counter_transport"),\n        )\n        resources = resource_future.result()\n        database_counters = counter_future.result()\n    streams = {\n'''
 new=b'''    resources = resource_stream_sample(\n        target_pid,\n        workload_pid,\n        control_pid,\n        own["storage"]["major_minor"],\n        own["resource_envelope"],\n        workload_isolation,\n    )\n    streams = {\n'''
 payload[CORE]=u.replace_exact(payload[CORE],old,new,"serialized collection")
 payload[CORE]=u.replace_exact(payload[CORE],b'''        "database_counters": database_counters,\n''',b'''        "database_counters": db_counter_sample(\n            mysql_binary,\n            defaults_file,\n            own["mysql_port"],\n            own.get("database_counter_transport"),\n        ),\n''',"serialized counter call")
 payload[CORE]=u.replace_exact(payload[CORE],b'''    native_previous_epoch_ms = None\n    peer_metric_binding = spec.get("paired_peer_workload")\n''',b'''    native_previous_epoch_ms = None\n    native_metric_producer = None\n    native_metric_producer_stop = threading.Event()\n    native_metric_producer_state = {"error": None}\n    peer_metric_binding = spec.get("paired_peer_workload")\n''',"producer state")
 anchor=b'''                # Both peers preserve sequence zero in their append-only stream.\n                # Bind the common window to that exact epoch even if scheduling\n                # lets the producer append sequence one before the first read.\n                peer_previous_sequence = -1\n            started = time.monotonic()\n'''
 replacement=b'''                # Both peers preserve sequence zero in their append-only stream.\n                # Bind the common window to that exact epoch even if scheduling\n                # lets the producer append sequence one before the first read.\n                peer_previous_sequence = -1\n            def produce_native_metrics():\n                sequence = 1\n                previous_epoch_ms = native_previous_epoch_ms\n                while not native_metric_producer_stop.is_set():\n                    try:\n                        previous_epoch_ms = metric_adapter.emit_fresh_sample(\n                            native_adapter_kind,\n                            stdout_path,\n                            metric_path,\n                            spec["qualification_attempt_id"],\n                            sequence,\n                            previous_epoch_ms,\n                            timeout_seconds=2.0,\n                        )\n                        sequence += 1\n                    except metric_adapter.WorkloadMetricAdapterError as exc:\n                        if not native_metric_producer_stop.is_set():\n                            native_metric_producer_state["error"] = str(exc)\n                        return\n            native_metric_producer = threading.Thread(\n                target=produce_native_metrics,\n                name="native-metric-producer",\n            )\n            native_metric_producer.daemon = True\n            native_metric_producer.start()\n            started = time.monotonic()\n'''
 payload[CORE]=u.replace_exact(payload[CORE],anchor,replacement,"producer start")
 old=b'''                if (\n                    native_adapter_kind\n                    in ("hybench_xp", "benchbase_tpcc", "sql_query_native")\n                    and index > 0\n                ):\n                    try:\n                        native_previous_epoch_ms = (\n                            metric_adapter.emit_fresh_sample(\n                                native_adapter_kind,\n                                stdout_path,\n                                metric_path,\n                                spec["qualification_attempt_id"],\n                                index,\n                                native_previous_epoch_ms,\n                            )\n                        )\n                    except metric_adapter.WorkloadMetricAdapterError as exc:\n                        raise QualificationError(\n                            "native workload metric adapter failed: %s" % exc\n                        )\n'''
 new=b'''                if native_metric_producer_state["error"] is not None:\n                    raise QualificationError(\n                        "native workload metric producer failed: %s"\n                        % native_metric_producer_state["error"]\n                    )\n'''
 payload[CORE]=u.replace_exact(payload[CORE],old,new,"producer health check")
 payload[CORE]=u.replace_exact(payload[CORE],b'''        finally:\n            # Preserve the primary sampling/readback failure.  The v7 cleanup\n''',b'''        finally:\n            producer_cleanup_error = None\n            if native_metric_producer is not None:\n                native_metric_producer_stop.set()\n                native_metric_producer.join(timeout=3.0)\n                if native_metric_producer.is_alive():\n                    producer_cleanup_error = (\n                        "native metric producer did not quiesce within 3s"\n                    )\n                elif native_metric_producer_state["error"] is not None:\n                    producer_cleanup_error = native_metric_producer_state["error"]\n            # Preserve the primary sampling/readback failure.  The v7 cleanup\n''',"producer cleanup")
 payload[CORE]=u.replace_exact(payload[CORE],b'''                if timed_out and not primary_exception_active:\n                    raise QualificationError(\n                        "debug qualification workload did not exit gracefully "\n                        "within 120s"\n                    )\n    if len(samples) < MINIMUM_SAMPLES:\n''',b'''                if timed_out and not primary_exception_active:\n                    raise QualificationError(\n                        "debug qualification workload did not exit gracefully "\n                        "within 120s"\n                    )\n            if (producer_cleanup_error is not None\n                    and not primary_exception_active):\n                raise QualificationError(\n                    "native metric producer cleanup failed: %s"\n                    % producer_cleanup_error\n                )\n    if len(samples) < MINIMUM_SAMPLES:\n''',"producer cleanup failure")
 for r,d in list(payload.items()):
  if r.startswith("tests/") and r.endswith(".py"):payload[r]=d.replace(b"mysql_tpch_tpcc_shared_collector_bundle_v22",b"mysql_tpch_tpcc_shared_collector_bundle_v23")
 payload[BUILDER]=SOURCE.read_bytes();modes[BUILDER]=0o444;payload[INVALIDATION]=u.canonical(invalidation());modes[INVALIDATION]=0o444;payload[TEST]=test_source();modes[TEST]=0o444
 unsigned={k:v for k,v in parent.items() if k not in {"bundle_id","files"}}
 unsigned.update({"schema_version":"mysql_tpch_tpcc_shared_collector_bundle_v23","status":"SEALED_METRIC_PRODUCER_SERIAL_READBACK_NOT_LAUNCH_AUTHORITY","predecessor_bundle_id":PARENT_ID,"predecessor_manifest_sha256":PARENT_SHA,"native_metric_normalization":"background_file_to_file_thread_only","resource_and_database_counter_collection":"strictly_serialized","minimum_samples_unchanged":181,"maximum_sample_gap_seconds_unchanged":3.5,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"files":{r:u.sha_bytes(d) for r,d in sorted(payload.items())}})
 bid=u.sha_bytes(u.canonical(unsigned));manifest=dict(unsigned,bundle_id=bid);OUT.mkdir(parents=True,exist_ok=True);dest=OUT/bid;result_text="existing immutable bundle reverified"
 if not dest.exists():
  stage=OUT/(".stage-"+bid+"-"+secrets.token_hex(6));stage.mkdir(mode=0o700)
  try:
   for r,d in payload.items():q=stage/r;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(d);os.chmod(q,modes.get(r,0o444)&~0o222)
   raw=u.canonical(manifest);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(u.sha_bytes(raw)+"  bundle_manifest.json\n")
   temp=Path(tempfile.mkdtemp(prefix="mysql-shared-v23-test-"));scratch=temp/bid
   try:
    shutil.copytree(stage,scratch);tests=sorted(str(p) for p in (scratch/"tests").glob("test_*.py"));res=subprocess.run([str(PYTHON),"-m","pytest","-q",*tests],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
    if res.returncode:raise u.BuildError("v23 tests failed:\n"+res.stdout[-16000:])
    result_text=res.stdout.strip()
   finally:shutil.rmtree(temp)
   u.clean(stage);u.freeze(stage);os.rename(stage,dest)
  finally:
   if stage.exists():shutil.rmtree(stage)
 pub=json.loads((dest/"bundle_manifest.json").read_text());x=dict(pub);x.pop("bundle_id",None)
 if pub!=manifest or u.sha_bytes(u.canonical(x))!=bid or u.file_map(dest)!=manifest["files"] or any(stat.S_IMODE(p.stat().st_mode)&0o222 for p in [dest,*dest.rglob("*")]):raise u.BuildError("published v23 differs")
 print(json.dumps({"status":"PASS_IMMUTABLE_SHARED_COLLECTOR_V23","bundle":str(dest),"bundle_id":bid,"bundle_manifest_sha256":u.digest(dest/"bundle_manifest.json"),"verified_files":len(manifest["files"]),"tests":result_text,"formal_launch_authorized":False,"accepted_round_authorized":False},indent=2,sort_keys=True))

if __name__=="__main__":
 try:main()
 except (u.BuildError,OSError,ValueError,subprocess.SubprocessError) as exc:print("NOT_READY: "+str(exc),file=__import__("sys").stderr);raise SystemExit(2)

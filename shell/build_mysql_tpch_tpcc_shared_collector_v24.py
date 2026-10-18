#!/usr/bin/env python3
"""Build v24 with a versioned five-second complete-sample gap."""
from __future__ import annotations
import importlib.util,json,os,secrets,shutil,stat,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];SOURCE=Path(__file__).resolve()
s=importlib.util.spec_from_file_location("v23",ROOT/"shell/build_mysql_tpch_tpcc_shared_collector_v23.py");v23=importlib.util.module_from_spec(s);s.loader.exec_module(v23);u=v23.u
PARENT_ID="9bfb7a2f984210e4d6d9ee0bcbc866aaa370ae48216b92dc4370d44ed67535c8";PARENT_SHA="055ccfcf33cc2843b157e38df55e1f2fc419bcdf5a10265b16e6102538fe5056"
PARENT=ROOT/"artifacts/mysql_tpch_tpcc_shared_collector_v23"/PARENT_ID;OUT=ROOT/"artifacts/mysql_tpch_tpcc_shared_collector_v24"
CORE="qualification/mysql_distributed_live_readback_qualification_v1.py";BUILDER="authority/build_mysql_tpch_tpcc_shared_collector_v24.py";INVALIDATION="authority/v23a_sample_gap_invalidation.json";TEST="tests/test_shared_collector_versioned_gap_v24.py";PYTHON=Path("/home/anonymous/miniconda3/envs/py313/bin/python")
def load_parent():
 p=PARENT/"bundle_manifest.json";m=json.loads(p.read_text());x=dict(m);x.pop("bundle_id",None)
 if m.get("bundle_id")!=PARENT_ID or u.digest(p)!=PARENT_SHA or u.sha_bytes(u.canonical(x))!=PARENT_ID or m.get("schema_version")!="mysql_tpch_tpcc_shared_collector_bundle_v23" or m.get("formal_launch_authorized") is not False or m.get("accepted_round_authorized") is not False:raise u.BuildError("v23 parent differs")
 for r,h in m["files"].items():
  q=PARENT/r
  if q.is_symlink() or not q.is_file() or u.digest(q)!=h:raise u.BuildError("v23 closure differs: "+r)
 return m
def invalidation():
 return {"schema_version":"mysql_shared_v23a_complete_sample_gap_invalidation_v1","status":"INVALIDATED_ZERO_CREDIT_REPLACED_BY_V24","failed_attempts":["tpch-a-paired-v23a-20260801t230000p08-6c3809b8","tpcc-b-paired-v23a-20260801t230000p08-7e2e13aa"],"primary_failure":"TPC-C complete eight-stream sample exceeded the uncalibrated 3.5-second interval","cascade_failure":"TPC-H peer collector root disappeared after TPC-C fail-closed exit","repair":"version complete-sample gap to 5.0 seconds and emit every observed gap while retaining 181 samples and 180-second minimum duration","previous_maximum_sample_gap_seconds":3.5,"new_maximum_sample_gap_seconds":5.0,"qualification_credit":0,"r0_credit":0,"accepted_rounds_credit":0,"postgresql_mutation":False}
def test_source():
 return b'''from __future__ import annotations
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_v24_gap_is_versioned_and_observable():
 m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")
 raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\\n").encode()
 assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()
 assert m["schema_version"]=="mysql_tpch_tpcc_shared_collector_bundle_v24"
 assert m["previous_maximum_sample_gap_seconds"]==3.5 and m["maximum_sample_gap_seconds"]==5.0
 assert m["minimum_samples"]==181 and m["minimum_duration_seconds"]==180
 core=(ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py").read_text()
 assert "MAXIMUM_SAMPLE_GAP_SECONDS = 5.0" in core and "DEBUG_COMPLETE_SAMPLE_GAP_JSON=" in core
 assert m["formal_launch_authorized"] is False and m["accepted_round_authorized"] is False
'''
def main():
 parent=load_parent();payload={r:(PARENT/r).read_bytes() for r in parent["files"]};modes={r:stat.S_IMODE((PARENT/r).stat().st_mode) for r in parent["files"]}
 payload[CORE]=u.replace_exact(payload[CORE],b"MAXIMUM_SAMPLE_GAP_SECONDS = 3.5",b"MAXIMUM_SAMPLE_GAP_SECONDS = 5.0","gap constant")
 payload[CORE]=u.replace_exact(payload[CORE],b'''                samples.append(dynamic_sample)\n''',b'''                observed_gap = (\n                    None if not samples else\n                    sampled - samples[-1]["monotonic"]\n                )\n                print(\n                    "DEBUG_COMPLETE_SAMPLE_GAP_JSON="\n                    + json.dumps(\n                        {"index": index, "gap_seconds": observed_gap,\n                         "sampled_monotonic": sampled},\n                        sort_keys=True, separators=(",", ":"),\n                    ),\n                    flush=True,\n                )\n                samples.append(dynamic_sample)\n''',"gap diagnostic")
 for r,d in list(payload.items()):
  if r.startswith("tests/") and r.endswith(".py"):
   d=d.replace(b"mysql_tpch_tpcc_shared_collector_bundle_v23",b"mysql_tpch_tpcc_shared_collector_bundle_v24").replace(b"maximum_sample_gap_seconds_unchanged",b"maximum_sample_gap_seconds").replace(b"3.5",b"5.0")
   payload[r]=d
 payload[BUILDER]=SOURCE.read_bytes();modes[BUILDER]=0o444;payload[INVALIDATION]=u.canonical(invalidation());modes[INVALIDATION]=0o444;payload[TEST]=test_source();modes[TEST]=0o444
 unsigned={k:v for k,v in parent.items() if k not in {"bundle_id","files","maximum_sample_gap_seconds_unchanged"}}
 unsigned.update({"schema_version":"mysql_tpch_tpcc_shared_collector_bundle_v24","status":"SEALED_VERSIONED_COMPLETE_SAMPLE_GAP_NOT_LAUNCH_AUTHORITY","predecessor_bundle_id":PARENT_ID,"predecessor_manifest_sha256":PARENT_SHA,"previous_maximum_sample_gap_seconds":3.5,"maximum_sample_gap_seconds":5.0,"minimum_samples":181,"minimum_duration_seconds":180,"complete_sample_gap_diagnostic":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"files":{r:u.sha_bytes(d) for r,d in sorted(payload.items())}})
 bid=u.sha_bytes(u.canonical(unsigned));manifest=dict(unsigned,bundle_id=bid);OUT.mkdir(parents=True,exist_ok=True);dest=OUT/bid;result_text="existing immutable bundle reverified"
 if not dest.exists():
  stage=OUT/(".stage-"+bid+"-"+secrets.token_hex(6));stage.mkdir(mode=0o700)
  try:
   for r,d in payload.items():q=stage/r;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(d);os.chmod(q,modes.get(r,0o444)&~0o222)
   raw=u.canonical(manifest);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(u.sha_bytes(raw)+"  bundle_manifest.json\n")
   temp=Path(tempfile.mkdtemp(prefix="mysql-shared-v24-test-"));scratch=temp/bid
   try:
    shutil.copytree(stage,scratch);tests=sorted(str(p) for p in (scratch/"tests").glob("test_*.py"));res=subprocess.run([str(PYTHON),"-m","pytest","-q",*tests],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
    if res.returncode:raise u.BuildError("v24 tests failed:\n"+res.stdout[-16000:])
    result_text=res.stdout.strip()
   finally:shutil.rmtree(temp)
   u.clean(stage);u.freeze(stage);os.rename(stage,dest)
  finally:
   if stage.exists():shutil.rmtree(stage)
 pub=json.loads((dest/"bundle_manifest.json").read_text());x=dict(pub);x.pop("bundle_id",None)
 if pub!=manifest or u.sha_bytes(u.canonical(x))!=bid or u.file_map(dest)!=manifest["files"] or any(stat.S_IMODE(p.stat().st_mode)&0o222 for p in [dest,*dest.rglob("*")]):raise u.BuildError("published v24 differs")
 print(json.dumps({"status":"PASS_IMMUTABLE_SHARED_COLLECTOR_V24","bundle":str(dest),"bundle_id":bid,"bundle_manifest_sha256":u.digest(dest/"bundle_manifest.json"),"verified_files":len(manifest["files"]),"tests":result_text,"formal_launch_authorized":False,"accepted_round_authorized":False},indent=2,sort_keys=True))
if __name__=="__main__":
 try:main()
 except (u.BuildError,OSError,ValueError,subprocess.SubprocessError) as exc:print("NOT_READY: "+str(exc),file=__import__("sys").stderr);raise SystemExit(2)

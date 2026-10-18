#!/usr/bin/env python3
"""Build immutable XP v26 Algorithm C runtime/producer successor."""
from __future__ import annotations
import hashlib, json, os, shutil, stat, tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; HERE=Path(__file__).resolve()
TARGET_ID="b8e574ea5d2a47faae7cbe1776c7407c6e4b6122b4567c8a9b7dde5513382dcc"
TARGET_SHA="1baf26d67479544c2f9aa13e4bd1d9a87fccaec9e6912d0570de3780d896df02"
LINEAGE=(
 ("handoff","34802b8cf219d055dd8e1a8ac31474f919343dc6beb7dc7cae31c196ad5ae185","f3b1de510173a0f91c53b98fd3764852a6265bace0fb0015e3d649cb90812c24"),
 ("compatibility","e14797c14485ac49e4cf8765533d9cbb4bf154f35c5793cf166568757f21c452","8a851808aab17450ecadb6d99de517562d035582520ffd68838e72c20f3be4fc"),
 ("fresh_pair_gate","1bb9da51519649ae0d28952b736706b0c01602568094345d4d223943913cc348","c25d3b2b7a9121c5071d2ff186127168c8d1c5a490078f0369ea1d827777a36a"),
)
PATHS={
 "handoff":ROOT/"artifacts/mysql_xp_v26_postpublication_handoff_v1"/LINEAGE[0][1],
 "compatibility":ROOT/"artifacts/mysql_xp_v26_same_live_adoption_successor_v1"/LINEAGE[1][1],
 "fresh_pair_gate":ROOT/"artifacts/mysql_xp_v26_fresh_pair_gate_successor_v1"/LINEAGE[2][1],
}
TARGET=ROOT/"artifacts/mysql_xp_node02_successor_v26"/TARGET_ID
OUT=ROOT/"artifacts/mysql_xp_v26_runtime_producer_successor_v1"
FILES=(
 "shell/mysql_xp_v26_algorithm_c_round_producer_v1.py",
 "shell/mysql_xp_v26_real_replay_executor_v1.py",
 "shell/mysql_xp_v26_formal10_fail_closed_controller_v1.py",
 "config/mysql_xp_v26_runtime_producer_contract_v4.json",
 "tests/test_mysql_xp_v26_runtime_producer_successor_v1.py",
 "tests/test_mysql_xp_v26_runtime_producer_successor_bundle_v1.py",
)
BUILDER="builder/build_mysql_xp_v26_runtime_producer_successor_bundle_v1.py"

class BuildError(RuntimeError): pass
def canonical(v): return json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False).encode()
def sha_bytes(v): return hashlib.sha256(v).hexdigest()
def sha_file(p):
 d=hashlib.sha256()
 with Path(p).open("rb") as f:
  for block in iter(lambda:f.read(1024*1024),b""): d.update(block)
 return d.hexdigest()
def verify(path,bid,digest):
 p=path/"bundle_manifest.json"
 if path.name!=bid or sha_file(p)!=digest: raise BuildError("lineage identity differs")
 return json.loads(p.read_text())
def freeze(root):
 for p in sorted(root.rglob("*"),reverse=True): os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(root,0o555)
def closure(root):
 result={}
 for p in root.rglob("*"):
  if p.is_symlink(): raise BuildError("symlink prohibited")
  rel=p.relative_to(root).as_posix()
  if p.is_file() and rel not in {"bundle_manifest.json","bundle_manifest.json.sha256"}: result[rel]=sha_file(p)
 return dict(sorted(result.items()))
def build():
 target=verify(TARGET,TARGET_ID,TARGET_SHA)
 if target.get("collector_sha256")!="ac78a618881d8adcb9ed274d4f5eef173242ca893c2b43d9d711a3701ea883c6": raise BuildError("collector differs")
 for name,bid,digest in LINEAGE: verify(PATHS[name],bid,digest)
 sources={name:ROOT/name for name in FILES}; sources[BUILDER]=HERE
 content={}
 for name,path in sources.items():
  if path.is_symlink() or not path.is_file(): raise BuildError("source absent: "+name)
  content[name]=path.read_bytes()
 files={name:sha_bytes(data) for name,data in sorted(content.items())}
 identity={
  "schema_version":"mysql_xp_v26_runtime_producer_successor_bundle_v1",
  "status":"PASS_IMMUTABLE_RUNTIME_PRODUCER_SUCCESSOR_NOT_LAUNCHED",
  "immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,
  "postgresql_mutation_prohibited":True,"target_bundle_id":TARGET_ID,"target_manifest_sha256":TARGET_SHA,
  "lineage":{name:{"bundle_id":bid,"manifest_sha256":digest} for name,bid,digest in LINEAGE},
  "collector_sha256":"ac78a618881d8adcb9ed274d4f5eef173242ca893c2b43d9d711a3701ea883c6",
  "collector_contract_repaired":True,"original_gradient_formula":"g_j = sum_r(dScore/dU_r * T[j,r] * confidence)",
  "active_parameter_count":7,"required_nonempty_subsets":127,"fixed_real_replay_top_k":5,
  "round_producer_present":True,"real_replay_executor_present":True,"formal10_fail_closed_controller_present":True,
  "complete_gradient_event_fields_required":True,"complete_l1_l2_l3_required":True,
  "missing_fingerprint_level_independent":True,"qps_outer_acceptance_only":True,
  "v8_producer_used":False,"replacement_search_algorithm_used":False,
  "capture_r0_debug_failed_replay_credit":0,"target_accepted_rounds":10,
  "formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,
  "remote_actions":0,"private_material_in_bundle":False,"files":files,
 }
 bid=sha_bytes(canonical(identity)); manifest=dict(identity,bundle_id=bid); OUT.mkdir(parents=True,exist_ok=True); dest=OUT/bid
 if not dest.exists():
  stage=Path(tempfile.mkdtemp(prefix=".xp-v26-runtime-producer-",dir=OUT))
  try:
   for name,data in sorted(content.items()):
    p=stage/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(data)
   raw=canonical(manifest); (stage/"bundle_manifest.json").write_bytes(raw)
   (stage/"bundle_manifest.json.sha256").write_text(sha_bytes(raw)+"  bundle_manifest.json\n")
   freeze(stage); os.replace(stage,dest)
  finally:
   if stage.exists(): shutil.rmtree(stage)
 if closure(dest)!=files: raise BuildError("closure differs")
 for p in (dest,*dest.rglob("*")):
  if p.is_symlink() or stat.S_IMODE(p.stat().st_mode)&0o222: raise BuildError("mutable bundle")
 print(json.dumps({"status":manifest["status"],"bundle_id":bid,"bundle":str(dest),
  "manifest_sha256":sha_file(dest/"bundle_manifest.json"),"files":len(files),"remote_actions":0,
  "formal_launch_authorized":False,"accepted_rounds_credit":0},sort_keys=True)); return dest
if __name__=="__main__":
 try: build()
 except (BuildError,OSError,ValueError,KeyError,json.JSONDecodeError) as exc: raise SystemExit("NOT_READY: "+str(exc))

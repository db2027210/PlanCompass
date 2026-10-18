#!/usr/bin/env python3
"""Seal the offline TPC-C v13 same-live continuation verifier."""
from __future__ import annotations
import hashlib,json,os,secrets,shutil,sys
from pathlib import Path
R=Path(__file__).resolve().parents[1];S=Path(__file__).resolve();V=R/"artifacts/mysql_tpcc_typed_distributed_successor_v13/4e87faac238a2ecd1523f9dfc8044593243f732b3f1818a1f4f6fda560d0c8ab";O=R/"artifacts/mysql_tpcc_v13_same_live_continuation_successor_v1"
def canon(v):return (json.dumps(v,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
def digest(p):
 h=hashlib.sha256()
 with p.open("rb") as f:
  for x in iter(lambda:f.read(1048576),b""):h.update(x)
 return h.hexdigest()
def main():
 if digest(V/"bundle_manifest.json")!="2172eae52c4736ba1afbf4a1342f8e02d20db6fa771828839db14cc9e5a17e90":raise RuntimeError("v13 binding differs")
 items={"continuation/mysql_tpcc_v13_same_live_continuation_v1.py":R.joinpath("shell/mysql_tpcc_v13_same_live_continuation_v1.py").read_bytes(),"tests/test_mysql_tpcc_v13_same_live_continuation_v1.py":R.joinpath("shell/test_mysql_tpcc_v13_same_live_continuation_v1.py").read_bytes(),"authority/v13_bundle_manifest.json":(V/"bundle_manifest.json").read_bytes(),"authority/build_mysql_tpcc_v13_same_live_continuation_successor_v1.py":S.read_bytes()};files={k:hashlib.sha256(v).hexdigest() for k,v in sorted(items.items())};m={"schema_version":"mysql_tpcc_v13_same_live_continuation_bundle_v1","status":"SEALED_READ_ONLY_CONTINUATION_AUTHORITY","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"v13_bundle_id":V.name,"v13_manifest_sha256":digest(V/"bundle_manifest.json"),"same_live_docker_inspect_required":True,"dual_projection_translation_required":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"files":files};bid=hashlib.sha256(canon(m)).hexdigest();m["bundle_id"]=bid;O.mkdir(parents=True,exist_ok=True);t=O/bid
 if t.exists():
  if json.loads((t/"bundle_manifest.json").read_text())!=m:raise RuntimeError("existing differs")
 else:
  d=O/(".stage-"+bid+"-"+secrets.token_hex(6));d.mkdir(mode=0o700)
  try:
   for n,v in items.items():q=d/n;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(v);os.chmod(q,0o444)
   raw=canon(m);(d/"bundle_manifest.json").write_bytes(raw);(d/"bundle_manifest.json.sha256").write_text(hashlib.sha256(raw).hexdigest()+"  bundle_manifest.json\n");os.rename(d,t)
  except BaseException:
   if d.exists():shutil.rmtree(d)
   raise
 print(json.dumps({"status":"PASS_SEALED_READ_ONLY_CONTINUATION_AUTHORITY","bundle":str(t),"bundle_id":bid,"manifest_sha256":digest(t/"bundle_manifest.json"),"accepted_round_authorized":False},sort_keys=True))
if __name__=="__main__":
 try:main()
 except Exception as e:sys.stderr.write("NOT_READY: %s\n"%e);raise SystemExit(2)

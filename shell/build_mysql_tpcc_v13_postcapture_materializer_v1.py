#!/usr/bin/env python3
"""Content-address and publish the offline-only v13 postcapture materializer."""
from __future__ import annotations
import hashlib,json,os,secrets,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];SELF=Path(__file__).resolve();SRC=ROOT/"shell/mysql_tpcc_v13_postcapture_evidence_materializer_v1.py";TEST=ROOT/"shell/test_mysql_tpcc_v13_postcapture_evidence_materializer_v1.py";V=ROOT/"artifacts/mysql_tpcc_typed_distributed_successor_v13/4e87faac238a2ecd1523f9dfc8044593243f732b3f1818a1f4f6fda560d0c8ab";SHA="2172eae52c4736ba1afbf4a1342f8e02d20db6fa771828839db14cc9e5a17e90";OUT=ROOT/"artifacts/mysql_tpcc_v13_postcapture_materializer_v1"
def canon(v):return (json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)+"\n").encode()
def digest(p):
 h=hashlib.sha256()
 with p.open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):h.update(b)
 return h.hexdigest()
def main():
 if digest(V/"bundle_manifest.json")!=SHA:raise RuntimeError("v13 binding differs")
 p={"materializer/mysql_tpcc_v13_postcapture_evidence_materializer_v1.py":SRC.read_bytes(),"tests/test_mysql_tpcc_v13_postcapture_evidence_materializer_v1.py":TEST.read_bytes(),"authority/v13_bundle_manifest.json":(V/"bundle_manifest.json").read_bytes(),"authority/build_mysql_tpcc_v13_postcapture_materializer_v1.py":SELF.read_bytes()};files={k:hashlib.sha256(x).hexdigest() for k,x in sorted(p.items())};m={"schema_version":"mysql_tpcc_v13_postcapture_materializer_bundle_v1","status":"SEALED_OFFLINE_PARTIAL_DERIVATION_NOT_CAPTURE_AUTHORITY","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"v13_bundle_id":V.name,"v13_manifest_sha256":SHA,"real_capture_required":True,"non_derivable_evidence_fail_closed":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"files":files};bid=hashlib.sha256(canon(m)).hexdigest();m["bundle_id"]=bid;OUT.mkdir(parents=True,exist_ok=True);t=OUT/bid
 if t.exists() or t.is_symlink():
  if json.loads((t/"bundle_manifest.json").read_text())!=m:raise RuntimeError("existing differs")
 else:
  s=OUT/(".stage-"+bid+"-"+secrets.token_hex(6));s.mkdir(mode=0o700)
  try:
   for n,d in p.items():q=s/n;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(d);os.chmod(q,0o444)
   raw=canon(m);(s/"bundle_manifest.json").write_bytes(raw);(s/"bundle_manifest.json.sha256").write_text(hashlib.sha256(raw).hexdigest()+"  bundle_manifest.json\n");os.rename(s,t)
  except BaseException:
   if s.exists():shutil.rmtree(s)
   raise
 print(json.dumps({"status":"PASS_SEALED_OFFLINE_POSTCAPTURE_MATERIALIZER_NOT_FORMAL_AUTHORITY","bundle":str(t),"bundle_id":bid,"manifest_sha256":digest(t/"bundle_manifest.json"),"accepted_round_authorized":False},sort_keys=True))
if __name__=="__main__":
 try:main()
 except Exception as e:sys.stderr.write("NOT_READY: %s\n"%e);raise SystemExit(2)

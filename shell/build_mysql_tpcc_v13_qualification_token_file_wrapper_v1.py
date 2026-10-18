#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,os,secrets,shutil,sys
from pathlib import Path
R=Path(__file__).resolve().parents[1];S=Path(__file__).resolve();Q=R/"shell/mysql_distributed_live_readback_qualification_v1.py";W=R/"shell/mysql_tpcc_v13_qualification_token_file_wrapper_v1.py";O=R/"artifacts/mysql_tpcc_v13_qualification_token_file_wrapper_v1"
def canon(v):return (json.dumps(v,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
def digest(p):
 h=hashlib.sha256()
 with p.open("rb") as f:
  for x in iter(lambda:f.read(1048576),b""):h.update(x)
 return h.hexdigest()
def main():
 items={"qualification/mysql_tpcc_v13_qualification_token_file_wrapper_v1.py":W.read_bytes(),"qualification/mysql_distributed_live_readback_qualification_v1.py":Q.read_bytes(),"authority/build_mysql_tpcc_v13_qualification_token_file_wrapper_v1.py":S.read_bytes()};files={k:hashlib.sha256(v).hexdigest() for k,v in sorted(items.items())};m={"schema_version":"mysql_tpcc_v13_qualification_token_file_wrapper_bundle_v1","status":"SEALED_TOKEN_NOT_IN_ARGV_QUALIFICATION_WRAPPER","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"token_file_must_be_current_user_0600":True,"token_persisted_in_output_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"files":files};bid=hashlib.sha256(canon(m)).hexdigest();m["bundle_id"]=bid;O.mkdir(parents=True,exist_ok=True);t=O/bid
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
 print(json.dumps({"status":m["status"],"bundle":str(t),"bundle_id":bid,"manifest_sha256":digest(t/"bundle_manifest.json")},sort_keys=True))
if __name__=="__main__":
 try:main()
 except Exception as e:sys.stderr.write("NOT_READY: %s\n"%e);raise SystemExit(2)

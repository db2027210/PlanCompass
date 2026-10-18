#!/usr/bin/env python3
"""Build a Retry9 systemd wrapper bound to the current dynamic bundles."""
from __future__ import annotations
import hashlib,json,os,shutil,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve()
OLD_ID="4849dd8600122990f77130206630a78b789ba3da6770c6754f940226c2d8eb67";OLD_SHA="9fee0bfe0f0258c395464da121732842b1f8e7c929f270d829f8e0e6cd6b801e"
OLD=ROOT/"artifacts/mysql_tpch_retry9_dynamic_attempt_one_click_successor_v1"/"25100fb555144610c4db07db2c01c451d38e92913d737489a32f13ba614dda19"/"dependencies/wrapper"/OLD_ID
RETRY="61f7d2b3b9d88a656f8bba4ab4a17cb87308fb9027c02276dff69e74a0a6c973";RETRY_SHA="9b72727479fc5c1d9a2837eeaab409129c85e0a6a4870c3244c517d7bc3697e3"
COLLECTOR="947fe36dd89966bfcf1427a67ecd60e872b450ce656556f6255199e47f353739";COLLECTOR_SHA="1be67f24372e9893b37be8b717e24ff66d76a33dc40cd73149000abd905e14b7";ENTRY_SHA="8287cf1954e634cc298665fa9b71026b9b4ed9612b3d995e868a44a95587035a";CORE_SHA="ae6384be782bf4954419dfffbd25a7395719f1cb967bc2477d4414790e2b5317"
OUT=ROOT/"artifacts/mysql_tpch_retry9_dynamic_retry5_identity_wrapper_successor_v1";C="controller/mysql_tpch_retry5_systemd219_completion_v1.py";B="builder/build_mysql_tpch_retry9_dynamic_retry5_identity_wrapper_successor_v1.py";T="tests/test_mysql_tpch_retry9_dynamic_retry5_identity_wrapper_successor_v1.py";FAILED="tpch-v2-20260803t094428z-b235retry964a0f8cd8c62"
def canon(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sb(b):return hashlib.sha256(b).hexdigest()
def sf(p):return sb(Path(p).read_bytes())
def fmap(r):return {p.relative_to(r).as_posix():sf(p) for p in sorted(r.rglob("*")) if p.is_file() and p.relative_to(r).as_posix() not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
def verify(r,bid,sha):
 m=json.loads((r/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id",None)
 if r.name!=bid or m.get("bundle_id")!=bid or sf(r/"bundle_manifest.json")!=sha or sb(canon(u))!=bid or fmap(r)!=m.get("files"):raise RuntimeError("closure differs: "+str(r))
 return m
def patch(s):
 old='RETRY_ID="819640d73e8b85a4547b2f4a09b5477eac4bcd19f425d46cac9764b01e3a57af";RETRY_SHA="fef5a7cbd3a93a5bd190036240492ffa5dabe495a8a701f77bc3e05bca978480";COLLECTOR_ID="a070e44ed4ca1acb0f5b4e0fe03386c6fc0dd43736d0f477cdd17a006bd465b6";COLLECTOR_SHA="a529c3aa35884314893afdd50b093a251dbf31c6a83d9ff797df052c960a7dce";ENTRY_SHA="8cb1283f8cc0d08af3a4d8d218880e9eb6d61b5d7958e971b42a026462b02293";CORE_SHA="ae6384be782bf4954419dfffbd25a7395719f1cb967bc2477d4414790e2b5317"'
 new=f'RETRY_ID="{RETRY}";RETRY_SHA="{RETRY_SHA}";COLLECTOR_ID="{COLLECTOR}";COLLECTOR_SHA="{COLLECTOR_SHA}";ENTRY_SHA="{ENTRY_SHA}";CORE_SHA="{CORE_SHA}"'
 if s.count(old)!=1:raise RuntimeError("identity anchor differs")
 return s.replace(old,new)
TEST=r'''from __future__ import annotations
import hashlib,importlib.util,json
from pathlib import Path
B=Path(__file__).resolve().parents[1];S=B/"controller/mysql_tpch_retry5_systemd219_completion_v1.py";q=importlib.util.spec_from_file_location("w",S);M=importlib.util.module_from_spec(q);q.loader.exec_module(M)
def test_content_address_and_scope():
 m=json.loads((B/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id");assert hashlib.sha256((json.dumps(u,sort_keys=True,indent=2)+"\n").encode()).hexdigest()==m["bundle_id"]==B.name;assert m["formal_scope"]=="mysql_only" and m["cross_engine_comparison_prohibited"] is True
def test_dynamic_retry5_and_collector_identity():
 assert M.RETRY_ID=="61f7d2b3b9d88a656f8bba4ab4a17cb87308fb9027c02276dff69e74a0a6c973" and M.RETRY_SHA=="9b72727479fc5c1d9a2837eeaab409129c85e0a6a4870c3244c517d7bc3697e3"
 assert M.COLLECTOR_ID=="947fe36dd89966bfcf1427a67ecd60e872b450ce656556f6255199e47f353739" and M.ENTRY_SHA=="8287cf1954e634cc298665fa9b71026b9b4ed9612b3d995e868a44a95587035a"
def test_old_static_identities_absent():
 s=S.read_text();assert "819640d73e8b85a4547b2f4a09b5477eac4bcd19f425d46cac9764b01e3a57af" not in s and "a070e44ed4ca1acb0f5b4e0fe03386c6fc0dd43736d0f477cdd17a006bd465b6" not in s
'''
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)
def build():
 m=verify(OLD,OLD_ID,OLD_SHA);content={}
 for n in sorted(m["files"]):
  if n.startswith("builder/") or n.startswith("tests/"):continue
  b=(OLD/n).read_bytes();content[n]=patch(b.decode()).encode() if n==C else b
 content[B]=HERE.read_bytes();content[T]=TEST.encode();content["authority/retry9_64a0_wrapper_static_identity_failure_v1.json"]=canon({"schema_version":"mysql_tpch_retry9_wrapper_static_identity_failure_v1","status":"FAILED_ZERO_CREDIT_RECONCILED_DO_NOT_RERUN","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"failed_attempt_token":FAILED,"error":"Retry9 systemd materializer failed rc=2 stderr=NOT_READY: retry5 bundle differs","bootstrap_passed":True,"defaults_readback_passed":True,"post_failure_closure_passed":True,"reconcile_passed":True,"rerun_prohibited":True,"accepted_rounds_credit":0})
 files={n:sb(b) for n,b in sorted(content.items())};u={"schema_version":"mysql_tpch_retry9_dynamic_retry5_identity_wrapper_successor_bundle_v1","status":"SEALED_DYNAMIC_RETRY5_AND_COLLECTOR_IDENTITY_WRAPPER_NOT_EXECUTED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"predecessor_bundle_id":OLD_ID,"predecessor_manifest_sha256":OLD_SHA,"retry5_materializer_bundle_id":RETRY,"retry5_materializer_manifest_sha256":RETRY_SHA,"collector_bundle_id":COLLECTOR,"collector_manifest_sha256":COLLECTOR_SHA,"collector_entry_sha256":ENTRY_SHA,"collector_core_sha256":CORE_SHA,"failed_attempt_token":FAILED,"failed_attempt_rerun_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,"files":files};bid=sb(canon(u));manifest=dict(u,bundle_id=bid);dst=OUT/bid
 if not dst.exists():
  OUT.mkdir(parents=True,exist_ok=True);stage=Path(tempfile.mkdtemp(prefix=".dynamic-wrapper-",dir=OUT))
  try:
   for n,b in content.items():p=stage/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
   raw=canon(manifest);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(sb(raw)+"  bundle_manifest.json\n");freeze(stage);os.replace(stage,dst)
  finally:
   if stage.exists():shutil.rmtree(stage)
 if fmap(dst)!=files or sf(dst/"bundle_manifest.json")!=sb(canon(manifest)):raise RuntimeError("successor differs")
 return dst
if __name__=="__main__":
 b=build();print(json.dumps({"bundle":str(b),"bundle_id":b.name,"manifest_sha256":sf(b/"bundle_manifest.json"),"controller_sha256":sf(b/C),"remote_actions":0},sort_keys=True))

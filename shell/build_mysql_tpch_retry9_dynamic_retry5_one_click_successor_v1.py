#!/usr/bin/env python3
"""Build Retry9 one-click closure with the dynamic retry5 wrapper."""
from __future__ import annotations
import hashlib,json,os,shutil,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve()
OLD_ID="25100fb555144610c4db07db2c01c451d38e92913d737489a32f13ba614dda19";OLD_SHA="75dff0bf18dcee29b6082cae2aaf15502e6501f182bf8a6ee7967659d3b9fa71";OLD=ROOT/"artifacts/mysql_tpch_retry9_dynamic_attempt_one_click_successor_v1"/OLD_ID
OLD_WRAPPER="4849dd8600122990f77130206630a78b789ba3da6770c6754f940226c2d8eb67";WRAPPER="02af9e95583d2cd792bc691d714f71cdbac094be5935e3a3fb9bf6e1bbd29b33";WRAPPER_SHA="8a218082bc8e88706682c3bbb12c821cf34b4d5cbc472588d433560a15c1ac1d";WRAPPER_FILE_SHA="d549c43a543388544266b4c5130072a05db3d2208153c142151bd5c958e9f517";W=ROOT/"artifacts/mysql_tpch_retry9_dynamic_retry5_identity_wrapper_successor_v1"/WRAPPER
OUT=ROOT/"artifacts/mysql_tpch_retry9_dynamic_retry5_one_click_successor_v1";C="controller/mysql_tpch_retry9_one_click_v1.py";B="builder/build_mysql_tpch_retry9_dynamic_retry5_one_click_successor_v1.py";T="tests/test_mysql_tpch_retry9_dynamic_retry5_one_click_successor_v1.py";FAILED="tpch-v2-20260803t094428z-b235retry964a0f8cd8c62";FAILURE="evidence/mysql_tpch_retry9_64a0_static_wrapper_identity_failure_exact_v1.json"
def canon(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sb(b):return hashlib.sha256(b).hexdigest()
def sf(p):
 d=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
def fmap(r):return {p.relative_to(r).as_posix():sf(p) for p in sorted(r.rglob("*")) if p.is_file() and p.relative_to(r).as_posix() not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
def verify(r,bid,sha):
 m=json.loads((r/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id",None)
 if r.name!=bid or m.get("bundle_id")!=bid or sf(r/"bundle_manifest.json")!=sha or sb(canon(u))!=bid or fmap(r)!=m.get("files") or m.get("formal_scope")!="mysql_only" or m.get("cross_engine_comparison_prohibited") is not True:raise RuntimeError("closure differs: "+str(r))
 return m
def repl(s,o,n,label):
 if s.count(o)!=1:raise RuntimeError(label+" anchor differs")
 return s.replace(o,n)
def patch(s):
 s=repl(s,f'WRAPPER_ID = "{OLD_WRAPPER}"\nWRAPPER_SHA = "9fee0bfe0f0258c395464da121732842b1f8e7c929f270d829f8e0e6cd6b801e"',f'WRAPPER_ID = "{WRAPPER}"\nWRAPPER_SHA = "{WRAPPER_SHA}"',"wrapper constants")
 old='FAILED_DYNAMIC_ERROR = "current TPC-H command attempt binding differs"\n';new=old+f'FAILED_WRAPPER_ATTEMPT = "{FAILED}"\nFAILED_WRAPPER_ERROR = "Retry9 systemd materializer failed rc=2 stderr=NOT_READY: retry5 bundle differs"\n';s=repl(s,old,new,"failed wrapper constants")
 old='        "failure_dynamic": root / "evidence/mysql_tpch_retry9_5c560_static_attempt_binding_failure_exact_v1.json",\n';new=old+f'        "failure_wrapper": root / "{FAILURE}",\n';s=repl(s,old,new,"failure path")
 old='        raise OneClickError("Retry9 dynamic-attempt exact zero-credit evidence differs")\n    return paths\n';new='''        raise OneClickError("Retry9 dynamic-attempt exact zero-credit evidence differs")
    failure_wrapper = sealed(paths["failure_wrapper"], "Retry9 wrapper identity failure")
    if (failure_wrapper.get("schema_version") != "mysql_tpch_retry9_static_wrapper_identity_failure_exact_v1"
            or failure_wrapper.get("status") != "FAILED_ZERO_CREDIT_RECONCILED_DO_NOT_RERUN"
            or failure_wrapper.get("attempt_token") != FAILED_WRAPPER_ATTEMPT
            or failure_wrapper.get("error") != FAILED_WRAPPER_ERROR
            or failure_wrapper.get("bootstrap_passed") is not True
            or failure_wrapper.get("defaults_readback_passed") is not True
            or failure_wrapper.get("post_failure_closure_passed") is not True
            or failure_wrapper.get("reconcile_passed") is not True
            or failure_wrapper.get("rerun_prohibited") is not True
            or failure_wrapper.get("accepted_rounds_credit") != 0):
        raise OneClickError("Retry9 wrapper identity exact zero-credit evidence differs")
    return paths
''';s=repl(s,old,new,"failure verify")
 old='    if attempt == FAILED_DYNAMIC_ATTEMPT:\n        raise OneClickError("failed Retry9 dynamic-attempt rerun is prohibited")\n';new=old+'    if attempt == FAILED_WRAPPER_ATTEMPT:\n        raise OneClickError("failed Retry9 wrapper-identity attempt rerun is prohibited")\n';s=repl(s,old,new,"materialize guard")
 old='        "failed_dynamic_evidence": str(paths["failure_dynamic"]),\n';new=old+'        "failed_wrapper_attempt": FAILED_WRAPPER_ATTEMPT,\n        "failed_wrapper_evidence": str(paths["failure_wrapper"]),\n';s=repl(s,old,new,"plan binding")
 old='            or plan.get("attempt_token") == FAILED_DYNAMIC_ATTEMPT\n';new=old+'            or plan.get("failed_wrapper_attempt") != FAILED_WRAPPER_ATTEMPT\n            or plan.get("attempt_token") == FAILED_WRAPPER_ATTEMPT\n';return repl(s,old,new,"execute guard")
FAIL={"schema_version":"mysql_tpch_retry9_static_wrapper_identity_failure_exact_v1","status":"FAILED_ZERO_CREDIT_RECONCILED_DO_NOT_RERUN","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"attempt_token":FAILED,"error":"Retry9 systemd materializer failed rc=2 stderr=NOT_READY: retry5 bundle differs","bootstrap_passed":True,"defaults_readback_passed":True,"post_failure_closure_passed":True,"reconcile_passed":True,"rerun_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0}
TEST=r'''from __future__ import annotations
import hashlib,importlib.util,json
from pathlib import Path
B=Path(__file__).resolve().parents[1];S=B/"controller/mysql_tpch_retry9_one_click_v1.py";q=importlib.util.spec_from_file_location("o",S);M=importlib.util.module_from_spec(q);q.loader.exec_module(M)
def test_content_address_and_scope():
 m=json.loads((B/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id");assert hashlib.sha256((json.dumps(u,sort_keys=True,indent=2)+"\n").encode()).hexdigest()==m["bundle_id"]==B.name;assert m["formal_scope"]=="mysql_only" and m["cross_engine_comparison_prohibited"] is True
def test_dynamic_wrapper_is_unique_and_verified():
 p=M.verify_package();assert p["wrapper"].name=="02af9e95583d2cd792bc691d714f71cdbac094be5935e3a3fb9bf6e1bbd29b33";assert not (B/"dependencies/wrapper/4849dd8600122990f77130206630a78b789ba3da6770c6754f940226c2d8eb67").exists()
 w=(p["wrapper"]/"controller/mysql_tpch_retry5_systemd219_completion_v1.py").read_text();assert "61f7d2b3b9d88a656f8bba4ab4a17cb87308fb9027c02276dff69e74a0a6c973" in w and "947fe36dd89966bfcf1427a67ecd60e872b450ce656556f6255199e47f353739" in w
def test_failed_64a0_is_zero_credit_and_prohibited():
 f=M.sealed(M.package_paths()["failure_wrapper"],"failure");assert f["attempt_token"]==M.FAILED_WRAPPER_ATTEMPT and f["rerun_prohibited"] is True and f["accepted_rounds_credit"]==0
 s=S.read_text();assert 'failed Retry9 wrapper-identity attempt rerun is prohibited' in s and 'plan.get("attempt_token") == FAILED_WRAPPER_ATTEMPT' in s
def test_dynamic_attempt_chain_preserved():
 p=M.package_paths();assert p["materializer"].name=="61f7d2b3b9d88a656f8bba4ab4a17cb87308fb9027c02276dff69e74a0a6c973" and p["support"].name=="947fe36dd89966bfcf1427a67ecd60e872b450ce656556f6255199e47f353739"
'''
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)
def build():
 m=verify(OLD,OLD_ID,OLD_SHA);verify(W,WRAPPER,WRAPPER_SHA);content={};oldp="dependencies/wrapper/"+OLD_WRAPPER+"/"
 for n in sorted(m["files"]):
  if n.startswith(oldp) or n.startswith("builder/") or n.startswith("tests/"):continue
  b=(OLD/n).read_bytes();content[n]=patch(b.decode()).encode() if n==C else b
 for p in sorted(W.rglob("*")):
  if p.is_file():content["dependencies/wrapper/"+WRAPPER+"/"+p.relative_to(W).as_posix()]=p.read_bytes()
 fp=canon(FAIL);content[FAILURE]=fp;content[FAILURE+".sha256"]=(sb(fp)+"  "+Path(FAILURE).name+"\n").encode();content[B]=HERE.read_bytes();content[T]=TEST.encode();content["authority/invalidated_retry9_64a0_static_wrapper_identity_v1.json"]=canon({"schema_version":"mysql_tpch_retry9_64a0_static_wrapper_identity_invalidation_v1","status":"INVALIDATED_FAILED_ZERO_CREDIT_PROHIBITED_RERUN","failed_attempt_token":FAILED,"failure_evidence":FAILURE,"failure_evidence_sha256":sb(fp),"old_wrapper_bundle_id":OLD_WRAPPER,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"accepted_rounds_credit":0,"remote_actions":0})
 files={n:sb(b) for n,b in sorted(content.items())};u={k:v for k,v in m.items() if k not in ("bundle_id","files","schema_version","status","predecessor_bundle_id","predecessor_manifest_sha256","wrapper_bundle_id")};u.update({"schema_version":"mysql_tpch_retry9_dynamic_retry5_one_click_successor_bundle_v1","status":"SEALED_DYNAMIC_RETRY5_IDENTITY_ONE_CLICK_NOT_EXECUTED","predecessor_bundle_id":OLD_ID,"predecessor_manifest_sha256":OLD_SHA,"wrapper_bundle_id":WRAPPER,"wrapper_manifest_sha256":WRAPPER_SHA,"wrapper_controller_sha256":WRAPPER_FILE_SHA,"failed_wrapper_attempt_token":FAILED,"failed_wrapper_attempt_rerun_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,"files":files});bid=sb(canon(u));manifest=dict(u,bundle_id=bid);dst=OUT/bid
 if not dst.exists():
  OUT.mkdir(parents=True,exist_ok=True);stage=Path(tempfile.mkdtemp(prefix=".dynamic-retry5-oneclick-",dir=OUT))
  try:
   for n,b in content.items():p=stage/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
   raw=canon(manifest);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(sb(raw)+"  bundle_manifest.json\n");freeze(stage);os.replace(stage,dst)
  finally:
   if stage.exists():shutil.rmtree(stage)
 if fmap(dst)!=files or sf(dst/"bundle_manifest.json")!=sb(canon(manifest)):raise RuntimeError("oneclick successor differs")
 return dst
if __name__=="__main__":
 b=build();print(json.dumps({"bundle":str(b),"bundle_id":b.name,"manifest_sha256":sf(b/"bundle_manifest.json"),"controller_sha256":sf(b/C),"remote_actions":0},sort_keys=True))

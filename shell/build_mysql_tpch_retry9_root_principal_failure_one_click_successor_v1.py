#!/usr/bin/env python3
"""Seal the failed root-principal attempt into the Retry9 authority."""
from __future__ import annotations
import hashlib,json,os,shutil,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve()
OLD_ID="74f28b7baf6312e54049fc57de189f6cb9b3bec526f72e61e3e285ceefce253b";OLD_SHA="130e85b366667211ac65d5b84453483b1a07f5d91af5c53730fe691f2b408c8b";OLD=ROOT/"artifacts/mysql_tpch_retry9_dynamic_retry5_one_click_successor_v1"/OLD_ID
OUT=ROOT/"artifacts/mysql_tpch_retry9_root_principal_failure_one_click_successor_v1";C="controller/mysql_tpch_retry9_one_click_v1.py";B="builder/build_mysql_tpch_retry9_root_principal_failure_one_click_successor_v1.py";T="tests/test_mysql_tpch_retry9_root_principal_failure_one_click_successor_v1.py";FAILED="tpch-v2-20260803t094046z-b235retry9ffb017893807";ERROR="NOT_READY: main-agent token file ownership/mode differs";F="evidence/mysql_tpch_retry9_ffb_root_principal_token_owner_failure_exact_v1.json"
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
 if r.name!=bid or m.get("bundle_id")!=bid or sf(r/"bundle_manifest.json")!=sha or sb(canon(u))!=bid or fmap(r)!=m.get("files") or m.get("formal_scope")!="mysql_only" or m.get("cross_engine_comparison_prohibited") is not True:raise RuntimeError("closure differs")
 return m
def repl(s,o,n,label):
 if s.count(o)!=1:raise RuntimeError(label+" anchor differs")
 return s.replace(o,n)
def patch(s):
 old='FAILED_WRAPPER_ERROR = "Retry9 systemd materializer failed rc=2 stderr=NOT_READY: retry5 bundle differs"\n';new=old+f'FAILED_ROOT_PRINCIPAL_ATTEMPT = "{FAILED}"\nFAILED_ROOT_PRINCIPAL_ERROR = "{ERROR}"\n';s=repl(s,old,new,"constants")
 old='        "failure_wrapper": root / "evidence/mysql_tpch_retry9_64a0_static_wrapper_identity_failure_exact_v1.json",\n';new=old+f'        "failure_root_principal": root / "{F}",\n';s=repl(s,old,new,"package path")
 old='        raise OneClickError("Retry9 wrapper identity exact zero-credit evidence differs")\n    return paths\n';new='''        raise OneClickError("Retry9 wrapper identity exact zero-credit evidence differs")
    failure_root = sealed(paths["failure_root_principal"], "Retry9 root-principal failure")
    if (failure_root.get("schema_version") != "mysql_tpch_retry9_root_principal_token_owner_failure_exact_v1"
            or failure_root.get("status") != "FAILED_ZERO_CREDIT_RECONCILED_DO_NOT_RERUN"
            or failure_root.get("attempt_token") != FAILED_ROOT_PRINCIPAL_ATTEMPT
            or failure_root.get("error") != FAILED_ROOT_PRINCIPAL_ERROR
            or failure_root.get("bootstrap_unit_failed") is not True
            or failure_root.get("post_failure_closure_passed") is not True
            or failure_root.get("reconcile_passed") is not True
            or failure_root.get("rerun_prohibited") is not True
            or failure_root.get("accepted_rounds_credit") != 0):
        raise OneClickError("Retry9 root-principal exact zero-credit evidence differs")
    return paths
''';s=repl(s,old,new,"failure verify")
 old='    if attempt == FAILED_WRAPPER_ATTEMPT:\n        raise OneClickError("failed Retry9 wrapper-identity attempt rerun is prohibited")\n';new=old+'    if attempt == FAILED_ROOT_PRINCIPAL_ATTEMPT:\n        raise OneClickError("failed Retry9 root-principal attempt rerun is prohibited")\n';s=repl(s,old,new,"materialize guard")
 old='        "failed_wrapper_evidence": str(paths["failure_wrapper"]),\n';new=old+'        "failed_root_principal_attempt": FAILED_ROOT_PRINCIPAL_ATTEMPT,\n        "failed_root_principal_evidence": str(paths["failure_root_principal"]),\n';s=repl(s,old,new,"plan")
 old='            or plan.get("attempt_token") == FAILED_WRAPPER_ATTEMPT\n';new=old+'            or plan.get("failed_root_principal_attempt") != FAILED_ROOT_PRINCIPAL_ATTEMPT\n            or plan.get("attempt_token") == FAILED_ROOT_PRINCIPAL_ATTEMPT\n';return repl(s,old,new,"execute guard")
FAIL={"schema_version":"mysql_tpch_retry9_root_principal_token_owner_failure_exact_v1","status":"FAILED_ZERO_CREDIT_RECONCILED_DO_NOT_RERUN","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"attempt_token":FAILED,"error":ERROR,"failure_stage":"bootstrap_main_agent_token_readback","materialized_as_uid":1008,"executed_as_uid":0,"observed_token_uid":1008,"observed_token_gid":1008,"observed_token_mode":"0600","required_token_uid_equals_execute_euid":True,"bootstrap_unit_failed":True,"bootstrap_evidence_generated":False,"defaults_generated":False,"workload_started":False,"post_failure_closure_passed":True,"reconcile_passed":True,"rerun_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0}
TEST=r'''from __future__ import annotations
import hashlib,importlib.util,json
from pathlib import Path
B=Path(__file__).resolve().parents[1];S=B/"controller/mysql_tpch_retry9_one_click_v1.py";q=importlib.util.spec_from_file_location("o",S);M=importlib.util.module_from_spec(q);q.loader.exec_module(M)
def test_content_address_and_scope():
 m=json.loads((B/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id");assert hashlib.sha256((json.dumps(u,sort_keys=True,indent=2)+"\n").encode()).hexdigest()==m["bundle_id"]==B.name;assert m["formal_scope"]=="mysql_only" and m["cross_engine_comparison_prohibited"] is True
def test_ffb_failure_is_exact_zero_credit():
 f=M.sealed(M.package_paths()["failure_root_principal"],"failure");assert f["attempt_token"]==M.FAILED_ROOT_PRINCIPAL_ATTEMPT and f["error"]=="NOT_READY: main-agent token file ownership/mode differs" and f["bootstrap_evidence_generated"] is False and f["accepted_rounds_credit"]==0
def test_materialize_and_execute_both_prohibit_ffb():
 s=S.read_text();assert 'failed Retry9 root-principal attempt rerun is prohibited' in s and 'plan.get("attempt_token") == FAILED_ROOT_PRINCIPAL_ATTEMPT' in s and 'plan.get("failed_root_principal_attempt") != FAILED_ROOT_PRINCIPAL_ATTEMPT' in s
def test_dynamic_identity_chain_unchanged():
 p=M.verify_package();assert p["materializer"].name=="61f7d2b3b9d88a656f8bba4ab4a17cb87308fb9027c02276dff69e74a0a6c973" and p["support"].name=="947fe36dd89966bfcf1427a67ecd60e872b450ce656556f6255199e47f353739" and p["wrapper"].name=="02af9e95583d2cd792bc691d714f71cdbac094be5935e3a3fb9bf6e1bbd29b33"
'''
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)
def build():
 m=verify(OLD,OLD_ID,OLD_SHA);content={}
 for n in sorted(m["files"]):
  if n.startswith("builder/") or n.startswith("tests/"):continue
  b=(OLD/n).read_bytes();content[n]=patch(b.decode()).encode() if n==C else b
 fp=canon(FAIL);content[F]=fp;content[F+".sha256"]=(sb(fp)+"  "+Path(F).name+"\n").encode();content[B]=HERE.read_bytes();content[T]=TEST.encode();content["authority/invalidated_retry9_ffb_root_principal_token_owner_v1.json"]=canon({"schema_version":"mysql_tpch_retry9_ffb_root_principal_invalidation_v1","status":"INVALIDATED_FAILED_ZERO_CREDIT_PROHIBITED_RERUN","failed_attempt_token":FAILED,"failure_evidence":F,"failure_evidence_sha256":sb(fp),"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"accepted_rounds_credit":0,"remote_actions":0})
 files={n:sb(b) for n,b in sorted(content.items())};u={k:v for k,v in m.items() if k not in ("bundle_id","files","schema_version","status","predecessor_bundle_id","predecessor_manifest_sha256")};u.update({"schema_version":"mysql_tpch_retry9_root_principal_failure_one_click_successor_bundle_v1","status":"SEALED_ROOT_PRINCIPAL_FAILURE_AUTHORITY_NOT_EXECUTED","predecessor_bundle_id":OLD_ID,"predecessor_manifest_sha256":OLD_SHA,"failed_root_principal_attempt_token":FAILED,"failed_root_principal_attempt_rerun_prohibited":True,"dynamic_retry5_support_wrapper_binding_unchanged":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,"files":files});bid=sb(canon(u));manifest=dict(u,bundle_id=bid);dst=OUT/bid
 if not dst.exists():
  OUT.mkdir(parents=True,exist_ok=True);stage=Path(tempfile.mkdtemp(prefix=".root-principal-successor-",dir=OUT))
  try:
   for n,b in content.items():p=stage/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
   raw=canon(manifest);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(sb(raw)+"  bundle_manifest.json\n");freeze(stage);os.replace(stage,dst)
  finally:
   if stage.exists():shutil.rmtree(stage)
 if fmap(dst)!=files or sf(dst/"bundle_manifest.json")!=sb(canon(manifest)):raise RuntimeError("successor differs")
 return dst
if __name__=="__main__":
 b=build();print(json.dumps({"bundle":str(b),"bundle_id":b.name,"manifest_sha256":sf(b/"bundle_manifest.json"),"controller_sha256":sf(b/C),"remote_actions":0},sort_keys=True))

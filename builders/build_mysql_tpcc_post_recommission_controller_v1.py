#!/usr/bin/env python3
"""Bind zero-swap controller to the fresh post-recommission authority."""
from __future__ import annotations
import hashlib,json,os,shutil,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve();PARENT_ID="a8ac9c4d0badc0700705a39dace3493c034732d4c9cd9ce26ab820947a7c57e0";PARENT_SHA="e1f3edd01088a448f8adc9f910980b7522bd8550257826daf4b0b37554ea5337";PARENT=ROOT/"artifacts/mysql_tpcc_zero_swap_preflight_controller_v1"/PARENT_ID;OUT=ROOT/"artifacts/mysql_tpcc_post_recommission_controller_v1";CTL="controller/mysql_tpcc_v16_independent_parallel_one_click_v1.py"
def can(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def hs(v):return hashlib.sha256(v).hexdigest()
def sha(p):return hs(Path(p).read_bytes())
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)
def main():
 m=json.loads((PARENT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id",None)
 if sha(PARENT/"bundle_manifest.json")!=PARENT_SHA or hs(can(u))!=PARENT_ID:raise RuntimeError("parent differs")
 c={}
 for r,w in sorted(m["files"].items()):
  if sha(PARENT/r)!=w:raise RuntimeError("closure differs")
  if not r.startswith("tests/") and not r.startswith("builder/"):c[r]=(PARENT/r).read_bytes()
 t=c[CTL].decode();old='ADOPTED=CUR/"adopted_service.json"';new='ADOPTED=CUR/"adopted_service.zero_swap_restart_v1.json"'
 if t.count(old)!=1:raise RuntimeError("adopted authority anchor differs")
 c[CTL]=t.replace(old,new,1).encode();test='''import hashlib,json\nfrom pathlib import Path\nB=Path(__file__).resolve().parents[1];S=B/"controller/mysql_tpcc_v16_independent_parallel_one_click_v1.py"\ndef test_id_and_fresh_authority():\n m=json.loads((B/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id");assert hashlib.sha256((json.dumps(u,sort_keys=True,indent=2)+"\\n").encode()).hexdigest()==m["bundle_id"]==B.name;assert 'ADOPTED=CUR/"adopted_service.zero_swap_restart_v1.json"' in S.read_text()\ndef test_zero_swap_typed_pg_constraints():\n s=S.read_text();assert s.count("zero_swap_readback(")>=5 and "mysql_only_pg_artifact_http_dynamic_noninterference_v1" in s;m=json.loads((B/"bundle_manifest.json").read_text());assert m["typed_binding_count"]==34 and m["postgresql_mutation_prohibited"] is True\n''';c["tests/test_mysql_tpcc_post_recommission_controller_v1.py"]=test.encode();c["builder/build_mysql_tpcc_post_recommission_controller_v1.py"]=HERE.read_bytes();files={r:hs(v) for r,v in sorted(c.items())};u={"schema_version":"mysql_tpcc_post_recommission_controller_bundle_v1","status":"SEALED_NOT_EXECUTED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"parent_bundle_id":PARENT_ID,"parent_manifest_sha256":PARENT_SHA,"recommission_runbook_bundle_id":"c8063f73a3cf7e8fb11e4b7bccd1998cc682ba09b7f1cc17b72811bbe8d72ee5","adopted_service_relative":"adopted_service.zero_swap_restart_v1.json","typed_binding_count":34,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"files":files};bid=hs(can(u));manifest=dict(u,bundle_id=bid);OUT.mkdir(parents=True,exist_ok=True);d=OUT/bid
 if d.exists():print(bid);print(sha(d/"bundle_manifest.json"));print(d);return
 st=Path(tempfile.mkdtemp(prefix=".post-recommission-",dir=OUT))
 try:
  for r,v in c.items():p=st/r;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
  raw=can(manifest);(st/"bundle_manifest.json").write_bytes(raw);(st/"bundle_manifest.json.sha256").write_text(hs(raw)+"  bundle_manifest.json\n");freeze(st);os.rename(st,d)
 finally:
  if st.exists():shutil.rmtree(st)
 print(bid);print(sha(d/"bundle_manifest.json"));print(d)
if __name__=="__main__":main()

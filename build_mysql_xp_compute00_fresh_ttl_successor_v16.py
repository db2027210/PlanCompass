#!/usr/bin/env python3
"""Build immutable XP v16 fresh-TTL materials support from the f6f v15 gate."""
from __future__ import annotations
import hashlib,json,os,shutil,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent;HERE=Path(__file__).resolve()
PRE_ID="f6fca736241f10a504791b30fa4762d9b37566b4f0e8efe13863fa570f8b67b1"
PRE_SHA="86fb3d853925f6eaafeb9d1554ea0d6fe9a7199dde29ef285753ca1193093fa3"
PRE=ROOT/"artifacts/mysql_xp_compute00_dedicated_image_successor_v15"/PRE_ID
OUT=ROOT/"artifacts/mysql_xp_compute00_fresh_ttl_successor_v16"
EXPIRED_ATTEMPT="xp-formal-qual-20260803T152502Z-fbffaf8b0debd96e4c836215"
EXPIRED_AT="2026-08-03T19:25:02+00:00"

def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sha_bytes(v):return hashlib.sha256(v).hexdigest()
def sha_file(p):
 d=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
def fmap(root):return {p.relative_to(root).as_posix():sha_file(p) for p in sorted(root.rglob("*")) if p.is_file() and p.name not in ("bundle_manifest.json","bundle_manifest.json.sha256")}

def patched_materializer():
 s=(PRE/"materializer/mysql_xp_v15_materializer.py").read_text()
 s=s.replace('FAILED_UNIT="ra-mysql-xp-v12-bootstrap-8291df7e98fff472.service"', 'FAILED_UNIT="ra-mysql-xp-v12-bootstrap-8291df7e98fff472.service"\nEXPIRED_ATTEMPT="'+EXPIRED_ATTEMPT+'";EXPIRED_AT="'+EXPIRED_AT+'"')
 s=s.replace("ra-mysql-xp-v15-","ra-mysql-xp-v16-")
 s=s.replace("bootstrap_spec_v3_dedicated_image_output_split","bootstrap_spec_v4_fresh_ttl_dedicated_image_output_split")
 s=s.replace("command_plan_v4_dedicated_image_output_split","command_plan_v5_fresh_ttl_dedicated_image_output_split")
 s=s.replace("materials_manifest_v4_dedicated_image_output_split","materials_manifest_v5_fresh_ttl_dedicated_image_output_split")
 old='"invalidated_failed_attempts":[{"attempt_id":FAILED_ATTEMPT,"failed_unit":FAILED_UNIT,"fresh_error":"dedicated daemon missing exact frozen image","formal_r0_credit":0,"accepted_rounds_credit":0,"rerun_prohibited":True}]'
 new='"invalidated_failed_attempts":[{"attempt_id":FAILED_ATTEMPT,"failed_unit":FAILED_UNIT,"fresh_error":"dedicated daemon missing exact frozen image","formal_r0_credit":0,"accepted_rounds_credit":0,"rerun_prohibited":True},{"attempt_id":EXPIRED_ATTEMPT,"expires_at_utc":EXPIRED_AT,"external_authorization_may_exist":True,"delete_prohibited":True,"reuse_prohibited":True,"formal_r0_credit":0,"accepted_rounds_credit":0}]'
 if s.count(old)!=1:raise RuntimeError("invalidation anchor differs")
 return s.replace(old,new)

def patched_test():
 s=(PRE/"tests/test_mysql_xp_v15_materializer.py").read_text()
 s=s.replace("XP_V15_BASE","XP_V16_BASE").replace("mysql_xp_v15_materializer.py","mysql_xp_v16_materializer.py").replace("ra-mysql-xp-v15-","ra-mysql-xp-v16-")
 anchor='  assert m["invalidated_failed_attempts"][0]["rerun_prohibited"]\n'
 extra='  assert m["invalidated_failed_attempts"][1]["attempt_id"]==M.EXPIRED_ATTEMPT and m["invalidated_failed_attempts"][1]["reuse_prohibited"] and m["invalidated_failed_attempts"][1]["delete_prohibited"]\n'
 if s.count(anchor)!=1:raise RuntimeError("test anchor differs")
 return s.replace(anchor,anchor+extra)

def build():
 pm=json.loads((PRE/"bundle_manifest.json").read_text());u=dict(pm);u.pop("bundle_id")
 if PRE.name!=PRE_ID or sha_file(PRE/"bundle_manifest.json")!=PRE_SHA or sha_bytes(canonical(u))!=PRE_ID or fmap(PRE)!=pm["files"]:raise RuntimeError("f6f predecessor closure differs")
 publisher=(PRE/"runtime/mysql_xp_dedicated_frozen_image_publication_v1.py").read_bytes()
 c={"builder/build_mysql_xp_compute00_fresh_ttl_successor_v16.py":HERE.read_bytes(),"runtime/mysql_xp_dedicated_frozen_image_publication_v1.py":publisher,"materializer/mysql_xp_v16_materializer.py":patched_materializer().encode(),"tests/test_mysql_xp_v16_materializer.py":patched_test().encode(),"README.md":b"XP v16 fresh 240-minute attempt materials successor. Retains the exact f6f dedicated-image publisher. Expired v15 external authorization is immutable, zero-credit, delete/reuse prohibited. MySQL-only; PostgreSQL mutation prohibited. Offline bundle: no data-plane action.\n"}
 files={k:sha_bytes(v) for k,v in sorted(c.items())};ident={"schema_version":"mysql_xp_compute00_fresh_ttl_successor_bundle_v16","status":"PASS_IMMUTABLE_OFFLINE_NOT_EXECUTED","immutable":True,"predecessor_support_bundle_id":PRE_ID,"predecessor_support_manifest_sha256":PRE_SHA,"f6f_image_publisher_sha256":sha_bytes(publisher),"expired_attempt":EXPIRED_ATTEMPT,"expired_external_authorization_delete_prohibited":True,"expired_external_authorization_reuse_prohibited":True,"new_attempt_required":True,"ttl_minutes_required":240,"unit_prefix":"ra-mysql-xp-v16-","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"formal_r0_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"data_plane_actions":0,"files":files}
 bid=sha_bytes(canonical(ident));manifest=dict(ident,bundle_id=bid);dest=OUT/bid
 if dest.exists():return dest
 OUT.mkdir(parents=True,exist_ok=True);stage=Path(tempfile.mkdtemp(prefix=".xp-v16-",dir=OUT))
 try:
  for n,v in c.items():p=stage/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
  raw=canonical(manifest);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(sha_bytes(raw)+"  bundle_manifest.json\n")
  for p in sorted(stage.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
  os.chmod(stage,0o555);os.replace(stage,dest)
 finally:
  if stage.exists():shutil.rmtree(stage)
 if fmap(dest)!=files:raise RuntimeError("v16 closure differs")
 return dest
if __name__=="__main__":
 r=build();print(json.dumps({"status":"PASS_IMMUTABLE_OFFLINE_NOT_EXECUTED","bundle":str(r),"bundle_id":r.name,"manifest_sha256":sha_file(r/"bundle_manifest.json"),"data_plane_actions":0},sort_keys=True))

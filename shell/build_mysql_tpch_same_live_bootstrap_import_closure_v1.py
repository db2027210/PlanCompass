#!/usr/bin/env python3
"""Repair 13cce ff313 sibling-module import closure and exact-path binding."""
from __future__ import annotations
import hashlib,json,os,shutil,stat,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve()
OLD_ID="13cce818601022f566e7b9f48f71818cef4ae0be7aa335e46f965aefaf717d37";OLD_SHA="f5e19cff2c262091a4c94f73bc8f7a64d120d6a0c4d9a731dfc92a34ee5d75cd";OLD=ROOT/"artifacts/mysql_tpch_same_live_bootstrap_refresh_v1"/OLD_ID
FF_ID="ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4";FF_SHA="af1d0facca8b33e2638c0a2af6a751bececc407d483055ca064a34308d27bb52";FF=ROOT/"artifacts/mysql_tpch_sql_native_percentile_successor_v1"/FF_ID;AUTH="mysql_tpch_distributed_v2_authority.py";IDENT="mysql_tpch_client_immutable_identity_v1.py";AUTH_SHA="120a848c63c6aaa5ecce24f55ed33d6b678d7a21e30ed2050b7a2efdd7a7b408";IDENT_SHA="8112d805623183b412a779cb9549273b40f7f1f02a1bc85d5d6407d8e02629d3"
MAT="materializer/mysql_tpch_same_live_bootstrap_refresh_v1.py";BUILDER="builder/build_mysql_tpch_same_live_bootstrap_import_closure_v1.py";AUDIT="authority/import_closure_remote_failure_audit.json";TEST="tests/test_mysql_tpch_same_live_bootstrap_import_closure_v1.py";OUT=ROOT/"artifacts/mysql_tpch_same_live_bootstrap_import_closure_v1"
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sb(b):return hashlib.sha256(b).hexdigest()
def sf(p):
 d=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
def fmap(r):return {p.relative_to(r).as_posix():sf(p) for p in sorted(r.rglob("*")) if p.is_file() and p.name not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
def patch_materializer(b):
 s=b.decode();constant='AUTHORITY_REL="mysql_tpch_distributed_v2_authority.py"';constant_new=constant+';IDENTITY_REL="mysql_tpch_client_immutable_identity_v1.py";IDENTITY_SHA="8112d805623183b412a779cb9549273b40f7f1f02a1bc85d5d6407d8e02629d3"'
 if s.count(constant)!=1:raise RuntimeError("identity constant anchor differs")
 s=s.replace(constant,constant_new);old='def import_file(p,name):s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m';new='''def import_file(p,name):
 p=Path(p).resolve(strict=True);parent=str(p.parent);before=list(sys.path);sibling="mysql_tpch_client_immutable_identity_v1";previous=sys.modules.pop(sibling,None);sys.path.insert(0,parent)
 try:
  s=importlib.util.spec_from_file_location(name,p)
  if s is None or s.loader is None:raise Error("ff313 authority import spec differs")
  m=importlib.util.module_from_spec(s);s.loader.exec_module(m);loaded=sys.modules.get(sibling)
  if loaded is None or Path(str(getattr(loaded,"__file__",""))).resolve(strict=True)!=p.parent/IDENTITY_REL:raise Error("ff313 immutable-identity import did not resolve to exact sibling")
  return m
 finally:
  sys.path[:]=before;sys.modules.pop(sibling,None)
  if previous is not None:sys.modules[sibling]=previous'''
 if s.count(old)!=1:raise RuntimeError("import_file anchor differs")
 s=s.replace(old,new)
 old2='oldsp,oldspec=sealed(a.old_bootstrap_spec,"old bootstrap spec");oldep,oldevidence=sealed(a.old_bootstrap_evidence,"old bootstrap evidence")'
 new2=old2+'\n oldbundle=oldspec.get("bundle") or {};oldpath=Path(str(oldbundle.get("path") or ""))\n if oldbundle.get("bundle_id")!=TARGET_ID or oldbundle.get("manifest_sha256")!=TARGET_SHA or not oldpath.is_absolute() or oldpath.resolve(strict=True)!=target:raise Error("old bootstrap spec is not bound to the exact published ff313 path")'
 if s.count(old2)!=1:raise RuntimeError("old spec anchor differs")
 s=s.replace(old2,new2)
 old3='if oldevidence.get("status")!="PASS_BOOTSTRAP_LIVE_NOT_R0_OR_FORMAL_AUTHORITY" or oldevidence.get("attempt_token")!=oldspec.get("attempt_token") or oldevidence.get("bundle_id")!=TARGET_ID or oldevidence.get("formal_r0_authorized") is not False or oldevidence.get("accepted_round_authorized") is not False:raise Error("old bootstrap identity/authority differs")'
 new3=old3+'\n authority=import_file(authority_path,"ff313_refresh_authority")'
 if s.count(old3)!=1:raise RuntimeError("early import anchor differs")
 s=s.replace(old3,new3)
 old4='specp=out/"fresh_bootstrap_spec.json";seal(specp,spec);authority=import_file(authority_path,"ff313_refresh_authority");authority.validate_spec(spec)'
 new4='specp=out/"fresh_bootstrap_spec.json";seal(specp,spec);authority.validate_spec(spec)'
 if s.count(old4)!=1:raise RuntimeError("late import anchor differs")
 s=s.replace(old4,new4);old5='if sha(authority_path)!=AUTHORITY_SHA or (tm.get("files") or {}).get(AUTHORITY_REL)!=AUTHORITY_SHA or sha(entry)!=ENTRY_SHA or sha(core)!=CORE_SHA:raise Error("validator/collector closure differs")';new5='if sha(authority_path)!=AUTHORITY_SHA or (tm.get("files") or {}).get(AUTHORITY_REL)!=AUTHORITY_SHA or sha(target/IDENTITY_REL)!=IDENTITY_SHA or (tm.get("files") or {}).get(IDENTITY_REL)!=IDENTITY_SHA or sha(entry)!=ENTRY_SHA or sha(core)!=CORE_SHA:raise Error("validator/identity/collector closure differs")'
 if s.count(old5)!=1:raise RuntimeError("identity closure anchor differs")
 return s.replace(old5,new5).encode()
TEST_BODY=r'''import importlib.util,sys,types
from pathlib import Path
B=Path(__file__).resolve().parents[1];s=importlib.util.spec_from_file_location("refresh",B/"materializer/mysql_tpch_same_live_bootstrap_refresh_v1.py");R=importlib.util.module_from_spec(s);s.loader.exec_module(R)
def test_actual_ff313_sibling_import_closure_and_path_restoration():
 fixture=B/"tests/fixtures/ff313";before=list(sys.path);m=R.import_file(fixture/"mysql_tpch_distributed_v2_authority.py","ff313_actual_layout_regression");assert callable(m.validate_spec) and list(sys.path)==before
def test_preloaded_wrong_sibling_cannot_be_reused_and_is_restored():
 fixture=B/"tests/fixtures/ff313";name="mysql_tpch_client_immutable_identity_v1";fake=types.ModuleType(name);fake.__file__="/tmp/not-ff313.py";previous=sys.modules.get(name);sys.modules[name]=fake
 try:m=R.import_file(fixture/"mysql_tpch_distributed_v2_authority.py","ff313_collision_regression");assert callable(m.validate_spec) and sys.modules.get(name) is fake
 finally:
  if previous is None:sys.modules.pop(name,None)
  else:sys.modules[name]=previous
def test_exact_published_path_is_fail_closed_before_collect():
 source=(B/"materializer/mysql_tpch_same_live_bootstrap_refresh_v1.py").read_text();path_check='oldpath.resolve(strict=True)!=target';collect='argv=[sys.executable,str(entry),"--collect-only"';assert path_check in source and source.index(path_check)<source.index(collect)
def test_authority_import_occurs_before_read_only_collect():
 source=(B/"materializer/mysql_tpch_same_live_bootstrap_refresh_v1.py").read_text();assert source.index('authority=import_file(authority_path')<source.index('argv=[sys.executable,str(entry),"--collect-only"')
'''
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)
def build():
 m=json.loads((OLD/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id",None)
 if m.get("bundle_id")!=OLD_ID or sf(OLD/"bundle_manifest.json")!=OLD_SHA or sb(canonical(u))!=OLD_ID or fmap(OLD)!=m.get("files"):raise RuntimeError("13cce predecessor differs")
 fm=json.loads((FF/"bundle_manifest.json").read_text())
 if fm.get("bundle_id")!=FF_ID or sf(FF/"bundle_manifest.json")!=FF_SHA or sf(FF/AUTH)!=AUTH_SHA or sf(FF/IDENT)!=IDENT_SHA or (fm.get("files") or {}).get(IDENT)!=IDENT_SHA:raise RuntimeError("actual ff313 import closure differs")
 content={n:(patch_materializer((OLD/n).read_bytes()) if n==MAT else (OLD/n).read_bytes()) for n in sorted(m["files"]) if n!="builder/build_mysql_tpch_same_live_bootstrap_refresh_v1.py"};content[BUILDER]=HERE.read_bytes();content[TEST]=TEST_BODY.encode();content["tests/fixtures/ff313/"+AUTH]=(FF/AUTH).read_bytes();content["tests/fixtures/ff313/"+IDENT]=(FF/IDENT).read_bytes();content[AUDIT]=canonical({"schema_version":"mysql_tpch_same_live_bootstrap_import_closure_audit_v1","status":"PASS_ACTUAL_FF313_SIBLING_IMPORT_AND_EXACT_PATH_ZERO_CREDIT","predecessor_bundle_id":OLD_ID,"predecessor_manifest_sha256":OLD_SHA,"actual_ff313_bundle_id":FF_ID,"actual_ff313_manifest_sha256":FF_SHA,"authority_sha256":AUTH_SHA,"immutable_identity_sha256":IDENT_SHA,"rejected_remote_output":"bootstrap_refresh_13cce_20260803t1241p08","rejected_remote_output_permanent_zero_credit":True,"rejected_remote_output_reuse_prohibited":True,"authority_import_before_collect_only":True,"exact_ff313_path_required":True,"valid_seconds_default":1800,"valid_seconds_minimum":300,"valid_seconds_maximum":3600,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0})
 files={n:sb(v) for n,v in sorted(content.items())};ident={k:v for k,v in m.items() if k not in ("bundle_id","files","schema_version","status")};ident.update({"schema_version":"mysql_tpch_same_live_bootstrap_import_closure_bundle_v1","status":"PASS_IMMUTABLE_IMPORT_CLOSURE_REFRESHER_NOT_EXECUTED","predecessor_bundle_id":OLD_ID,"predecessor_manifest_sha256":OLD_SHA,"ff313_immutable_identity_sha256":IDENT_SHA,"actual_ff313_sibling_import_tested":True,"exact_ff313_path_required":True,"rejected_remote_output_reuse_prohibited":True,"valid_seconds_default":1800,"valid_seconds_minimum":300,"valid_seconds_maximum":3600,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions_during_build":0,"files":files});bid=sb(canonical(ident));manifest=dict(ident,bundle_id=bid);dst=OUT/bid
 if dst.exists():
  if sf(dst/"bundle_manifest.json")!=sb(canonical(manifest)):raise RuntimeError("existing differs")
  return dst
 OUT.mkdir(parents=True,exist_ok=True);stage=Path(tempfile.mkdtemp(prefix=".bootstrap-import-closure-",dir=OUT))
 try:
  for n,v in content.items():p=stage/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
  raw=canonical(manifest);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(sb(raw)+"  bundle_manifest.json\n");freeze(stage);os.replace(stage,dst)
 finally:
  if stage.exists():shutil.rmtree(stage)
 if fmap(dst)!=files:raise RuntimeError("published files differ")
 for p in (dst,*dst.rglob("*")):
  if p.is_symlink() or stat.S_IMODE(p.stat().st_mode)&0o222:raise RuntimeError("mutable successor")
 return dst
if __name__=="__main__":
 b=build();print(json.dumps({"bundle":str(b),"bundle_id":b.name,"manifest_sha256":sf(b/"bundle_manifest.json"),"materializer_sha256":sf(b/MAT),"remote_actions_during_build":0},sort_keys=True))

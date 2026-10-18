#!/usr/bin/env python3
"""Rebind Retry9 materializer to dynamic support and sealed bootstrap inputs."""
from __future__ import annotations
import hashlib,json,os,shutil,stat,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve()
OLD_ID="1fdd3d28a1f20fbd47fecd6ec69621802702293a9e860c1e82ab6528e4623c66";OLD_SHA="3d359c54f62465dc7618f5b932c8357d8c2a1821d99c0d678d48b7c2b9f2fb7e";OLD=ROOT/"artifacts/mysql_tpch_retry9_dual_principal_materializer_successor_v1"/OLD_ID
SUPPORT="947fe36dd89966bfcf1427a67ecd60e872b450ce656556f6255199e47f353739";SUPPORT_SHA="1be67f24372e9893b37be8b717e24ff66d76a33dc40cd73149000abd905e14b7";ENTRY_SHA="8287cf1954e634cc298665fa9b71026b9b4ed9612b3d995e868a44a95587035a";CORE_SHA="ae6384be782bf4954419dfffbd25a7395719f1cb967bc2477d4414790e2b5317"
OUT=ROOT/"artifacts/mysql_tpch_retry9_dynamic_attempt_materializer_successor_v1";M="materializer/mysql_tpch_qualification_retry5_completion_materializer_v1.py";BUILDER="builder/build_mysql_tpch_retry9_dynamic_attempt_materializer_successor_v1.py";FAILED="tpch-v2-20260803t085536z-b235retry95c56007ba48c"
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sb(v):return hashlib.sha256(v).hexdigest()
def sf(p):return sb(Path(p).read_bytes())
def fmap(r):return {p.relative_to(r).as_posix():sf(p) for p in sorted(r.rglob("*")) if p.is_file() and p.relative_to(r).as_posix() not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
def verify():
 m=json.loads((OLD/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id",None)
 if m.get("bundle_id")!=OLD_ID or sf(OLD/"bundle_manifest.json")!=OLD_SHA or sb(canonical(u))!=OLD_ID or fmap(OLD)!=m.get("files"):raise RuntimeError("1fdd closure differs")
 return m
def patch(s):
 s=s.replace('SUPPORT_ID="a070e44ed4ca1acb0f5b4e0fe03386c6fc0dd43736d0f477cdd17a006bd465b6";SUPPORT_SHA="a529c3aa35884314893afdd50b093a251dbf31c6a83d9ff797df052c960a7dce";ENTRY_SHA="8cb1283f8cc0d08af3a4d8d218880e9eb6d61b5d7958e971b42a026462b02293";CORE_SHA="ae6384be782bf4954419dfffbd25a7395719f1cb967bc2477d4414790e2b5317"',f'SUPPORT_ID="{SUPPORT}";SUPPORT_SHA="{SUPPORT_SHA}";ENTRY_SHA="{ENTRY_SHA}";CORE_SHA="{CORE_SHA}"')
 old='for n in ("bundle","support-bundle","successor-command","retry8-failure-evidence","supplement","mysql-defaults-file","output-dir"):p.add_argument("--"+n,type=Path,required=True)'
 new='for n in ("bundle","support-bundle","successor-command","bootstrap-spec","bootstrap-evidence","retry8-failure-evidence","supplement","mysql-defaults-file","output-dir"):p.add_argument("--"+n,type=Path,required=True)'
 if s.count(old)!=1:raise RuntimeError("arg anchor differs")
 s=s.replace(old,new)
 old=''' base=load(a.successor_command,"fresh successor command");failure=load(a.retry8_failure_evidence,"retry8 failure evidence");old=base.get("qualification_attempt_id");validate_retry8_failure(failure)
 supplement=load(a.supplement,"legacy blkio supplement")
'''
 new=''' base=load(a.successor_command,"fresh successor command");spec=load(a.bootstrap_spec,"fresh bootstrap spec");evidence=load(a.bootstrap_evidence,"fresh bootstrap evidence");failure=load(a.retry8_failure_evidence,"retry8 failure evidence");old=base.get("qualification_attempt_id");validate_retry8_failure(failure)
 supplement=load(a.supplement,"legacy blkio supplement")
'''
 if s.count(old)!=1:raise RuntimeError("input anchor differs")
 s=s.replace(old,new)
 old=''' bootstrap_attempt=supplement.get("attempt_token");db=supplement.get("database_container_name");client=(base.get("client_container") or {}).get("name")
 if not isinstance(bootstrap_attempt,str) or old!=bootstrap_attempt+"-qualification_solo" or not isinstance(db,str) or not isinstance(client,str) or db==client:raise Error("fresh bootstrap container binding differs")
 defaults=a.mysql_defaults_file
'''
 new=''' bootstrap_attempt=supplement.get("attempt_token");db=supplement.get("database_container_name");client=(base.get("client_container") or {}).get("name")
 spec_bundle=spec.get("bundle") or {};spec_paths=spec.get("paths") or {}
 if (not isinstance(bootstrap_attempt,str) or old!=bootstrap_attempt+"-qualification_solo" or not isinstance(db,str) or not isinstance(client,str) or db==client or spec.get("attempt_token")!=bootstrap_attempt or evidence.get("attempt_token")!=bootstrap_attempt or evidence.get("status")!="PASS_BOOTSTRAP_LIVE_NOT_R0_OR_FORMAL_AUTHORITY" or evidence.get("bundle_id")!=TARGET_ID or evidence.get("spec_sha256")!=sha(a.bootstrap_spec) or spec_bundle.get("bundle_id")!=TARGET_ID or Path(str(spec_bundle.get("path") or "")).resolve(strict=True)!=target or spec_paths.get("attempt_root")!=str(target.parent.parent/"attempts"/bootstrap_attempt)):raise Error("fresh bootstrap spec/evidence/command attempt binding differs")
 defaults=a.mysql_defaults_file
 if str(spec.get("mysql_defaults_file") or "")!=str(defaults):raise Error("fresh bootstrap spec/defaults binding differs")
'''
 if s.count(old)!=1:raise RuntimeError("attempt anchor differs")
 s=s.replace(old,new)
 old='''"mysql_defaults_binding":defaults_contract,"predecessor_reuse_prohibited":True,"formal_launch_authorized":False'''
 new='''"bootstrap_spec_sha256":sha(a.bootstrap_spec),"bootstrap_evidence_sha256":sha(a.bootstrap_evidence),"bootstrap_attempt_token":bootstrap_attempt,"failed_5c560_attempt_rerun_prohibited":True,"mysql_defaults_binding":defaults_contract,"predecessor_reuse_prohibited":True,"formal_launch_authorized":False'''
 if s.count(old)!=1:raise RuntimeError("receipt anchor differs")
 return s.replace(old,new)
TEST=r'''from __future__ import annotations
import hashlib,json
from pathlib import Path
B=Path(__file__).resolve().parents[1];S=B/"materializer/mysql_tpch_qualification_retry5_completion_materializer_v1.py"
def test_manifest_and_dynamic_support_binding():
 m=json.loads((B/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id");assert hashlib.sha256((json.dumps(u,sort_keys=True,indent=2)+"\n").encode()).hexdigest()==m["bundle_id"]==B.name
 assert m["support_bundle_id"]=="947fe36dd89966bfcf1427a67ecd60e872b450ce656556f6255199e47f353739"
def test_spec_evidence_command_attempt_binding_is_explicit():
 s=S.read_text()
 for t in ('"bootstrap-spec"','"bootstrap-evidence"','spec.get("attempt_token")!=bootstrap_attempt','evidence.get("attempt_token")!=bootstrap_attempt','evidence.get("spec_sha256")!=sha(a.bootstrap_spec)','old!=bootstrap_attempt+"-qualification_solo"','spec.get("mysql_defaults_file")','failed_5c560_attempt_rerun_prohibited'):assert t in s
def test_failed_attempt_is_zero_credit_and_not_hardcoded_as_next_attempt():
 m=json.loads((B/"bundle_manifest.json").read_text());assert m["failed_attempt_token"]=="tpch-v2-20260803t085536z-b235retry95c56007ba48c" and m["failed_attempt_rerun_prohibited"] is True and m["accepted_rounds_credit"]==0
'''
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)
def build():
 m=verify();content={}
 for n in sorted(m["files"]):
  if n.startswith("builder/") or n.startswith("tests/"):continue
  b=(OLD/n).read_bytes();content[n]=patch(b.decode()).encode() if n==M else b
 content[BUILDER]=HERE.read_bytes();content["tests/test_mysql_tpch_retry9_dynamic_attempt_materializer_successor_v1.py"]=TEST.encode();files={n:sb(b) for n,b in sorted(content.items())}
 u={"schema_version":"mysql_tpch_retry9_dynamic_attempt_materializer_successor_bundle_v1","status":"SEALED_SPEC_EVIDENCE_COMMAND_DYNAMIC_ATTEMPT_MATERIALIZER_NOT_EXECUTED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"predecessor_bundle_id":OLD_ID,"predecessor_manifest_sha256":OLD_SHA,"support_bundle_id":SUPPORT,"support_manifest_sha256":SUPPORT_SHA,"support_entry_sha256":ENTRY_SHA,"support_core_sha256":CORE_SHA,"fresh_spec_evidence_command_attempt_exact_binding_required":True,"dual_principal_defaults_exact_binding_required":True,"failed_attempt_token":FAILED,"failed_attempt_rerun_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,"files":files};bid=sb(canonical(u));manifest=dict(u,bundle_id=bid);dst=OUT/bid
 if not dst.exists():
  OUT.mkdir(parents=True,exist_ok=True);stage=Path(tempfile.mkdtemp(prefix=".dynamic-materializer-",dir=OUT))
  try:
   for n,b in content.items():p=stage/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
   raw=canonical(manifest);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(sb(raw)+"  bundle_manifest.json\n");freeze(stage);os.replace(stage,dst)
  finally:
   if stage.exists():shutil.rmtree(stage)
 if fmap(dst)!=files or sf(dst/"bundle_manifest.json")!=sb(canonical(manifest)):raise RuntimeError("closure differs")
 return dst
if __name__=="__main__":
 b=build();print(json.dumps({"bundle":str(b),"bundle_id":b.name,"manifest_sha256":sf(b/"bundle_manifest.json"),"materializer_sha256":sf(b/M),"remote_actions":0},sort_keys=True))

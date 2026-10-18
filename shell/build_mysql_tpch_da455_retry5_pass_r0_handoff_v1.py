#!/usr/bin/env python3
"""Build retry5 PASS to da455 postqualification/R0 handoff successor."""
from __future__ import annotations
import hashlib,json,os,shutil,stat,tempfile
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve()
OLD_ID="ed63d5d610b9828c5885a56dd45689f324db8d3bf268b7fd4237fde18daf6bd1";OLD_SHA="48182980da7710d9ecdb04f1879bbae52e0d79965a1e2bbf722885fa875faf7b";OLD=ROOT/"artifacts/mysql_tpch_qualification_pass_fresh_r0_handoff_v1"/OLD_ID
OLD_SUPPORT_ID="86232f80b4e274aadf1cdbd8acc19033786acfda8dafb6dc18f7bace4bdb61a3";OLD_SUPPORT_SHA="2ef63e1ad38dc0217011317c1ed3177c47e6fb46380587482739f87eee761cf7";OLD_CORE="6cc0958fe0c485f0b5efa494b4e9b4868488a4fad07f06e4b11c7978f9f2594f";OLD_POST="6b3457e74f11119cd20c9afc23089da5c27177ccd4290df0eff13619e1df47b9";OLD_POST_SHA="96a98a43405b914502851c66eadef4f16586517e6a83991fce5b0975c0efb97e";OLD_RUNNER="3d3db6e4e178ec747a25e74e1ee4524ed4daefe7fbe9070f93b6ff0c2c6de50e"
SUPPORT_ID="da455246652a82bb13d63f5f1ba6ebeb2e92fd08389e3f06707e5897e12646b6";SUPPORT_SHA="0f835d084459a2c66729327a9a4b9ed6ec070116e9873631874ac667039b34c4";CORE="ae8f22b8c4dab4f4e02e023122c72f21b7ebb244ba3d57e136b91468d7542863";ENTRY="5545dcb6277fd8979a4b6e1994bf7941aa8db19b4a369498bf22ae4b8c2412b1";POST="2b9e632feb676bf10bf4524b6e704cdab95be6822c6cfe9a9317902e45148a94";POST_SHA="f8d0bc5fe5a8a0460ddff21ddd7936236a1e337f81f7fe0fa6d7ce3377468d5c";RUNNER="51980a5e0e23a21fa35f39b6dfaa369899ef333f7571996583a9098f6fb26e63"
OUT=ROOT/"artifacts/mysql_tpch_da455_retry5_pass_r0_handoff_v1";BUILDER="builder/build_mysql_tpch_da455_retry5_pass_r0_handoff_v1.py";AUDIT="authority/da455_retry5_handoff_audit.json"
def canonical(v:Any)->bytes:return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sb(v:bytes)->str:return hashlib.sha256(v).hexdigest()
def sf(p:Path)->str:
 d=hashlib.sha256()
 with p.open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
def load(p):return json.loads(p.read_text())
def fmap(r):return {p.relative_to(r).as_posix():sf(p) for p in sorted(r.rglob("*")) if p.is_file() and p.name not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
def verify():
 m=load(OLD/"bundle_manifest.json");u=dict(m);u.pop("bundle_id",None)
 if m.get("bundle_id")!=OLD_ID or sf(OLD/"bundle_manifest.json")!=OLD_SHA or sb(canonical(u))!=OLD_ID or fmap(OLD)!=m.get("files"):raise RuntimeError("old handoff differs")
 p=ROOT/"artifacts/mysql_tpch_da455_postqualification_successor_v1"/POST
 if sf(p/"bundle_manifest.json")!=POST_SHA or sf(p/"qualification/mysql_tpch_v2_fresh_r0_runner_v2.py")!=RUNNER:raise RuntimeError("postqualification successor differs")
 return m
def patch(n,b):
 try:s=b.decode()
 except UnicodeDecodeError:return b
 for a,z in ((OLD_SUPPORT_ID,SUPPORT_ID),(OLD_SUPPORT_SHA,SUPPORT_SHA),(OLD_CORE,CORE),(OLD_POST,POST),(OLD_POST_SHA,POST_SHA),(OLD_RUNNER,RUNNER),("retry4","retry5"),("RETRY4","RETRY5"),("[REMOTE_86232]","[REMOTE_DA455]"),("[REMOTE_BE747]","[REMOTE_DA455_POST]")) :s=s.replace(a,z)
 s=s.replace("mysql_tpch_qualification_retry5_materialization_v1","mysql_tpch_qualification_retry5_completion_materialization_v1").replace("retry5_materializer_binding","retry5_completion_binding")
 if n.endswith("materializer/mysql_tpch_qualification_pass_fresh_r0_handoff_v1.py"):
  anchor='material=load(a.retry5_materialization,"retry5 materialization");oldcmd=load(a.retry5_command,"retry5 command");passed=load(a.retry5_output,"retry5 PASS output");module.core.validate_qualification_evidence(a.retry5_output.resolve(),"qualification_solo");old=material.get("qualification_attempt_id")'
  replacement=anchor+'\n completion_path=Path((material.get("fresh_paths") or {}).get("completion",""));host_metric=Path((material.get("fresh_paths") or {}).get("host_metric",""));completion=module.core.validate_native_metric_producer_completion(host_metric,old);\n if completion_path.resolve()!=Path(str(host_metric)+".producer-complete-v1.json").resolve() or completion.get("sample_count")!=181 or completion.get("last_sequence")!=180:raise HandoffError("retry5 finite completion seal differs")'
  if s.count(anchor)!=1:raise RuntimeError("retry5 validation anchor differs")
  s=s.replace(anchor,replacement)
  oldcheck='or material.get("accepted_rounds_credit")!=0 or oldcmd.get("qualification_attempt_id")!=old'
  newcheck='or material.get("support_collector_core_sha256")!=CORE_SHA or material.get("completion_sidecar_required") is not True or material.get("completion_metric_stream_sha256_required") is not True or material.get("accepted_rounds_credit")!=0 or oldcmd.get("qualification_attempt_id")!=old'
  if s.count(oldcheck)!=1:raise RuntimeError("retry5 material check anchor differs")
  s=s.replace(oldcheck,newcheck)
 return s.encode()
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)
def build():
 old=verify();content={n:patch(n,(OLD/n).read_bytes()) for n in sorted(old["files"]) if n!="builder/build_mysql_tpch_qualification_pass_fresh_r0_handoff_v1.py"};content[BUILDER]=HERE.read_bytes();content[AUDIT]=canonical({"schema_version":"mysql_tpch_da455_retry5_pass_r0_handoff_audit_v1","status":"PASS_EXACT_RETRY5_COMPLETION_AND_DA455_BINDING_ZERO_CREDIT","predecessor_bundle_id":OLD_ID,"support_collector_bundle_id":SUPPORT_ID,"support_collector_manifest_sha256":SUPPORT_SHA,"support_collector_core_sha256":CORE,"postqualification_bundle_id":POST,"postqualification_manifest_sha256":POST_SHA,"fresh_r0_runner_sha256":RUNNER,"retry5_pass_required":True,"retry5_completion_seal_required":True,"old_86232_collector_accepted":False,"failed_evidence_accepted":False,"solo_evidence_is_dynamic_gate":False,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0})
 files={n:sb(v) for n,v in sorted(content.items())};u={"schema_version":"mysql_tpch_da455_retry5_pass_r0_handoff_bundle_v1","status":"PASS_IMMUTABLE_LOCAL_HANDOFF_NOT_LAUNCHED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"predecessor_bundle_id":OLD_ID,"predecessor_manifest_sha256":OLD_SHA,"support_collector_bundle_id":SUPPORT_ID,"support_collector_manifest_sha256":SUPPORT_SHA,"support_collector_entry_sha256":ENTRY,"support_collector_core_sha256":CORE,"postqualification_bundle_id":POST,"postqualification_manifest_sha256":POST_SHA,"fresh_r0_runner_sha256":RUNNER,"retry5_pass_required":True,"retry5_completion_seal_required":True,"unexpired_da455_dynamic_gate_required":True,"old_86232_collector_accepted":False,"failed_evidence_accepted":False,"manual_json_edit_required":False,"materializer_executes_workload":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,"files":files};bid=sb(canonical(u));m=dict(u,bundle_id=bid);f=OUT/bid
 if f.exists():
  if sf(f/"bundle_manifest.json")!=sb(canonical(m)):raise RuntimeError("existing differs")
  return f
 OUT.mkdir(parents=True,exist_ok=True);st=Path(tempfile.mkdtemp(prefix=".da455-handoff-",dir=OUT))
 try:
  for n,v in content.items():p=st/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
  raw=canonical(m);(st/"bundle_manifest.json").write_bytes(raw);(st/"bundle_manifest.json.sha256").write_text(sb(raw)+"  bundle_manifest.json\n");freeze(st);os.replace(st,f)
 finally:
  if st.exists():shutil.rmtree(st)
 if fmap(f)!=files:raise RuntimeError("published files differ")
 for p in (f,*f.rglob("*")):
  if p.is_symlink() or stat.S_IMODE(p.stat().st_mode)&0o222:raise RuntimeError("mutable successor")
 return f
if __name__=="__main__":
 b=build();print(json.dumps({"bundle":str(b),"bundle_id":b.name,"manifest_sha256":sf(b/"bundle_manifest.json"),"materializer_sha256":sf(b/"materializer/mysql_tpch_qualification_pass_fresh_r0_handoff_v1.py"),"remote_actions":0},sort_keys=True))

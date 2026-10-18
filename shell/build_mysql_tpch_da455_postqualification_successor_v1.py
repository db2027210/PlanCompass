#!/usr/bin/env python3
"""Rebind the frozen postqualification/R0/top5 closure to da455 exactly."""
from __future__ import annotations
import hashlib,json,os,shutil,stat,tempfile
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve()
OLD_ID="6b3457e74f11119cd20c9afc23089da5c27177ccd4290df0eff13619e1df47b9";OLD_SHA="96a98a43405b914502851c66eadef4f16586517e6a83991fce5b0975c0efb97e";OLD=ROOT/"artifacts/mysql_tpch_postqualification_r0_top5_successor_v1"/OLD_ID
OLD_SUPPORT_ID="86232f80b4e274aadf1cdbd8acc19033786acfda8dafb6dc18f7bace4bdb61a3";OLD_SUPPORT_SHA="2ef63e1ad38dc0217011317c1ed3177c47e6fb46380587482739f87eee761cf7";OLD_CORE_SHA="6cc0958fe0c485f0b5efa494b4e9b4868488a4fad07f06e4b11c7978f9f2594f"
SUPPORT_ID="da455246652a82bb13d63f5f1ba6ebeb2e92fd08389e3f06707e5897e12646b6";SUPPORT_SHA="0f835d084459a2c66729327a9a4b9ed6ec070116e9873631874ac667039b34c4";CORE_SHA="ae8f22b8c4dab4f4e02e023122c72f21b7ebb244ba3d57e136b91468d7542863";ENTRY_SHA="5545dcb6277fd8979a4b6e1994bf7941aa8db19b4a369498bf22ae4b8c2412b1"
SUPPORT=ROOT/"artifacts/mysql_tpch_native_metric_completion_successor_v1"/SUPPORT_ID;OUT=ROOT/"artifacts/mysql_tpch_da455_postqualification_successor_v1";BUILDER="builder/build_mysql_tpch_da455_postqualification_successor_v1.py";AUDIT="authority/da455_rebind_audit.json"
def canonical(v:Any)->bytes:return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sb(v:bytes)->str:return hashlib.sha256(v).hexdigest()
def sf(p:Path)->str:
 d=hashlib.sha256()
 with p.open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
def fmap(root):return {p.relative_to(root).as_posix():sf(p) for p in sorted(root.rglob("*")) if p.is_file() and p.name not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
def load(p):
 v=json.loads(p.read_text());
 if not isinstance(v,dict):raise RuntimeError("object required")
 return v
def verify():
 om=load(OLD/"bundle_manifest.json");u=dict(om);u.pop("bundle_id",None)
 if om.get("bundle_id")!=OLD_ID or sf(OLD/"bundle_manifest.json")!=OLD_SHA or sb(canonical(u))!=OLD_ID or fmap(OLD)!=om.get("files"):raise RuntimeError("old postqualification closure differs")
 sm=load(SUPPORT/"bundle_manifest.json")
 if sm.get("bundle_id")!=SUPPORT_ID or sf(SUPPORT/"bundle_manifest.json")!=SUPPORT_SHA or sf(SUPPORT/"qualification/mysql_distributed_live_readback_qualification_v1.py")!=CORE_SHA or sf(SUPPORT/"qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py")!=ENTRY_SHA:raise RuntimeError("da455 support differs")
 return om
def replace(payload:bytes)->bytes:
 try:s=payload.decode("utf-8")
 except UnicodeDecodeError:return payload
 return s.replace(OLD_SUPPORT_ID,SUPPORT_ID).replace(OLD_SUPPORT_SHA,SUPPORT_SHA).replace(OLD_CORE_SHA,CORE_SHA).replace("[EXACT_86232_SUPPORT_BUNDLE]","[EXACT_DA455_SUPPORT_BUNDLE]").encode()
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)
def build():
 old=verify();content={n:replace((OLD/n).read_bytes()) for n in sorted(old["files"]) if n!="builder/build_mysql_tpch_postqualification_r0_top5_successor_v1.py"};content[BUILDER]=HERE.read_bytes();content[AUDIT]=canonical({"schema_version":"mysql_tpch_da455_postqualification_rebind_audit_v1","status":"PASS_EXACT_DA455_REBIND_ZERO_CREDIT","predecessor_bundle_id":OLD_ID,"predecessor_manifest_sha256":OLD_SHA,"support_collector_bundle_id":SUPPORT_ID,"support_collector_manifest_sha256":SUPPORT_SHA,"support_collector_entry_sha256":ENTRY_SHA,"support_collector_core_sha256":CORE_SHA,"old_86232_collector_accepted":False,"failed_qualification_evidence_accepted":False,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0})
 files={n:sb(v) for n,v in sorted(content.items())};identity={k:v for k,v in old.items() if k not in ("bundle_id","files","schema_version","status","builder_sha256","support_collector_bundle_id","support_collector_manifest_sha256","support_collector_core_sha256")};identity.update({"schema_version":"mysql_tpch_da455_postqualification_successor_bundle_v1","status":"SEALED_LOCAL_DA455_POSTQUALIFICATION_NOT_EXECUTED","predecessor_bundle_id":OLD_ID,"predecessor_manifest_sha256":OLD_SHA,"builder_sha256":sf(HERE),"support_collector_bundle_id":SUPPORT_ID,"support_collector_manifest_sha256":SUPPORT_SHA,"support_collector_entry_sha256":ENTRY_SHA,"support_collector_core_sha256":CORE_SHA,"old_86232_collector_accepted":False,"failed_qualification_evidence_accepted":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,"files":files});bid=sb(canonical(identity));m=dict(identity,bundle_id=bid);f=OUT/bid
 if f.exists():
  if sf(f/"bundle_manifest.json")!=sb(canonical(m)):raise RuntimeError("existing differs")
  return f
 OUT.mkdir(parents=True,exist_ok=True);st=Path(tempfile.mkdtemp(prefix=".da455-post-",dir=OUT))
 try:
  for n,v in content.items():p=st/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
  raw=canonical(m);(st/"bundle_manifest.json").write_bytes(raw);(st/"bundle_manifest.json.sha256").write_text(sb(raw)+"  bundle_manifest.json\n");freeze(st);os.replace(st,f)
 finally:
  if st.exists():shutil.rmtree(st)
 if fmap(f)!=files:raise RuntimeError("published files differ")
 text="\n".join(p.read_text(errors="ignore") for p in f.rglob("*") if p.is_file() and p.relative_to(f).as_posix()!=BUILDER)
 if OLD_SUPPORT_ID in text or OLD_SUPPORT_SHA in text or OLD_CORE_SHA in text:raise RuntimeError("old collector leaked into successor")
 for p in (f,*f.rglob("*")):
  if p.is_symlink() or stat.S_IMODE(p.stat().st_mode)&0o222:raise RuntimeError("mutable successor")
 return f
if __name__=="__main__":
 b=build();print(json.dumps({"bundle":str(b),"bundle_id":b.name,"manifest_sha256":sf(b/"bundle_manifest.json"),"runner_sha256":sf(b/"qualification/mysql_tpch_v2_fresh_r0_runner_v2.py"),"remote_actions":0},sort_keys=True))

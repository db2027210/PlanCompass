#!/usr/bin/env python3
"""Seal local closure evidence and a remote publication checklist."""
from __future__ import annotations
import hashlib,importlib.util,json,os,shutil,stat,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve();OUT=ROOT/"artifacts/mysql_tpch_release_preflight_v1"
BUNDLES={
 "target":("artifacts/mysql_tpch_deadline_shutdown_successor_v1","b235d1ca300e5ad02fe77005096fc511bfbdc5d91b9601233a66a10502be5a47","386f59b23f20eb7be59665c42c44fe2453512bf2112d850e12df6b398a16f439"),
 "support":("artifacts/mysql_tpch_deadline_shutdown_collector_successor_v1","a070e44ed4ca1acb0f5b4e0fe03386c6fc0dd43736d0f477cdd17a006bd465b6","a529c3aa35884314893afdd50b093a251dbf31c6a83d9ff797df052c960a7dce"),
 "materializer":("artifacts/mysql_tpch_qualification_retry5_completion_materializer_v1","e421ba0af5bb1568264196a6df5fd870bbbd7f4690d4f78e95298ae03353aea4","b9c64fb674c78f5ffc983a2cb61ba4a811bb81a158fec0a4f1c7e507b0fa3d3e"),
 "wrapper":("artifacts/mysql_tpch_retry5_systemd219_completion_v1","471be5c39dfa4bcda64245f5f3feda092f20235f099f4f969bbfc9843d3d75ed","ded17dfd37f8c0d5b5db36dcbe70a09b985252565d8b43f00dd98945f02c681c"),
 "formal":("artifacts/mysql_tpch_linear_campaign_atomic_v1","9e4540dc53b27326a867fecf593e21923ee9d6b12daaa1dc7333bf57ed2b5672","0594daf080331416fe30214e14df848460d47f89dc5e67931a8e2f99d87aa5d0"),
}
OLD={"target":("artifacts/mysql_tpch_sql_native_percentile_successor_v1","ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4"),"support":("artifacts/mysql_tpch_deadline_shutdown_collector_successor_v1","ed11c7a10671f852033ad61d0887579d423f915bfc157d9051951c5775f85f2a"),"materializer":("artifacts/mysql_tpch_qualification_retry5_completion_materializer_v1","eb7ec48c9219a702c433de7e656abdbfef08a2bec9f5d83e43a327b88e90e405"),"wrapper":("artifacts/mysql_tpch_retry5_systemd219_completion_v1","2165936c1bb7d894bee96b8602d57a098fd39c11000e2cde94efe08cc57d9d0b"),"formal":("artifacts/mysql_tpch_linear_campaign_atomic_v1","da60019271290e91dd6cc9a2291877e44798a7292135d2eb4f331f70b676636b")}
DISCARDED_SUPPORT=("artifacts/mysql_tpch_parallel_socket_inventory_successor_v1","db187d92940fc57536af98441cb4851c0907f85c92d862789c49ad5c59c56b40")
DISCARDED_TARGET=("artifacts/mysql_tpch_deadline_shutdown_successor_v1","a008c13b58d27e5dcaf17a08a48501191da1dd4caf0e59e1bc1e130275a8f3d4")
OLDER_PROHIBITED=("1e1906c4ce78655425764335cd7571b8cb5ae367f894786ddeabf9dddc8ecf3e","152b4112e4dee8d0f8687bed8ddca8a439ce130412075540eb6924707f823358","c327b3cc0b92bcf0047bae0d6c139e2c82863989c044f16a7c41522cb9903a03")
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sb(b):return hashlib.sha256(b).hexdigest()
def sf(p):
 d=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
def closure(label,rel,bid,msha):
 root=(ROOT/rel/bid).resolve(strict=True);mp=root/"bundle_manifest.json";side=root/"bundle_manifest.json.sha256";m=json.loads(mp.read_text());u=dict(m);u.pop("bundle_id",None)
 actual={p.relative_to(root).as_posix():sf(p) for p in sorted(root.rglob("*")) if p.is_file() and p.relative_to(root).as_posix() not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
 if root.name!=bid or m.get("bundle_id")!=bid or sf(mp)!=msha or sb(canonical(u))!=bid or side.read_text()!=msha+"  bundle_manifest.json\n" or actual!=(m.get("files") or {}) or m.get("formal_scope")!="mysql_only" or m.get("cross_engine_comparison_prohibited") is not True or m.get("formal_launch_authorized") is not False or m.get("accepted_round_authorized") is not False:raise RuntimeError(label+" closure differs")
 bad=[]
 for p in root.rglob("*"):
  mode=stat.S_IMODE(p.stat().st_mode)
  if p.is_symlink() or (p.is_file() and mode&0o222) or (p.is_dir() and mode&0o222):bad.append(str(p))
 if bad:raise RuntimeError(label+" is mutable/symlinked")
 return {"bundle_id":bid,"manifest_sha256":msha,"file_count":len(actual),"local_path":str(root),"all_manifest_files_match":True,"manifest_sidecar_matches":True,"no_unmanifested_payload_files":True,"read_only_no_symlinks":True}
def imod(path,name):s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def rejected(call):
 try:call()
 except Exception:return True
 return False
def negative(rows):
 nb={k:Path(v["local_path"]) for k,v in rows.items()};ob={k:(ROOT/r/i).resolve(strict=True) for k,(r,i) in OLD.items()}
 discarded=(ROOT/DISCARDED_SUPPORT[0]/DISCARDED_SUPPORT[1]).resolve(strict=True)
 discarded_target=(ROOT/DISCARDED_TARGET[0]/DISCARDED_TARGET[1]).resolve(strict=True)
 mat=imod(nb["materializer"]/"materializer/mysql_tpch_qualification_retry5_completion_materializer_v1.py","preflight_mat");wrap=imod(nb["wrapper"]/"controller/mysql_tpch_retry5_systemd219_completion_v1.py","preflight_wrap");exe=imod(nb["formal"]/"executor/mysql_tpch_raw15_fixed_top5_executor_v1.py","preflight_formal")
 checks={
  "old_ff313_rejected_by_new_materializer":rejected(lambda:mat.bundle(ob["target"],mat.TARGET_ID,mat.TARGET_SHA,"old target")),
  "old_ed11_rejected_by_new_materializer":rejected(lambda:mat.bundle(ob["support"],mat.SUPPORT_ID,mat.SUPPORT_SHA,"old support")),
  "old_eb7_rejected_by_new_wrapper":rejected(lambda:wrap.bundle(ob["materializer"],wrap.RETRY_ID,wrap.RETRY_SHA,"old materializer")),
  "old_2165_rejected_by_new_formal":rejected(lambda:exe.bundle(ob["wrapper"],exe.WRAPPER_ID,exe.WRAPPER_SHA,"old wrapper")),
  "old_da60_rejected_by_new_formal":rejected(lambda:exe.bundle(ob["formal"],exe.PROTOCOL_ID,exe.PROTOCOL_SHA,"protocol")),
  "old_ed11_rejected_by_new_wrapper":rejected(lambda:wrap.bundle(ob["support"],wrap.COLLECTOR_ID,wrap.COLLECTOR_SHA,"old collector")),
  "discarded_a008_rejected_by_new_materializer":rejected(lambda:mat.bundle(discarded_target,mat.TARGET_ID,mat.TARGET_SHA,"discarded target")),
  "discarded_db187_rejected_by_new_materializer":rejected(lambda:mat.bundle(discarded,mat.SUPPORT_ID,mat.SUPPORT_SHA,"discarded support")),
 }
 if not all(checks.values()):raise RuntimeError("old-chain negative validation differs")
 return checks
VERIFY=r'''#!/usr/bin/env python3
import hashlib,json,sys
from pathlib import Path
EXPECTED=__EXPECTED__
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sha(p):
 d=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
def main():
 if len(sys.argv)!=2:raise SystemExit("usage: verify_remote_release.py REMOTE_REPO_ROOT")
 root=Path(sys.argv[1]).resolve(strict=True)
 for label,row in EXPECTED.items():
  p=(root/row["relative_parent"]/row["bundle_id"]).resolve(strict=True);m=json.loads((p/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id",None);actual={x.relative_to(p).as_posix():sha(x) for x in sorted(p.rglob("*")) if x.is_file() and x.relative_to(p).as_posix() not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
  if p.name!=row["bundle_id"] or sha(p/"bundle_manifest.json")!=row["manifest_sha256"] or hashlib.sha256(canonical(u)).hexdigest()!=row["bundle_id"] or actual!=m.get("files") or (p/"bundle_manifest.json.sha256").read_text()!=row["manifest_sha256"]+"  bundle_manifest.json\n":raise SystemExit("NOT_READY "+label+" closure differs")
 print(json.dumps({"status":"PASS_EXACT_FIVE_BUNDLE_REMOTE_CLOSURE","remote_execution_or_launch_performed":False},sort_keys=True))
if __name__=="__main__":main()
'''
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)
def build():
 rows={k:closure(k,*v) for k,v in BUNDLES.items()};neg=negative(rows);expected={k:{"relative_parent":v[0],"bundle_id":v[1],"manifest_sha256":v[2]} for k,v in BUNDLES.items()};verifier=VERIFY.replace("__EXPECTED__",repr(expected)).encode()
 prohibited=[v[1] for v in OLD.values()]+[DISCARDED_TARGET[1],DISCARDED_SUPPORT[1]]+list(OLDER_PROHIBITED);checklist={"schema_version":"mysql_tpch_five_bundle_release_preflight_v1","status":"PASS_LOCAL_FULL_FILE_CLOSURE_OLD_CHAIN_REJECTED_NOT_PUBLISHED","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"publication_order":["target","support","materializer","wrapper","formal"],"bundles":rows,"negative_runtime_validation":neg,"old_bundle_ids_prohibited":prohibited,"remote_checklist":["copy b235 target, then a070 support, e421 materializer, 471b wrapper, and 9e45 formal into their exact relative parents and exact 64-hex directories; never overlay an old directory","run verify_remote_release.py with the remote repository root before bootstrap or materialization","confirm all five manifest SHA256 values and every manifest files entry","confirm no symlinks, no writable payloads, no unmanifested payload files","bootstrap a fresh b235 attempt and use only its generated qualification command, dynamically bound blkio supplement, and mysql defaults file","materialize only with e421 using b235+a070 and the exact sealed retry8 failure evidence; never reuse the failed retry8/FF313 command or stale deadbeef container identities","materialize systemd only with 471b using e421+a070, then invoke generated launcher as /bin/sh launch_and_wait.sh or use sealed direct systemd argv","do not execute the immediate old ff313/ed11/eb7/2165/da60 chain, invalid a008 target, discarded db187 support, or older prohibited bundles; do not reuse their output directories","retain failed retry8 as zero-credit; accepted-round credit remains forbidden until the new qualification and formal gates pass"],"remote_publication_performed":False,"workload_execution_performed":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"remote_actions":0};content={"release/mysql_tpch_five_bundle_release_preflight_v1.json":canonical(checklist),"release/verify_remote_release.py":verifier,"builder/build_mysql_tpch_release_preflight_v1.py":HERE.read_bytes()};files={n:sb(v) for n,v in sorted(content.items())};u={"schema_version":"mysql_tpch_release_preflight_bundle_v1","status":"PASS_IMMUTABLE_LOCAL_PREFLIGHT_NOT_PUBLISHED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"verified_bundle_ids":[BUNDLES[k][1] for k in ("target","support","materializer","wrapper","formal")],"old_bundle_ids_prohibited":prohibited,"full_file_closure_verified":True,"negative_runtime_validation_passed":True,"remote_publication_performed":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"remote_actions":0,"files":files};bid=sb(canonical(u));m=dict(u,bundle_id=bid);dst=OUT/bid
 if dst.exists():
  if sf(dst/"bundle_manifest.json")!=sb(canonical(m)):raise RuntimeError("existing differs")
  return dst
 OUT.mkdir(parents=True,exist_ok=True);st=Path(tempfile.mkdtemp(prefix=".release-preflight-",dir=OUT))
 try:
  for n,v in content.items():p=st/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
  raw=canonical(m);(st/"bundle_manifest.json").write_bytes(raw);(st/"bundle_manifest.json.sha256").write_text(sb(raw)+"  bundle_manifest.json\n");freeze(st);os.replace(st,dst)
 finally:
  if st.exists():shutil.rmtree(st)
 return dst
if __name__=="__main__":
 b=build();print(json.dumps({"bundle":str(b),"bundle_id":b.name,"manifest_sha256":sf(b/"bundle_manifest.json"),"remote_actions":0},sort_keys=True))

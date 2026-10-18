#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,os,shutil,stat,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve();OUT=ROOT/"artifacts/mysql_tpcc_joint_successor_v1"
LINEAGE={
 "refresh":("artifacts/mysql_tpch_same_live_bootstrap_refresh_v1/13cce818601022f566e7b9f48f71818cef4ae0be7aa335e46f965aefaf717d37","f5e19cff2c262091a4c94f73bc8f7a64d120d6a0c4d9a731dfc92a34ee5d75cd"),
 "current_args":("artifacts/mysql_tpcc_v16_da455_current_args_successor_v1/82fc38d14bd511df73e44d077779db600c46e048a441cbad9451952ad822eb2c","2c9f4e83e232ddfbdf66c15333891bd81d5d3e11d22c7c2c83091ca33dea0e53"),
 "four_flow":("artifacts/mysql_tpch_tpcc_da455_systemd219_four_flow_v1/aec07a90d60ea0e6bbabe79c531cedac065cdf68679db4f7ee324cc93d282898","8913b2c3d6385b8b1013516a5cce037e69488a4610327252a50f6aa3a813d228")}
def sf(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def canonical(v):return json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False).encode()
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)
def build():
 lin={}
 for name,(rel,digest) in LINEAGE.items():
  p=ROOT/rel;mp=p/"bundle_manifest.json";m=json.loads(mp.read_text())
  if sf(mp)!=digest or m.get("bundle_id")!=p.name:raise RuntimeError("lineage differs: "+name)
  lin[name]={"bundle_id":p.name,"manifest_sha256":digest}
 sources={"adapter/mysql_tpcc_joint_successor_v1.py":ROOT/"shell/mysql_tpcc_joint_successor_v1.py","tests/test_mysql_tpcc_joint_successor_v1.py":ROOT/"tests/test_mysql_tpcc_joint_successor_v1.py","builder/build_mysql_tpcc_joint_successor_v1.py":HERE}
 content={n:p.read_bytes() for n,p in sources.items()};files={n:hashlib.sha256(v).hexdigest() for n,v in sorted(content.items())}
 ident={"schema_version":"mysql_tpcc_joint_successor_bundle_v4","status":"PASS_IMMUTABLE_NESTED_FIXED_BIN_SH_JOINT_SUCCESSOR_NOT_EXECUTED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"lineage":lin,"package_safe_import":True,"exact_ff313_authority_and_sibling_helper_manifest_hash_bound":True,"exact_da455_core_and_three_sibling_manifest_hash_bound":True,"ff313_two_argument_call_shape_required":True,"da455_one_argument_call_shape_required":True,"sys_path_restored_after_import":True,"sys_modules_residue_prohibited":True,"preloaded_absolute_helper_reuse_prohibited":True,"outer_and_inner_fixed_bin_sh_interpreted_launch":True,"launch_chmod_or_copy_prohibited":True,"fresh_refresh_authority_required":True,"same_live_identity_required":True,"authority_ttl_seconds":1800,"minimum_completion_margin_seconds":900,"four_unique_flow_tokens_required":True,"fresh_metric_and_completion_paths_required":True,"cpu_device_isolation_inherited_and_revalidated":True,"completion_receipt_all_sha_reverse_binding":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,"files":files}
 bid=hashlib.sha256(canonical(ident)).hexdigest();m=dict(ident,bundle_id=bid);dest=OUT/bid;OUT.mkdir(parents=True,exist_ok=True)
 if not dest.exists():
  st=Path(tempfile.mkdtemp(prefix=".tpcc-joint-",dir=OUT))
  try:
   for n,v in content.items():p=st/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
   raw=canonical(m);(st/"bundle_manifest.json").write_bytes(raw);(st/"bundle_manifest.json.sha256").write_text(hashlib.sha256(raw).hexdigest()+"  bundle_manifest.json\n");freeze(st);os.replace(st,dest)
  finally:
   if st.exists():shutil.rmtree(st)
 actual={p.relative_to(dest).as_posix():sf(p) for p in dest.rglob("*") if p.is_file() and p.name not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
 if actual!=files or any(stat.S_IMODE(p.stat().st_mode)&0o222 for p in (dest,*dest.rglob("*"))):raise RuntimeError("closure/mode differs")
 print(json.dumps({"bundle_id":bid,"bundle":str(dest),"manifest_sha256":sf(dest/"bundle_manifest.json"),"files":len(files),"remote_actions":0},sort_keys=True));return dest
if __name__=="__main__":build()

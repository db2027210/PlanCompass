#!/usr/bin/env python3
"""Cross-bundle fail-closed adapter for fresh TPC-H/TPC-C four-flow gates."""
from __future__ import annotations
import argparse,hashlib,importlib.machinery,importlib.util,json,os,shlex,stat,sys,time,types
from datetime import datetime,timezone
from pathlib import Path

FF_ID="ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4"
FF_SHA="af1d0facca8b33e2638c0a2af6a751bececc407d483055ca064a34308d27bb52"
FF_AUTH_REL="mysql_tpch_distributed_v2_authority.py"
FF_AUTH_SHA="120a848c63c6aaa5ecce24f55ed33d6b678d7a21e30ed2050b7a2efdd7a7b408"
FF_HELPER_REL="mysql_tpch_client_immutable_identity_v1.py"
FF_HELPER_SHA="8112d805623183b412a779cb9549273b40f7f1f02a1bc85d5d6407d8e02629d3"
REFRESH_ID="13cce818601022f566e7b9f48f71818cef4ae0be7aa335e46f965aefaf717d37"
REFRESH_SHA="f5e19cff2c262091a4c94f73bc8f7a64d120d6a0c4d9a731dfc92a34ee5d75cd"
ARGS_ID="82fc38d14bd511df73e44d077779db600c46e048a441cbad9451952ad822eb2c"
ARGS_SHA="2c9f4e83e232ddfbdf66c15333891bd81d5d3e11d22c7c2c83091ca33dea0e53"
FLOW_ID="aec07a90d60ea0e6bbabe79c531cedac065cdf68679db4f7ee324cc93d282898"
FLOW_SHA="8913b2c3d6385b8b1013516a5cce037e69488a4610327252a50f6aa3a813d228"
COLLECTOR_ID="da455246652a82bb13d63f5f1ba6ebeb2e92fd08389e3f06707e5897e12646b6"
COLLECTOR_SHA="0f835d084459a2c66729327a9a4b9ed6ec070116e9873631874ac667039b34c4"
COLLECTOR_CORE_REL="qualification/mysql_distributed_live_readback_qualification_v1.py"
COLLECTOR_EXACT_FILES={COLLECTOR_CORE_REL:"ae8f22b8c4dab4f4e02e023122c72f21b7ebb244ba3d57e136b91468d7542863","qualification/runtime_tree_content_identity_v3.py":"4d4228a2ec0b415054d4198e9d1acd5332579ddf21112f287e652ab0cb9112bc","qualification/mysql_distributed_workload_metric_adapter_v1.py":"b160009ee3ca1c5ddb80e4a1db185b578035dc0167ca0e0f9921ca104c4126c7","qualification/mysql_tpch_client_immutable_identity_v1.py":"8112d805623183b412a779cb9549273b40f7f1f02a1bc85d5d6407d8e02629d3"}
LABELS=("A_solo","A_paired","B_solo","B_paired")
class Error(RuntimeError):pass
def sha(p):
 d=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
def sealed(p,label):
 p=Path(p).resolve(strict=True);s=Path(str(p)+".sha256")
 if p.is_symlink() or not p.is_file() or not s.is_file() or s.read_text()!=sha(p)+"  "+p.name+"\n":raise Error(label+" seal differs")
 v=json.loads(p.read_text());
 if not isinstance(v,dict):raise Error(label+" object required")
 return p,v
def bundle(p,bid,digest):
 p=Path(p).resolve(strict=True);mp,m=sealed(p/"bundle_manifest.json","bundle manifest")
 if p.name!=bid or m.get("bundle_id")!=bid or sha(mp)!=digest:raise Error("bundle identity differs")
 return p
def import_file(path,name):
 path=Path(path).resolve(strict=True)
 if name in sys.modules:raise Error("transient module name collision")
 before_path=tuple(sys.path);spec=importlib.util.spec_from_file_location(name,path)
 if spec is None or spec.loader is None:raise Error("transient module spec differs")
 mod=importlib.util.module_from_spec(spec);sys.modules[name]=mod
 try:
  spec.loader.exec_module(mod)
  if tuple(sys.path)!=before_path:raise Error("transient import changed sys.path")
  return mod
 finally:
  sys.modules.pop(name,None)
  if tuple(sys.path)!=before_path:sys.path[:]=before_path
def import_ff313_authority(path,name):
 path=Path(path).resolve(strict=True);root=path.parent;mp,m=sealed(root/"bundle_manifest.json","ff313 manifest")
 files=m.get("files") or {};helper=root/FF_HELPER_REL
 if (root.name!=FF_ID or m.get("bundle_id")!=FF_ID or sha(mp)!=FF_SHA or path.name!=FF_AUTH_REL
  or sha(path)!=FF_AUTH_SHA or files.get(FF_AUTH_REL)!=FF_AUTH_SHA or sha(helper)!=FF_HELPER_SHA
  or files.get(FF_HELPER_REL)!=FF_HELPER_SHA):raise Error("ff313 exact sibling closure differs")
 tag=hashlib.sha256(str(root).encode()).hexdigest()[:24];pkg="_ra_ff313_"+tag
 helper_name=pkg+"."+Path(FF_HELPER_REL).stem;authority_name=pkg+"."+Path(FF_AUTH_REL).stem
 keys=(pkg,helper_name,authority_name)
 if any(k in sys.modules for k in keys):raise Error("ff313 private package collision")
 before_path=tuple(sys.path);package=types.ModuleType(pkg);package.__path__=[str(root)];package.__package__=pkg
 package.__spec__=importlib.machinery.ModuleSpec(pkg,loader=None,is_package=True)
 hs=importlib.util.spec_from_file_location(helper_name,helper);aus=importlib.util.spec_from_file_location(authority_name,path)
 if hs is None or hs.loader is None or aus is None or aus.loader is None:raise Error("ff313 private package spec differs")
 hm=importlib.util.module_from_spec(hs);am=importlib.util.module_from_spec(aus)
 sys.modules[pkg]=package;sys.modules[helper_name]=hm;sys.modules[authority_name]=am
 try:
  hs.loader.exec_module(hm);aus.loader.exec_module(am)
  if tuple(sys.path)!=before_path:raise Error("ff313 private import changed sys.path")
  if getattr(am,"client_container_immutable_projection",None) is not getattr(hm,"client_container_immutable_projection",None):raise Error("ff313 authority did not bind exact sibling helper")
  return am
 finally:
  for k in reversed(keys):sys.modules.pop(k,None)
  if tuple(sys.path)!=before_path:sys.path[:]=before_path
def import_da455_collector_core(path):
 path=Path(path).resolve(strict=True);root=path.parents[1];mp,m=sealed(root/"bundle_manifest.json","da455 manifest");files=m.get("files") or {}
 if root.name!=COLLECTOR_ID or m.get("bundle_id")!=COLLECTOR_ID or sha(mp)!=COLLECTOR_SHA or path!=root/COLLECTOR_CORE_REL:raise Error("da455 collector identity/path differs")
 for rel,digest in COLLECTOR_EXACT_FILES.items():
  member=(root/rel).resolve(strict=True)
  if files.get(rel)!=digest or sha(member)!=digest:raise Error("da455 exact import closure differs: "+rel)
 names={"runtime_tree_content_identity_v3":"qualification/runtime_tree_content_identity_v3.py","mysql_distributed_workload_metric_adapter_v1":"qualification/mysql_distributed_workload_metric_adapter_v1.py","mysql_tpch_client_immutable_identity_v1":"qualification/mysql_tpch_client_immutable_identity_v1.py"}
 unique="_ra_da455_core_"+hashlib.sha256(str(root).encode()).hexdigest()[:24];keys=tuple(names)+(unique,);previous={key:sys.modules.get(key) for key in keys};present={key:key in sys.modules for key in keys};before_path=tuple(sys.path);loaded={}
 try:
  for key,rel in names.items():
   sys.modules.pop(key,None);spec=importlib.util.spec_from_file_location(key,root/rel)
   if spec is None or spec.loader is None:raise Error("da455 sibling spec differs: "+key)
   module=importlib.util.module_from_spec(spec);sys.modules[key]=module;spec.loader.exec_module(module);loaded[key]=module
  sys.modules.pop(unique,None);spec=importlib.util.spec_from_file_location(unique,path)
  if spec is None or spec.loader is None:raise Error("da455 core spec differs")
  core=importlib.util.module_from_spec(spec);sys.modules[unique]=core;spec.loader.exec_module(core)
  if (getattr(core,"runtime_tree",None) is not loaded["runtime_tree_content_identity_v3"] or getattr(core,"metric_adapter",None) is not loaded["mysql_distributed_workload_metric_adapter_v1"] or getattr(core,"client_container_immutable_projection",None) is not getattr(loaded["mysql_tpch_client_immutable_identity_v1"],"client_container_immutable_projection",None)):raise Error("da455 core did not bind exact sibling closure")
  return core
 finally:
  sys.path[:]=before_path
  for key in keys:
   if present[key]:sys.modules[key]=previous[key]
   else:sys.modules.pop(key,None)
def import_current_dependency(path,*authority_files):
 path=Path(path).resolve(strict=True)
 if path.name==FF_AUTH_REL:
  if len(authority_files)!=1 or not isinstance(authority_files[0],dict):raise Error("ff313 current loader call shape differs")
  expected=authority_files[0]
  if expected.get(FF_AUTH_REL)!=FF_AUTH_SHA or expected.get(FF_HELPER_REL)!=FF_HELPER_SHA:raise Error("ff313 current loader manifest projection differs")
  return import_ff313_authority(path,"tpch_current_args_authority")
 if path.name==Path(COLLECTOR_CORE_REL).name:
  if authority_files:raise Error("da455 current loader must use the one-argument call shape")
  return import_da455_collector_core(path)
 raise Error("current dependency loader path is not authorized")
def import_current_args(bundle_path):
 path=bundle_path/"materializer/mysql_tpcc_v16_current_args_materializer_v1.py";mp,m=sealed(bundle_path/"bundle_manifest.json","current args manifest")
 rel="materializer/mysql_tpcc_v16_current_args_materializer_v1.py"
 if (m.get("files") or {}).get(rel)!=sha(path):raise Error("current args source outside closure")
 mod=import_file(path,"tpcc_current_fixed_"+sha(path)[:16]);mod.load_module=import_current_dependency
 mod._joint_package_safe_patch_applied=True;return mod
def iso(v):
 try:return datetime.fromisoformat(str(v).replace("Z","+00:00")).astimezone(timezone.utc).timestamp()
 except Exception as e:raise Error("expiry differs") from e
def refresh_closure(specp,evidencep,authorityp,materialp,minimum=900):
 sp,s=sealed(specp,"fresh spec");ep,e=sealed(evidencep,"fresh evidence");ap,a=sealed(authorityp,"refresh authority");mp,m=sealed(materialp,"refresh materialization")
 ident=a.get("same_live_identity")
 if (a.get("schema_version")!="mysql_tpch_same_live_bootstrap_refresh_authority_v1" or a.get("status")!="PASS_FRESH_SPEC_AND_EVIDENCE_VALIDATED_BY_FF313"
  or a.get("target_bundle_id")!=FF_ID or a.get("target_manifest_sha256")!=FF_SHA or a.get("fresh_bootstrap_spec_sha256")!=sha(sp)
  or a.get("fresh_bootstrap_evidence_sha256")!=sha(ep) or a.get("attempt_token")!=s.get("attempt_token") or e.get("attempt_token")!=s.get("attempt_token")
  or e.get("same_live_identity")!=ident or m.get("same_live_identity")!=ident or iso(a.get("expires_at_utc"))-time.time()<minimum
  or m.get("paths",{}).get("bootstrap_spec")!=str(sp) or m.get("paths",{}).get("bootstrap_evidence")!=str(ep)
  or a.get("accepted_round_authorized") is not False):raise Error("fresh refresh closure differs")
 return {"spec":sp,"evidence":ep,"authority":ap,"materialization":mp,"attempt_token":s["attempt_token"],"identity":ident}
def refresh(a):
 rb=bundle(a.refresh_bundle,REFRESH_ID,REFRESH_SHA);mod=import_file(rb/"materializer/mysql_tpch_same_live_bootstrap_refresh_v1.py","tpch_refresh_fixed")
 mod.import_file=import_ff313_authority;old=list(sys.argv)
 sys.argv=[str(rb/"materializer/mysql_tpch_same_live_bootstrap_refresh_v1.py"),"--bundle",str(a.tpch_bundle),"--support-bundle",str(a.collector_bundle),"--old-bootstrap-spec",str(a.old_bootstrap_spec),"--old-bootstrap-evidence",str(a.old_bootstrap_evidence),"--mysql-defaults-file",str(a.mysql_defaults_file),"--output-dir",str(a.output_dir),"--valid-seconds","1800"]
 try:return mod.main()
 finally:sys.argv[:]=old
def current(a):
 c=refresh_closure(a.tpch_bootstrap_spec,a.tpch_bootstrap_evidence,a.tpch_refresh_authority,a.tpch_refresh_materialization)
 ab=bundle(a.current_args_bundle,ARGS_ID,ARGS_SHA);mod=import_current_args(ab)
 mod.TPCH_ATTEMPT=c["attempt_token"]
 forwarded=[]
 for name in ("a336_snapshot","tpcc_adopted_service","tpch_bootstrap_spec","tpch_bootstrap_evidence","tpch_bundle","tpcc_bundle","collector_bundle","tpcc_defaults_file","a_solo_token_file","a_paired_token_file","b_solo_token_file","b_paired_token_file","output_root"):
  forwarded += ["--"+name.replace("_","-"),str(getattr(a,name))]
 forwarded += ["--valid-seconds","1800"]
 rc=mod.main(forwarded)
 rp,r=sealed(a.output_root/"materialization_receipt.json","current receipt")
 binding={"schema_version":"mysql_tpcc_joint_current_args_binding_v1","status":"PASS_FRESH_REFRESH_BOUND_CURRENT_ARGS_ZERO_CREDIT","current_args_receipt_sha256":sha(rp),"refresh":{k+"_sha256":sha(v) for k,v in c.items() if isinstance(v,Path)},"attempt_token":c["attempt_token"],"same_live_identity":c["identity"],"ttl_seconds":1800,"accepted_round_authorized":False,"accepted_rounds_credit":0}
 mod.write_sealed(a.output_root/"joint_current_args_binding.json",binding,0o400);return rc
def fourflow(a):
 c=refresh_closure(a.tpch_bootstrap_spec,a.tpch_bootstrap_evidence,a.tpch_refresh_authority,a.tpch_refresh_materialization)
 jp,j=sealed(a.joint_current_args_binding,"joint current binding");cp,cv=sealed(a.current_args,"current args");rp,rv=sealed(a.current_args_receipt,"current receipt")
 tokens=[];metrics=[];completions=[]
 for label in LABELS:
  argv=cv.get("qualification_args",{}).get(label,[])
  def val(opt):
   i=argv.index(opt);return argv[i+1]
  tp=Path(val("--main-agent-token-file")).resolve(strict=True);cmdp,cmd=sealed(val("--command-spec"),label+" command")
  metric=Path((cmd.get("workload_metric_stream") or {}).get("path") or "");completion=Path(str(metric)+".producer-complete-v1.json")
  if cmd.get("qualification_attempt_id") is None or label.split("_")[1] not in str(cmd.get("mode")) or cmd.get("accepted_round_authorized") is not False:raise Error("flow label/attempt differs")
  tokens.append((label,str(tp),sha(tp)));metrics.append(str(metric));completions.append(str(completion))
 if len({(x[1],x[2]) for x in tokens})!=4 or len(set(metrics))!=4 or any(Path(x).exists() for x in metrics+completions):raise Error("flow token/output freshness differs")
 if (j.get("current_args_receipt_sha256")!=sha(rp) or j.get("attempt_token")!=c["attempt_token"] or rv.get("ttl_seconds")!=1800):raise Error("current binding differs")
 fb=bundle(a.flow_bundle,FLOW_ID,FLOW_SHA);flow=import_file(fb/"controller/mysql_tpch_tpcc_da455_systemd219_four_flow_v1.py","fourflow_inner")
 ns=argparse.Namespace(current_args_bundle=a.current_args_bundle,current_args=cp,current_args_receipt=rp,collector_bundle=a.collector_bundle,gate_bundle=a.gate_bundle,bootstrap=c["evidence"],run_root=a.run_root,output_dir=a.output_dir)
 rc=flow.materialize(ns);pp,p=sealed(a.output_dir/"four_flow.plan.json","flow plan")
 binding={"schema_version":"mysql_tpcc_joint_four_flow_binding_v1","status":"SEALED_ALL_INPUTS_FRESH_NOT_EXECUTED","plan_sha256":sha(pp),"current_args_sha256":sha(cp),"current_args_receipt_sha256":sha(rp),"joint_current_binding_sha256":sha(jp),"bootstrap":{k+"_sha256":sha(v) for k,v in c.items() if isinstance(v,Path)},"flow_tokens":{x[0]:{"path":x[1],"sha256":x[2]} for x in tokens},"metric_paths":dict(zip(LABELS,metrics)),"completion_paths":dict(zip(LABELS,completions)),"same_live_identity":c["identity"],"accepted_rounds_credit":0}
 flow.seal(a.output_dir/"joint_four_flow_binding.json",binding,0o400)
 outer=a.output_dir/"launch_and_wait_joint.sh";inner=a.output_dir/"launch_and_wait.sh";final=a.run_root/"completion/joint_four_flow.completion.json"
 text="#!/bin/sh\nset -eu\n/bin/sh "+shlex.quote(str(inner))+"\nexec "+shlex.join([sys.executable,str(Path(__file__).resolve()),"finalize","--binding",str(a.output_dir/"joint_four_flow_binding.json"),"--inner-receipt",str(a.run_root/"completion/four_flow.completion.json"),"--output",str(final)])+"\n"
 flow.write(outer,text.encode(),0o500);return rc
def finalize(a):
 bp,b=sealed(a.binding,"joint binding");ip,i=sealed(a.inner_receipt,"inner completion")
 if i.get("status")!="PASS_FOUR_FLOWS_FRESH_GATE_ZERO_CREDIT":raise Error("inner completion differs")
 flow_outputs={}
 for label in LABELS:
  metric=Path(b["metric_paths"][label]).resolve(strict=True);completion,unused=sealed(b["completion_paths"][label],label+" producer completion")
  if metric.is_symlink() or not metric.is_file():raise Error(label+" metric output differs")
  flow_outputs[label]={"metric_sha256":sha(metric),"completion_sha256":sha(completion)}
 out={"schema_version":"mysql_tpcc_joint_four_flow_completion_v1","status":"PASS_ALL_INPUTS_AND_FOUR_FLOWS_BOUND_ZERO_CREDIT","joint_binding_sha256":sha(bp),"inner_completion_sha256":sha(ip),"inner_evidence_sha256":i.get("evidence_sha256"),"inner_dynamic_gate_sha256":i.get("dynamic_gate_sha256"),"flow_output_sha256":flow_outputs,"all_input_sha256":b,"formal_scope":"mysql_only","postgresql_mutation_prohibited":True,"accepted_round_authorized":False,"accepted_rounds_credit":0}
 raw=(json.dumps(out,sort_keys=True,indent=2)+"\n").encode();a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_bytes(raw);Path(str(a.output)+".sha256").write_text(hashlib.sha256(raw).hexdigest()+"  "+a.output.name+"\n");return 0
def main():
 p=argparse.ArgumentParser();sp=p.add_subparsers(dest="mode",required=True)
 r=sp.add_parser("refresh")
 for n in ("refresh-bundle","tpch-bundle","collector-bundle","old-bootstrap-spec","old-bootstrap-evidence","mysql-defaults-file","output-dir"):r.add_argument("--"+n,type=Path,required=True)
 c=sp.add_parser("current")
 for n in ("current-args-bundle","tpch-refresh-authority","tpch-refresh-materialization","a336-snapshot","tpcc-adopted-service","tpch-bootstrap-spec","tpch-bootstrap-evidence","tpch-bundle","tpcc-bundle","collector-bundle","tpcc-defaults-file","a-solo-token-file","a-paired-token-file","b-solo-token-file","b-paired-token-file","output-root"):c.add_argument("--"+n,type=Path,required=True)
 f=sp.add_parser("fourflow")
 for n in ("flow-bundle","current-args-bundle","current-args","current-args-receipt","joint-current-args-binding","tpch-bootstrap-spec","tpch-bootstrap-evidence","tpch-refresh-authority","tpch-refresh-materialization","collector-bundle","gate-bundle","run-root","output-dir"):f.add_argument("--"+n,type=Path,required=True)
 x=sp.add_parser("finalize");x.add_argument("--binding",type=Path,required=True);x.add_argument("--inner-receipt",type=Path,required=True);x.add_argument("--output",type=Path,required=True)
 a=p.parse_args();return refresh(a) if a.mode=="refresh" else current(a) if a.mode=="current" else fourflow(a) if a.mode=="fourflow" else finalize(a)
if __name__=="__main__":
 try:raise SystemExit(main())
 except (Error,OSError,ValueError,KeyError) as e:raise SystemExit("NOT_READY: "+str(e))

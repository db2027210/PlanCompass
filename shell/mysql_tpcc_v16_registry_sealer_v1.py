#!/usr/bin/env python3
"""Seal a v16 typed trace registry only after a336 post-capture authority."""
from __future__ import annotations

import argparse, hashlib, importlib.util, json, os, secrets, shutil, stat, sys
from pathlib import Path
from typing import Any, Mapping

V16_ID="7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77"
V16_SHA="e9d5e2213a14d2289c07063c0b24dfb68d6059f068c5c91388862f8a873b4cba"
GATE_ID="a336aef82ec2a8098840ff10c6046a18eca73480c5905a96373d265caaa5f723"
GATE_SHA="a7e7717b9e7ddec7a6f11a4e6c97b68116d60d17e767aeb469ed6fc46219e6e0"
COLLECTOR_ID="1878a2ca8b68e1b3917021499cda396e23f60a23baacd299496c78346e29c86c"
COLLECTOR_SHA="f95c21304314474eaca1930fc3fd14931e3034f2c0ecd875813fa53b7d227994"
BINDING_SHA="689d4bc1558d14042c3e43a2267d3cc332d71ba567c635d92180a189c3f90182"
ATTEMPT="tpcc-v16-7e2e13aa-20260801t121500p08"
TX={"Delivery","NewOrder","OrderStatus","Payment","StockLevel"}

class SealError(RuntimeError): pass
def canonical(v:Any)->bytes:return (json.dumps(v,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
def sha(p:Path)->str:
 h=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):h.update(b)
 return h.hexdigest()
def regular(p:Path,label:str)->Path:
 p=Path(p).resolve(strict=True)
 if p.is_symlink() or not p.is_file():raise SealError(label+" regular file required")
 return p
def sealed(p:Path,label:str)->tuple[Path,dict[str,Any]]:
 p=regular(p,label);s=regular(Path(str(p)+".sha256"),label+" sidecar")
 if s.read_text(encoding="ascii")!=f"{sha(p)}  {p.name}\n":raise SealError(label+" seal differs")
 v=json.loads(p.read_text(encoding="utf-8"))
 if not isinstance(v,dict):raise SealError(label+" object required")
 return p,v
def bundle(root:Path,identity:str,digest:str,label:str)->dict[str,Any]:
 root=Path(root).resolve(strict=True);p,v=sealed(root/"bundle_manifest.json",label);u=dict(v);u.pop("bundle_id",None)
 if root.name!=identity or v.get("bundle_id")!=identity or sha(p)!=digest or hashlib.sha256(canonical(u)).hexdigest()!=identity:raise SealError(label+" identity differs")
 if v.get("formal_scope")!="mysql_only" or v.get("cross_engine_comparison_prohibited") is not True or v.get("postgresql_mutation_prohibited") is not True:raise SealError(label+" scope differs")
 return v
def reject_qps(v:Any,where="evidence"):
 if isinstance(v,Mapping):
  for k,x in v.items():
   if str(k).lower() in {"qps","throughput","transactions_per_second"}:raise SealError("QPS prohibited: "+where)
   reject_qps(x,where+"."+str(k))
 elif isinstance(v,list):
  for i,x in enumerate(v):reject_qps(x,f"{where}[{i}]")
def import_collector(path:Path):
 spec=importlib.util.spec_from_file_location("tpcc_portable_1878",path);mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod);return mod

def validate(args:argparse.Namespace)->tuple[dict[str,bytes],dict[str,Any]]:
 vm=bundle(args.v16_bundle,V16_ID,V16_SHA,"v16 bundle");bundle(args.gate_bundle,GATE_ID,GATE_SHA,"a336 bundle")
 base=Path(args.v16_bundle).resolve(); cdir=base/"authority"/("collector_payload_"+COLLECTOR_ID)
 collector=regular(cdir/"tpcc_typed_collector.py","portable collector");binding=regular(cdir/"tpcc_offline_binding_manifest.json","binding manifest")
 if vm.get("portable_collector_payload_id")!=COLLECTOR_ID or sha(collector)!=COLLECTOR_SHA or sha(binding)!=BINDING_SHA:raise SealError("portable collector closure differs")
 pp,pre=sealed(args.precapture_authority,"pre-capture authority");op,post=sealed(args.postcapture_authority,"post-capture authority")
 psp,pre_snapshot=sealed(args.precapture_snapshot,"pre-capture same-live snapshot");sp,snapshot=sealed(args.postcapture_snapshot,"post-capture same-live snapshot");cmdp,command=sealed(args.typed_command,"typed command");trace=regular(args.trace,"production typed trace")
 common=lambda g: g.get("schema_version")=="mysql_tpcc_v16_same_live_typed_gate_authority_v1" and g.get("bundle_id")==V16_ID and g.get("attempt_id")==ATTEMPT and g.get("formal_scope")=="mysql_only" and g.get("postgresql_mutation_prohibited") is True and g.get("accepted_round_authorized") is False and g.get("accepted_rounds_credit")==0 and g.get("algorithm_c_unchanged") is True
 if (not common(pre) or pre.get("status")!="PASS_SAME_LIVE_TYPED_CAPTURE_READY_ZERO_CREDIT" or pre.get("capture_authorized") is not True or pre.get("registry_ready") is not False
  or not common(post) or post.get("status")!="PASS_SAME_LIVE_REGISTRY_READY_ZERO_CREDIT" or post.get("capture_authorized") is not False or post.get("registry_ready") is not True
  or pre.get("same_live_identity")!=post.get("same_live_identity")
  or (pre.get("inputs") or {}).get("same_live_snapshot_sha256")!=sha(psp) or (post.get("inputs") or {}).get("same_live_snapshot_sha256")!=sha(sp)
  or any((pre.get("inputs") or {}).get(k)!=(post.get("inputs") or {}).get(k) for k in ("base_manifest_sha256","binding_manifest_sha256","adopted_service_sha256","phase_ledger_sha256","dynamic_gate_sha256","dynamic_gate_raw_sha256","flows"))):
  raise SealError("a336 pre/post capture transition differs")
 bind=command.get("capture_authority_binding") or {};unsigned=dict(command);spec_sha=unsigned.pop("command_spec_sha256",None)
 if (command.get("phase")!="typed_capture" or command.get("bundle_id")!=V16_ID or command.get("attempt_id")!=ATTEMPT or command.get("capture_only") is not True
  or command.get("accepted_round_authorized") is not False or command.get("qps_resource_input_prohibited") is not True or spec_sha!=hashlib.sha256(canonical(unsigned)).hexdigest()
  or bind.get("schema_version")!="mysql_tpcc_v16_capture_authority_binding_v1" or bind.get("capture_gate_authority_sha256")!=sha(pp)
  or bind.get("same_live_snapshot_sha256")!=sha(psp) or bind.get("v16_manifest_sha256")!=V16_SHA or bind.get("a336_manifest_sha256")!=GATE_SHA):
  raise SealError("typed command lacks capture-authority adapter binding")
 mod=import_collector(collector); result=mod.collect_and_validate(binding,trace)
 validation=regular(Path(args.collector_output)/"tpcc_agent_validation.json","collector validation");l2=regular(Path(args.collector_output)/"tpcc_agent_l2_transactions.json","collector L2");l3=regular(Path(args.collector_output)/"tpcc_agent_l3_vectors.json","collector L3")
 observed=json.loads(validation.read_text()); observed_l2=json.loads(l2.read_text()); observed_l3=json.loads(l3.read_text())
 if observed!=result or observed_l2!=result.get("l2_transactions") or observed_l3.get("vectors")!=result.get("l3_vectors"):raise SealError("collector outputs are not reproducible")
 if (result.get("manifest_sha256")!=BINDING_SHA or result.get("trace_sha256")!=sha(trace) or result.get("binding_count")!=34 or result.get("observed_binding_count")!=34
  or set(result.get("transaction_types") or [])!=TX or set(result.get("l3_vectors") or {})!=TX or result.get("qps_included") is not False or result.get("correctness_verified") is not True
  or result.get("capture_workload_authorized") is not True or result.get("outer_execution_authorized") is not False or post.get("collector_validation_sha256")!=sha(validation)):
  raise SealError("typed 34/34 five-transaction L2/L3 authority differs")
 sensitive={s.get("binding_id") for tx in result.get("l2_transactions",[]) for s in tx.get("statements",[])}
 if "34:SensitiveStockLevel:SensitiveStockLevel/item-in-orderline-w1" not in sensitive:raise SealError("SensitiveStockLevel normalization evidence absent")
 for value in (pre,post,pre_snapshot,snapshot,command,result,observed_l3):reject_qps(value)
 sources={"trace/typed_trace.jsonl":trace.read_bytes(),"authority/precapture_gate.json":pp.read_bytes(),"authority/postcapture_gate.json":op.read_bytes(),
  "authority/precapture_same_live_snapshot.json":psp.read_bytes(),"authority/postcapture_same_live_snapshot.json":sp.read_bytes(),"authority/typed_command.json":cmdp.read_bytes(),"collector/collector_validation.json":validation.read_bytes(),
  "collector/l2_transactions.json":l2.read_bytes(),"collector/l3_vectors.json":l3.read_bytes(),"collector/tpcc_typed_collector.py":collector.read_bytes(),"collector/typed_binding_manifest.json":binding.read_bytes()}
 meta={"trace_sha256":sha(trace),"precapture_authority_sha256":sha(pp),"postcapture_authority_sha256":sha(op),"precapture_snapshot_sha256":sha(psp),"postcapture_snapshot_sha256":sha(sp),"typed_command_sha256":sha(cmdp),"collector_validation_sha256":sha(validation)}
 return sources,meta

def publish(args:argparse.Namespace)->dict[str,Any]:
 payload,meta=validate(args);files={k:hashlib.sha256(v).hexdigest() for k,v in sorted(payload.items())}
 manifest={"schema_version":"mysql_tpcc_v16_typed_trace_registry_bundle_v1","status":"PASS_REAL_PRODUCTION_TYPED_L2_L3_REGISTRY_ZERO_CREDIT","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"immutable":True,"source_v16_bundle_id":V16_ID,"source_v16_manifest_sha256":V16_SHA,"a336_bundle_id":GATE_ID,"a336_manifest_sha256":GATE_SHA,"portable_collector_payload_id":COLLECTOR_ID,"portable_collector_sha256":COLLECTOR_SHA,"binding_manifest_sha256":BINDING_SHA,"binding_count":34,"observed_binding_count":34,"transaction_types":sorted(TX),"sensitive_stock_level_normalized_to":"StockLevel","setter_type_parameter_position_sql_hash_verified":True,"typed_transaction_l2_l3_complete":True,"qps_resource_input_prohibited":True,**meta,"capture_only":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"files":files}
 identity=hashlib.sha256(canonical(manifest)).hexdigest();manifest["bundle_id"]=identity;root=Path(args.output_root).resolve();root.mkdir(parents=True,exist_ok=True);target=root/identity
 if target.exists():
  if json.loads((target/"bundle_manifest.json").read_text())!=manifest:raise SealError("existing registry identity collision")
  return {"status":"PASS_IDEMPOTENT","bundle":str(target),"bundle_id":identity,"accepted_rounds_credit":0}
 stage=root/(".stage-"+identity+"-"+secrets.token_hex(6));stage.mkdir(mode=0o700)
 try:
  for name,data in payload.items():p=stage/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);os.chmod(p,0o400)
  raw=canonical(manifest);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(hashlib.sha256(raw).hexdigest()+"  bundle_manifest.json\n",encoding="ascii")
  for p in sorted(stage.rglob("*"),reverse=True):os.chmod(p,0o444 if p.is_file() else 0o555)
  os.chmod(stage,0o555);os.rename(stage,target)
 except BaseException:
  if stage.exists():shutil.rmtree(stage)
  raise
 return {"status":manifest["status"],"bundle":str(target),"bundle_id":identity,"accepted_rounds_credit":0}

def cli()->int:
 p=argparse.ArgumentParser(description=__doc__)
 for n in ("v16_bundle","gate_bundle","precapture_authority","postcapture_authority","precapture_snapshot","postcapture_snapshot","typed_command","trace","collector_output","output_root"):p.add_argument("--"+n.replace("_","-"),type=Path,required=True)
 print(json.dumps(publish(p.parse_args()),sort_keys=True));return 0
if __name__=="__main__":
 try:raise SystemExit(cli())
 except (SealError,OSError,ValueError,KeyError) as e:sys.stderr.write("NOT_READY: %s\n"%e);raise SystemExit(2)

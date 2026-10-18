#!/usr/bin/env python3
"""Derive only hash-verifiable TPC-C v13 post-capture evidence, fail closed.

It never runs a workload.  In particular it never synthesizes capture-result,
completion, provenance, resource readback, runtime PID evidence, or a dynamic
gate; those are named explicitly as non-derivable evidence.
"""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, os, secrets, shutil, sys
from pathlib import Path
from typing import Any

V13="4e87faac238a2ecd1523f9dfc8044593243f732b3f1818a1f4f6fda560d0c8ab"
V13SHA="2172eae52c4736ba1afbf4a1342f8e02d20db6fa771828839db14cc9e5a17e90"
MANIFEST="689d4bc1558d14042c3e43a2267d3cc332d71ba567c635d92180a189c3f90182"

class Error(RuntimeError): pass
def sha(p: Path) -> str:
 h=hashlib.sha256()
 with p.open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""): h.update(b)
 return h.hexdigest()
def canon(v: Any)->bytes:return (json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)+"\n").encode()
def regular(p: Path, what: str)->Path:
 try:p=Path(p).resolve(strict=True)
 except OSError as e:raise Error(what+" absent") from e
 if p.is_symlink() or not p.is_file():raise Error(what+" must be regular")
 return p
def obj(p:Path,what:str)->dict[str,Any]:
 try:v=json.loads(regular(p,what).read_text())
 except (UnicodeError,json.JSONDecodeError) as e:raise Error(what+" JSON differs") from e
 if not isinstance(v,dict):raise Error(what+" object required")
 return v
def sidecar(p:Path,what:str)->None:
 s=regular(Path(str(p)+".sha256"),what+" sidecar")
 if s.read_text(encoding="ascii")!=sha(p)+"  "+p.name+"\n":raise Error(what+" sidecar differs")
def collector(bundle:Path):
 source=bundle/"authority/collector_payload_032eb2c9f4ba01a7edf8a438cdadc7fb115243246ba34923dbb18386802099bd/tpcc_typed_collector.py"
 spec=importlib.util.spec_from_file_location("sealed_collector",source)
 if spec is None or spec.loader is None:raise Error("sealed collector import differs")
 m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def main()->int:
 p=argparse.ArgumentParser(description=__doc__)
 for x in ("--v13-bundle","--adopted-service","--prefault","--qualification-command","--qualification-receipt","--qualification-stdout","--qualification-stderr","--same-live-handoff","--typed-command","--typed-receipt","--typed-stdout","--typed-stderr","--trace","--trace-lock","--performance-authority","--sensitive-authority","--sensitive-events","--collector-output","--output-root"):p.add_argument(x,type=Path,required=True)
 a=p.parse_args();b=a.v13_bundle.resolve(strict=True);mp=b/"bundle_manifest.json"
 if b.name!=V13 or sha(regular(mp,"v13 manifest"))!=V13SHA:raise Error("v13 bundle differs")
 sidecar(mp,"v13 manifest"); service=obj(a.adopted_service,"adopted service");sidecar(a.adopted_service,"adopted service");prefault=obj(a.prefault,"prefault");sidecar(a.prefault,"prefault");qcmd=obj(a.qualification_command,"qualification command");sidecar(a.qualification_command,"qualification command");qreceipt=obj(a.qualification_receipt,"qualification receipt");sidecar(a.qualification_receipt,"qualification receipt");qstdout=regular(a.qualification_stdout,"qualification stdout");qstderr=regular(a.qualification_stderr,"qualification stderr");sidecar(qstdout,"qualification stdout");sidecar(qstderr,"qualification stderr");handoff=obj(a.same_live_handoff,"same live handoff");sidecar(a.same_live_handoff,"same live handoff");cmd=obj(a.typed_command,"typed command");sidecar(a.typed_command,"typed command")
 receipt=obj(a.typed_receipt,"typed receipt");sidecar(a.typed_receipt,"typed receipt")
 stdout=regular(a.typed_stdout,"typed stdout");stderr=regular(a.typed_stderr,"typed stderr");sidecar(stdout,"typed stdout");sidecar(stderr,"typed stderr")
 token=str(cmd.get("attempt_id") or "");env=cmd.get("environment") or {}
 if cmd.get("phase")!="typed_capture" or cmd.get("capture_only") is not True or not token or env.get("MYSQL_TPCC_TYPED_TRACE_ROLE")!="OWNER" or env.get("MYSQL_TPCC_TYPED_CAPTURE_TOKEN")!=token or env.get("MYSQL_TPCC_TYPED_MANIFEST_SHA256")!=MANIFEST:raise Error("typed command authority differs")
 if receipt.get("status")!="PASS_TERMINAL_GATE" or receipt.get("phase")!="typed_capture" or receipt.get("attempt_id")!=token or receipt.get("command_spec_sha256")!=cmd.get("command_spec_sha256") or receipt.get("returncode")!=0 or receipt.get("stdout_sha256")!=sha(stdout) or receipt.get("stderr_sha256")!=sha(stderr):raise Error("typed receipt/stdout/stderr differs")
 if service.get("bundle_id")!=V13 or service.get("attempt_id")!=token or not isinstance(service.get("database"),dict) or not isinstance(service.get("client"),dict) or prefault.get("attempt_id")!=token or qcmd.get("phase")!="qualification" or qcmd.get("attempt_id")!=token or qreceipt.get("status")!="PASS_TERMINAL_GATE" or qreceipt.get("phase")!="qualification" or qreceipt.get("attempt_id")!=token or qreceipt.get("command_spec_sha256")!=qcmd.get("command_spec_sha256") or qreceipt.get("returncode")!=0 or qreceipt.get("stdout_sha256")!=sha(qstdout) or qreceipt.get("stderr_sha256")!=sha(qstderr) or handoff.get("attempt_id")!=token or handoff.get("bundle_id")!=V13:raise Error("same-live qualification prefix differs")
 trace=regular(a.trace,"trace"); lock=regular(a.trace_lock,"trace lock")
 if lock!=Path(str(trace)+".lock").resolve(strict=True):raise Error("trace lock path differs")
 header=json.loads(trace.read_text().splitlines()[0])
 if header.get("record_type")!="header" or header.get("schema_version")!="mysql_tpcc_jdbc_typed_trace_v1" or header.get("capture_token")!=token or header.get("manifest_sha256")!=MANIFEST or header.get("capture_only") is not True or header.get("outer_execution_authorized") is not False or header.get("correctness_verified") is not True or header.get("qps_included") is not False:raise Error("trace header differs")
 perf=obj(a.performance_authority,"performance authority");join=obj(a.sensitive_authority,"sensitive authority");events=regular(a.sensitive_events,"sensitive events")
 if perf.get("status")!="PASS_ENABLED_BEFORE_WORKLOAD_START" or join.get("status")!="PASS_REAL_OWNER_TRACE_JOIN" or join.get("trace_header")!=header:raise Error("typed owner authorities differ")
 out=Path(a.collector_output).resolve(strict=True);m=collector(b);rebuilt=m.collect_and_validate(b/"predecessor/config/tpcc_offline_binding_manifest.json",trace)
 for n in ("tpcc_agent_validation.json","tpcc_agent_l2_transactions.json","tpcc_agent_l3_vectors.json"):regular(out/n,"collector output "+n)
 observed=obj(out/"tpcc_agent_validation.json","collector validation")
 if observed!=rebuilt or rebuilt.get("binding_count")!=34 or rebuilt.get("observed_binding_count")!=34 or rebuilt.get("qps_included") is not False:raise Error("collector is not reproducible")
 root=a.output_root.resolve();root.mkdir(parents=True,exist_ok=True)
 inputs={"adopted_service":sha(a.adopted_service),"prefault":sha(a.prefault),"qualification_command":sha(a.qualification_command),"qualification_receipt":sha(a.qualification_receipt),"qualification_stdout":sha(qstdout),"qualification_stderr":sha(qstderr),"same_live_handoff":sha(a.same_live_handoff),"typed_command":sha(a.typed_command),"typed_receipt":sha(a.typed_receipt),"typed_stdout":sha(stdout),"typed_stderr":sha(stderr),"trace":sha(trace),"trace_lock":sha(lock),"performance_authority":sha(a.performance_authority),"sensitive_authority":sha(a.sensitive_authority),"sensitive_events":sha(events),"collector_validation":sha(out/"tpcc_agent_validation.json"),"collector_l2":sha(out/"tpcc_agent_l2_transactions.json"),"collector_l3":sha(out/"tpcc_agent_l3_vectors.json")}
 coverage={"schema_version":"mysql_tpcc_typed_capture_coverage_postcapture_derivation_v1","attempt_id":token,"capture_only":True,"qps_resource_input_prohibited":True,"derived_from_trace_sha256":sha(trace),"required_binding_count":34,"observed_binding_count":34,"complete":True}
 material={"schema_version":"mysql_tpcc_v13_postcapture_evidence_materialization_v1","status":"PASS_PARTIAL_DERIVATION_NOT_CAPTURE_AUTHORITY","formal_scope":"mysql_only","attempt_id":token,"v13_bundle_id":V13,"inputs":inputs,"derived":{"trace_sha256":sha(trace),"coverage":coverage},"not_derived":["typed_capture_result","typed_capture_completion","production_trace_provenance","db_live_readback","client_live_readback","resource_readback","effective_resource_readback","runtime_process_gate","workload_process_evidence","distributed_dynamic_gate","distributed_dynamic_gate_raw_four_inputs"],"formal_launch_authorized":False,"accepted_round_authorized":False}
 ident=hashlib.sha256(canon(material)).hexdigest();target=root/ident
 if target.exists():
  if obj(target/"materialization.json","existing")!=dict(material,materialization_id=ident):raise Error("existing materialization differs")
  print(json.dumps({"status":"PASS_IDEMPOTENT_PARTIAL_DERIVATION","materialization":str(target)},sort_keys=True));return 0
 stage=root/(".stage-"+ident+"-"+secrets.token_hex(6));stage.mkdir(mode=0o700)
 try:
  (stage/"trace.jsonl.sha256").write_text(sha(trace)+"  trace.jsonl\n",encoding="ascii")
  (stage/"typed_capture_coverage.json").write_bytes(canon(coverage));material["materialization_id"]=ident;(stage/"materialization.json").write_bytes(canon(material));os.rename(stage,target)
 except BaseException:
  if stage.exists():shutil.rmtree(stage)
  raise
 print(json.dumps({"status":material["status"],"materialization":str(target),"materialization_id":ident,"accepted_round_authorized":False},sort_keys=True));return 0
if __name__=="__main__":
 try:raise SystemExit(main())
 except (Error,OSError,ValueError,KeyError) as e:sys.stderr.write("NOT_READY: %s\n"%e);raise SystemExit(2)

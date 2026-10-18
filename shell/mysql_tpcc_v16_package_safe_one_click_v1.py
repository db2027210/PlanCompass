#!/usr/bin/env python3
"""Run the node04 TPC-C zero-credit refresh through four-flow completion.

This host-local controller contains no SSH or PostgreSQL mutation path.  It
does not use jq.  Every executable is reached through an exact, sealed bundle
path and is invoked in a child interpreter instead of imported into this
process.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shlex
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any

TPCH_ROOT=Path("/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729")
TPCC_ROOT=Path("/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729")
TPCC_ATTEMPT="tpcc-v16-7e2e13aa-20260801t121500p08"
CUR=TPCC_ROOT/TPCC_ATTEMPT
PYTHON=TPCH_ROOT/"runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89/bin/python3.11"
HEX64=re.compile(r"^[0-9a-f]{64}$")
BUNDLES={
 "tpch_ff":("b235d1ca300e5ad02fe77005096fc511bfbdc5d91b9601233a66a10502be5a47","386f59b23f20eb7be59665c42c44fe2453512bf2112d850e12df6b398a16f439"),
 "collector":("da455246652a82bb13d63f5f1ba6ebeb2e92fd08389e3f06707e5897e12646b6","0f835d084459a2c66729327a9a4b9ed6ec070116e9873631874ac667039b34c4"),
 "current":("8007386a577296be2b9510c7b512399137e2131ad16c1b7f427967062844f888","d75b93ccf75d2c5da11aee7a9550d77cb1c1dffe11b5aa89bbd26cb68e31e713"),
 "a336":("a336aef82ec2a8098840ff10c6046a18eca73480c5905a96373d265caaa5f723","a7e7717b9e7ddec7a6f11a4e6c97b68116d60d17e767aeb469ed6fc46219e6e0"),
 "tpcc_v16":("7e2e13aa76c0d2e2990ff098dcdcee9b09fdbb98a60abbf4fc08d52ea4fd6c77","e9d5e2213a14d2289c07063c0b24dfb68d6059f068c5c91388862f8a873b4cba"),
 "local_flow":("f04674db368b743d175e8e0c5f781dd659ec4b7b01ebb50e8381a20671310ddb","7ae7204536f685eb5ba45f54b3a4bfd34ef477b19aea1713a2221e31e9286231"),
 "blkio_sealer":("a75e23ef75351a03266b38790ec2c6d745ea40e4eab3742dd281b0a55c543c7b","bfdc060610122778896f8debcdbe031395f02e38b8ba012a0b5484a40e782d65"),
}

class NotReady(RuntimeError): pass

def sha(path:Path)->str:
 h=hashlib.sha256()
 with Path(path).open("rb") as stream:
  for block in iter(lambda:stream.read(1048576),b""):h.update(block)
 return h.hexdigest()

def canonical(value:Any)->bytes:
 return (json.dumps(value,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()

def regular(path:Path,label:str)->Path:
 path=Path(path)
 try: state=path.lstat();resolved=path.resolve(strict=True)
 except OSError as exc: raise NotReady(label+" absent") from exc
 if stat.S_ISLNK(state.st_mode) or not resolved.is_file():raise NotReady(label+" not regular")
 return resolved

def sealed(path:Path,label:str)->tuple[Path,dict[str,Any]]:
 path=regular(path,label);side=regular(Path(str(path)+".sha256"),label+" sidecar")
 if side.read_text(encoding="ascii")!=sha(path)+"  "+path.name+"\n":raise NotReady(label+" seal differs")
 try:value=json.loads(path.read_text(encoding="utf-8"))
 except (UnicodeError,json.JSONDecodeError) as exc:raise NotReady(label+" JSON differs") from exc
 if not isinstance(value,dict):raise NotReady(label+" object required")
 return path,value

def resolve_bundle(label:str)->tuple[Path,dict[str,Any]]:
 identity,manifest_sha=BUNDLES[label];matches=[]
 for base in (TPCH_ROOT/"bundles",TPCH_ROOT/"support_bundles",TPCC_ROOT/"bundles",TPCC_ROOT/"support_bundles"):
  candidate=base/identity
  if candidate.is_dir() and not candidate.is_symlink():matches.append(candidate.resolve(strict=True))
 if len(matches)!=1:raise NotReady(label+" publication cardinality="+str(len(matches)))
 root=matches[0];mp,m=sealed(root/"bundle_manifest.json",label+" manifest")
 if (root.name!=identity or m.get("bundle_id")!=identity or sha(mp)!=manifest_sha
  or m.get("formal_scope")!="mysql_only" or m.get("cross_engine_comparison_prohibited") is not True
  or m.get("postgresql_mutation_prohibited") is not True or m.get("formal_launch_authorized") is not False
  or m.get("accepted_round_authorized") is not False or m.get("accepted_rounds_credit")!=0):raise NotReady(label+" identity/scope differs")
 files=m.get("files")
 if not isinstance(files,dict) or not files:raise NotReady(label+" file closure absent")
 for rel,digest in files.items():
  pure=PurePosixPath(str(rel))
  if pure.is_absolute() or ".." in pure.parts or HEX64.fullmatch(str(digest)) is None:raise NotReady(label+" unsafe closure entry")
  member=regular(root/pure,label+" member "+str(rel))
  if member.parent!=root and root not in member.parents:raise NotReady(label+" member escaped bundle")
  if sha(member)!=digest:raise NotReady(label+" member hash differs: "+str(rel))
 return root,m

def verify_self()->Path:
 script=Path(__file__).resolve(strict=True);root=script.parents[1];mp,m=sealed(root/"bundle_manifest.json","one-click manifest");unsigned=dict(m);identity=unsigned.pop("bundle_id",None)
 rel=script.relative_to(root).as_posix()
 if (root.name!=identity or hashlib.sha256(canonical(unsigned)).hexdigest()!=identity or (m.get("files") or {}).get(rel)!=sha(script)
  or m.get("formal_scope")!="mysql_only" or m.get("postgresql_mutation_prohibited") is not True or m.get("accepted_round_authorized") is not False or m.get("accepted_rounds_credit")!=0):raise NotReady("one-click self identity/closure differs")
 return root

def expect(value:dict[str,Any],label:str,**fields:Any)->None:
 for key,wanted in fields.items():
  if value.get(key)!=wanted:raise NotReady(label+" "+key+" differs")

def dual_principal_tpch_defaults(path:Path,spec:dict[str,Any],evidence:dict[str,Any],attempt:str)->Path:
 path=Path(path);paths=spec.get("paths") or {};attempt_root=Path(str(paths.get("attempt_root") or ""));credentials=Path(str(paths.get("credentials_dir") or ""));expected=attempt_root/"credentials/mysql.cnf"
 try:file_state=path.lstat();directory_state=credentials.lstat();resolved=path.resolve(strict=True);resolved_directory=credentials.resolve(strict=True)
 except OSError as exc:raise NotReady("TPC-H dual-principal defaults absent") from exc
 if (not path.is_absolute() or not attempt_root.is_absolute() or attempt_root.name!=attempt or credentials!=attempt_root/"credentials" or path!=expected or str(spec.get("mysql_defaults_file") or "")!=str(expected) or resolved!=path or resolved_directory!=credentials or stat.S_ISLNK(file_state.st_mode) or not stat.S_ISREG(file_state.st_mode) or stat.S_ISLNK(directory_state.st_mode) or not stat.S_ISDIR(directory_state.st_mode) or (file_state.st_uid,file_state.st_gid,stat.S_IMODE(file_state.st_mode))!=(1008,0,0o440) or (directory_state.st_uid,directory_state.st_gid,stat.S_IMODE(directory_state.st_mode))!=(1008,0,0o750) or evidence.get("attempt_token")!=attempt or evidence.get("spec_sha256")!=hashlib.sha256(canonical(spec)).hexdigest()):raise NotReady("TPC-H dual-principal defaults/spec/evidence contract differs")
 return resolved

def tmpfs(path:Path)->None:
 result=subprocess.run(["/usr/bin/findmnt","-rn","-o","FSTYPE","--target",str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=10)
 if result.returncode or result.stdout.strip()!="tmpfs":raise NotReady(str(path)+" is not tmpfs")

def live_container(socket_uri:str,identity:str)->None:
 if HEX64.fullmatch(str(identity)) is None:raise NotReady("container identity differs")
 result=subprocess.run(["/usr/bin/docker","--host",socket_uri,"inspect","--format","{{json .}}",identity],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=20)
 try:row=json.loads(result.stdout)
 except json.JSONDecodeError as exc:raise NotReady("container inspect JSON differs") from exc
 if result.returncode or row.get("Id")!=identity or (row.get("State") or {}).get("Running") is not True or int((row.get("State") or {}).get("Pid") or 0)<=0:raise NotReady("container is not live: "+identity)

def run_logged(argv:list[str],log_root:Path,label:str,timeout:int=1200)->tuple[Path,Path]:
 stdout=log_root/(label+".stdout");stderr=log_root/(label+".stderr")
 env={"PATH":"/usr/bin:/bin","PYTHONPATH":"","PYTHONDONTWRITEBYTECODE":"1","LANG":"C","LC_ALL":"C"}
 with stdout.open("xb") as so,stderr.open("xb") as se:
  result=subprocess.run(argv,stdin=subprocess.DEVNULL,stdout=so,stderr=se,env=env,timeout=timeout)
 if result.returncode:raise NotReady(label+" failed rc="+str(result.returncode)+" stdout="+str(stdout)+" stderr="+str(stderr))
 print(json.dumps({"stage":label,"status":"PASS_SUBPROCESS","stdout":str(stdout),"stderr":str(stderr),"accepted_rounds_credit":0},sort_keys=True),flush=True)
 return stdout,stderr

def subprocess_json(stdout:Path,label:str)->dict[str,Any]:
 try:value=json.loads(regular(stdout,label+" stdout").read_text(encoding="utf-8"))
 except (UnicodeError,json.JSONDecodeError) as exc:raise NotReady(label+" stdout JSON differs: "+str(stdout)) from exc
 if not isinstance(value,dict):raise NotReady(label+" stdout object required: "+str(stdout))
 return value

def write_private(path:Path,text:str)->None:
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 try:
  os.write(fd,text.encode("ascii"));os.fsync(fd)
 finally:os.close(fd)
 if stat.S_IMODE(path.stat().st_mode)!=0o600 or path.stat().st_uid!=0:raise NotReady("private token ownership/mode differs")

def start_ticks(pid:int)->int:
 raw=(Path("/proc")/str(pid)/"stat").read_text(encoding="ascii");end=raw.rfind(")")
 return int(raw[end+2:].split()[19])

def workload_rows(value:Any)->list[dict[str,Any]]:
 found=[]
 def visit(node:Any)->None:
  if isinstance(node,dict):
   if node.get("role")=="qualification_workload" and isinstance(node.get("pid"),int) and node["pid"]>0:
    found.append({key:node.get(key) for key in ("pid","ppid","comm","cmdline_sha256","cpus_allowed_list","mems_allowed_list","container_identity","role")})
   for child in node.values():visit(child)
  elif isinstance(node,list):
   for child in node:visit(child)
 visit(value);unique={int(row["pid"]):row for row in found};return [unique[key] for key in sorted(unique)]

def seal_output(path:Path,value:dict[str,Any])->None:
 raw=canonical(value);fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o400)
 try:os.write(fd,raw);os.fsync(fd)
 finally:os.close(fd)
 side=Path(str(path)+".sha256");fd=os.open(side,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o400)
 try:os.write(fd,(hashlib.sha256(raw).hexdigest()+"  "+path.name+"\n").encode());os.fsync(fd)
 finally:os.close(fd)

def run(*,local_only:bool=False,tpch_bootstrap_spec:Path,tpch_bootstrap_evidence:Path,tpch_defaults_file:Path)->dict[str,Any]:
 self_bundle=verify_self()
 if not local_only:raise NotReady("this b235 successor authorizes only the TPC-C-local zero-credit flow")
 if socket.gethostname().split(".")[0]!="node04":raise NotReady("must run directly on node04")
 if os.geteuid()!=0:raise NotReady("root execution required for root-owned short-lived authorities")
 py=regular(PYTHON,"pinned Python")
 if Path(sys.executable).resolve()!=py:raise NotReady("controller must use pinned Python: "+str(py))
 needed={"tpch_ff","collector","current","a336","tpcc_v16","local_flow","blkio_sealer"}
 bundles={name:resolve_bundle(name)[0] for name in needed}
 a336_control=regular(bundles["a336"]/"control/mysql_tpcc_v16_same_live_gate_control_v1.py","a336 control")
 old_spec_path,old_spec=sealed(tpch_bootstrap_spec,"current TPC-H spec")
 old_evidence_path,old_evidence=sealed(tpch_bootstrap_evidence,"current TPC-H evidence")
 adopted_path,adopted=sealed(CUR/"adopted_service.json","adopted service")
 ledger_path,ledger=sealed(CUR/"phase_ledger_v2.json","phase ledger")
 tpcc_defaults=regular(CUR/"config/mysql.cnf","TPC-C defaults");tpcc_xml=regular(CUR/"config/tpcc.xml","TPC-C XML")
 tpch_attempt=old_spec.get("attempt_token")
 if not isinstance(tpch_attempt,str) or re.fullmatch(r"tpch-v2-[A-Za-z0-9_.-]{12,140}",tpch_attempt) is None:raise NotReady("current TPC-H attempt token differs")
 tpch_defaults=dual_principal_tpch_defaults(tpch_defaults_file,old_spec,old_evidence,tpch_attempt)
 expect(old_evidence,"old evidence",attempt_token=tpch_attempt,status="PASS_BOOTSTRAP_LIVE_NOT_R0_OR_FORMAL_AUTHORITY",bundle_id=BUNDLES["tpch_ff"][0],accepted_round_authorized=False)
 if Path(str(old_spec.get("mysql_defaults_file") or "")).resolve(strict=True)!=tpch_defaults:raise NotReady("current TPC-H defaults/spec binding differs")
 if (old_spec.get("bundle") or {}).get("bundle_id")!=BUNDLES["tpch_ff"][0] or Path((old_spec.get("bundle") or {}).get("path","")).resolve(strict=True)!=bundles["tpch_ff"]:raise NotReady("old spec exact FF path differs")
 expect(adopted,"adopted",schema_version="mysql_tpcc_node04_adopted_service_authority_v2",status="PASS",bundle_id=BUNDLES["tpcc_v16"][0],attempt_id=TPCC_ATTEMPT,formal_launch_authorized=False,accepted_round_authorized=False)
 expect(ledger,"ledger",status="INCOMPLETE")
 if set((ledger.get("phases") or {}))!={"qualification"} or (ledger.get("phases") or {}).get("qualification",{}).get("status")!="PASS":raise NotReady("qualification ledger differs")
 database=adopted.get("database") or {};client=adopted.get("client") or {}
 if (database.get("cpuset_cpus")!="21-24,57-60" or client.get("cpuset_cpus")!="8-15,44-51" or database.get("device")!="/dev/nvme3n1" or database.get("device_major_minor")!="259:2" or database.get("mysql_port")!=65106):raise NotReady("TPC-C resource identity differs")
 private=(adopted.get("client_private_config") or {}).get("files") or {}
 if sha(tpcc_defaults)!=(private.get("mysql.cnf") or {}).get("sha256") or sha(tpcc_xml)!=(private.get("tpcc.xml") or {}).get("sha256"):raise NotReady("TPC-C private config hash differs")
 if stat.S_IMODE(tpcc_defaults.stat().st_mode)!=0o600 or stat.S_IMODE(tpcc_xml.stat().st_mode)!=0o600:raise NotReady("TPC-C private config mode differs")
 tpcc_measurement=Path((adopted.get("measurement_bridge") or {}).get("host_path","")).resolve(strict=True);tpch_measurement=Path((old_spec.get("paths") or {}).get("host_measurement_tmpfs","")).resolve(strict=True);tmpfs(tpcc_measurement);tmpfs(tpch_measurement)
 identities=old_evidence.get("same_live_identity") or {};live_container("unix:///run/ra-docker-mysql-tpch-v1.sock",identities.get("database_container_id"));live_container("unix:///run/ra-docker-mysql-tpch-v1.sock",identities.get("client_container_id"));live_container("unix:///run/ra-docker-mysql-tpcc-v1.sock",database.get("container_id"));live_container("unix:///run/ra-docker-mysql-tpcc-v1.sock",client.get("container_id"))
 tag="joint-"+time.strftime("%Y%m%dT%H%M%SZ",time.gmtime())+"-"+secrets.token_hex(8);log_root=tpcc_measurement/(tag+"-oneclick");log_root.mkdir(mode=0o700)
 stage_logs={}
 # The caller supplies the fresh b235 spec, evidence, and private defaults as
 # three explicit sealed inputs.  Do not manufacture FF313 continuity through
 # the obsolete refresh/joint chain.  The sealed d949 current-args materializer
 # below independently loads b235's authority module and validates this exact
 # spec/evidence/defaults triple fail-closed before producing launch material.
 fresh_spec_path,fresh_spec=old_spec_path,old_spec
 fresh_evidence_path,fresh_evidence=old_evidence_path,old_evidence
 explicit_input_path=log_root/"explicit_b235_tpch_inputs.authority.json"
 seal_output(explicit_input_path,{"schema_version":"mysql_tpcc_v16_explicit_b235_tpch_inputs_v2","status":"BOUND_EXPLICIT_SEALED_INPUTS_AND_EXACT_DUAL_PRINCIPAL_DEFAULTS_PENDING_8007_REVALIDATION","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"tpch_bundle_id":BUNDLES["tpch_ff"][0],"tpch_manifest_sha256":BUNDLES["tpch_ff"][1],"attempt_token":tpch_attempt,"bootstrap_spec_path":str(fresh_spec_path),"bootstrap_spec_sha256":sha(fresh_spec_path),"bootstrap_evidence_path":str(fresh_evidence_path),"bootstrap_evidence_sha256":sha(fresh_evidence_path),"mysql_defaults_file":str(tpch_defaults),"mysql_defaults_sha256":sha(tpch_defaults),"mysql_defaults_identity":{"uid":1008,"gid":0,"mode":"0440"},"credentials_directory_identity":{"uid":1008,"gid":0,"mode":"0750"},"credential_chmod_or_chown_performed":False,"ff313_continuity_claimed":False,"old_refresh_chain_used":False,"tpch_workload_started":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0})
 snapshot_out=tpcc_measurement/(tag+"-a336");a336_stdout,a336_stderr=run_logged([str(py),"-B",str(a336_control),"snapshot","--bundle",str(bundles["tpcc_v16"]),"--adopted-service",str(adopted_path),"--phase-ledger",str(ledger_path),"--output-root",str(snapshot_out)],log_root,"a336");stage_logs["a336"]=(str(a336_stdout),str(a336_stderr));a336_result=subprocess_json(a336_stdout,"a336")
 try:snapshot_candidate=Path(str(a336_result["authority"])).resolve(strict=True)
 except (KeyError,OSError) as exc:raise NotReady("a336 stdout authority path absent: "+str(a336_stdout)) from exc
 snapshot_path,snapshot=sealed(snapshot_candidate,"a336 snapshot");snapshot_id=hashlib.sha256(canonical(snapshot)).hexdigest()
 if (a336_result.get("authority_id")!=snapshot_id or snapshot_path.name!="same_live_snapshot.json" or snapshot_path.parent.name!=snapshot_id or snapshot_path.parent.parent!=snapshot_out.resolve(strict=True)):raise NotReady("a336 content-addressed output binding differs: "+str(snapshot_path))
 expect(snapshot,"snapshot",status="PASS_READ_ONLY_LIVE_IDENTITY_NOT_CAPTURE_AUTHORITY",accepted_round_authorized=False,accepted_rounds_credit=0)
 if local_only:
  blkio_entry=regular(bundles["blkio_sealer"]/"control/mysql_tpcc_v16_blkio_authority_sidecar_sealer_v1.py","blkio sidecar sealer");blkio_authority=CUR/"blkio_throttle_authority.json";blkio_receipt=log_root/"blkio_sidecar.receipt.json"
  stage_logs["blkio_sidecar"]=tuple(str(path) for path in run_logged([str(py),"-B",str(blkio_entry),"--v16-bundle",str(bundles["tpcc_v16"]),"--adopted-service",str(adopted_path),"--authority",str(blkio_authority),"--output",str(blkio_receipt)],log_root,"blkio_sidecar"))
  bp,blkio=sealed(blkio_receipt,"blkio sidecar receipt")
  if (blkio.get("status")!="PASS_VALIDATED_LIVE_BLKIO_AUTHORITY_SIDECAR" or blkio.get("authority_sha256")!="6822e90cc8e6980b6b0030f02f9a2b53a7b5fc23eeb58f214ea26de5a585661d" or blkio.get("sidecar_content")!="6822e90cc8e6980b6b0030f02f9a2b53a7b5fc23eeb58f214ea26de5a585661d  blkio_throttle_authority.json\n" or blkio.get("authority_content_modified") is not False or blkio.get("database_restarted") is not False or blkio.get("client_restarted") is not False or blkio.get("workload_started") is not False or blkio.get("accepted_rounds_credit")!=0):raise NotReady("blkio sidecar repair receipt differs")
 token_root=tpcc_measurement/(tag+"-tokens");token_root.mkdir(mode=0o700);tokens={}
 for name in ("a-solo","a-paired","b-solo","b-paired"):
  path=token_root/(name+".token");write_private(path,secrets.token_hex(48)+"\n");tokens[name]=path
 if len({sha(path) for path in tokens.values()})!=4:raise NotReady("four flow tokens are not unique")
 current_out=tpcc_measurement/(tag+"-current")
 current_entry=regular(bundles["current"]/"materializer/mysql_tpcc_v16_current_args_materializer_v1.py","b235-aware current args materializer")
 stage_logs["current"]=tuple(str(path) for path in run_logged([str(py),"-B",str(current_entry),"--a336-snapshot",str(snapshot_path),"--tpcc-adopted-service",str(adopted_path),"--tpch-bootstrap-spec",str(fresh_spec_path),"--tpch-bootstrap-evidence",str(fresh_evidence_path),"--tpch-bundle",str(bundles["tpch_ff"]),"--tpcc-bundle",str(bundles["tpcc_v16"]),"--collector-bundle",str(bundles["collector"]),"--tpcc-defaults-file",str(tpcc_defaults),"--a-solo-token-file",str(tokens["a-solo"]),"--a-paired-token-file",str(tokens["a-paired"]),"--b-solo-token-file",str(tokens["b-solo"]),"--b-paired-token-file",str(tokens["b-paired"]),"--output-root",str(current_out)],log_root,"current"))
 current_args_path,current_args=sealed(current_out/"current_args.json","current args");current_receipt_path,current_receipt=sealed(current_out/"materialization_receipt.json","current receipt")
 expect(current_args,"current args",status="SEALED_FRESH_FOUR_FLOW_ARGS_NOT_EXECUTED",accepted_round_authorized=False);expect(current_receipt,"current receipt",status="PASS_OFFLINE_FRESH_SPECS_AUTHORITIES_NOT_EXECUTED",accepted_round_authorized=False)
 if (current_receipt.get("inputs") or {}).get("tpch_bootstrap_spec_sha256")!=sha(fresh_spec_path) or (current_receipt.get("inputs") or {}).get("tpch_bootstrap_evidence_sha256")!=sha(fresh_evidence_path) or (current_receipt.get("inputs") or {}).get("tpch_defaults_sha256")!=sha(tpch_defaults) or (current_receipt.get("inputs") or {}).get("tpch_manifest_sha256")!=BUNDLES["tpch_ff"][1]:raise NotReady("8007 current args did not bind the explicit b235 dual-principal inputs")
 if local_only:
  local_entry=regular(bundles["local_flow"]/"controller/mysql_tpcc_v16_local_parallel_systemd219_flow_v1.py","TPC-C local flow entry");local_run=tpcc_measurement/(tag+"-tpcc-local-run");local_out=tpcc_measurement/(tag+"-tpcc-local-materials")
  stage_logs["local_materialize"]=tuple(str(path) for path in run_logged([str(py),"-B",str(local_entry),"materialize","--current-args-bundle",str(bundles["current"]),"--current-args",str(current_args_path),"--current-args-receipt",str(current_receipt_path),"--collector-bundle",str(bundles["collector"]),"--v16-bundle",str(bundles["tpcc_v16"]),"--run-root",str(local_run),"--output-dir",str(local_out)],log_root,"local_materialize"))
  plan_path,plan=sealed(local_out/"tpcc_local.plan.json","TPC-C local plan");argv_path,argv=sealed(local_out/"systemd219.argv.json","TPC-C local argv");launch=regular(local_out/"launch_and_wait.sh","TPC-C local launch")
  systemd_argv=argv.get("systemd_run_argv");wait_argv=argv.get("wait_argv");expected=("#!/bin/sh\nset -eu\n"+shlex.join(systemd_argv)+"\nexec "+shlex.join(wait_argv)+"\n").encode()
  if (plan.get("schema_version")!="mysql_tpcc_v16_local_parallel_systemd219_plan_v1" or plan.get("status")!="SEALED_TPCC_ONLY_NOT_EXECUTED" or plan.get("data_plane_workload_started")!=["tpcc_cpu"] or plan.get("tpch_client_policy")!="read_only_inventory_only" or plan.get("tpch_workload_start_stop_or_change_prohibited") is not True or plan.get("accepted_rounds_credit")!=0 or argv.get("schema_version")!="mysql_tpcc_local_systemd219_argv_v1" or systemd_argv[:5]!=["/usr/bin/systemd-run","--unit",plan.get("unit"),"--service-type=oneshot","--remain-after-exit"] or argv.get("legacy_property_options_used") is not False or argv.get("wait_or_collect_options_used") is not False or launch.read_bytes()!=expected or stat.S_IMODE(launch.stat().st_mode)!=0o500 or launch.stat().st_uid!=0 or launch.stat().st_gid!=0):raise NotReady("TPC-C local launch boundary differs")
  completion=local_run/"completion/tpcc_local.completion.json";env={"PATH":"/usr/bin:/bin","PYTHONPATH":"","PYTHONDONTWRITEBYTECODE":"1","LANG":"C","LC_ALL":"C"};so=(log_root/"local_launch.stdout").open("xb");se=(log_root/"local_launch.stderr").open("xb");proc=subprocess.Popen(["/bin/sh",str(launch)],stdin=subprocess.DEVNULL,stdout=so,stderr=se,env=env);controller=[];unit=str(plan.get("unit") or "")
  try:
   deadline=time.time()+1900
   while proc.poll() is None and time.time()<deadline:
    status=subprocess.run(["/usr/bin/systemctl","show","--property=MainPID","--value",unit],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=10)
    try:pid=int(status.stdout.strip())
    except ValueError:pid=0
    if pid>0 and all(row["pid"]!=pid for row in controller):
     try:controller.append({"pid":pid,"process_start_ticks":start_ticks(pid),"unit":unit})
     except (OSError,ValueError):pass
    time.sleep(0.2)
   if proc.poll() is None:raise NotReady("TPC-C local launch exceeded deadline: "+unit)
   if proc.returncode:raise NotReady("TPC-C local launch failed rc="+str(proc.returncode)+" stderr="+str(log_root/"local_launch.stderr"))
  finally:so.close();se.close()
  cp,done=sealed(completion,"TPC-C local completion");ep,evidence=sealed(local_run/"tpcc_local.evidence.json","TPC-C local evidence");gp,gate=sealed(local_run/"tpcc_local.interference_gate.json","TPC-C local gate");rows=workload_rows(evidence)
  if (done.get("status")!="PASS_TPCC_LOCAL_QUALIFICATION_ZERO_CREDIT" or done.get("tpcc_workloads_started")!=1 or done.get("tpch_workloads_started")!=0 or done.get("tpch_mutation_performed") is not False or done.get("tpcc_evidence_sha256")!=sha(ep) or done.get("local_interference_gate_sha256")!=sha(gp) or gate.get("status")!="PASS_TPCC_ONLY_WITH_TPCH_READ_ONLY_STABLE" or not rows):raise NotReady("TPC-C local completion/PID evidence differs")
  stage_logs["local_launch"]=(str(log_root/"local_launch.stdout"),str(log_root/"local_launch.stderr"));receipt={"schema_version":"mysql_tpcc_v16_local_parallel_one_click_completion_v3","status":"PASS_EXPLICIT_B235_A336_BLKIO_CURRENT_TPCC_LOCAL_REAL_PID_ZERO_CREDIT","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"run_tag":tag,"one_click_bundle":str(self_bundle),"bundle_identity":{name:{"bundle_id":BUNDLES[name][0],"manifest_sha256":BUNDLES[name][1]} for name in sorted(needed)},"explicit_b235_input_authority":str(explicit_input_path),"explicit_b235_input_authority_sha256":sha(explicit_input_path),"ff313_continuity_claimed":False,"old_refresh_chain_used":False,"a336_snapshot_path":str(snapshot_path),"blkio_sidecar_receipt_path":str(bp),"blkio_sidecar_receipt_sha256":sha(bp),"blkio_authority_sha256":blkio["authority_sha256"],"stage_logs":stage_logs,"controller_pids":controller,"real_workload_pids":{"tpcc_local":rows},"completion_path":str(cp),"completion_sha256":sha(cp),"local_gate_path":str(gp),"local_gate_sha256":sha(gp),"tpcc_workloads_started":1,"tpch_workloads_started":0,"tpch_mutation_performed":False,"capture_not_run":True,"fresh_r0_not_run":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0}
  final=log_root/"local_one_click.completion.json";seal_output(final,receipt);receipt["one_click_completion_path"]=str(final);return receipt

def main()->int:
 p=argparse.ArgumentParser(description=__doc__);p.add_argument("--mode",choices=("local",),default="local");p.add_argument("--tpch-bootstrap-spec",type=Path,required=True);p.add_argument("--tpch-bootstrap-evidence",type=Path,required=True);p.add_argument("--tpch-defaults-file",type=Path,required=True);a=p.parse_args();result=run(local_only=True,tpch_bootstrap_spec=a.tpch_bootstrap_spec,tpch_bootstrap_evidence=a.tpch_bootstrap_evidence,tpch_defaults_file=a.tpch_defaults_file);print(json.dumps(result,sort_keys=True));return 0

if __name__=="__main__":
 try:raise SystemExit(main())
 except (NotReady,OSError,UnicodeError,ValueError,KeyError,subprocess.SubprocessError) as exc:print("NOT_READY: "+str(exc),file=sys.stderr);raise SystemExit(2)

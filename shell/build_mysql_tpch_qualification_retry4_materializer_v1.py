#!/usr/bin/env python3
"""Build an immutable, non-executing TPC-H qualification retry4 materializer."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
OUT = ROOT / "artifacts/mysql_tpch_qualification_retry4_materializer_v1"
TARGET_ID = "ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4"
TARGET_MANIFEST_SHA = "af1d0facca8b33e2638c0a2af6a751bececc407d483055ca064a34308d27bb52"
SUPPORT_ID = "86232f80b4e274aadf1cdbd8acc19033786acfda8dafb6dc18f7bace4bdb61a3"
SUPPORT_MANIFEST_SHA = "2ef63e1ad38dc0217011317c1ed3177c47e6fb46380587482739f87eee761cf7"
SUPPORT_ENTRY_SHA = "5545dcb6277fd8979a4b6e1994bf7941aa8db19b4a369498bf22ae4b8c2412b1"
SUPPLEMENT_RENDERER_SHA = "7c3691f47c6193ed164a5666ecee4058878e0d21be926f658d66f61f92ae4139"


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            digest.update(block)
    return digest.hexdigest()


MATERIALIZER = rf'''#!/usr/bin/env python3
"""Create fresh retry4 command/authority/token/argv without launching it."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, os, secrets, shlex, stat, sys, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

TARGET_ID = "{TARGET_ID}"
TARGET_MANIFEST_SHA = "{TARGET_MANIFEST_SHA}"
SUPPORT_ID = "{SUPPORT_ID}"
SUPPORT_MANIFEST_SHA = "{SUPPORT_MANIFEST_SHA}"
SUPPORT_ENTRY_SHA = "{SUPPORT_ENTRY_SHA}"
SUPPLEMENT_RENDERER_SHA = "{SUPPLEMENT_RENDERER_SHA}"
BOOTSTRAP_ATTEMPT = "tpch-v2-20260803t022500z-afbe6badff313374deadbeef"
DATABASE_CONTAINER = "ra_mysql_tpch_v2_tpch_v2_20260803t022500z_afbe6badff313374deadbee_db"
CLIENT_CONTAINER = "ra_mysql_tpch_v2_tpch_v2_20260803t022500z_afbe6badff313374deadbee_client"
ENTRY_RELATIVE = "qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py"
SUPPLEMENT_NAME = "blkio_throttle_authority.supplement.json"
MIN_TTL = 300
MAX_TTL = 3600

class MaterializeError(RuntimeError): pass
def canonical(v): return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sha256(p):
 d=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""): d.update(b)
 return d.hexdigest()
def load_sealed(p,label):
 p=Path(p).resolve(strict=True); s=Path(str(p)+".sha256")
 if p.is_symlink() or not p.is_file() or s.is_symlink() or not s.is_file() or s.read_text(encoding="ascii",errors="strict") != f"{{sha256(p)}}  {{p.name}}\n": raise MaterializeError(label+" seal differs")
 try:v=json.loads(p.read_text(encoding="utf-8",errors="strict"))
 except json.JSONDecodeError as e: raise MaterializeError(label+" JSON differs") from e
 if not isinstance(v,dict): raise MaterializeError(label+" must be object")
 return v
def write_exclusive(path,payload,mode=0o600):
 path=Path(path)
 if path.exists() or path.is_symlink(): raise MaterializeError("refusing overwrite: "+str(path))
 with path.open("xb") as f: f.write(payload); f.flush(); os.fsync(f.fileno())
 os.chmod(path,mode)
def write_sealed(path,value):
 payload=canonical(value); write_exclusive(path,payload); write_exclusive(Path(str(path)+".sha256"),f"{{hashlib.sha256(payload).hexdigest()}}  {{Path(path).name}}\n".encode("ascii"))
def verify_bundle(bundle,wanted_id,wanted_sha,label):
 bundle=Path(bundle).resolve(strict=True); manifest=load_sealed(bundle/"bundle_manifest.json",label+" manifest")
 if bundle.name!=wanted_id or manifest.get("bundle_id")!=wanted_id or sha256(bundle/"bundle_manifest.json")!=wanted_sha or manifest.get("formal_scope")!="mysql_only" or manifest.get("cross_engine_comparison_prohibited") is not True or manifest.get("formal_launch_authorized") is not False: raise MaterializeError(label+" identity differs")
 return bundle,manifest
def validate_supplement(path):
 value=load_sealed(path,"legacy blkio supplement")
 if (Path(path).name!=SUPPLEMENT_NAME or Path(path).resolve().parent.name!=BOOTSTRAP_ATTEMPT
  or value.get("schema_version")!="mysql_tpch_legacy_blkio_live_supplement_v1"
  or value.get("status")!="PASS_LEGACY_AUTHORITY_PLUS_LIVE_READ_ONLY_SUPPLEMENT"
  or value.get("renderer_sha256")!=SUPPLEMENT_RENDERER_SHA
  or value.get("bundle_id")!=TARGET_ID or value.get("bundle_manifest_sha256")!=TARGET_MANIFEST_SHA
  or value.get("attempt_token")!=BOOTSTRAP_ATTEMPT
  or value.get("host_config_memory_bytes")!=6174015488
  or value.get("host_config_memory_swap_bytes")!=6174015488
  or value.get("host_config_swap_capacity_bytes")!=0
  or value.get("cgroup_total_swap_bytes")!=0
  or value.get("process_tree_total_swap_bytes")!=0
  or value.get("live_effective_cgroup_v1_throttle_verified") is not True
  or value.get("device_major_minor")!="259:1"
  or value.get("read_bps")!=272629760 or value.get("write_bps")!=272629760
  or value.get("container_restart_count")!=0
  or value.get("database_restarted_by_remediation") is not False
  or value.get("authority_file_mutated") is not False
  or value.get("database_or_client_mutation_performed") is not False
  or value.get("postgresql_mutation_performed") is not False
  or value.get("accepted_round_authorized") is not False
  or value.get("accepted_rounds_credit")!=0): raise MaterializeError("legacy blkio supplement binding differs")
 return value
def values_for_keys(value,keys):
 result=[]
 if isinstance(value,Mapping):
  for key,child in value.items():
   if key in keys: result.append(child)
   result.extend(values_for_keys(child,keys))
 elif isinstance(value,list):
  for child in value: result.extend(values_for_keys(child,keys))
 return result
def fresh_attempt():
 stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
 return "tpch-qualification-retry4-"+stamp+"-"+secrets.token_hex(16)
def amend_command(base,attempt,supplement_path,supplement_sha,support_bundle,metric_root):
 result=json.loads(json.dumps(base)); old=result.get("qualification_attempt_id"); argv=list(result.get("argv") or [])
 if not isinstance(old,str) or len(old)<32 or old==attempt: raise MaterializeError("retry3 attempt binding differs")
 indexes=[i for i,v in enumerate(argv) if isinstance(v,str) and v.startswith("--attempt-id=")]
 switches=[i for i,v in enumerate(argv) if v=="--metric-stream"]
 if len(indexes)!=1 or len(switches)!=1 or switches[0]+1>=len(argv): raise MaterializeError("retry3 argv contract differs")
 host_metric=metric_root/(attempt+".host_metrics.jsonl"); native_host=Path(result["measurement_output"]["host_bind_source"])/(attempt+".native.jsonl"); native_container=str(Path(result["measurement_output"]["container_tmpfs_destination"])/(attempt+".native.jsonl"))
 for path in (host_metric,native_host):
  if path.exists() or path.is_symlink(): raise MaterializeError("retry4 metric output is not fresh: "+str(path))
 argv[indexes[0]]="--attempt-id="+attempt; argv[switches[0]+1]=native_container
 result["qualification_attempt_id"]=attempt; result["argv"]=argv
 result["workload_metric_stream"]={{"schema_version":"mysql_algorithm_c_debug_workload_metric_stream_v1","path":str(host_metric)}}
 result["retry4_materializer_binding"]={{"schema_version":"mysql_tpch_qualification_retry4_binding_v1","predecessor_attempt_id":old,"predecessor_reuse_prohibited":True,"support_collector_bundle_id":SUPPORT_ID,"support_collector_manifest_sha256":SUPPORT_MANIFEST_SHA,"support_collector_entry_sha256":SUPPORT_ENTRY_SHA,"support_bundle_path":str(support_bundle),"legacy_blkio_supplement_path":str(supplement_path),"legacy_blkio_supplement_sha256":supplement_sha,"bootstrap_attempt_token":BOOTSTRAP_ATTEMPT,"qualification_attempt_id":attempt,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"accepted_round_authorized":False}}
 return result,old,host_metric,native_host
def import_entry(path):
 spec=importlib.util.spec_from_file_location("retry4_support_entry",path); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module
def main():
 p=argparse.ArgumentParser(description=__doc__); p.add_argument("--bundle",type=Path,required=True); p.add_argument("--support-bundle",type=Path,required=True); p.add_argument("--retry3-command",type=Path,required=True); p.add_argument("--retry3-evidence",type=Path); p.add_argument("--retry3-expected-output",type=Path); p.add_argument("--supplement",type=Path,required=True); p.add_argument("--mysql-defaults-file",type=Path,required=True); p.add_argument("--output-dir",type=Path,required=True); p.add_argument("--valid-seconds",type=int,default=1800); a=p.parse_args()
 if not MIN_TTL<=a.valid_seconds<=MAX_TTL: raise MaterializeError("authority TTL must be 300..3600 seconds")
 target,_=verify_bundle(a.bundle,TARGET_ID,TARGET_MANIFEST_SHA,"target bundle"); support,smanifest=verify_bundle(a.support_bundle,SUPPORT_ID,SUPPORT_MANIFEST_SHA,"support bundle"); entry=support/ENTRY_RELATIVE
 if sha256(entry)!=SUPPORT_ENTRY_SHA or (smanifest.get("files") or {{}}).get(ENTRY_RELATIVE)!=SUPPORT_ENTRY_SHA: raise MaterializeError("support entry closure differs")
 supplement=validate_supplement(a.supplement); supplement_sha=sha256(a.supplement)
 base=load_sealed(a.retry3_command,"retry3 command")
 old_attempt=base.get("qualification_attempt_id")
 if (a.retry3_evidence is None)==(a.retry3_expected_output is None): raise MaterializeError("choose exactly one retry3 evidence or expected-output absence proof")
 absence=None
 if a.retry3_evidence is not None:
  evidence=load_sealed(a.retry3_evidence,"retry3 evidence"); evidence_attempts=values_for_keys(evidence,{{"qualification_attempt_id","attempt_id"}})
  if old_attempt not in evidence_attempts: raise MaterializeError("retry3 evidence attempt differs")
  if any(v is True for v in values_for_keys(evidence,{{"formal_launch_authorized","formal_r0_authorized","formal_authorized","accepted_round_authorized"}})): raise MaterializeError("retry3 evidence unexpectedly authorizes credit")
  retry3_evidence_mode="sealed_failure_or_zero_credit_evidence"; retry3_evidence_sha=sha256(a.retry3_evidence); retry3_evidence_source=str(a.retry3_evidence.resolve())
 else:
  expected=a.retry3_expected_output
  side=Path(str(expected)+".sha256")
  if not expected.is_absolute() or expected.exists() or expected.is_symlink() or side.exists() or side.is_symlink(): raise MaterializeError("retry3 expected output absence differs")
  absence={{"schema_version":"mysql_tpch_retry3_output_absence_evidence_v1","status":"PASS_RETRY3_OUTPUT_AND_SIDECAR_ABSENT_ZERO_CREDIT","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"qualification_attempt_id":old_attempt,"retry3_command_sha256":sha256(a.retry3_command),"expected_output_path":str(expected),"output_absent":True,"sidecar_absent":True,"predecessor_reuse_prohibited":True,"formal_launch_authorized":False,"formal_r0_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0}}
  retry3_evidence_mode="materialized_output_absence_evidence"; retry3_evidence_source=str(expected); retry3_evidence_sha=None
 defaults=a.mysql_defaults_file.resolve(strict=True)
 if defaults.is_symlink() or not defaults.is_file() or stat.S_IMODE(defaults.stat().st_mode)&0o077: raise MaterializeError("MySQL defaults file must be private regular file")
 output=a.output_dir
 if not output.is_absolute() or output.exists() or output.is_symlink(): raise MaterializeError("retry4 output directory must be fresh absolute path")
 output.mkdir(parents=True,mode=0o700); os.chmod(output,0o700)
 if absence is not None:
  absence_path=output/"retry3.output_absence.json"; write_sealed(absence_path,absence); retry3_evidence_sha=sha256(absence_path); retry3_evidence_source=str(absence_path)
 attempt=fresh_attempt(); metric_root=Path(base["measurement_output"]["host_tmpfs_directory"])
 command,predecessor,host_metric,native_host=amend_command(base,attempt,a.supplement,supplement_sha,support,metric_root)
 command_path=output/"retry4.command.json"; authority_path=output/"retry4.authority.json"; token_path=output/"retry4.token"; result_path=output/(attempt+".qualification.json"); launch_path=output/"run_retry4.sh"; launch_argv_path=output/"retry4.launch_argv.json"; materialization_path=output/"retry4.materialization.json"
 token=secrets.token_hex(48); write_exclusive(token_path,(token+"\n").encode("ascii"),0o600); write_sealed(command_path,command)
 runtime=json.loads((target/"distributed_runtime_contract.json").read_text())
 module=import_entry(entry); module.validate_command_spec_without_static_pid(command_path,TARGET_ID,"qualification_solo",runtime["client_resource_envelope"])
 now=time.time(); authority={{"schema_version":"mysql_algorithm_c_main_agent_qualification_authority_v1","authorized_by":"main_agent","bundle_id":TARGET_ID,"mode":"qualification_solo","qualification_attempt_id":attempt,"main_agent_token_sha256":hashlib.sha256(token.encode()).hexdigest(),"command_spec_sha256":sha256(command_path),"debug_only":True,"capture_authorized":False,"formal_r0_authorized":False,"formal_authorized":False,"accepted_round_authorized":False,"not_before_epoch":now-1.0,"expires_epoch":now+float(a.valid_seconds),"target_manifest_sha256":TARGET_MANIFEST_SHA,"support_collector_bundle_id":SUPPORT_ID,"support_collector_manifest_sha256":SUPPORT_MANIFEST_SHA,"support_collector_entry_sha256":SUPPORT_ENTRY_SHA,"legacy_blkio_supplement_sha256":supplement_sha}}
 write_sealed(authority_path,authority); module.validate_main_agent_authority_without_static_pid(authority_path,token,TARGET_ID,"qualification_solo",command_path)
 launch=[sys.executable,str(entry),"--qualification-solo","--bundle",str(target),"--container",DATABASE_CONTAINER,"--mysql-binary","/usr/bin/mysql","--mysql-client-container",CLIENT_CONTAINER,"--mysql-binary-in-client","/usr/bin/mysql","--mysql-defaults-file",str(defaults),"--output",str(result_path),"--main-agent-token-file",str(token_path),"--main-agent-authority",str(authority_path),"--command-spec",str(command_path)]
 write_sealed(launch_argv_path,{{"schema_version":"mysql_tpch_qualification_retry4_launch_argv_v1","argv":launch,"qualification_attempt_id":attempt,"support_collector_bundle_id":SUPPORT_ID,"formal_launch_authorized":False,"accepted_round_authorized":False}})
 script=("#!/bin/sh\nset -eu\nexec "+shlex.join(launch)+"\n").encode(); write_exclusive(launch_path,script,0o500)
 material={{"schema_version":"mysql_tpch_qualification_retry4_materialization_v1","status":"SEALED_FRESH_RETRY4_NOT_LAUNCHED","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"target_bundle_id":TARGET_ID,"target_manifest_sha256":TARGET_MANIFEST_SHA,"support_collector_bundle_id":SUPPORT_ID,"support_collector_manifest_sha256":SUPPORT_MANIFEST_SHA,"support_collector_entry_sha256":SUPPORT_ENTRY_SHA,"bootstrap_attempt_token":BOOTSTRAP_ATTEMPT,"qualification_attempt_id":attempt,"predecessor_retry3_attempt_id":predecessor,"predecessor_retry3_command_sha256":sha256(a.retry3_command),"predecessor_retry3_evidence_mode":retry3_evidence_mode,"predecessor_retry3_evidence_source":retry3_evidence_source,"predecessor_retry3_evidence_sha256":retry3_evidence_sha,"legacy_blkio_supplement_sha256":supplement_sha,"authority_ttl_seconds":a.valid_seconds,"authority_expires_epoch":authority["expires_epoch"],"fresh_paths":{{"command":str(command_path),"authority":str(authority_path),"token":str(token_path),"host_metric":str(host_metric),"native_metric_host":str(native_host),"qualification_output":str(result_path),"launch_script":str(launch_path)}},"predecessor_reuse_prohibited":True,"output_reuse_prohibited":True,"token_reuse_prohibited":True,"authority_reuse_prohibited":True,"formal_launch_authorized":False,"formal_r0_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_execution_performed":False}}
 write_sealed(materialization_path,material)
 print(json.dumps({{"status":material["status"],"qualification_attempt_id":attempt,"materialization":str(materialization_path),"launch_script":str(launch_path),"authority_expires_epoch":authority["expires_epoch"],"remote_execution_performed":False}},sort_keys=True)); return 0
if __name__=="__main__":
 try: raise SystemExit(main())
 except (MaterializeError,OSError,UnicodeError,ValueError,KeyError) as e: print("NOT_READY: "+str(e),file=sys.stderr); raise SystemExit(2)
'''


TEST = r'''import importlib.util, json, os
from pathlib import Path
import pytest
BUNDLE=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("m",BUNDLE/"materializer/mysql_tpch_qualification_retry4_materializer_v1.py"); M=importlib.util.module_from_spec(spec); spec.loader.exec_module(M)
def base(tmp):
 root=tmp/"measure"; bind=root/"client_measurement"; bind.mkdir(parents=True)
 return {"qualification_attempt_id":"retry3-"+"a"*40,"argv":["/readonly/adapter.py","--attempt-id=old","--metric-stream","/measurement/old.native.jsonl"],"measurement_output":{"host_tmpfs_directory":str(root),"host_bind_source":str(bind),"container_tmpfs_destination":"/measurement"}}
def test_amend_generates_fresh_attempt_and_paths(tmp_path):
 value,old,host,native=M.amend_command(base(tmp_path),"retry4-"+"b"*40,tmp_path/"supplement.json","c"*64,tmp_path/"support",tmp_path/"measure")
 assert value["qualification_attempt_id"]!=old; assert value["argv"][1].startswith("--attempt-id=retry4-"); assert value["argv"][3].endswith(".native.jsonl"); assert not host.exists() and not native.exists(); assert value["retry4_materializer_binding"]["predecessor_reuse_prohibited"] is True
def test_amend_rejects_reused_metric_output(tmp_path):
 b=base(tmp_path); attempt="retry4-"+"b"*40; existing=tmp_path/"measure"/(attempt+".host_metrics.jsonl"); existing.touch()
 with pytest.raises(M.MaterializeError,match="not fresh"): M.amend_command(b,attempt,tmp_path/"supplement.json","c"*64,tmp_path/"support",tmp_path/"measure")
def test_attempts_are_retry4_and_unique():
 left,right=M.fresh_attempt(),M.fresh_attempt(); assert left.startswith("tpch-qualification-retry4-") and left!=right and len(left)>=32
def test_retry3_nested_evidence_contract_needs_no_json_rewrite():
 evidence={"diagnostic":{"command":{"qualification_attempt_id":"retry3-"+"a"*40,"accepted_round_authorized":False}}}
 assert "retry3-"+"a"*40 in M.values_for_keys(evidence,{"qualification_attempt_id","attempt_id"})
 assert True not in M.values_for_keys(evidence,{"accepted_round_authorized"})
def test_missing_retry3_main_evidence_has_explicit_absence_mode():
 source=(BUNDLE/"materializer/mysql_tpch_qualification_retry4_materializer_v1.py").read_text(); assert "--retry3-expected-output" in source; assert "mysql_tpch_retry3_output_absence_evidence_v1" in source; assert "output_absent" in source and "sidecar_absent" in source
def test_materializer_never_executes_and_has_bounded_ttl():
 source=(BUNDLE/"materializer/mysql_tpch_qualification_retry4_materializer_v1.py").read_text(); assert "subprocess" not in source; assert "MIN_TTL = 300" in source and "MAX_TTL = 3600" in source; assert '"remote_execution_performed":False' in source
def test_launch_binds_exact_support_and_supplement():
 source=(BUNDLE/"materializer/mysql_tpch_qualification_retry4_materializer_v1.py").read_text(); assert M.SUPPORT_ID in source; assert M.SUPPORT_ENTRY_SHA in source; assert M.SUPPLEMENT_RENDERER_SHA in source; assert "legacy_blkio_supplement_sha256" in source
'''


def freeze(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def build() -> Path:
    content = {
        "materializer/mysql_tpch_qualification_retry4_materializer_v1.py": MATERIALIZER.encode(),
        "tests/test_mysql_tpch_qualification_retry4_materializer_v1.py": TEST.encode(),
        "builder/build_mysql_tpch_qualification_retry4_materializer_v1.py": HERE.read_bytes(),
        "templates/remote_commands.json": canonical({
            "schema_version": "mysql_tpch_qualification_retry4_remote_commands_v1",
            "materialize_with_existing_failure_evidence_argv": ["[SEALED_PYTHON]", "[THIS_BUNDLE]/materializer/mysql_tpch_qualification_retry4_materializer_v1.py", "--bundle", "[REMOTE_FF313_BUNDLE]", "--support-bundle", "[REMOTE_86232_BUNDLE]", "--retry3-command", "[SEALED_RETRY3_COMMAND_JSON]", "--retry3-evidence", "[SEALED_RETRY3_FAILURE_OR_ZERO_CREDIT_EVIDENCE_JSON]", "--supplement", "[BOOTSTRAP_ATTEMPT_ROOT]/blkio_throttle_authority.supplement.json", "--mysql-defaults-file", "[BOOTSTRAP_ATTEMPT_ROOT]/credentials/mysql.cnf", "--output-dir", "[FRESH_ABSOLUTE_RETRY4_MATERIALS_DIRECTORY]", "--valid-seconds", "1800"],
            "materialize_with_absent_retry3_output_argv": ["[SEALED_PYTHON]", "[THIS_BUNDLE]/materializer/mysql_tpch_qualification_retry4_materializer_v1.py", "--bundle", "[REMOTE_FF313_BUNDLE]", "--support-bundle", "[REMOTE_86232_BUNDLE]", "--retry3-command", "[SEALED_RETRY3_COMMAND_JSON]", "--retry3-expected-output", "[ABSENT_RETRY3_MAIN_EVIDENCE_JSON_PATH]", "--supplement", "[BOOTSTRAP_ATTEMPT_ROOT]/blkio_throttle_authority.supplement.json", "--mysql-defaults-file", "[BOOTSTRAP_ATTEMPT_ROOT]/credentials/mysql.cnf", "--output-dir", "[FRESH_ABSOLUTE_RETRY4_MATERIALS_DIRECTORY]", "--valid-seconds", "1800"],
            "then_execute": "[FRESH_ABSOLUTE_RETRY4_MATERIALS_DIRECTORY]/run_retry4.sh",
            "json_manual_edit_required": False,
            "materializer_executes_workload": False,
            "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
            "postgresql_mutation_prohibited": True, "accepted_round_authorized": False,
        }),
    }
    files = {name: sha_bytes(payload) for name, payload in sorted(content.items())}
    unsigned = {
        "schema_version": "mysql_tpch_qualification_retry4_materializer_bundle_v1",
        "status": "PASS_IMMUTABLE_LOCAL_MATERIALIZER_NOT_LAUNCHED",
        "immutable": True, "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "target_bundle_id": TARGET_ID, "target_manifest_sha256": TARGET_MANIFEST_SHA,
        "support_collector_bundle_id": SUPPORT_ID,
        "support_collector_manifest_sha256": SUPPORT_MANIFEST_SHA,
        "support_collector_entry_sha256": SUPPORT_ENTRY_SHA,
        "supplement_renderer_sha256": SUPPLEMENT_RENDERER_SHA,
        "retry3_contract_input_required": True,
        "retry3_sealed_evidence_or_output_absence_proof_required": True,
        "retry4_fresh_attempt_generated": True,
        "fresh_output_paths_required": True,
        "authority_ttl_range_seconds": [300, 3600],
        "manual_json_edit_required": False,
        "materializer_executes_workload": False,
        "generated_launch_script": True,
        "formal_launch_authorized": False, "accepted_round_authorized": False,
        "accepted_rounds_credit": 0, "remote_actions": 0, "files": files,
    }
    bundle_id = sha_bytes(canonical(unsigned)); manifest = dict(unsigned, bundle_id=bundle_id)
    final = OUT / bundle_id
    if final.exists():
        if sha256(final / "bundle_manifest.json") != sha_bytes(canonical(manifest)):
            raise RuntimeError("existing bundle differs")
        return final
    OUT.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".retry4-", dir=OUT))
    try:
        for name, payload in content.items():
            path = stage / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(payload)
        payload = canonical(manifest); (stage / "bundle_manifest.json").write_bytes(payload)
        (stage / "bundle_manifest.json.sha256").write_text(sha_bytes(payload)+"  bundle_manifest.json\n",encoding="ascii")
        freeze(stage); os.replace(stage, final)
    finally:
        if stage.exists(): shutil.rmtree(stage)
    return final


if __name__ == "__main__":
    bundle = build()
    print(json.dumps({"bundle": str(bundle), "bundle_id": bundle.name,
                      "manifest_sha256": sha256(bundle / "bundle_manifest.json"),
                      "manual_json_edit_required": False,
                      "remote_actions": 0}, sort_keys=True))

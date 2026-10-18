#!/usr/bin/env python3
"""Build an immutable, offline XP v13 bootstrap/adopt output-split materializer."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


ROOT = Path(__file__).resolve().parent
HERE = Path(__file__).resolve()
OUT = ROOT / "artifacts/mysql_xp_compute00_output_collision_materials_successor_v14"
DATA_BUNDLE_ID = "3b91c974bb07539e2b0927401c8bf30efcb43e44ce57e8e297a0cb85aff560dd"
DATA_MANIFEST_SHA = "5f436c19d4431fd952eb7a2ac42e12cc6014d8785cc6a9e01655a6eac58df552"
OLD_MATERIALS_SHA = "4750cd854cf63c64579440a9e4c24f8ba4aee7b91ad51fdff3889e7ed575660d"
OLD_SPEC_SHA = "520fc18d0bd7227e7830047e39b7e11549debac5888b8b00f42f4ffd2a398144"
OLD_PLAN_SHA = "65b19d13d0f2f0d69d24d13658f18b58642b864eadf3598479c3f7911353544b"
OLD_INSTALL_SHA = "fff5efd68a793e8f49059368d4d47037461aebb964c0ae06d4b6088e2f78b1c5"
COPY_AUTHORITY_SHA = "eb379cd4417c5d462ee687e6501005e6a2d24daca2a203bd002d3106d3a87b06"
ATTEMPT = "xp-formal-qual-20260803T132047Z-77435774a1e56e8986285441"


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                       allow_nan=False) + "\n").encode()


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


MATERIALIZER = r'''#!/usr/bin/env python3
"""Offline-only reconciler for the XP v13 bootstrap/final-adopt output collision."""
from __future__ import annotations
import argparse, copy, hashlib, json, os, stat
from pathlib import Path

DATA_BUNDLE_ID="3b91c974bb07539e2b0927401c8bf30efcb43e44ce57e8e297a0cb85aff560dd"
DATA_MANIFEST_SHA="5f436c19d4431fd952eb7a2ac42e12cc6014d8785cc6a9e01655a6eac58df552"
OLD_MATERIALS_SHA="4750cd854cf63c64579440a9e4c24f8ba4aee7b91ad51fdff3889e7ed575660d"
OLD_SPEC_SHA="520fc18d0bd7227e7830047e39b7e11549debac5888b8b00f42f4ffd2a398144"
OLD_PLAN_SHA="65b19d13d0f2f0d69d24d13658f18b58642b864eadf3598479c3f7911353544b"
OLD_INSTALL_SHA="fff5efd68a793e8f49059368d4d47037461aebb964c0ae06d4b6088e2f78b1c5"
COPY_AUTHORITY_SHA="eb379cd4417c5d462ee687e6501005e6a2d24daca2a203bd002d3106d3a87b06"
ATTEMPT="xp-formal-qual-20260803T132047Z-77435774a1e56e8986285441"
PRIVATE_FILES=("private_authority/main_agent_token","private_credentials/hybench_xp_qualification.properties","private_credentials/mysql-client.cnf","private_credentials/mysql-root-bootstrap.cnf")

class Error(RuntimeError): pass
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sha_bytes(v):return hashlib.sha256(v).hexdigest()
def sha_file(p):
 d=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
def load(p):return json.loads(Path(p).read_text(encoding="utf-8"))
def output_arg(argv):
 if argv.count("--output")!=1:raise Error("stage argv must contain exactly one --output")
 return argv[argv.index("--output")+1]
def replace_output(argv,old,new):
 result=list(argv);i=result.index("--output")+1
 if result[i]!=old:raise Error("predecessor bootstrap output identity differs")
 result[i]=new;return result
def write_sealed(root,name,payload):
 path=root/name
 if path.exists() or path.with_name(path.name+".sha256").exists():raise Error("refusing to overwrite successor evidence")
 raw=canonical(payload);path.write_bytes(raw);os.chmod(path,0o444)
 side=path.with_name(path.name+".sha256");side.write_text(sha_bytes(raw)+"  "+path.name+"\n",encoding="ascii");os.chmod(side,0o444)
 return sha_bytes(raw)

def reconcile(predecessor,output_root,support_bundle_id):
 predecessor=Path(predecessor).resolve(); output_root=Path(output_root).resolve()
 expected={"materials_manifest.json":OLD_MATERIALS_SHA,"bootstrap_spec.json":OLD_SPEC_SHA,"command_plan.json":OLD_PLAN_SHA,"authorization_install_plan.json":OLD_INSTALL_SHA}
 for name,digest in expected.items():
  path=predecessor/name
  if not path.is_file() or sha_file(path)!=digest:raise Error("predecessor public material differs: "+name)
 old_manifest=load(predecessor/"materials_manifest.json")
 if old_manifest.get("attempt_id")!=ATTEMPT or old_manifest.get("bundle_id")!=DATA_BUNDLE_ID or old_manifest.get("bundle_manifest_sha256")!=DATA_MANIFEST_SHA:raise Error("predecessor attempt/data bundle binding differs")
 if old_manifest.get("formal_scope")!="mysql_only" or old_manifest.get("cross_engine_comparison_prohibited") is not True or old_manifest.get("postgresql_mutation_prohibited") is not True:raise Error("scope/non-interference contract differs")
 for relative in PRIVATE_FILES:
  path=predecessor/relative
  if not path.is_file() or sha_file(path)!=old_manifest["files"][relative]:raise Error("private dependency differs: "+relative)
  if stat.S_IMODE(path.stat().st_mode)!=0o600:raise Error("private dependency mode differs: "+relative)
 old_spec=load(predecessor/"bootstrap_spec.json");old_plan=load(predecessor/"command_plan.json")
 if old_spec["attempt"]["id"]!=ATTEMPT or old_spec["bundle"]["id"]!=DATA_BUNDLE_ID:raise Error("spec identity differs")
 old_output=old_spec["paths"]["adoption"]; attempt_root=old_spec["paths"]["attempt_root"]
 bootstrap_output=attempt_root+"/bootstrap_adoption.json"
 direct=old_plan["commands_to_run_on_compute00_after_separate_authorization"]
 host=old_plan["host_systemd_run_commands_after_separate_authorization"]
 poll=old_plan["controller_poll_after_each_systemd_run"]
 if not (output_arg(direct["bootstrap"])==output_arg(host["bootstrap"])==output_arg(poll["bootstrap"]["expected_exec_start_argv"])==old_output==output_arg(direct["adopt_readback"])):raise Error("expected predecessor output collision is absent or changed")
 spec=copy.deepcopy(old_spec);plan=copy.deepcopy(old_plan)
 spec["schema_version"]="mysql_xp_successor_qualification_bootstrap_spec_v2_output_split"
 spec["status"]="QUALIFICATION_ONLY_OUTPUT_AUTHORITY_SPLIT_NOT_LAUNCHED"
 spec["paths"]["bootstrap_adoption"]=bootstrap_output
 spec["output_authority_contract"]={"bootstrap_output":bootstrap_output,"final_adopt_output":old_output,"distinct":True,"attempt_owned":True,"overwrite_prohibited":True,"both_paths_must_be_absent_before_bootstrap":True,"bootstrap_does_not_authorize_formal_r0":True,"accepted_rounds_credit":0}
 spec["reconciliation"]={"schema_version":"mysql_xp_v13_output_collision_reconciliation_v1","support_bundle_id":support_bundle_id,"predecessor_materials_manifest_sha256":OLD_MATERIALS_SHA,"predecessor_bootstrap_spec_sha256":OLD_SPEC_SHA,"predecessor_command_plan_sha256":OLD_PLAN_SHA,"physical_copy_authority_sha256":COPY_AUTHORITY_SHA,"data_bundle_id":DATA_BUNDLE_ID,"data_bundle_manifest_sha256":DATA_MANIFEST_SHA,"completed_physical_copy_remains_valid":True,"data_copy_rerun_required":False,"data_plane_mutations":0}
 spec_sha=sha_bytes(canonical(spec))
 plan["schema_version"]="mysql_xp_successor_qualification_command_plan_v3_output_split"
 plan["status"]="PLANNED_OUTPUT_AUTHORITY_SPLIT_NOT_LAUNCHED"
 plan["spec_sha256"]=spec_sha
 plan["output_authority_gate"]={"bootstrap_output":bootstrap_output,"final_adopt_output":old_output,"distinct":True,"both_paths_must_be_absent_before_bootstrap":True,"overwrite_or_delete_prohibited":True,"failure_policy":"FAIL_CLOSED_ABANDON_ATTEMPT"}
 for container in (plan["commands_to_run_on_compute00_after_separate_authorization"],plan["host_systemd_run_commands_after_separate_authorization"]):container["bootstrap"]=replace_output(container["bootstrap"],old_output,bootstrap_output)
 expected_argv=replace_output(plan["controller_poll_after_each_systemd_run"]["bootstrap"]["expected_exec_start_argv"],old_output,bootstrap_output)
 plan["controller_poll_after_each_systemd_run"]["bootstrap"]["expected_exec_start_argv"]=expected_argv
 plan["controller_poll_after_each_systemd_run"]["bootstrap"]["expected_exec_start_argv_sha256"]=sha_bytes(canonical(expected_argv))
 if output_arg(plan["commands_to_run_on_compute00_after_separate_authorization"]["adopt_readback"])!=old_output:raise Error("final adopt output changed")
 if plan["formal_scope"]!="mysql_only" or plan["cross_engine_comparison_prohibited"] is not True or plan["postgresql_mutation_prohibited"] is not True:raise Error("plan scope/non-interference changed")
 if any(plan.get(k) is not False for k in ("formal_authorized","formal_r0_authorized","accepted_round_authorized")):raise Error("qualification plan authorization widened")
 reconciliation={"schema_version":"mysql_xp_v13_output_collision_reconciliation_authority_v1","status":"PASS_OFFLINE_OUTPUT_AUTHORITY_SPLIT_NOT_EXECUTED","attempt_id":ATTEMPT,"support_bundle_id":support_bundle_id,"data_bundle_id":DATA_BUNDLE_ID,"data_bundle_manifest_sha256":DATA_MANIFEST_SHA,"predecessor_materials_manifest_sha256":OLD_MATERIALS_SHA,"predecessor_bootstrap_spec_sha256":OLD_SPEC_SHA,"predecessor_command_plan_sha256":OLD_PLAN_SHA,"physical_copy_authority_sha256":COPY_AUTHORITY_SHA,"physical_copy_status":"PASS_COMPLETED_REUSED_WITHOUT_RERUN","bootstrap_output":bootstrap_output,"final_adopt_output":old_output,"output_paths_distinct":True,"old_colliding_bootstrap_suffix_invalidated":True,"old_final_adopt_identity_preserved":True,"runtime_payload_changed":False,"resource_envelope_changed":False,"trace_gate_changed":False,"execution_order_changed":False,"unit_names_changed":False,"authorization_expiry_changed":False,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"formal_r0_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"data_plane_actions":0}
 if output_root.exists():raise Error("refusing to overwrite output root")
 output_root.mkdir(parents=True,mode=0o755)
 try:
  install=load(predecessor/"authorization_install_plan.json")
  hashes={"authorization_install_plan.json":write_sealed(output_root,"authorization_install_plan.json",install),"bootstrap_spec.json":write_sealed(output_root,"bootstrap_spec.json",spec),"command_plan.json":write_sealed(output_root,"command_plan.json",plan),"reconciliation_authority.json":write_sealed(output_root,"reconciliation_authority.json",reconciliation)}
  successor_manifest={"schema_version":"mysql_xp_successor_qualification_materials_manifest_v3_output_split","status":"SEALED_QUALIFICATION_ONLY_OUTPUT_AUTHORITY_SPLIT_NOT_LAUNCHED","support_bundle_id":support_bundle_id,"attempt_id":ATTEMPT,"bundle_id":DATA_BUNDLE_ID,"bundle_manifest_sha256":DATA_MANIFEST_SHA,"physical_copy_authority_sha256":COPY_AUTHORITY_SHA,"completed_physical_copy_reused":True,"predecessor_materials_manifest_sha256":OLD_MATERIALS_SHA,"public_files":hashes,"external_private_dependencies":{name:old_manifest["files"][name] for name in PRIVATE_FILES},"private_materials_copied":False,"formal_gate_binding":old_manifest["formal_gate_binding"],"created_at_utc":old_manifest["created_at_utc"],"expires_at_utc":old_manifest["expires_at_utc"],"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"formal_authorized":False,"formal_r0_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"runtime_payload_changed":False,"resource_envelope_changed":False,"trace_gate_changed":False,"data_plane_actions":0}
  manifest_sha=write_sealed(output_root,"materials_manifest.json",successor_manifest)
 except Exception:
  raise
 return {"status":"PASS_SEALED_OUTPUT_AUTHORITY_SPLIT_NOT_EXECUTED","output":str(output_root),"materials_manifest_sha256":manifest_sha,"bootstrap_spec_sha256":hashes["bootstrap_spec.json"],"command_plan_sha256":hashes["command_plan.json"],"reconciliation_authority_sha256":hashes["reconciliation_authority.json"],"attempt_id":ATTEMPT,"data_bundle_id":DATA_BUNDLE_ID,"physical_copy_authority_sha256":COPY_AUTHORITY_SHA,"bootstrap_output":bootstrap_output,"final_adopt_output":old_output,"data_plane_actions":0}

def main():
 p=argparse.ArgumentParser();p.add_argument("--predecessor-materials",type=Path,required=True);p.add_argument("--output-root",type=Path,required=True);a=p.parse_args()
 support=Path(__file__).resolve().parents[1]
 result=reconcile(a.predecessor_materials,a.output_root,support.name);print(json.dumps(result,sort_keys=True))
if __name__=="__main__":main()
'''

TEST = r'''from __future__ import annotations
import hashlib, importlib.util, json, os, shutil, tempfile
from pathlib import Path

BUNDLE=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("split",BUNDLE/"materializer/mysql_xp_v13_output_collision_reconciliation_v14.py")
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
PRE=Path(os.environ.get("XP_V13_MATERIALS","/tmp/mysql-xp-3b91-materials-v13/xp-formal-qual-20260803T132047Z-77435774a1e56e8986285441"))
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2)+"\n").encode()
def load(p):return json.loads(Path(p).read_text())
def out(argv):return argv[argv.index("--output")+1]

def test_bundle_manifest_and_dependency_closure():
 m=load(BUNDLE/"bundle_manifest.json");u=dict(m);u.pop("bundle_id")
 assert hashlib.sha256(canonical(u)).hexdigest()==m["bundle_id"]==BUNDLE.name
 assert m["predecessor_data_bundle_id"]==M.DATA_BUNDLE_ID and m["physical_copy_authority_sha256"]==M.COPY_AUTHORITY_SHA
 assert m["formal_scope"]=="mysql_only" and m["cross_engine_comparison_prohibited"] and m["postgresql_mutation_prohibited"]

def test_materialized_outputs_are_distinct_and_everything_else_is_preserved():
 root=Path(tempfile.mkdtemp(prefix="xp-output-split-test-"));target=root/"materials"
 try:
  M.reconcile(PRE,target,BUNDLE.name)
  oldp=load(PRE/"command_plan.json");newp=load(target/"command_plan.json")
  oldspec=load(PRE/"bootstrap_spec.json");newspec=load(target/"bootstrap_spec.json")
  direct=newp["commands_to_run_on_compute00_after_separate_authorization"]
  assert out(direct["bootstrap"])==newspec["paths"]["bootstrap_adoption"]
  assert out(direct["adopt_readback"])==newspec["paths"]["adoption"]
  assert out(direct["bootstrap"])!=out(direct["adopt_readback"])
  assert direct["adopt_readback"]==oldp["commands_to_run_on_compute00_after_separate_authorization"]["adopt_readback"]
  assert newp["execution_order"]==oldp["execution_order"]
  for stage in ("physical_copy","jdk_publish","plan_only"):
   assert direct[stage]==oldp["commands_to_run_on_compute00_after_separate_authorization"][stage]
  assert newspec["bundle"]==oldspec["bundle"] and newspec["attempt"]==oldspec["attempt"]
  assert newspec["formal_envelope"]==oldspec["formal_envelope"]
  assert newspec["formal_gate_binding"]==oldspec["formal_gate_binding"]
  assert newspec["host_output_tmpfs"]==oldspec["host_output_tmpfs"]
  expected=newp["controller_poll_after_each_systemd_run"]["bootstrap"]["expected_exec_start_argv"]
  assert newp["controller_poll_after_each_systemd_run"]["bootstrap"]["expected_exec_start_argv_sha256"]==hashlib.sha256(canonical(expected)).hexdigest()
 finally:shutil.rmtree(root)

def test_scope_gates_credit_and_private_dependency_policy():
 root=Path(tempfile.mkdtemp(prefix="xp-output-split-test-"));target=root/"materials"
 try:
  M.reconcile(PRE,target,BUNDLE.name);m=load(target/"materials_manifest.json");p=load(target/"command_plan.json")
  assert m["formal_scope"]=="mysql_only" and m["cross_engine_comparison_prohibited"] and m["postgresql_mutation_prohibited"]
  assert not m["formal_r0_authorized"] and not m["accepted_round_authorized"] and m["accepted_rounds_credit"]==0
  assert not m["private_materials_copied"] and len(m["external_private_dependencies"])==4
  assert p["formal_scope"]=="mysql_only" and p["postgresql_mutation_prohibited"] and not p["formal_r0_authorized"]
  assert m["formal_gate_binding"]==load(PRE/"materials_manifest.json")["formal_gate_binding"]
  try:M.reconcile(PRE,target,BUNDLE.name)
  except M.Error:pass
  else:raise AssertionError("overwrite accepted")
 finally:shutil.rmtree(root)
'''

README = r'''# XP v13 output-authority split successor v14

This is an offline control/materials successor. It does not replace the v13 data
bundle and does not authorize or launch a workload. It binds the already sealed
v13 bundle, current attempt, private dependency hashes, and completed physical
copy authority. It changes only the bootstrap output authority from
`adopted_service.json` to `bootstrap_adoption.json`; final adoption remains
`adopted_service.json`.

The operator must verify both output paths are absent and the retained systemd
unit identities are unused before any execution. Any pre-existing output is a
fail-closed attempt abandonment condition. PostgreSQL mutation and cross-engine
comparison are prohibited. Formal R0 and accepted rounds remain unauthorized.
'''


def file_map(root):
    return {p.relative_to(root).as_posix(): sha_file(p) for p in sorted(root.rglob("*"))
            if p.is_file() and p.name not in ("bundle_manifest.json", "bundle_manifest.json.sha256")}


def freeze(root):
    for p in sorted(root.rglob("*"), reverse=True):
        os.chmod(p, 0o555 if p.is_dir() else 0o444)
    os.chmod(root, 0o555)


def build():
    content = {
        "builder/build_mysql_xp_compute00_output_collision_materials_successor_v14.py": HERE.read_bytes(),
        "materializer/mysql_xp_v13_output_collision_reconciliation_v14.py": MATERIALIZER.encode(),
        "tests/test_mysql_xp_v13_output_collision_reconciliation_v14.py": TEST.encode(),
        "README.md": README.encode(),
    }
    files = {name: sha_bytes(raw) for name, raw in sorted(content.items())}
    identity = {
        "schema_version": "mysql_xp_compute00_output_collision_materials_successor_bundle_v14",
        "status": "PASS_IMMUTABLE_OFFLINE_CONTROL_SUCCESSOR_NOT_EXECUTED",
        "immutable": True,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "predecessor_data_bundle_id": DATA_BUNDLE_ID,
        "predecessor_data_bundle_manifest_sha256": DATA_MANIFEST_SHA,
        "predecessor_materials_manifest_sha256": OLD_MATERIALS_SHA,
        "predecessor_bootstrap_spec_sha256": OLD_SPEC_SHA,
        "predecessor_command_plan_sha256": OLD_PLAN_SHA,
        "predecessor_authorization_install_plan_sha256": OLD_INSTALL_SHA,
        "physical_copy_authority_sha256": COPY_AUTHORITY_SHA,
        "physical_copy_reused_without_rerun": True,
        "attempt_id": ATTEMPT,
        "runtime_payload_changed": False,
        "resource_envelope_changed": False,
        "trace_gate_changed": False,
        "formal_launch_authorized": False,
        "formal_r0_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "data_plane_actions": 0,
        "files": files,
    }
    bundle_id = sha_bytes(canonical(identity)); manifest = dict(identity, bundle_id=bundle_id)
    destination = OUT / bundle_id
    if destination.exists():
        if sha_file(destination / "bundle_manifest.json") != sha_bytes(canonical(manifest)):
            raise RuntimeError("existing content-addressed destination differs")
        return destination
    OUT.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".xp-output-split-v14-", dir=OUT))
    try:
        for name, raw in sorted(content.items()):
            path = stage / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
        raw = canonical(manifest); (stage / "bundle_manifest.json").write_bytes(raw)
        (stage / "bundle_manifest.json.sha256").write_text(sha_bytes(raw) + "  bundle_manifest.json\n")
        freeze(stage); os.replace(stage, destination)
    finally:
        if stage.exists(): shutil.rmtree(stage)
    if file_map(destination) != files: raise RuntimeError("published dependency closure differs")
    return destination


if __name__ == "__main__":
    result = build()
    print(json.dumps({"status": "PASS_IMMUTABLE_OFFLINE_CONTROL_SUCCESSOR_NOT_EXECUTED", "bundle": str(result), "bundle_id": result.name, "manifest_sha256": sha_file(result / "bundle_manifest.json"), "data_plane_actions": 0}, sort_keys=True))

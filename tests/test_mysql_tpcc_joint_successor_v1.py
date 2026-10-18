import hashlib,importlib.util,json,sys,time,types
from datetime import datetime,timedelta,timezone
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/"adapter/mysql_tpcc_joint_successor_v1.py" if (ROOT/"adapter/mysql_tpcc_joint_successor_v1.py").is_file() else ROOT/"shell/mysql_tpcc_joint_successor_v1.py")
spec=importlib.util.spec_from_file_location("joint",SOURCE);J=importlib.util.module_from_spec(spec);spec.loader.exec_module(J)
def seal(p,v):
 p.write_text(json.dumps(v));Path(str(p)+".sha256").write_text(hashlib.sha256(p.read_bytes()).hexdigest()+"  "+p.name+"\n")
def closure(tmp,ttl=1800):
 ident={"database_container_id":"d"*64,"client_container_id":"c"*64};token="fresh-token"
 paths={n:tmp/(n+".json") for n in ("spec","evidence","authority","materialization")}
 seal(paths["spec"],{"attempt_token":token});seal(paths["evidence"],{"attempt_token":token,"same_live_identity":ident})
 seal(paths["authority"],{"schema_version":"mysql_tpch_same_live_bootstrap_refresh_authority_v1","status":"PASS_FRESH_SPEC_AND_EVIDENCE_VALIDATED_BY_FF313","target_bundle_id":J.FF_ID,"target_manifest_sha256":J.FF_SHA,"fresh_bootstrap_spec_sha256":J.sha(paths["spec"]),"fresh_bootstrap_evidence_sha256":J.sha(paths["evidence"]),"attempt_token":token,"same_live_identity":ident,"expires_at_utc":(datetime.now(timezone.utc)+timedelta(seconds=ttl)).isoformat(),"accepted_round_authorized":False})
 seal(paths["materialization"],{"same_live_identity":ident,"paths":{"bootstrap_spec":str(paths["spec"].resolve()),"bootstrap_evidence":str(paths["evidence"].resolve())}});return paths
def project_root():
 return next(x for x in (ROOT,*ROOT.parents) if (x/"artifacts/mysql_tpch_sql_native_percentile_successor_v1").is_dir())
def test_package_safe_import_is_transient_and_restores_path(tmp_path):
 p=tmp_path/"m.py";p.write_text("from dataclasses import dataclass\n@dataclass\nclass X:\n x:int\n")
 before=list(sys.path);m=J.import_file(p,"joint_package_probe");assert m.X(3).x==3 and "joint_package_probe" not in sys.modules and sys.path==before
def test_ff313_exact_sibling_private_package_has_no_residue_or_path_hijack():
 project=project_root();ff=project/"artifacts/mysql_tpch_sql_native_percentile_successor_v1"/J.FF_ID;absolute="mysql_tpch_client_immutable_identity_v1";fake=types.ModuleType(absolute);fake.__file__="/tmp/attacker.py";old=sys.modules.get(absolute);sys.modules[absolute]=fake;before_path=list(sys.path);before_private={k for k in sys.modules if k.startswith("_ra_ff313_")}
 try:
  m=J.import_ff313_authority(ff/J.FF_AUTH_REL,"ignored");assert callable(m.validate_spec) and sys.modules[absolute] is fake and sys.path==before_path and {k for k in sys.modules if k.startswith("_ra_ff313_")}==before_private
 finally:
  if old is None:sys.modules.pop(absolute,None)
  else:sys.modules[absolute]=old
def test_fresh_refresh_closure_rejects_stale_ttl(tmp_path):
 p=closure(tmp_path,100)
 with pytest.raises(J.Error,match="closure"):J.refresh_closure(p["spec"],p["evidence"],p["authority"],p["materialization"])
def test_current_adapter_replaces_only_validated_attempt_constant():
 text=SOURCE.read_text();assert 'mod.TPCH_ATTEMPT=c["attempt_token"]' in text and "refresh_closure" in text and '["--valid-seconds","1800"]' in text and "mod.load_module=import_current_dependency" in text and "mod.import_file=import_ff313_authority" in text
def test_current_loader_real_two_argument_ff_and_one_argument_da455_shapes_leave_no_residue():
 project=project_root();current=project/"artifacts/mysql_tpcc_v16_da455_current_args_successor_v1"/J.ARGS_ID;mod=J.import_current_args(current);ff=project/"artifacts/mysql_tpch_sql_native_percentile_successor_v1"/J.FF_ID;ffm=json.loads((ff/"bundle_manifest.json").read_text());collector=project/"artifacts/mysql_tpch_native_metric_completion_successor_v1"/J.COLLECTOR_ID;before=list(sys.path);watched=set(J.COLLECTOR_EXACT_FILES)|{"runtime_tree_content_identity_v3","mysql_distributed_workload_metric_adapter_v1","mysql_tpch_client_immutable_identity_v1"};prior={key:sys.modules.get(key) for key in watched}
 authority=mod.load_module(ff/J.FF_AUTH_REL,ffm["files"]);assert callable(authority.validate_bootstrap_evidence)
 core=mod.load_module(collector/J.COLLECTOR_CORE_REL);assert callable(core.validate_command_spec) and callable(core.validate_main_agent_authority) and sys.path==before
 for key,value in prior.items():assert sys.modules.get(key) is value
def test_current_loader_rejects_wrong_shapes_and_paths(tmp_path):
 p=tmp_path/"unknown.py";p.write_text("x=1\n")
 with pytest.raises(J.Error,match="not authorized"):J.import_current_dependency(p)
def test_fourflow_binds_unique_tokens_fresh_outputs_and_final_receipt():
 text=SOURCE.read_text()
 for term in ("for x in tokens})!=4","len(set(metrics))!=4","producer-complete-v1.json","joint_four_flow.completion.json","inner_completion_sha256","flow_output_sha256","completion_sha256","all_input_sha256",'set -eu\\n/bin/sh '):assert term in text
def test_original_cpu_device_checks_remain_bound():
 project=project_root()
 src=(project/"artifacts/mysql_tpcc_v16_da455_current_args_successor_v1"/J.ARGS_ID/"materializer/mysql_tpcc_v16_current_args_materializer_v1.py").read_text()
 assert 'database.get("cpuset_cpus") != TPCC_DB_CPUSET' in src and 'database.get("device") != "/dev/nvme3n1"' in src and 'client.get("cpuset_cpus") != TPCC_CLIENT_CPUSET' in src
 mod=J.import_current_args(project/"artifacts/mysql_tpcc_v16_da455_current_args_successor_v1"/J.ARGS_ID)
 assert mod._joint_package_safe_patch_applied is True and mod.TPCC_DB_CPUSET and mod.TPCC_CLIENT_CPUSET

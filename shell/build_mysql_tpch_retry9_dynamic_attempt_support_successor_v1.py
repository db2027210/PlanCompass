#!/usr/bin/env python3
"""Build a fail-closed fresh-attempt-aware Retry9 support successor."""
from __future__ import annotations
import hashlib,json,os,secrets,shutil,stat,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve()
OLD_ID="a070e44ed4ca1acb0f5b4e0fe03386c6fc0dd43736d0f477cdd17a006bd465b6";OLD_SHA="a529c3aa35884314893afdd50b093a251dbf31c6a83d9ff797df052c960a7dce"
OLD=ROOT/"artifacts/mysql_tpch_deadline_shutdown_collector_successor_v1"/OLD_ID
OUT=ROOT/"artifacts/mysql_tpch_retry9_dynamic_attempt_support_successor_v1"
ENTRY="qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py"
FAILED="tpch-v2-20260803t085536z-b235retry95c56007ba48c"
INVALIDATION="authority/mysql_tpch_retry9_5c560_static_attempt_binding_failure_v1.json"
BUILDER="builder/build_mysql_tpch_retry9_dynamic_attempt_support_successor_v1.py"
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sb(v):return hashlib.sha256(v).hexdigest()
def sf(p):
 d=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
def fmap(r):return {p.relative_to(r).as_posix():sf(p) for p in sorted(r.rglob("*")) if p.is_file() and p.relative_to(r).as_posix() not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
def verify():
 m=json.loads((OLD/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id",None)
 if m.get("bundle_id")!=OLD_ID or sf(OLD/"bundle_manifest.json")!=OLD_SHA or sb(canonical(u))!=OLD_ID or fmap(OLD)!=m.get("files"):raise RuntimeError("a070 closure differs")
 return m
def patch_entry(s):
 s=s.replace('CURRENT_TPCH_ATTEMPT = "tpch-v2-20260803t022500z-afbe6badff313374deadbeef"\nCURRENT_TPCH_DB_NAME = "ra_mysql_tpch_v2_tpch_v2_20260803t022500z_afbe6badff313374deadbee_db"\nCURRENT_TPCH_CLIENT_NAME = "ra_mysql_tpch_v2_tpch_v2_20260803t022500z_afbe6badff313374deadbee_client"\n','')
 old='''def verify_current_tpch_command_binding(value):
    client = value.get("client_container") or {}
    measurement = value.get("measurement_output") or {}
    expected_measurement = Path("/dev/shm/ra_mysql_tpch_v2") / CURRENT_TPCH_ATTEMPT
    if (
        client.get("name") != CURRENT_TPCH_CLIENT_NAME
        or Path(str(measurement.get("host_tmpfs_directory") or "")) != expected_measurement
        or not re.fullmatch(r"[0-9a-f]{64}", str(client.get("container_id") or ""))
    ):
        raise core.QualificationError("current TPC-H command attempt binding differs")
'''
 new='''def _tpch_container_name(attempt, role):
    if not isinstance(attempt, str) or re.fullmatch(r"tpch-v2-[A-Za-z0-9_.-]{12,140}", attempt) is None:
        raise core.QualificationError("fresh TPC-H attempt token differs")
    suffix = re.sub(r"[^a-z0-9]", "_", attempt.lower())[:48]
    return "ra_mysql_tpch_v2_%s_%s" % (suffix, role)


def verify_current_tpch_command_binding(value):
    client = value.get("client_container") or {}
    measurement = value.get("measurement_output") or {}
    measurement_path = Path(str(measurement.get("host_tmpfs_directory") or ""))
    expected_root = Path("/dev/shm/ra_mysql_tpch_v2")
    attempt = measurement_path.name
    if (
        measurement_path.parent != expected_root
        or client.get("name") != _tpch_container_name(attempt, "client")
        or not re.fullmatch(r"[0-9a-f]{64}", str(client.get("container_id") or ""))
    ):
        raise core.QualificationError("dynamic TPC-H command attempt binding differs")
    return attempt
'''
 if s.count(old)!=1:raise RuntimeError("command binding anchor differs")
 s=s.replace(old,new)
 old='''    defaults_file = Path(defaults_file).resolve(strict=True)
    expected_defaults = (
        Path("/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729")
        / "attempts" / CURRENT_TPCH_ATTEMPT / "credentials/mysql.cnf"
    )
    if defaults_file != expected_defaults:
        raise core.QualificationError("current TPC-H credential path differs")
    directory = defaults_file.parent
    directory_state = directory.stat(follow_symlinks=False)
    defaults_state = defaults_file.stat(follow_symlinks=False)
    if (
        directory_state.st_uid != 1008
        or directory_state.st_gid != 1008
        or stat.S_IMODE(directory_state.st_mode) != 0o700
        or defaults_state.st_uid != 1008
        or defaults_state.st_gid != 1008
        or stat.S_IMODE(defaults_state.st_mode) != 0o400
    ):
'''
 new='''    raw_defaults = Path(defaults_file)
    attempt = raw_defaults.parent.parent.name
    expected_defaults = (
        Path("/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729")
        / "attempts" / attempt / "credentials/mysql.cnf"
    )
    try:
        defaults_state = raw_defaults.lstat()
        directory_state = raw_defaults.parent.lstat()
        resolved_defaults = raw_defaults.resolve(strict=True)
    except OSError as exc:
        raise core.QualificationError("fresh TPC-H credential path absent") from exc
    if (raw_defaults != expected_defaults or resolved_defaults != raw_defaults
        or stat.S_ISLNK(defaults_state.st_mode) or not stat.S_ISREG(defaults_state.st_mode)
        or stat.S_ISLNK(directory_state.st_mode) or not stat.S_ISDIR(directory_state.st_mode)):
        raise core.QualificationError("fresh TPC-H credential attempt/path differs")
    defaults_file = resolved_defaults
    directory = defaults_file.parent
    if (
        directory_state.st_uid != 1008
        or directory_state.st_gid != 0
        or stat.S_IMODE(directory_state.st_mode) != 0o750
        or defaults_state.st_uid != 1008
        or defaults_state.st_gid != 0
        or stat.S_IMODE(defaults_state.st_mode) != 0o440
    ):
'''
 if s.count(old)!=1:raise RuntimeError("credential anchor differs")
 s=s.replace(old,new).replace('if scenario == "tpch_memory" and attempt_root.name != CURRENT_TPCH_ATTEMPT:\n        raise core.QualificationError("current TPC-H attempt differs")','if scenario == "tpch_memory":\n        _tpch_container_name(attempt_root.name, "database")',1)
 old='''    if runtime.get("scenario") == "tpch_memory" and core._container_name(container_row, "TPC-H database") != CURRENT_TPCH_DB_NAME:
        raise core.QualificationError("current TPC-H database attempt binding differs")
'''
 new='''    if runtime.get("scenario") == "tpch_memory" and core._container_name(container_row, "TPC-H database") != _tpch_container_name(attempt_token, "db"):
        raise core.QualificationError("dynamic TPC-H database attempt binding differs")
'''
 if s.count(old)!=1:raise RuntimeError("database binding anchor differs")
 return s.replace(old,new)
def patch_test(n,s):
 if n.endswith("test_mysql_tpch_tpcc_shared_collector_current_v1.py"):
  old='''def test_current_tpch_attempt_binding_rejects_stale_or_swapped_command():
    valid = {
        "client_container": {"name": module.CURRENT_TPCH_CLIENT_NAME, "container_id": "a" * 64},
        "measurement_output": {"host_tmpfs_directory": "/dev/shm/ra_mysql_tpch_v2/" + module.CURRENT_TPCH_ATTEMPT},
    }
    module.verify_current_tpch_command_binding(valid)
    stale = {**valid, "measurement_output": {"host_tmpfs_directory": "/dev/shm/ra_mysql_tpch_v2/stale"}}
    with pytest.raises(module.core.QualificationError):
        module.verify_current_tpch_command_binding(stale)
'''
  new='''def test_dynamic_tpch_attempt_binding_accepts_fresh_and_rejects_swaps():
    attempt = "tpch-v2-20260803t999999z-b235retry9fresh000000000001"
    valid = {"client_container": {"name": module._tpch_container_name(attempt, "client"), "container_id": "a" * 64}, "measurement_output": {"host_tmpfs_directory": "/dev/shm/ra_mysql_tpch_v2/" + attempt}}
    assert module.verify_current_tpch_command_binding(valid) == attempt
    stale = {**valid, "measurement_output": {"host_tmpfs_directory": "/dev/shm/ra_mysql_tpch_v2/tpch-v2-different-fresh-attempt"}}
    with pytest.raises(module.core.QualificationError): module.verify_current_tpch_command_binding(stale)

def test_no_static_tpch_attempt_and_dual_principal_contract_is_exact():
    text = ENTRY.read_text()
    assert "CURRENT_TPCH_ATTEMPT" not in text and "CURRENT_TPCH_DB_NAME" not in text and "CURRENT_TPCH_CLIENT_NAME" not in text
    for term in ("directory_state.st_gid != 0", "defaults_state.st_gid != 0", "0o750", "0o440", '_tpch_container_name(attempt_token, "db")'): assert term in text
'''
  if s.count(old)!=1:raise RuntimeError("current test anchor differs")
  s=s.replace(old,new)
 if n.endswith("test_mysql_tpch_inventory_retry_support_v1.py"):
  s=s.replace('attempt = tmp_path / MODULE.CURRENT_TPCH_ATTEMPT','attempt_token = "tpch-v2-test-dynamic-attempt-123456789abc"\n    attempt = tmp_path / attempt_token').replace('"attempt_token": MODULE.CURRENT_TPCH_ATTEMPT','"attempt_token": attempt_token')
 return s
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)
def build():
 m=verify();content={}
 for n in sorted(m["files"]):
  if n=="authority/build_mysql_tpch_deadline_shutdown_collector_successor_v1.py":continue
  b=(OLD/n).read_bytes()
  if n==ENTRY:b=patch_entry(b.decode()).encode()
  elif n.startswith("tests/"):b=patch_test(n,b.decode()).encode()
  content[n]=b
 content[BUILDER]=HERE.read_bytes();content[INVALIDATION]=canonical({"schema_version":"mysql_tpch_retry9_static_attempt_binding_failure_v1","status":"FAILED_ZERO_CREDIT_RECONCILED_DO_NOT_RERUN","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"failed_attempt_token":FAILED,"failure_stage":"retry9_materializer_validate_command_spec_without_static_pid","error":"current TPC-H command attempt binding differs","bootstrap_passed":True,"dual_principal_defaults_readback_passed":True,"reconcile_passed":True,"rerun_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0})
 files={n:sb(v) for n,v in sorted(content.items())};u={"schema_version":"mysql_tpch_retry9_dynamic_attempt_support_successor_bundle_v1","status":"SEALED_DYNAMIC_FRESH_ATTEMPT_AND_DUAL_PRINCIPAL_SUPPORT_NOT_EXECUTED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"predecessor_bundle_id":OLD_ID,"predecessor_manifest_sha256":OLD_SHA,"tpch_target_bundle_id":"b235d1ca300e5ad02fe77005096fc511bfbdc5d91b9601233a66a10502be5a47","fresh_attempt_derived_from_command_measurement_and_container":True,"static_bootstrap_attempt_prohibited":True,"dual_principal_defaults_file":{"uid":1008,"gid":0,"mode":"0440"},"dual_principal_defaults_parent":{"uid":1008,"gid":0,"mode":"0750"},"failed_attempt_token":FAILED,"failed_attempt_rerun_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,"files":files};bid=sb(canonical(u));manifest=dict(u,bundle_id=bid);dst=OUT/bid
 if not dst.exists():
  OUT.mkdir(parents=True,exist_ok=True);stage=Path(tempfile.mkdtemp(prefix=".dynamic-support-",dir=OUT))
  try:
   for n,b in content.items():p=stage/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
   raw=canonical(manifest);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(sb(raw)+"  bundle_manifest.json\n");freeze(stage);os.replace(stage,dst)
  finally:
   if stage.exists():shutil.rmtree(stage)
 if fmap(dst)!=files or sf(dst/"bundle_manifest.json")!=sb(canonical(manifest)):raise RuntimeError("successor closure differs")
 return dst
if __name__=="__main__":
 b=build();print(json.dumps({"bundle":str(b),"bundle_id":b.name,"manifest_sha256":sf(b/"bundle_manifest.json"),"entry_sha256":sf(b/ENTRY),"core_sha256":sf(b/"qualification/mysql_distributed_live_readback_qualification_v1.py"),"remote_actions":0},sort_keys=True))

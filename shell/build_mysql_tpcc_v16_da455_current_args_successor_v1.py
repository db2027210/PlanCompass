#!/usr/bin/env python3
"""Rebind 69df current-args materializer to da455 with unique metrics/TTL."""
from __future__ import annotations
import hashlib,json,os,shutil,stat,tempfile
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve();OLD_ID="69dfbb419adc879bcd45cb701518d3d348264a5e735e04421ae309d6349559a9";OLD_SHA="f1c342373322a99e22bacb4aedcc2efeaf5dd4bcdab8a0f290361d31128f8b65";OLD=ROOT/"artifacts/mysql_tpcc_v16_current_args_materializer_successor_v1"/OLD_ID
OLD_COLLECTOR="feb2e6ea9cd866a3fb82c5fbe9dea14a68cb2a6f73d97de0f4a615c410c8caff";OLD_COLLECTOR_SHA="2663d7b98567d5f6273c391bf62c88285ad55c82862d0c28dfffe6299428a4b5";COLLECTOR="da455246652a82bb13d63f5f1ba6ebeb2e92fd08389e3f06707e5897e12646b6";COLLECTOR_SHA="0f835d084459a2c66729327a9a4b9ed6ec070116e9873631874ac667039b34c4";COLLECTOR_PATH=ROOT/"artifacts/mysql_tpch_native_metric_completion_successor_v1"/COLLECTOR
OLD_TPCH="ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4";OLD_TPCH_SHA="af1d0facca8b33e2638c0a2af6a751bececc407d483055ca064a34308d27bb52";TPCH="b235d1ca300e5ad02fe77005096fc511bfbdc5d91b9601233a66a10502be5a47";TPCH_SHA="386f59b23f20eb7be59665c42c44fe2453512bf2112d850e12df6b398a16f439"
OUT=ROOT/"artifacts/mysql_tpcc_v16_da455_current_args_successor_v1";BUILDER="builder/build_mysql_tpcc_v16_da455_current_args_successor_v1.py";AUDIT="authority/da455_current_args_audit.json"
def canonical(v:Any)->bytes:return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sb(v:bytes)->str:return hashlib.sha256(v).hexdigest()
def sf(p:Path)->str:
 d=hashlib.sha256()
 with p.open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
def fmap(r):return {p.relative_to(r).as_posix():sf(p) for p in sorted(r.rglob("*")) if p.is_file() and p.relative_to(r).as_posix() not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
def verify():
 m=json.loads((OLD/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id",None)
 if m.get("bundle_id")!=OLD_ID or sf(OLD/"bundle_manifest.json")!=OLD_SHA or sb(canonical(u))!=OLD_ID or fmap(OLD)!=m.get("files"):raise RuntimeError("69df closure differs")
 cm=json.loads((COLLECTOR_PATH/"bundle_manifest.json").read_text())
 if cm.get("bundle_id")!=COLLECTOR or sf(COLLECTOR_PATH/"bundle_manifest.json")!=COLLECTOR_SHA:raise RuntimeError("da455 differs")
 return m
def patch(n,b):
 try:s=b.decode()
 except UnicodeDecodeError:return b
 s=s.replace(OLD_COLLECTOR,COLLECTOR).replace(OLD_COLLECTOR_SHA,COLLECTOR_SHA).replace(OLD_TPCH,TPCH).replace(OLD_TPCH_SHA,TPCH_SHA).replace("collector_feb2","collector_da455").replace("FINAL_FEB2_SHARED_COLLECTOR_BUNDLE","FINAL_DA455_SHARED_COLLECTOR_BUNDLE")
 if n.endswith("materializer/mysql_tpcc_v16_current_args_materializer_v1.py"):
  s=s.replace('parser.add_argument("--valid-seconds", type=int, default=900)','parser.add_argument("--valid-seconds", type=int, default=1800)').replace('if args.valid_seconds < 240 or args.valid_seconds > 1800:','if args.valid_seconds < 1200 or args.valid_seconds > 1800:').replace('raise MaterializationError("authority validity must be 240..1800 seconds")','raise MaterializationError("authority validity must be 1200..1800 seconds")')
  old='''    metric = {
        "A_solo": a_root / "qualification_solo.host_metrics.jsonl",
        "A_paired": a_root / "qualification_paired.host_metrics.jsonl",
        "B_solo": measurement_root / f"{attempts['B_solo']}.host_metrics.jsonl",
        "B_paired": measurement_root / f"{attempts['B_paired']}.host_metrics.jsonl",
    }
'''
  new='''    metric = {
        "A_solo": a_root / f"{attempts['A_solo']}.host_metrics.jsonl",
        "A_paired": a_root / f"{attempts['A_paired']}.host_metrics.jsonl",
        "B_solo": measurement_root / f"{attempts['B_solo']}.host_metrics.jsonl",
        "B_paired": measurement_root / f"{attempts['B_paired']}.host_metrics.jsonl",
    }
    if len({str(path) for path in metric.values()}) != 4:
        raise MaterializationError("four attempt-unique metric paths required")
    for path in metric.values():
        completion = Path(str(path) + ".producer-complete-v1.json")
        if path.exists() or path.is_symlink() or completion.exists() or completion.is_symlink():
            raise MaterializationError("fresh metric/completion output path required")
'''
  if s.count(old)!=1:raise RuntimeError("metric anchor differs")
  s=s.replace(old,new)
  old='''    full_id(service_client["container_id"], "TPC-C service client")
    if HEX64.fullmatch(service_client["inspect_config_sha256"]) is None:
        raise MaterializationError("TPC-C client inspect config identity differs")
    return dict(service_client)
'''
  new='''    full_id(service_client["container_id"], "TPC-C service client")
    if HEX64.fullmatch(service_client["inspect_config_sha256"]) is None:
        raise MaterializationError("TPC-C client inspect config identity differs")
    database_transport_name = service_db.get("name")
    if (
        not isinstance(database_transport_name, str)
        or not database_transport_name.startswith("ra_mysql_tpcc_v2_db_")
        or database_transport_name != "ra_mysql_tpcc_v2_db_" + TPCC_ATTEMPT
    ):
        raise MaterializationError("TPC-C database transport name differs")
    result = dict(service_client)
    result["database_transport_name"] = database_transport_name
    return result
'''
  if s.count(old)!=1:raise RuntimeError("database transport name anchor differs")
  s=s.replace(old,new)
  old='''        "B": base_args(tpcc_bundle, database["container_id"], snapshot_client["container_id"],
                       tpcc_defaults),
'''
  new='''        # The da455 transport accepts a Docker identifier for inspect, but its
        # anti-cross-attempt gate is intentionally name-based.  Pass the exact
        # adopted-service database name, while the snapshot/service continuity
        # above still binds the same full container ID and process start ticks.
        "B": base_args(tpcc_bundle, service_client["database_transport_name"],
                       snapshot_client["container_id"], tpcc_defaults),
'''
  if s.count(old)!=1:raise RuntimeError("B database identifier anchor differs")
  s=s.replace(old,new)
  old='''    if (
        tpch_spec.get("attempt_token") != TPCH_ATTEMPT
        or (tpch_spec.get("bundle") or {}).get("bundle_id") != TPCH_ID
        or Path(str((tpch_spec.get("bundle") or {}).get("path"))).resolve() != tpch_bundle
    ):
        raise MaterializationError("current TPC-H bootstrap spec differs")
'''
  new='''    tpch_attempt = tpch_spec.get("attempt_token")
    if (
        not isinstance(tpch_attempt, str)
        or re.fullmatch(r"tpch-v2-[A-Za-z0-9_.-]{12,140}", tpch_attempt) is None
        or (tpch_spec.get("bundle") or {}).get("bundle_id") != TPCH_ID
        or Path(str((tpch_spec.get("bundle") or {}).get("path"))).resolve() != tpch_bundle
    ):
        raise MaterializationError("current TPC-H bootstrap spec differs")
'''
  if s.count(old)!=1:raise RuntimeError("fresh TPC-H attempt anchor differs")
  s=s.replace(old,new)
  s=s.replace('f"{TPCH_ATTEMPT}-qualification_solo-{materialization_id}"','f"{tpch_attempt}-qualification_solo-{materialization_id}"',1)
  s=s.replace('f"{TPCH_ATTEMPT}-qualification_paired-{materialization_id}"','f"{tpch_attempt}-qualification_paired-{materialization_id}"',1)
  old='''    identity = module.validate_bootstrap_evidence(tpch_evidence, tpch_spec)
    tpch_runtime_path, tpch_runtime = load_bundle_json(
'''
  new='''    identity = module.validate_bootstrap_evidence(tpch_evidence, tpch_spec)
    tpch_attempt_root = Path(str((tpch_spec.get("paths") or {}).get("attempt_root") or ""))
    tpch_credentials = Path(str((tpch_spec.get("paths") or {}).get("credentials_dir") or ""))
    tpch_defaults = Path(str(tpch_spec.get("mysql_defaults_file") or ""))
    try:
        defaults_state = tpch_defaults.lstat()
        credentials_state = tpch_credentials.lstat()
        resolved_defaults = tpch_defaults.resolve(strict=True)
        resolved_credentials = tpch_credentials.resolve(strict=True)
    except OSError as exc:
        raise MaterializationError("TPC-H dual-principal defaults absent") from exc
    if (
        not tpch_attempt_root.is_absolute()
        or tpch_attempt_root.name != tpch_attempt
        or tpch_credentials != tpch_attempt_root / "credentials"
        or tpch_defaults != tpch_credentials / "mysql.cnf"
        or resolved_defaults != tpch_defaults
        or resolved_credentials != tpch_credentials
        or stat.S_ISLNK(defaults_state.st_mode)
        or not stat.S_ISREG(defaults_state.st_mode)
        or stat.S_ISLNK(credentials_state.st_mode)
        or not stat.S_ISDIR(credentials_state.st_mode)
        or (defaults_state.st_uid, defaults_state.st_gid,
            stat.S_IMODE(defaults_state.st_mode)) != (1008, 0, 0o440)
        or (credentials_state.st_uid, credentials_state.st_gid,
            stat.S_IMODE(credentials_state.st_mode)) != (1008, 0, 0o750)
        or tpch_evidence.get("attempt_token") != tpch_attempt
        or tpch_evidence.get("spec_sha256") != hashlib.sha256(canonical(tpch_spec)).hexdigest()
    ):
        raise MaterializationError("TPC-H dual-principal defaults/spec/evidence contract differs")
    tpch_runtime_path, tpch_runtime = load_bundle_json(
'''
  if s.count(old)!=1:raise RuntimeError("dual-principal defaults anchor differs")
  s=s.replace(old,new)
  old='''            "tpch_bootstrap_evidence_sha256": sha256(tpch_evidence_path),
            "tpch_manifest_sha256": sha256(tpch_manifest_path),
'''
  new='''            "tpch_bootstrap_evidence_sha256": sha256(tpch_evidence_path),
            "tpch_defaults_sha256": sha256(tpch_defaults),
            "tpch_defaults_identity": {"uid": 1008, "gid": 0, "mode": "0440"},
            "tpch_credentials_directory_identity": {"uid": 1008, "gid": 0, "mode": "0750"},
            "tpch_manifest_sha256": sha256(tpch_manifest_path),
'''
  if s.count(old)!=1:raise RuntimeError("receipt defaults binding anchor differs")
  s=s.replace(old,new)
 if n.endswith("templates/mysql_tpcc_v16_current_args_materializer_invocation_v1.json"):s=s.replace('"900"','"1800"').replace('"authority_ttl_minimum_seconds": 240','"authority_ttl_minimum_seconds": 1200')
 if n.endswith("tests/test_mysql_tpcc_v16_current_args_materializer_v1.py"):
  s=s.replace("artifacts/mysql_tpch_inventory_retry_support_v1/", "artifacts/mysql_tpch_native_metric_completion_successor_v1/")
  s=s.replace("artifacts/mysql_tpch_sql_native_percentile_successor_v1/", "artifacts/mysql_tpch_deadline_shutdown_successor_v1/")
  s=s.replace('    service["client"].update({\n','    service["database"]["name"] = "ra_mysql_tpcc_v2_db_" + m.TPCC_ATTEMPT\n    service["client"].update({\n',1)
  s += '''\n\ndef test_database_counter_transport_uses_exact_adopted_database_name():\n    source = SOURCE.read_text()\n    assert 'database_transport_name != "ra_mysql_tpcc_v2_db_" + TPCC_ATTEMPT' in source\n    assert 'base_args(tpcc_bundle, service_client["database_transport_name"]' in source\n    assert 'database["container_id"], snapshot_client["container_id"]' not in source\n    assert 'tpch_attempt = tpch_spec.get("attempt_token")' in source\n    assert 'tpch_spec.get("attempt_token") != TPCH_ATTEMPT' not in source\n\ndef test_b235_dual_principal_defaults_contract_is_exact_and_non_mutating():\n    source = SOURCE.read_text()\n    for term in ('tpch_defaults.lstat()', 'tpch_credentials.lstat()', '(1008, 0, 0o440)', '(1008, 0, 0o750)', 'tpch_attempt_root.name != tpch_attempt', 'tpch_evidence.get("spec_sha256") != hashlib.sha256(canonical(tpch_spec)).hexdigest()', '"tpch_defaults_sha256": sha256(tpch_defaults)'):\n        assert term in source\n    region = source[source.index('tpch_attempt_root = Path'):source.index('tpch_runtime_path, tpch_runtime')]\n    assert 'chmod(' not in region and 'chown(' not in region\n'''
 return s.encode()
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)
def build():
 old=verify();content={n:patch(n,(OLD/n).read_bytes()) for n in sorted(old["files"]) if n!="builder/build_mysql_tpcc_v16_current_args_materializer_successor_v1.py" and not n.startswith("authority/collector_feb2/")};cm=(COLLECTOR_PATH/"bundle_manifest.json").read_bytes();cms=(COLLECTOR_PATH/"bundle_manifest.json.sha256").read_bytes();content["authority/collector_da455/bundle_manifest.json"]=cm;content["authority/collector_da455/bundle_manifest.json.sha256"]=cms;content[BUILDER]=HERE.read_bytes();content[AUDIT]=canonical({"schema_version":"mysql_tpcc_v16_da455_current_args_audit_v1","status":"PASS_DA455_REBIND_AND_UNIQUE_OUTPUTS_ZERO_CREDIT","predecessor_bundle_id":OLD_ID,"predecessor_manifest_sha256":OLD_SHA,"collector_bundle_id":COLLECTOR,"collector_manifest_sha256":COLLECTOR_SHA,"all_four_metric_paths_attempt_unique":True,"native_completion_paths_fresh_required":True,"authority_ttl_range_seconds":[1200,1800],"old_feb2_evidence_accepted_by_da455_gate":False,"failed_tokens_or_outputs_reused":False,"systemd_219_completion_margin_required":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0})
 files={n:sb(v) for n,v in sorted(content.items())};u={"schema_version":"mysql_tpcc_v16_da455_current_args_successor_bundle_v5","status":"SEALED_LOCAL_DA455_B235_DUAL_PRINCIPAL_DEFAULTS_EXACT_DATABASE_TRANSPORT_NOT_EXECUTED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"predecessor_bundle_id":OLD_ID,"predecessor_manifest_sha256":OLD_SHA,"collector_bundle_id":COLLECTOR,"collector_manifest_sha256":COLLECTOR_SHA,"tpch_bundle_id":TPCH,"tpch_manifest_sha256":TPCH_SHA,"old_ff313_continuity_prohibited":True,"all_four_metric_paths_attempt_unique":True,"native_completion_paths_fresh_required":True,"authority_ttl_range_seconds":[1200,1800],"database_counter_transport_identifier":"exact_adopted_service_database_name","database_container_id_and_start_ticks_continuity_required":True,"tpch_attempt_binding":"sealed_current_b235_bootstrap_spec_evidence_and_dual_principal_defaults","tpch_defaults_file_identity":{"uid":1008,"gid":0,"mode":"0440"},"tpch_credentials_directory_identity":{"uid":1008,"gid":0,"mode":"0750"},"tpch_defaults_regular_non_symlink_required":True,"tpch_defaults_attempt_path_exact_required":True,"credential_chmod_or_chown_by_materializer_prohibited":True,"stale_tpch_attempt_constant_prohibited":True,"database_counter_timeout_fail_closed_unchanged":True,"database_counter_missing_sample_fail_closed_unchanged":True,"typed_transaction_l2_l3_required_before_registry":True,"old_feb2_collector_accepted":False,"failed_tokens_or_outputs_reused":False,"manual_json_edit_required":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,"files":files};bid=sb(canonical(u));m=dict(u,bundle_id=bid);f=OUT/bid
 if f.exists():
  if sf(f/"bundle_manifest.json")!=sb(canonical(m)):raise RuntimeError("existing differs")
  return f
 OUT.mkdir(parents=True,exist_ok=True);st=Path(tempfile.mkdtemp(prefix=".da455-current-args-",dir=OUT))
 try:
  for n,v in content.items():p=st/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
  raw=canonical(m);(st/"bundle_manifest.json").write_bytes(raw);(st/"bundle_manifest.json.sha256").write_text(sb(raw)+"  bundle_manifest.json\n");freeze(st);os.replace(st,f)
 finally:
  if st.exists():shutil.rmtree(st)
 if fmap(f)!=files:raise RuntimeError("published closure differs")
 for p in (f,*f.rglob("*")):
  if p.is_symlink() or stat.S_IMODE(p.stat().st_mode)&0o222:raise RuntimeError("mutable successor")
 return f
if __name__=="__main__":
 b=build();print(json.dumps({"bundle":str(b),"bundle_id":b.name,"manifest_sha256":sf(b/"bundle_manifest.json"),"materializer_sha256":sf(b/"materializer/mysql_tpcc_v16_current_args_materializer_v1.py"),"remote_actions":0},sort_keys=True))

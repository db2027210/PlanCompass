#!/usr/bin/env python3
"""Seal TPC-C v11 from v10 with inner-datadir and credential successors only."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, os, shutil, stat, sys, tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BASE_ID="2185b4b072121e9ab8d6bb6ed4ee0da0e9ebde955ca6a92e5a20f416287597fe"
BASE=ROOT/"artifacts/mysql_tpcc_typed_distributed_successor_v10"/BASE_ID
OUT=ROOT/"artifacts/mysql_tpcc_typed_distributed_successor_v11"
SERVICE=ROOT/"shell/mysql_tpcc_node04_adopted_service_v2.py"
SERVICE_V1=ROOT/"shell/mysql_tpcc_node04_adopted_service_v1.py"
MATERIALIZER=ROOT/"shell/mysql_tpcc_config_materialize_v2.py"
MATERIALIZER_V1=ROOT/"shell/mysql_tpcc_config_materialize_v1.py"
PHASE_MATERIALIZER=ROOT/"shell/mysql_tpcc_materialize_phase_command_v2.py"
BLKIO_HELPER=ROOT/"shell/mysql_tpcc_blkio_v1.py"
MEASUREMENT_BRIDGE_HELPER=ROOT/"shell/mysql_tpcc_measurement_bridge_v1.py"
BENCHBASE_PLUGIN=ROOT/"goal_20260718/benchbase/config/plugin.xml"
INVALIDATION=ROOT/"preflight/mysql_tpcc_2185b4_v10_inner_datadir_invalidation_20260731.json"
INVALIDATED_784=ROOT/"preflight/mysql_tpcc_v11_784a955_closure_invalidation_20260731.json"
INVALIDATED_9A=ROOT/"preflight/mysql_tpcc_v11_9a04d8_credential_readback_invalidation_20260731.json"
INVALIDATED_3C83=ROOT/"preflight/mysql_tpcc_v11_3c83_attempt_root_mode_invalidation_20260731.json"
INVALIDATED_52BAFA=ROOT/"preflight/mysql_tpcc_v11_52bafa_plan_stat_import_invalidation_20260731.json"
INVALIDATED_9B3=ROOT/"preflight/mysql_tpcc_v11_9b3_blkio_v1_invalidation_20260731.json"
INVALIDATED_B35=ROOT/"preflight/mysql_tpcc_v11_b35_start_output_path_invalidation_20260731.json"
INVALIDATED_1954_ID="1954fa4bfd4401a9026638926dee7690c0f7c44c1b9628380dffad2b8807d51c"
INVALIDATED_1954_BUNDLE=OUT/INVALIDATED_1954_ID
INVALIDATED_1954=ROOT/"preflight/mysql_tpcc_v11_1954_prefault_v1_adopt_invalidation_20260731.json"
INVALIDATED_0068_ID="0068f06905d2f9c3fa71a8ecd573ed597846702e1d7733466c9c52c06a395b35"
INVALIDATED_0068_BUNDLE=OUT/INVALIDATED_0068_ID
INVALIDATED_0068=ROOT/"preflight/mysql_tpcc_v11_0068_prefault_cgroup_v1_invalidation_20260731.json"
INVALIDATED_48ABD_ID="48abd6390d7f0d87f8c237830e0dfcbef7f9a7218ca2a42c3ec0cef634851344"
INVALIDATED_48ABD_BUNDLE=OUT/INVALIDATED_48ABD_ID
INVALIDATED_48ABD=ROOT/"preflight/mysql_tpcc_v11_48abd_prefault_absent_target_invalidation_20260731.json"
INVALIDATED_7EC89_ID="7ec89c2ea72b47d2798efacf106560b34e3308eda64c12a5e84555a212d4bf17"
INVALIDATED_7EC89_BUNDLE=OUT/INVALIDATED_7EC89_ID
INVALIDATED_7EC89=ROOT/"preflight/mysql_tpcc_v11_7ec89_phase_v2_identity_invalidation_20260731.json"
INVALIDATED_ACB45_ID="acb45d8886729d8fd389197ba1ba3b21cdc03ceeebc0690f7e87fb5111638d84"
INVALIDATED_ACB45_BUNDLE=OUT/INVALIDATED_ACB45_ID
INVALIDATED_ACB45=ROOT/"preflight/mysql_tpcc_v11_acb45_benchbase_classpath_invalidation_20260731.json"
INVALIDATED_45ADA_ID="45ada2d5a371b96f8349b4c69b458f4cc020322da94f0a3081cb4a9f423484f1"
INVALIDATED_45ADA_BUNDLE=OUT/INVALIDATED_45ADA_ID
INVALIDATED_45ADA=ROOT/"preflight/mysql_tpcc_v11_45ada_prefault_plugin_destination_invalidation_20260731.json"
INVALIDATED_D828_ID="d82884f3ed79ef1491beb0a405df4b7933ec7142b5a7612dda035f4849895fdf"
INVALIDATED_D828_BUNDLE=OUT/INVALIDATED_D828_ID
INVALIDATED_D828=ROOT/"preflight/mysql_tpcc_v11_d828_client_user_config_access_invalidation_20260731.json"
INVALIDATED_C58_ID="c58caf2abc6fab87b9ca73b7e44e565ee87d8f75866a369b806f43c4ddce37dc"
INVALIDATED_C58_BUNDLE=OUT/INVALIDATED_C58_ID
INVALIDATED_C58=ROOT/"preflight/mysql_tpcc_v11_c58_measurement_tmpfs_owner_invalidation_20260731.json"
INVALIDATED_96E0_ID="96e0bde80a06c85c56417e465b100b839b99f7d2c53b881e12f88bb0d74d4033"
INVALIDATED_96E0_BUNDLE=OUT/INVALIDATED_96E0_ID
INVALIDATED_96E0=ROOT/"preflight/mysql_tpcc_v11_96e0_config_owner_invalidation_20260731.json"
INVALIDATED_5188_ID="51886595b82626501afcbd19016c586f3e3a400f5ea8f66af468bd1a6f85ea52"
INVALIDATED_5188_BUNDLE=OUT/INVALIDATED_5188_ID
INVALIDATED_5188=ROOT/"preflight/mysql_tpcc_v11_5188_config_materializer_interface_invalidation_20260731.json"
INVALIDATED_D464_ID="d464e4839501810a91608511403b48ec049fa5f5079b52af1386bcbd870b2e8d"
INVALIDATED_D464_BUNDLE=OUT/INVALIDATED_D464_ID
INVALIDATED_D464=ROOT/"preflight/mysql_tpcc_v11_d464_qualification_terminal_gate_invalidation_20260731.json"
INVALIDATED_1C55_ID="1c55eda363e52e23ee49bda922c18d0e3a907b66867074d5326b42e0838d4eb8"
INVALIDATED_1C55_BUNDLE=OUT/INVALIDATED_1C55_ID
INVALIDATED_1C55=ROOT/"preflight/mysql_tpcc_v11_1c55_nonfinite_terminal_json_invalidation_20260731.json"
INVALIDATED_0625_ID="0625ad06f3742df414f4138125b10b4e4f536ca568141ec7a288c3d096042bcb"
INVALIDATED_0625_BUNDLE=OUT/INVALIDATED_0625_ID
INVALIDATED_0625=ROOT/"preflight/mysql_tpcc_v11_0625_root_phase_sudo_invalidation_20260731.json"

class BuildError(RuntimeError): pass
def canonical(v): return (json.dumps(v,ensure_ascii=True,sort_keys=True,indent=2)+"\n").encode()
def sha(p):
 h=hashlib.sha256()
 with Path(p).open("rb") as s:
  for b in iter(lambda:s.read(1048576),b""):h.update(b)
 return h.hexdigest()
def load(p): return json.loads(Path(p).read_text())
def files(root):
 return {
  relative: sha(p)
  for p in sorted(root.rglob("*"))
  if p.is_file()
  for relative in [p.relative_to(root).as_posix()]
  if relative not in {"bundle_manifest.json", "bundle_manifest.json.sha256"}
 }
def helper():
 p=ROOT/"shell/build_mysql_tpcc_distributed_successor_v5.py";s=importlib.util.spec_from_file_location("v10",p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m._v4()
def verify(bundle):
 m=load(bundle/"bundle_manifest.json");u=dict(m);bid=u.pop("bundle_id",None)
 if bid!=bundle.name or hashlib.sha256(canonical(u)).hexdigest()!=bid or m.get("schema_version")!="mysql_tpcc_typed_distributed_successor_bundle_v11" or files(bundle)!=m.get("files") or m.get("container_readonly_tree_modes")!={"directory":"0555","regular_file":"0444","executable_file":"0555"}:raise BuildError("v11 closure differs")
 for path in (bundle,*sorted(bundle.rglob("*"))):
  if path.is_symlink():raise BuildError("v11 closure symlink differs")
  mode=stat.S_IMODE(path.stat().st_mode)
  if (path.is_dir() and mode!=0o555) or (path.is_file() and mode not in (0o444,0o555)):raise BuildError("v11 container-readable mode differs")
 return m
def freeze_for_container(root):
 for path in sorted(root.rglob("*"),reverse=True):
  if path.is_symlink():raise BuildError("v11 closure symlink prohibited")
  if path.is_dir():os.chmod(path,0o555)
  elif path.is_file():os.chmod(path,0o555 if stat.S_IMODE(path.stat().st_mode)&0o111 else 0o444)
 os.chmod(root,0o555)
def build(output_root=OUT):
 if not BASE.is_dir() or any(p.is_symlink() or not p.is_file() for p in (SERVICE,SERVICE_V1,MATERIALIZER,MATERIALIZER_V1,PHASE_MATERIALIZER,BLKIO_HELPER,MEASUREMENT_BRIDGE_HELPER,BENCHBASE_PLUGIN,INVALIDATION,INVALIDATED_784,INVALIDATED_9A,INVALIDATED_3C83,INVALIDATED_52BAFA,INVALIDATED_9B3,INVALIDATED_B35,INVALIDATED_1954,INVALIDATED_1954_BUNDLE/"bundle_manifest.json",INVALIDATED_0068,INVALIDATED_0068_BUNDLE/"bundle_manifest.json",INVALIDATED_48ABD,INVALIDATED_48ABD_BUNDLE/"bundle_manifest.json",INVALIDATED_7EC89,INVALIDATED_7EC89_BUNDLE/"bundle_manifest.json",INVALIDATED_ACB45,INVALIDATED_ACB45_BUNDLE/"bundle_manifest.json",INVALIDATED_45ADA,INVALIDATED_45ADA_BUNDLE/"bundle_manifest.json",INVALIDATED_D828,INVALIDATED_D828_BUNDLE/"bundle_manifest.json",INVALIDATED_C58,INVALIDATED_C58_BUNDLE/"bundle_manifest.json",INVALIDATED_96E0,INVALIDATED_96E0_BUNDLE/"bundle_manifest.json",INVALIDATED_5188,INVALIDATED_5188_BUNDLE/"bundle_manifest.json",INVALIDATED_D464,INVALIDATED_D464_BUNDLE/"bundle_manifest.json",INVALIDATED_1C55,INVALIDATED_1C55_BUNDLE/"bundle_manifest.json",INVALIDATED_0625,INVALIDATED_0625_BUNDLE/"bundle_manifest.json")):raise BuildError("v11 inputs absent")
 if sha(BENCHBASE_PLUGIN)!="a770865560e0d1044c98a2954bacfd3d0c58ed3e956d3e783b2e830c7951d7e2":raise BuildError("production BenchBase plugin differs")
 invalidated_1954=load(INVALIDATED_1954)
 if invalidated_1954.get("bundle_id")!=INVALIDATED_1954_ID or invalidated_1954.get("bundle_manifest_sha256")!=sha(INVALIDATED_1954_BUNDLE/"bundle_manifest.json") or invalidated_1954.get("accepted_rounds_credit")!=0 or invalidated_1954.get("status")!="INVALIDATED_DO_NOT_LAUNCH":raise BuildError("1954 prefault invalidation differs")
 invalidated_0068=load(INVALIDATED_0068)
 if invalidated_0068.get("bundle_id")!=INVALIDATED_0068_ID or invalidated_0068.get("bundle_manifest_sha256")!=sha(INVALIDATED_0068_BUNDLE/"bundle_manifest.json") or invalidated_0068.get("accepted_rounds_credit")!=0 or invalidated_0068.get("status")!="INVALIDATED_DO_NOT_LAUNCH":raise BuildError("0068 prefault cgroup-v1 invalidation differs")
 invalidated_48abd=load(INVALIDATED_48ABD)
 if invalidated_48abd.get("bundle_id")!=INVALIDATED_48ABD_ID or invalidated_48abd.get("bundle_manifest_sha256")!=sha(INVALIDATED_48ABD_BUNDLE/"bundle_manifest.json") or invalidated_48abd.get("accepted_rounds_credit")!=0 or invalidated_48abd.get("status")!="INVALIDATED_DO_NOT_LAUNCH":raise BuildError("48abd absent-target invalidation differs")
 invalidated_7ec89=load(INVALIDATED_7EC89)
 if invalidated_7ec89.get("bundle_id")!=INVALIDATED_7EC89_ID or invalidated_7ec89.get("bundle_manifest_sha256")!=sha(INVALIDATED_7EC89_BUNDLE/"bundle_manifest.json") or invalidated_7ec89.get("accepted_rounds_credit")!=0 or invalidated_7ec89.get("status")!="INVALIDATED_DO_NOT_LAUNCH":raise BuildError("7ec89 phase-v2 invalidation differs")
 invalidated_acb45=load(INVALIDATED_ACB45)
 if invalidated_acb45.get("bundle_id")!=INVALIDATED_ACB45_ID or invalidated_acb45.get("bundle_manifest_sha256")!=sha(INVALIDATED_ACB45_BUNDLE/"bundle_manifest.json") or invalidated_acb45.get("accepted_rounds_credit")!=0 or invalidated_acb45.get("status")!="INVALIDATED_DO_NOT_LAUNCH" or invalidated_acb45.get("phase_ledger_pass_written") is not False:raise BuildError("acb45 BenchBase classpath invalidation differs")
 invalidated_45ada=load(INVALIDATED_45ADA)
 if invalidated_45ada.get("bundle_id")!=INVALIDATED_45ADA_ID or invalidated_45ada.get("bundle_manifest_sha256")!=sha(INVALIDATED_45ADA_BUNDLE/"bundle_manifest.json") or invalidated_45ada.get("accepted_rounds_credit")!=0 or invalidated_45ada.get("status")!="INVALIDATED_DO_NOT_LAUNCH" or invalidated_45ada.get("phase_ledger_pass_written") is not False or invalidated_45ada.get("prefaulted_target_mount_destinations")!=["/measurement/config","/opt/mysql-tpcc-bundle","/opt/mysql-tpcc-config","/opt/mysql-tpcc-python"] or invalidated_45ada.get("quiet_window_all_zero") is not True:raise BuildError("45ada prefault plugin destination invalidation differs")
 invalidated_d828=load(INVALIDATED_D828)
 if invalidated_d828.get("bundle_id")!=INVALIDATED_D828_ID or invalidated_d828.get("bundle_manifest_sha256")!=sha(INVALIDATED_D828_BUNDLE/"bundle_manifest.json") or invalidated_d828.get("accepted_rounds_credit")!=0 or invalidated_d828.get("status")!="INVALIDATED_DO_NOT_LAUNCH" or invalidated_d828.get("phase_ledger_pass_written") is not False or invalidated_d828.get("config_owner_uid_gid")!=[1008,1008] or invalidated_d828.get("config_directory_mode")!="0700" or invalidated_d828.get("config_file_mode")!="0600":raise BuildError("d828 client user/config access invalidation differs")
 invalidated_c58=load(INVALIDATED_C58)
 if invalidated_c58.get("bundle_id")!=INVALIDATED_C58_ID or invalidated_c58.get("bundle_manifest_sha256")!=sha(INVALIDATED_C58_BUNDLE/"bundle_manifest.json") or invalidated_c58.get("accepted_rounds_credit")!=0 or invalidated_c58.get("status")!="INVALIDATED_DO_NOT_LAUNCH" or invalidated_c58.get("phase_ledger_pass_written") is not False or invalidated_c58.get("adopt_authority_sealed") is not False or invalidated_c58.get("measurement_tmpfs_root_uid")!=0 or invalidated_c58.get("measurement_tmpfs_root_gid")!=0 or invalidated_c58.get("measurement_tmpfs_root_mode")!="0700":raise BuildError("c58 measurement tmpfs owner invalidation differs")
 invalidated_96e0=load(INVALIDATED_96E0)
 if invalidated_96e0.get("bundle_id")!=INVALIDATED_96E0_ID or invalidated_96e0.get("bundle_manifest_sha256")!=sha(INVALIDATED_96E0_BUNDLE/"bundle_manifest.json") or invalidated_96e0.get("accepted_rounds_credit")!=0 or invalidated_96e0.get("status")!="INVALIDATED_DO_NOT_LAUNCH" or invalidated_96e0.get("phase_ledger_pass_written") is not False or invalidated_96e0.get("database_container_started") is not False or invalidated_96e0.get("client_container_started") is not False or invalidated_96e0.get("config_directory_uid")!=0 or invalidated_96e0.get("config_directory_gid")!=0 or invalidated_96e0.get("config_directory_mode")!="0700":raise BuildError("96e0 config owner invalidation differs")
 invalidated_5188=load(INVALIDATED_5188)
 if invalidated_5188.get("bundle_id")!=INVALIDATED_5188_ID or invalidated_5188.get("bundle_manifest_sha256")!=sha(INVALIDATED_5188_BUNDLE/"bundle_manifest.json") or invalidated_5188.get("accepted_rounds_credit")!=0 or invalidated_5188.get("status")!="INVALIDATED_DO_NOT_LAUNCH" or invalidated_5188.get("failure_stage")!="attempt_config_materialization_before_service_plan" or invalidated_5188.get("formal_launch_authorized") is not False:raise BuildError("5188 materializer interface invalidation differs")
 invalidated_d464=load(INVALIDATED_D464)
 if invalidated_d464.get("bundle_id")!=INVALIDATED_D464_ID or invalidated_d464.get("bundle_manifest_sha256")!=sha(INVALIDATED_D464_BUNDLE/"bundle_manifest.json") or invalidated_d464.get("accepted_rounds_credit")!=0 or invalidated_d464.get("qualification_credit")!=0 or invalidated_d464.get("fresh_r0_credit")!=0 or invalidated_d464.get("status")!="INVALIDATED_DO_NOT_LAUNCH" or invalidated_d464.get("failure_stage")!="qualification_stop_protocol_and_terminal_metrics_gate" or invalidated_d464.get("phase_ledger_pass_written") is not True or invalidated_d464.get("formal_launch_authorized") is not False:raise BuildError("d464 qualification terminal-gate invalidation differs")
 invalidated_1c55=load(INVALIDATED_1C55)
 if invalidated_1c55.get("bundle_id")!=INVALIDATED_1C55_ID or invalidated_1c55.get("bundle_manifest_sha256")!=sha(INVALIDATED_1C55_BUNDLE/"bundle_manifest.json") or invalidated_1c55.get("accepted_rounds_credit")!=0 or invalidated_1c55.get("qualification_credit")!=0 or invalidated_1c55.get("fresh_r0_credit")!=0 or invalidated_1c55.get("status")!="INVALIDATED_DO_NOT_LAUNCH" or invalidated_1c55.get("failure_stage")!="local_terminal_metrics_json_strictness_review" or invalidated_1c55.get("phase_ledger_pass_written") is not False or invalidated_1c55.get("remote_publication_executed") is not False:raise BuildError("1c55 terminal JSON invalidation differs")
 invalidated_0625=load(INVALIDATED_0625)
 if invalidated_0625.get("bundle_id")!=INVALIDATED_0625_ID or invalidated_0625.get("bundle_manifest_sha256")!=sha(INVALIDATED_0625_BUNDLE/"bundle_manifest.json") or invalidated_0625.get("accepted_rounds_credit")!=0 or invalidated_0625.get("qualification_credit")!=0 or invalidated_0625.get("fresh_r0_credit")!=0 or invalidated_0625.get("status")!="INVALIDATED_DO_NOT_LAUNCH" or invalidated_0625.get("failure_stage")!="provision_random_attempt_password_before_config" or invalidated_0625.get("physical_copy_pass") is not True or invalidated_0625.get("measurement_bridge_pass") is not True or invalidated_0625.get("workload_started") is not False:raise BuildError("0625 root-phase sudo invalidation differs")
 base=load(BASE/"bundle_manifest.json");output_root.mkdir(parents=True,exist_ok=True);stage=Path(tempfile.mkdtemp(prefix=".tpcc-v11.",dir=output_root));h=helper()
 try:
  h.copy_mutable(BASE,stage)
  h.write(stage/"authority/mysql_tpcc_node04_adopted_service_v1.py",SERVICE_V1.read_bytes(),0o755)
  h.write(stage/"authority/mysql_tpcc_node04_adopted_service_v2.py",SERVICE.read_bytes(),0o755)
  h.write(stage/"authority/mysql_tpcc_config_materialize_v1.py",MATERIALIZER_V1.read_bytes(),0o755)
  h.write(stage/"authority/mysql_tpcc_config_materialize_v2.py",MATERIALIZER.read_bytes(),0o755)
  h.write(stage/"authority/mysql_tpcc_materialize_phase_command_v2.py",PHASE_MATERIALIZER.read_bytes(),0o755)
  h.write(stage/"authority/mysql_tpcc_blkio_v1.py",BLKIO_HELPER.read_bytes(),0o755)
  h.write(stage/"authority/mysql_tpcc_measurement_bridge_v1.py",MEASUREMENT_BRIDGE_HELPER.read_bytes(),0o755)
  h.write(stage/"benchbase_runtime/config/plugin.xml",BENCHBASE_PLUGIN.read_bytes())
  h.write(stage/("authority/invalidation_"+BASE_ID+".json"),INVALIDATION.read_bytes())
  h.write(stage/"authority/mysql_tpcc_v11_784a955_closure_invalidation_20260731.json",INVALIDATED_784.read_bytes())
  h.write(stage/"authority/mysql_tpcc_v11_9a04d8_credential_readback_invalidation_20260731.json",INVALIDATED_9A.read_bytes())
  h.write(stage/"authority/mysql_tpcc_v11_3c83_attempt_root_mode_invalidation_20260731.json",INVALIDATED_3C83.read_bytes())
  h.write(stage/"authority/mysql_tpcc_v11_52bafa_plan_stat_import_invalidation_20260731.json",INVALIDATED_52BAFA.read_bytes())
  h.write(stage/"authority/mysql_tpcc_v11_9b3_blkio_v1_invalidation_20260731.json",INVALIDATED_9B3.read_bytes())
  h.write(stage/"authority/mysql_tpcc_v11_b35_start_output_path_invalidation_20260731.json",INVALIDATED_B35.read_bytes())
  h.write(stage/"authority/mysql_tpcc_v11_1954_prefault_v1_adopt_invalidation_20260731.json",INVALIDATED_1954.read_bytes())
  h.write(stage/"authority/mysql_tpcc_v11_0068_prefault_cgroup_v1_invalidation_20260731.json",INVALIDATED_0068.read_bytes())
  h.write(stage/"authority/mysql_tpcc_v11_48abd_prefault_absent_target_invalidation_20260731.json",INVALIDATED_48ABD.read_bytes())
  h.write(stage/"authority/mysql_tpcc_v11_7ec89_phase_v2_identity_invalidation_20260731.json",INVALIDATED_7EC89.read_bytes())
  h.write(stage/"authority/mysql_tpcc_v11_acb45_benchbase_classpath_invalidation_20260731.json",INVALIDATED_ACB45.read_bytes())
  h.write(stage/"authority/mysql_tpcc_v11_45ada_prefault_plugin_destination_invalidation_20260731.json",INVALIDATED_45ADA.read_bytes())
  h.write(stage/"authority/mysql_tpcc_v11_d828_client_user_config_access_invalidation_20260731.json",INVALIDATED_D828.read_bytes())
  h.write(stage/"authority/mysql_tpcc_v11_c58_measurement_tmpfs_owner_invalidation_20260731.json",INVALIDATED_C58.read_bytes())
  h.write(stage/"authority/mysql_tpcc_v11_96e0_config_owner_invalidation_20260731.json",INVALIDATED_96E0.read_bytes())
  h.write(stage/"authority/mysql_tpcc_v11_5188_config_materializer_interface_invalidation_20260731.json",INVALIDATED_5188.read_bytes())
  h.write(stage/"authority/mysql_tpcc_v11_d464_qualification_terminal_gate_invalidation_20260731.json",INVALIDATED_D464.read_bytes())
  h.write(stage/"authority/mysql_tpcc_v11_1c55_nonfinite_terminal_json_invalidation_20260731.json",INVALIDATED_1C55.read_bytes())
  h.write(stage/"authority/mysql_tpcc_v11_0625_root_phase_sudo_invalidation_20260731.json",INVALIDATED_0625.read_bytes())
  closure=files(stage)
  identity={
   "schema_version":"mysql_tpcc_typed_distributed_successor_bundle_v11",
   "scenario":"tpcc","formal_scope":"mysql_only",
   "cross_engine_comparison_prohibited":True,"immutable":True,
   "offline_build_only":True,"remote_actions":0,
   "supersedes_bundle_id":INVALIDATED_0625_ID,
   "construction_base_bundle_id":BASE_ID,
   "superseded_manifest_sha256":sha(INVALIDATED_0625_BUNDLE/"bundle_manifest.json"),
   "invalidated_bundle_ids":[
    *base.get("invalidated_bundle_ids",[]),BASE_ID,
    "784a955123770d1d22ccb5c02e53ceb78671f9505b577d67abb99ae9fd9dd55a",
    "9a04d8c56a2446c630f9ce00940815c71955e5b5fbfc290a8bfc2a7bb2aebbcf",
    "3c83b1d96ebd1d7f9fc1a9fcd9aa060507f36d24d0f06b78d782437016275ef3",
    "52bafa51b39a1e5528ed521c882dc483a78ee6773193803330cf305ef66ac48f",
    "9b3b770250d30490ca5af41d9853bd9133bba3a3d43e956f1a3e27440c12ab79",
    "b35c333fc7737c0cbd7f7a2015c23d0d5c496800d6a02f9ca9fbb160ad8b8396",
    INVALIDATED_1954_ID,INVALIDATED_0068_ID,INVALIDATED_48ABD_ID,
    INVALIDATED_7EC89_ID,INVALIDATED_ACB45_ID,INVALIDATED_45ADA_ID,
    INVALIDATED_D828_ID,INVALIDATED_C58_ID,INVALIDATED_96E0_ID,
    INVALIDATED_5188_ID,INVALIDATED_D464_ID,INVALIDATED_1C55_ID,
    INVALIDATED_0625_ID,
   ],
   "base_manifest_sha256":sha(BASE/"bundle_manifest.json"),
   "service_v1_sha256":sha(SERVICE_V1),"service_v2_sha256":sha(SERVICE),
   "config_materializer_v1_sha256":sha(MATERIALIZER_V1),
   "config_materializer_v2_sha256":sha(MATERIALIZER),
   "phase_materializer_v2_sha256":sha(PHASE_MATERIALIZER),
   "blkio_v1_helper_sha256":sha(BLKIO_HELPER),
   "measurement_bridge_helper_v1_sha256":sha(MEASUREMENT_BRIDGE_HELPER),
   "benchbase_plugin_sha256":sha(BENCHBASE_PLUGIN),
   "v10_invalidation_sha256":sha(INVALIDATION),
   "v11_784_invalidation_sha256":sha(INVALIDATED_784),
   "v11_9a_invalidation_sha256":sha(INVALIDATED_9A),
   "v11_3c83_invalidation_sha256":sha(INVALIDATED_3C83),
   "v11_52bafa_invalidation_sha256":sha(INVALIDATED_52BAFA),
   "v11_9b3_invalidation_sha256":sha(INVALIDATED_9B3),
   "v11_b35_invalidation_sha256":sha(INVALIDATED_B35),
   "v11_1954_invalidation_sha256":sha(INVALIDATED_1954),
   "v11_0068_invalidation_sha256":sha(INVALIDATED_0068),
   "v11_48abd_invalidation_sha256":sha(INVALIDATED_48ABD),
   "v11_7ec89_invalidation_sha256":sha(INVALIDATED_7EC89),
   "v11_acb45_invalidation_sha256":sha(INVALIDATED_ACB45),
   "v11_45ada_invalidation_sha256":sha(INVALIDATED_45ADA),
   "v11_d828_invalidation_sha256":sha(INVALIDATED_D828),
   "v11_c58_invalidation_sha256":sha(INVALIDATED_C58),
   "v11_96e0_invalidation_sha256":sha(INVALIDATED_96E0),
   "v11_5188_invalidation_sha256":sha(INVALIDATED_5188),
   "v11_d464_invalidation_sha256":sha(INVALIDATED_D464),
   "v11_1c55_invalidation_sha256":sha(INVALIDATED_1C55),
   "v11_0625_invalidation_sha256":sha(INVALIDATED_0625),
   "container_readonly_tree_modes":{
    "directory":"0555","regular_file":"0444","executable_file":"0555",
   },
   "phase_materializer_contract":{
    "sole_adopted_service_schema":"mysql_tpcc_node04_adopted_service_authority_v2",
    "full_database_and_client_identity_binding":True,
    "blkio_v1_authority_binding":True,
    "prefault_snapshot_authority_binding":True,
    "qualification_and_typed_capture_executor_audited":True,
    "production_benchbase_classpath_manifest_audited":True,
    "container_classpath_traversal_required":True,
    "client_private_config_and_readability_authority_required":True,
    "measurement_bridge_root_authority_required":True,
    "qps_resource_input_prohibited":True,
    "stdout_and_stderr_persisted_with_sha256":True,
    "unique_benchbase_terminal_metrics_required_for_qualification":True,
    "qualification_stop_requested_and_observed_required":True,
    "qualification_measured_window_seconds":[180,600],
    "qualification_completed_total_positive_required":True,
    "qualification_unexpected_errors_zero_required":True,
    "qualification_outputs_written_required":True,
    "invalid_stop_file_fail_closed_before_ledger":True,
    "atomic_no_overwrite_stop_writer_sealed":True,
   },
   "client_user_contract":{
    "configured_user":"1008:1008","config_user_readback_required":True,
    "effective_uid":1008,"effective_gid":1008,
    "effective_capabilities_hex":"0000000000000000",
    "config_directory_mode":"0700","config_file_mode":"0600",
    "world_readable_config_prohibited":True,
    "pid_and_process_start_ticks_bound":True,
    "measurement_rootless_write_smoke_required":True,
    "readability_required":[
     "jdk","bundle","plugin","python","tpcc_config","mysql_defaults",
    ],
   },
   "config_materialization_contract":{
    "target_uid":1008,"target_gid":1008,
    "directory_mode":"0700","regular_file_mode":"0600",
    "stage_owner_set_before_atomic_publish":True,
    "authority_owner_uid":1008,"authority_owner_gid":1008,
    "authority_mode":"0600",
    "owner_and_mode_readback_required":True,
    "failed_stage_must_not_publish_config":True,
    "host_effective_uid_gid":[0,0],
    "credential_provisioning_root_authority_required":True,
    "qps_resource_input_prohibited":True,
   },
   "measurement_bridge_contract":{
    "filesystem":"tmpfs","host_root_helper_required":True,
    "host_uid":1008,"host_gid":1008,"host_mode":"0700",
    "authority_file_mode":"0600","size_bytes":2147483648,
    "nosuid":True,"nodev":True,"noexec":True,
    "device_readback_required":True,
    "rootless_client_write_required":True,
    "nested_readonly_plugin_traversal_required":True,
   },
   "prefault_contract":{
    "v2_same_live_adoption_required":True,
    "physical_copy_authority_path":"<attempt>/physical_copy_authority.json",
    "zero_target_device_io_quiet_window_gate_required":True,
    "before_and_after_target_device_baselines_must_be_zero":True,
    "cgroup_io_snapshot_versions":[1,2],
    "full_client_container_id_binding_required":True,
    "target_device_major_minor":"259:2",
    "v1_required_operations":["Read","Write","Sync","Async"],
    "v1_symmetric_target_absence_as_zero":True,
    "v1_asymmetric_or_incomplete_target_coverage_fail_closed":True,
    "counter_missing_duplicate_or_malformed_fail_closed":True,
    "readonly_benchbase_plugin_destination":"/measurement/config",
    "readonly_benchbase_plugin_destination_required_in_prefault":True,
    "prefault_destinations_sorted_unique_required":True,
   },
   "inner_mysql_datadir_relative":"datadir/datadir",
   "attempt_root_mode":"0700",
   "cgroup_v1_blkio_throttle":{
    "device_major_minor":"259:2","read_bps":1205862400,
    "write_bps":1205862400,"host_root_helper_required":True,
    "must_pass_after_start_before_adopt":True,
   },
   "target_only_permission_normalization":{
    "uid":999,"gid":999,"directory_mode":"0700","regular_file_mode":"0600",
   },
   "attempt_secret_policy":"sealed_temporary_skip_grant_tables_skip_networking_atomic_0600_secret_before_reset_stdin_only_sql_existing_root_percent_altered",
   "formal_launch_authorized":False,"accepted_round_authorized":False,
   "root_phase_contract":{
    "sudo_noninteractive_prefix":["sudo","-n"],
    "effective_uid_gid":[0,0],
    "all_root_required_generator_commands_bound":True,
    "credential_config_service_and_phase_authorities_fail_closed":True,
   },
   "fresh_r0_required":True,"files":closure,
  }
  bid=hashlib.sha256(canonical(identity)).hexdigest()
  h.write(stage/"bundle_manifest.json",canonical({**identity,"bundle_id":bid}))
  h.write(stage/"bundle_manifest.json.sha256",f"{sha(stage/'bundle_manifest.json')}  bundle_manifest.json\n".encode())
  freeze_for_container(stage);dest=output_root/bid
  if dest.exists(): verify(dest);return dest
  os.rename(stage,dest);return dest
 finally:
  if stage.exists(): h.thaw_directories(stage);shutil.rmtree(stage)
def main():
 p=argparse.ArgumentParser();p.add_argument("--output-root",type=Path,default=OUT);p.add_argument("--verify",type=Path);a=p.parse_args();b=verify(a.verify) if a.verify else verify(build(a.output_root));print(json.dumps({"status":"PASS","bundle_id":b["bundle_id"],"formal_launch_authorized":False,"accepted_round_authorized":False},sort_keys=True));return 0
if __name__=="__main__":
 try:raise SystemExit(main())
 except BuildError as e:sys.stderr.write("NOT_READY: "+str(e)+"\n");raise SystemExit(2)

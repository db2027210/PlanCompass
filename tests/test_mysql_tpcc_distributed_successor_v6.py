from __future__ import annotations
import importlib.util, inspect, json, os, subprocess, sys
from pathlib import Path
import stat, zipfile
import pytest

ROOT=Path(__file__).resolve().parents[1]
BUILDER=ROOT/"shell/build_mysql_tpcc_distributed_successor_v6.py"
SPEC=ROOT/"shell/mysql_tpcc_v5_bootstrap_spec.py"
PUBLISHER=ROOT/"shell/publish_remote_content_addressed_bundle_v1.py"

def module(path,name):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def test_v11_binds_inner_datadir_and_denylists_v10(tmp_path: Path):
 b=module(BUILDER,"v11builder"); bundle=b.build(tmp_path); manifest=b.verify(bundle)
 assert manifest["supersedes_bundle_id"]==b.INVALIDATED_0625_ID
 assert manifest["construction_base_bundle_id"]==b.BASE_ID
 assert manifest["superseded_manifest_sha256"]==b.sha(b.INVALIDATED_0625_BUNDLE/"bundle_manifest.json")
 assert b.BASE_ID in manifest["invalidated_bundle_ids"]
 assert b.INVALIDATED_1954_ID in manifest["invalidated_bundle_ids"]
 assert b.INVALIDATED_0068_ID in manifest["invalidated_bundle_ids"]
 assert b.INVALIDATED_48ABD_ID in manifest["invalidated_bundle_ids"]
 assert b.INVALIDATED_7EC89_ID in manifest["invalidated_bundle_ids"]
 assert b.INVALIDATED_ACB45_ID in manifest["invalidated_bundle_ids"]
 assert b.INVALIDATED_45ADA_ID in manifest["invalidated_bundle_ids"]
 assert b.INVALIDATED_D828_ID in manifest["invalidated_bundle_ids"]
 assert b.INVALIDATED_C58_ID in manifest["invalidated_bundle_ids"]
 assert b.INVALIDATED_96E0_ID in manifest["invalidated_bundle_ids"]
 assert b.INVALIDATED_5188_ID in manifest["invalidated_bundle_ids"]
 assert b.INVALIDATED_D464_ID in manifest["invalidated_bundle_ids"]
 assert b.INVALIDATED_1C55_ID in manifest["invalidated_bundle_ids"]
 assert b.INVALIDATED_0625_ID in manifest["invalidated_bundle_ids"]
 assert "784a955123770d1d22ccb5c02e53ceb78671f9505b577d67abb99ae9fd9dd55a" in manifest["invalidated_bundle_ids"]
 assert "9a04d8c56a2446c630f9ce00940815c71955e5b5fbfc290a8bfc2a7bb2aebbcf" in manifest["invalidated_bundle_ids"]
 assert "3c83b1d96ebd1d7f9fc1a9fcd9aa060507f36d24d0f06b78d782437016275ef3" in manifest["invalidated_bundle_ids"]
 assert "9b3b770250d30490ca5af41d9853bd9133bba3a3d43e956f1a3e27440c12ab79" in manifest["invalidated_bundle_ids"]
 assert "b35c333fc7737c0cbd7f7a2015c23d0d5c496800d6a02f9ca9fbb160ad8b8396" in manifest["invalidated_bundle_ids"]
 assert "52bafa51b39a1e5528ed521c882dc483a78ee6773193803330cf305ef66ac48f" in manifest["invalidated_bundle_ids"]
 assert manifest["attempt_root_mode"]=="0700"
 assert manifest["inner_mysql_datadir_relative"]=="datadir/datadir"
 assert manifest["target_only_permission_normalization"]=={"uid":999,"gid":999,"directory_mode":"0700","regular_file_mode":"0600"}
 assert "skip_grant_tables_skip_networking" in manifest["attempt_secret_policy"]
 service=(bundle/"authority/mysql_tpcc_node04_adopted_service_v2.py").read_text()
 service_v1=(bundle/"authority/mysql_tpcc_node04_adopted_service_v1.py").read_text()
 assert "import stat" in service_v1
 assert "MYSQL_DATADIR_RELATIVE = Path(\"datadir\")" in service
 assert "--user\", \"0:0\"" in service and "chown 999:999" in service
 assert "attempt_root.mkdir(parents=True, mode=0o700, exist_ok=False)" in service
 assert "attempt root mode must be exactly 0700" in service
 assert "-printf '%U:%G:%m\\\\n'" in service
 assert "-printf '%u:%g:%m\\\\n'" not in service
 assert "def prefault(plan: Mapping[str, Any])" in service
 assert "service = adopt(plan)" in service
 assert "else base.prefault(plan)" not in service
 assert "real_window_all_target_device_io_must_be_zero" in service
 assert "BENCHBASE_PLUGIN_DESTINATION = \"/measurement/config\"" in service
 assert "production BenchBase plugin mount differs" in service
 materializer=(bundle/"authority/mysql_tpcc_config_materialize_v2.py").read_text()
 materializer_v1_path=bundle/"authority/mysql_tpcc_config_materialize_v1.py"
 materializer_v1=module(materializer_v1_path,"sealed_tpcc_config_materializer_v1")
 parameters=inspect.signature(materializer_v1.materialize).parameters
 assert parameters["target_uid"].kind is inspect.Parameter.KEYWORD_ONLY
 assert parameters["target_gid"].kind is inspect.Parameter.KEYWORD_ONLY
 assert parameters["target_uid"].default is None
 assert parameters["target_gid"].default is None
 assert manifest["config_materializer_v1_sha256"]==b.sha(materializer_v1_path)
 assert "target_uid=CLIENT_UID, target_gid=CLIENT_GID" in materializer
 authority_dir=str((bundle/"authority").resolve())
 old_base=sys.modules.pop("mysql_tpcc_config_materialize_v1",None)
 sys.path.insert(0,authority_dir)
 try:
  sealed_v2=module(
   bundle/"authority/mysql_tpcc_config_materialize_v2.py",
   "sealed_tpcc_config_materializer_v2",
  )
 finally:
  sys.path.remove(authority_dir)
  sys.modules.pop("mysql_tpcc_config_materialize_v1",None)
  if old_base is not None:
   sys.modules["mysql_tpcc_config_materialize_v1"]=old_base
 assert Path(sealed_v2.base.__file__).resolve()==materializer_v1_path.resolve()
 assert "target_uid" in inspect.signature(sealed_v2.base.materialize).parameters
 assert "--skip-grant-tables=ON" in materializer and "--skip-networking=ON" in materializer
 assert "_write_password_atomically(password_file, password, attempt_uid, attempt_gid)" in materializer
 assert "attempt root must be an exact 0700 directory" in materializer
 assert '["docker", "exec", "-i", name, "mysql", "-uroot"]' in materializer
 assert 'input=sql' in materializer
 assert 'ALTER USER \'root\'@\'%\'' in materializer
 assert '"-NBe", sql' not in materializer
 phase_materializer=bundle/"authority/mysql_tpcc_materialize_phase_command_v2.py"
 assert b.sha(phase_materializer)==b.sha(b.PHASE_MATERIALIZER)
 assert manifest["phase_materializer_v2_sha256"]==b.sha(b.PHASE_MATERIALIZER)
 phase_source=phase_materializer.read_text()
 assert "mysql_tpcc_node04_adopted_service_authority_v2" in phase_source
 assert "authority/mysql_tpcc_node04_adopted_service_v2.py" in phase_source
 publisher=module(PUBLISHER,"generic_publisher")
 assert publisher.verify_local(bundle)==(bundle.name,len(manifest["files"]))
 assert (bundle/"authority/mysql_tpcc_v11_784a955_closure_invalidation_20260731.json").is_file()
 assert (bundle/"authority/mysql_tpcc_v11_9a04d8_credential_readback_invalidation_20260731.json").is_file()
 assert (bundle/"authority/mysql_tpcc_v11_3c83_attempt_root_mode_invalidation_20260731.json").is_file()
 assert (bundle/"authority/mysql_tpcc_v11_9b3_blkio_v1_invalidation_20260731.json").is_file()
 assert (bundle/"authority/mysql_tpcc_v11_b35_start_output_path_invalidation_20260731.json").is_file()
 assert (bundle/"authority/mysql_tpcc_v11_1954_prefault_v1_adopt_invalidation_20260731.json").is_file()
 assert (bundle/"authority/mysql_tpcc_v11_0068_prefault_cgroup_v1_invalidation_20260731.json").is_file()
 assert (bundle/"authority/mysql_tpcc_v11_48abd_prefault_absent_target_invalidation_20260731.json").is_file()
 assert (bundle/"authority/mysql_tpcc_v11_7ec89_phase_v2_identity_invalidation_20260731.json").is_file()
 assert (bundle/"authority/mysql_tpcc_v11_acb45_benchbase_classpath_invalidation_20260731.json").is_file()
 assert (bundle/"authority/mysql_tpcc_v11_45ada_prefault_plugin_destination_invalidation_20260731.json").is_file()
 assert (bundle/"authority/mysql_tpcc_v11_d828_client_user_config_access_invalidation_20260731.json").is_file()
 assert (bundle/"authority/mysql_tpcc_v11_c58_measurement_tmpfs_owner_invalidation_20260731.json").is_file()
 assert (bundle/"authority/mysql_tpcc_v11_96e0_config_owner_invalidation_20260731.json").is_file()
 assert (bundle/"authority/mysql_tpcc_v11_5188_config_materializer_interface_invalidation_20260731.json").is_file()
 assert (bundle/"authority/mysql_tpcc_v11_d464_qualification_terminal_gate_invalidation_20260731.json").is_file()
 assert (bundle/"authority/mysql_tpcc_v11_1c55_nonfinite_terminal_json_invalidation_20260731.json").is_file()
 assert (bundle/"authority/mysql_tpcc_v11_0625_root_phase_sudo_invalidation_20260731.json").is_file()
 assert (bundle/"authority/mysql_tpcc_measurement_bridge_v1.py").is_file()
 bridge_helper=(bundle/"authority/mysql_tpcc_measurement_bridge_v1.py").read_text()
 assert '"size=2147483648,mode=0700,uid=1008,gid=1008,nosuid,nodev,noexec"' in bridge_helper
 assert "os.chown(path, CLIENT_UID, CLIENT_GID)" in bridge_helper
 assert "live_size_bytes != BRIDGE_BYTES" in bridge_helper
 assert manifest["measurement_bridge_helper_v1_sha256"]==b.sha(
  bundle/"authority/mysql_tpcc_measurement_bridge_v1.py"
 )
 assert manifest["container_readonly_tree_modes"]=={"directory":"0555","regular_file":"0444","executable_file":"0555"}
 assert manifest["benchbase_plugin_sha256"]=="a770865560e0d1044c98a2954bacfd3d0c58ed3e956d3e783b2e830c7951d7e2"
 assert b.sha(bundle/"benchbase_runtime/config/plugin.xml")==manifest["benchbase_plugin_sha256"]
 assert all(
  stat.S_IMODE(path.stat().st_mode)==0o555
  for path in [bundle,*[item for item in bundle.rglob("*") if item.is_dir()]]
 )
 assert all(
  stat.S_IMODE(path.stat().st_mode) in {0o444,0o555}
  for path in bundle.rglob("*") if path.is_file()
 )
 jar=bundle/"predecessor/runtime/benchbase-formal.jar"
 with zipfile.ZipFile(jar) as archive:
  assert {
   "com/oltpbenchmark/DBWorkload.class",
   "com/oltpbenchmark/benchmarks/tpcc/TPCCBenchmark.class",
   "com/oltpbenchmark/benchmarks/tpcc/TPCCWorker.class",
  }.issubset(archive.namelist())
 assert manifest["phase_materializer_contract"]=={"sole_adopted_service_schema":"mysql_tpcc_node04_adopted_service_authority_v2","full_database_and_client_identity_binding":True,"blkio_v1_authority_binding":True,"prefault_snapshot_authority_binding":True,"qualification_and_typed_capture_executor_audited":True,"production_benchbase_classpath_manifest_audited":True,"container_classpath_traversal_required":True,"client_private_config_and_readability_authority_required":True,"measurement_bridge_root_authority_required":True,"qps_resource_input_prohibited":True,"stdout_and_stderr_persisted_with_sha256":True,"unique_benchbase_terminal_metrics_required_for_qualification":True,"qualification_stop_requested_and_observed_required":True,"qualification_measured_window_seconds":[180,600],"qualification_completed_total_positive_required":True,"qualification_unexpected_errors_zero_required":True,"qualification_outputs_written_required":True,"invalid_stop_file_fail_closed_before_ledger":True,"atomic_no_overwrite_stop_writer_sealed":True}
 assert manifest["client_user_contract"]=={"configured_user":"1008:1008","config_user_readback_required":True,"effective_uid":1008,"effective_gid":1008,"effective_capabilities_hex":"0000000000000000","config_directory_mode":"0700","config_file_mode":"0600","world_readable_config_prohibited":True,"pid_and_process_start_ticks_bound":True,"measurement_rootless_write_smoke_required":True,"readability_required":["jdk","bundle","plugin","python","tpcc_config","mysql_defaults"]}
 assert manifest["measurement_bridge_contract"]=={"filesystem":"tmpfs","host_root_helper_required":True,"host_uid":1008,"host_gid":1008,"host_mode":"0700","authority_file_mode":"0600","size_bytes":2147483648,"nosuid":True,"nodev":True,"noexec":True,"device_readback_required":True,"rootless_client_write_required":True,"nested_readonly_plugin_traversal_required":True}
 assert manifest["config_materialization_contract"]=={"target_uid":1008,"target_gid":1008,"directory_mode":"0700","regular_file_mode":"0600","stage_owner_set_before_atomic_publish":True,"authority_owner_uid":1008,"authority_owner_gid":1008,"authority_mode":"0600","owner_and_mode_readback_required":True,"failed_stage_must_not_publish_config":True,"host_effective_uid_gid":[0,0],"credential_provisioning_root_authority_required":True,"qps_resource_input_prohibited":True}
 assert manifest["root_phase_contract"]=={"sudo_noninteractive_prefix":["sudo","-n"],"effective_uid_gid":[0,0],"all_root_required_generator_commands_bound":True,"credential_config_service_and_phase_authorities_fail_closed":True}
 assert manifest["prefault_contract"]=={"v2_same_live_adoption_required":True,"physical_copy_authority_path":"<attempt>/physical_copy_authority.json","zero_target_device_io_quiet_window_gate_required":True,"before_and_after_target_device_baselines_must_be_zero":True,"cgroup_io_snapshot_versions":[1,2],"full_client_container_id_binding_required":True,"target_device_major_minor":"259:2","v1_required_operations":["Read","Write","Sync","Async"],"v1_symmetric_target_absence_as_zero":True,"v1_asymmetric_or_incomplete_target_coverage_fail_closed":True,"counter_missing_duplicate_or_malformed_fail_closed":True,"readonly_benchbase_plugin_destination":"/measurement/config","readonly_benchbase_plugin_destination_required_in_prefault":True,"prefault_destinations_sorted_unique_required":True}
 assert manifest["cgroup_v1_blkio_throttle"]=={"device_major_minor":"259:2","read_bps":1205862400,"write_bps":1205862400,"host_root_helper_required":True,"must_pass_after_start_before_adopt":True}
 helper=(bundle/"authority/mysql_tpcc_blkio_v1.py").read_text()
 assert "os.geteuid() != 0" in helper and "blkio.throttle.read_bps_device" in helper
 assert (bundle/"authority/mysql_tpcc_v11_52bafa_plan_stat_import_invalidation_20260731.json").is_file()


def test_v11_prefault_uses_v2_adoption_and_retains_zero_io_gate(
 monkeypatch: pytest.MonkeyPatch
):
 service=module(ROOT/"shell/mysql_tpcc_node04_adopted_service_v2.py","v11prefault")
 plan={"client":{"name":"tpcc-client"}}
 adopted={
  "bundle_id":"a"*64,
  "attempt_id":"tpcc-v4-20260731t101500z-0123456789abcdef",
  "handoff_token_sha256":"b"*64,
  "client":{"container_id":"c"*64,"pid":4321,"process_start_ticks":9876},
 }
 calls=[]
 monkeypatch.setattr(service,"adopt",lambda value:(calls.append("v2_adopt") or adopted))
 monkeypatch.setattr(service.base,"prefault",lambda value:(_ for _ in ()).throw(AssertionError("v1 prefault called")))
 monkeypatch.setattr(service.base,"_inspect",lambda name:{"Id":"c"*64})
 monkeypatch.setattr(service.base,"_target_mount_destinations",lambda inspect:["/sealed-bundle"])
 authority={"cgroup_version":1,"counter_files":["bytes","serviced"]}
 snapshots=iter([
  {"counters":{"rbytes":0,"wbytes":0,"rios":0,"wios":0,"dbytes":0,"dios":0},
   "sync_async":{},"authority":authority},
  {"counters":{"rbytes":0,"wbytes":0,"rios":0,"wios":0,"dbytes":0,"dios":0},
   "sync_async":{},"authority":authority},
  {"counters":{"rbytes":0,"wbytes":0,"rios":0,"wios":0,"dbytes":0,"dios":0},
   "sync_async":{},"authority":authority},
 ])
 monkeypatch.setattr(service,"_target_io_snapshot",lambda client_id,pid:next(snapshots))
 monkeypatch.setattr(service.base,"_run",lambda argv:calls.append(argv))
 monkeypatch.setattr(service.time,"sleep",lambda seconds:calls.append(("sleep",seconds)))
 result=service.prefault(plan)
 assert calls[0]=="v2_adopt"
 assert result["v2_same_live_adoption_verified"] is True
 assert result["real_window_all_target_device_io_must_be_zero"] is True
 assert result["quiet_window_delta"]=={"rbytes":0,"wbytes":0,"rios":0,"wios":0,"dbytes":0,"dios":0}
 assert result["accepted_round_authorized"] is False
 docker_exec=[value for value in calls if isinstance(value,list)]
 assert docker_exec and docker_exec[0][:3]==["docker","exec","tpcc-client"]


def test_v11_prefault_cgroup_v1_snapshot_is_full_id_bound_and_strict(tmp_path: Path):
 service=module(ROOT/"shell/mysql_tpcc_node04_adopted_service_v2.py","v11cgroupv1")
 client_id="c"*64; pid=4321
 proc=tmp_path/"proc"; cgroups=tmp_path/"cgroup"
 (proc/str(pid)).mkdir(parents=True)
 (proc/str(pid)/"cgroup").write_text(f"7:blkio:/docker/{client_id}\n")
 root=cgroups/"blkio"/"docker"/client_id; root.mkdir(parents=True)
 (root/"blkio.throttle.io_service_bytes").write_text(
  "259:2 Read 4096\n259:2 Write 1024\n259:2 Sync 4096\n"
  "259:2 Async 1024\n259:2 Total 5120\nTotal 5120\n"
 )
 (root/"blkio.throttle.io_serviced").write_text(
  "259:2 Read 4\n259:2 Write 1\n259:2 Sync 4\n"
  "259:2 Async 1\n259:2 Total 5\nTotal 5\n"
 )
 result=service._target_io_snapshot(
  client_id,pid,proc_root=proc,cgroup_root=cgroups
 )
 assert result["counters"]=={
  "rbytes":4096,"wbytes":1024,"rios":4,"wios":1,"dbytes":0,"dios":0,
 }
 assert result["sync_async"]=={
  "sync_bytes":4096,"async_bytes":1024,"sync_ios":4,"async_ios":1,
 }
 assert result["authority"]["cgroup_version"]==1
 assert result["authority"]["counter_family"]=="throttle"
 assert result["absence_as_zero"] is False
 assert result["authority"]["counter_files"]==[
  str(root/"blkio.throttle.io_service_bytes"),
  str(root/"blkio.throttle.io_serviced"),
 ]


@pytest.mark.parametrize("fault",["missing","duplicate","malformed"])
def test_v11_prefault_cgroup_v1_target_rows_fail_closed(
 tmp_path: Path, fault: str
):
 service=module(ROOT/"shell/mysql_tpcc_node04_adopted_service_v2.py",f"v11v1bad{fault}")
 client_id="d"*64; pid=5678
 proc=tmp_path/"proc"; cgroups=tmp_path/"cgroup"
 (proc/str(pid)).mkdir(parents=True)
 (proc/str(pid)/"cgroup").write_text(f"7:blkio:/docker/{client_id}\n")
 root=cgroups/"blkio"/"docker"/client_id; root.mkdir(parents=True)
 rows=[
  "259:2 Read 4096","259:2 Write 1024","259:2 Sync 4096",
  "259:2 Async 1024","259:2 Total 5120",
 ]
 if fault=="missing": rows=[row for row in rows if " Async " not in f" {row} "]
 elif fault=="duplicate": rows.insert(1,"259:2 Read 4096")
 else: rows[0]="259:2 Read nope"
 (root/"blkio.throttle.io_service_bytes").write_text("\n".join(rows)+"\n")
 (root/"blkio.throttle.io_serviced").write_text(
  "259:2 Read 4\n259:2 Write 1\n259:2 Sync 4\n"
  "259:2 Async 1\n259:2 Total 5\n"
 )
 with pytest.raises(service.ServiceError):
  service._target_io_snapshot(client_id,pid,proc_root=proc,cgroup_root=cgroups)


def test_v11_prefault_cgroup_v2_support_is_retained(tmp_path: Path):
 service=module(ROOT/"shell/mysql_tpcc_node04_adopted_service_v2.py","v11cgroupv2")
 client_id="e"*64; pid=6789
 proc=tmp_path/"proc"; cgroups=tmp_path/"cgroup"
 (proc/str(pid)).mkdir(parents=True)
 (proc/str(pid)/"cgroup").write_text(f"0::/docker/{client_id}\n")
 root=cgroups/"docker"/client_id; root.mkdir(parents=True)
 (root/"io.stat").write_text(
  "259:2 rbytes=4096 wbytes=1024 rios=4 wios=1 dbytes=0 dios=0\n"
 )
 result=service._target_io_snapshot(
  client_id,pid,proc_root=proc,cgroup_root=cgroups
 )
 assert result["authority"]["cgroup_version"]==2
 assert result["counters"]=={
  "rbytes":4096,"wbytes":1024,"rios":4,"wios":1,"dbytes":0,"dios":0,
 }


def test_v11_prefault_cgroup_v1_uses_membership_bound_authoritative_fallback(
 tmp_path: Path
):
 service=module(ROOT/"shell/mysql_tpcc_node04_adopted_service_v2.py","v11v1fallback")
 client_id="f"*64; pid=7890
 proc=tmp_path/"proc"; cgroups=tmp_path/"cgroup"
 relative=f"/system.slice/docker-{client_id}.scope"
 (proc/str(pid)).mkdir(parents=True)
 (proc/str(pid)/"cgroup").write_text(f"7:blkio:{relative}\n")
 root=cgroups/"blkio"/"system.slice"/f"docker-{client_id}.scope"
 root.mkdir(parents=True)
 (root/"blkio.io_service_bytes").write_text(
  "259:2 Read 2048\n259:2 Write 0\n259:2 Sync 2048\n"
  "259:2 Async 0\n259:2 Total 2048\n"
 )
 (root/"blkio.io_serviced").write_text(
  "259:2 Read 2\n259:2 Write 0\n259:2 Sync 2\n"
  "259:2 Async 0\n259:2 Total 2\n"
 )
 result=service._target_io_snapshot(
  client_id,pid,proc_root=proc,cgroup_root=cgroups
 )
 assert result["authority"]["counter_family"]=="controller"
 assert result["authority"]["counter_files"]==[
  str(root/"blkio.io_service_bytes"),str(root/"blkio.io_serviced"),
 ]


def test_v11_prefault_cgroup_v1_symmetric_target_absence_is_zero(tmp_path: Path):
 service=module(ROOT/"shell/mysql_tpcc_node04_adopted_service_v2.py","v11v1absent")
 client_id="1"*64; pid=8901
 proc=tmp_path/"proc"; cgroups=tmp_path/"cgroup"
 (proc/str(pid)).mkdir(parents=True)
 (proc/str(pid)/"cgroup").write_text(f"7:blkio:/docker/{client_id}\n")
 root=cgroups/"blkio"/"docker"/client_id; root.mkdir(parents=True)
 # Other-device rows prove both authority files exist and are strictly parsed;
 # the fresh client has never touched target 259:2, so that row is absent.
 (root/"blkio.throttle.io_service_bytes").write_text(
  "8:0 Read 4096\n8:0 Write 0\n8:0 Sync 4096\n"
  "8:0 Async 0\n8:0 Total 4096\nTotal 4096\n"
 )
 (root/"blkio.throttle.io_serviced").write_text(
  "8:0 Read 4\n8:0 Write 0\n8:0 Sync 4\n"
  "8:0 Async 0\n8:0 Total 4\nTotal 4\n"
 )
 result=service._target_io_snapshot(
  client_id,pid,proc_root=proc,cgroup_root=cgroups
 )
 assert result["absence_as_zero"] is True
 assert result["counters"]=={
  "rbytes":0,"wbytes":0,"rios":0,"wios":0,"dbytes":0,"dios":0,
 }
 assert result["sync_async"]=={
  "sync_bytes":0,"async_bytes":0,"sync_ios":0,"async_ios":0,
 }


def test_v11_prefault_cgroup_v1_asymmetric_target_absence_fails_closed(
 tmp_path: Path
):
 service=module(ROOT/"shell/mysql_tpcc_node04_adopted_service_v2.py","v11v1asymmetric")
 client_id="2"*64; pid=9012
 proc=tmp_path/"proc"; cgroups=tmp_path/"cgroup"
 (proc/str(pid)).mkdir(parents=True)
 (proc/str(pid)/"cgroup").write_text(f"7:blkio:/docker/{client_id}\n")
 root=cgroups/"blkio"/"docker"/client_id; root.mkdir(parents=True)
 (root/"blkio.throttle.io_service_bytes").write_text(
  "259:2 Read 0\n259:2 Write 0\n259:2 Sync 0\n259:2 Async 0\n"
 )
 (root/"blkio.throttle.io_serviced").write_text(
  "8:0 Read 0\n8:0 Write 0\n8:0 Sync 0\n8:0 Async 0\n"
 )
 with pytest.raises(
  service.ServiceError,match="target-device counter-file coverage differs"
 ):
  service._target_io_snapshot(client_id,pid,proc_root=proc,cgroup_root=cgroups)


def _v2_phase_evidence(materializer, bundle_id: str, token: str):
 db_id="3"*64; client_id="4"*64
 db_name=f"ra_mysql_tpcc_v2_db_{token}"
 client_name=f"ra_mysql_tpcc_v2_client_{token}"
 cgroup="/sys/fs/cgroup/blkio/docker/"+db_id
 service={
  "schema_version":"mysql_tpcc_node04_adopted_service_authority_v2",
  "status":"PASS","formal_scope":"mysql_only",
  "cross_engine_comparison_prohibited":True,"bundle_id":bundle_id,
  "attempt_id":token,
  "handoff_token_sha256":materializer.sha_bytes(token.encode()),
  "host":"node04","host_effective_uid_gid":[0,0],
  "same_live_database_required":True,
  "container_recreated":False,"database_restarted":False,
  "datadir_replaced":False,"service_ready":True,
  "formal_launch_authorized":False,"accepted_round_authorized":False,
  "database":{
   "container_id":db_id,"name":db_name,"pid":67144,
   "process_start_ticks":123456,"datadir":"/attempt/datadir/datadir",
   "device":"/dev/nvme3n1","device_major_minor":"259:2",
   "cpuset_cpus":"21-24,57-60","cpuset_mems":"1",
   "cpu_quota_cores":4.0,"memory_bytes":8*1024**3,
   "memory_swap_bytes":8*1024**3,"pids_max":512,"restart_count":0,
   "mysql_port":65106,"image_id":"sha256:"+"5"*64,
  },
  "client":{
   "container_id":client_id,"name":client_name,"pid":67320,
   "process_start_ticks":234567,"cpuset_cpus":"8-15,44-51",
   "cpuset_mems":"0","cpu_quota_cores":16.0,
   "memory_bytes":8*1024**3,"memory_swap_bytes":8*1024**3,
   "pids_max":1024,"restart_count":0,
   "configured_user":"1008:1008",
   "root_filesystem_read_only":True,"network_mode":"host",
   "target_mysql_device_write_prohibited":True,
   "image_id":"sha256:"+"5"*64,
  },
  "client_private_config":{
   "configured_user":"1008:1008","uid":1008,"gid":1008,
       "directory_mode":"0700",
       "ownership_transactional_before_publish":True,
       "host_effective_uid_gid":[0,0],
       "credential_provisioning_authority_sha256":"f"*64,
   "host_effective_uid_gid":[0,0],
   "credential_provisioning_authority_sha256":"b"*64,
   "authority_path":(
    "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/"
    "runtime_algorithm_c_successor_20260729/"+token+
    "/config_materialization.json"
   ),
   "authority_sha256":"a"*64,"authority_mode":"0600",
   "files":{
    "mysql.cnf":{"sha256":"6"*64,"bytes":128,"mode":"0600",
                 "uid":1008,"gid":1008},
    "tpcc.xml":{"sha256":"7"*64,"bytes":1024,"mode":"0600",
                "uid":1008,"gid":1008},
   },
  },
  "client_readability_smoke":{
   "schema_version":"mysql_tpcc_client_readability_smoke_v1",
   "status":"PASS","configured_user":"1008:1008",
   "effective_uid":1008,"effective_gid":1008,
   "effective_capabilities_hex":"0000000000000000",
   "client_container_id":client_id,"client_pid":67320,
   "client_process_start_ticks":234567,
   "measurement_write":{
    "directory":"/measurement","bytes":27,
    "file_uid":1008,"file_gid":1008,"file_mode":"0600",
   },
   "paths":{
    "jdk":{"path":"/opt/mysql-tpcc-jdk/bin/java",
           "bytes_read":1,"sha256":None},
    "bundle":{"path":"/opt/mysql-tpcc-bundle/predecessor/runtime/benchbase-formal.jar",
              "bytes_read":1,"sha256":None},
    "plugin":{"path":"/measurement/config/plugin.xml","bytes_read":1,
              "sha256":"a770865560e0d1044c98a2954bacfd3d0c58ed3e956d3e783b2e830c7951d7e2"},
    "python":{"path":"/opt/mysql-tpcc-python/bin/python3.11",
              "bytes_read":1,"sha256":None},
    "tpcc_config":{"path":"/opt/mysql-tpcc-config/tpcc.xml",
                   "bytes_read":1,"sha256":"7"*64},
    "mysql_defaults":{"path":"/opt/mysql-tpcc-config/mysql.cnf",
                      "bytes_read":1,"sha256":"6"*64},
   },
  },
  "blkio_v1_throttle":{
   "schema_version":"mysql_tpcc_blkio_v1_throttle_authority_v1",
   "status":"PASS","formal_scope":"mysql_only",
   "cross_engine_comparison_prohibited":True,"bundle_id":bundle_id,
   "attempt_id":token,"database_container":db_name,
   "database_container_id":db_id,"database_pid":67144,
   "host_effective_uid_gid":[0,0],"cgroup_version":"v1",
   "cgroup_path":cgroup,"device_major_minor":"259:2",
   "read_bps":1205862400,"write_bps":1205862400,
   "read_throttle_file":cgroup+"/blkio.throttle.read_bps_device",
   "write_throttle_file":cgroup+"/blkio.throttle.write_bps_device",
   "docker_hostconfig_readback_not_authoritative":True,
   "database_or_client_started":True,"workload_started":False,
   "formal_launch_authorized":False,"accepted_round_authorized":False,
  },
  "benchbase_plugin_mount":{
   "source":(
    "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/"
    "runtime_algorithm_c_successor_20260729/bundles/"+bundle_id+
    "/benchbase_runtime/config"
   ),
   "destination":"/measurement/config","plugin_relative":"plugin.xml",
   "plugin_sha256":"a770865560e0d1044c98a2954bacfd3d0c58ed3e956d3e783b2e830c7951d7e2",
   "read_only":True,
  },
  "measurement_bridge":{
   "schema_version":"mysql_tpcc_measurement_bridge_authority_v1",
   "status":"PASS","formal_scope":"mysql_only",
   "cross_engine_comparison_prohibited":True,
   "bundle_id":bundle_id,"attempt_id":token,
   "host_effective_uid_gid":[0,0],
   "host_path":(
    "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/"
    "runtime_algorithm_c_successor_20260729/"+token+"/measurement"
   ),
   "container_path":"/measurement","filesystem":"tmpfs",
   "mount_source":"tmpfs",
   "mount_target":(
    "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/"
    "runtime_algorithm_c_successor_20260729/"+token+"/measurement"
   ),
   "device_major_minor":"0:123","size_bytes":2*1024**3,
   "uid":1008,"gid":1008,"mode":"0700",
   "nosuid":True,"nodev":True,"noexec":True,
   "rootless_client_writable_by_owner_mode":True,
   "nested_readonly_plugin_mount_required":True,
   "formal_launch_authorized":False,"accepted_round_authorized":False,
   "authority_path":(
    "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/"
    "runtime_algorithm_c_successor_20260729/"+token+
    "/measurement_bridge_authority.json"
   ),
   "authority_sha256":"9"*64,
   "authority_file_uid":1008,"authority_file_gid":1008,
   "authority_file_mode":"0600","live_readback_verified":True,
  },
  "runtime_tree_readback":{
   "jdk":{"tree_content_sha256":materializer.JDK_TREE_SHA256,
          "mounted_read_only":True},
   "python":{"tree_content_sha256":materializer.PYTHON_TREE_SHA256,
             "mounted_read_only":True},
  },
 }
 service["live_readback_sha256"]=materializer.sha_bytes(materializer.canonical(service))
 zeros={"rbytes":0,"wbytes":0,"rios":0,"wios":0,"dbytes":0,"dios":0}
 sync={"sync_bytes":0,"async_bytes":0,"sync_ios":0,"async_ios":0}
 client_root="/sys/fs/cgroup/blkio/docker/"+client_id
 snapshot={
  "counters":zeros,"sync_async":sync,"absence_as_zero":True,
  "authority":{
   "cgroup_version":1,"counter_family":"throttle",
   "target_device_major_minor":"259:2",
   "counter_files":[
    client_root+"/blkio.throttle.io_service_bytes",
    client_root+"/blkio.throttle.io_serviced",
   ],
  },
 }
 prefault={
  "schema_version":"mysql_tpcc_client_target_device_prefault_v1",
  "status":"PASS_PREFAULT_ZERO_IO_BASELINE","formal_scope":"mysql_only",
  "cross_engine_comparison_prohibited":True,"bundle_id":bundle_id,
  "attempt_id":token,"handoff_token_sha256":service["handoff_token_sha256"],
  "client_container_id":client_id,"client_pid":67320,
  "client_process_start_ticks":234567,"target_device_major_minor":"259:2",
  "prefaulted_target_mount_destinations":[
   "/measurement/config",
   "/opt/mysql-tpcc-bundle",
   "/opt/mysql-tpcc-config",
   "/opt/mysql-tpcc-python",
  ],
  "before_prefault":zeros,"after_prefault":zeros,
  "real_window_baseline":zeros,"quiet_window_seconds":2,
  "quiet_window_delta":zeros,
  "client_cgroup_io_snapshot_evidence":{
   "before_prefault":snapshot,"after_prefault":snapshot,
   "real_window_baseline":snapshot,
  },
  "real_window_all_target_device_io_must_be_zero":True,
  "v2_same_live_adoption_verified":True,
  "formal_launch_authorized":False,"accepted_round_authorized":False,
 }
 return service,prefault


def test_v11_phase_materializer_accepts_only_current_v2_identity_and_binds_commands(
 tmp_path: Path,monkeypatch: pytest.MonkeyPatch
):
 b=module(BUILDER,"v11phasebuilder"); bundle=b.build(tmp_path/"bundles")
 materializer=module(
  ROOT/"shell/mysql_tpcc_materialize_phase_command_v2.py","v11phasematerializer"
 )
 monkeypatch.setattr(materializer.os,"geteuid",lambda:0)
 monkeypatch.setattr(materializer.os,"getegid",lambda:0)
 token="tpcc-v4-20260730t220000z-0123456789abcdef"
 service,prefault=_v2_phase_evidence(materializer,bundle.name,token)
 for phase in ("qualification","typed_capture"):
  command=materializer.materialize(
   bundle=bundle,service=service,token=token,phase=phase,
   config_in_container="/opt/mysql-tpcc-config/tpcc.xml",
   mysql_defaults_in_container="/opt/mysql-tpcc-config/mysql-client.cnf",
   phase_authority=None,readiness_audit=None,prefault_authority=prefault,
  )
  materializer.validate_materialized_command(command,service,prefault)
  assert command["phase"]==phase
  assert command["capture_only"] is (phase=="typed_capture")
  assert command["docker_exec_argv"][-len(command["argv"]):]==command["argv"]
  stop=command["graceful_stop_request"]
  assert stop["payload_utf8"]=="180\n"
  assert stop["measurement_seconds"]==180
  assert stop["empty_file_prohibited"] is True
  assert stop["touch_command_prohibited"] is True
  assert stop["atomic_create_required"] is True
  assert "write-stop --phase" in stop["sealed_writer_action"]
  classpath=command["production_benchbase_classpath"]
  assert classpath["main_class"]=="com.oltpbenchmark.DBWorkload"
  assert classpath["container_plugin_path"]=="/measurement/config/plugin.xml"
  assert classpath["all_regular_files_manifest_bound"] is True
 old=dict(service); old["schema_version"]="mysql_tpcc_node04_adopted_service_authority_v1"
 with pytest.raises(materializer.PhaseError,match="identity/scope"):
  materializer.validate_service(old,bundle,token)
 bad=json.loads(json.dumps(service));bad["blkio_v1_throttle"]["database_pid"]+=1
 bad_unsigned=dict(bad);bad_unsigned.pop("live_readback_sha256")
 bad["live_readback_sha256"]=materializer.sha_bytes(materializer.canonical(bad_unsigned))
 with pytest.raises(materializer.PhaseError,match="blkio"):
  materializer.validate_service(bad,bundle,token)
 bad_prefault=json.loads(json.dumps(prefault))
 bad_prefault["client_cgroup_io_snapshot_evidence"]["after_prefault"]["absence_as_zero"]=False
 with pytest.raises(materializer.PhaseError,match="snapshot identity"):
  materializer.validate_prefault(bad_prefault,service)
 missing_plugin=json.loads(json.dumps(prefault))
 missing_plugin["prefaulted_target_mount_destinations"].remove("/measurement/config")
 with pytest.raises(materializer.PhaseError,match="destinations differ"):
  materializer.validate_prefault(missing_plugin,service)
 unsorted=json.loads(json.dumps(prefault))
 unsorted["prefaulted_target_mount_destinations"]=list(
  reversed(unsorted["prefaulted_target_mount_destinations"])
 )
 with pytest.raises(materializer.PhaseError,match="destinations differ"):
  materializer.validate_prefault(unsorted,service)
 writable_plugin=json.loads(json.dumps(service))
 writable_plugin["benchbase_plugin_mount"]["read_only"]=False
 with pytest.raises(materializer.PhaseError,match="destinations differ"):
  materializer.validate_prefault(prefault,writable_plugin)


def test_v11_qualification_and_typed_capture_executors_use_v2_full_client_identity(
 tmp_path: Path,monkeypatch: pytest.MonkeyPatch
):
 b=module(BUILDER,"v11executebuilder");bundle=b.build(tmp_path/"bundles")
 materializer=module(
  ROOT/"shell/mysql_tpcc_materialize_phase_command_v2.py","v11phaseexecutor"
 )
 monkeypatch.setattr(materializer.os,"geteuid",lambda:0)
 monkeypatch.setattr(materializer.os,"getegid",lambda:0)
 token="tpcc-v4-20260730t220500z-0123456789abcdef"
 service,prefault=_v2_phase_evidence(materializer,bundle.name,token)
 service_path=tmp_path/"service.json";prefault_path=tmp_path/"prefault.json"
 plan_path=tmp_path/"plan.json";ledger_path=tmp_path/"ledger.json"
 materializer.atomic_json(service_path,service)
 materializer.atomic_json(prefault_path,prefault)
 materializer.atomic_json(plan_path,{"attempt_id":token})
 calls=[]
 class FakeService:
  @staticmethod
  def adopt(plan): return json.loads(json.dumps(service))
  @staticmethod
  def _target_io_snapshot(client_id,pid):
   calls.append((client_id,pid))
   return {"counters":{"rbytes":0,"wbytes":0,"rios":0,"wios":0,
                       "dbytes":0,"dios":0},"authority":{"version":1}}
  @staticmethod
  def _io_delta(before,after):
   return {"rbytes":0,"wbytes":0,"rios":0,"wios":0,"dbytes":0,"dios":0}
 monkeypatch.setattr(materializer,"_service_module",lambda bundle:FakeService)
 monkeypatch.setattr(
  materializer.subprocess,"run",
  lambda argv,**kwargs:subprocess.CompletedProcess(
   argv,0,
   (
    "BENCHBASE_FORMAL_METRICS_JSON="
    '{"measurement_seconds":180,"stop_requested":true,'
    '"stop_request_observed_epoch_ms":1785456000000,'
    '"completed_total":42,"unexpected_errors":0,"outputs_written":true}\n'
   ),
   "",
  ),
 )
 for index,phase in enumerate(("qualification","typed_capture")):
  command=materializer.materialize(
   bundle=bundle,service=service,token=token,phase=phase,
   config_in_container="/opt/mysql-tpcc-config/tpcc.xml",
   mysql_defaults_in_container="/opt/mysql-tpcc-config/mysql-client.cnf",
   phase_authority=None,readiness_audit=None,prefault_authority=prefault,
  )
  command_path=tmp_path/f"{phase}.json"
  materializer.atomic_json(command_path,command)
  assert materializer.execute(
   bundle,plan_path,service_path,command_path,ledger_path,None,prefault_path
  )==0
  assert calls[index*2:(index+1)*2]==[
   (service["client"]["container_id"],service["client"]["pid"]),
   (service["client"]["container_id"],service["client"]["pid"]),
  ]
 ledger=materializer.load(ledger_path)
 assert list(materializer._ordered_phase_names(ledger["phases"]))==[
  "qualification","typed_capture",
 ]
 qualification=ledger["phases"]["qualification"]
 assert qualification["benchbase_formal_metrics"]["measurement_seconds"]==180
 assert qualification["benchbase_formal_metrics"]["stop_requested"] is True
 evidence=qualification["execution_evidence"]
 assert evidence["status"]=="PASS_TERMINAL_GATE"
 assert Path(evidence["stdout_path"]).read_text().startswith(
  "BENCHBASE_FORMAL_METRICS_JSON="
 )
 assert Path(evidence["stderr_path"]).read_text()==""


def test_v11_qualification_terminal_metrics_fail_closed():
 materializer=module(
  ROOT/"shell/mysql_tpcc_materialize_phase_command_v2.py",
  "v11qualificationterminalgate",
 )
 good={
  "measurement_seconds":180,"stop_requested":True,
  "stop_request_observed_epoch_ms":1785456000000,
  "completed_total":1,"unexpected_errors":0,"outputs_written":True,
 }
 line=lambda value:(
  "BENCHBASE_FORMAL_METRICS_JSON="+json.dumps(value,separators=(",",":"))+"\n"
 )
 assert materializer.qualification_terminal_metrics(line(good),"")==good
 invalid=[
  ({**good,"measurement_seconds":179},""),
  ({**good,"measurement_seconds":601},""),
  ({**good,"stop_requested":False},""),
  ({**good,"stop_request_observed_epoch_ms":None},""),
  ({**good,"completed_total":0},""),
  ({**good,"unexpected_errors":1},""),
  ({**good,"outputs_written":False},""),
 ]
 for metrics,stderr in invalid:
  with pytest.raises(materializer.PhaseError):
   materializer.qualification_terminal_metrics(line(metrics),stderr)
 with pytest.raises(materializer.PhaseError,match="exactly one"):
  materializer.qualification_terminal_metrics("", "")
 with pytest.raises(materializer.PhaseError,match="exactly one"):
  materializer.qualification_terminal_metrics(line(good)+line(good), "")
 with pytest.raises(materializer.PhaseError,match="stop-file protocol error"):
  materializer.qualification_terminal_metrics(
   line({**good,"stop_requested":False}),
   "java.lang.IllegalArgumentException: stop request must be decimal\n",
  )
 with pytest.raises(materializer.PhaseError,match="malformed"):
  materializer.qualification_terminal_metrics(
   line(good).replace('"completed_total":1','"completed_total":NaN'),""
  )


def test_v11_failed_qualification_persists_logs_without_pass_ledger(
 tmp_path: Path,monkeypatch: pytest.MonkeyPatch
):
 b=module(BUILDER,"v11failedexecutebuilder");bundle=b.build(tmp_path/"bundles")
 materializer=module(
  ROOT/"shell/mysql_tpcc_materialize_phase_command_v2.py",
  "v11failedphaseexecutor",
 )
 monkeypatch.setattr(materializer.os,"geteuid",lambda:0)
 monkeypatch.setattr(materializer.os,"getegid",lambda:0)
 token="tpcc-v4-20260730t221000z-0123456789abcdef"
 service,prefault=_v2_phase_evidence(materializer,bundle.name,token)
 service_path=tmp_path/"service.json";prefault_path=tmp_path/"prefault.json"
 plan_path=tmp_path/"plan.json";ledger_path=tmp_path/"ledger.json"
 for path,value in (
  (service_path,service),(prefault_path,prefault),(plan_path,{"attempt_id":token}),
 ):
  materializer.atomic_json(path,value)
 class FakeService:
  @staticmethod
  def adopt(plan): return json.loads(json.dumps(service))
  @staticmethod
  def _target_io_snapshot(client_id,pid):
   return {"counters":{"rbytes":0,"wbytes":0,"rios":0,"wios":0,
                       "dbytes":0,"dios":0},"authority":{"version":1}}
  @staticmethod
  def _io_delta(before,after):
   return {"rbytes":0,"wbytes":0,"rios":0,"wios":0,"dbytes":0,"dios":0}
 monkeypatch.setattr(materializer,"_service_module",lambda bundle:FakeService)
 bad_metrics=(
  "BENCHBASE_FORMAL_METRICS_JSON="
  '{"measurement_seconds":600,"stop_requested":false,'
  '"stop_request_observed_epoch_ms":null,"completed_total":99,'
  '"unexpected_errors":0,"outputs_written":true}\n'
 )
 monkeypatch.setattr(
  materializer.subprocess,"run",
  lambda argv,**kwargs:subprocess.CompletedProcess(
   argv,0,bad_metrics,
   "java.lang.IllegalArgumentException: stop request must be decimal\n",
  ),
 )
 command=materializer.materialize(
  bundle=bundle,service=service,token=token,phase="qualification",
  config_in_container="/opt/mysql-tpcc-config/tpcc.xml",
  mysql_defaults_in_container="/opt/mysql-tpcc-config/mysql-client.cnf",
  phase_authority=None,readiness_audit=None,prefault_authority=prefault,
 )
 command_path=tmp_path/"qualification.json"
 materializer.atomic_json(command_path,command)
 with pytest.raises(materializer.PhaseError,match="stop-file protocol error"):
  materializer.execute(
   bundle,plan_path,service_path,command_path,ledger_path,None,prefault_path
  )
 assert not ledger_path.exists()
 receipts=list(
  (tmp_path/"ledger.json.execution_evidence").glob(
   "qualification-*/execution_receipt.json"
  )
 )
 assert len(receipts)==1
 receipt=materializer.load(receipts[0])
 assert receipt["status"]=="FAIL_TERMINAL_GATE_NO_LEDGER_CREDIT"
 assert receipt["returncode"]==0
 assert receipt["qualification_terminal_metrics"] is None
 assert "stop request must be decimal" in Path(receipt["stderr_path"]).read_text()


def test_v11_service_plan_mounts_manifest_bound_plugin_into_measurement_workdir(
 tmp_path: Path,monkeypatch: pytest.MonkeyPatch
):
 service=module(ROOT/"shell/mysql_tpcc_node04_adopted_service_v2.py","v11pluginplan")
 monkeypatch.setattr(service.os,"geteuid",lambda:0)
 monkeypatch.setattr(service.os,"getegid",lambda:0)
 attempt="tpcc-v4-20260730t223000z-0123456789abcdef"
 runtime=tmp_path/"runtime";bundle=runtime/"bundles"/("a"*64)
 plugin=bundle/"benchbase_runtime/config/plugin.xml"
 plugin.parent.mkdir(parents=True)
 plugin.write_bytes((ROOT/"goal_20260718/benchbase/config/plugin.xml").read_bytes())
 monkeypatch.setattr(service.base,"RUNTIME_ROOT",str(runtime))
 image="sha256:"+"b"*64
 def base_plan(**kwargs):
  return {
   "bundle_path":str(bundle),"expected_image_id":image,
   "database":{"datadir":str(runtime/attempt/"datadir"),
               "create_argv":["old"]},
   "client":{"create_argv":["docker","create",image,"sleep"]},
  }
 monkeypatch.setattr(service.base,"build_plan",base_plan)
 monkeypatch.setattr(
  service,"_verify_client_private_config",
  lambda path:{
   "configured_user":"1008:1008","uid":1008,"gid":1008,
   "directory_mode":"0700",
   "ownership_transactional_before_publish":True,
   "authority_path":str(runtime/attempt/"config_materialization.json"),
   "authority_sha256":"e"*64,"authority_mode":"0600",
   "files":{
    "mysql.cnf":{"sha256":"c"*64,"bytes":1,"mode":"0600",
                 "uid":1008,"gid":1008},
    "tpcc.xml":{"sha256":"d"*64,"bytes":1,"mode":"0600",
                "uid":1008,"gid":1008},
   },
  },
 )
 monkeypatch.setattr(
  service,"_verify_measurement_bridge",
  lambda plan:{"schema_version":"mysql_tpcc_measurement_bridge_authority_v1"},
 )
 plan=service.build_plan(
  bundle=bundle,image_id=image,config_dir=runtime/attempt/"config",
  datadir=runtime/attempt/"datadir"/"datadir",
  measurement_bridge=runtime/attempt/"measurement",
  attempt_id=attempt,handoff_token=attempt,
 )
 mount=(
  "type=bind,src="+str(bundle/"benchbase_runtime/config")+
  ",dst=/measurement/config,readonly"
 )
 create=plan["client"]["create_argv"]
 assert create[create.index(image)-4:create.index(image)]==[
  "--user","1008:1008","--mount",mount,
 ]
 assert plan["client"]["configured_user"]=="1008:1008"
 assert plan["benchbase_plugin_mount"]=={
  "source":str(bundle/"benchbase_runtime/config"),
  "destination":"/measurement/config","plugin_relative":"plugin.xml",
  "plugin_sha256":"a770865560e0d1044c98a2954bacfd3d0c58ed3e956d3e783b2e830c7951d7e2",
 "read_only":True,
 }


def test_v11_client_user_and_capless_readability_are_live_bound(
 monkeypatch: pytest.MonkeyPatch
):
 service=module(ROOT/"shell/mysql_tpcc_node04_adopted_service_v2.py","v11clientuser")
 client_id="8"*64
 inspect={"Config":{"User":"1008:1008"},"State":{"Pid":4242}}
 identity={"container_id":client_id,"pid":4242,"process_start_ticks":919191}
 monkeypatch.setattr(service.base,"_expect_inspect",lambda *args,**kwargs:dict(identity))
 monkeypatch.setattr(service.base,"_proc_start_ticks",lambda pid:919191)
 client=service._expect_client_inspect(inspect,{"client":{}})
 assert client["configured_user"]=="1008:1008"
 with pytest.raises(service.ServiceError,match="configured user"):
  service._expect_client_inspect(
   {"Config":{"User":""},"State":{"Pid":4242}},{"client":{}}
  )
 private={
  "files":{
   "mysql.cnf":{"sha256":"6"*64},
   "tpcc.xml":{"sha256":"7"*64},
  },
 }
 paths={
  name:{"path":path,"bytes_read":1,"sha256":None}
  for name,path in service.CLIENT_READABILITY_PATHS.items()
 }
 paths["plugin"]["sha256"]=service.BENCHBASE_PLUGIN_SHA256
 paths["tpcc_config"]["sha256"]="7"*64
 paths["mysql_defaults"]["sha256"]="6"*64
 evidence={
  "effective_uid":1008,"effective_gid":1008,
  "effective_capabilities_hex":"0000000000000000","paths":paths,
  "measurement_write":{
   "directory":"/measurement","bytes":27,
   "file_uid":1008,"file_gid":1008,"file_mode":"0600",
  },
 }
 calls=[]
 def run(argv,**kwargs):
  calls.append(argv)
  return subprocess.CompletedProcess(argv,0,json.dumps(evidence),"")
 monkeypatch.setattr(service.base,"_run",run)
 result=service._client_readability_smoke(
  {"client":{"name":"sealed-client"},"client_private_config":private},
  client,
 )
 assert result["status"]=="PASS"
 assert calls[0][:2]==["docker","exec"] and "--user" not in calls[0]
 bad=json.loads(json.dumps(evidence))
 bad["effective_capabilities_hex"]="0000000000000001"
 monkeypatch.setattr(
  service.base,"_run",
  lambda argv,**kwargs:subprocess.CompletedProcess(argv,0,json.dumps(bad),""),
 )
 with pytest.raises(service.ServiceError,match="read-only mount smoke differs"):
  service._client_readability_smoke(
   {"client":{"name":"sealed-client"},"client_private_config":private},
   client,
  )


def test_v11_static_spec_orders_secret_before_config_and_mounts_inner_root(tmp_path: Path):
 b=module(BUILDER,"v11builder_spec"); bundle=b.build(tmp_path)
 s=module(SPEC,"v11spec"); token="tpcc-v4-20260730t193000z-0123456789abcdef"
 s.BUNDLE_ID=bundle.name; s.base.BUNDLE_ID=bundle.name
 s.base.BUNDLE_REMOTE=s.base.RUNTIME_ROOT+"/bundles/"+bundle.name
 s.base.SERVICE_REMOTE=s.base.BUNDLE_REMOTE+"/authority/mysql_tpcc_node04_adopted_service_v2.py"
 s.base.MATERIALIZER_REMOTE=s.base.BUNDLE_REMOTE+"/authority/mysql_tpcc_materialize_phase_command_v2.py"
 s.base.CONFIG_MATERIALIZER_REMOTE=s.base.BUNDLE_REMOTE+"/authority/mysql_tpcc_config_materialize_v2.py"
 s.MEASUREMENT_BRIDGE_HELPER_REMOTE=s.base.BUNDLE_REMOTE+"/authority/mysql_tpcc_measurement_bridge_v1.py"
 doc=s.make_spec(bundle/"bundle_manifest.json",token,token)
 plan=s.command_plan(doc); commands=plan["commands_to_run_on_node04"]
 assert all(commands[name][:2]==["sudo","-n"] for name in s.ROOT_REQUIRED_PHASES)
 assert plan["inner_mysql_datadir"].endswith("/datadir/datadir")
 assert "--image-id" in commands["prepare_physical_copy"]
 bridge=commands["enforce_private_measurement_tmpfs_after_copy"]
 assert bridge[:3]==["sudo","-n",s.base.HOST_PYTHON]
 assert bridge[3].endswith("/authority/mysql_tpcc_measurement_bridge_v1.py")
 assert commands["provision_random_attempt_password_before_config"][4]=="provision-password"
 assert commands["start_containers_after_main_agent_authorization"][4]=="start"
 assert commands["start_containers_after_main_agent_authorization"][-1].endswith("/start_authority.json")
 blkio=commands["enforce_sealed_cgroup_v1_blkio_after_start_before_adopt"]
 assert blkio[:3]==["sudo","-n",s.base.HOST_PYTHON] and blkio[3].endswith("/authority/mysql_tpcc_blkio_v1.py")
 assert commands["adopt_same_live_services_after_blkio_readback"][-1].endswith("/adopted_service.json")
 stop=commands["atomically_request_qualification_stop_after_180s"]
 assert stop[:3]==["sudo","-n",s.base.HOST_PYTHON]
 assert stop[4:6]==["write-stop","--bundle"]
 assert stop[-4:]==["--phase","qualification","--seconds","180"]
 names=list(commands)
 assert names.index("prepare_physical_copy") < names.index("enforce_private_measurement_tmpfs_after_copy") < names.index("emit_service_plan_only")
 assert names.index("start_containers_after_main_agent_authorization") < names.index("enforce_sealed_cgroup_v1_blkio_after_start_before_adopt") < names.index("adopt_same_live_services_after_blkio_readback")
 outputs={
  name:value[value.index("--output")+1]
  for name,value in commands.items()
  if name in {
   "start_containers_after_main_agent_authorization",
   "enforce_private_measurement_tmpfs_after_copy",
   "enforce_sealed_cgroup_v1_blkio_after_start_before_adopt",
   "adopt_same_live_services_after_blkio_readback",
   "prefault_zero_io_gate",
  }
 }
 assert len(set(outputs.values()))==5
 assert outputs["enforce_private_measurement_tmpfs_after_copy"].endswith("/measurement_bridge_authority.json")
 assert outputs["start_containers_after_main_agent_authorization"].endswith("/start_authority.json")
 assert outputs["enforce_sealed_cgroup_v1_blkio_after_start_before_adopt"].endswith("/blkio_throttle_authority.json")
 assert outputs["adopt_same_live_services_after_blkio_readback"].endswith("/adopted_service.json")
 assert outputs["prefault_zero_io_gate"].endswith("/prefault_zero_io.json")
 payload=json.dumps(plan,sort_keys=True)
 assert "1954fa4bfd4401a9026638926dee7690c0f7c44c1b9628380dffad2b8807d51c" not in payload
 assert "0068f06905d2f9c3fa71a8ecd573ed597846702e1d7733466c9c52c06a395b35" not in payload
 assert "48abd6390d7f0d87f8c237830e0dfcbef7f9a7218ca2a42c3ec0cef634851344" not in payload
 assert "7ec89c2ea72b47d2798efacf106560b34e3308eda64c12a5e84555a212d4bf17" not in payload
 assert "acb45d8886729d8fd389197ba1ba3b21cdc03ceeebc0690f7e87fb5111638d84" not in payload
 assert "45ada2d5a371b96f8349b4c69b458f4cc020322da94f0a3081cb4a9f423484f1" not in payload
 assert "d82884f3ed79ef1491beb0a405df4b7933ec7142b5a7612dda035f4849895fdf" not in payload
 assert "c58caf2abc6fab87b9ca73b7e44e565ee87d8f75866a369b806f43c4ddce37dc" not in payload
 assert "96e0bde80a06c85c56417e465b100b839b99f7d2c53b881e12f88bb0d74d4033" not in payload
 assert set(part[:64] for part in payload.split(s.base.RUNTIME_ROOT+"/bundles/")[1:])=={bundle.name}
 assert "sealed_host_root_cgroup_v1_blkio_259_2_1205862400_PASS_after_start_before_adopt" in plan["required_preconditions"]
 assert "sealed_host_root_measurement_tmpfs_1008_1008_0700_PASS_before_service_plan" in plan["required_preconditions"]
 assert plan["formal_r0_authorized"] is False and plan["accepted_round_authorized"] is False


def test_v11_credential_reset_uses_stdin_and_preserves_secret_on_failure(
 tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
 materializer=module(ROOT/"shell/mysql_tpcc_config_materialize_v2.py","v11credentials")
 monkeypatch.setattr(materializer.os,"geteuid",lambda:0)
 monkeypatch.setattr(materializer.os,"getegid",lambda:0)
 attempt="tpcc-v4-20260730t193000z-0123456789abcdef"
 monkeypatch.setattr(materializer.base,"RUNTIME_ROOT",str(tmp_path))
 monkeypatch.setattr(materializer.base,"verify_bundle",lambda bundle:{"bundle_id":"a"*64})
 datadir=tmp_path/attempt/"datadir"/"datadir"; datadir.mkdir(parents=True); (tmp_path/attempt).chmod(0o700); (datadir/"ibdata1").write_bytes(b"x")
 password_file=tmp_path/attempt/"mysql-password"; output=tmp_path/attempt/"credential_provisioning.json"; calls=[]
 def run(argv,**kwargs):
  calls.append((argv,kwargs))
  if argv[:3]==["docker","run","-d"]:
   assert password_file.is_file() and (password_file.stat().st_mode&0o777)==0o600
  return subprocess.CompletedProcess(argv,0,"","")
 monkeypatch.setattr(materializer.subprocess,"run",run)
 result=materializer.provision(tmp_path/"bundle","sha256:"+"a"*64,attempt,datadir,password_file,output)
 reset=[call for call in calls if call[0][:3]==["docker","exec","-i"]]
 assert len(reset)==1 and reset[0][0]==["docker","exec","-i","ra_mysql_tpcc_v11_credential_"+attempt,"mysql","-uroot"]
 assert "-e" not in reset[0][0] and "-NBe" not in reset[0][0]
 assert "ALTER USER 'root'@'%'" in reset[0][1]["input"]
 assert password_file.read_text().strip() not in reset[0][0]
 assert password_file.stat().st_uid==os.getuid() and output.stat().st_uid==os.getuid()
 assert result["host_effective_uid_gid"]==[0,0]
 assert result["formal_launch_authorized"] is False and output.is_file()
 failing_root=tmp_path/"failed"; monkeypatch.setattr(materializer.base,"RUNTIME_ROOT",str(failing_root))
 bad_attempt="tpcc-v4-20260730t193001z-0123456789abcdef"; bad_datadir=failing_root/bad_attempt/"datadir"/"datadir"; bad_datadir.mkdir(parents=True); (failing_root/bad_attempt).chmod(0o700); (bad_datadir/"ibdata1").write_bytes(b"x")
 bad_password=failing_root/bad_attempt/"mysql-password"; bad_output=failing_root/bad_attempt/"credential_provisioning.json"
 def fail_reset(argv,**kwargs):
  rc=1 if argv[:3]==["docker","exec","-i"] else 0
  return subprocess.CompletedProcess(argv,rc,"","reset failure")
 monkeypatch.setattr(materializer.subprocess,"run",fail_reset)
 with pytest.raises(materializer.base.ConfigError,match="root reset"):
  materializer.provision(tmp_path/"bundle","sha256:"+"a"*64,bad_attempt,bad_datadir,bad_password,bad_output)
 assert bad_password.is_file() and (bad_password.stat().st_mode&0o777)==0o600
 assert not bad_output.exists()


def test_v11_materialize_contract_rejects_nonprivate_attempt_root(
 tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
 materializer=module(ROOT/"shell/mysql_tpcc_config_materialize_v2.py","v11private_root")
 attempt="tpcc-v4-20260730t193002z-0123456789abcdef"; root=tmp_path/attempt; root.mkdir(); root.chmod(0o755)
 monkeypatch.setattr(materializer.base,"RUNTIME_ROOT",str(tmp_path))
 with pytest.raises(materializer.base.ConfigError,match="exact 0700"):
  materializer._attempt_owner(root)


def test_v11_config_owner_is_transactional_before_publish(
 tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
 builder=module(BUILDER,"v11configownerbuilder")
 bundle=builder.build(tmp_path/"bundles")
 materializer=module(
  ROOT/"shell/mysql_tpcc_config_materialize_v2.py","v11configowner"
 )
 monkeypatch.setattr(materializer.os,"geteuid",lambda:0)
 monkeypatch.setattr(materializer.os,"getegid",lambda:0)
 runtime=tmp_path/"runtime"
 monkeypatch.setattr(materializer.base,"RUNTIME_ROOT",str(runtime))
 monkeypatch.setattr(materializer,"CLIENT_UID",os.getuid())
 monkeypatch.setattr(materializer,"CLIENT_GID",os.getgid())
 attempt="tpcc-v4-20260731t001000z-0123456789abcdef"
 root=runtime/attempt;root.mkdir(parents=True,mode=0o700);root.chmod(0o700)
 password=root/"mysql-password";password.write_text("test-only-password\n")
 password.chmod(0o600)
 credential=root/"credential_provisioning.json"
 credential.write_text(json.dumps({
  "schema_version":"mysql_tpcc_attempt_credential_provisioning_v1",
  "status":"PASS_SEALED_TEMPORARY_SKIP_GRANT_RESET",
  "bundle_id":bundle.name,"attempt_id":attempt,
  "host_effective_uid_gid":[0,0],
  "credential_file":str(password),"credential_file_mode":"0600",
  "formal_launch_authorized":False,"accepted_round_authorized":False,
 }))
 credential.chmod(0o600)
 config=root/"config";authority=root/"config_materialization.json"
 value=materializer.materialize(bundle,attempt,config,password,authority)
 assert value["host_effective_uid_gid"]==[0,0]
 assert len(value["credential_provisioning_authority_sha256"])==64
 assert value["ownership_transactional_before_publish"] is True
 assert value["config_dir_uid"]==os.getuid()
 assert value["config_dir_gid"]==os.getgid()
 assert stat.S_IMODE(config.stat().st_mode)==0o700
 assert authority.stat().st_uid==os.getuid()
 assert authority.stat().st_gid==os.getgid()
 assert stat.S_IMODE(authority.stat().st_mode)==0o600
 for name,evidence in value["files"].items():
  path=config/name
  assert evidence["uid"]==os.getuid() and path.stat().st_uid==os.getuid()
  assert evidence["gid"]==os.getgid() and path.stat().st_gid==os.getgid()
  assert evidence["mode"]=="0600"
  assert stat.S_IMODE(path.stat().st_mode)==0o600

 failing_attempt="tpcc-v4-20260731t001001z-0123456789abcdef"
 failing_root=runtime/failing_attempt
 failing_root.mkdir(parents=True,mode=0o700);failing_root.chmod(0o700)
 failing_password=failing_root/"mysql-password"
 failing_password.write_text("test-only-password\n");failing_password.chmod(0o600)
 failing_credential=failing_root/"credential_provisioning.json"
 failing_credential.write_text(json.dumps({
  "schema_version":"mysql_tpcc_attempt_credential_provisioning_v1",
  "status":"PASS_SEALED_TEMPORARY_SKIP_GRANT_RESET",
  "bundle_id":bundle.name,"attempt_id":failing_attempt,
  "host_effective_uid_gid":[0,0],
  "credential_file":str(failing_password),"credential_file_mode":"0600",
  "formal_launch_authorized":False,"accepted_round_authorized":False,
 }))
 failing_credential.chmod(0o600)
 real_chown=materializer.base.os.chown
 def fail_stage_chown(path,uid,gid):
  if ".config-stage-" in str(path):
   raise OSError("injected stage ownership failure")
  return real_chown(path,uid,gid)
 monkeypatch.setattr(materializer.base.os,"chown",fail_stage_chown)
 with pytest.raises(OSError,match="injected stage"):
  materializer.materialize(
   bundle,failing_attempt,failing_root/"config",failing_password,
   failing_root/"config_materialization.json",
  )
 assert not (failing_root/"config").exists()
 assert not (failing_root/"config_materialization.json").exists()
 assert not list(failing_root.glob(".config-stage-*"))


def test_v11_root_required_authorities_fail_closed_for_nonroot(
 monkeypatch: pytest.MonkeyPatch
):
 config=module(
  ROOT/"shell/mysql_tpcc_config_materialize_v2.py","v11nonrootconfig"
 )
 service=module(
  ROOT/"shell/mysql_tpcc_node04_adopted_service_v2.py","v11nonrootservice"
 )
 phase=module(
  ROOT/"shell/mysql_tpcc_materialize_phase_command_v2.py","v11nonrootphase"
 )
 for subject in (config,service,phase):
  monkeypatch.setattr(subject.os,"geteuid",lambda:1002)
  monkeypatch.setattr(subject.os,"getegid",lambda:1002)
 with pytest.raises(config.base.ConfigError,match="effective uid/gid 0"):
  config._require_host_root()
 with pytest.raises(service.ServiceError,match="effective uid/gid 0"):
  service._require_host_root()
 with pytest.raises(phase.PhaseError,match="effective uid/gid 0"):
  phase._require_host_root()

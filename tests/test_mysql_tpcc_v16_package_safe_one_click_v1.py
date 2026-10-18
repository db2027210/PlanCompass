import hashlib,importlib.util,json,os,stat,subprocess,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];SOURCE=(ROOT/"controller/mysql_tpcc_v16_package_safe_one_click_v1.py" if (ROOT/"controller/mysql_tpcc_v16_package_safe_one_click_v1.py").is_file() else ROOT/"shell/mysql_tpcc_v16_package_safe_one_click_v1.py")
spec=importlib.util.spec_from_file_location("oneclick",SOURCE);M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
def seal(path,value):
 path.write_bytes(M.canonical(value));Path(str(path)+".sha256").write_text(hashlib.sha256(path.read_bytes()).hexdigest()+"  "+path.name+"\n")
def test_b235_local_authority_chain_and_correct_adopted_path_are_pinned():
 assert set(M.BUNDLES)=={"tpch_ff","collector","current","a336","tpcc_v16","local_flow","blkio_sealer"}
 assert M.BUNDLES["tpch_ff"] == ("b235d1ca300e5ad02fe77005096fc511bfbdc5d91b9601233a66a10502be5a47", "386f59b23f20eb7be59665c42c44fe2453512bf2112d850e12df6b398a16f439")
 assert M.BUNDLES["current"] == ("8007386a577296be2b9510c7b512399137e2131ad16c1b7f427967062844f888", "d75b93ccf75d2c5da11aee7a9550d77cb1c1dffe11b5aa89bbd26cb68e31e713")
 assert M.BUNDLES["local_flow"] == ("f04674db368b743d175e8e0c5f781dd659ec4b7b01ebb50e8381a20671310ddb", "7ae7204536f685eb5ba45f54b3a4bfd34ef477b19aea1713a2221e31e9286231")
 text=SOURCE.read_text()
 for obsolete in ("82fc38d14bd511df73e44d077779db600c46e048a441cbad9451952ad822eb2c","2c9f4e83e232ddfbdf66c15333891bd81d5d3e11d22c7c2c83091ca33dea0e53","26200b2fe311caa2e317d5da27c79097f93bce095c83c09faa55019d9eab65a1","ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4","13cce818601022f566e7b9f48f71818cef4ae0be7aa335e46f965aefaf717d37"):assert obsolete not in text
 assert M.CUR/M.Path("adopted_service.json")==M.TPCC_ROOT/M.TPCC_ATTEMPT/"adopted_service.json"
def test_no_jq_ssh_or_import_execution_path():
 text=SOURCE.read_text();assert '"/usr/bin/jq"' not in text and '["ssh"' not in text.lower() and "spec_from_file_location" not in text
 assert "PYTHONPATH\":\"\"" in text and "postgresql_mutation_prohibited\":True" in text
def test_sealed_json_is_fail_closed(tmp_path):
 path=tmp_path/"x.json";seal(path,{"x":1});assert M.sealed(path,"x")[1]=={"x":1}
 path.write_text('{"x":2}')
 with pytest.raises(M.NotReady,match="seal"):M.sealed(path,"x")
def test_output_sealer_writes_a_strict_sidecar(tmp_path):
 path=tmp_path/"out.json";M.seal_output(path,{"x":1});assert M.sealed(path,"out")[1]=={"x":1}
def test_real_workload_pid_extraction_is_role_bound():
 value={"processes":[{"pid":9,"role":"qualification_workload","ppid":2,"cmdline_sha256":"a"*64},{"pid":8,"role":"database"},{"pid":9,"role":"qualification_workload"}]}
 assert [row["pid"] for row in M.workload_rows(value)]==[9]
def test_a336_content_addressed_stdout_is_parsed(tmp_path):
 stdout=tmp_path/"a336.stdout";stdout.write_text(json.dumps({"status":"PASS_READ_ONLY_LIVE_IDENTITY_NOT_CAPTURE_AUTHORITY","authority":str(tmp_path/"abc/same_live_snapshot.json"),"authority_id":"abc"}))
 assert M.subprocess_json(stdout,"a336")["authority_id"]=="abc"
def test_noexec_style_direct_denial_is_bypassed_only_by_fixed_bin_sh(tmp_path):
 inner=tmp_path/"inner.sh";inner.write_text("#!/bin/sh\nexit 0\n");inner.chmod(0o400);outer=tmp_path/"outer.sh";outer.write_text("#!/bin/sh\n/bin/sh "+str(inner)+"\n");outer.chmod(0o400)
 with pytest.raises(PermissionError):subprocess.run([str(outer)],check=False)
 assert subprocess.run(["/bin/sh",str(outer)],check=False).returncode==0
def test_launch_uses_fixed_interpreter_without_chmod_or_copy():
 text=SOURCE.read_text();needle='subprocess.Popen(["/bin/sh",str(launch)]';assert needle in text and '#!/bin/sh\\nset -eu\\n' in text
 launch_region=text[text.index("if local_only:"):text.index("def main(")];assert "chmod(" not in launch_region and "copy(" not in launch_region
def test_node04_systemd219_help_contract_is_fail_closed():
 text=SOURCE.read_text()
 for term in ('mysql_tpcc_v16_local_parallel_systemd219_plan_v1','mysql_tpcc_local_systemd219_argv_v1','--service-type=oneshot','--remain-after-exit','legacy_property_options_used','wait_or_collect_options_used'):assert term in text
def test_local_mode_only_launches_tpcc_and_records_real_pid():
 text=SOURCE.read_text();assert 'needed={"tpch_ff","collector","current","a336","tpcc_v16","local_flow","blkio_sealer"}' in text
 assert '"tpcc_workloads_started":1,"tpch_workloads_started":0,"tpch_mutation_performed":False' in text and 'PASS_TPCC_ONLY_WITH_TPCH_READ_ONLY_STABLE' in text and 'real_workload_pids":{"tpcc_local":rows}' in text and 'PASS_VALIDATED_LIVE_BLKIO_AUTHORITY_SIDECAR' in text and '6822e90cc8e6980b6b0030f02f9a2b53a7b5fc23eeb58f214ea26de5a585661d' in text
 assert '--tpch-bootstrap-spec' in text and '--tpch-bootstrap-evidence' in text and '--tpch-defaults-file' in text
 assert 'tpch_spec.get("attempt_token") != TPCH_ATTEMPT' not in text and 'choices=("local",)' in text
 assert 'old_refresh_chain_used":False' in text and 'ff313_continuity_claimed":False' in text
def test_b235_dual_principal_defaults_contract_is_exact_and_non_mutating():
 text=SOURCE.read_text()
 for term in ('def dual_principal_tpch_defaults','path.lstat()','credentials.lstat()','(1008,0,0o440)','(1008,0,0o750)','attempt_root.name!=attempt','evidence.get("spec_sha256")!=hashlib.sha256(canonical(spec)).hexdigest()','credential_chmod_or_chown_performed":False'):assert term in text
 region=text[text.index('def dual_principal_tpch_defaults'):text.index('def tmpfs')]
 assert 'chmod(' not in region and 'chown(' not in region
def test_zero_credit_final_contract_terms_present():
 text=SOURCE.read_text()
 for term in ('verify_self()','a336_result["authority"]','snapshot_path.parent.name!=snapshot_id','explicit_b235_tpch_inputs.authority.json','stage_logs','accepted_rounds_credit":0','capture_not_run":True','fresh_r0_not_run":True','completion_path','real_workload_pids','PASS_EXPLICIT_B235_A336_BLKIO_CURRENT_TPCC_LOCAL_REAL_PID_ZERO_CREDIT'):assert term in text

#!/usr/bin/env python3
"""Build the immutable, package-safe fresh-b235 Retry9 one-click bundle."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
CONTROLLER = ROOT / "shell/mysql_tpch_retry9_one_click_v1.py"
OUT = ROOT / "artifacts/mysql_tpch_retry9_one_click_v1"
DEPENDENCIES = {
    "bootstrap_renderer": (
        ROOT / "artifacts/mysql_tpch_bootstrap_renderer_successor_v1/a496ad36e202ed7ded0f4910fa16a9690bcc590d7d33de4f574fe7aba63e4226",
        "a496ad36e202ed7ded0f4910fa16a9690bcc590d7d33de4f574fe7aba63e4226",
        "f82f0d04d8f59b67d687f245874db6eeaf9c937d65e4add12464dbf0a3cf600a",
    ),
    "support": (
        ROOT / "artifacts/mysql_tpch_deadline_shutdown_collector_successor_v1/a070e44ed4ca1acb0f5b4e0fe03386c6fc0dd43736d0f477cdd17a006bd465b6",
        "a070e44ed4ca1acb0f5b4e0fe03386c6fc0dd43736d0f477cdd17a006bd465b6",
        "a529c3aa35884314893afdd50b093a251dbf31c6a83d9ff797df052c960a7dce",
    ),
    "materializer": (
        ROOT / "artifacts/mysql_tpch_qualification_retry5_completion_materializer_v1/819640d73e8b85a4547b2f4a09b5477eac4bcd19f425d46cac9764b01e3a57af",
        "819640d73e8b85a4547b2f4a09b5477eac4bcd19f425d46cac9764b01e3a57af",
        "fef5a7cbd3a93a5bd190036240492ffa5dabe495a8a701f77bc3e05bca978480",
    ),
    "wrapper": (
        ROOT / "artifacts/mysql_tpch_retry5_systemd219_completion_v1/4849dd8600122990f77130206630a78b789ba3da6770c6754f940226c2d8eb67",
        "4849dd8600122990f77130206630a78b789ba3da6770c6754f940226c2d8eb67",
        "9fee0bfe0f0258c395464da121732842b1f8e7c929f270d829f8e0e6cd6b801e",
    ),
}
FAILURE = ROOT / "preflight/mysql_tpch_retry8_failure_exact_v1.json"


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_map(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): sha_file(path)
            for path in sorted(root.rglob("*")) if path.is_file()
            and path.relative_to(root).as_posix() not in
            ("bundle_manifest.json", "bundle_manifest.json.sha256")}


def verify_dependency(path: Path, bundle_id: str, manifest_sha: str) -> None:
    manifest_path = path / "bundle_manifest.json"
    sidecar = path / "bundle_manifest.json.sha256"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    unsigned = dict(manifest)
    unsigned.pop("bundle_id", None)
    if (path.name != bundle_id or manifest.get("bundle_id") != bundle_id
            or sha_file(manifest_path) != manifest_sha
            or sidecar.read_text(encoding="ascii")
            != manifest_sha + "  bundle_manifest.json\n"
            or sha_bytes(canonical(unsigned)) != bundle_id
            or file_map(path) != manifest.get("files")
            or manifest.get("formal_scope") != "mysql_only"
            or manifest.get("cross_engine_comparison_prohibited") is not True
            or manifest.get("formal_launch_authorized") is not False
            or manifest.get("accepted_round_authorized") is not False):
        raise BuildError("dependency closure differs: " + str(path))


TEST = r'''from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

BUNDLE=Path(__file__).resolve().parents[1]
SOURCE=BUNDLE/"controller/mysql_tpch_retry9_one_click_v1.py"
spec=importlib.util.spec_from_file_location("retry9_one_click",SOURCE);M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)

def test_package_is_content_addressed_read_only_and_self_contained():
 manifest=json.loads((BUNDLE/"bundle_manifest.json").read_text());unsigned=dict(manifest);unsigned.pop("bundle_id");payload=(json.dumps(unsigned,sort_keys=True,indent=2)+"\n").encode();assert hashlib.sha256(payload).hexdigest()==manifest["bundle_id"]==BUNDLE.name
 assert manifest["package_safe"] is True and manifest["remote_actions"]==0
 for path in (BUNDLE,*BUNDLE.rglob("*")):assert not path.is_symlink() and stat.S_IMODE(path.stat().st_mode)&0o222==0

def test_all_runtime_dependencies_are_inside_package():
 paths=M.package_paths();assert all(path.is_relative_to(BUNDLE) for path in paths.values())
 M.verify_package()

def test_bundle_root_write_bits_are_rejected(tmp_path):
 root=tmp_path/M.TARGET_ID;root.mkdir(mode=0o700)
 try:M.verify_bundle(root,M.TARGET_ID,M.TARGET_SHA,"fixture")
 except M.OneClickError as error:assert str(error)=="fixture is mutable: ."
 else:raise AssertionError("mutable root was accepted")

def test_no_external_json_cli_and_boot_plan_argv_is_single_source():
 source=SOURCE.read_text();assert "j"+"q" not in source
 assert 'bootstrap_argv = boot_plan["argv"]' in source
 assert 'subprocess.run(bootstrap_argv' in source
 assert 'boot_plan.get("argv") != plan.get("bootstrap_argv")' in source
 assert 'BOOT_PLAN.argv differs from the sole executable argv' in source

def test_expected_bootstrap_argv_is_exact():
 spec={"attempt_token":"tpch-v2-offline-b235retry9-1234567890","runtime":{"target_root":"/sealed/runtime"}}
 target=Path("/sealed/target");sp=Path("/sealed/spec.json");token=Path("/sealed/token");evidence=Path("/sealed/evidence.json")
 argv=M.expected_bootstrap_argv(spec,target,sp,token,evidence)
 assert argv==["systemd-run","--quiet","--unit","ra-mysql-tpch-bootstrap-tpch-v2-offline-b235retry9-1234567890.service","--service-type=oneshot","--remain-after-exit","/sealed/runtime/bin/python3.11","-B","/sealed/target/mysql_tpch_distributed_v2_authority.py","--spec","/sealed/spec.json","--execute-bootstrap","--output","/sealed/evidence.json","--main-agent-token-file","/sealed/token"]

def test_writable_renderer_import_creates_no_bytecode(tmp_path):
 source=M.package_paths()["renderer"]/"controller";copy=tmp_path/"controller";shutil.copytree(source,copy)
 env=dict(os.environ,PYTHONDONTWRITEBYTECODE="1");renderer=copy/"render_mysql_tpch_sql_native_percentile_bootstrap_spec_v1.py"
 result=subprocess.run([sys.executable,"-B",str(renderer),"--help"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
 assert result.returncode==0 and not list(tmp_path.rglob("__pycache__")) and not list(tmp_path.rglob("*.pyc")) and not list(tmp_path.rglob("*.pyo"))

def test_pid_identity_reports_pid_startticks_and_command():
 row=M.pid_identity(os.getpid(),"test");assert row["pid"]==os.getpid() and row["starttime_ticks"]>0 and row["cmdline_sha256"] and row["cmdline"]

def stale_row(identifier,name,owner="mysql_algorithm_c_tpch_v2"):
 return {"Id":identifier,"Name":"/"+name,"State":{"Running":True,"Pid":321},"Config":{"Image":M.IMAGE_ID,"Labels":{"ra.attempt":M.STALE_ATTEMPT,"ra.bundle_id":M.STALE_BUNDLE,"ra.owner":owner,"ra.scenario":"tpch_memory"}}}

def test_stale_retry8_identity_is_exact_and_foreign_is_rejected():
 identifier,name=next(iter(M.STALE_CONTAINERS.items()));assert M.validate_owned_row(stale_row(identifier,name),M.STALE_ATTEMPT,M.STALE_BUNDLE)==(identifier,name,321)
 try:M.validate_owned_row(stale_row(identifier,name,"foreign"),M.STALE_ATTEMPT,M.STALE_BUNDLE)
 except M.OneClickError as error:assert "ownership differs" in str(error)
 else:raise AssertionError("foreign container was accepted")

def test_exact_reconcile_validates_full_set_then_stops_and_removes(monkeypatch):
 rows=[stale_row(identifier,name) for identifier,name in M.STALE_CONTAINERS.items()];calls=[]
 class Done:
  returncode=0;stdout="";stderr=""
 monkeypatch.setattr(M,"run_checked",lambda argv,label,timeout=120:(calls.append(argv) or Done()))
 monkeypatch.setattr(M,"docker_inventory",lambda:[])
 value=M.remove_exact_rows(rows,M.STALE_ATTEMPT,M.STALE_BUNDLE,M.STALE_CONTAINERS,"test")
 assert value["dedicated_daemon_empty_after"] is True and len(value["containers"])==2
 assert len(calls)==4 and all(call[:4]==M.DOCKER_CLI+["stop"] or call[:4]==M.DOCKER_CLI+["rm"] for call in calls)
 assert "-f" not in " ".join(part for call in calls for part in call)

def test_completion_failure_and_pid_contract_is_fail_closed():
 source=SOURCE.read_text();assert 'set(pids) != {"bootstrap", "controller", "workload"}' in source
 assert "PASS_FRESH_B235_RETRY9_COMPLETED_ZERO_CREDIT" in source and '"status": "FAILED_ZERO_CREDIT"' in source
 assert '"accepted_round_authorized": False' in source and '"accepted_rounds_credit": 0' in source
 assert 'package_closure_unchanged_after_execute' in source and 'post_failure_closure_error' in source
 assert 'reconcile_stale_retry8(' in source and 'reconcile_failed_current_attempt(' in source
 assert 'post_failure_reconcile_passed' in source and 'stale_retry8_reconcile_sha256' in source

def test_every_python_boundary_disables_bytecode():
 source=SOURCE.read_text();assert "sys.dont_write_bytecode = True" in source
 assert 'PYTHONDONTWRITEBYTECODE="1"' in source and 'export PYTHONDONTWRITEBYTECODE=1' in source
 assert 'spec["runtime"]["target_root"] + "/bin/python3.11", "-B"' in source
 materializer=(BUNDLE/"controller/mysql_tpch_bootstrap_plan_materializer_no_bytecode_v1.py").read_text();assert 'python3.11", "-B",' in materializer

def test_tpcc_fourflow_mutex_covers_unit_and_controller_markers():
 assert str(M.MUTEX)=="/run/lock/ra-mysql-node04-tpch-tpcc-fourflow.lock"
 assert M.FOURFLOW_UNIT=="ra-mysql-da455-fourflow-*.service"
 assert "mysql_tpcc_v16_fresh_four_flow_controller_v1.py" in M.FOURFLOW_PROCESS_MARKERS

def test_retry8_failure_is_exactly_sealed_in_package():
 value=M.sealed(M.package_paths()["failure"],"failure");assert value["status"]=="FAILED_ZERO_CREDIT" and value["accepted_rounds_credit"]==0 and len(value)==7
'''


def freeze(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def build() -> Path:
    for path, bundle_id, manifest_sha in DEPENDENCIES.values():
        verify_dependency(path, bundle_id, manifest_sha)
    failure_sidecar = Path(str(FAILURE) + ".sha256")
    if (sha_file(FAILURE) != "18622022868fa0faeac6a040cda6273f522edfdc1c84640dcd75a767738c37de"
            or failure_sidecar.read_text(encoding="ascii")
            != "18622022868fa0faeac6a040cda6273f522edfdc1c84640dcd75a767738c37de  mysql_tpch_retry8_failure_exact_v1.json\n"):
        raise BuildError("retry8 exact failure seal differs")
    content: dict[str, bytes] = {
        "controller/mysql_tpch_retry9_one_click_v1.py": CONTROLLER.read_bytes(),
        "builder/build_mysql_tpch_retry9_one_click_v1.py": HERE.read_bytes(),
        "tests/test_mysql_tpch_retry9_one_click_v1.py": TEST.encode(),
        "evidence/mysql_tpch_retry8_failure_exact_v1.json": FAILURE.read_bytes(),
        "evidence/mysql_tpch_retry8_failure_exact_v1.json.sha256": failure_sidecar.read_bytes(),
        "authority/invalidated_fa9_remote_pycache_pollution_v1.json": canonical({
            "schema_version": "mysql_tpch_retry9_remote_pycache_invalidation_v1",
            "status": "INVALIDATED_FAILED_ZERO_CREDIT_PROHIBITED_REUSE",
            "invalidated_bundle_id": "fa9ba703929ec749d8f263e6873567287742790a8fac0aaf3d92045fe70b06b8",
            "failure": "one-click is mutable: dependencies/bootstrap_renderer/controller/__pycache__",
            "root_can_bypass_mode_bits": True,
            "remote_publication_reuse_prohibited": True,
            "accepted_round_authorized": False,
            "accepted_rounds_credit": 0,
        }),
        "authority/invalidated_c1_retry9_stale_daemon_failure_v1.json": canonical({
            "schema_version": "mysql_tpch_retry9_stale_daemon_failure_invalidation_v1",
            "status": "INVALIDATED_FAILED_ZERO_CREDIT_PROHIBITED_RERUN",
            "invalidated_bundle_id": "c1f49780058830273c29300c49252bcd0e55ebe83929a9dd664330ed0e2ef1d3",
            "failed_attempt": "tpch-v2-20260803t074729z-b235retry9aa511ee4b5b5",
            "bootstrap_unit": "ra-mysql-tpch-bootstrap-tpch-v2-20260803t074729z-b235retry9aa511ee4b5b5.service",
            "failure": "NOT_READY: dedicated daemon is not empty before attempt",
            "stale_attempt": "tpch-v2-20260803t022500z-afbe6badff313374deadbeef",
            "stale_bundle_id": "ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4",
            "stale_database_container_id": "a28bcc5e6fd284cd1138d91ca73d12e7009c999148bb099b30f3f79029053257",
            "stale_database_host_pid_observed": 108534,
            "stale_client_container_id": "41a51161cd0718faf66380b36eeacea28cd9af21c400a9d384febc1212e411d4",
            "stale_client_host_pid_observed": 108739,
            "retry8_unit": "ra-mysql-tpch-retry5-971eda5d6b76480d.service",
            "retry8_failure_cleanup_gap": "qualification wrapper did not own or remove bootstrap-persistent database/client containers",
            "remote_publication_reuse_prohibited": True,
            "accepted_round_authorized": False,
            "accepted_rounds_credit": 0,
        }),
        "templates/node04_one_click_command.json": canonical({
            "schema_version": "mysql_tpch_retry9_node04_one_click_command_v1",
            "materialize_argv": [
                "[SEALED_PYTHON]",
                "-B",
                "[THIS_BUNDLE]/controller/mysql_tpch_retry9_one_click_v1.py",
                "materialize",
                "--runtime-root",
                "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729",
                "--output-dir",
                "[FRESH_ABSOLUTE_RETRY9_ONE_CLICK_MATERIALS]",
                "--valid-seconds",
                "2400",
            ],
            "then_execute_argv": [
                "/bin/sh",
                "[FRESH_ABSOLUTE_RETRY9_ONE_CLICK_MATERIALS]/launch_and_wait_retry9.sh",
            ],
            "materialization_executes_workload": False,
            "one_click_executes_bootstrap_and_retry9": True,
            "external_json_cli_required": False,
            "manual_json_edit_required": False,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "accepted_round_authorized": False,
        }),
    }
    boot_materializer_source = (DEPENDENCIES["bootstrap_renderer"][0]
        / "launch/mysql_tpch_systemd_oneshot_materializer_v1.py").read_text(encoding="utf-8")
    old_python = 'spec["runtime"]["target_root"] + "/bin/python3.11",\n        authority'
    new_python = 'spec["runtime"]["target_root"] + "/bin/python3.11", "-B",\n        authority'
    if boot_materializer_source.count(old_python) != 1:
        raise BuildError("bootstrap materializer Python argv anchor differs")
    content["controller/mysql_tpch_bootstrap_plan_materializer_no_bytecode_v1.py"] = (
        boot_materializer_source.replace(old_python, new_python).encode("utf-8")
    )
    for label, (root, bundle_id, _) in DEPENDENCIES.items():
        prefix = "dependencies/" + label + "/" + bundle_id + "/"
        for path in sorted(root.rglob("*")):
            if path.is_file():
                content[prefix + path.relative_to(root).as_posix()] = path.read_bytes()
    prohibited = [relative for relative in content
                  if "__pycache__" in Path(relative).parts
                  or Path(relative).suffix in (".pyc", ".pyo")]
    if prohibited:
        raise BuildError("bytecode payload prohibited: " + ",".join(prohibited[:5]))
    files = {relative: sha_bytes(payload) for relative, payload in sorted(content.items())}
    identity = {
        "schema_version": "mysql_tpch_retry9_one_click_bundle_v1",
        "status": "PASS_IMMUTABLE_PACKAGE_SAFE_ONE_CLICK_NOT_EXECUTED",
        "immutable": True,
        "package_safe": True,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "target_bundle_id": "b235d1ca300e5ad02fe77005096fc511bfbdc5d91b9601233a66a10502be5a47",
        "bootstrap_renderer_bundle_id": DEPENDENCIES["bootstrap_renderer"][1],
        "support_bundle_id": DEPENDENCIES["support"][1],
        "materializer_bundle_id": DEPENDENCIES["materializer"][1],
        "wrapper_bundle_id": DEPENDENCIES["wrapper"][1],
        "bootstrap_plan_argv_exact_execution_required": True,
        "external_json_cli_required": False,
        "tpcc_fourflow_full_window_mutex_required": True,
        "bootstrap_controller_workload_pid_receipt_required": True,
        "completion_or_failure_receipt_required": True,
        "generated_launch_and_wait_one_click": True,
        "python_dont_write_bytecode_environment_required": True,
        "python_interpreter_B_flag_required": True,
        "pre_and_post_full_file_closure_required": True,
        "pycache_or_pyc_payload_prohibited": True,
        "invalidated_fa9_bundle_id": "fa9ba703929ec749d8f263e6873567287742790a8fac0aaf3d92045fe70b06b8",
        "invalidated_fa9_remote_reuse_prohibited": True,
        "invalidated_c1_stale_daemon_rerun_prohibited": True,
        "exact_stale_retry8_daemon_reconcile_required": True,
        "post_failure_current_attempt_daemon_reconcile_required": True,
        "foreign_container_cleanup_prohibited": True,
        "retry8_exact_zero_credit_evidence_sha256": sha_file(FAILURE),
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
        "files": files,
    }
    bundle_id = sha_bytes(canonical(identity))
    manifest = dict(identity, bundle_id=bundle_id)
    destination = OUT / bundle_id
    if destination.exists():
        if sha_file(destination / "bundle_manifest.json") != sha_bytes(canonical(manifest)):
            raise BuildError("existing one-click bundle differs")
        return destination
    OUT.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".tpch-retry9-one-click-", dir=OUT))
    try:
        for relative, payload in sorted(content.items()):
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        raw = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(raw)
        (stage / "bundle_manifest.json.sha256").write_text(
            sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii")
        freeze(stage)
        os.replace(stage, destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    if file_map(destination) != files:
        raise BuildError("one-click package file closure differs")
    return destination


if __name__ == "__main__":
    bundle = build()
    print(json.dumps({"status": "PASS_IMMUTABLE_PACKAGE_SAFE_ONE_CLICK_NOT_EXECUTED",
                      "bundle": str(bundle), "bundle_id": bundle.name,
                      "manifest_sha256": sha_file(bundle / "bundle_manifest.json"),
                      "remote_actions": 0}, sort_keys=True))

#!/usr/bin/env python3
"""Build the local Retry9 successor that keeps qualification output on frozen tmpfs."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
MAT_OLD_ID = "61f7d2b3b9d88a656f8bba4ab4a17cb87308fb9027c02276dff69e74a0a6c973"
MAT_OLD_SHA = "9b72727479fc5c1d9a2837eeaab409129c85e0a6a4870c3244c517d7bc3697e3"
MAT_OLD = ROOT / "artifacts/mysql_tpch_retry9_dynamic_attempt_materializer_successor_v1" / MAT_OLD_ID
WRAP_OLD_ID = "02af9e95583d2cd792bc691d714f71cdbac094be5935e3a3fb9bf6e1bbd29b33"
WRAP_OLD_SHA = "8a218082bc8e88706682c3bbb12c821cf34b4d5cbc472588d433560a15c1ac1d"
WRAP_OLD = ROOT / "artifacts/mysql_tpch_retry9_dynamic_retry5_identity_wrapper_successor_v1" / WRAP_OLD_ID
ONE_OLD_ID = "1ef07fd3c8b547598d3c6d1419c11383abf08ca7d23d8c85e8820707963e0bf3"
ONE_OLD_SHA = "88e06baaec02c84e3cebb78cd74bf2e06b03de674118766aac23581d4c7963bd"
ONE_OLD = ROOT / "artifacts/mysql_tpch_retry9_root_principal_failure_one_click_successor_v1" / ONE_OLD_ID
SUPPORT_ID = "947fe36dd89966bfcf1427a67ecd60e872b450ce656556f6255199e47f353739"
SUPPORT_SHA = "1be67f24372e9893b37be8b717e24ff66d76a33dc40cd73149000abd905e14b7"
FAILED = "tpch-v2-20260803t102014z-b235retry948271876b5f8"
FAIL_ERROR = "measurement output escaped frozen tmpfs"
FAIL_PATH = "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729/tpch-v16-7e2e13aa-20260801t121500p08/retry9_systemd/tpch-v2-20260803t102014z-b235retry948271876b5f8/completion/retry5.failure.json"


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                       allow_nan=False) + "\n").encode()


def sb(data):
    return hashlib.sha256(data).hexdigest()


def sf(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1048576), b""):
            digest.update(block)
    return digest.hexdigest()


def file_map(root):
    return {
        path.relative_to(root).as_posix(): sf(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.relative_to(root).as_posix()
        not in ("bundle_manifest.json", "bundle_manifest.json.sha256")
    }


def verify(root, bundle_id, manifest_sha):
    manifest = json.loads((root / "bundle_manifest.json").read_text())
    unsigned = dict(manifest)
    unsigned.pop("bundle_id", None)
    if (root.name != bundle_id or manifest.get("bundle_id") != bundle_id
            or sf(root / "bundle_manifest.json") != manifest_sha
            or sb(canonical(unsigned)) != bundle_id
            or file_map(root) != manifest.get("files")):
        raise RuntimeError("predecessor closure differs: " + str(root))
    return manifest


def replace_once(text, old, new, label):
    if text.count(old) != 1:
        raise RuntimeError(label + " anchor differs")
    return text.replace(old, new)


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def publish(out_root, unsigned, content, prefix):
    files = {name: sb(data) for name, data in sorted(content.items())}
    unsigned = dict(unsigned, files=files)
    bundle_id = sb(canonical(unsigned))
    manifest = dict(unsigned, bundle_id=bundle_id)
    destination = out_root / bundle_id
    if not destination.exists():
        out_root.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=prefix, dir=out_root))
        try:
            for name, data in content.items():
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            raw = canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                sb(raw) + "  bundle_manifest.json\n"
            )
            freeze(stage)
            os.replace(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    if (file_map(destination) != files
            or sf(destination / "bundle_manifest.json") != sb(canonical(manifest))):
        raise RuntimeError("published closure differs: " + str(destination))
    return destination


def build_materializer():
    old_manifest = verify(MAT_OLD, MAT_OLD_ID, MAT_OLD_SHA)
    source_rel = "materializer/mysql_tpch_qualification_retry5_completion_materializer_v1.py"
    source = (MAT_OLD / source_rel).read_text()
    source = replace_once(
        source,
        'RETRY8_FAILURE={',
        'FAILED_MEASUREMENT_ATTEMPT="' + FAILED + '"\nRETRY8_FAILURE={',
        "materializer failed-attempt constant",
    )
    source = replace_once(
        source,
        ' bootstrap_attempt=supplement.get("attempt_token");db=supplement.get("database_container_name");client=(base.get("client_container") or {}).get("name")\n spec_bundle=spec.get("bundle") or {};spec_paths=spec.get("paths") or {}\n',
        ' bootstrap_attempt=supplement.get("attempt_token");db=supplement.get("database_container_name");client=(base.get("client_container") or {}).get("name")\n if bootstrap_attempt==FAILED_MEASUREMENT_ATTEMPT:raise Error("failed measurement-output attempt rerun is prohibited")\n spec_bundle=spec.get("bundle") or {};spec_paths=spec.get("paths") or {}\n',
        "materializer rerun guard",
    )
    source = replace_once(
        source,
        ' out.mkdir(parents=True,mode=0o700);new=attempt();metric_root=Path(base["measurement_output"]["host_tmpfs_directory"]);command,predecessor,host,native=amend(base,new,support,metric_root)\n cp=out/"retry5.command.json";ap=out/"retry5.authority.json";tp=out/"retry5.token";result=out/(new+".qualification.json");script=out/"run_retry5.sh";launchp=out/"retry5.launch_argv.json";materialp=out/"retry5.materialization.json"\n',
        ''' out.mkdir(parents=True,mode=0o700);new=attempt();measurement=base.get("measurement_output") or {};metric_root=Path(str(measurement.get("host_tmpfs_directory") or ""));bind_root=Path(str(measurement.get("host_bind_source") or ""));container_root=Path(str(measurement.get("container_tmpfs_destination") or ""));expected_root=Path("/dev/shm/ra_mysql_tpch_v2")/bootstrap_attempt
 if (not metric_root.is_absolute() or metric_root!=expected_root or Path(str(spec_paths.get("host_measurement_tmpfs") or ""))!=expected_root or metric_root.is_symlink() or not metric_root.is_dir() or metric_root.resolve(strict=True)!=metric_root or bind_root!=metric_root/"client_measurement" or bind_root.is_symlink() or not bind_root.is_dir() or bind_root.resolve(strict=True)!=bind_root or container_root!=Path("/measurement")):raise Error("frozen measurement tmpfs authority differs")
 command,predecessor,host,native=amend(base,new,support,metric_root)
 cp=out/"retry5.command.json";ap=out/"retry5.authority.json";tp=out/"retry5.token";result=metric_root/(new+".qualification.json");script=out/"run_retry5.sh";launchp=out/"retry5.launch_argv.json";materialp=out/"retry5.materialization.json"
 for p in (result,Path(str(result)+".sha256"),result.with_name(result.name+".workload.stdout"),result.with_name(result.name+".workload.stderr")):
  if p.exists() or p.is_symlink():raise Error("retry5 qualification output is not fresh: "+str(p))
''',
        "frozen result derivation",
    )
    source = replace_once(
        source,
        '"fresh_paths":{"command":str(cp),"authority":str(ap),"token":str(tp),"host_metric":str(host),"native_metric_host":str(native),"completion":str(host)+".producer-complete-v1.json","qualification_output":str(result),"launch_script":str(script)},',
        '"fresh_paths":{"command":str(cp),"authority":str(ap),"token":str(tp),"host_metric":str(host),"native_metric_host":str(native),"completion":str(host)+".producer-complete-v1.json","qualification_output":str(result),"launch_script":str(script)},"frozen_measurement_binding":{"schema_version":"mysql_tpch_retry5_frozen_measurement_binding_v1","host_tmpfs_directory":str(metric_root),"host_bind_source":str(bind_root),"container_tmpfs_destination":str(container_root),"qualification_output_parent":str(result.parent),"host_metric_parent":str(host.parent),"native_metric_host_parent":str(native.parent),"bootstrap_attempt_token":bootstrap_attempt,"runtime_gate_unchanged":True},"failed_measurement_attempt":FAILED_MEASUREMENT_ATTEMPT,"failed_measurement_attempt_rerun_prohibited":True,',
        "materialization binding receipt",
    )
    test = '''from __future__ import annotations
import hashlib,json
from pathlib import Path
B=Path(__file__).resolve().parents[1];S=B/"materializer/mysql_tpch_qualification_retry5_completion_materializer_v1.py"
def test_content_address_and_scope():
 m=json.loads((B/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id");assert hashlib.sha256((json.dumps(u,sort_keys=True,indent=2)+"\\n").encode()).hexdigest()==m["bundle_id"]==B.name;assert m["formal_scope"]=="mysql_only" and m["cross_engine_comparison_prohibited"] is True
def test_qualification_output_is_derived_from_frozen_tmpfs():
 s=S.read_text();assert 'result=metric_root/(new+".qualification.json")' in s;assert 'result=out/(new+".qualification.json")' not in s;assert 'spec_paths.get("host_measurement_tmpfs")' in s;assert 'runtime_gate_unchanged":True' in s
def test_failed_482_attempt_has_materializer_guard():
 s=S.read_text();assert "tpch-v2-20260803t102014z-b235retry948271876b5f8" in s and "failed measurement-output attempt rerun is prohibited" in s
'''
    content = {}
    for name in sorted(old_manifest["files"]):
        if name.startswith("builder/") or name.startswith("tests/"):
            continue
        content[name] = source.encode() if name == source_rel else (MAT_OLD / name).read_bytes()
    builder_rel = "builder/build_mysql_tpch_retry9_frozen_measurement_output_successor_v1.py"
    test_rel = "tests/test_mysql_tpch_retry9_frozen_measurement_output_successor_v1.py"
    content[builder_rel] = HERE.read_bytes()
    content[test_rel] = test.encode()
    unsigned = {
        "schema_version": "mysql_tpch_retry9_frozen_measurement_output_materializer_successor_bundle_v1",
        "status": "SEALED_FROZEN_TMPFS_QUALIFICATION_OUTPUT_MATERIALIZER_NOT_EXECUTED",
        "immutable": True,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "predecessor_bundle_id": MAT_OLD_ID,
        "predecessor_manifest_sha256": MAT_OLD_SHA,
        "support_bundle_id": SUPPORT_ID,
        "support_manifest_sha256": SUPPORT_SHA,
        "failed_attempt_token": FAILED,
        "failed_attempt_error": FAIL_ERROR,
        "failed_attempt_rerun_prohibited": True,
        "qualification_output_parent_equals_frozen_host_tmpfs_required": True,
        "tmpfs_runtime_gate_weakened": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
    }
    return publish(ROOT / "artifacts/mysql_tpch_retry9_frozen_measurement_output_materializer_successor_v1", unsigned, content, ".frozen-output-materializer-")


def build_wrapper(materializer):
    old_manifest = verify(WRAP_OLD, WRAP_OLD_ID, WRAP_OLD_SHA)
    materializer_sha = sf(materializer / "bundle_manifest.json")
    source_rel = "controller/mysql_tpch_retry5_systemd219_completion_v1.py"
    source = (WRAP_OLD / source_rel).read_text()
    old = 'RETRY_ID="' + MAT_OLD_ID + '";RETRY_SHA="' + MAT_OLD_SHA + '";'
    new = 'RETRY_ID="' + materializer.name + '";RETRY_SHA="' + materializer_sha + '";FAILED_MEASUREMENT_ATTEMPT="' + FAILED + '";'
    source = replace_once(source, old, new, "wrapper materializer identity")
    source = replace_once(
        source,
        ' if mat.get("schema_version")!="mysql_tpch_qualification_retry5_completion_materialization_v1" or mat.get("status")!="SEALED_FRESH_RETRY5_NOT_LAUNCHED" or mat.get("support_collector_bundle_id")!=COLLECTOR_ID or mat.get("support_collector_manifest_sha256")!=COLLECTOR_SHA or mat.get("expected_sample_count")!=181 or mat.get("expected_last_sequence")!=180 or mat.get("formal_launch_authorized") is not False or mat.get("accepted_round_authorized") is not False:raise Error("retry5 materialization differs")\n',
        ' if mat.get("schema_version")!="mysql_tpch_qualification_retry5_completion_materialization_v1" or mat.get("status")!="SEALED_FRESH_RETRY5_NOT_LAUNCHED" or mat.get("support_collector_bundle_id")!=COLLECTOR_ID or mat.get("support_collector_manifest_sha256")!=COLLECTOR_SHA or mat.get("expected_sample_count")!=181 or mat.get("expected_last_sequence")!=180 or mat.get("formal_launch_authorized") is not False or mat.get("accepted_round_authorized") is not False or mat.get("bootstrap_attempt_token")==FAILED_MEASUREMENT_ATTEMPT or mat.get("failed_measurement_attempt")!=FAILED_MEASUREMENT_ATTEMPT or mat.get("failed_measurement_attempt_rerun_prohibited") is not True:raise Error("retry5 materialization differs")\n',
        "wrapper materialization guard",
    )
    source = replace_once(
        source,
        ' if paths["completion"]!=Path(str(paths["host_metric"])+".producer-complete-v1.json"):raise Error("completion path differs")\n',
        ''' binding=mat.get("frozen_measurement_binding") or {};measurement=cmd.get("measurement_output") or {};tmpfs=Path(str(measurement.get("host_tmpfs_directory") or ""));bind=Path(str(measurement.get("host_bind_source") or ""));bootstrap=mat.get("bootstrap_attempt_token")
 if (binding.get("schema_version")!="mysql_tpch_retry5_frozen_measurement_binding_v1" or binding.get("runtime_gate_unchanged") is not True or tmpfs!=Path("/dev/shm/ra_mysql_tpch_v2")/str(bootstrap) or bind!=tmpfs/"client_measurement" or Path(str(measurement.get("container_tmpfs_destination") or ""))!=Path("/measurement") or paths["qualification_output"].parent!=tmpfs or paths["host_metric"].parent!=tmpfs or paths["native_metric_host"].parent!=bind or binding.get("qualification_output_parent")!=str(tmpfs) or binding.get("host_metric_parent")!=str(tmpfs) or binding.get("native_metric_host_parent")!=str(bind)):raise Error("retry5 frozen measurement path binding differs")
 if paths["completion"]!=Path(str(paths["host_metric"])+".producer-complete-v1.json"):raise Error("completion path differs")
''',
        "wrapper path validation",
    )
    source = replace_once(
        source,
        '"qualification_attempt_id":attempt,"qualification_output":str(paths["qualification_output"]),',
        '"qualification_attempt_id":attempt,"failed_measurement_attempt":FAILED_MEASUREMENT_ATTEMPT,"failed_measurement_attempt_rerun_prohibited":True,"qualification_output":str(paths["qualification_output"]),',
        "wrapper plan guard",
    )
    source = replace_once(
        source,
        'def execution_inputs(plan):\n for key in ("retry5_materialization","retry5_command","retry5_authority","retry5_token"):\n',
        'def execution_inputs(plan):\n if plan.get("failed_measurement_attempt")!=FAILED_MEASUREMENT_ATTEMPT or plan.get("failed_measurement_attempt_rerun_prohibited") is not True:raise Error("failed measurement-output attempt execution authority differs")\n for key in ("retry5_materialization","retry5_command","retry5_authority","retry5_token"):\n',
        "wrapper execute guard",
    )
    test = f'''from __future__ import annotations
import hashlib,importlib.util,json
from pathlib import Path
B=Path(__file__).resolve().parents[1];S=B/"controller/mysql_tpch_retry5_systemd219_completion_v1.py";q=importlib.util.spec_from_file_location("w",S);M=importlib.util.module_from_spec(q);q.loader.exec_module(M)
def test_content_address_and_scope():
 m=json.loads((B/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id");assert hashlib.sha256((json.dumps(u,sort_keys=True,indent=2)+"\\n").encode()).hexdigest()==m["bundle_id"]==B.name;assert m["formal_scope"]=="mysql_only"
def test_new_materializer_and_failed_attempt_binding():
 assert M.RETRY_ID=="{materializer.name}" and M.RETRY_SHA=="{materializer_sha}" and M.FAILED_MEASUREMENT_ATTEMPT=="{FAILED}"
def test_frozen_path_and_execute_guards_present():
 s=S.read_text();assert 'qualification_output"].parent!=tmpfs' in s and 'native_metric_host"].parent!=bind' in s and 'failed measurement-output attempt execution authority differs' in s
'''
    content = {}
    for name in sorted(old_manifest["files"]):
        if name.startswith("builder/") or name.startswith("tests/"):
            continue
        content[name] = source.encode() if name == source_rel else (WRAP_OLD / name).read_bytes()
    content["builder/build_mysql_tpch_retry9_frozen_measurement_output_successor_v1.py"] = HERE.read_bytes()
    content["tests/test_mysql_tpch_retry9_frozen_measurement_output_wrapper_successor_v1.py"] = test.encode()
    unsigned = {
        "schema_version": "mysql_tpch_retry9_frozen_measurement_output_wrapper_successor_bundle_v1",
        "status": "SEALED_FROZEN_MEASUREMENT_PATH_AND_EXECUTE_GUARD_WRAPPER_NOT_EXECUTED",
        "immutable": True,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "predecessor_bundle_id": WRAP_OLD_ID,
        "predecessor_manifest_sha256": WRAP_OLD_SHA,
        "retry5_materializer_bundle_id": materializer.name,
        "retry5_materializer_manifest_sha256": materializer_sha,
        "collector_bundle_id": SUPPORT_ID,
        "collector_manifest_sha256": SUPPORT_SHA,
        "failed_attempt_token": FAILED,
        "failed_attempt_error": FAIL_ERROR,
        "failed_attempt_rerun_prohibited": True,
        "materialize_and_execute_guards_required": True,
        "tmpfs_runtime_gate_weakened": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
    }
    return publish(ROOT / "artifacts/mysql_tpch_retry9_frozen_measurement_output_wrapper_successor_v1", unsigned, content, ".frozen-output-wrapper-")


def build_oneclick(materializer, wrapper):
    old_manifest = verify(ONE_OLD, ONE_OLD_ID, ONE_OLD_SHA)
    materializer_sha = sf(materializer / "bundle_manifest.json")
    wrapper_sha = sf(wrapper / "bundle_manifest.json")
    source_rel = "controller/mysql_tpch_retry9_one_click_v1.py"
    source = (ONE_OLD / source_rel).read_text()
    source = replace_once(source, 'MATERIALIZER_ID = "' + MAT_OLD_ID + '"', 'MATERIALIZER_ID = "' + materializer.name + '"', "oneclick materializer id")
    source = replace_once(source, 'MATERIALIZER_SHA = "' + MAT_OLD_SHA + '"', 'MATERIALIZER_SHA = "' + materializer_sha + '"', "oneclick materializer sha")
    source = replace_once(source, 'WRAPPER_ID = "' + WRAP_OLD_ID + '"', 'WRAPPER_ID = "' + wrapper.name + '"', "oneclick wrapper id")
    source = replace_once(source, 'WRAPPER_SHA = "' + WRAP_OLD_SHA + '"', 'WRAPPER_SHA = "' + wrapper_sha + '"', "oneclick wrapper sha")
    source = replace_once(
        source,
        'FAILED_ROOT_PRINCIPAL_ERROR = "NOT_READY: main-agent token file ownership/mode differs"\n',
        'FAILED_ROOT_PRINCIPAL_ERROR = "NOT_READY: main-agent token file ownership/mode differs"\nFAILED_MEASUREMENT_ATTEMPT = "' + FAILED + '"\nFAILED_MEASUREMENT_ERROR = "' + FAIL_ERROR + '"\n',
        "oneclick failure constants",
    )
    failure_rel = "evidence/mysql_tpch_retry9_48271876_measurement_tmpfs_escape_failure_exact_v1.json"
    source = replace_once(source, '        "failure_root_principal": root / "evidence/mysql_tpch_retry9_ffb_root_principal_token_owner_failure_exact_v1.json",\n', '        "failure_root_principal": root / "evidence/mysql_tpch_retry9_ffb_root_principal_token_owner_failure_exact_v1.json",\n        "failure_measurement": root / "' + failure_rel + '",\n', "oneclick failure path")
    source = replace_once(
        source,
        '        raise OneClickError("Retry9 root-principal exact zero-credit evidence differs")\n    return paths\n',
        '''        raise OneClickError("Retry9 root-principal exact zero-credit evidence differs")
    failure_measurement = sealed(paths["failure_measurement"], "Retry9 measurement tmpfs failure")
    if (failure_measurement.get("schema_version") != "mysql_tpch_retry9_measurement_tmpfs_escape_failure_exact_v1"
            or failure_measurement.get("status") != "FAILED_ZERO_CREDIT_RECONCILED_DO_NOT_RERUN"
            or failure_measurement.get("attempt_token") != FAILED_MEASUREMENT_ATTEMPT
            or failure_measurement.get("error") != FAILED_MEASUREMENT_ERROR
            or failure_measurement.get("bootstrap_passed") is not True
            or failure_measurement.get("workload_started") is not False
            or failure_measurement.get("post_failure_closure_passed") is not True
            or failure_measurement.get("containers_reconciled_absent") is not True
            or failure_measurement.get("rerun_prohibited") is not True
            or failure_measurement.get("accepted_rounds_credit") != 0):
        raise OneClickError("Retry9 measurement tmpfs exact zero-credit evidence differs")
    return paths
''',
        "oneclick failure verify",
    )
    source = replace_once(source, '    if attempt == FAILED_ROOT_PRINCIPAL_ATTEMPT:\n        raise OneClickError("failed Retry9 root-principal attempt rerun is prohibited")\n', '    if attempt == FAILED_ROOT_PRINCIPAL_ATTEMPT:\n        raise OneClickError("failed Retry9 root-principal attempt rerun is prohibited")\n    if attempt == FAILED_MEASUREMENT_ATTEMPT:\n        raise OneClickError("failed Retry9 measurement-output attempt rerun is prohibited")\n', "oneclick materialize guard")
    source = replace_once(source, '        "failed_root_principal_evidence": str(paths["failure_root_principal"]),\n', '        "failed_root_principal_evidence": str(paths["failure_root_principal"]),\n        "failed_measurement_attempt": FAILED_MEASUREMENT_ATTEMPT,\n        "failed_measurement_evidence": str(paths["failure_measurement"]),\n', "oneclick plan failure")
    source = replace_once(source, '            or plan.get("attempt_token") == FAILED_ROOT_PRINCIPAL_ATTEMPT\n', '            or plan.get("attempt_token") == FAILED_ROOT_PRINCIPAL_ATTEMPT\n            or plan.get("failed_measurement_attempt") != FAILED_MEASUREMENT_ATTEMPT\n            or plan.get("attempt_token") == FAILED_MEASUREMENT_ATTEMPT\n', "oneclick execute guard")
    failure = {
        "schema_version": "mysql_tpch_retry9_measurement_tmpfs_escape_failure_exact_v1",
        "status": "FAILED_ZERO_CREDIT_RECONCILED_DO_NOT_RERUN",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "attempt_token": FAILED,
        "error": FAIL_ERROR,
        "failure_stage": "retry5_qualification_pre_workload_measurement_tmpfs_readback",
        "failure_path": FAIL_PATH,
        "bootstrap_passed": True,
        "database_pid_at_bootstrap": 81424,
        "client_pid_at_bootstrap": 81624,
        "workload_started": False,
        "root_cause": "qualification_output_parent_was_retry9_materializations_on_nvme_instead_of_frozen_host_tmpfs",
        "runtime_tmpfs_gate_weakened": False,
        "post_failure_closure_passed": True,
        "containers_reconciled_absent": True,
        "outer_oneclick_failure_receipt_pending_readback": True,
        "rerun_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
    }
    test = f'''from __future__ import annotations
import hashlib,importlib.util,json
from pathlib import Path
B=Path(__file__).resolve().parents[1];S=B/"controller/mysql_tpch_retry9_one_click_v1.py";q=importlib.util.spec_from_file_location("o",S);M=importlib.util.module_from_spec(q);q.loader.exec_module(M)
def test_content_address_and_scope():
 m=json.loads((B/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id");assert hashlib.sha256((json.dumps(u,sort_keys=True,indent=2)+"\\n").encode()).hexdigest()==m["bundle_id"]==B.name;assert m["formal_scope"]=="mysql_only" and m["cross_engine_comparison_prohibited"] is True
def test_successor_dependency_chain():
 p=M.verify_package();assert p["materializer"].name=="{materializer.name}" and p["wrapper"].name=="{wrapper.name}" and p["support"].name=="{SUPPORT_ID}"
def test_482_failure_is_zero_credit_and_double_guarded():
 f=M.sealed(M.package_paths()["failure_measurement"],"failure");s=S.read_text();assert f["attempt_token"]==M.FAILED_MEASUREMENT_ATTEMPT and f["accepted_rounds_credit"]==0 and f["containers_reconciled_absent"] is True;assert "failed Retry9 measurement-output attempt rerun is prohibited" in s and 'plan.get("attempt_token") == FAILED_MEASUREMENT_ATTEMPT' in s
'''
    content = {}
    old_mat_prefix = "dependencies/materializer/" + MAT_OLD_ID + "/"
    old_wrap_prefix = "dependencies/wrapper/" + WRAP_OLD_ID + "/"
    for name in sorted(old_manifest["files"]):
        if name.startswith("builder/") or name.startswith("tests/") or name.startswith(old_mat_prefix) or name.startswith(old_wrap_prefix):
            continue
        content[name] = source.encode() if name == source_rel else (ONE_OLD / name).read_bytes()
    for dependency, prefix in ((materializer, "dependencies/materializer/"), (wrapper, "dependencies/wrapper/")):
        for path in sorted(dependency.rglob("*")):
            if path.is_file():
                content[prefix + dependency.name + "/" + path.relative_to(dependency).as_posix()] = path.read_bytes()
    failure_bytes = canonical(failure)
    content[failure_rel] = failure_bytes
    content[failure_rel + ".sha256"] = (sb(failure_bytes) + "  " + Path(failure_rel).name + "\n").encode()
    content["authority/invalidated_retry9_48271876_measurement_tmpfs_escape_v1.json"] = canonical({
        "schema_version": "mysql_tpch_retry9_measurement_tmpfs_escape_invalidation_v1",
        "status": "INVALIDATED_FAILED_ZERO_CREDIT_PROHIBITED_RERUN",
        "failed_attempt_token": FAILED,
        "failure_evidence": failure_rel,
        "failure_evidence_sha256": sb(failure_bytes),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
    })
    content["builder/build_mysql_tpch_retry9_frozen_measurement_output_successor_v1.py"] = HERE.read_bytes()
    content["tests/test_mysql_tpch_retry9_frozen_measurement_output_one_click_successor_v1.py"] = test.encode()
    unsigned = {key: value for key, value in old_manifest.items()
                if key not in ("bundle_id", "files", "schema_version", "status",
                               "predecessor_bundle_id", "predecessor_manifest_sha256",
                               "dynamic_retry5_support_wrapper_binding_unchanged")}
    unsigned.update({
        "schema_version": "mysql_tpch_retry9_frozen_measurement_output_one_click_successor_bundle_v1",
        "status": "SEALED_482_FAILURE_AND_FROZEN_TMPFS_OUTPUT_SUCCESSOR_NOT_EXECUTED",
        "predecessor_bundle_id": ONE_OLD_ID,
        "predecessor_manifest_sha256": ONE_OLD_SHA,
        "materializer_bundle_id": materializer.name,
        "materializer_manifest_sha256": materializer_sha,
        "wrapper_bundle_id": wrapper.name,
        "wrapper_manifest_sha256": wrapper_sha,
        "support_bundle_id": SUPPORT_ID,
        "support_manifest_sha256": SUPPORT_SHA,
        "failed_measurement_attempt_token": FAILED,
        "failed_measurement_attempt_error": FAIL_ERROR,
        "failed_measurement_attempt_rerun_prohibited": True,
        "qualification_output_parent_equals_frozen_host_tmpfs_required": True,
        "materialize_and_execute_guards_required": True,
        "tmpfs_runtime_gate_weakened": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
    })
    return publish(ROOT / "artifacts/mysql_tpch_retry9_frozen_measurement_output_one_click_successor_v1", unsigned, content, ".frozen-output-oneclick-")


def main():
    verify(ONE_OLD, ONE_OLD_ID, ONE_OLD_SHA)
    materializer = build_materializer()
    wrapper = build_wrapper(materializer)
    oneclick = build_oneclick(materializer, wrapper)
    result = {
        "materializer": str(materializer),
        "materializer_manifest_sha256": sf(materializer / "bundle_manifest.json"),
        "wrapper": str(wrapper),
        "wrapper_manifest_sha256": sf(wrapper / "bundle_manifest.json"),
        "oneclick": str(oneclick),
        "oneclick_manifest_sha256": sf(oneclick / "bundle_manifest.json"),
        "failed_attempt_token": FAILED,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
    }
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()

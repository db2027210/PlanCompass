#!/usr/bin/env python3
"""Build immutable Retry9 one-click successor for b235 dual-principal defaults."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
OLD_ID = "24653774e3707d56be8b7ead920144a9a4bae0e53d23481ca93931ff97550b8a"
OLD_SHA = "3e385d4e345ccc47b1e2139eea3f3097ba04939966dfbf97eede6ef2889f4e63"
OLD = ROOT / "artifacts/mysql_tpch_retry9_one_click_v1" / OLD_ID
NEW_MATERIALIZER_ID = "1fdd3d28a1f20fbd47fecd6ec69621802702293a9e860c1e82ab6528e4623c66"
NEW_MATERIALIZER_SHA = "3d359c54f62465dc7618f5b932c8357d8c2a1821d99c0d678d48b7c2b9f2fb7e"
NEW_MATERIALIZER = ROOT / "artifacts/mysql_tpch_retry9_dual_principal_materializer_successor_v1" / NEW_MATERIALIZER_ID
OLD_MATERIALIZER_ID = "819640d73e8b85a4547b2f4a09b5477eac4bcd19f425d46cac9764b01e3a57af"
OUT = ROOT / "artifacts/mysql_tpch_retry9_dual_principal_one_click_successor_v1"
CONTROLLER = "controller/mysql_tpch_retry9_one_click_v1.py"
BUILDER = "builder/build_mysql_tpch_retry9_dual_principal_one_click_successor_v1.py"
TEST = "tests/test_mysql_tpch_retry9_dual_principal_one_click_successor_v1.py"
FAILURE = "evidence/mysql_tpch_retry9_246_defaults_mode_failure_exact_v1.json"
INVALIDATION = "authority/invalidated_246_retry9_defaults_mode_failure_v1.json"
INTERMEDIATE_INVALIDATION = "authority/invalidated_0d6d_intermediate_defaults_binding_v1.json"
INTERMEDIATE_ID = "0d6d4b48040034329384851572b35138588bf85ec6d6031383441ede838fb983"
INTERMEDIATE_SHA = "35512f4f7903240b6181f613748223d9cbaf0799a9cef9e76356693859e9d072"
FAILED_ATTEMPT = "tpch-v2-20260803t080426z-b235retry91df04b0b3b12"
FAILED_RECEIPT = "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729/retry9_one_click_materials/tpch-retry9-20260803t160400p08-246f01/completion/retry9.failure.json"
FAILED_ERROR = "Retry9 workload materializer failed rc=2 stderr=NOT_READY: MySQL defaults file must be private\n"


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_map(root):
    return {path.relative_to(root).as_posix(): sha_file(path)
            for path in sorted(Path(root).rglob("*")) if path.is_file()
            and path.relative_to(root).as_posix() not in
            ("bundle_manifest.json", "bundle_manifest.json.sha256")}


def verify_bundle(root, bundle_id, manifest_sha):
    manifest_path = root / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    unsigned = dict(manifest); unsigned.pop("bundle_id", None)
    if (root.name != bundle_id or manifest.get("bundle_id") != bundle_id
            or sha_file(manifest_path) != manifest_sha
            or sha_bytes(canonical(unsigned)) != bundle_id
            or file_map(root) != manifest.get("files")
            or manifest.get("formal_scope") != "mysql_only"
            or manifest.get("cross_engine_comparison_prohibited") is not True):
        raise BuildError("bundle closure differs: " + str(root))
    return manifest


FAILURE_VALUE = {
    "schema_version": "mysql_tpch_retry9_246_defaults_mode_failure_exact_v1",
    "status": "FAILED_ZERO_CREDIT",
    "failed_bundle_id": OLD_ID,
    "failed_bundle_manifest_sha256": OLD_SHA,
    "failure_receipt": FAILED_RECEIPT,
    "attempt_token": FAILED_ATTEMPT,
    "error": FAILED_ERROR,
    "mysql_defaults_file": "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729/attempts/" + FAILED_ATTEMPT + "/credentials/mysql.cnf",
    "observed_defaults_identity": {"owner": "anonymous", "uid": 1008, "group": "root", "gid": 0, "mode": "0440", "regular_file": True, "symlink": False},
    "post_failure_reconcile_passed": True,
    "post_failure_closure_passed": True,
    "old_attempt_rerun_prohibited": True,
    "formal_scope": "mysql_only",
    "cross_engine_comparison_prohibited": True,
    "postgresql_mutation_prohibited": True,
    "accepted_round_authorized": False,
    "accepted_rounds_credit": 0,
}


def patch_controller(source):
    source = source.replace(
        'MATERIALIZER_ID = "819640d73e8b85a4547b2f4a09b5477eac4bcd19f425d46cac9764b01e3a57af"',
        'MATERIALIZER_ID = "' + NEW_MATERIALIZER_ID + '"')
    source = source.replace(
        'MATERIALIZER_SHA = "fef5a7cbd3a93a5bd190036240492ffa5dabe495a8a701f77bc3e05bca978480"',
        'MATERIALIZER_SHA = "' + NEW_MATERIALIZER_SHA + '"')
    constants_anchor = 'FAILED_RETRY9_UNIT = (\n    "ra-mysql-tpch-bootstrap-"\n    "tpch-v2-20260803t074729z-b235retry9aa511ee4b5b5.service"\n)\n'
    constants = (
        'FAILED_246_BUNDLE = "' + OLD_ID + '"\n'
        'FAILED_246_MANIFEST_SHA = "' + OLD_SHA + '"\n'
        'FAILED_246_ATTEMPT = "' + FAILED_ATTEMPT + '"\n'
    )
    if source.count(constants_anchor) != 1:
        raise BuildError("controller constants anchor differs")
    source = source.replace(constants_anchor, constants_anchor + constants)
    package_anchor = '        "failure": root / "evidence/mysql_tpch_retry8_failure_exact_v1.json",\n'
    package_new = package_anchor + '        "failure_246": root / "' + FAILURE + '",\n'
    if source.count(package_anchor) != 1:
        raise BuildError("package failure anchor differs")
    source = source.replace(package_anchor, package_new)
    verify_anchor = '    if failure != expected:\n        raise OneClickError("retry8 exact zero-credit evidence differs")\n    return paths\n'
    verify_new = '''    if failure != expected:
        raise OneClickError("retry8 exact zero-credit evidence differs")
    failure_246 = sealed(paths["failure_246"], "Retry9 246 defaults failure")
    if (failure_246.get("schema_version") != "mysql_tpch_retry9_246_defaults_mode_failure_exact_v1"
            or failure_246.get("status") != "FAILED_ZERO_CREDIT"
            or failure_246.get("failed_bundle_id") != FAILED_246_BUNDLE
            or failure_246.get("failed_bundle_manifest_sha256") != FAILED_246_MANIFEST_SHA
            or failure_246.get("attempt_token") != FAILED_246_ATTEMPT
            or failure_246.get("error") != "Retry9 workload materializer failed rc=2 stderr=NOT_READY: MySQL defaults file must be private\\n"
            or failure_246.get("old_attempt_rerun_prohibited") is not True
            or failure_246.get("post_failure_reconcile_passed") is not True
            or failure_246.get("post_failure_closure_passed") is not True
            or failure_246.get("accepted_rounds_credit") != 0):
        raise OneClickError("Retry9 246 exact zero-credit evidence differs")
    return paths
'''
    if source.count(verify_anchor) != 1:
        raise BuildError("verify package anchor differs")
    source = source.replace(verify_anchor, verify_new)
    attempt_anchor = 'def expected_bootstrap_argv(spec: Mapping[str, Any], target: Path,\n'
    helper = r'''def _defaults_metadata(state: os.stat_result, parent_state: os.stat_result) -> None:
    if (not stat.S_ISREG(state.st_mode) or state.st_uid != 1008
            or state.st_gid != 0 or stat.S_IMODE(state.st_mode) != 0o440):
        raise OneClickError("MySQL defaults dual-principal file identity differs")
    if (not stat.S_ISDIR(parent_state.st_mode) or parent_state.st_uid != 1008
            or parent_state.st_gid != 0 or stat.S_IMODE(parent_state.st_mode) != 0o750):
        raise OneClickError("MySQL defaults dual-principal parent identity differs")


def readback_mysql_defaults(path: Path, attempt: str, receipt: Path) -> dict[str, Any]:
    expected = RUNTIME_ROOT / "attempts" / attempt / "credentials/mysql.cnf"
    if (not path.is_absolute() or path != expected or path.name != "mysql.cnf"
            or path.parent.name != "credentials"):
        raise OneClickError("MySQL defaults attempt/path binding differs")
    state = os.lstat(path); parent_state = os.lstat(path.parent)
    if (stat.S_ISLNK(state.st_mode) or stat.S_ISLNK(parent_state.st_mode)
            or path.resolve(strict=True) != path):
        raise OneClickError("MySQL defaults path contains a symlink")
    _defaults_metadata(state, parent_state)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (state.st_dev, state.st_ino):
            raise OneClickError("MySQL defaults inode changed during readback")
        _defaults_metadata(opened, parent_state)
        digest = hashlib.sha256()
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block: break
            digest.update(block)
    finally:
        os.close(descriptor)
    value = {"schema_version": "mysql_tpch_retry9_dual_principal_defaults_readback_v1",
             "status": "PASS_EXACT_DUAL_PRINCIPAL_DEFAULTS_READBACK",
             "attempt_token": attempt, "path": str(path),
             "sha256": digest.hexdigest(), "device": opened.st_dev,
             "inode": opened.st_ino, "uid": opened.st_uid, "gid": opened.st_gid,
             "mode": "%04o" % stat.S_IMODE(opened.st_mode),
             "parent_uid": parent_state.st_uid, "parent_gid": parent_state.st_gid,
             "parent_mode": "%04o" % stat.S_IMODE(parent_state.st_mode),
             "regular_file": True, "symlink": False,
             "credential_mutation_performed": False,
             "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
             "accepted_round_authorized": False, "accepted_rounds_credit": 0}
    seal_new(receipt, value, 0o400)
    return value


'''
    if source.count(attempt_anchor) != 1:
        raise BuildError("defaults helper insertion anchor differs")
    source = source.replace(attempt_anchor, helper + attempt_anchor)
    fresh_anchor = '    if ATTEMPT_RE.fullmatch(attempt) is None:\n        raise OneClickError("attempt token differs")\n'
    fresh_new = fresh_anchor + '    if attempt == FAILED_246_ATTEMPT:\n        raise OneClickError("failed Retry9 246 attempt rerun is prohibited")\n'
    if source.count(fresh_anchor) != 1:
        raise BuildError("fresh attempt anchor differs")
    source = source.replace(fresh_anchor, fresh_new)
    plan_anchor = '        "mysql_defaults": str(attempt_root / "credentials/mysql.cnf"),\n'
    plan_new = plan_anchor + '        "defaults_readback_receipt": str(output / "completion/mysql_defaults.readback.json"),\n        "failed_246_attempt": FAILED_246_ATTEMPT,\n        "failed_246_bundle": FAILED_246_BUNDLE,\n        "failed_246_evidence": str(paths["failure_246"]),\n'
    if source.count(plan_anchor) != 1:
        raise BuildError("plan defaults anchor differs")
    source = source.replace(plan_anchor, plan_new)
    authority_anchor = '            or plan.get("failed_retry9_bootstrap_unit") != FAILED_RETRY9_UNIT\n'
    authority_new = authority_anchor + '            or plan.get("failed_246_attempt") != FAILED_246_ATTEMPT\n            or plan.get("failed_246_bundle") != FAILED_246_BUNDLE\n            or plan.get("attempt_token") == FAILED_246_ATTEMPT\n'
    if source.count(authority_anchor) != 1:
        raise BuildError("execute old attempt anchor differs")
    source = source.replace(authority_anchor, authority_new)
    execute_anchor = '        evidence = Path(plan["bootstrap_evidence"])\n        sealed(evidence, "bootstrap evidence")\n        runtime_python = RUNTIME_ROOT / "runtime" / RUNTIME_TREE / "bin/python3.11"\n'
    execute_new = '        evidence = Path(plan["bootstrap_evidence"])\n        sealed(evidence, "bootstrap evidence")\n        defaults_readback = readback_mysql_defaults(Path(plan["mysql_defaults"]), str(plan["attempt_token"]), Path(plan["defaults_readback_receipt"]))\n        runtime_python = RUNTIME_ROOT / "runtime" / RUNTIME_TREE / "bin/python3.11"\n'
    if source.count(execute_anchor) != 1:
        raise BuildError("post-bootstrap readback anchor differs")
    source = source.replace(execute_anchor, execute_new)
    materialization_anchor = '        retry9_materialization = Path(plan["retry9_materials"]) / "retry5.materialization.json"\n'
    materialization_new = '''        retry9_materialization = Path(plan["retry9_materials"]) / "retry5.materialization.json"
        materialization_value = sealed(retry9_materialization, "Retry9 workload materialization")
        material_defaults = materialization_value.get("mysql_defaults_binding") or {}
        if (material_defaults.get("bootstrap_attempt_token") != defaults_readback["attempt_token"]
                or material_defaults.get("path") != defaults_readback["path"]
                or material_defaults.get("sha256") != defaults_readback["sha256"]
                or material_defaults.get("uid") != defaults_readback["uid"]
                or material_defaults.get("gid") != defaults_readback["gid"]
                or material_defaults.get("mode") != defaults_readback["mode"]
                or material_defaults.get("parent_uid") != defaults_readback["parent_uid"]
                or material_defaults.get("parent_gid") != defaults_readback["parent_gid"]
                or material_defaults.get("parent_mode") != defaults_readback["parent_mode"]):
            raise OneClickError("one-click/materializer MySQL defaults bindings differ")
'''
    if source.count(materialization_anchor) != 1:
        raise BuildError("workload materialization binding anchor differs")
    source = source.replace(materialization_anchor, materialization_new)
    result_anchor = '                  "target_closure_unchanged_after_execute": True,\n'
    result_new = result_anchor + '                  "mysql_defaults_readback": plan["defaults_readback_receipt"],\n                  "mysql_defaults_readback_sha256": sha256(Path(plan["defaults_readback_receipt"])),\n                  "mysql_defaults_content_sha256": defaults_readback["sha256"],\n                  "mysql_defaults_mutation_performed": False,\n'
    if source.count(result_anchor) != 1:
        raise BuildError("completion readback anchor differs")
    source = source.replace(result_anchor, result_new)
    failure_anchor = '                     "inner_failure_sha256": sha256(inner_failure) if inner_failure.exists() else None,\n'
    failure_new = failure_anchor + '                     "mysql_defaults_readback": (str(plan.get("defaults_readback_receipt")) if plan.get("defaults_readback_receipt") and Path(str(plan["defaults_readback_receipt"])).is_file() else None),\n                     "mysql_defaults_readback_sha256": (sha256(Path(str(plan["defaults_readback_receipt"]))) if plan.get("defaults_readback_receipt") and Path(str(plan["defaults_readback_receipt"])).is_file() else None),\n'
    if source.count(failure_anchor) != 1:
        raise BuildError("failure readback anchor differs")
    return source.replace(failure_anchor, failure_new)


TEST_SOURCE = r'''from __future__ import annotations
import hashlib,importlib.util,json,stat
from pathlib import Path
from types import SimpleNamespace

BUNDLE=Path(__file__).resolve().parents[1]
SOURCE=BUNDLE/"controller/mysql_tpch_retry9_one_click_v1.py"
spec=importlib.util.spec_from_file_location("oneclick_successor",SOURCE);M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)

def state(kind,uid,gid,mode):
 bits=stat.S_IFREG if kind=="file" else stat.S_IFDIR
 return SimpleNamespace(st_mode=bits|mode,st_uid=uid,st_gid=gid,st_dev=8,st_ino=12)

def test_content_address_and_successor_bindings():
 manifest=json.loads((BUNDLE/"bundle_manifest.json").read_text());unsigned=dict(manifest);unsigned.pop("bundle_id")
 assert hashlib.sha256((json.dumps(unsigned,sort_keys=True,indent=2)+"\n").encode()).hexdigest()==manifest["bundle_id"]==BUNDLE.name
 assert manifest["predecessor_bundle_id"]=="24653774e3707d56be8b7ead920144a9a4bae0e53d23481ca93931ff97550b8a"
 assert manifest["materializer_bundle_id"]=="1fdd3d28a1f20fbd47fecd6ec69621802702293a9e860c1e82ab6528e4623c66"

def test_exact_failure_is_sealed_and_old_attempt_cannot_rerun():
 failure=M.sealed(M.package_paths()["failure_246"],"failure")
 assert failure["attempt_token"]==M.FAILED_246_ATTEMPT
 assert failure["error"]=="Retry9 workload materializer failed rc=2 stderr=NOT_READY: MySQL defaults file must be private\n"
 assert failure["old_attempt_rerun_prohibited"] is True and failure["accepted_rounds_credit"]==0
 source=SOURCE.read_text();assert 'failed Retry9 246 attempt rerun is prohibited' in source and 'plan.get("attempt_token") == FAILED_246_ATTEMPT' in source

def test_oneclick_exact_dual_principal_readback_contract():
 M._defaults_metadata(state("file",1008,0,0o440),state("dir",1008,0,0o750))
 for wrong in (state("file",1008,0,0o600),state("file",0,0,0o440),state("dir",1008,0,0o440)):
  try:M._defaults_metadata(wrong,state("dir",1008,0,0o750))
  except M.OneClickError:pass
  else:raise AssertionError("wrong defaults identity accepted")

def test_readback_is_after_bootstrap_and_before_workload_materializer():
 source=SOURCE.read_text();boot=source.index('sealed(evidence, "bootstrap evidence")');readback=source.index('defaults_readback = readback_mysql_defaults');materializer=source.index('"Retry9 workload materializer", 180)')
 assert boot<readback<materializer
 assert 'credential_mutation_performed": False' in source and 'O_NOFOLLOW' in source
 assert 'mysql_defaults_readback_sha256' in source and 'mysql_defaults_content_sha256' in source
 assert 'one-click/materializer MySQL defaults bindings differ' in source
 assert 'material_defaults.get("sha256") != defaults_readback["sha256"]' in source

def test_new_materializer_closure_is_embedded_and_verified():
 paths=M.package_paths();assert paths["materializer"].name==M.MATERIALIZER_ID
 M.verify_package()
 material=(paths["materializer"]/"materializer/mysql_tpch_qualification_retry5_completion_materializer_v1.py").read_text()
 assert 'mysql_defaults_binding":defaults_contract' in material and "MySQL defaults file must be private" not in material
'''


def freeze(root):
    for path in sorted(Path(root).rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def build():
    old = verify_bundle(OLD, OLD_ID, OLD_SHA)
    verify_bundle(NEW_MATERIALIZER, NEW_MATERIALIZER_ID, NEW_MATERIALIZER_SHA)
    content = {}
    old_prefix = "dependencies/materializer/" + OLD_MATERIALIZER_ID + "/"
    for relative in sorted(old["files"]):
        if relative.startswith(old_prefix) or relative == "builder/build_mysql_tpch_retry9_one_click_v1.py":
            continue
        payload = (OLD / relative).read_bytes()
        if relative == CONTROLLER:
            payload = patch_controller(payload.decode("utf-8")).encode("utf-8")
        content[relative] = payload
    new_prefix = "dependencies/materializer/" + NEW_MATERIALIZER_ID + "/"
    for path in sorted(NEW_MATERIALIZER.rglob("*")):
        if path.is_file(): content[new_prefix + path.relative_to(NEW_MATERIALIZER).as_posix()] = path.read_bytes()
    failure_payload = canonical(FAILURE_VALUE)
    content[FAILURE] = failure_payload
    content[FAILURE + ".sha256"] = (sha_bytes(failure_payload) + "  " + Path(FAILURE).name + "\n").encode("ascii")
    content[INVALIDATION] = canonical({
        "schema_version": "mysql_tpch_retry9_246_defaults_mode_failure_invalidation_v1",
        "status": "INVALIDATED_FAILED_ZERO_CREDIT_PROHIBITED_RERUN",
        "invalidated_bundle_id": OLD_ID,
        "invalidated_manifest_sha256": OLD_SHA,
        "failed_attempt": FAILED_ATTEMPT,
        "failure_evidence": FAILURE,
        "failure_evidence_sha256": sha_bytes(failure_payload),
        "root_cause": "old workload materializer rejected the sealed b235 dual-principal credential identity 1008:0 mode 0440",
        "credential_chmod_prohibited": True,
        "old_attempt_rerun_prohibited": True,
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "formal_launch_authorized": False, "accepted_round_authorized": False,
        "accepted_rounds_credit": 0, "remote_actions": 0,
    })
    content[INTERMEDIATE_INVALIDATION] = canonical({
        "schema_version": "mysql_tpch_retry9_intermediate_defaults_binding_invalidation_v1",
        "status": "INVALIDATED_LOCAL_INTERMEDIATE_ZERO_CREDIT_PROHIBITED_USE",
        "invalidated_bundle_id": INTERMEDIATE_ID,
        "invalidated_manifest_sha256": INTERMEDIATE_SHA,
        "reason": "intermediate locally-built successor did not compare one-click and workload-materializer defaults SHA/identity bindings after materialization",
        "remote_publication_observed": False,
        "remote_execution_observed": False,
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "formal_launch_authorized": False, "accepted_round_authorized": False,
        "accepted_rounds_credit": 0, "remote_actions": 0,
    })
    content[BUILDER] = HERE.read_bytes(); content[TEST] = TEST_SOURCE.encode("utf-8")
    files = {name: sha_bytes(payload) for name, payload in sorted(content.items())}
    identity = {key: value for key, value in old.items() if key not in
                ("bundle_id", "files", "schema_version", "status", "materializer_bundle_id")}
    identity.update({
        "schema_version": "mysql_tpch_retry9_dual_principal_one_click_successor_bundle_v1",
        "status": "PASS_IMMUTABLE_DUAL_PRINCIPAL_ONE_CLICK_NOT_EXECUTED",
        "predecessor_bundle_id": OLD_ID,
        "predecessor_manifest_sha256": OLD_SHA,
        "materializer_bundle_id": NEW_MATERIALIZER_ID,
        "materializer_manifest_sha256": NEW_MATERIALIZER_SHA,
        "failed_246_attempt": FAILED_ATTEMPT,
        "failed_246_evidence_sha256": sha_bytes(failure_payload),
        "old_246_bundle_reuse_prohibited": True,
        "old_failed_attempt_rerun_prohibited": True,
        "invalidated_local_intermediate_bundle_id": INTERMEDIATE_ID,
        "invalidated_local_intermediate_manifest_sha256": INTERMEDIATE_SHA,
        "bootstrap_dual_principal_credential_mutation_prohibited": True,
        "post_bootstrap_pre_materializer_exact_defaults_readback_required": True,
        "defaults_readback_receipt_required": True,
        "formal_launch_authorized": False, "accepted_round_authorized": False,
        "accepted_rounds_credit": 0, "remote_actions": 0, "files": files,
    })
    bundle_id = sha_bytes(canonical(identity)); manifest = dict(identity, bundle_id=bundle_id)
    destination = OUT / bundle_id
    if destination.exists():
        if sha_file(destination / "bundle_manifest.json") != sha_bytes(canonical(manifest)):
            raise BuildError("existing one-click successor differs")
        return destination
    OUT.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".tpch-dual-principal-one-click-", dir=OUT))
    try:
        for relative, payload in sorted(content.items()):
            target = stage / relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(payload)
        raw = canonical(manifest); (stage / "bundle_manifest.json").write_bytes(raw)
        (stage / "bundle_manifest.json.sha256").write_text(sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii")
        freeze(stage); os.replace(stage, destination)
    finally:
        if stage.exists(): shutil.rmtree(stage)
    if file_map(destination) != files: raise BuildError("one-click successor closure differs")
    return destination


if __name__ == "__main__":
    result = build()
    print(json.dumps({"status": "PASS_IMMUTABLE_DUAL_PRINCIPAL_ONE_CLICK_NOT_EXECUTED", "bundle": str(result), "bundle_id": result.name, "manifest_sha256": sha_file(result / "bundle_manifest.json"), "remote_actions": 0}, sort_keys=True))

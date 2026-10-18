#!/usr/bin/env python3
"""Build the immutable fresh-attempt-aware Retry9 one-click closure."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
OLD_ID = "734665339f63f9c2060c9b6c1bd2de3e14fc1521433181a62348ea03fea1ec12"
OLD_SHA = "a75f199ab2644226a1849511c6016f2c1a99432659911a37bea35002bb64b10e"
OLD = ROOT / "artifacts/mysql_tpch_retry9_dual_principal_one_click_successor_v1" / OLD_ID
OLD_SUPPORT_ID = "a070e44ed4ca1acb0f5b4e0fe03386c6fc0dd43736d0f477cdd17a006bd465b6"
OLD_MATERIALIZER_ID = "1fdd3d28a1f20fbd47fecd6ec69621802702293a9e860c1e82ab6528e4623c66"
SUPPORT_ID = "947fe36dd89966bfcf1427a67ecd60e872b450ce656556f6255199e47f353739"
SUPPORT_SHA = "1be67f24372e9893b37be8b717e24ff66d76a33dc40cd73149000abd905e14b7"
SUPPORT_ENTRY_SHA = "8287cf1954e634cc298665fa9b71026b9b4ed9612b3d995e868a44a95587035a"
SUPPORT = ROOT / "artifacts/mysql_tpch_retry9_dynamic_attempt_support_successor_v1" / SUPPORT_ID
MATERIALIZER_ID = "61f7d2b3b9d88a656f8bba4ab4a17cb87308fb9027c02276dff69e74a0a6c973"
MATERIALIZER_SHA = "9b72727479fc5c1d9a2837eeaab409129c85e0a6a4870c3244c517d7bc3697e3"
MATERIALIZER_FILE_SHA = "0fcc191f4d18af718d630643d275fa3e0622f16e7cd61745f700e92cd596c013"
MATERIALIZER = ROOT / "artifacts/mysql_tpch_retry9_dynamic_attempt_materializer_successor_v1" / MATERIALIZER_ID
OUT = ROOT / "artifacts/mysql_tpch_retry9_dynamic_attempt_one_click_successor_v1"
CONTROLLER = "controller/mysql_tpch_retry9_one_click_v1.py"
BUILDER = "builder/build_mysql_tpch_retry9_dynamic_attempt_one_click_successor_v1.py"
TEST = "tests/test_mysql_tpch_retry9_dynamic_attempt_one_click_successor_v1.py"
FAILURE = "evidence/mysql_tpch_retry9_5c560_static_attempt_binding_failure_exact_v1.json"
INVALIDATION = "authority/invalidated_retry9_5c560_static_attempt_binding_failure_v1.json"
FAILED_ATTEMPT = "tpch-v2-20260803t085536z-b235retry95c56007ba48c"
FAILED_ERROR = "current TPC-H command attempt binding differs"


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
    return {
        path.relative_to(root).as_posix(): sha_file(path)
        for path in sorted(Path(root).rglob("*"))
        if path.is_file()
        and path.relative_to(root).as_posix()
        not in ("bundle_manifest.json", "bundle_manifest.json.sha256")
    }


def verify_bundle(root, bundle_id, manifest_sha):
    manifest_path = root / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    unsigned = dict(manifest)
    unsigned.pop("bundle_id", None)
    if (
        root.name != bundle_id
        or manifest.get("bundle_id") != bundle_id
        or sha_file(manifest_path) != manifest_sha
        or sha_bytes(canonical(unsigned)) != bundle_id
        or file_map(root) != manifest.get("files")
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
    ):
        raise BuildError("bundle closure differs: " + str(root))
    return manifest


def replace_exact(source, old, new, label):
    if source.count(old) != 1:
        raise BuildError(label + " anchor differs")
    return source.replace(old, new)


def patch_controller(source):
    source = replace_exact(
        source,
        f'SUPPORT_ID = "{OLD_SUPPORT_ID}"\nSUPPORT_SHA = "a529c3aa35884314893afdd50b093a251dbf31c6a83d9ff797df052c960a7dce"',
        f'SUPPORT_ID = "{SUPPORT_ID}"\nSUPPORT_SHA = "{SUPPORT_SHA}"',
        "support constants",
    )
    source = replace_exact(
        source,
        f'MATERIALIZER_ID = "{OLD_MATERIALIZER_ID}"\nMATERIALIZER_SHA = "3d359c54f62465dc7618f5b932c8357d8c2a1821d99c0d678d48b7c2b9f2fb7e"',
        f'MATERIALIZER_ID = "{MATERIALIZER_ID}"\nMATERIALIZER_SHA = "{MATERIALIZER_SHA}"',
        "materializer constants",
    )
    old = 'FAILED_246_ATTEMPT = "tpch-v2-20260803t080426z-b235retry91df04b0b3b12"\n'
    new = old + f'FAILED_DYNAMIC_ATTEMPT = "{FAILED_ATTEMPT}"\nFAILED_DYNAMIC_ERROR = "{FAILED_ERROR}"\n'
    source = replace_exact(source, old, new, "failed attempt constants")
    old = '        "failure_246": root / "evidence/mysql_tpch_retry9_246_defaults_mode_failure_exact_v1.json",\n'
    new = old + f'        "failure_dynamic": root / "{FAILURE}",\n'
    source = replace_exact(source, old, new, "failure package path")
    old = '        raise OneClickError("Retry9 246 exact zero-credit evidence differs")\n    return paths\n'
    new = '''        raise OneClickError("Retry9 246 exact zero-credit evidence differs")
    failure_dynamic = sealed(paths["failure_dynamic"], "Retry9 dynamic-attempt failure")
    if (failure_dynamic.get("schema_version") != "mysql_tpch_retry9_static_attempt_binding_failure_exact_v1"
            or failure_dynamic.get("status") != "FAILED_ZERO_CREDIT_RECONCILED_DO_NOT_RERUN"
            or failure_dynamic.get("attempt_token") != FAILED_DYNAMIC_ATTEMPT
            or failure_dynamic.get("error") != FAILED_DYNAMIC_ERROR
            or failure_dynamic.get("bootstrap_passed") is not True
            or failure_dynamic.get("dual_principal_defaults_readback_passed") is not True
            or failure_dynamic.get("reconcile_passed") is not True
            or failure_dynamic.get("rerun_prohibited") is not True
            or failure_dynamic.get("accepted_rounds_credit") != 0):
        raise OneClickError("Retry9 dynamic-attempt exact zero-credit evidence differs")
    return paths
'''
    source = replace_exact(source, old, new, "dynamic failure verification")
    old = '    if attempt == FAILED_246_ATTEMPT:\n        raise OneClickError("failed Retry9 246 attempt rerun is prohibited")\n'
    new = old + '    if attempt == FAILED_DYNAMIC_ATTEMPT:\n        raise OneClickError("failed Retry9 dynamic-attempt rerun is prohibited")\n'
    source = replace_exact(source, old, new, "fresh attempt guard")
    old = '        "failed_246_evidence": str(paths["failure_246"]),\n'
    new = old + '        "failed_dynamic_attempt": FAILED_DYNAMIC_ATTEMPT,\n        "failed_dynamic_evidence": str(paths["failure_dynamic"]),\n'
    source = replace_exact(source, old, new, "plan failure binding")
    old = '            or plan.get("attempt_token") == FAILED_246_ATTEMPT\n'
    new = old + '            or plan.get("failed_dynamic_attempt") != FAILED_DYNAMIC_ATTEMPT\n            or plan.get("attempt_token") == FAILED_DYNAMIC_ATTEMPT\n'
    source = replace_exact(source, old, new, "execute failure guard")
    old = '''                     "--bundle", str(target), "--support-bundle", str(paths["support"]),
                     "--successor-command", str(command), "--retry8-failure-evidence",
                     str(paths["failure"]), "--supplement", str(supplement),
                     "--mysql-defaults-file", plan["mysql_defaults"], "--output-dir",
'''
    new = '''                     "--bundle", str(target), "--support-bundle", str(paths["support"]),
                     "--successor-command", str(command), "--bootstrap-spec",
                     plan["bootstrap_spec"], "--bootstrap-evidence",
                     plan["bootstrap_evidence"], "--retry8-failure-evidence",
                     str(paths["failure"]), "--supplement", str(supplement),
                     "--mysql-defaults-file", plan["mysql_defaults"], "--output-dir",
'''
    source = replace_exact(source, old, new, "materializer bootstrap binding")
    old = '        material_defaults = materialization_value.get("mysql_defaults_binding") or {}\n'
    new = '''        if (materialization_value.get("bootstrap_attempt_token") != plan["attempt_token"]
                or materialization_value.get("bootstrap_spec_sha256") != sha256(Path(plan["bootstrap_spec"]))
                or materialization_value.get("bootstrap_evidence_sha256") != sha256(Path(plan["bootstrap_evidence"]))
                or materialization_value.get("failed_5c560_attempt_rerun_prohibited") is not True):
            raise OneClickError("one-click/materializer bootstrap attempt bindings differ")
        material_defaults = materialization_value.get("mysql_defaults_binding") or {}
'''
    return replace_exact(source, old, new, "materialization receipt binding")


FAILURE_VALUE = {
    "schema_version": "mysql_tpch_retry9_static_attempt_binding_failure_exact_v1",
    "status": "FAILED_ZERO_CREDIT_RECONCILED_DO_NOT_RERUN",
    "formal_scope": "mysql_only",
    "cross_engine_comparison_prohibited": True,
    "postgresql_mutation_prohibited": True,
    "attempt_token": FAILED_ATTEMPT,
    "failure_stage": "retry9_materializer_validate_command_spec_without_static_pid",
    "error": FAILED_ERROR,
    "bootstrap_passed": True,
    "dual_principal_defaults_readback_passed": True,
    "reconcile_passed": True,
    "rerun_prohibited": True,
    "formal_launch_authorized": False,
    "accepted_round_authorized": False,
    "accepted_rounds_credit": 0,
}


TEST_SOURCE = r'''from __future__ import annotations
import hashlib,importlib.util,json
from pathlib import Path

B=Path(__file__).resolve().parents[1]
S=B/"controller/mysql_tpch_retry9_one_click_v1.py"
spec=importlib.util.spec_from_file_location("dynamic_oneclick",S);M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)

def test_content_address_and_scope():
 m=json.loads((B/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")
 assert hashlib.sha256((json.dumps(u,sort_keys=True,indent=2)+"\n").encode()).hexdigest()==m["bundle_id"]==B.name
 assert m["formal_scope"]=="mysql_only" and m["cross_engine_comparison_prohibited"] is True

def test_exact_dynamic_dependencies_are_embedded_and_verified():
 p=M.verify_package()
 assert p["support"].name=="947fe36dd89966bfcf1427a67ecd60e872b450ce656556f6255199e47f353739"
 assert p["materializer"].name=="61f7d2b3b9d88a656f8bba4ab4a17cb87308fb9027c02276dff69e74a0a6c973"
 assert not (B/"dependencies/support/a070e44ed4ca1acb0f5b4e0fe03386c6fc0dd43736d0f477cdd17a006bd465b6").exists()
 assert not (B/"dependencies/materializer/1fdd3d28a1f20fbd47fecd6ec69621802702293a9e860c1e82ab6528e4623c66").exists()

def test_spec_evidence_command_attempt_closure_is_wired_before_materializer():
 s=S.read_text();call=s.index('"--bootstrap-spec"');material=s.index('"Retry9 workload materializer", 180)')
 assert call<material
 for term in ('plan["bootstrap_spec"]','plan["bootstrap_evidence"]','materialization_value.get("bootstrap_attempt_token")','bootstrap_spec_sha256','bootstrap_evidence_sha256','one-click/materializer bootstrap attempt bindings differ'):assert term in s

def test_failed_attempt_is_zero_credit_and_prohibited_rerun():
 f=M.sealed(M.package_paths()["failure_dynamic"],"failure")
 assert f["attempt_token"]==M.FAILED_DYNAMIC_ATTEMPT and f["rerun_prohibited"] is True and f["accepted_rounds_credit"]==0
 s=S.read_text();assert 'failed Retry9 dynamic-attempt rerun is prohibited' in s and 'plan.get("attempt_token") == FAILED_DYNAMIC_ATTEMPT' in s

def test_nested_entry_and_materializer_hashes():
 p=M.package_paths()
 assert hashlib.sha256((p["support"]/"qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py").read_bytes()).hexdigest()=="8287cf1954e634cc298665fa9b71026b9b4ed9612b3d995e868a44a95587035a"
 assert hashlib.sha256((p["materializer"]/"materializer/mysql_tpch_qualification_retry5_completion_materializer_v1.py").read_bytes()).hexdigest()=="0fcc191f4d18af718d630643d275fa3e0622f16e7cd61745f700e92cd596c013"
'''


def freeze(root):
    for path in sorted(Path(root).rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def add_bundle(content, prefix, root):
    for path in sorted(root.rglob("*")):
        if path.is_file():
            content[prefix + path.relative_to(root).as_posix()] = path.read_bytes()


def build():
    old = verify_bundle(OLD, OLD_ID, OLD_SHA)
    verify_bundle(SUPPORT, SUPPORT_ID, SUPPORT_SHA)
    verify_bundle(MATERIALIZER, MATERIALIZER_ID, MATERIALIZER_SHA)
    content = {}
    excluded = (
        "dependencies/support/" + OLD_SUPPORT_ID + "/",
        "dependencies/materializer/" + OLD_MATERIALIZER_ID + "/",
        "builder/",
        "tests/",
    )
    for relative in sorted(old["files"]):
        if relative.startswith(excluded):
            continue
        payload = (OLD / relative).read_bytes()
        if relative == CONTROLLER:
            payload = patch_controller(payload.decode("utf-8")).encode("utf-8")
        content[relative] = payload
    add_bundle(content, "dependencies/support/" + SUPPORT_ID + "/", SUPPORT)
    add_bundle(content, "dependencies/materializer/" + MATERIALIZER_ID + "/", MATERIALIZER)
    failure_payload = canonical(FAILURE_VALUE)
    content[FAILURE] = failure_payload
    content[FAILURE + ".sha256"] = (
        sha_bytes(failure_payload) + "  " + Path(FAILURE).name + "\n"
    ).encode("ascii")
    content[INVALIDATION] = canonical({
        "schema_version": "mysql_tpch_retry9_5c560_failure_invalidation_v1",
        "status": "INVALIDATED_FAILED_ZERO_CREDIT_PROHIBITED_RERUN",
        "failed_attempt_token": FAILED_ATTEMPT,
        "failure_evidence": FAILURE,
        "failure_evidence_sha256": sha_bytes(failure_payload),
        "old_support_bundle_id": OLD_SUPPORT_ID,
        "old_materializer_bundle_id": OLD_MATERIALIZER_ID,
        "static_attempt_binding_prohibited": True,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
    })
    content[BUILDER] = HERE.read_bytes()
    content[TEST] = TEST_SOURCE.encode("utf-8")
    files = {name: sha_bytes(payload) for name, payload in sorted(content.items())}
    identity = {key: value for key, value in old.items()
                if key not in ("bundle_id", "files", "schema_version", "status",
                               "predecessor_bundle_id", "predecessor_manifest_sha256",
                               "support_bundle_id", "materializer_bundle_id",
                               "materializer_manifest_sha256", "failed_246_attempt")}
    identity.update({
        "schema_version": "mysql_tpch_retry9_dynamic_attempt_one_click_successor_bundle_v1",
        "status": "SEALED_DYNAMIC_ATTEMPT_ONE_CLICK_CLOSURE_NOT_EXECUTED",
        "predecessor_bundle_id": OLD_ID,
        "predecessor_manifest_sha256": OLD_SHA,
        "support_bundle_id": SUPPORT_ID,
        "support_manifest_sha256": SUPPORT_SHA,
        "support_entry_sha256": SUPPORT_ENTRY_SHA,
        "materializer_bundle_id": MATERIALIZER_ID,
        "materializer_manifest_sha256": MATERIALIZER_SHA,
        "materializer_file_sha256": MATERIALIZER_FILE_SHA,
        "fresh_spec_evidence_command_attempt_exact_binding_required": True,
        "failed_attempt_token": FAILED_ATTEMPT,
        "failed_attempt_error": FAILED_ERROR,
        "failed_attempt_rerun_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
        "files": files,
    })
    bundle_id = sha_bytes(canonical(identity))
    manifest = dict(identity, bundle_id=bundle_id)
    destination = OUT / bundle_id
    if destination.exists():
        if (sha_file(destination / "bundle_manifest.json") != sha_bytes(canonical(manifest))
                or file_map(destination) != files):
            raise BuildError("existing dynamic one-click successor differs")
        return destination
    OUT.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".tpch-dynamic-one-click-", dir=OUT))
    try:
        for relative, payload in sorted(content.items()):
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        raw = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(raw)
        (stage / "bundle_manifest.json.sha256").write_text(
            sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii"
        )
        freeze(stage)
        os.replace(stage, destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    if file_map(destination) != files:
        raise BuildError("dynamic one-click closure differs")
    return destination


if __name__ == "__main__":
    result = build()
    print(json.dumps({
        "status": "SEALED_DYNAMIC_ATTEMPT_ONE_CLICK_CLOSURE_NOT_EXECUTED",
        "bundle": str(result),
        "bundle_id": result.name,
        "manifest_sha256": sha_file(result / "bundle_manifest.json"),
        "controller_sha256": sha_file(result / CONTROLLER),
        "remote_actions": 0,
    }, sort_keys=True))

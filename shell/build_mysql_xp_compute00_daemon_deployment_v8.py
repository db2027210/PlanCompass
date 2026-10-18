#!/usr/bin/env python3
"""Build the canonical-verifier-fixed XP daemon deployment v8 offline."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
PARENT_ID = "47a2367d2548fe140e7ea5e2536be4a9ab1f60b9dfb52c7c4cdbf7d1f95c2c52"
PARENT_MANIFEST_SHA256 = "dc0de7d15f9cc9b1ca2a1d3e7e7fe795817cb85a0cb2d948631c2acb43251c25"
V6_ID = "02b460d96dfd69972ca76a2b9620228fc2d239d46453d3c880d8ac001a7f12b4"
PARENT = ROOT / "artifacts/mysql_xp_compute00_daemon_deployment_v7" / PARENT_ID
OUTPUT = ROOT / "artifacts/mysql_xp_compute00_daemon_deployment_v8"
SCHEMA = "mysql_xp_compute00_daemon_deployment_successor_v8"
STATUS = "SEALED_XP_DAEMON_DEPLOYMENT_CANONICAL_FIXED_NOT_EXECUTED"
DEPLOYER_SOURCE = ROOT / "shell/mysql_xp_compute00_daemon_deploy_v1.py"
TEST_SOURCE = ROOT / "tests/test_mysql_xp_compute00_daemon_deployment_v7.py"
DEPLOYER = "deployment/mysql_xp_compute00_daemon_deploy_v1.py"
TEST = "tests_v8/test_mysql_xp_compute00_daemon_deployment_v8.py"
FAILURE = "authority/mysql_xp_v7_deployer_canonical_failure_20260803.json"
RUNBOOK = "operator/mysql_xp_compute00_daemon_deployment_v8.md"


class BuildError(RuntimeError):
    pass


def load_v7_builder():
    path = ROOT / "shell/build_mysql_xp_compute00_daemon_deployment_v7.py"
    spec = importlib.util.spec_from_file_location("xp_v7_builder_for_v8", path)
    if spec is None or spec.loader is None:
        raise BuildError("v7 builder import differs")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


V7 = load_v7_builder()
canonical, sha, sha_bytes = V7.canonical, V7.sha, V7.sha_bytes
writable, freeze, files = V7.writable, V7.freeze, V7.files


def replace_once(text, old, new, label):
    if text.count(old) != 1:
        raise BuildError(label + " patch point differs")
    return text.replace(old, new, 1)


def failure_payload():
    return {
        "schema_version": "mysql_xp_v7_deployer_canonical_failure_v1",
        "status": "INVALID_ZERO_CREDIT_PRE_MUTATION_DO_NOT_REUSE",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
        "deployment_entrypoint": DEPLOYER,
        "observed_error": "NOT_READY: sealed daemon-deployment bundle differs",
        "exact_failure_condition": "deployer sha256(compact_json(unsigned_manifest)+newline) did not equal bundle_id built from pretty_json(unsigned_manifest)+newline",
        "root_diagnosis": {
            "deployer_canonical": "json.dumps(sort_keys=True,separators=(',',':'),ensure_ascii=False)+newline",
            "builder_canonical": "json.dumps(ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+newline",
            "local_verify_bundle_reproduces": True,
        },
        "published_to_runtime": True,
        "deployment_invoked": True,
        "failure_before_systemd_run": True,
        "systemd_mutation": False,
        "unit_created": False,
        "daemon_started": False,
        "container_created": False,
        "database_container_started": False,
        "client_container_started": False,
        "workload_started": False,
        "deployment_receipt_created": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "authorization_reuse_prohibited": True,
        "postgresql_mutation": False,
    }


def patch_deployer(stage):
    path = stage / DEPLOYER
    shutil.copy2(DEPLOYER_SOURCE, path); path.chmod(0o755)
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        'SCHEMA = "mysql_xp_compute00_daemon_deployment_successor_v7"',
        'SCHEMA = "%s"' % SCHEMA,
        "deployer schema",
    )
    canonical_block = text[text.index("def canonical(value):"):
                           text.index("\n\ndef sha_bytes", text.index("def canonical(value):"))]
    required = ("ensure_ascii=True", "indent=2", "sort_keys=True",
                "allow_nan=False")
    if any(canonical_block.count(value) != 1 for value in required):
        raise BuildError("deployer canonical implementation differs")
    path.write_text(text, encoding="utf-8")


def patch_test(stage):
    old = stage / "tests_v7"
    if old.exists():
        shutil.rmtree(old)
    (stage / "tests_v8").mkdir()
    path = stage / TEST
    shutil.copy2(TEST_SOURCE, path)
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        'FAILURE = ROOT / "authority/mysql_xp_v6_missing_dedicated_daemon_20260803.json"',
        'FAILURE = ROOT / "%s"' % FAILURE,
        "test failure path",
    )
    text = replace_once(
        text,
        'SCHEMA = "mysql_xp_compute00_daemon_deployment_successor_v7"',
        'SCHEMA = "%s"' % SCHEMA,
        "test schema",
    )
    start = text.index("def test_exact_deployment_contract_and_v6_failure_are_sealed():")
    end = text.index("\n\ndef test_complete_systemd_run_and_dockerd_argv_are_frozen():", start)
    replacement = '''def test_exact_contract_and_v7_pre_mutation_failure_are_sealed():
    contract = json.loads(CONTRACT.read_text())
    assert contract["unit"] == "ra-docker-mysql-xp-v2.service"
    assert contract["fragment_path"] == "/run/systemd/transient/ra-docker-mysql-xp-v2.service"
    assert contract["control_group"] == "/system.slice/ra-docker-mysql-xp-v2.service"
    assert contract["socket"] == "/run/ra-docker-mysql-xp-v2.sock"
    assert contract["data_root"] == "/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/docker_ra_xp_v29"
    assert contract["daemon_cpuset"] == "192-193,448-449"
    assert contract["data_major_minor"] == "259:6"
    assert contract["postgresql_cpu_overlap"] == []
    assert contract["workload_launch_authorized"] is False
    failure = json.loads(FAILURE.read_text())
    assert failure["predecessor_bundle_id"] == "''' + PARENT_ID + '''"
    assert failure["observed_error"] == "NOT_READY: sealed daemon-deployment bundle differs"
    assert failure["failure_before_systemd_run"] is True
    assert failure["systemd_mutation"] is False
    assert failure["unit_created"] is False
    assert failure["daemon_started"] is False
    assert failure["workload_started"] is False
    assert failure["accepted_rounds_credit"] == 0
    assert failure["authorization_reuse_prohibited"] is True
'''
    text = text[:start] + replacement + text[end:]
    text = text.replace(
        'operator/mysql_xp_compute00_daemon_deployment_v7.md',
        'operator/mysql_xp_compute00_daemon_deployment_v8.md',
    )
    path.write_text(text, encoding="utf-8")


def patch_runbook(stage):
    old = stage / "operator/mysql_xp_compute00_daemon_deployment_v7.md"
    text = old.read_text(encoding="utf-8")
    old.unlink()
    text = text.replace("XP v7", "XP v8").replace("v7_bundle_id", "v8_bundle_id")
    text += "\nV7 bundle `%s` is invalid after its pre-mutation canonical verifier failure and must never be deployed again.\n" % PARENT_ID
    (stage / RUNBOOK).write_text(text, encoding="utf-8")


def validate_target(target, bundle_id, require_name=True):
    manifest = json.loads((target / "bundle_manifest.json").read_text())
    unsigned = dict(manifest); observed = unsigned.pop("bundle_id", None)
    if (
        observed != bundle_id
        or (require_name and target.name != bundle_id)
        or sha_bytes(canonical(unsigned)) != bundle_id
        or files(target) != manifest.get("files")
    ):
        raise BuildError("v8 content-addressed closure differs")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
               XP_BUNDLE_UNDER_TEST=str(target),
               XP_EXPECTED_BUNDLE_ID=bundle_id)
    completed = subprocess.run(
        [str(PYTHON), "-B", "-m", "pytest", "-q", str(target / TEST)],
        cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, timeout=180,
    )
    if completed.returncode:
        raise BuildError("v8 sealed tests failed:\n" + completed.stdout)
    return completed.stdout


def prepublish_content_addressed_validation(stage, bundle_id):
    with tempfile.TemporaryDirectory(prefix="xp-v8-content-addressed-",
                                     dir="/tmp") as temporary:
        candidate = Path(temporary) / bundle_id
        shutil.copytree(stage, candidate)
        freeze(candidate); candidate.chmod(0o555)
        return validate_target(candidate, bundle_id, require_name=True)


def build():
    if sha(PARENT / "bundle_manifest.json") != PARENT_MANIFEST_SHA256:
        raise BuildError("v7 predecessor manifest identity differs")
    parent = json.loads((PARENT / "bundle_manifest.json").read_text())
    if (
        parent.get("bundle_id") != PARENT_ID
        or parent.get("schema_version")
        != "mysql_xp_compute00_daemon_deployment_successor_v7"
        or parent.get("daemon_deployment_authorized") is not True
        or parent.get("workload_launch_authorized") is not False
        or parent.get("accepted_rounds_credit") != 0
    ):
        raise BuildError("v7 predecessor authority differs")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    raw = Path(tempfile.mkdtemp(prefix=".stage-xp-daemon-v8-", dir=OUTPUT))
    stage = raw / "bundle"
    try:
        shutil.copytree(PARENT, stage); writable(stage)
        for name in ("bundle_manifest.json", "bundle_manifest.json.sha256"):
            (stage / name).unlink()
        patch_deployer(stage); patch_test(stage); patch_runbook(stage)
        (stage / FAILURE).write_bytes(canonical(failure_payload()))
        shutil.copy2(SELF, stage / "builder/build_mysql_xp_compute00_daemon_deployment_v8.py")
        inventory = dict(parent.get("controlled_existing_runtime_inventory") or {})
        required = list(inventory.get("required_bundle_ids") or [])
        for identity in (V6_ID, PARENT_ID):
            if identity not in required:
                required.append(identity)
        inventory.update({
            "required_bundle_ids": required,
            "invalidated_daemon_deployment_bundle_ids": [PARENT_ID],
            "accepted_rounds_credit": 0,
            "reuse_prohibited": True,
            "new_bundle_no_overwrite_atomic_publication_required": True,
        })
        manifest = {key: value for key, value in parent.items()
                    if key not in ("bundle_id", "files")}
        manifest.update({
            "schema_version": SCHEMA, "status": STATUS,
            "predecessor_bundle_id": PARENT_ID,
            "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
            "supersedes_bundle_id": PARENT_ID,
            "v7_deployment_reuse_prohibited": True,
            "v7_failure_evidence": FAILURE,
            "deployer_canonical_contract": {
                "ensure_ascii": True, "indent": 2, "sort_keys": True,
                "allow_nan": False, "trailing_newline": True,
                "same_function_as_builder": True,
                "final_content_addressed_verify_bundle_test_required": True,
            },
            "daemon_deployment_authorized": True,
            "transactional_daemon_deployment": True,
            "daemon_deployment_entrypoint": DEPLOYER,
            "daemon_deployment_runbook": RUNBOOK,
            "controlled_existing_runtime_inventory": inventory,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "postgresql_mutation_prohibited": True,
            "workload_launch_authorized": False,
            "database_container_launch_authorized": False,
            "client_container_launch_authorized": False,
            "formal_launch_authorized": False,
            "formal_r0_authorized": False,
            "accepted_round_authorized": False,
            "accepted_rounds_credit": 0,
            "remote_actions": 0,
            "files": files(stage),
        })
        bundle_id = sha_bytes(canonical(manifest)); manifest["bundle_id"] = bundle_id
        target = OUTPUT / bundle_id
        if target.exists():
            return target, validate_target(target, bundle_id), "ADOPTED_IDENTICAL"
        (stage / "bundle_manifest.json").write_bytes(canonical(manifest))
        (stage / "bundle_manifest.json.sha256").write_text(
            sha(stage / "bundle_manifest.json") + "  bundle_manifest.json\n",
            encoding="ascii",
        )
        output = prepublish_content_addressed_validation(stage, bundle_id)
        freeze(stage); stage.chmod(0o755); os.rename(stage, target)
        target.chmod(0o555)
        final_output = validate_target(target, bundle_id)
        if "passed" not in output or "passed" not in final_output:
            raise BuildError("pre/post publication test result differs")
        return target, final_output, "PUBLISHED_NEW"
    finally:
        if raw.exists():
            writable(raw); shutil.rmtree(raw)


def main():
    target, output, mode = build()
    print(json.dumps({
        "schema_version": "mysql_xp_compute00_daemon_deployment_v8_build_result_v1",
        "status": "PASS_CANONICAL_FIXED_DAEMON_DEPLOYMENT_ONLY_NOT_EXECUTED",
        "publish_mode": mode, "bundle": str(target),
        "bundle_id": target.name,
        "manifest_sha256": sha(target / "bundle_manifest.json"),
        "tests": "PASS", "test_output": output.strip().splitlines()[-1],
        "verify_bundle_final_content_addressed": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "daemon_deployment_authorized": True,
        "workload_launch_authorized": False,
        "formal_launch_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (BuildError, OSError, ValueError,
            subprocess.SubprocessError) as error:
        raise SystemExit("NOT_READY: " + str(error))

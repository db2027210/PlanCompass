#!/usr/bin/env python3
"""Build the systemd-219 not-found inventory compatible XP daemon v9."""
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
PARENT_ID = "f60b1a7e4fa082d8455f060e402065d07e7beee5aa86a7a737735c0cb98c7fda"
PARENT_MANIFEST_SHA256 = "0622043fee503417832c10cdbd7fe3612b6972779c3efdea9da616324e6a67e1"
PARENT = ROOT / "artifacts/mysql_xp_compute00_daemon_deployment_v8" / PARENT_ID
OUTPUT = ROOT / "artifacts/mysql_xp_compute00_daemon_deployment_v9"
SCHEMA = "mysql_xp_compute00_daemon_deployment_successor_v9"
STATUS = "SEALED_XP_DAEMON_SYSTEMD219_NOT_FOUND_COMPAT_NOT_EXECUTED"
DEPLOYER_SOURCE = ROOT / "shell/mysql_xp_compute00_daemon_deploy_v1.py"
DEPLOYER = "deployment/mysql_xp_compute00_daemon_deploy_v1.py"
TEST = "tests_v9/test_mysql_xp_compute00_daemon_deployment_v9.py"
FIXTURE = "authority/mysql_xp_v8_systemd219_not_found.stdout"
FAILURE = "authority/mysql_xp_v8_systemd_property_inventory_failure_20260803.json"
RUNBOOK = "operator/mysql_xp_compute00_daemon_deployment_v9.md"
CONTRACT = "authority/mysql_xp_compute00_daemon_deployment_contract_v1.json"
CONTRACT_SHA256 = "7fdb1c24e59743e81b70496dadcb1f5ea7f287b2a347ac2aa0f269fa5ce8afa3"
OBSERVED_STDOUT = """Restart=no
MainPID=0
ControlGroup=
Delegate=no
CPUShares=[not set]
MemoryLimit=infinity
TasksMax=629145
LimitNOFILE=1048576
KillMode=control-group
Id=ra-docker-mysql-xp-v2.service
Requires=
Wants=
BindsTo=
PartOf=
Conflicts=
Before=
After=
LoadState=not-found
ActiveState=inactive
SubState=dead
FragmentPath=
SourcePath=
UnitFileState=
Transient=no
"""
OMITTED_EXEC = (
    "ExecStart", "ExecStartPre", "ExecStartPost", "ExecStop", "ExecStopPost",
    "ExecReload",
)


class BuildError(RuntimeError):
    pass


def load_v8_builder():
    path = ROOT / "shell/build_mysql_xp_compute00_daemon_deployment_v8.py"
    spec = importlib.util.spec_from_file_location("xp_v8_builder_for_v9", path)
    if spec is None or spec.loader is None:
        raise BuildError("v8 builder import differs")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


V8 = load_v8_builder()
canonical, sha, sha_bytes = V8.canonical, V8.sha, V8.sha_bytes
writable, freeze, files = V8.writable, V8.freeze, V8.files


def replace_once(text, old, new, label):
    if text.count(old) != 1:
        raise BuildError(label + " patch point differs")
    return text.replace(old, new, 1)


def failure_payload(fixture_sha):
    return {
        "schema_version": "mysql_xp_v8_systemd_property_inventory_failure_v1",
        "status": "INVALID_ZERO_CREDIT_PRE_MUTATION_DO_NOT_REUSE",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
        "deployment_entrypoint": DEPLOYER,
        "observed_error": "NOT_READY: systemd property inventory differs",
        "root_diagnosis": "systemd 219 omits empty Exec* properties for a not-found unit",
        "observed_stdout_fixture": FIXTURE,
        "observed_stdout_sha256": fixture_sha,
        "observed_load_state": "not-found",
        "observed_active_state": "inactive",
        "observed_main_pid": 0,
        "omitted_properties": list(OMITTED_EXEC),
        "omission_allowed_only_while_load_state_not_found": True,
        "published_to_runtime": True,
        "deployment_invoked": True,
        "failure_before_systemd_run": True,
        "persistent_change": False,
        "systemd_mutation": False,
        "unit_created": False,
        "socket_created": False,
        "deployment_receipt_created": False,
        "daemon_started": False,
        "container_created": False,
        "database_container_started": False,
        "client_container_started": False,
        "workload_started": False,
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
    for required in (
        "OPTIONAL_EMPTY_EXEC_PROPERTIES_WHEN_NOT_FOUND",
        "SYSTEMD219_NOT_FOUND_EXPECTED",
        "missing <= OPTIONAL_EMPTY_EXEC_PROPERTIES_WHEN_NOT_FOUND",
        "properties != SYSTEMD219_NOT_FOUND_EXPECTED",
    ):
        if required not in text:
            raise BuildError("deployer systemd219 compatibility differs")
    path.write_text(text, encoding="utf-8")


def patch_test(stage):
    shutil.rmtree(stage / "tests_v8")
    (stage / "tests_v9").mkdir()
    source = PARENT / "tests_v8/test_mysql_xp_compute00_daemon_deployment_v8.py"
    path = stage / TEST
    shutil.copy2(source, path)
    path.chmod(0o644)
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        'FAILURE = ROOT / "authority/mysql_xp_v7_deployer_canonical_failure_20260803.json"',
        'FAILURE = ROOT / "%s"\nFIXTURE = ROOT / "%s"' % (FAILURE, FIXTURE),
        "test evidence paths",
    )
    text = replace_once(
        text,
        'SCHEMA = "mysql_xp_compute00_daemon_deployment_successor_v8"',
        'SCHEMA = "%s"' % SCHEMA,
        "test schema",
    )
    start = text.index("def test_exact_contract_and_v7_pre_mutation_failure_are_sealed():")
    end = text.index("\n\ndef test_complete_systemd_run_and_dockerd_argv_are_frozen():", start)
    replacement = '''def test_exact_contract_and_v8_pre_mutation_failure_are_sealed():
    contract = json.loads(CONTRACT.read_text())
    assert hashlib.sha256(CONTRACT.read_bytes()).hexdigest() == "''' + CONTRACT_SHA256 + '''"
    assert contract["unit"] == "ra-docker-mysql-xp-v2.service"
    assert contract["socket"] == "/run/ra-docker-mysql-xp-v2.sock"
    assert contract["data_root"] == "/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/docker_ra_xp_v29"
    assert contract["daemon_cpuset"] == "192-193,448-449"
    assert contract["data_major_minor"] == "259:6"
    assert contract["postgresql_cpu_overlap"] == []
    assert contract["workload_launch_authorized"] is False
    failure = json.loads(FAILURE.read_text())
    assert failure["predecessor_bundle_id"] == "''' + PARENT_ID + '''"
    assert failure["observed_error"] == "NOT_READY: systemd property inventory differs"
    assert failure["failure_before_systemd_run"] is True
    assert failure["persistent_change"] is False
    assert failure["systemd_mutation"] is False
    assert failure["unit_created"] is False
    assert failure["socket_created"] is False
    assert failure["deployment_receipt_created"] is False
    assert failure["daemon_started"] is False
    assert failure["workload_started"] is False
    assert failure["accepted_rounds_credit"] == 0
    assert failure["authorization_reuse_prohibited"] is True
'''
    text = text[:start] + replacement + text[end:]
    text = text.replace(
        "operator/mysql_xp_compute00_daemon_deployment_v8.md",
        "operator/mysql_xp_compute00_daemon_deployment_v9.md",
    )
    extra = r'''

def test_real_systemd219_not_found_stdout_fixture_is_exact_and_accepted():
    mod = load_deployer(); raw = FIXTURE.read_text(encoding="utf-8")
    assert raw == "Restart=no\nMainPID=0\nControlGroup=\nDelegate=no\nCPUShares=[not set]\nMemoryLimit=infinity\nTasksMax=629145\nLimitNOFILE=1048576\nKillMode=control-group\nId=ra-docker-mysql-xp-v2.service\nRequires=\nWants=\nBindsTo=\nPartOf=\nConflicts=\nBefore=\nAfter=\nLoadState=not-found\nActiveState=inactive\nSubState=dead\nFragmentPath=\nSourcePath=\nUnitFileState=\nTransient=no\n"
    assert not set(mod.OPTIONAL_EMPTY_EXEC_PROPERTIES_WHEN_NOT_FOUND) & {
        line.split("=", 1)[0] for line in raw.splitlines()
    }
    assert mod.parse_show(raw) == mod.SYSTEMD219_NOT_FOUND_EXPECTED


def test_only_six_empty_exec_fields_may_be_omitted_only_when_not_found():
    mod = load_deployer(); raw = FIXTURE.read_text(encoding="utf-8")
    non_exec_lines = raw.splitlines()
    for index in range(len(non_exec_lines)):
        changed = "\n".join(non_exec_lines[:index] + non_exec_lines[index + 1:]) + "\n"
        with pytest.raises(mod.DeploymentError, match="inventory differs"):
            mod.parse_show(changed)
    active = raw.replace("LoadState=not-found", "LoadState=loaded")
    with pytest.raises(mod.DeploymentError, match="inventory differs"):
        mod.parse_show(active)
    duplicate = raw + "MainPID=0\n"
    with pytest.raises(mod.DeploymentError, match="duplicate"):
        mod.parse_show(duplicate)
'''
    marker = "\ndef test_main_agent_one_click_is_deployment_only():"
    text = replace_once(text, marker, extra + marker, "systemd219 fixture tests")
    path.write_text(text, encoding="utf-8")


def patch_runbook(stage):
    old = stage / "operator/mysql_xp_compute00_daemon_deployment_v8.md"
    text = old.read_text(encoding="utf-8"); old.unlink()
    text = text.replace("XP v8", "XP v9").replace("v8_bundle_id", "v9_bundle_id")
    text += "\nV8 bundle `%s` failed before mutation on the sealed systemd-219 not-found representation and must never be deployed again.\n" % PARENT_ID
    (stage / RUNBOOK).write_text(text, encoding="utf-8")


def validate_target(target, bundle_id, require_name=True):
    manifest = json.loads((target / "bundle_manifest.json").read_text())
    unsigned = dict(manifest); observed = unsigned.pop("bundle_id", None)
    if (
        observed != bundle_id
        or (require_name and target.name != bundle_id)
        or sha_bytes(canonical(unsigned)) != bundle_id
        or files(target) != manifest.get("files")
        or sha(target / CONTRACT) != CONTRACT_SHA256
    ):
        raise BuildError("v9 content-addressed closure/envelope differs")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
               XP_BUNDLE_UNDER_TEST=str(target),
               XP_EXPECTED_BUNDLE_ID=bundle_id)
    completed = subprocess.run(
        [str(PYTHON), "-B", "-m", "pytest", "-q", str(target / TEST)],
        cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, timeout=180,
    )
    if completed.returncode:
        raise BuildError("v9 sealed tests failed:\n" + completed.stdout)
    return completed.stdout


def prepublish_validation(stage, bundle_id):
    with tempfile.TemporaryDirectory(prefix="xp-v9-content-addressed-",
                                     dir="/tmp") as temporary:
        candidate = Path(temporary) / bundle_id
        shutil.copytree(stage, candidate)
        freeze(candidate); candidate.chmod(0o555)
        return validate_target(candidate, bundle_id)


def build():
    if sha(PARENT / "bundle_manifest.json") != PARENT_MANIFEST_SHA256:
        raise BuildError("v8 predecessor manifest identity differs")
    parent = json.loads((PARENT / "bundle_manifest.json").read_text())
    if (
        parent.get("bundle_id") != PARENT_ID
        or parent.get("schema_version")
        != "mysql_xp_compute00_daemon_deployment_successor_v8"
        or parent.get("daemon_deployment_authorized") is not True
        or parent.get("workload_launch_authorized") is not False
        or parent.get("accepted_rounds_credit") != 0
        or sha(PARENT / CONTRACT) != CONTRACT_SHA256
    ):
        raise BuildError("v8 predecessor authority/envelope differs")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    raw = Path(tempfile.mkdtemp(prefix=".stage-xp-daemon-v9-", dir=OUTPUT))
    stage = raw / "bundle"
    try:
        shutil.copytree(PARENT, stage); writable(stage)
        for name in ("bundle_manifest.json", "bundle_manifest.json.sha256"):
            (stage / name).unlink()
        patch_deployer(stage); patch_test(stage); patch_runbook(stage)
        (stage / FIXTURE).write_text(OBSERVED_STDOUT, encoding="utf-8")
        fixture_sha = sha(stage / FIXTURE)
        (stage / FAILURE).write_bytes(canonical(failure_payload(fixture_sha)))
        shutil.copy2(SELF, stage / "builder/build_mysql_xp_compute00_daemon_deployment_v9.py")
        inventory = dict(parent.get("controlled_existing_runtime_inventory") or {})
        required = list(inventory.get("required_bundle_ids") or [])
        if PARENT_ID not in required:
            required.append(PARENT_ID)
        invalid = list(inventory.get("invalidated_daemon_deployment_bundle_ids") or [])
        if PARENT_ID not in invalid:
            invalid.append(PARENT_ID)
        inventory.update({"required_bundle_ids": required,
                          "invalidated_daemon_deployment_bundle_ids": invalid,
                          "accepted_rounds_credit": 0, "reuse_prohibited": True,
                          "new_bundle_no_overwrite_atomic_publication_required": True})
        manifest = {key: value for key, value in parent.items()
                    if key not in ("bundle_id", "files")}
        manifest.update({
            "schema_version": SCHEMA, "status": STATUS,
            "predecessor_bundle_id": PARENT_ID,
            "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
            "supersedes_bundle_id": PARENT_ID,
            "v8_deployment_reuse_prohibited": True,
            "v8_failure_evidence": FAILURE,
            "systemd219_not_found_stdout_fixture": FIXTURE,
            "systemd219_not_found_stdout_sha256": fixture_sha,
            "systemd219_optional_omitted_properties": list(OMITTED_EXEC),
            "optional_property_omission_allowed_only_when_load_state_not_found": True,
            "active_readback_requires_complete_inventory": True,
            "daemon_contract_sha256": CONTRACT_SHA256,
            "daemon_envelope_unchanged": True,
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
            encoding="ascii")
        prepublish_validation(stage, bundle_id)
        freeze(stage); stage.chmod(0o755); os.rename(stage, target)
        target.chmod(0o555)
        return target, validate_target(target, bundle_id), "PUBLISHED_NEW"
    finally:
        if raw.exists():
            writable(raw); shutil.rmtree(raw)


def main():
    target, output, mode = build()
    print(json.dumps({
        "schema_version": "mysql_xp_compute00_daemon_deployment_v9_build_result_v1",
        "status": "PASS_SYSTEMD219_NOT_FOUND_COMPAT_DAEMON_ONLY_NOT_EXECUTED",
        "publish_mode": mode, "bundle": str(target),
        "bundle_id": target.name,
        "manifest_sha256": sha(target / "bundle_manifest.json"),
        "tests": "PASS", "test_output": output.strip().splitlines()[-1],
        "verify_bundle_final_content_addressed": "PASS",
        "real_systemd219_stdout_fixture": "PASS",
        "daemon_contract_sha256": CONTRACT_SHA256,
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

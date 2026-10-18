#!/usr/bin/env python3
"""Build the content-addressed JOB v3 same-live-adoption control successor."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
OUTPUT = ROOT / "artifacts/mysql_job_v3_same_live_adoption_successor_v1"
JOB_ID = "6b4844bcdcc7ada8dfd76c6676f87c98dc382aa76979504d3edc65baaab52dd7"
JOB_SHA = "3ee84d8e47827ff1a4221e3f8828d41e85f3d6aca2e15cf69dc610aa460f2ce6"
JOB = ROOT / "artifacts/mysql_job_raw_all_subset_three_level_successor_v13" / JOB_ID
CONTROL_ID = "6b55e29ef3c47cca437c94501511317c6c79dd1579be3dc6e3a4983587245f74"
CONTROL_SHA = "5c5e59098304942800ef44dc17c777ffa6df0a81261a275b526a9737474c77ba"
CONTROL = ROOT / "artifacts/mysql_job_v13_qualification_r0_closure_v3" / CONTROL_ID
OFFLINE_ID = "2ea99e7a257a1264315347ad9b6194212872197711573d11ebe25a164e205588"
OFFLINE_SHA = "6fd5c8d408beb3f4d0bd5bf4c6e003c8ec41639bcbd7efdb94fd972d7facb03b"
OFFLINE = ROOT / "artifacts/mysql_job_v3_xp_v23_offline_materialization_v1" / OFFLINE_ID
XP_BINDING_SOURCE_CONTROL_ID = "7801df9f783c61fb5a72341c8bfc6ac04c28bd6f7729d12f7a5e987d28e3b1b3"
XP_BINDING_SOURCE_CONTROL_SHA = "ce1f9cd0c4bc6d493c7775d89933853bc8abbf4ef318e9c9a1426ff64768e650"
AUDIT = ROOT / "artifacts/mysql_job_v3_remote_readiness_audit_20260803/job_v3_readonly_readiness_audit.json"
AUDIT_SHA = "57a1d3192e8bd621207968347c441faddfefb7246ee74b9e2db0a3df0972110a"
XP_ID = "ba68d4a57f7ebb03f8e9c662250492aac7341757c66c56256665c41b5ee3a77e"
XP_SHA = "e03f095d21876c687e9b916b8b2b5e4dca6999d5ece745d3b6261becfc5eecf0"
XP = ROOT / "artifacts/mysql_xp_node02_successor_v23" / XP_ID
SEALED_PYTHON = "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/runtime_algorithm_c_successor_20260729/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89/bin/python3.11"
SEALED_PYTHON_SHA = "05bd6d6d900bd990b8c00d17df3b3e1425d6c32f0408f07cd29bc495ac4aff39"
SEALED_PYTHON_TREE = "3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89"
FILES = {
    "runtime/mysql_job_v3_same_live_adoption_v1.py": ROOT / "shell/mysql_job_v3_same_live_adoption_v1.py",
    "tests/test_mysql_job_v3_same_live_adoption_v1.py": ROOT / "tests/test_mysql_job_v3_same_live_adoption_v1.py",
    "templates/mysql_job_v3_same_live_adoption_commands_v1.json": ROOT / "templates/mysql_job_v3_same_live_adoption_commands_v1.json",
    "templates/mysql_job_v3_same_live_adoption_remote_compatibility_tests_v1.json": ROOT / "templates/mysql_job_v3_same_live_adoption_remote_compatibility_tests_v1.json",
    "authority/job_v3_readonly_readiness_audit.json": AUDIT,
    "authority/job_v3_readonly_readiness_audit.json.sha256": Path(str(AUDIT) + ".sha256"),
    "authority/job_v3_control_bundle_manifest.json": CONTROL / "bundle_manifest.json",
    "authority/job_v3_control_bundle_manifest.json.sha256": CONTROL / "bundle_manifest.json.sha256",
    "authority/job_target_bundle_manifest.json": JOB / "bundle_manifest.json",
    "authority/job_target_bundle_manifest.json.sha256": JOB / "bundle_manifest.json.sha256",
    "authority/xp_successor_binding.json": OFFLINE / "xp_successor_binding.json",
    "authority/xp_successor_binding.json.sha256": OFFLINE / "xp_successor_binding.json.sha256",
    "builder/build_mysql_job_v3_same_live_adoption_successor_v1.py": SELF,
}


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def manifest(path: Path, identifier: str, digest: str) -> dict[str, Any]:
    value = json.loads((path / "bundle_manifest.json").read_text(encoding="utf-8"))
    if (
        path.is_symlink() or not path.is_dir() or path.name != identifier
        or value.get("bundle_id") != identifier
        or sha256(path / "bundle_manifest.json") != digest
        or (path / "bundle_manifest.json.sha256").read_text(encoding="ascii")
        != f"{digest}  bundle_manifest.json\n"
    ):
        raise BuildError("sealed predecessor manifest differs")
    return value


def verify() -> None:
    job = manifest(JOB, JOB_ID, JOB_SHA)
    control = manifest(CONTROL, CONTROL_ID, CONTROL_SHA)
    offline = manifest(OFFLINE, OFFLINE_ID, OFFLINE_SHA)
    manifest(XP, XP_ID, XP_SHA)
    actual_control = {
        item.relative_to(CONTROL).as_posix(): sha256(item)
        for item in CONTROL.rglob("*") if item.is_file()
        and item.name not in {"bundle_manifest.json", "bundle_manifest.json.sha256"}
    }
    if actual_control != control.get("files") or len(actual_control) != 13:
        raise BuildError("JOB v3 exact 13-file closure differs")
    if (
        job.get("bundle_id") != JOB_ID
        or offline.get("job_control_bundle_id") != XP_BINDING_SOURCE_CONTROL_ID
        or offline.get("job_control_manifest_sha256") != XP_BINDING_SOURCE_CONTROL_SHA
        or offline.get("xp_bundle_id") != XP_ID
        or sha256(AUDIT) != AUDIT_SHA
        or Path(str(AUDIT) + ".sha256").read_text(encoding="ascii")
        != f"{AUDIT_SHA}  {AUDIT.name}\n"
    ):
        raise BuildError("JOB/audit/offline binding differs")
    validator_source = CONTROL / "controller/mysql_xp_successor_binding_v1.py"
    spec = importlib.util.spec_from_file_location("_same_live_build_xp", validator_source)
    if spec is None or spec.loader is None:
        raise BuildError("cannot load v3 XP validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    binding_path = OFFLINE / "xp_successor_binding.json"
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    expected_binding = module.build_binding(XP, Path(binding["xp_bundle_path"]))
    if (
        Path(str(binding_path) + ".sha256").read_text(encoding="ascii")
        != f"{sha256(binding_path)}  {binding_path.name}\n"
        or binding != expected_binding
    ):
        raise BuildError("XP v23 sealed binding differs")
    if binding.get("xp_bundle_id") != XP_ID:
        raise BuildError("XP v23 chain binding differs")
    job_runtime_path = JOB / "distributed_runtime_contract.json"
    job_runtime = json.loads(job_runtime_path.read_text(encoding="utf-8"))
    toolchain = job_runtime.get("toolchain") or {}
    tree_manifest = JOB / str(toolchain.get("python_tree_manifest") or "")
    tree_value = json.loads(tree_manifest.read_text(encoding="utf-8"))
    if (
        job.get("files", {}).get("distributed_runtime_contract.json") != sha256(job_runtime_path)
        or job.get("files", {}).get(str(toolchain.get("python_tree_manifest") or "")) != sha256(tree_manifest)
        or toolchain.get("python_runtime") != SEALED_PYTHON
        or toolchain.get("python_binary_sha256") != SEALED_PYTHON_SHA
        or toolchain.get("python_tree_content_sha256") != SEALED_PYTHON_TREE
        or (tree_value.get("artifacts") or {}).get("bin/python3.11") != SEALED_PYTHON_SHA
        or tree_value.get("tree_content_sha256") != SEALED_PYTHON_TREE
    ):
        raise BuildError("sealed JOB Python 3.11.11 authority differs")
    template = json.loads(FILES["templates/mysql_job_v3_same_live_adoption_commands_v1.json"].read_text())
    command_argv = [
        value[0] for key, value in template.items()
        if key.endswith("_argv") and isinstance(value, list) and value
    ]
    if not command_argv or any(value != SEALED_PYTHON for value in command_argv):
        raise BuildError("JOB command template does not use only sealed Python")
    if (OFFLINE / "xp_successor_binding.json.sha256").read_text(encoding="ascii") != (
            f"{sha256(OFFLINE / 'xp_successor_binding.json')}  xp_successor_binding.json\n"):
        raise BuildError("original XP binding basename/sidecar differs")
    for relative, source in FILES.items():
        if source.is_symlink() or not source.is_file():
            raise BuildError("bundle input is absent or unsafe: " + relative)


def build() -> dict[str, Any]:
    verify()
    files = {relative: sha256(source) for relative, source in sorted(FILES.items())}
    unsigned = {
        "schema_version": "mysql_job_v3_same_live_adoption_successor_bundle_v2",
        "status": "SEALED_SAME_LIVE_ADOPTION_CONTROL_NOT_EXECUTED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "target_job_bundle_id": JOB_ID,
        "target_job_manifest_sha256": JOB_SHA,
        "bound_job_control_bundle_id": CONTROL_ID,
        "bound_job_control_manifest_sha256": CONTROL_SHA,
        "bound_shared_dispatch_sha256": "0bcedce743562eb54fde2d17fa2c45cfa446e69b62ed116d7595e9ce37023911",
        "offline_materialization_bundle_id": OFFLINE_ID,
        "offline_materialization_manifest_sha256": OFFLINE_SHA,
        "offline_materialization_role": "xp_v23_binding_source_only",
        "offline_materialization_command_tables_authorized": False,
        "xp_binding_source_job_control_bundle_id": XP_BINDING_SOURCE_CONTROL_ID,
        "xp_binding_source_job_control_manifest_sha256": XP_BINDING_SOURCE_CONTROL_SHA,
        "xp_successor_bundle_id": XP_ID,
        "xp_successor_manifest_sha256": XP_SHA,
        "supersedes_bundle_id": "43720349bbd0a36a3accc76695bb1877e51178ddaa9eb17a0553ae73f9d4ed6c",
        "successor_generation": "post_verify_shared_dispatch_bound_same_live_adoption_v1",
        "stale_7801_adoption_output_for_new_launch_authorized": False,
        "refreshed_spec_evidence_and_downstream_launch_same_control_required": True,
        "two_read_stable_identity_and_resource_fields_exact": True,
        "started_at_compared_as_rfc3339_utc_nanosecond_value": True,
        "started_at_trailing_zero_same_instant_accepted": True,
        "started_at_real_one_nanosecond_change_result": "FAIL_CLOSED",
        "systemd_219_compatible_mainpid_query": "systemctl show --property=MainPID UNIT",
        "systemd_mainpid_unique_nonzero_exact": True,
        "mount_and_blkio_row_order_canonicalized": True,
        "unconsumed_dynamic_mount_fields_excluded": True,
        "real_identity_resource_throttle_or_mount_change_result": "FAIL_CLOSED",
        "sealed_python_path": SEALED_PYTHON,
        "sealed_python_version": "3.11.11",
        "sealed_python_binary_sha256": SEALED_PYTHON_SHA,
        "sealed_python_tree_content_sha256": SEALED_PYTHON_TREE,
        "xp_successor_binding_basename": "xp_successor_binding.json",
        "renamed_binding_with_stale_sidecar_rejected": True,
        "remote_compatibility_test_template": "templates/mysql_job_v3_same_live_adoption_remote_compatibility_tests_v1.json",
        "source_bootstrap_spec_sha256": "d8cc7b9de54c68812281822175c725dd2f49fa5652728e4719f1c39c8bf09d77",
        "source_bootstrap_evidence_sha256": "e425b4161c08d1e50350df56222ec8912861373685333e512fa783bfc80f0493",
        "live_identity_source": "selected Docker fields plus systemd MainPID plus proc startticks/status",
        "docker_config_env_requested": False,
        "credential_file_opened_by_successor": False,
        "static_pid_arguments": 0,
        "static_container_identity_arguments": 0,
        "container_recreation_authorized": False,
        "database_or_client_start_stop_authorized": False,
        "qualification_authorized": False,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "files": files,
    }
    bundle_id = hashlib.sha256(canonical(unsigned)).hexdigest()
    value = dict(unsigned, bundle_id=bundle_id)
    destination = OUTPUT / bundle_id
    if destination.exists() or destination.is_symlink():
        existing = json.loads((destination / "bundle_manifest.json").read_text(encoding="utf-8"))
        if existing != value:
            raise BuildError("existing same-live successor differs")
        return value
    OUTPUT.mkdir(parents=True, exist_ok=True)
    stage = OUTPUT / f".stage-{bundle_id}-{secrets.token_hex(6)}"
    stage.mkdir(mode=0o700)
    try:
        for relative, source in FILES.items():
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(0o444)
        payload = canonical(value)
        (stage / "bundle_manifest.json").write_bytes(payload)
        (stage / "bundle_manifest.json.sha256").write_text(
            f"{hashlib.sha256(payload).hexdigest()}  bundle_manifest.json\n", encoding="ascii"
        )
        for directory in sorted((item for item in stage.rglob("*") if item.is_dir()), reverse=True):
            directory.chmod(0o555)
        (stage / "bundle_manifest.json").chmod(0o444)
        (stage / "bundle_manifest.json.sha256").chmod(0o444)
        os.rename(stage, destination)
        destination.chmod(0o555)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return value


def main() -> int:
    value = build()
    print(json.dumps({
        "status": value["status"], "bundle_id": value["bundle_id"],
        "bundle": str(OUTPUT / value["bundle_id"]),
        "accepted_round_authorized": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BuildError, OSError, ValueError, KeyError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)

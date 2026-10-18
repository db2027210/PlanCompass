#!/usr/bin/env python3
"""Seal the JOB authorization-sidecar closure successor without remote actions."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import stat


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "78b00bed2e52fcb00ff940f0e19856341b48ec8fb9d89d6dbbbfe5db98313bbb"
PARENT_MANIFEST_SHA256 = "bd94d6e6bf2803275ae406a7157695678427bb08cf09c8a10c40c737d53cd8cb"
PARENT = ROOT / "artifacts/mysql_job_raw_all_subset_three_level_successor_v5" / PARENT_ID
OUTPUT = ROOT / "artifacts/mysql_job_raw_all_subset_three_level_successor_v6"
SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v6"
MATERIALIZER = "qualification/mysql_job_dedicated_daemon_materializer_v5.py"
TEST = "tests/test_mysql_job_module_closure_successor_v5.py"
BUILDER = "authority/build_mysql_job_authorization_sidecar_successor_v6.py"
INVALIDATION = "authority/mysql_job_78b_authorization_sidecar_invalidation_20260801.json"
FAILED_ATTEMPTS = [
    "job-v2-20260731t153956z-f3d33768187775c49c175e8c",
    "job-v2-20260731t163142z-4208a76411f1b68f98a5e559",
]


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def digest_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def replace_once(source, old, new, label):
    if source.count(old) != 1:
        raise BuildError(label + " patch cardinality differs")
    return source.replace(old, new)


def verify_parent():
    path = PARENT / "bundle_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    identity = dict(manifest)
    identity.pop("bundle_id", None)
    if (
        PARENT.name != PARENT_ID
        or manifest.get("bundle_id") != PARENT_ID
        or manifest.get("schema_version")
        != "mysql_job_raw_all_subset_three_level_successor_bundle_v5"
        or digest(path) != PARENT_MANIFEST_SHA256
        or digest_bytes(canonical(identity)) != PARENT_ID
    ):
        raise BuildError("JOB v5 parent identity differs")
    for relative, expected in manifest.get("files", {}).items():
        candidate = PARENT / relative
        if candidate.is_symlink() or not candidate.is_file() or digest(candidate) != expected:
            raise BuildError("JOB v5 parent closure differs: " + relative)
    return manifest


def patch_materializer(payload):
    source = payload.decode("utf-8")
    source = replace_once(
        source,
        'SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v5"',
        'SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v6"',
        "materializer schema",
    )
    bootstrap = '''                "bootstrap_spec.json": {
                    "source_relative": "bootstrap_spec.json",
                    "destination":
                        authorization_root + "/bootstrap_spec.json",
                    "mode": "0400",
                },
'''
    bootstrap_new = bootstrap + '''                "bootstrap_spec.json.sha256": {
                    "source_relative": "bootstrap_spec.json.sha256",
                    "destination":
                        authorization_root + "/bootstrap_spec.json.sha256",
                    "mode": "0400",
                },
'''
    source = replace_once(source, bootstrap, bootstrap_new,
                          "bootstrap sidecar install")
    command = '''                "command_plan.json": {
                    "source_relative": "command_plan.json",
                    "destination":
                        authorization_root + "/command_plan.json",
                    "mode": "0400",
                },
'''
    command_new = command + '''                "command_plan.json.sha256": {
                    "source_relative": "command_plan.json.sha256",
                    "destination":
                        authorization_root + "/command_plan.json.sha256",
                    "mode": "0400",
                },
'''
    source = replace_once(source, command, command_new,
                          "command sidecar install")
    source = replace_once(
        source,
        '"mysql_job_authorization_install_plan_v1"',
        '"mysql_job_authorization_install_plan_v2"',
        "install plan schema",
    )
    source = replace_once(
        source,
        '"mysql_job_dedicated_daemon_materials_v3"',
        '"mysql_job_dedicated_daemon_materials_v6"',
        "materials schema",
    )
    marker = '                "job-v2-20260731t085459z-a1e71a7acd26efec1f3f7e89",\n'
    additions = marker + "".join('                "' + item + '",\n' for item in FAILED_ATTEMPTS)
    source = replace_once(source, marker, additions, "failed attempt invalidation")
    return source.encode("utf-8")


def patch_test(payload):
    source = payload.decode("utf-8")
    source = replace_once(source, "JOB v5 successor", "JOB v6 successor",
                          "test title")
    source = replace_once(
        source,
        'SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v5"',
        'SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v6"',
        "test schema",
    )
    anchor = '''    assert {
        "qualification/gradient_descent_optimizer.py",
'''
    injected = '''    materializer = (ROOT / "qualification/mysql_job_dedicated_daemon_materializer_v5.py").read_text()
    assert '"bootstrap_spec.json.sha256"' in materializer
    assert '"command_plan.json.sha256"' in materializer
    assert '"mysql_job_authorization_install_plan_v2"' in materializer
    assert {
        "qualification/gradient_descent_optimizer.py",
'''
    return replace_once(source, anchor, injected,
                        "test sidecar assertions").encode("utf-8")


def invalidation():
    return {
        "schema_version": "mysql_job_78b_authorization_sidecar_invalidation_v1",
        "status": "INVALID_ZERO_CREDIT",
        "invalidated_at_utc": "2026-08-01T00:33:00+08:00",
        "invalidated_bundle_id": PARENT_ID,
        "invalidated_manifest_sha256": PARENT_MANIFEST_SHA256,
        "invalidated_attempt_tokens": FAILED_ATTEMPTS,
        "reason": "AUTHORIZATION_INSTALL_PLAN_OMITTED_REQUIRED_SPEC_AND_COMMAND_SHA256_SIDECARS",
        "workload_started": False,
        "formal_r0_credit": 0,
        "candidate_credit": 0,
        "top5_replay_credit": 0,
        "accepted_round_credit": 0,
        "rerun_same_attempt_prohibited": True,
        "fresh_attempt_required": True,
    }


def freeze(path):
    for candidate in sorted(Path(path).rglob("*"), reverse=True):
        if candidate.is_symlink():
            raise BuildError("symlink in successor")
        os.chmod(candidate, 0o444 if candidate.is_file() else 0o555)
    os.chmod(path, 0o555)


def build():
    parent = verify_parent()
    payload = {}
    modes = {}
    for relative in parent["files"]:
        if relative == "closure.json":
            continue
        source = PARENT / relative
        payload[relative] = source.read_bytes()
        modes[relative] = stat.S_IMODE(source.stat().st_mode)
    payload[MATERIALIZER] = patch_materializer(payload[MATERIALIZER])
    payload[TEST] = patch_test(payload[TEST])
    payload[INVALIDATION] = canonical(invalidation())
    payload[BUILDER] = Path(__file__).read_bytes()
    modes.update({MATERIALIZER: 0o555, TEST: 0o444, INVALIDATION: 0o444,
                  BUILDER: 0o444})

    closure = json.loads((PARENT / "closure.json").read_text(encoding="utf-8"))
    closure.update({
        "schema_version": "mysql_job_module_closure_v6",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_bundle_manifest_sha256": PARENT_MANIFEST_SHA256,
        "authorization_install": {
            "attempt_root_absent_gate": True,
            "outside_attempt_root": True,
            "root": "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/runtime_algorithm_c_successor_20260729/authorizations/<attempt>",
            "required_files": [
                "bootstrap_spec.json", "bootstrap_spec.json.sha256",
                "command_plan.json", "command_plan.json.sha256",
                "main_agent_token",
            ],
            "all_required_files_hash_verified_before_bootstrap": True,
        },
        "invalidated_authorization_sidecar_parent": INVALIDATION,
    })
    closure["files"] = {name: digest_bytes(data) for name, data in sorted(payload.items())}
    payload["closure.json"] = canonical(closure)
    modes["closure.json"] = 0o444

    manifest = dict(parent)
    manifest.pop("bundle_id", None)
    manifest.update({
        "schema_version": SCHEMA,
        "status": "SEALED_AUTHORIZATION_SIDECAR_CLOSURE_DEBUG_BOOTSTRAP_NOT_R0_OR_FORMAL_AUTHORITY",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_bundle_manifest_sha256": PARENT_MANIFEST_SHA256,
        "authorization_sidecar_closure_required": True,
        "authorization_install_plan_schema": "mysql_job_authorization_install_plan_v2",
        "invalidated_78b_authorization_sidecar_attempts": FAILED_ATTEMPTS,
        "invalidated_78b_authorization_sidecar_credit": 0,
        "invalidated_78b_authorization_sidecar_authority_sha256": digest_bytes(payload[INVALIDATION]),
        "builder_sha256": digest_bytes(payload[BUILDER]),
        "formal_launch_authorized": False,
        "formal10_authorized": False,
        "accepted_round_authorized": False,
        "files": {name: digest_bytes(data) for name, data in sorted(payload.items())},
    })
    bundle_id = digest_bytes(canonical(manifest))
    manifest["bundle_id"] = bundle_id
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / bundle_id
    if target.exists():
        raise BuildError("immutable successor already exists")
    stage = OUTPUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6))
    stage.mkdir(mode=0o700)
    for relative, data in payload.items():
        destination = stage / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        os.chmod(destination, modes[relative] & ~0o222)
    raw = canonical(manifest)
    (stage / "bundle_manifest.json").write_bytes(raw)
    (stage / "bundle_manifest.json.sha256").write_text(
        digest_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii")
    freeze(stage)
    os.rename(stage, target)
    return target, manifest


def main():
    target, manifest = build()
    print(json.dumps({
        "status": manifest["status"],
        "bundle": str(target),
        "bundle_id": manifest["bundle_id"],
        "manifest_sha256": digest(target / "bundle_manifest.json"),
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (BuildError, OSError, ValueError) as exc:
        raise SystemExit("NOT_READY: %s" % exc)

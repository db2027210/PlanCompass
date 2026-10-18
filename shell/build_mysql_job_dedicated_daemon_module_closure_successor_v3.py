#!/usr/bin/env python3
"""Seal the self-contained-test JOB successor offline and transactionally."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import stat


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = (
    "b3e3132457ef28dca78857b1d221c5f52a718560d7e2aab6fad9e4e7eeef417c"
)
PARENT_MANIFEST_SHA256 = (
    "4b8f51c5238b18ec9abebbd82af5d8c9d59d5c724e66d1696ccd1e8c9bebb3ca"
)
PARENT = (
    ROOT / "artifacts/mysql_job_dedicated_daemon_module_closure_successor_v2"
    / PARENT_ID
)
OUTPUT = (
    ROOT
    / "artifacts/mysql_job_dedicated_daemon_module_closure_successor_v3"
)
OLD_TEST = "tests/test_mysql_job_module_closure_successor_v2.py"
NEW_TEST = "tests/test_mysql_job_module_closure_successor_v3.py"
MATERIALIZER = (
    "qualification/mysql_job_dedicated_daemon_materializer_v3.py"
)
INVALIDATION = (
    "authority/mysql_job_b3e_self_test_and_materials_invalidation_20260731.json"
)
SCHEMA_V2 = "mysql_job_dedicated_daemon_module_closure_successor_bundle_v2"
SCHEMA_V3 = "mysql_job_dedicated_daemon_module_closure_successor_bundle_v3"
INVALID_ATTEMPT = (
    "job-v2-20260731t065736z-8426c73073314c4fbf079075"
)


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def replace_once(source, old, new, label):
    if source.count(old) != 1:
        raise BuildError(
            "%s patch cardinality differs: %d" % (label, source.count(old))
        )
    return source.replace(old, new)


def verify_parent():
    path = PARENT / "bundle_manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        PARENT.name != value.get("bundle_id")
        or digest(path) != PARENT_MANIFEST_SHA256
        or value.get("schema_version") != SCHEMA_V2
        or not isinstance(value.get("files"), dict)
    ):
        raise BuildError("b3e parent identity differs")
    for relative, expected in value["files"].items():
        candidate = PARENT / relative
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or digest(candidate) != expected
        ):
            raise BuildError("b3e manifest closure differs: " + relative)
    return value


def invalidation():
    return {
        "schema_version":
            "mysql_job_b3e_self_test_and_materials_invalidation_v1",
        "status": "INVALID_ZERO_CREDIT",
        "invalidated_at_utc": "2026-07-31T07:08:00+00:00",
        "bundle_id": PARENT_ID,
        "bundle_manifest_sha256": PARENT_MANIFEST_SHA256,
        "reason":
            "SEALED_BUNDLE_TEST_REFERENCES_ABSENT_EXTERNAL_BUILDER_PATH",
        "observed_test_result": {
            "passed": 0,
            "failed": 3,
            "failure_type": "FileNotFoundError",
            "missing_relative_path":
                "shell/build_mysql_job_dedicated_daemon_module_closure_successor_v2.py",
        },
        "bundle_publish_prohibited": True,
        "invalidated_materials": {
            "material_id":
                "4c0ab5d4745651f70fd4f7e54ebe67b686ed600231c6ea311df84fed5c8e5a70",
            "attempt_token": INVALID_ATTEMPT,
            "materials_manifest_sha256":
                "17027de4e57f1f04653fa6c92c7a2622e217e4e5bb13758693ac6332188fe6f3",
            "created_at_utc": "2026-07-31T06:57:36.770998+00:00",
            "expires_at_utc": "2026-07-31T07:42:36.770998+00:00",
            "reason": "BOUND_TO_INVALID_BUNDLE",
        },
        "qualification_credit": 0,
        "r0_credit": 0,
        "candidate_credit": 0,
        "replay_credit": 0,
        "accepted_round_credit": 0,
        "reuse_prohibited": True,
    }


def patched_materializer(payload):
    source = payload.decode("utf-8")
    source = replace_once(
        source,
        'SCHEMA = "%s"' % SCHEMA_V2,
        'SCHEMA = "%s"' % SCHEMA_V3,
        "materializer successor schema",
    )
    source = replace_once(
        source,
        '                "job-v2-20260731t063314z-6a38fa4a52b89030e7d6e5e4",\n',
        '                "job-v2-20260731t063314z-6a38fa4a52b89030e7d6e5e4",\n'
        '                "%s",\n' % INVALID_ATTEMPT,
        "invalidated b3e material attempt",
    )
    return source.encode("utf-8")


def freeze(root):
    for path in sorted(Path(root).rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink prohibited in successor")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(Path(root).stat().st_mode) & ~0o222)


def verify_bundle(path, expected_id=None):
    manifest_path = path / "bundle_manifest.json"
    sidecar = path / "bundle_manifest.json.sha256"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_sidecar = "%s  bundle_manifest.json\n" % digest(manifest_path)
    identity = dict(manifest)
    identity.pop("bundle_id", None)
    actual = {}
    for relative in manifest.get("files", {}):
        candidate = path / relative
        if candidate.is_symlink() or not candidate.is_file():
            raise BuildError("successor file absent: " + relative)
        actual[relative] = digest(candidate)
    if (
        path.name != (expected_id or manifest.get("bundle_id"))
        or manifest.get("bundle_id") != path.name
        or manifest.get("schema_version") != SCHEMA_V3
        or sha_bytes(canonical(identity)) != path.name
        or sidecar.read_text(encoding="ascii") != expected_sidecar
        or actual != manifest.get("files")
        or manifest.get("self_contained_bundle_tests") is not True
        or manifest.get("bundle_tests_publish_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("successor immutable identity differs")
    return manifest


def publish():
    parent = verify_parent()
    payload = {}
    modes = {}
    for relative in parent["files"]:
        if relative in (OLD_TEST, "closure.json"):
            continue
        source = PARENT / relative
        payload[relative] = source.read_bytes()
        modes[relative] = stat.S_IMODE(source.stat().st_mode)
    payload[MATERIALIZER] = patched_materializer(payload[MATERIALIZER])
    modes[MATERIALIZER] = 0o555
    payload[NEW_TEST] = (
        ROOT / "tests/test_mysql_job_module_closure_successor_v3.py"
    ).read_bytes()
    modes[NEW_TEST] = 0o444
    payload[INVALIDATION] = canonical(invalidation())
    modes[INVALIDATION] = 0o444

    closure = json.loads(
        (PARENT / "closure.json").read_text(encoding="utf-8")
    )
    closure.update(
        {
            "schema_version": "mysql_job_module_closure_v3",
            "predecessor_bundle_id": PARENT_ID,
            "predecessor_bundle_manifest_sha256":
                PARENT_MANIFEST_SHA256,
            "self_contained_bundle_test": NEW_TEST,
            "bundle_test_external_builder_dependency_prohibited": True,
            "bundle_test_publish_prohibited": True,
            "invalidated_b3e_bundle_and_materials": INVALIDATION,
        }
    )
    closure["files"] = {
        relative: sha_bytes(value)
        for relative, value in sorted(payload.items())
    }
    payload["closure.json"] = canonical(closure)
    modes["closure.json"] = 0o444
    files = {
        relative: sha_bytes(value)
        for relative, value in sorted(payload.items())
    }

    identity = {
        key: item
        for key, item in parent.items()
        if key not in ("bundle_id", "files")
    }
    identity.update(
        {
            "schema_version": SCHEMA_V3,
            "predecessor_bundle_id": PARENT_ID,
            "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
            "self_contained_bundle_tests": True,
            "self_contained_bundle_test": NEW_TEST,
            "bundle_tests_external_project_dependency_prohibited": True,
            "bundle_tests_publish_prohibited": True,
            "remote_isomorphic_fixture_execution_required": True,
            "missing_sibling_module_fail_closed_test_required": True,
            "invalidated_b3e_bundle_id": PARENT_ID,
            "invalidated_b3e_bundle_credit": 0,
            "invalidated_b3e_material_attempt": INVALID_ATTEMPT,
            "invalidated_b3e_material_credit": 0,
            "invalidation_sha256": sha_bytes(payload[INVALIDATION]),
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
            "offline_build_only": True,
            "remote_actions": 0,
            "files": files,
        }
    )
    bundle_id = sha_bytes(canonical(identity))
    manifest = dict(identity)
    manifest["bundle_id"] = bundle_id
    OUTPUT.mkdir(parents=True, exist_ok=True)
    final = OUTPUT / bundle_id
    if final.exists() or final.is_symlink():
        freeze(final)
        verify_bundle(final, bundle_id)
        return final
    stage = OUTPUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6))
    stage.mkdir(mode=0o700)
    try:
        for relative, value in payload.items():
            path = stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
            os.chmod(path, modes[relative])
        manifest_payload = canonical(manifest)
        manifest_path = stage / "bundle_manifest.json"
        manifest_path.write_bytes(manifest_payload)
        os.chmod(manifest_path, 0o444)
        sidecar = stage / "bundle_manifest.json.sha256"
        sidecar.write_text(
            "%s  bundle_manifest.json\n" % sha_bytes(manifest_payload),
            encoding="ascii",
        )
        os.chmod(sidecar, 0o444)
        os.rename(stage, final)
    except BaseException:
        raise
    freeze(final)
    verify_bundle(final, bundle_id)
    return final


if __name__ == "__main__":
    value = publish()
    print(
        json.dumps(
            {
                "status": "SEALED_LOCAL_ONLY_NOT_LAUNCHED",
                "bundle": str(value),
                "bundle_id": value.name,
                "bundle_manifest_sha256":
                    digest(value / "bundle_manifest.json"),
                "formal_launch_authorized": False,
                "accepted_round_authorized": False,
            },
            sort_keys=True,
        )
    )

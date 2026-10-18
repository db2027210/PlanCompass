#!/usr/bin/env python3
"""Seal the JOB v5 estimator-identity successor without launching anything."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import stat


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "d6eddbd219c089e930686264e4099f1ced4623e481ecbb348e7b0345858629f3"
PARENT_MANIFEST_SHA256 = "68eea6ed38418d0665ac3787af9f62adf34e3710bdb3d0d6f09fa88d2e600b26"
PARENT = ROOT / "artifacts/mysql_job_raw_all_subset_three_level_successor_v4" / PARENT_ID
OUTPUT = ROOT / "artifacts/mysql_job_raw_all_subset_three_level_successor_v5"
SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v5"
OLD_TEST = "tests/test_mysql_job_module_closure_successor_v4.py"
NEW_TEST = "tests/test_mysql_job_module_closure_successor_v5.py"
OLD_MATERIALIZER = "qualification/mysql_job_dedicated_daemon_materializer_v4.py"
NEW_MATERIALIZER = "qualification/mysql_job_dedicated_daemon_materializer_v5.py"
INVALIDATION = "authority/mysql_job_d6ed_unresponsive_estimator_invalidation_20260731.json"
INVALIDATED_ATTEMPT = "job-v2-20260731t085459z-a1e71a7acd26efec1f3f7e89"
ALGORITHM_C_CONTRACT = {
    "formal_raw_all_nonempty_subsets_before_top5": True,
    "plan_clustering_diagnostic_only": True,
    "missing_l2_l3_independent": True,
    "original_gradient_confidence_required": True,
    "fixed_top5_only": True,
    "candidate_estimator_identity_required": True,
    "canonical_plan_bundle_required_before_seal": True,
    "all_unresponsive_candidates_fail_closed": True,
}
ALGORITHM_SOURCE_OVERRIDES = {
    "qualification/gradient_descent_optimizer.py": "gradient_descent_optimizer.py",
    "qualification/experience_table_subset_optimizer.py": "experience_table_subset_optimizer.py",
    "qualification/mysql_parameter_optimizer.py": "mysql_parameter_optimizer.py",
    "qualification/mysql_plan_clustering.py": "mysql_plan_clustering.py",
    "qualification/mysql_three_level_fingerprint.py": "mysql_three_level_fingerprint.py",
}


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def sha_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def verify_parent():
    manifest_path = PARENT / "bundle_manifest.json"
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        PARENT.name != PARENT_ID
        or value.get("bundle_id") != PARENT_ID
        or digest(manifest_path) != PARENT_MANIFEST_SHA256
        or value.get("schema_version") != "mysql_job_raw_all_subset_three_level_successor_bundle_v4"
        or not isinstance(value.get("files"), dict)
    ):
        raise BuildError("d6ed authority parent identity differs")
    for relative, expected in value["files"].items():
        candidate = PARENT / relative
        if candidate.is_symlink() or not candidate.is_file() or digest(candidate) != expected:
            raise BuildError("d6ed authority closure differs: " + relative)
    return value


def invalidation():
    return {
        "schema_version": "mysql_job_d6ed_unresponsive_estimator_invalidation_v1",
        "status": "INVALID_ZERO_CREDIT",
        "invalidated_at_utc": "2026-07-31T09:30:00+00:00",
        "invalidated_bundle_id": PARENT_ID,
        "invalidated_manifest_sha256": PARENT_MANIFEST_SHA256,
        "invalidated_attempt_token": INVALIDATED_ATTEMPT,
        "reason": "ALL_15_NONBASELINE_CANDIDATES_REUSED_BASELINE_RESOURCE_AND_PLAN_WORK_WITHOUT_CANDIDATE_ESTIMATOR_IDENTITY_BINDING",
        "ranking_error_proven": False,
        "gradient_formula_error_proven": False,
        "formal_r0_credit": 0,
        "candidate_credit": 0,
        "top5_replay_credit": 0,
        "accepted_round_credit": 0,
        "reuse_prohibited": True,
        "fresh_r0_required": True,
    }


def patched_materializer(payload):
    source = payload.decode("utf-8")
    old = 'SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v4"'
    new = 'SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v5"'
    if source.count(old) != 1:
        raise BuildError("v4 materializer schema patch cardinality differs")
    source = source.replace(old, new)
    old_attempt = '                "job-v2-20260731t070911z-0969fe661522c07a32fe38b9",\n'
    new_attempt = old_attempt + '                "' + INVALIDATED_ATTEMPT + '",\n'
    if source.count(old_attempt) != 1:
        raise BuildError("v4 materializer invalidated-attempt patch cardinality differs")
    return source.replace(old_attempt, new_attempt).encode("utf-8")


def verify_bundle(path, expected_id=None):
    manifest_path = path / "bundle_manifest.json"
    sidecar = path / "bundle_manifest.json.sha256"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    identity = dict(manifest)
    identity.pop("bundle_id", None)
    actual = {}
    actual_paths = set()
    for candidate in path.rglob("*"):
        relative = candidate.relative_to(path).as_posix()
        if candidate.is_symlink():
            raise BuildError("symlink in sealed successor: " + relative)
        if candidate.is_file():
            actual_paths.add(relative)
        elif not candidate.is_dir():
            raise BuildError("non-regular entry in sealed successor: " + relative)
    for relative in manifest.get("files", {}):
        candidate = path / relative
        if candidate.is_symlink() or not candidate.is_file():
            raise BuildError("successor file absent: " + relative)
        actual[relative] = digest(candidate)
    expected_paths = set(manifest.get("files", {})) | {"bundle_manifest.json", "bundle_manifest.json.sha256"}
    if (
        path.name != (expected_id or manifest.get("bundle_id"))
        or manifest.get("bundle_id") != path.name
        or manifest.get("schema_version") != SCHEMA
        or sha_bytes(canonical(identity)) != path.name
        or sidecar.read_text(encoding="ascii") != "%s  bundle_manifest.json\n" % digest(manifest_path)
        or actual != manifest.get("files")
        or actual_paths != expected_paths
        or manifest.get("algorithm_c_contract") != ALGORITHM_C_CONTRACT
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or any("__pycache__" in Path(item).parts or item.endswith((".pyc", ".pyo")) for item in actual_paths)
    ):
        raise BuildError("v5 immutable identity differs")
    return manifest


def freeze(root):
    for path in sorted(Path(root).rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink prohibited in successor")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(Path(root).stat().st_mode) & ~0o222)


def publish():
    parent = verify_parent()
    payload, modes = {}, {}
    for relative in parent["files"]:
        if relative in {OLD_TEST, OLD_MATERIALIZER, "closure.json"}:
            continue
        source = PARENT / relative
        payload[relative] = source.read_bytes()
        modes[relative] = stat.S_IMODE(source.stat().st_mode)
    payload[NEW_MATERIALIZER] = patched_materializer((PARENT / OLD_MATERIALIZER).read_bytes())
    modes[NEW_MATERIALIZER] = 0o555
    for relative, source_name in ALGORITHM_SOURCE_OVERRIDES.items():
        payload[relative] = (ROOT / source_name).read_bytes()
        modes[relative] = 0o444
    payload[NEW_TEST] = (ROOT / NEW_TEST).read_bytes()
    modes[NEW_TEST] = 0o444
    payload[INVALIDATION] = canonical(invalidation())
    modes[INVALIDATION] = 0o444
    closure = json.loads((PARENT / "closure.json").read_text(encoding="utf-8"))
    closure.update({
        "schema_version": "mysql_job_module_closure_v5",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_bundle_manifest_sha256": PARENT_MANIFEST_SHA256,
        "self_contained_bundle_test": NEW_TEST,
        "invalidated_d6ed_unresponsive_estimator": INVALIDATION,
        "algorithm_c_contract": ALGORITHM_C_CONTRACT,
        "formal_candidate_source": "global_raw_all_nonempty_subsets_candidate_score_with_bound_estimator_identity",
        "formal_replay_policy": "fixed_top_5_only",
    })
    closure["files"] = {relative: sha_bytes(value) for relative, value in sorted(payload.items())}
    payload["closure.json"] = canonical(closure)
    modes["closure.json"] = 0o444
    files = {relative: sha_bytes(value) for relative, value in sorted(payload.items())}
    identity = {key: item for key, item in parent.items() if key not in ("bundle_id", "files")}
    identity.update({
        "schema_version": SCHEMA,
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
        "self_contained_bundle_tests": True,
        "self_contained_bundle_test": NEW_TEST,
        "bundle_tests_external_project_dependency_prohibited": True,
        "bundle_tests_publish_prohibited": True,
        "algorithm_c_contract": ALGORITHM_C_CONTRACT,
        "invalidated_d6ed_bundle_id": PARENT_ID,
        "invalidated_d6ed_attempt": INVALIDATED_ATTEMPT,
        "invalidated_d6ed_credit": 0,
        "invalidation_sha256": sha_bytes(payload[INVALIDATION]),
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "offline_build_only": True,
        "remote_actions": 0,
        "files": files,
    })
    bundle_id = sha_bytes(canonical(identity))
    manifest = {**identity, "bundle_id": bundle_id}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    final = OUTPUT / bundle_id
    if final.exists() or final.is_symlink():
        freeze(final)
        verify_bundle(final, bundle_id)
        return final
    stage = OUTPUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6))
    stage.mkdir(mode=0o700)
    for relative, value in payload.items():
        path = stage / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        os.chmod(path, modes[relative])
    manifest_payload = canonical(manifest)
    (stage / "bundle_manifest.json").write_bytes(manifest_payload)
    os.chmod(stage / "bundle_manifest.json", 0o444)
    (stage / "bundle_manifest.json.sha256").write_text(
        "%s  bundle_manifest.json\n" % sha_bytes(manifest_payload), encoding="ascii"
    )
    os.chmod(stage / "bundle_manifest.json.sha256", 0o444)
    os.rename(stage, final)
    freeze(final)
    verify_bundle(final, bundle_id)
    return final


if __name__ == "__main__":
    value = publish()
    print(json.dumps({"status": "SEALED_LOCAL_ONLY_NOT_LAUNCHED", "bundle": str(value), "bundle_id": value.name, "bundle_manifest_sha256": digest(value / "bundle_manifest.json"), "formal_launch_authorized": False, "accepted_round_authorized": False}, sort_keys=True))

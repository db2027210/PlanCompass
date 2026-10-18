#!/usr/bin/env python3
"""Seal the JOB raw-all-subset/three-level successor transactionally."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import stat


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = (
    "e12670fd20039aaf4dc19d3d24533fbaf179d84511837d89b41b870a6d664de8"
)
PARENT_MANIFEST_SHA256 = (
    "e5b0f5821e88c0da10675f9f932a9bebdc4b8f8fc8e6b18a693d47c9862ec95f"
)
PARENT = (
    ROOT / "artifacts/mysql_job_dedicated_daemon_module_closure_successor_v3"
    / PARENT_ID
)
OUTPUT = (
    ROOT
    / "artifacts/mysql_job_raw_all_subset_three_level_successor_v4"
)
OLD_TEST = "tests/test_mysql_job_module_closure_successor_v3.py"
NEW_TEST = "tests/test_mysql_job_module_closure_successor_v4.py"
OLD_MATERIALIZER = (
    "qualification/mysql_job_dedicated_daemon_materializer_v3.py"
)
NEW_MATERIALIZER = (
    "qualification/mysql_job_dedicated_daemon_materializer_v4.py"
)
INVALIDATION = (
    "authority/mysql_job_e126_algorithm_and_materials_invalidation_20260731.json"
)
SCHEMA_V3 = "mysql_job_dedicated_daemon_module_closure_successor_bundle_v3"
SCHEMA_V4 = "mysql_job_raw_all_subset_three_level_successor_bundle_v4"
INVALID_ATTEMPT = (
    "job-v2-20260731t070911z-0969fe661522c07a32fe38b9"
)
INVALID_MATERIAL_ID = (
    "128c4f23cda01ca1a7aad122917426579c4b553ac1aec8a07ad9ca3a771bd270"
)
INVALIDATED_D6ED_BUNDLE_ID = (
    "d6eddbd219c089e930686264e4099f1ced4623e481ecbb348e7b0345858629f3"
)
INVALIDATED_D6ED_ATTEMPT = (
    "job-v2-20260731t085459z-a1e71a7acd26efec1f3f7e89"
)
ALGORITHM_C_CONTRACT = {
    "formal_raw_all_nonempty_subsets_before_top5": True,
    "plan_clustering_diagnostic_only": True,
    "missing_l2_l3_independent": True,
    "original_gradient_confidence_required": True,
    "fixed_top5_only": True,
}
ALGORITHM_SOURCE_OVERRIDES = {
    "qualification/gradient_descent_optimizer.py":
        "gradient_descent_optimizer.py",
    "qualification/experience_table_subset_optimizer.py":
        "experience_table_subset_optimizer.py",
    "qualification/mysql_parameter_optimizer.py":
        "mysql_parameter_optimizer.py",
    "qualification/mysql_plan_clustering.py":
        "mysql_plan_clustering.py",
    "qualification/mysql_three_level_fingerprint.py":
        "mysql_three_level_fingerprint.py",
}


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
        or value.get("schema_version") != SCHEMA_V3
        or not isinstance(value.get("files"), dict)
    ):
        raise BuildError("e126 parent identity differs")
    for relative, expected in value["files"].items():
        candidate = PARENT / relative
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or digest(candidate) != expected
        ):
            raise BuildError("e126 manifest closure differs: " + relative)
    return value


def invalidation():
    return {
        "schema_version":
            "mysql_job_e126_algorithm_and_materials_invalidation_v1",
        "status": "INVALID_ZERO_CREDIT",
        "invalidated_at_utc": "2026-07-31T07:55:00+00:00",
        "bundle_id": PARENT_ID,
        "bundle_manifest_sha256": PARENT_MANIFEST_SHA256,
        "reason": (
            "FORMAL_INPUT_USED_L1_REPRESENTATIVES_AND_GRADIENT_CLOSURE_"
            "OMITTED_CONFIDENCE_AND_COMPLETE_EVENT"
        ),
        "observed_test_result": {
            "passed": 0,
            "failed": 2,
            "failure_type": "AlgorithmContractViolation",
            "violations": [
                "plan_cluster_representatives_used_as_formal_replay_input",
                "g_j_omitted_confidence_and_required_event_fields",
            ],
        },
        "bundle_publish_prohibited": True,
        "invalidated_materials": {
            "material_id": INVALID_MATERIAL_ID,
            "attempt_token": INVALID_ATTEMPT,
            "materials_manifest_sha256":
                "06f78d9cf1da3152588ed050ac69b74ffa052919e86e0aa80ae0ea9669c6d11f",
            "created_at_utc": None,
            "expires_at_utc": None,
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
        'SCHEMA = "%s"' % SCHEMA_V3,
        'SCHEMA = "%s"' % SCHEMA_V4,
        "materializer successor schema",
    )
    source = replace_once(
        source,
        '                "job-v2-20260731t065736z-8426c73073314c4fbf079075",\n',
        '                "job-v2-20260731t065736z-8426c73073314c4fbf079075",\n'
        '                "%s",\n' % INVALID_ATTEMPT,
        "invalidated e126 material attempt",
    )
    source = replace_once(
        source,
        '                "bootstrap_spec.json": {\n'
        '                    "source_relative": "bootstrap_spec.json",\n'
        '                    "destination":\n'
        '                        authorization_root + "/bootstrap_spec.json",\n'
        '                    "mode": "0400",\n'
        '                },\n',
        '                "bootstrap_spec.json": {\n'
        '                    "source_relative": "bootstrap_spec.json",\n'
        '                    "destination":\n'
        '                        authorization_root + "/bootstrap_spec.json",\n'
        '                    "mode": "0400",\n'
        '                },\n'
        '                "bootstrap_spec.json.sha256": {\n'
        '                    "source_relative": "bootstrap_spec.json.sha256",\n'
        '                    "destination": authorization_root\n'
        '                        + "/bootstrap_spec.json.sha256",\n'
        '                    "mode": "0400",\n'
        '                },\n',
        "bootstrap spec sidecar installation",
    )
    source = replace_once(
        source,
        '                "command_plan.json": {\n'
        '                    "source_relative": "command_plan.json",\n'
        '                    "destination":\n'
        '                        authorization_root + "/command_plan.json",\n'
        '                    "mode": "0400",\n'
        '                },\n',
        '                "command_plan.json": {\n'
        '                    "source_relative": "command_plan.json",\n'
        '                    "destination":\n'
        '                        authorization_root + "/command_plan.json",\n'
        '                    "mode": "0400",\n'
        '                },\n'
        '                "command_plan.json.sha256": {\n'
        '                    "source_relative": "command_plan.json.sha256",\n'
        '                    "destination": authorization_root\n'
        '                        + "/command_plan.json.sha256",\n'
        '                    "mode": "0400",\n'
        '                },\n',
        "command plan sidecar installation",
    )
    source = replace_once(
        source,
        '                "job-v2-20260731t070911z-0969fe661522c07a32fe38b9",\n',
        '                "job-v2-20260731t070911z-0969fe661522c07a32fe38b9",\n'
        '                "%s",\n' % INVALIDATED_D6ED_ATTEMPT,
        "invalidated d6ed launch attempt",
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
    expected_paths = set(manifest.get("files", {})) | {
        "bundle_manifest.json",
        "bundle_manifest.json.sha256",
    }
    forbidden = [
        relative
        for relative in actual_paths
        if "__pycache__" in Path(relative).parts
        or relative.endswith((".pyc", ".pyo"))
    ]
    if (
        path.name != (expected_id or manifest.get("bundle_id"))
        or manifest.get("bundle_id") != path.name
        or manifest.get("schema_version") != SCHEMA_V4
        or sha_bytes(canonical(identity)) != path.name
        or sidecar.read_text(encoding="ascii") != expected_sidecar
        or actual != manifest.get("files")
        or manifest.get("self_contained_bundle_tests") is not True
        or manifest.get("bundle_tests_publish_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or manifest.get("algorithm_c_contract") != ALGORITHM_C_CONTRACT
        or actual_paths != expected_paths
        or forbidden
    ):
        raise BuildError("successor immutable identity differs")
    return manifest


def publish():
    parent = verify_parent()
    payload = {}
    modes = {}
    for relative in parent["files"]:
        if relative in (OLD_TEST, OLD_MATERIALIZER, "closure.json"):
            continue
        source = PARENT / relative
        payload[relative] = source.read_bytes()
        modes[relative] = stat.S_IMODE(source.stat().st_mode)
    payload[NEW_MATERIALIZER] = patched_materializer(
        (PARENT / OLD_MATERIALIZER).read_bytes()
    )
    modes[NEW_MATERIALIZER] = 0o555
    for relative, source_name in ALGORITHM_SOURCE_OVERRIDES.items():
        payload[relative] = (ROOT / source_name).read_bytes()
        modes[relative] = 0o444
    payload[NEW_TEST] = (
        ROOT / "tests/test_mysql_job_module_closure_successor_v4.py"
    ).read_bytes()
    modes[NEW_TEST] = 0o444
    payload[INVALIDATION] = canonical(invalidation())
    modes[INVALIDATION] = 0o444
    d6ed_invalidation = (
        "authority/mysql_job_d6ed_missing_spec_sidecar_invalidation_"
        "20260731.json"
    )
    payload[d6ed_invalidation] = canonical(
        {
            "schema_version":
                "mysql_job_d6ed_missing_spec_sidecar_invalidation_v1",
            "status": "INVALID_ZERO_CREDIT",
            "invalidated_bundle_id": INVALIDATED_D6ED_BUNDLE_ID,
            "invalidated_attempt_token": INVALIDATED_D6ED_ATTEMPT,
            "reason":
                "authorization install plan omitted bootstrap spec SHA-256 "
                "sidecar required by the sealed bootstrap authority",
            "workload_pid_observed": False,
            "r0_credit": 0,
            "accepted_round_credit": 0,
            "reuse_prohibited": True,
        }
    )
    modes[d6ed_invalidation] = 0o444

    closure = json.loads(
        (PARENT / "closure.json").read_text(encoding="utf-8")
    )
    closure.update(
        {
            "schema_version": "mysql_job_module_closure_v4",
            "predecessor_bundle_id": PARENT_ID,
            "predecessor_bundle_manifest_sha256":
                PARENT_MANIFEST_SHA256,
            "self_contained_bundle_test": NEW_TEST,
            "bundle_test_external_builder_dependency_prohibited": True,
            "bundle_test_publish_prohibited": True,
            "invalidated_e126_bundle_and_materials": INVALIDATION,
            "invalidated_d6ed_real_launch": d6ed_invalidation,
            "algorithm_c_contract": ALGORITHM_C_CONTRACT,
            "formal_candidate_source":
                "global_raw_all_nonempty_subsets_candidate_score",
            "formal_replay_policy": "fixed_top_5_only",
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
            "schema_version": SCHEMA_V4,
            "predecessor_bundle_id": PARENT_ID,
            "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
            "self_contained_bundle_tests": True,
            "self_contained_bundle_test": NEW_TEST,
            "bundle_tests_external_project_dependency_prohibited": True,
            "bundle_tests_publish_prohibited": True,
            "remote_isomorphic_fixture_execution_required": True,
            "missing_sibling_module_fail_closed_test_required": True,
            "algorithm_c_contract": ALGORITHM_C_CONTRACT,
            "invalidated_e126_bundle_id": PARENT_ID,
            "invalidated_e126_bundle_credit": 0,
            "invalidated_e126_material_attempt": INVALID_ATTEMPT,
            "invalidated_e126_material_credit": 0,
            "invalidated_d6ed_bundle_id": INVALIDATED_D6ED_BUNDLE_ID,
            "invalidated_d6ed_attempt": INVALIDATED_D6ED_ATTEMPT,
            "invalidated_d6ed_credit": 0,
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

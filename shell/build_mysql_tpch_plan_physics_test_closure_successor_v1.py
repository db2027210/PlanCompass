#!/usr/bin/env python3
"""Build an immutable TPC-H successor closing a stale Algorithm-C unit test.

The selected formal configuration already uses the required plan_physics
estimator.  Its embedded source closure nevertheless contains one v3 test
fixture that still requests ratio_scale, which the current fail-closed
validator correctly rejects.  This builder changes only that fixture, rebinds
the nested source manifest and top-level bundle closure, and records zero
experimental credit.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(__file__).resolve()
PARENT_ID = "87ed507ed689c103781db5f96332ae7875e4773d04d69b98a7e63e3d4097b257"
PARENT_MANIFEST_SHA256 = "fe747c7811a6288a4b624f5bab48ace95727069a534e7314ec6e79938ca47e04"
PARENT = ROOT / "artifacts/mysql_tpch_memswap_consistency_successor_v1" / PARENT_ID
OUTPUT = ROOT / "artifacts/mysql_tpch_plan_physics_test_closure_successor_v1"
TEST = "algorithm_source/test_mysql_formal_algorithm_b_contract.py"
SOURCE_MANIFEST = "algorithm_source/source_manifest.json"
FORMAL_CONFIG = "algorithm_source/configs/tpch_memory.algorithm_c.optimizer.json"
BUILDER = "builder/build_mysql_tpch_plan_physics_test_closure_successor_v1.py"
INVALIDATION = "invalidated_stale_ratio_scale_unit_fixture.json"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
INVALIDATED_CACHE_POLLUTED_BUILD_ID = (
    "9858472f0e31d9830e592719b8af2154f94e720dc526c39305008223ab6ea2ea"
)
EXPECTED_NESTED_STALE_HASHES = {
    "profiles/tpch_v35_debug_profile.json": (
        "ba4162ed520ac84e171017b6c699180bd5535e97aeda91a23220d83bacf22795",
        "33f8039b1003b1fda2b848ccdc74dedfa1609f69410bde3cba82f005edd8a5a8",
    ),
    "shell/mysql_tpch_algorithm_c_local_services_v27.py": (
        "5f323e5f2bd9226580b3febbb60846aefc512f279402ef91781d4445ac479b16",
        "0e45939ff29cfa89bc073ee377994e1f1a3d4e59210f1f9f534432ee7a4c7070",
    ),
    "shell/verify_mysql_tpch_v35_readiness.py": (
        "6b9541c35911e90e90d35b74688a339f4bf442a9e3ad91c49976f41bc8251197",
        "d4179754fec821bfbbac895cf6baba227085ffb977fb3710d1f7c3143462158a",
    ),
    "tests/test_mysql_tpch_v35_authority.py": (
        "b77d9f5c9d48d6aece0f6e3f8379f95bb5a3b855f7a5037530407cc82ecd37bb",
        "8dffb5ebe2aa67a4cd9e917661c3afc6559f444f7f01365fceb81b7424cfa437",
    ),
}


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError("JSON object required: " + str(path))
    return value


def relative_files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError("symlink prohibited: " + str(path))
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in {
            "bundle_manifest.json", "bundle_manifest.json.sha256"
        }:
            result[relative] = sha_file(path)
    return result


def verify_parent() -> dict[str, Any]:
    manifest = load(PARENT / "bundle_manifest.json")
    unsigned = {key: value for key, value in manifest.items()
                if key != "bundle_id"}
    source_manifest = load(PARENT / SOURCE_MANIFEST)
    source_unsigned = {key: value for key, value in source_manifest.items()
                       if key != "bundle_id"}
    if (
        manifest.get("bundle_id") != PARENT_ID
        or sha_bytes(canonical(unsigned)) != PARENT_ID
        or sha_file(PARENT / "bundle_manifest.json") != PARENT_MANIFEST_SHA256
        or relative_files(PARENT) != manifest.get("files")
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or sha_bytes(canonical(source_unsigned)) != source_manifest.get("bundle_id")
        or manifest.get("algorithm_source_bundle_id") != source_manifest.get("bundle_id")
        or manifest.get("algorithm_source_manifest_sha256")
        != sha_file(PARENT / SOURCE_MANIFEST)
    ):
        raise BuildError("parent identity, closure, or authority differs")
    source_files = source_manifest.get("files")
    if not isinstance(source_files, dict):
        raise BuildError("nested source file map absent")
    stale: dict[str, tuple[str, str]] = {}
    for relative, expected in source_files.items():
        path = PARENT / "algorithm_source" / relative
        if not path.is_file() or path.is_symlink():
            raise BuildError("nested source closure path differs: " + relative)
        actual = sha_file(path)
        if actual != expected:
            stale[relative] = (expected, actual)
    if stale != EXPECTED_NESTED_STALE_HASHES:
        raise BuildError("nested stale-hash inventory differs")
    config = load(PARENT / FORMAL_CONFIG)
    if (
        config.get("formal_scope") != "mysql_only"
        or config.get("benchmark") != "tpch"
        or (config.get("estimator") or {}).get("type") != "plan_physics"
        or (config.get("optimizer") or {}).get("subset_max_workers") != 1
        or len((config.get("cost_parameters") or {}).get("active") or []) != 4
        or (config.get("formal_outer_protocol") or {}).get(
            "formal_search_initial_replay_top_k") != 5
        or (config.get("formal_outer_protocol") or {}).get(
            "formal_search_max_replay_top_k") != 5
        or (config.get("formal_outer_protocol") or {}).get(
            "target_accepted_shrinks") != 10
    ):
        raise BuildError("formal TPC-H Algorithm-C binding differs")
    return manifest


def patch_test(payload: bytes) -> bytes:
    text = payload.decode("utf-8")
    old = '                "type": "ratio_scale",'
    new = '                "type": "plan_physics",'
    if text.count(old) != 1 or text.count(new) != 0:
        raise BuildError("stale estimator fixture patch cardinality differs")
    text = text.replace(old, new)
    anchor = '            "optimizer": {\n'
    fingerprint = '''            "three_level_fingerprint": {
                "schema_version": "mysql_candidate_three_level_fingerprint_v1",
                "l1_version": "mysql_plan_structure_l1_v2",
                "l2_required_metrics": [
                    "workers", "hash_batches", "sort_mode", "spill_mode",
                    "spill_bytes", "bitmap_lossy_pages", "intermediate_rows",
                ],
                "l3_profile": "job",
                "l3_primary_metrics": [
                    "memory_peak_bytes", "dynamic_execution_memory_bytes",
                    "spill_bytes",
                ],
                "missing_evidence_policy": "independent_singleton",
                "qps_role": "outer_acceptance_only",
                "tolerance": {
                    "l2_relative": 0.05,
                    "l2_absolute": 1,
                    "l3_relative": 0.1,
                    "l3_absolute": 1e-09,
                },
            },
'''
    if text.count(anchor) != 1 or "three_level_fingerprint" in text:
        raise BuildError("stale fingerprint fixture patch cardinality differs")
    return text.replace(anchor, fingerprint + anchor).encode("utf-8")


def invalidation() -> dict[str, Any]:
    return {
        "schema_version": "mysql_tpch_stale_ratio_scale_unit_fixture_invalidation_v1",
        "status": "INVALIDATED_ZERO_CREDIT_TEST_CLOSURE_ONLY",
        "predecessor_bundle_id": PARENT_ID,
        "defect": (
            "embedded v3 unit-test fixture requested ratio_scale and omitted "
            "the three-level fingerprint after the formal validator was "
            "correctly tightened"
        ),
        "preexisting_nested_source_manifest_stale_hashes": {
            name: {"declared": pair[0], "top_level_authoritative": pair[1]}
            for name, pair in sorted(EXPECTED_NESTED_STALE_HASHES.items())
        },
        "nested_source_manifest_rebound_to_top_level_authoritative_closure": True,
        "formal_runtime_config_estimator_before": "plan_physics",
        "formal_runtime_config_estimator_after": "plan_physics",
        "algorithm_changed": False,
        "resource_envelope_changed": False,
        "workload_started": False,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


def run_tests(stage: Path) -> str:
    source = stage / "algorithm_source"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join((str(source), str(ROOT)))
    tests = [
        "test_experience_table_gradient_optimizer.py",
        "test_mysql_all_subset_integration.py",
        "test_mysql_formal_algorithm_b_contract.py",
        "test_debug_mysql_gradient_round.py",
    ]
    completed = subprocess.run(
        [str(PYTHON), "-m", "pytest", "-q",
         *(str(source / test) for test in tests)],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0 or "41 passed" not in completed.stdout:
        raise BuildError("successor algorithm tests failed:\n" + completed.stdout[-12000:])
    return completed.stdout.strip()


def remove_test_caches(stage: Path) -> None:
    for path in sorted(stage.rglob("*"), reverse=True):
        if path.is_dir() and path.name in {"__pycache__", ".pytest_cache"}:
            shutil.rmtree(path)
        elif path.is_file() and path.suffix in {".pyc", ".pyo"}:
            path.unlink()


def seal_permissions(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o444 if path.is_file() else 0o555)
    os.chmod(root, 0o555)


def build() -> dict[str, Any]:
    parent = verify_parent()
    content: dict[str, bytes] = {}
    for relative, expected in sorted(parent["files"].items()):
        payload = (PARENT / relative).read_bytes()
        if sha_bytes(payload) != expected:
            raise BuildError("parent file hash differs: " + relative)
        content[relative] = payload

    content[TEST] = patch_test(content[TEST])
    nested = json.loads(content[SOURCE_MANIFEST])
    old_nested_id = nested.pop("bundle_id")
    nested["schema_version"] = "mysql_tpch_plan_physics_test_closure_source_v1"
    nested["predecessor_source_bundle_id"] = old_nested_id
    nested["files"] = {
        relative.removeprefix("algorithm_source/"): sha_bytes(payload)
        for relative, payload in sorted(content.items())
        if relative.startswith("algorithm_source/")
        and relative != SOURCE_MANIFEST
    }
    new_nested_id = sha_bytes(canonical(nested))
    nested["bundle_id"] = new_nested_id
    content[SOURCE_MANIFEST] = canonical(nested)
    content[BUILDER] = SOURCE.read_bytes()
    content[INVALIDATION] = canonical(invalidation())
    files = {name: sha_bytes(payload) for name, payload in sorted(content.items())}

    identity = {key: value for key, value in parent.items()
                if key not in {"bundle_id", "files"}}
    identity.update({
        "schema_version": "mysql_tpch_plan_physics_test_closure_successor_v1",
        "successor_generation": "plan_physics_test_closure_v1",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
        "algorithm_source_bundle_id": new_nested_id,
        "algorithm_source_manifest_sha256": files[SOURCE_MANIFEST],
        "builder_sha256": files[BUILDER],
        "invalidated_stale_ratio_scale_unit_fixture_sha256": files[INVALIDATION],
        "invalidated_cache_polluted_build_id": INVALIDATED_CACHE_POLLUTED_BUILD_ID,
        "formal_runtime_algorithm_changed": False,
        "formal_runtime_resource_envelope_changed": False,
        "fresh_qualification_required": True,
        "fresh_r0_required": True,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "offline_build_only": True,
        "remote_actions": 0,
        "files": files,
    })
    bundle_id = sha_bytes(canonical(identity))
    manifest = dict(identity, bundle_id=bundle_id)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    destination = OUTPUT / bundle_id
    test_result = "existing immutable bundle reverified"
    if not destination.exists():
        stage = OUTPUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6))
        stage.mkdir(mode=0o700)
        try:
            for relative, payload in sorted(content.items()):
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            test_result = run_tests(stage)
            remove_test_caches(stage)
            raw = canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii"
            )
            seal_permissions(stage)
            os.replace(stage, destination)
        except BaseException:
            if stage.exists():
                shutil.rmtree(stage)
            raise

    published = load(destination / "bundle_manifest.json")
    unsigned = {key: value for key, value in published.items()
                if key != "bundle_id"}
    if (
        published != manifest
        or destination.name != bundle_id
        or sha_bytes(canonical(unsigned)) != bundle_id
        or relative_files(destination) != files
        or (destination / "bundle_manifest.json.sha256").read_text(encoding="ascii")
        != sha_file(destination / "bundle_manifest.json") + "  bundle_manifest.json\n"
    ):
        raise BuildError("published successor identity or closure differs")
    for path in [destination, *destination.rglob("*")]:
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError("published successor is mutable: " + str(path))
    config = load(destination / FORMAL_CONFIG)
    if (config.get("estimator") or {}).get("type") != "plan_physics":
        raise BuildError("published formal estimator binding differs")
    return {
        "status": "PASS_IMMUTABLE_TPCH_PLAN_PHYSICS_TEST_CLOSURE_SUCCESSOR",
        "bundle": str(destination),
        "bundle_id": bundle_id,
        "bundle_manifest_sha256": sha_file(destination / "bundle_manifest.json"),
        "algorithm_source_bundle_id": new_nested_id,
        "verified_files": len(files),
        "algorithm_tests": test_result,
        "formal_runtime_algorithm_changed": False,
        "fresh_qualification_required": True,
        "fresh_r0_required": True,
        "accepted_rounds_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


if __name__ == "__main__":
    try:
        print(json.dumps(build(), indent=2, sort_keys=True))
    except (BuildError, OSError, UnicodeError, ValueError, KeyError,
            subprocess.SubprocessError) as exc:
        sys.stderr.write("NOT_READY: " + str(exc) + "\n")
        raise SystemExit(2)

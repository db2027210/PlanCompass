#!/usr/bin/env python3
"""Build the immutable TPC-H 260 MiB/s IO-margin successor.

The source qualification is debug-only and receives zero R0/round credit.
Only the frozen read/write throttle changes; Algorithm C and the workload are
copied byte-for-byte except for their exact resource-envelope bindings.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PARENT_ID = "87ed507ed689c103781db5f96332ae7875e4773d04d69b98a7e63e3d4097b257"
PARENT_MANIFEST_SHA256 = "fe747c7811a6288a4b624f5bab48ace95727069a534e7314ec6e79938ca47e04"
PARENT = ROOT / "artifacts/mysql_tpch_memswap_consistency_successor_v1" / PARENT_ID
OUTPUT_ROOT = ROOT / "artifacts/mysql_tpch_io_margin_successor_v3"
SOURCE = ROOT / "history/mysql_gradient_debug/tpch_solo_v28a_persistent_recovery/A_solo_v28a.evidence.json"
SOURCE_SHA256 = "7fb1fa0fcf608820f7f7a1981aa1a2152cf8720d40fb921018a356c95b5054c9"
OLD_IO = 293_601_280
IO = 272_629_760
CPU = 5.0
MEMORY = 6_174_015_488

FORMAL = "tpch_formal10_contract.json"
RUNTIME = "distributed_runtime_contract.json"
FOUR_SCENE = "contracts/four_scene_v2.json"
PROFILE = "algorithm_source/profiles/tpch_v35_debug_profile.json"
EVIDENCE = "qualification_source/A_solo_v28a.evidence.json"
CALIBRATION = "tpch_io_margin_recalibration_evidence_v2.json"
INVALIDATION = "invalidated_87ed507e_insufficient_io_margin.json"
INTERMEDIATE_INVALIDATION = "invalidated_4314873a_wrong_predecessor.json"
BUILDER = "builder/build_mysql_tpch_io_margin_successor_v3.py"
TEXT_PATHS = (
    "mysql_tpch_distributed_v2_authority.py",
    "tpch_formal10_readiness_v2.py",
    "qualification/mysql_tpch_blkio_v1.py",
    "builder/build_mysql_tpch_socket_identity_successor_v1.py",
    "offline_tests/test_effective_authority_projection_v1.py",
    "offline_tests/test_sealed_bundle_self_contained_v1.py",
    "algorithm_source/run_mysql_gd_matrix.py",
    "algorithm_source/shell/mysql_tpch_algorithm_c_local_services_v27.py",
    "algorithm_source/shell/verify_mysql_tpch_v35_readiness.py",
    "algorithm_source/tests/test_mysql_tpch_v35_authority.py",
)


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(payload: bytes, label: str) -> dict[str, Any]:
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise BuildError(label + " is not an object")
    return value


def relative_files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError("symlink prohibited: " + str(path))
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in (
            "bundle_manifest.json", "bundle_manifest.json.sha256"
        ):
            result[relative] = sha_file(path)
    return result


def verify_parent() -> dict[str, Any]:
    manifest_path = PARENT / "bundle_manifest.json"
    manifest = load(manifest_path.read_bytes(), "parent manifest")
    if (
        manifest.get("bundle_id") != PARENT_ID
        or sha_file(manifest_path) != PARENT_MANIFEST_SHA256
        or relative_files(PARENT) != manifest.get("files")
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("parent identity, closure, scope, or authority differs")
    if sha_file(SOURCE) != SOURCE_SHA256:
        raise BuildError("source qualification identity differs")
    source = load(SOURCE.read_bytes(), "source qualification")
    util = source.get("summary", {}).get("resource_utilization", {})
    if (
        source.get("status")
        != "PASS_COMPLETE_8_STREAM_DEBUG_QUALIFICATION_NOT_GATE_AUTHORITY"
        or source.get("qualification_attempt_id")
        != "tpch-a-solo-v28a-20260802t024300p08-6c3809b8"
        or source.get("duration_seconds") != 180
        or source.get("summary", {}).get("sample_count") != 181
        or util.get("cpu_usage_ratio") != 0.42162398233932985
        or util.get("memory_utilization_ratio") != 0.871846309224774
        or util.get("device_utilization_ratio") != 0.5070831717791171
    ):
        raise BuildError("source qualification semantics differ")
    return manifest


def patch_text(content: dict[str, bytes]) -> None:
    for relative in TEXT_PATHS:
        text = content[relative].decode("utf-8")
        count_plain = text.count(str(OLD_IO))
        count_grouped = text.count("293_601_280")
        if count_plain + count_grouped == 0:
            raise BuildError("missing IO binding in " + relative)
        text = text.replace(str(OLD_IO), str(IO)).replace(
            "293_601_280", "272_629_760"
        )
        content[relative] = text.encode("utf-8")


def patch_json(content: dict[str, bytes]) -> None:
    formal = load(content[FORMAL], FORMAL)
    runtime = load(content[RUNTIME], RUNTIME)
    four = load(content[FOUR_SCENE], FOUR_SCENE)
    profile = load(content[PROFILE], PROFILE)
    envelopes = (
        formal["database_resource_envelope"],
        runtime["resource_envelope"],
        four["scenarios"]["tpch"]["resource_envelope"],
    )
    for envelope in envelopes:
        if (envelope.get("io_read_bps"), envelope.get("io_write_bps")) != (OLD_IO, OLD_IO):
            raise BuildError("JSON IO predecessor differs")
        envelope.update(io_read_bps=IO, io_write_bps=IO)
    authority = runtime["effective_blkio_authority"]
    if (authority.get("read_bps"), authority.get("write_bps")) != (OLD_IO, OLD_IO):
        raise BuildError("runtime blkio predecessor differs")
    authority.update(read_bps=IO, write_bps=IO)
    if (profile.get("read_bps"), profile.get("write_bps")) != (OLD_IO, OLD_IO):
        raise BuildError("profile IO predecessor differs")
    profile.update(read_bps=IO, write_bps=IO)
    content[FORMAL] = canonical(formal)
    content[RUNTIME] = canonical(runtime)
    content[FOUR_SCENE] = canonical(four)
    content[PROFILE] = canonical(profile)


def replace_once(payload: bytes, old: str, new: str, label: str) -> bytes:
    text = payload.decode("utf-8")
    if text.count(old) != 1:
        raise BuildError(label + " contract hash cardinality differs")
    return text.replace(old, new).encode("utf-8")


def calibration() -> dict[str, Any]:
    return {
        "schema_version": "mysql_tpch_io_margin_recalibration_v2",
        "status": "CALIBRATION_ONLY_ZERO_CREDIT_FRESH_R0_REQUIRED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "source_evidence_sha256": SOURCE_SHA256,
        "source_attempt": "tpch-a-solo-v28a-20260802t024300p08-6c3809b8",
        "source_status": "PASS_COMPLETE_8_STREAM_DEBUG_QUALIFICATION_NOT_GATE_AUTHORITY",
        "source_envelope": {"cpu_quota_cores": CPU, "memory_bytes": MEMORY,
                            "io_read_bps": OLD_IO, "io_write_bps": OLD_IO},
        "observed_utilization": {"cpu": 0.42162398233932985,
                                 "memory": 0.871846309224774,
                                 "io": 0.5070831717791171},
        "successor_envelope": {"cpu_quota_cores": CPU, "memory_bytes": MEMORY,
                               "memory_swap_bytes": MEMORY,
                               "io_read_bps": IO, "io_write_bps": IO},
        "projected_io_utilization": 0.5070831717791171 * IO / OLD_IO,
        "target_intervals": {"cpu": [0.30, 0.50], "memory": [0.85, 0.95],
                             "io": [0.30, 0.50]},
        "fresh_qualification_required": True,
        "fresh_r0_required": True,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


def freeze(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def build() -> Path:
    parent = verify_parent()
    content = {relative: (PARENT / relative).read_bytes()
               for relative in sorted(parent["files"])}
    old_four_hash = sha_bytes(content[FOUR_SCENE])
    patch_text(content)
    patch_json(content)
    new_four_hash = sha_bytes(content[FOUR_SCENE])
    for relative in ("mysql_tpch_distributed_v2_authority.py",
                     "tpch_formal10_readiness_v2.py"):
        content[relative] = replace_once(
            content[relative], old_four_hash, new_four_hash, relative
        )
    content[EVIDENCE] = SOURCE.read_bytes()
    content[CALIBRATION] = canonical(calibration())
    content[INVALIDATION] = canonical({
        "schema_version": "mysql_tpch_insufficient_io_margin_invalidation_v2",
        "invalidated_bundle_id": PARENT_ID,
        "reason": "solo debug qualification IO utilization 0.507083 exceeded 0.50 and lacked measurement margin",
        "replacement_io_read_bps": IO,
        "replacement_io_write_bps": IO,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    })
    content[INTERMEDIATE_INVALIDATION] = canonical({
        "schema_version": "mysql_tpch_wrong_predecessor_invalidation_v1",
        "invalidated_bundle_id":
            "4314873ab242617ed25fbe9a3083a96bb087dc12b0d7fa1a9cc1451234b3ad4c",
        "reason": "derived from pre-affinity/pre-memswap 94beaa70 rather than current 87ed507e authority",
        "database_or_client_started": False,
        "workload_started": False,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    })
    content[BUILDER] = HERE.read_bytes()

    reverse = {value: key for key, value in parent["files"].items()}
    rebound: dict[str, str] = {}
    for name, old_hash in parent.get("component_hashes", {}).items():
        relative = reverse.get(old_hash)
        if relative is None:
            raise BuildError("component path absent: " + name)
        rebound[name] = sha_bytes(content[relative])
    formal = load(content[FORMAL], FORMAL)
    formal["qualification_hashes"] = {
        name: value for name, value in rebound.items()
        if name not in ("self_contained_read_only_verifier",
                        "systemd_oneshot_materializer")
    }
    content[FORMAL] = canonical(formal)

    files = {relative: sha_bytes(payload)
             for relative, payload in sorted(content.items())}
    identity = {key: value for key, value in parent.items()
                if key not in ("bundle_id", "files")}
    identity.update({
        "successor_generation": "io_margin_recalibration_v3",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
        "builder_sha256": sha_file(HERE),
        "component_hashes": rebound,
        "formal10_contract_sha256": files[FORMAL],
        "distributed_runtime_contract_sha256": files[RUNTIME],
        "four_scene_v2_contract_sha256": files[FOUR_SCENE],
        "four_scene_contract_sha256": files[FOUR_SCENE],
        "io_margin_recalibration_evidence_sha256": files[CALIBRATION],
        "source_qualification_evidence_sha256": files[EVIDENCE],
        "invalidated_parent_io_margin_sha256": files[INVALIDATION],
        "invalidated_wrong_predecessor_bundle_sha256": files[INTERMEDIATE_INVALIDATION],
        "fresh_r0_envelope_preserved": {
            "cpu_quota_cores": CPU, "memory_bytes": MEMORY,
            "memory_swap_bytes": MEMORY,
            "io_read_bps": IO, "io_write_bps": IO,
            "profile_intervals": {"cpu": [0.30, 0.50],
                                  "memory": [0.85, 0.95],
                                  "io": [0.30, 0.50]},
        },
        "docker_empty_projection_policy": {
            **dict(parent.get("docker_empty_projection_policy") or {}),
            "requires_exact_read_bps": IO,
            "requires_exact_write_bps": IO,
        },
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
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    destination = OUTPUT_ROOT / bundle_id
    if not destination.exists():
        stage = Path(tempfile.mkdtemp(prefix=".tpch-io-margin.", dir=OUTPUT_ROOT))
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
    actual = relative_files(destination)
    unsigned = {key: value for key, value in manifest.items() if key != "bundle_id"}
    if (actual != files or destination.name != bundle_id
            or sha_bytes(canonical(unsigned)) != bundle_id):
        raise BuildError("published successor identity differs")
    for path in (destination, *destination.rglob("*")):
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError("published successor is mutable")
    print(json.dumps({"status": "PASS_IMMUTABLE_IO_MARGIN_SUCCESSOR",
                      "bundle_id": bundle_id, "bundle": str(destination),
                      "verified_files": len(files),
                      "io_read_bps": IO, "io_write_bps": IO,
                      "fresh_r0_required": True,
                      "formal_launch_authorized": False,
                      "accepted_round_authorized": False}, sort_keys=True))
    return destination


if __name__ == "__main__":
    try:
        build()
    except (BuildError, OSError, ValueError, KeyError) as exc:
        raise SystemExit("NOT_READY: " + str(exc))

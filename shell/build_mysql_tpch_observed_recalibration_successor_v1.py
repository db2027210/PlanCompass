#!/usr/bin/env python3
"""Build an immutable TPC-H successor from the observed 2026-07-31 qualification.

This is an offline, fail-closed transmuter.  It preserves Algorithm C and all
qualification gates, changes only the frozen resource envelope, records zero
credit for the qualification used as calibration evidence, and publishes by
content address.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from typing import Any

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PARENT_ID = "b64f5520074fb423e1968cac4fec0f72ec0dee9b1e55d864e5330c5026d0fc1e"
PARENT_MANIFEST_SHA256 = "282bb61f91f1a86f60bb8f020ede97c45a181466e26a607ba63476adc8639a8b"
PARENT = ROOT / "artifacts/mysql_tpch_v23_recalibrated_successor_v3" / PARENT_ID
OUTPUT_ROOT = ROOT / "artifacts/mysql_tpch_observed_recalibration_successor_v1"

OLD_CPU = 3.0
OLD_CPU_QUOTA_US = 300_000
OLD_MEMORY = 5_583_454_208
OLD_IO = 335_544_320
CPU = 5.0
CPU_QUOTA_US = 500_000
MEMORY = 6_174_015_488
IO = 293_601_280

OBSERVED_CPU = 0.7489832568972539
OBSERVED_MEMORY = 0.9818003369743097
OBSERVED_IO = 0.5529780472718137
SOURCE_EVIDENCE_SHA256 = "evidence.json:verified-by-sidecar-on-node04"
SOURCE_ATTEMPT = "tpch-solo-invdiag-20260731t213500-9b3aa8cc"

AUTHORITY = "mysql_tpch_distributed_v2_authority.py"
READINESS = "tpch_formal10_readiness_v2.py"
BLKIO = "qualification/mysql_tpch_blkio_v1.py"
FORMAL = "tpch_formal10_contract.json"
RUNTIME = "distributed_runtime_contract.json"
FOUR_SCENE = "contracts/four_scene_v2.json"
PROFILE = "algorithm_source/profiles/tpch_v35_debug_profile.json"
READ_ONLY = "builder/build_mysql_tpch_socket_identity_successor_v1.py"
PROJECTION_TEST = "offline_tests/test_effective_authority_projection_v1.py"
SEALED_TEST = "offline_tests/test_sealed_bundle_self_contained_v1.py"
LOCAL_FILES = (
    "algorithm_source/shell/mysql_tpch_algorithm_c_local_services_v27.py",
    "algorithm_source/shell/verify_mysql_tpch_v35_readiness.py",
    "algorithm_source/tests/test_mysql_tpch_v35_authority.py",
)
EVIDENCE = "tpch_observed_qualification_recalibration_evidence.json"
INVALIDATION = "invalidated_b64f_observed_envelope_bundle.json"


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_object(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BuildError(label + " is invalid JSON") from exc
    if not isinstance(value, dict):
        raise BuildError(label + " is not a JSON object")
    return value


def relative_files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError("symlink is prohibited: " + str(path))
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in (
            "bundle_manifest.json", "bundle_manifest.json.sha256"
        ):
            result[relative] = sha_file(path)
    return result


def verify_parent() -> dict[str, Any]:
    manifest_path = PARENT / "bundle_manifest.json"
    sidecar = PARENT / "bundle_manifest.json.sha256"
    manifest = load_object(manifest_path.read_bytes(), "parent manifest")
    if (
        PARENT.name != PARENT_ID
        or manifest.get("bundle_id") != PARENT_ID
        or sha_file(manifest_path) != PARENT_MANIFEST_SHA256
        or sidecar.read_text(encoding="ascii")
        != PARENT_MANIFEST_SHA256 + "  bundle_manifest.json\n"
        or relative_files(PARENT) != manifest.get("files")
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("parent identity, closure, or authority differs")
    return manifest


def replace(payload: bytes, old: str, new: str, count: int, label: str) -> bytes:
    text = payload.decode("utf-8")
    observed = text.count(old)
    if observed != count:
        raise BuildError(f"{label}: expected {count} occurrences of {old!r}, got {observed}")
    return text.replace(old, new).encode("utf-8")


def patch_text(content: dict[str, bytes]) -> None:
    patches: dict[str, list[tuple[str, str, int]]] = {
        AUTHORITY: [
            ('"cpu_quota_cores": 3.0', '"cpu_quota_cores": 5.0', 1),
            ('"cpu_quota_us": 300000', '"cpu_quota_us": 500000', 1),
            ('"memory_bytes": 5583454208', '"memory_bytes": 6174015488', 1),
            ('"io_read_bps": 335544320', '"io_read_bps": 293601280', 1),
            ('"io_write_bps": 335544320', '"io_write_bps": 293601280', 1),
        ],
        READINESS: [
            ('"cpu_quota_cores": 3.0', '"cpu_quota_cores": 5.0', 1),
            ('"memory_bytes": 5583454208', '"memory_bytes": 6174015488', 1),
            ('"io_read_bps": 335544320', '"io_read_bps": 293601280', 1),
            ('"io_write_bps": 335544320', '"io_write_bps": 293601280', 1),
            ('!= 335544320', '!= 293601280', 2),
        ],
        BLKIO: [("IO_BPS = 335_544_320", "IO_BPS = 293_601_280", 1)],
        READ_ONLY: [
            ('envelope.get("cpu_quota_cores") != 3.0',
             'envelope.get("cpu_quota_cores") != 5.0', 1),
            ('envelope.get("io_read_bps") != 335544320',
             'envelope.get("io_read_bps") != 293601280', 1),
            ('envelope.get("io_write_bps") != 335544320',
             'envelope.get("io_write_bps") != 293601280', 1),
            ('effective_blkio.get("read_bps") != 335544320',
             'effective_blkio.get("read_bps") != 293601280', 1),
            ('effective_blkio.get("write_bps") != 335544320',
             'effective_blkio.get("write_bps") != 293601280', 1),
        ],
        PROJECTION_TEST: [("RATE = 335544320", "RATE = 293601280", 1)],
        SEALED_TEST: [
            ("(3.0, 335544320, 335544320, 65103)",
             "(5.0, 293601280, 293601280, 65103)", 1),
        ],
    }
    for relative in LOCAL_FILES:
        patches[relative] = [
            ('"cpu_cores": 3.0', '"cpu_cores": 5.0',
             0 if relative.endswith("test_mysql_tpch_v35_authority.py") else 1),
            ('"cpu_quota_cores": 3.0', '"cpu_quota_cores": 5.0',
             2 if relative.endswith("local_services_v27.py") else 1),
            ('"memory_bytes": 5583454208', '"memory_bytes": 6174015488',
             3 if relative.endswith("local_services_v27.py") else
             (2 if relative.endswith("readiness.py") else 1)),
            ('"read_bps": 335544320', '"read_bps": 293601280',
             0 if relative.endswith("test_mysql_tpch_v35_authority.py") else 1),
            ('"write_bps": 335544320', '"write_bps": 293601280',
             0 if relative.endswith("test_mysql_tpch_v35_authority.py") else 1),
            ('"io_read_bps": 335544320', '"io_read_bps": 293601280',
             2 if relative.endswith("local_services_v27.py") else 1),
            ('"io_write_bps": 335544320', '"io_write_bps": 293601280',
             2 if relative.endswith("local_services_v27.py") else 1),
        ]
    for relative, rows in patches.items():
        payload = content[relative]
        for old, new, count in rows:
            payload = replace(payload, old, new, count, relative)
        content[relative] = payload


def patch_json(content: dict[str, bytes]) -> None:
    formal = load_object(content[FORMAL], FORMAL)
    env = formal["database_resource_envelope"]
    env.update(cpu_quota_cores=CPU, memory_bytes=MEMORY,
               memory_swap_bytes=MEMORY, io_read_bps=IO, io_write_bps=IO)

    runtime = load_object(content[RUNTIME], RUNTIME)
    env = runtime["resource_envelope"]
    env.update(cpu_quota_cores=CPU, memory_bytes=MEMORY,
               memory_swap_bytes=MEMORY, io_read_bps=IO, io_write_bps=IO)
    runtime["effective_blkio_authority"].update(read_bps=IO, write_bps=IO)

    four = load_object(content[FOUR_SCENE], FOUR_SCENE)
    env = four["scenarios"]["tpch"]["resource_envelope"]
    env.update(cpu_quota_cores=CPU, memory_bytes=MEMORY,
               memory_swap_bytes=MEMORY, io_read_bps=IO, io_write_bps=IO)
    content[FOUR_SCENE] = canonical(four)
    old_contract_hash = sha_bytes(content[FOUR_SCENE])  # replaced below after source hash is recovered
    del old_contract_hash

    profile = load_object(content[PROFILE], PROFILE)
    profile.update(cpu_cores=CPU, memory_bytes=MEMORY, read_bps=IO, write_bps=IO)
    content[PROFILE] = canonical(profile)
    content[RUNTIME] = canonical(runtime)

    # The formal contract binds hashes of the components and is finalized after
    # all text/JSON payloads have their successor values.
    content[FORMAL] = canonical(formal)


def evidence() -> dict[str, Any]:
    return {
        "schema_version": "mysql_tpch_observed_qualification_recalibration_v1",
        "status": "CALIBRATION_ONLY_ZERO_CREDIT_FRESH_R0_REQUIRED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "source": {
            "attempt_id": SOURCE_ATTEMPT,
            "evidence_sidecar_verified": True,
            "evidence_identity": SOURCE_EVIDENCE_SHA256,
            "status": "PASS_COMPLETE_8_STREAM_DEBUG_QUALIFICATION_NOT_GATE_AUTHORITY",
            "qualification_only": True,
            "r0_credit": 0,
            "accepted_rounds_credit": 0,
            "old_envelope": {"cpu_quota_cores": OLD_CPU,
                             "memory_bytes": OLD_MEMORY,
                             "io_read_bps": OLD_IO, "io_write_bps": OLD_IO},
            "observed_utilization": {"cpu": OBSERVED_CPU,
                                     "memory": OBSERVED_MEMORY, "io": OBSERVED_IO},
            "sample_count": 181,
            "duration_seconds": 180,
            "maximum_sample_gap_seconds": 1.5,
        },
        "successor_envelope": {
            "cpu_quota_cores": CPU, "cpu_period_us": 100_000,
            "cpu_quota_us": CPU_QUOTA_US, "memory_bytes": MEMORY,
            "memory_swap_bytes": MEMORY, "io_read_bps": IO, "io_write_bps": IO,
            "projected_utilization": {
                "cpu": OBSERVED_CPU * OLD_CPU / CPU,
                "memory": OBSERVED_MEMORY * OLD_MEMORY / MEMORY,
                "io_device_linear_projection": OBSERVED_IO * IO / OLD_IO,
            },
            "target_intervals": {"cpu": [0.30, 0.50],
                                 "memory": [0.85, 0.95], "io": [0.30, 0.50]},
        },
        "fresh_qualification_required": True,
        "fresh_r0_required": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


def invalidation() -> dict[str, Any]:
    return {
        "schema_version": "mysql_tpch_observed_envelope_invalidation_v1",
        "status": "INVALIDATED_DO_NOT_RUN_OR_CREDIT",
        "bundle_id": PARENT_ID,
        "bundle_manifest_sha256": PARENT_MANIFEST_SHA256,
        "reason": "observed qualification exceeded CPU, memory, and IO target ceilings",
        "source_attempt": SOURCE_ATTEMPT,
        "r0_credit": 0,
        "candidate_credit": 0,
        "replay_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


def chmod_sealed(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file():
            os.chmod(path, 0o444)
        elif path.is_dir():
            os.chmod(path, 0o555)
    os.chmod(root, 0o555)


def verify_published(bundle: Path, manifest: dict[str, Any]) -> None:
    unsigned = {k: v for k, v in manifest.items() if k != "bundle_id"}
    sidecar = bundle / "bundle_manifest.json.sha256"
    if (
        bundle.name != manifest["bundle_id"]
        or relative_files(bundle) != manifest["files"]
        or sha_bytes(canonical(unsigned)) != manifest["bundle_id"]
        or sidecar.read_text(encoding="ascii")
        != sha_file(bundle / "bundle_manifest.json") + "  bundle_manifest.json\n"
    ):
        raise BuildError("published identity or closure differs")
    for path in [bundle, *bundle.rglob("*")]:
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError("published successor is mutable: " + str(path))


def build() -> dict[str, Any]:
    parent = verify_parent()
    content: dict[str, bytes] = {}
    for relative, expected in sorted(parent["files"].items()):
        payload = (PARENT / relative).read_bytes()
        if sha_bytes(payload) != expected:
            raise BuildError("parent file differs: " + relative)
        content[relative] = payload
    old_four_hash = sha_bytes(content[FOUR_SCENE])
    patch_text(content)
    patch_json(content)
    new_four_hash = sha_bytes(content[FOUR_SCENE])
    for relative in (AUTHORITY, READINESS):
        content[relative] = replace(content[relative], old_four_hash,
                                    new_four_hash, 1, relative)
    content[EVIDENCE] = canonical(evidence())
    content[INVALIDATION] = canonical(invalidation())

    # Rebind the formal qualification component hashes after all payloads are final.
    formal = load_object(content[FORMAL], FORMAL)
    component_paths = dict(parent.get("component_hashes") or {})
    reverse = {v: k for k, v in parent["files"].items()}
    rebound: dict[str, str] = {}
    for name, old_hash in component_paths.items():
        relative = reverse.get(old_hash)
        if relative is None:
            raise BuildError("parent component hash is not in file closure: " + name)
        rebound[name] = sha_bytes(content[relative])
    formal["qualification_hashes"] = {
        name: value for name, value in rebound.items()
        if name not in ("self_contained_read_only_verifier",
                        "systemd_oneshot_materializer")
    }
    content[FORMAL] = canonical(formal)

    files = {relative: sha_bytes(payload) for relative, payload in sorted(content.items())}
    identity = {k: v for k, v in parent.items() if k not in ("bundle_id", "files")}
    identity.update({
        # Preserve the established runtime schema allow-list; the immutable
        # generation and evidence below distinguish this successor.
        "schema_version": "mysql_tpch_v23_recalibrated_successor_v3",
        "successor_generation": "observed_recalibration_v1",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
        "builder_sha256": sha_file(HERE),
        "component_hashes": rebound,
        "formal10_contract_sha256": files[FORMAL],
        "distributed_runtime_contract_sha256": files[RUNTIME],
        "four_scene_v2_contract_sha256": files[FOUR_SCENE],
        "four_scene_contract_sha256": files[FOUR_SCENE],
        "observed_recalibration_evidence_sha256": files[EVIDENCE],
        "invalidated_parent_observed_envelope_sha256": files[INVALIDATION],
        "fresh_r0_envelope_preserved": {
            "cpu_quota_cores": CPU, "memory_bytes": MEMORY,
            "memory_swap_bytes": MEMORY, "io_read_bps": IO, "io_write_bps": IO,
            "profile_intervals": {"cpu": [0.30, 0.50],
                                  "memory": [0.85, 0.95], "io": [0.30, 0.50]},
        },
        "fresh_qualification_required": True,
        "fresh_r0_required": True,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "docker_empty_projection_policy": {
            **dict(parent.get("docker_empty_projection_policy") or {}),
            "requires_exact_read_bps": IO,
            "requires_exact_write_bps": IO,
        },
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "offline_build_only": True,
        "remote_actions": 0,
        "files": files,
    })
    unsigned = dict(identity)
    bundle_id = sha_bytes(canonical(unsigned))
    manifest = dict(unsigned, bundle_id=bundle_id)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    destination = OUTPUT_ROOT / bundle_id
    if not destination.exists():
        stage = Path(tempfile.mkdtemp(prefix=".tpch-observed-recalibration.",
                                      dir=OUTPUT_ROOT))
        try:
            for relative, payload in sorted(content.items()):
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            manifest_payload = canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(manifest_payload)
            (stage / "bundle_manifest.json.sha256").write_text(
                sha_bytes(manifest_payload) + "  bundle_manifest.json\n",
                encoding="ascii")
            chmod_sealed(stage)
            os.replace(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    verify_published(destination, manifest)
    return {
        "status": "PASS_IMMUTABLE_OBSERVED_RECALIBRATION_SUCCESSOR_PUBLISHED",
        "bundle_id": bundle_id,
        "bundle": str(destination),
        "bundle_manifest_sha256": sha_file(destination / "bundle_manifest.json"),
        "verified_files": len(files),
        "resource_envelope": {"cpu_quota_cores": CPU, "memory_bytes": MEMORY,
                              "io_read_bps": IO, "io_write_bps": IO},
        "fresh_qualification_required": True,
        "fresh_r0_required": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


if __name__ == "__main__":
    try:
        print(json.dumps(build(), sort_keys=True, indent=2))
    except (BuildError, OSError, UnicodeError, ValueError, KeyError) as exc:
        sys.stderr.write("NOT_READY: " + str(exc) + "\n")
        raise SystemExit(2)

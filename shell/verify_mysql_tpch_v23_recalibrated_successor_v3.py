#!/usr/bin/env python3
"""Strict offline verifier for the sealed TPC-H v23-recalibrated successor."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import sys
from typing import Any


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ID = "b64f5520074fb423e1968cac4fec0f72ec0dee9b1e55d864e5330c5026d0fc1e"
EXPECTED_MANIFEST_SHA256 = (
    "282bb61f91f1a86f60bb8f020ede97c45a181466e26a607ba63476adc8639a8b"
)
DEFAULT_BUNDLE = (
    ROOT / "artifacts/mysql_tpch_v23_recalibrated_successor_v3" / EXPECTED_ID
)
EXPECTED_ENVELOPE = {
    "cpu_quota_cores": 3.0,
    "io_read_bps": 335_544_320,
    "io_write_bps": 335_544_320,
}
COMPONENT_PATHS = {
    "blkio_v1_helper": "qualification/mysql_tpch_blkio_v1.py",
    "bootstrap_authority": "mysql_tpch_distributed_v2_authority.py",
    "client_identity_helper_qualification":
        "qualification/mysql_tpch_client_immutable_identity_v1.py",
    "client_identity_helper_root": "mysql_tpch_client_immutable_identity_v1.py",
    "client_mysql_datadir_readonly_mask":
        "qualification/client_volume_masks/mysql_datadir/mask_identity.json",
    "collector":
        "qualification/mysql_distributed_live_readback_qualification_v1.py",
    "fresh_r0_runner": "qualification/mysql_tpch_v2_fresh_r0_runner_v1.py",
    "metric_adapter":
        "qualification/mysql_distributed_workload_metric_adapter_v1.py",
    "qualification_authority_renderer":
        "qualification/mysql_tpch_v2_qualification_authority_v1.py",
    "readiness_verifier": "tpch_formal10_readiness_v2.py",
    "runtime_helper": "qualification/runtime_tree_content_identity_v3.py",
    "self_contained_read_only_verifier":
        "builder/build_mysql_tpch_socket_identity_successor_v1.py",
    "sql_adapter": "qualification/mysql_sql_debug_metric_adapter_v1.py",
    "systemd_oneshot_materializer":
        "launch/mysql_tpch_systemd_oneshot_materializer_v1.py",
}


class VerifyError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise VerifyError("absent or symlinked: " + str(path))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VerifyError("invalid JSON: " + str(path)) from exc
    if not isinstance(value, dict):
        raise VerifyError("JSON object required: " + str(path))
    return value


def exact_files(bundle: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(bundle.rglob("*")):
        relative = path.relative_to(bundle).as_posix()
        if path.is_symlink():
            raise VerifyError("symlink in closure: " + relative)
        if path.is_file():
            if path in (
                bundle / "bundle_manifest.json",
                bundle / "bundle_manifest.json.sha256",
            ):
                continue
            if "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
                raise VerifyError("bytecode/cache in closure: " + relative)
            result[relative] = sha256(path)
        elif not path.is_dir():
            raise VerifyError("special file in closure: " + relative)
    return result


def subset_envelope(value: dict[str, Any]) -> dict[str, Any]:
    return {key: value.get(key) for key in EXPECTED_ENVELOPE}


def verify(bundle: Path) -> dict[str, Any]:
    bundle = bundle.resolve(strict=True)
    manifest_path = bundle / "bundle_manifest.json"
    sidecar = bundle / "bundle_manifest.json.sha256"
    manifest = load(manifest_path)
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    manifest_sha = sha256(manifest_path)
    if (
        bundle.name != EXPECTED_ID
        or bundle_id != EXPECTED_ID
        or manifest_sha != EXPECTED_MANIFEST_SHA256
        or hashlib.sha256(canonical(unsigned)).hexdigest() != EXPECTED_ID
        or sidecar.is_symlink()
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii")
        != manifest_sha + "  bundle_manifest.json\n"
        or exact_files(bundle) != manifest.get("files")
    ):
        raise VerifyError("identity, sidecar, or exact closure differs")
    if (
        manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("postgresql_mutation_prohibited") is not True
        or manifest.get("fresh_r0_required") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or manifest.get("v23_failed_r0_credit") != 0
        or manifest.get("parent_bundle_credit") != 0
    ):
        raise VerifyError("scope or fail-closed authority differs")

    formal = load(bundle / "tpch_formal10_contract.json")
    runtime = load(bundle / "distributed_runtime_contract.json")
    four_scene = load(bundle / "contracts/four_scene_v2.json")
    recalibration = load(bundle / "tpch_v23_fresh_r0_recalibration_evidence.json")
    b1ff_invalidation = load(
        bundle / "invalidated_b1ff_stale_resource_envelope_bundle.json"
    )
    a0c_invalidation = load(
        bundle / "invalidated_a0c_stale_four_scene_hash_binding_bundle.json"
    )
    formal_envelope = subset_envelope(formal["database_resource_envelope"])
    runtime_envelope = subset_envelope(runtime["resource_envelope"])
    four_scene_envelope = subset_envelope(
        four_scene["scenarios"]["tpch"]["resource_envelope"]
    )
    manifest_envelope = subset_envelope(
        manifest["fresh_r0_envelope_preserved"]
    )
    recalibrated_envelope = subset_envelope(
        recalibration["successor_envelope"]
    )
    if not all(
        value == EXPECTED_ENVELOPE
        for value in (
            formal_envelope,
            runtime_envelope,
            four_scene_envelope,
            manifest_envelope,
            recalibrated_envelope,
        )
    ):
        raise VerifyError("formal/runtime/four-scene/recalibration envelope split")
    if runtime.get("effective_blkio_authority", {}).get("read_bps") != 335_544_320:
        raise VerifyError("effective read throttle differs")
    if runtime.get("effective_blkio_authority", {}).get("write_bps") != 335_544_320:
        raise VerifyError("effective write throttle differs")

    four_scene_sha = sha256(bundle / "contracts/four_scene_v2.json")
    authority = (bundle / "mysql_tpch_distributed_v2_authority.py").read_text(
        encoding="utf-8"
    )
    readiness = (bundle / "tpch_formal10_readiness_v2.py").read_text(
        encoding="utf-8"
    )
    required_authority_tokens = (
        '"cpu_quota_cores": 3.0,',
        '"cpu_quota_us": 300000,',
        '"io_read_bps": 335544320,',
        '"io_write_bps": 335544320,',
        four_scene_sha,
    )
    if any(token not in authority for token in required_authority_tokens):
        raise VerifyError("launch/readback authority is not bound to new envelope")
    if any(token not in readiness for token in (
        '"cpu_quota_cores": 3.0,',
        '"io_read_bps": 335544320,',
        '"io_write_bps": 335544320,',
        four_scene_sha,
    )):
        raise VerifyError("formal readiness is not bound to new envelope")
    if (
        manifest.get("four_scene_contract_sha256") != four_scene_sha
        or manifest.get("four_scene_v2_contract_sha256") != four_scene_sha
        or manifest.get("formal10_contract_sha256")
        != sha256(bundle / "tpch_formal10_contract.json")
        or manifest.get("distributed_runtime_contract_sha256")
        != sha256(bundle / "distributed_runtime_contract.json")
    ):
        raise VerifyError("contract hashes differ")

    components = {
        name: sha256(bundle / relative)
        for name, relative in sorted(COMPONENT_PATHS.items())
    }
    if components != manifest.get("component_hashes"):
        raise VerifyError("component hash rebinding differs")
    expected_qualification = {
        name: value
        for name, value in components.items()
        if name not in (
            "self_contained_read_only_verifier",
            "systemd_oneshot_materializer",
        )
    }
    if formal.get("qualification_hashes") != expected_qualification:
        raise VerifyError("formal qualification hash rebinding differs")
    if not all(
        value.get("status") == "INVALIDATED_DO_NOT_RUN_OR_CREDIT"
        and value.get("accepted_rounds_credit") == 0
        for value in (b1ff_invalidation, a0c_invalidation)
    ):
        raise VerifyError("predecessor/intermediate invalidation differs")
    if (
        recalibration.get("source_attempt", {}).get("terminal_status")
        != "R0_FAILED"
        or recalibration.get("source_attempt", {}).get("valid_r0") is not False
        or recalibration.get("fresh_r0_required") is not True
    ):
        raise VerifyError("v23 failed-R0 treatment differs")

    for path in [bundle, *bundle.rglob("*")]:
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise VerifyError("sealed immutable mode differs: " + str(path))
    return {
        "schema_version": "mysql_tpch_v23_recalibrated_successor_verify_v3",
        "status": "PASS_STRICT_RECALIBRATED_SUCCESSOR",
        "bundle_id": bundle_id,
        "bundle_manifest_sha256": manifest_sha,
        "verified_manifest_files": len(manifest["files"]),
        "resource_envelope": EXPECTED_ENVELOPE,
        "cross_file_envelope_match": True,
        "component_hash_rebinding_match": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    args = parser.parse_args()
    print(json.dumps(verify(args.bundle), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (VerifyError, OSError, UnicodeError, ValueError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)

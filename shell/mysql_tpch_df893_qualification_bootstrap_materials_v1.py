#!/usr/bin/env python3
"""Seal one local-only, short-lived TPC-H df893 qualification material set.

This utility performs no network, Docker, database, or workload operation.  It
does not authorize R0, formal replay, or accepted rounds.  The material set is
bound to the immutable df893 successor and is intentionally separate from the
invalidated 0ec89 history.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import render_mysql_tpch_distributed_v2_bootstrap_spec_blkio_v2 as renderer


ROOT = HERE.parent
BUNDLE_ID = "df8939b1d7e22d22d72c3c3e6cf54434bee2c71959e8a28446d847d5b221520d"
MANIFEST_SHA256 = "fb52d46eb2080baa7b7d7637f21d3a60b0de646f54d336950f0d34c2ab60c7ab"
DEFAULT_BUNDLE = ROOT / "artifacts/mysql_tpch_formal10_successor_v2" / BUNDLE_ID
DEFAULT_OUTPUT_ROOT = ROOT / "artifacts/mysql_tpch_df893_qualification_bootstrap_materials_v1"
MYSQL_REFERENCE = "mysql@sha256:a3dff78d876222746a0bacc36dd7e4bf9e673c85fb7ee0d12ed25bd32c43c19b"
MYSQL_IMAGE_ID = "sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
VALID_MINUTES = 25

EXPECTED_RUNTIME = {
    "cpu_quota_cores": 4,
    "io_read_bps": 100663296,
    "io_write_bps": 100663296,
}
EXPECTED_STORAGE = {
    "device": "/dev/nvme0n1",
    "expected_live_major_minor": "259:1",
    "full_physical_copy_required": True,
    "overlay_or_reflink_prohibited": True,
}
EXPECTED_TMPFS = {
    "capacity_bytes": 2147483648,
    "client_subdirectory": "client_measurement",
    "failure_cleanup_and_readback_evidence_required": True,
    "gid": 1008,
    "implementation": "host_root_attempt_private_exact_tmpfs_mount_v1",
    "mode": "0700",
    "mount_options_required": ["rw", "nosuid", "nodev", "noexec"],
    "uid": 1008,
}


class MaterialError(RuntimeError):
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


def write_new(path: Path, payload: bytes, mode: int = 0o444) -> None:
    sidecar = Path(str(path) + ".sha256")
    if path.exists() or path.is_symlink() or sidecar.exists() or sidecar.is_symlink():
        raise MaterialError("refusing to overwrite sealed material: " + path.name)
    temporary = path.with_name(".%s.%s.tmp" % (path.name, secrets.token_hex(8)))
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sidecar.write_text("%s  %s\n" % (hashlib.sha256(payload).hexdigest(), path.name),
                       encoding="ascii")
    os.chmod(path, mode)
    os.chmod(sidecar, mode)


def _load_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise MaterialError("required bundle file is absent or symlinked: " + path.name)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MaterialError("invalid JSON: " + path.name) from exc
    if not isinstance(value, dict):
        raise MaterialError("JSON object required: " + path.name)
    return value


def verify_bundle(bundle: Path) -> dict[str, Any]:
    bundle = bundle.resolve(strict=True)
    manifest_path = bundle / "bundle_manifest.json"
    sidecar = Path(str(manifest_path) + ".sha256")
    if (bundle.name != BUNDLE_ID or bundle.is_symlink() or manifest_path.is_symlink()
            or sidecar.is_symlink() or not manifest_path.is_file() or not sidecar.is_file()
            or sha256(manifest_path) != MANIFEST_SHA256
            or sidecar.read_text(encoding="ascii")
            != "%s  %s\n" % (MANIFEST_SHA256, manifest_path.name)):
        raise MaterialError("df893 bundle manifest binding differs")
    manifest = _load_json(manifest_path)
    if (manifest.get("bundle_id") != BUNDLE_ID
            or manifest.get("formal_scope") != "mysql_only"
            or manifest.get("cross_engine_comparison_prohibited") is not True
            or manifest.get("postgresql_mutation_prohibited") is not True
            or manifest.get("formal_launch_authorized") is not False
            or manifest.get("accepted_round_authorized") is not False):
        raise MaterialError("df893 scope or authorization boundary differs")
    unsigned = dict(manifest)
    identity = unsigned.pop("bundle_id", None)
    if hashlib.sha256(canonical(unsigned)).hexdigest() != identity:
        raise MaterialError("df893 content-addressed identity differs")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise MaterialError("df893 manifest file closure differs")
    required = (
        "distributed_runtime_contract.json",
        "mysql_tpch_distributed_v2_authority.py",
        "qualification/mysql_distributed_live_readback_qualification_v1.py",
        "qualification/mysql_tpch_blkio_v1.py",
        "invalidated_0ec89_blkio_projection_bundle.json",
        "tpch_formal10_contract.json",
    )
    for relative in required:
        file_path = bundle / relative
        if file_path.is_symlink() or not file_path.is_file() or files.get(relative) != sha256(file_path):
            raise MaterialError("df893 manifest closure differs: " + relative)
    runtime = _load_json(bundle / "distributed_runtime_contract.json")
    if ({key: (runtime.get("resource_envelope") or {}).get(key)
         for key in EXPECTED_RUNTIME} != EXPECTED_RUNTIME
            or {key: (runtime.get("storage") or {}).get(key) for key in EXPECTED_STORAGE}
            != EXPECTED_STORAGE
            or (runtime.get("host_measurement_authority") or {}) != EXPECTED_TMPFS):
        raise MaterialError("df893 CPU, whole-NVMe, or private tmpfs contract differs")
    invalidation = _load_json(bundle / "invalidated_0ec89_blkio_projection_bundle.json")
    if (invalidation.get("bundle_id") != "0ec89ddaeee61cb073bfe2119ad19106a3df2674fa8a23fe0fbd9232eff0e7b4"
            or invalidation.get("accepted_rounds_credit") != 0
            or invalidation.get("formal_r0_authorized") is not False
            or invalidation.get("replay_or_rerun_authorized") is not False):
        raise MaterialError("0ec89 invalidation must remain zero-credit")
    return manifest


def verify_materials(directory: Path) -> dict[str, Any]:
    directory = directory.resolve(strict=True)
    manifest_path = directory / "materials_manifest.json"
    materials = _load_json(manifest_path)
    expected_files = materials.get("files")
    if not isinstance(expected_files, dict):
        raise MaterialError("materials file closure differs")
    for name, expected_sha in expected_files.items():
        item = directory / name
        if item.is_symlink() or not item.is_file() or sha256(item) != expected_sha:
            raise MaterialError("materials file digest differs: " + name)
    token_path = directory / "main_agent_token"
    if stat.S_IMODE(token_path.stat().st_mode) != 0o600:
        raise MaterialError("main-agent token mode differs")
    spec = _load_json(directory / "bootstrap_spec.json")
    attempt = materials.get("attempt_token")
    if (materials.get("status") != "SEALED_QUALIFICATION_ONLY_NOT_EXECUTED"
            or materials.get("bundle_id") != BUNDLE_ID
            or materials.get("bundle_manifest_sha256") != MANIFEST_SHA256
            or spec.get("attempt_token") != attempt
            or spec.get("bundle", {}).get("bundle_id") != BUNDLE_ID
            or spec.get("bundle", {}).get("manifest_sha256") != MANIFEST_SHA256
            or spec.get("formal_scope") != "mysql_only"
            or spec.get("postgresql_mutation_prohibited") is not True
            or spec.get("qualification_only") is not True
            or any(spec.get(flag) is not False for flag in
                   ("formal_r0_authorized", "formal_authorized", "accepted_round_authorized"))
            or spec.get("paths", {}).get("host_measurement_tmpfs")
            != "/dev/shm/ra_mysql_tpch_v2/" + str(attempt)):
        raise MaterialError("sealed material authority boundary differs")
    now = datetime.now(timezone.utc)
    try:
        expires = datetime.fromisoformat(str(spec["expires_at_utc"]).replace("Z", "+00:00"))
    except (KeyError, ValueError) as exc:
        raise MaterialError("materials expiry differs") from exc
    if expires.tzinfo is None or expires.astimezone(timezone.utc) <= now:
        raise MaterialError("materials are stale")
    return {"status": "PASS_VERIFIED_UNEXPIRED", "attempt_token": attempt,
            "expires_at_utc": spec["expires_at_utc"], "materials_directory": str(directory)}


def create(bundle: Path, output_root: Path) -> dict[str, Any]:
    verify_bundle(bundle)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    attempt = "tpch-v2-" + now.strftime("%Y%m%dt%H%M%Sz").lower() + "-" + secrets.token_hex(12)
    if renderer.ATTEMPT_RE.fullmatch(attempt) is None:
        raise MaterialError("generated attempt token differs")
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    final_output = output_root / attempt
    if final_output.exists() or final_output.is_symlink():
        raise MaterialError("attempt output already exists")
    output = output_root / ("." + attempt + "." + secrets.token_hex(8) + ".tmp")
    output.mkdir(mode=0o700)
    token_path = output / "main_agent_token"
    descriptor = os.open(str(token_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, ("tpch-v2-main-agent-" + secrets.token_urlsafe(36) + "\n").encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    spec_path = output / "bootstrap_spec.json"
    args = argparse.Namespace(bundle_manifest=bundle / "bundle_manifest.json",
        mysql_image_reference=MYSQL_REFERENCE, mysql_image_id=MYSQL_IMAGE_ID,
        client_image_reference=MYSQL_REFERENCE, client_image_id=MYSQL_IMAGE_ID,
        main_agent_token_file=token_path, attempt_token=attempt,
        valid_minutes=VALID_MINUTES, output=spec_path)
    spec = renderer.render(args)
    renderer.atomic_new(spec_path, renderer.canonical(spec))
    renderer.atomic_new(Path(str(spec_path) + ".sha256"),
        (hashlib.sha256(renderer.canonical(spec)).hexdigest() + "  " + spec_path.name + "\n").encode("ascii"))
    plan = {
        "schema_version": "mysql_tpch_df893_qualification_only_command_plan_v1",
        "status": "SEALED_PLAN_NOT_EXECUTED", "bundle_id": BUNDLE_ID,
        "bundle_manifest_sha256": MANIFEST_SHA256, "attempt_token": attempt,
        "bootstrap_spec_sha256": sha256(spec_path),
        "frozen_envelope": {**EXPECTED_RUNTIME, **EXPECTED_STORAGE,
            "host_measurement_tmpfs_base": "/dev/shm/ra_mysql_tpch_v2",
            "host_measurement_authority": EXPECTED_TMPFS},
        "qualification_only": True, "formal_r0_authorized": False,
        "formal_authorized": False, "accepted_round_authorized": False,
    }
    plan_path = output / "command_plan.json"
    write_new(plan_path, canonical(plan))
    materials = {
        "schema_version": "mysql_tpch_df893_bootstrap_materials_v1",
        "status": "SEALED_QUALIFICATION_ONLY_NOT_EXECUTED", "bundle_id": BUNDLE_ID,
        "bundle_manifest_sha256": MANIFEST_SHA256, "attempt_token": attempt,
        "created_at_utc": spec["created_at_utc"], "expires_at_utc": spec["expires_at_utc"],
        "valid_minutes": VALID_MINUTES,
        "files": {"bootstrap_spec.json": sha256(spec_path),
                  "command_plan.json": sha256(plan_path),
                  "main_agent_token": sha256(token_path)},
        "main_agent_token_mode": "0600", "qualification_only": True,
        "formal_r0_authorized": False, "formal_authorized": False,
        "accepted_round_authorized": False,
    }
    write_new(output / "materials_manifest.json", canonical(materials))
    verify_materials(output)
    os.rename(output, final_output)
    descriptor = os.open(str(output_root), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return verify_materials(final_output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--verify-materials", type=Path)
    args = parser.parse_args()
    result = verify_materials(args.verify_materials) if args.verify_materials else create(args.bundle, args.output_root)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (MaterialError, OSError, ValueError, renderer.RenderError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)

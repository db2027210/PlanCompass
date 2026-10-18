#!/usr/bin/env python3
"""Seal short-lived qualification materials for the immutable 295f bundle."""

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
import render_mysql_tpch_effective_authority_bootstrap_spec_v1 as renderer


ROOT = HERE.parent
BUNDLE_ID = renderer.BUNDLE_ID
MANIFEST_SHA256 = renderer.MANIFEST_SHA256
DEFAULT_BUNDLE = renderer.DEFAULT_BUNDLE_MANIFEST.parent
DEFAULT_OUTPUT_ROOT = (
    ROOT / "artifacts/mysql_tpch_effective_authority_materials_v1"
)
MYSQL_REFERENCE = (
    "mysql@sha256:a3dff78d876222746a0bacc36dd7e4bf9e673c85fb7ee0d12ed25bd32c43c19b"
)
MYSQL_IMAGE_ID = (
    "sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
)
VALID_MINUTES = 25


class MaterialError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return renderer.canonical(value)


def sha256(path: Path) -> str:
    return renderer.sha256(path)


def load_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise MaterialError("material file is absent or symlinked: " + path.name)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MaterialError("material JSON is invalid: " + path.name) from exc
    if not isinstance(value, dict):
        raise MaterialError("material JSON object required: " + path.name)
    return value


def write_new(path: Path, payload: bytes, mode: int = 0o444) -> None:
    sidecar = Path(str(path) + ".sha256")
    if path.exists() or path.is_symlink() or sidecar.exists() or sidecar.is_symlink():
        raise MaterialError("refusing to overwrite sealed material: " + path.name)
    temporary = path.with_name("." + path.name + "." + secrets.token_hex(8) + ".tmp")
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.rename(temporary, path)
    sidecar.write_text(
        hashlib.sha256(payload).hexdigest() + "  " + path.name + "\n",
        encoding="ascii",
    )
    os.chmod(path, mode)
    os.chmod(sidecar, mode)


def verify_materials(directory: Path) -> dict[str, Any]:
    directory = directory.resolve(strict=True)
    materials = load_object(directory / "materials_manifest.json")
    files = materials.get("files")
    if not isinstance(files, dict):
        raise MaterialError("materials closure differs")
    for name, expected in files.items():
        path = directory / name
        sidecar = Path(str(path) + ".sha256")
        if (
            path.is_symlink()
            or not path.is_file()
            or sha256(path) != expected
            or sidecar.is_symlink()
            or not sidecar.is_file()
            or sidecar.read_text(encoding="ascii")
            != expected + "  " + path.name + "\n"
        ):
            raise MaterialError("sealed material digest differs: " + name)
    token = directory / "main_agent_token"
    spec = load_object(directory / "bootstrap_spec.json")
    plan = load_object(directory / "command_plan.json")
    attempt = materials.get("attempt_token")
    if (
        materials.get("schema_version")
        != "mysql_tpch_effective_authority_materials_v1"
        or materials.get("status") != "SEALED_QUALIFICATION_ONLY_NOT_EXECUTED"
        or materials.get("bundle_id") != BUNDLE_ID
        or materials.get("bundle_manifest_sha256") != MANIFEST_SHA256
        or stat.S_IMODE(token.stat().st_mode) != 0o600
        or spec.get("attempt_token") != attempt
        or spec.get("bundle", {}).get("bundle_id") != BUNDLE_ID
        or spec.get("bundle", {}).get("manifest_sha256") != MANIFEST_SHA256
        or plan.get("effective_authority_first_empty_projection") is not True
        or plan.get("qualification_only") is not True
        or any(
            document.get(flag) is not False
            for document in (materials, spec, plan)
            for flag in (
                "formal_r0_authorized",
                "formal_authorized",
                "accepted_round_authorized",
            )
        )
    ):
        raise MaterialError("materials authority boundary differs")
    try:
        expires = datetime.fromisoformat(
            str(materials["expires_at_utc"]).replace("Z", "+00:00")
        )
    except (KeyError, ValueError) as exc:
        raise MaterialError("materials expiry differs") from exc
    if expires.tzinfo is None or expires.astimezone(timezone.utc) <= datetime.now(
        timezone.utc
    ):
        raise MaterialError("materials are stale")
    return {
        "status": "PASS_VERIFIED_UNEXPIRED",
        "bundle_id": BUNDLE_ID,
        "attempt_token": attempt,
        "expires_at_utc": materials["expires_at_utc"],
        "materials_directory": str(directory),
        "formal_r0_authorized": False,
        "accepted_round_authorized": False,
    }


def create(bundle: Path, output_root: Path) -> dict[str, Any]:
    bundle = bundle.resolve(strict=True)
    if bundle != DEFAULT_BUNDLE.resolve(strict=True):
        raise MaterialError("materials generator is bound only to 295f")
    renderer.verify_successor(bundle / "bundle_manifest.json")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    attempt = (
        "tpch-v2-"
        + now.strftime("%Y%m%dt%H%M%Sz").lower()
        + "-"
        + secrets.token_hex(12)
    )
    if renderer.ATTEMPT_RE.fullmatch(attempt) is None:
        raise MaterialError("generated attempt token differs")
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    final = output_root / attempt
    if final.exists() or final.is_symlink():
        raise MaterialError("attempt output already exists")
    stage = output_root / ("." + attempt + "." + secrets.token_hex(8) + ".tmp")
    stage.mkdir(mode=0o700)
    try:
        token = stage / "main_agent_token"
        descriptor = os.open(
            token, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        try:
            os.write(
                descriptor,
                (
                    "tpch-v2-main-agent-" + secrets.token_urlsafe(36) + "\n"
                ).encode("ascii"),
            )
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        spec_path = stage / "bootstrap_spec.json"
        args = argparse.Namespace(
            bundle_manifest=bundle / "bundle_manifest.json",
            mysql_image_reference=MYSQL_REFERENCE,
            mysql_image_id=MYSQL_IMAGE_ID,
            client_image_reference=MYSQL_REFERENCE,
            client_image_id=MYSQL_IMAGE_ID,
            main_agent_token_file=token,
            attempt_token=attempt,
            valid_minutes=VALID_MINUTES,
            output=spec_path,
        )
        spec = renderer.render(args)
        write_new(spec_path, canonical(spec))
        plan = {
            "schema_version":
                "mysql_tpch_effective_authority_qualification_plan_v1",
            "status": "SEALED_PLAN_NOT_EXECUTED",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "postgresql_mutation_prohibited": True,
            "bundle_id": BUNDLE_ID,
            "bundle_manifest_sha256": MANIFEST_SHA256,
            "attempt_token": attempt,
            "bootstrap_spec_sha256": sha256(spec_path),
            "effective_authority_first_empty_projection": True,
            "frozen_envelope": {
                "cpu_quota_cores": 4,
                "device": "/dev/nvme0n1",
                "expected_live_major_minor": "259:1",
                "io_read_bps": 100663296,
                "io_write_bps": 100663296,
                "full_physical_copy_required": True,
                "overlay_or_reflink_prohibited": True,
                "empty_docker_projection_requires_exact_same_live_authority":
                    True,
            },
            "qualification_only": True,
            "formal_r0_authorized": False,
            "formal_authorized": False,
            "accepted_round_authorized": False,
        }
        plan_path = stage / "command_plan.json"
        write_new(plan_path, canonical(plan))
        files = {
            "bootstrap_spec.json": sha256(spec_path),
            "command_plan.json": sha256(plan_path),
            "main_agent_token": sha256(token),
        }
        # Token gets a sidecar as part of the exact material closure.
        token_sidecar = Path(str(token) + ".sha256")
        token_sidecar.write_text(
            files["main_agent_token"] + "  main_agent_token\n", encoding="ascii"
        )
        os.chmod(token_sidecar, 0o444)
        materials = {
            "schema_version": "mysql_tpch_effective_authority_materials_v1",
            "status": "SEALED_QUALIFICATION_ONLY_NOT_EXECUTED",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "postgresql_mutation_prohibited": True,
            "bundle_id": BUNDLE_ID,
            "bundle_manifest_sha256": MANIFEST_SHA256,
            "attempt_token": attempt,
            "created_at_utc": spec["created_at_utc"],
            "expires_at_utc": spec["expires_at_utc"],
            "valid_minutes": VALID_MINUTES,
            "files": files,
            "main_agent_token_mode": "0600",
            "qualification_only": True,
            "formal_r0_authorized": False,
            "formal_authorized": False,
            "accepted_round_authorized": False,
        }
        write_new(stage / "materials_manifest.json", canonical(materials))
        verify_materials(stage)
        os.rename(stage, final)
        descriptor = os.open(output_root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return verify_materials(final)
    except BaseException:
        if stage.exists():
            for path in stage.rglob("*"):
                if path.is_file():
                    os.chmod(path, 0o600)
            for path in sorted(stage.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            stage.rmdir()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--verify-materials", type=Path)
    args = parser.parse_args()
    result = (
        verify_materials(args.verify_materials)
        if args.verify_materials
        else create(args.bundle, args.output_root)
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (MaterialError, OSError, ValueError, renderer.RenderError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)

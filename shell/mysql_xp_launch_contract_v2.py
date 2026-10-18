#!/usr/bin/env python3
"""Immutable XP launch contract for capture, fresh R0, and formal outer."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
from typing import Any, Mapping


BUNDLE = Path(__file__).resolve().parent
DRIVERS = {
    "capture": BUNDLE / "capture/mysql_xp_local_capture_entry.py",
    "formal_r0": BUNDLE / "campaign/mysql_xp_formal_r0_entry.py",
    "formal_outer": BUNDLE / "driver/formal_mysql_sgd_campaign.py",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def local_file(path: Path) -> Path:
    if path.is_symlink():
        raise RuntimeError(f"regular local file required (symlink rejected): {path}")
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise RuntimeError(f"regular local file required: {path}")
    return resolved


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(local_file(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def valid_sidecar(path: Path) -> bool:
    sidecar = path.with_name(path.name + ".sha256")
    return (
        sidecar.is_file()
        and not sidecar.is_symlink()
        and sidecar.read_text(encoding="ascii")
        == f"{sha256(path)}  {path.name}\n"
    )


def validate_bundle() -> dict[str, Any]:
    manifest = load_object(BUNDLE / "manifest.json")
    if (
        socket.gethostname() != "compute00"
        or manifest.get("bundle_id") != BUNDLE.name
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("XP bundle identity/scope differs")
    profile = load_object(BUNDLE / str(manifest["profile"]["path"]))
    expected = manifest["resource_envelope"]
    if {key: profile.get(key) for key in expected} != expected:
        raise RuntimeError("XP frozen profile differs from manifest envelope")
    for relative, expected_sha in manifest["payload_files"].items():
        path = local_file(BUNDLE / relative)
        if sha256(path) != expected_sha:
            raise RuntimeError(f"XP bundle payload hash mismatch: {relative}")
    return manifest


def validate_r0_seal(path: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    path = local_file(path)
    if not valid_sidecar(path):
        raise RuntimeError("formal XP AT seal sidecar is absent or differs")
    seal = load_object(path)
    if (
        seal.get("schema_version") != "mysql_xp_capture_at_coverage_seal_v1"
        or seal.get("status")
        != "SEALED_CAPTURE_AT_COVERAGE_FOR_FORMAL_R0_ONLY"
        or seal.get("bundle_id") != manifest["bundle_id"]
        or seal.get("bundle_manifest_sha256") != sha256(BUNDLE / "manifest.json")
        or seal.get("sealed") is not True
        or seal.get("at_coverage")
        != {f"AT{index}": True for index in range(1, 7)}
        or seal.get("capture_authorized") is not True
        or seal.get("formal_r0_launch_authorized") is not True
        or seal.get("outer_execution_authorized") is not False
        or seal.get("algorithm_c_execution_authorized") is not False
        or seal.get("accepted_round_authorized") is not False
        or seal.get("capture_io_is_not_r0") is not True
        or seal.get("qps_resource_input_prohibited") is not True
        or seal.get("formal_scope") != "mysql_only"
        or seal.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("formal XP AT seal is not exact bundle-bound R0-only authority")
    sources = seal.get("sources")
    capture_source = (
        sources.get("capture_spec") if isinstance(sources, Mapping) else None
    )
    if not isinstance(capture_source, Mapping):
        raise RuntimeError("formal XP AT seal capture spec authority is absent")
    capture_spec = local_file(Path(str(capture_source.get("path") or "")))
    if (
        capture_source.get("sha256") != sha256(capture_spec)
        or capture_source.get("schema_version")
        != "mysql_xp_fresh_local_capture_spec_v1"
        or not valid_sidecar(capture_spec)
    ):
        raise RuntimeError("formal XP AT seal capture spec hash/schema differs")
    return seal


validate_formal_r0_seal = validate_r0_seal


def validate_outer_authority(
    path: Path,
    manifest: Mapping[str, Any],
    config_path: Path,
    r0_authority_path: Path,
) -> dict[str, Any]:
    path = local_file(path)
    if not valid_sidecar(path):
        raise RuntimeError("XP outer authorization sidecar is absent or differs")
    authority = load_object(path)
    if (
        authority.get("schema_version") != "mysql_xp_outer_authorization_v1"
        or authority.get("status") != "SEALED_OUTER_AUTHORIZED"
        or authority.get("bundle_id") != manifest["bundle_id"]
        or authority.get("config_sha256") != sha256(config_path)
        or authority.get("formal_r0_authority_sha256") != sha256(r0_authority_path)
        or authority.get("formal_r0_passed") is not True
        or authority.get("strict_registries_authorized") is not True
        or authority.get("outer_execution_authorized") is not True
        or authority.get("algorithm_c_execution_authorized") is not True
        or authority.get("accepted_round_authorized") is not False
        or authority.get("formal_scope") != "mysql_only"
        or authority.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("XP outer authorization is incomplete or identity-mismatched")
    return authority


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=("capture", "formal_r0", "formal_outer"), required=True
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--driver", type=Path, required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--capture-spec", type=Path)
    parser.add_argument("--sealed-at-registry", type=Path)
    parser.add_argument("--outer-authorization", type=Path)
    parser.add_argument("--readiness-manifest", type=Path)
    parser.add_argument("--formal-r0-authority", type=Path)
    parser.add_argument("--journal-root", type=Path)
    args = parser.parse_args()
    manifest = validate_bundle()
    config_path = local_file(args.config)
    if not valid_sidecar(config_path):
        raise RuntimeError("XP launch config checksum sidecar is absent or differs")
    expected_driver = local_file(DRIVERS[args.mode])
    if local_file(args.driver) != expected_driver:
        raise RuntimeError(f"XP {args.mode} driver is outside the bundle closure")
    config = load_object(config_path)
    if (
        config.get("formal_scope") != "mysql_only"
        or config.get("cross_engine_comparison_prohibited") is not True
        or config.get("runtime_bundle_id") != manifest["bundle_id"]
    ):
        raise RuntimeError("XP launch config scope/bundle identity differs")

    environment = dict(os.environ)
    environment["MYSQL_XP_RUNTIME_BUNDLE"] = str(BUNDLE)
    environment["MYSQL_FORMAL_SCOPE"] = "mysql_only"
    environment["MYSQL_CROSS_ENGINE_COMPARISON_PROHIBITED"] = "1"
    environment["MYSQL_XP_CONTRACT_BUNDLE_ID"] = manifest["bundle_id"]
    environment["MYSQL_XP_CONTRACT_CONFIG_SHA256"] = sha256(config_path)
    environment["MYSQL_XP_CONTRACT_MODE"] = args.mode
    bundled_python = [
        str(BUNDLE / "python"),
        str(BUNDLE / "orchestration"),
        str(BUNDLE / "driver"),
        str(BUNDLE / "capture"),
    ]
    if environment.get("PYTHONPATH"):
        bundled_python.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(bundled_python)

    if args.mode == "capture":
        if (
            not args.run_id
            or args.capture_spec is None
            or args.sealed_at_registry is not None
            or args.outer_authorization is not None
            or args.formal_r0_authority is not None
        ):
            raise RuntimeError("capture requires only run-id and capture-spec authority")
        capture_spec = local_file(args.capture_spec)
        if not valid_sidecar(capture_spec):
            raise RuntimeError("XP capture spec sidecar is absent or differs")
        if (
            config.get("authorization_stage") != "capture_only"
            or config.get("formal_r0_launch_authorized") is not False
            or config.get("outer_execution_authorized") is not False
            or config.get("accepted_round_authorized") is not False
            or config.get("mysql", {}).get("port") != 65204
            or config.get("workload", {}).get("capture_only") is not True
        ):
            raise RuntimeError("XP capture config is not exact capture-only authority")
        argv = [
            sys.executable, str(expected_driver),
            "--config", str(config_path),
            "--capture-spec", str(capture_spec),
            "--run-id", args.run_id,
        ]
    elif args.mode == "formal_r0":
        if (
            not args.run_id
            or args.sealed_at_registry is None
            or args.capture_spec is not None
            or args.outer_authorization is not None
            or args.formal_r0_authority is not None
        ):
            raise RuntimeError("formal_r0 requires only run-id and sealed AT registry")
        seal_path = local_file(args.sealed_at_registry)
        validate_r0_seal(seal_path, manifest)
        if (
            config.get("authorization_stage") != "formal_r0_only"
            or config.get("formal_r0_launch_authorized") is not True
            or config.get("outer_execution_authorized") is not False
            or config.get("accepted_round_authorized") is not False
            or config.get("mysql", {}).get("port") != 65304
            or config.get("workload", {}).get("formal_r0_only") is not True
        ):
            raise RuntimeError("XP formal R0 config is not exact R0-only authority")
        environment["MYSQL_XP_CONTRACT_R0_SEAL_SHA256"] = sha256(seal_path)
        environment["MYSQL_XP_CONTRACT_RUN_ID"] = args.run_id
        argv = [
            sys.executable, str(expected_driver),
            "--config", str(config_path),
            "--sealed-at-registry", str(seal_path),
            "--run-id", args.run_id,
        ]
    else:
        if (
            args.outer_authorization is None
            or args.readiness_manifest is None
            or args.formal_r0_authority is None
            or args.journal_root is None
            or args.capture_spec is not None
            or args.sealed_at_registry is not None
            or args.run_id is not None
        ):
            raise RuntimeError(
                "formal_outer requires outer authorization, readiness, "
                "formal R0 authority, and journal root"
            )
        r0_authority = local_file(args.formal_r0_authority)
        if not valid_sidecar(r0_authority):
            raise RuntimeError("XP formal R0 authority sidecar is absent or differs")
        outer_authority = local_file(args.outer_authorization)
        validate_outer_authority(
            outer_authority, manifest, config_path, r0_authority
        )
        if (
            config.get("authorization_stage") != "formal_outer"
            or config.get("execution_authorized") is not True
            or config.get("outer_execution_authorized") is not True
            or config.get("accepted_round_authorized") is not False
        ):
            raise RuntimeError("XP formal outer config is not independently authorized")
        readiness = local_file(args.readiness_manifest)
        if readiness != outer_authority:
            raise RuntimeError(
                "XP formal outer readiness must be the independently sealed "
                "outer authorization"
            )
        environment["MYSQL_XP_CONTRACT_OUTER_AUTHORIZATION_SHA256"] = sha256(
            outer_authority
        )
        argv = [
            sys.executable, str(expected_driver),
            "--config", str(config_path),
            "--readiness-manifest", str(readiness),
            "--r0-authority-readiness-manifest", str(r0_authority),
            "--service-factory", "mysql_algorithm_c_local_services:build",
            "--journal-root", str(args.journal_root.resolve()),
        ]
    os.execve(sys.executable, argv, environment)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Materialize one attempt-owned, manifest-bound TPC-C configuration.

This utility is intentionally offline with respect to the database: it only
reads an immutable bundle and an external mode-0600 password file, then
atomically creates an attempt-local config directory.  It never prints or
records the password/token/QPS value.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Any


RUNTIME_ROOT = "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729"
FILES = {
    "tpcc.xml": "configuration/tpcc.xml.template",
    "mysql.cnf": "configuration/mysql.cnf.template",
}
PROFILE = "configuration/config_profile.json"
TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{16,128}$")


class ConfigError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ConfigError("regular non-symlink file required: " + str(path))


def verify_bundle(bundle: Path) -> dict[str, Any]:
    bundle = bundle.resolve(strict=True)
    manifest_path = bundle / "bundle_manifest.json"
    _regular(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    unsigned = dict(manifest)
    identity = unsigned.pop("bundle_id", None)
    if identity != bundle.name or hashlib.sha256(canonical(unsigned)).hexdigest() != identity:
        raise ConfigError("bundle content identity differs")
    listed = manifest.get("files")
    if not isinstance(listed, dict):
        raise ConfigError("bundle file closure is absent")
    for relative in [*FILES.values(), PROFILE]:
        candidate = bundle / relative
        if listed.get(relative) != sha256(candidate):
            raise ConfigError("sealed configuration input differs: " + relative)
    return manifest


def _password(path: Path) -> str:
    _regular(path)
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ConfigError("password file must be mode 0600")
    value = path.read_text(encoding="utf-8").rstrip("\r\n")
    if not value or "\x00" in value or "\n" in value or "\r" in value:
        raise ConfigError("password file must contain one nonempty line")
    return value


def _atomic_write(
    path: Path, payload: bytes, mode: int,
    uid: int | None = None, gid: int | None = None,
) -> None:
    if (uid is None) != (gid is None):
        raise ConfigError("atomic owner requires both uid and gid")
    temporary = path.with_name("." + path.name + "." + secrets.token_hex(8) + ".tmp")
    descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(str(temporary), mode)
        if uid is not None and gid is not None:
            os.chown(temporary, uid, gid)
        os.replace(str(temporary), str(path))
    finally:
        if temporary.exists():
            temporary.unlink()


def materialize(
    bundle: Path, attempt_id: str, config_dir: Path,
    password_file: Path, output: Path, *,
    target_uid: int | None = None, target_gid: int | None = None,
) -> dict[str, Any]:
    manifest = verify_bundle(bundle)
    if TOKEN.fullmatch(attempt_id) is None:
        raise ConfigError("attempt ID must be safe and 16..128 characters")
    attempt_root = Path(RUNTIME_ROOT) / attempt_id
    config_dir = config_dir.resolve(strict=False)
    output = output.resolve(strict=False)
    if config_dir != attempt_root / "config" or output != attempt_root / "config_materialization.json":
        raise ConfigError("configuration paths must be attempt-owned")
    if config_dir.exists() or config_dir.is_symlink() or output.exists() or output.is_symlink():
        raise ConfigError("attempt configuration already exists; refusing reuse")
    if config_dir.parent != attempt_root or not attempt_root.is_dir() or attempt_root.is_symlink():
        raise ConfigError("attempt root must already exist as a regular directory")
    if stat.S_IMODE(attempt_root.stat().st_mode) & 0o077:
        raise ConfigError("attempt root must not be group/world accessible")
    profile_path = bundle.resolve() / PROFILE
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    if (
        profile.get("qps_resource_input_prohibited") is not True
        or profile.get("password_handling") != "external_0600_file_substituted_only_at_attempt_materialization_not_logged_or_manifested"
    ):
        raise ConfigError("sealed profile violates secret/QPS boundary")
    password = _password(password_file)
    if (target_uid is None) != (target_gid is None):
        raise ConfigError("config target owner requires both uid and gid")
    config_uid = os.geteuid() if target_uid is None else target_uid
    config_gid = os.getegid() if target_gid is None else target_gid
    if config_uid < 0 or config_gid < 0:
        raise ConfigError("config target owner is invalid")
    stage = attempt_root / (".config-stage-" + secrets.token_hex(12))
    stage.mkdir(mode=0o700)
    result_files: dict[str, dict[str, Any]] = {}
    try:
        os.chmod(str(stage), 0o700)
        os.chown(stage, config_uid, config_gid)
        for target, relative in FILES.items():
            template = (bundle.resolve() / relative).read_text(encoding="utf-8")
            if template.count("${MYSQL_PASSWORD}") != 1:
                raise ConfigError("sealed template placeholder differs: " + relative)
            rendered = template.replace("${MYSQL_PASSWORD}", password).encode("utf-8")
            destination = stage / target
            _atomic_write(
                destination, rendered, 0o600, config_uid, config_gid
            )
            file_stat = destination.stat()
            if (
                destination.is_symlink()
                or file_stat.st_uid != config_uid
                or file_stat.st_gid != config_gid
                or stat.S_IMODE(file_stat.st_mode) != 0o600
            ):
                raise ConfigError(
                    "staged config ownership/mode differs: " + target
                )
            result_files[target] = {
                "sha256": hashlib.sha256(rendered).hexdigest(),
                "bytes": len(rendered), "mode": "0600",
                "uid": config_uid, "gid": config_gid,
                "template_sha256": sha256(bundle.resolve() / relative),
            }
        stage_stat = stage.stat()
        if (
            stage_stat.st_uid != config_uid
            or stage_stat.st_gid != config_gid
            or stat.S_IMODE(stage_stat.st_mode) != 0o700
        ):
            raise ConfigError("staged config directory ownership/mode differs")
        os.replace(str(stage), str(config_dir))
        descriptor = os.open(str(attempt_root), os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except BaseException:
        if stage.exists():
            for item in stage.iterdir():
                item.unlink()
            stage.rmdir()
        raise
    result = {
        "schema_version": "mysql_tpcc_attempt_config_materialization_v1",
        "status": "PASS_CONFIG_MATERIALIZED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "bundle_id": manifest["bundle_id"], "attempt_id": attempt_id,
        "config_dir": str(config_dir), "config_dir_mode": "0700",
        "config_dir_uid": config_uid, "config_dir_gid": config_gid,
        "ownership_transactional_before_publish": True,
        "files": result_files, "profile_sha256": sha256(profile_path),
        "qps_resource_input_prohibited": True,
        "password_source": "external_0600_file_not_logged_or_hashed",
        "formal_launch_authorized": False, "accepted_round_authorized": False,
    }
    _atomic_write(
        output, canonical(result), 0o600, config_uid, config_gid
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["materialize"])
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--mysql-password-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = materialize(args.bundle, args.attempt_id, args.config_dir, args.mysql_password_file, args.output)
    print(json.dumps({"status": value["status"], "bundle_id": value["bundle_id"], "attempt_id": value["attempt_id"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, ConfigError) as exc:
        sys.stderr.write("NOT_READY: " + str(exc) + "\n")
        raise SystemExit(2)

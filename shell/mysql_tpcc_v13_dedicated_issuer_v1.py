#!/usr/bin/env python3
"""Issue one fail-closed TPC-C dedicated-daemon qualification plan.

This is an offline issuer.  It never contacts a worker and never starts a
container.  The emitted plan binds every host phase to the frozen bundle,
Python runtime, image ID, attempt paths, and the real v2 command parsers.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
from typing import Any


RUNTIME_ROOT = Path(
    "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/"
    "runtime_algorithm_c_successor_20260729"
)
PYTHON_TREE_SHA256 = (
    "3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89"
)
PYTHON = (
    RUNTIME_ROOT / "runtime" / ("python311-" + PYTHON_TREE_SHA256)
    / "bin" / "python3.11"
)
MYSQL_IMAGE_ID = (
    "sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
)
EXPECTED_SCHEMA = "mysql_tpcc_v13_dedicated_daemon_successor_bundle_v1"
ATTEMPT_PREFIX = "tpcc-dd-"
FORMAL_SCOPE = "mysql_only"


class IssuerError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_manifest(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise IssuerError("bundle manifest is absent or symlinked")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IssuerError("bundle manifest is malformed") from exc
    if not isinstance(value, dict):
        raise IssuerError("bundle manifest must be an object")
    bundle_id = str(value.get("bundle_id") or "")
    files = value.get("files")
    required = {
        "authority/mysql_tpcc_node04_adopted_service_v2.py",
        "authority/mysql_tpcc_config_materialize_v2.py",
        "authority/mysql_tpcc_measurement_bridge_v1.py",
        "authority/mysql_tpcc_blkio_v1.py",
    }
    if (
        value.get("schema_version") != EXPECTED_SCHEMA
        or path.parent.name != bundle_id
        or len(bundle_id) != 64
        or not isinstance(files, dict)
        or not required.issubset(files)
        or value.get("formal_scope") != FORMAL_SCOPE
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("formal_launch_authorized") is not False
        or value.get("accepted_round_authorized") is not False
    ):
        raise IssuerError("bundle identity or authority closure differs")
    for relative in required:
        target = path.parent / relative
        if (
            target.is_symlink()
            or not target.is_file()
            or sha256(target) != files[relative]
        ):
            raise IssuerError("required authority is absent or hash differs")
    return value


def verify_source_token(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise IssuerError("source token is absent or symlinked")
    value = path.stat()
    if value.st_uid != os.getuid() or stat.S_IMODE(value.st_mode) != 0o600:
        raise IssuerError("source token owner or mode differs")
    if value.st_size < 1:
        raise IssuerError("source token is empty")
    return sha256(path)


def service_argv(
    bundle: Path,
    action: str,
    output: Path,
    *,
    attempt_id: str,
    attempt_root: Path,
    authorization_root: Path,
) -> list[str]:
    script = bundle / "authority/mysql_tpcc_node04_adopted_service_v2.py"
    if action == "prepare-copy":
        arguments = [
            action,
            "--image-id",
            MYSQL_IMAGE_ID,
            "--attempt-id",
            attempt_id,
        ]
    elif action == "plan":
        arguments = [
            action,
            "--bundle",
            str(bundle),
            "--image-id",
            MYSQL_IMAGE_ID,
            "--config-dir",
            str(attempt_root / "config"),
            "--datadir",
            str(attempt_root / "datadir/datadir"),
            "--measurement-bridge",
            str(attempt_root / "measurement"),
            "--attempt-id",
            attempt_id,
            "--handoff-token-file",
            str(authorization_root / "root-token"),
        ]
    elif action in {"start", "adopt", "prefault"}:
        arguments = [
            action,
            "--plan",
            str(attempt_root / "plan.json"),
        ]
    else:
        raise IssuerError("unknown service action")
    return [str(PYTHON), str(script), *arguments, "--output", str(output)]


def config_argv(
    bundle: Path,
    action: str,
    output: Path,
    *,
    attempt_id: str,
    attempt_root: Path,
) -> list[str]:
    script = bundle / "authority/mysql_tpcc_config_materialize_v2.py"
    arguments = [
        action,
        "--bundle",
        str(bundle),
        "--attempt-id",
        attempt_id,
        "--mysql-password-file",
        str(attempt_root / "mysql-password"),
    ]
    if action == "provision-password":
        arguments.extend(
            [
                "--image-id",
                MYSQL_IMAGE_ID,
                "--datadir",
                str(attempt_root / "datadir/datadir"),
            ]
        )
    elif action == "materialize":
        arguments.extend(["--config-dir", str(attempt_root / "config")])
    else:
        raise IssuerError("unknown config action")
    return [str(PYTHON), str(script), *arguments, "--output", str(output)]


def measurement_bridge_argv(
    bundle: Path,
    output: Path,
    *,
    attempt_id: str,
    attempt_root: Path,
    bundle_id: str,
) -> list[str]:
    script = bundle / "authority/mysql_tpcc_measurement_bridge_v1.py"
    return [
        str(PYTHON),
        str(script),
        "enforce",
        "--path",
        str(attempt_root / "measurement"),
        "--attempt-id",
        attempt_id,
        "--bundle-id",
        bundle_id,
        "--output",
        str(output),
    ]


def blkio_argv(
    bundle: Path,
    output: Path,
    *,
    attempt_root: Path,
) -> list[str]:
    script = bundle / "authority/mysql_tpcc_blkio_v1.py"
    return [
        str(PYTHON),
        str(script),
        "enforce",
        "--plan",
        str(attempt_root / "plan.json"),
        "--output",
        str(output),
    ]


def phase(
    attempt_id: str,
    name: str,
    argv: list[str],
    *,
    dependencies: list[str],
) -> dict[str, Any]:
    return {
        "argv": argv,
        "dependencies": dependencies,
        "unit": f"ra-mysql-tpcc-{attempt_id}-{name}.service",
        "systemd_run": {
            "service_type": "oneshot",
            "remain_after_exit": True,
            "forbidden_options": ["--wait", "--collect"],
        },
        "poll_success": {
            "ActiveState": "active",
            "SubState": "exited",
            "MainPID": "0",
            "ExecMainStatus": "0",
            "Result": "success",
        },
    }


def issue(
    manifest_path: Path,
    token_path: Path,
    output_root: Path,
    ttl_seconds: int,
) -> tuple[Path, dict[str, Any]]:
    if not 60 <= ttl_seconds <= 1800:
        raise IssuerError("TTL must be between 60 and 1800 seconds")
    manifest = load_manifest(manifest_path)
    source_token_sha256 = verify_source_token(token_path)
    now = dt.datetime.now(dt.timezone.utc)
    attempt_id = ATTEMPT_PREFIX + secrets.token_hex(12)
    handoff_token = (attempt_id + "\n").encode("ascii")
    handoff_token_sha256 = hashlib.sha256(handoff_token).hexdigest()
    bundle = RUNTIME_ROOT / "bundles" / manifest["bundle_id"]
    attempt_root = RUNTIME_ROOT / attempt_id
    authorization_root = RUNTIME_ROOT / "authorizations" / attempt_id

    outputs = {
        "prepare-copy": attempt_root / "physical_copy_authority.json",
        "measurement-bridge": attempt_root / "measurement_bridge_authority.json",
        "provision-password": attempt_root / "credential_provisioning.json",
        "materialize": attempt_root / "config_materialization.json",
        "plan": attempt_root / "plan.json",
        "start": attempt_root / "start_authority.json",
        "blkio": attempt_root / "blkio_throttle_authority.json",
        "adopt": attempt_root / "adopted_service.json",
        "prefault": attempt_root / "prefault_zero_io.json",
    }
    stages = {
        "prepare-copy": phase(
            attempt_id,
            "prepare-copy",
            service_argv(
                bundle,
                "prepare-copy",
                outputs["prepare-copy"],
                attempt_id=attempt_id,
                attempt_root=attempt_root,
                authorization_root=authorization_root,
            ),
            dependencies=[],
        ),
        "measurement-bridge": phase(
            attempt_id,
            "measurement-bridge",
            measurement_bridge_argv(
                bundle,
                outputs["measurement-bridge"],
                attempt_id=attempt_id,
                attempt_root=attempt_root,
                bundle_id=manifest["bundle_id"],
            ),
            dependencies=["prepare-copy"],
        ),
        "provision-password": phase(
            attempt_id,
            "provision-password",
            config_argv(
                bundle,
                "provision-password",
                outputs["provision-password"],
                attempt_id=attempt_id,
                attempt_root=attempt_root,
            ),
            dependencies=["measurement-bridge"],
        ),
        "materialize": phase(
            attempt_id,
            "materialize",
            config_argv(
                bundle,
                "materialize",
                outputs["materialize"],
                attempt_id=attempt_id,
                attempt_root=attempt_root,
            ),
            dependencies=["provision-password"],
        ),
        "plan": phase(
            attempt_id,
            "plan",
            service_argv(
                bundle,
                "plan",
                outputs["plan"],
                attempt_id=attempt_id,
                attempt_root=attempt_root,
                authorization_root=authorization_root,
            ),
            dependencies=["materialize"],
        ),
        "start": phase(
            attempt_id,
            "start",
            service_argv(
                bundle,
                "start",
                outputs["start"],
                attempt_id=attempt_id,
                attempt_root=attempt_root,
                authorization_root=authorization_root,
            ),
            dependencies=["plan"],
        ),
        "blkio": phase(
            attempt_id,
            "blkio",
            blkio_argv(
                bundle,
                outputs["blkio"],
                attempt_root=attempt_root,
            ),
            dependencies=["start"],
        ),
        "adopt": phase(
            attempt_id,
            "adopt",
            service_argv(
                bundle,
                "adopt",
                outputs["adopt"],
                attempt_id=attempt_id,
                attempt_root=attempt_root,
                authorization_root=authorization_root,
            ),
            dependencies=["blkio"],
        ),
        "prefault": phase(
            attempt_id,
            "prefault",
            service_argv(
                bundle,
                "prefault",
                outputs["prefault"],
                attempt_id=attempt_id,
                attempt_root=attempt_root,
                authorization_root=authorization_root,
            ),
            dependencies=["adopt"],
        ),
    }
    result = {
        "schema_version": "mysql_tpcc_v13_dedicated_materials_v2",
        "status": "ISSUED_NOT_EXECUTED",
        "formal_scope": FORMAL_SCOPE,
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "bundle_id": manifest["bundle_id"],
        "bundle_manifest_sha256": sha256(manifest_path),
        "attempt_id": attempt_id,
        "attempt_root": str(attempt_root),
        "attempt_root_must_be_absent": True,
        "authorization_root": str(authorization_root),
        "authorization_root_must_be_absent": True,
        "authorization_outside_attempt": True,
        "source_token_sha256": source_token_sha256,
        "handoff_token_sha256": handoff_token_sha256,
        "remote_install_gate": {
            "source": "private_authority/root-token",
            "target": str(authorization_root / "root-token"),
            "destination_directory_mode": "0700",
            "file_mode": "0600",
            "owner_uid_gid": [0, 0],
            "regular": True,
            "non_symlink": True,
            "exclusive_create_required": True,
            "sha256_expected": handoff_token_sha256,
            "source_token_is_issuer_authority_only": True,
            "handoff_token_must_equal_attempt_id_after_strip": True,
            "token_in_argv_or_log_prohibited": True,
        },
        "frozen_python": {
            "path": str(PYTHON),
            "tree_sha256": PYTHON_TREE_SHA256,
        },
        "mysql_image_id": MYSQL_IMAGE_ID,
        "stage_order": list(stages),
        "stages": stages,
        "issued_at_utc": now.isoformat(),
        "expires_at_utc": (
            now + dt.timedelta(seconds=ttl_seconds)
        ).isoformat(),
        "qualification_only": True,
        "typed_trace_authorization_required_after_service": True,
        "formal_r0_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
    }
    identity = hashlib.sha256(canonical(result)).hexdigest()
    destination = output_root / identity
    destination.mkdir(parents=True, mode=0o700, exist_ok=False)
    os.chmod(destination, 0o700)
    private = destination / "private_authority"
    private.mkdir(mode=0o700)
    token_copy = private / "root-token"
    descriptor = os.open(
        token_copy,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(handoff_token)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        token_copy.unlink(missing_ok=True)
        raise
    os.chmod(token_copy, 0o600)
    if sha256(token_copy) != handoff_token_sha256:
        raise IssuerError("generated handoff token hash differs")
    material_path = destination / "materials.json"
    material_path.write_bytes(canonical(result))
    os.chmod(material_path, 0o400)
    sidecar = destination / "materials.json.sha256"
    sidecar.write_text(
        sha256(material_path) + "  materials.json\n",
        encoding="ascii",
    )
    os.chmod(sidecar, 0o400)
    return destination, result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-manifest", type=Path, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--ttl", type=int, default=900)
    args = parser.parse_args()
    destination, result = issue(
        args.bundle_manifest,
        args.token_file,
        args.output_root,
        args.ttl,
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "materials": str(destination),
                "bundle_id": result["bundle_id"],
                "attempt_id": result["attempt_id"],
                "expires_at_utc": result["expires_at_utc"],
                "formal_launch_authorized": False,
                "accepted_round_authorized": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (IssuerError, OSError, ValueError) as exc:
        print("NOT_READY: " + str(exc), file=os.sys.stderr)
        raise SystemExit(2)

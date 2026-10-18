#!/usr/bin/env python3
"""Issue fresh JOB module-closure bootstrap materials without remote actions."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import stat
import sys


sys.dont_write_bytecode = True
SCHEMA = "mysql_job_dedicated_daemon_module_closure_successor_bundle_v2"
RUNTIME_ROOT = (
    "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/"
    "runtime_algorithm_c_successor_20260729"
)


class MaterialError(RuntimeError):
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


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise MaterialError("sealed module spec is unavailable: " + str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_manifest(path):
    path = Path(path).resolve(strict=True)
    if path.is_symlink() or not path.is_file():
        raise MaterialError("bundle manifest is absent or symlinked")
    value = json.loads(path.read_text(encoding="utf-8"))
    files = value.get("files")
    if (
        path.parent.name != value.get("bundle_id")
        or value.get("schema_version") != SCHEMA
        or not isinstance(files, dict)
        or value.get("formal_launch_authorized") is not False
        or value.get("accepted_round_authorized") is not False
    ):
        raise MaterialError("module-closure successor binding differs")
    actual = {}
    for relative in files:
        candidate = path.parent / relative
        if candidate.is_symlink() or not candidate.is_file():
            raise MaterialError("manifest file is absent: " + relative)
        actual[relative] = digest(candidate)
    if actual != files:
        raise MaterialError("bundle manifest closure differs")
    return path, value


def write_new(path, payload, mode):
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise MaterialError("refusing to overwrite " + str(path))
    descriptor = os.open(
        str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode
    )
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(path, mode)


def write_public(path, value):
    payload = canonical(value)
    write_new(path, payload, 0o400)
    write_new(
        Path(str(path) + ".sha256"),
        (
            hashlib.sha256(payload).hexdigest()
            + "  "
            + Path(path).name
            + "\n"
        ).encode("ascii"),
        0o400,
    )


def materialize(args):
    manifest_path, manifest = load_manifest(args.bundle_manifest)
    token_path = args.main_agent_token_file
    if token_path.is_symlink() or not token_path.is_file():
        raise MaterialError("main-agent token is absent or symlinked")
    if stat.S_IMODE(token_path.stat().st_mode) & 0o077:
        raise MaterialError("main-agent token mode is too permissive")
    token = token_path.read_text(encoding="utf-8").strip()
    if not token:
        raise MaterialError("main-agent token is empty")
    root = manifest_path.parent
    renderer = load_module(
        root / "controller/render_mysql_job_v32_bootstrap_spec_tmpfs_v2.py",
        "job_module_closure_renderer",
    )
    authority = load_module(
        root
        / "qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py",
        "job_module_closure_authority",
    )
    namespace = argparse.Namespace(
        bundle_manifest=manifest_path,
        mysql_image_reference=args.mysql_image_reference,
        mysql_image_id=args.mysql_image_id,
        client_image_reference=args.client_image_reference,
        client_image_id=args.client_image_id,
        main_agent_token_file=token_path,
        attempt_token=None,
        valid_minutes=args.valid_minutes,
        output=Path("unused"),
    )
    spec = renderer.render(namespace)
    plan = authority.command_plan(spec)
    attempt = spec["attempt_token"]
    authorization_root = (
        RUNTIME_ROOT + "/authorizations/" + attempt
    )
    if (
        spec["paths"]["authorization_root"] != authorization_root
        or spec["paths"]["main_agent_token_file"]
        != authorization_root + "/main_agent_token"
        or authorization_root.startswith(spec["paths"]["attempt_root"] + "/")
    ):
        raise MaterialError("authorization placement differs")
    material_id = hashlib.sha256(
        canonical(
            {
                "bundle_id": manifest["bundle_id"],
                "attempt_token": attempt,
                "spec_sha256": hashlib.sha256(canonical(spec)).hexdigest(),
            }
        )
    ).hexdigest()
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    final = output_root / material_id
    if final.exists() or final.is_symlink():
        raise MaterialError("immutable material collision")
    stage = output_root / (".stage-" + material_id + "-" + secrets.token_hex(6))
    stage.mkdir(mode=0o700)
    private = stage / "private_authority"
    private.mkdir(mode=0o700)
    try:
        write_new(
            private / "main_agent_token",
            (token + "\n").encode("utf-8"),
            0o600,
        )
        install = {
            "schema_version":
                "mysql_job_authorization_install_plan_v1",
            "status": "PLANNED_NOT_INSTALLED",
            "attempt_token": attempt,
            "attempt_root": spec["paths"]["attempt_root"],
            "attempt_root_must_be_absent_before_install": True,
            "destination_root": authorization_root,
            "destination_root_must_be_outside_attempts": True,
            "destination_root_must_be_absent": True,
            "destination_directory_mode": "0700",
            "files": {
                "bootstrap_spec.json": {
                    "source_relative": "bootstrap_spec.json",
                    "destination":
                        authorization_root + "/bootstrap_spec.json",
                    "mode": "0400",
                },
                "command_plan.json": {
                    "source_relative": "command_plan.json",
                    "destination":
                        authorization_root + "/command_plan.json",
                    "mode": "0400",
                },
                "main_agent_token": {
                    "source_relative":
                        "private_authority/main_agent_token",
                    "destination":
                        authorization_root + "/main_agent_token",
                    "mode": "0600",
                    "content_in_argv_or_log_prohibited": True,
                },
            },
            "partial_install_invalidates_attempt": True,
            "exclusive_create_required": True,
            "formal_r0_authorized": False,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
        }
        materials = {
            "schema_version":
                "mysql_job_dedicated_daemon_materials_v3",
            "status": "ISSUED_NOT_EXECUTED",
            "bundle_id": manifest["bundle_id"],
            "bundle_manifest_sha256": digest(manifest_path),
            "attempt_token": attempt,
            "authorization_root": authorization_root,
            "attempt_root": spec["paths"]["attempt_root"],
            "attempt_root_must_be_absent": True,
            "authorization_outside_attempt": True,
            "invalidated_predecessor_attempts": [
                "job-v2-20260731t061719z-3c69f2da1d4781cafa9e5b39",
                "job-v2-20260731t063314z-6a38fa4a52b89030e7d6e5e4",
            ],
            "invalidated_predecessor_credit": 0,
            "formal_r0_authorized": False,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
        }
        write_public(stage / "bootstrap_spec.json", spec)
        write_public(stage / "command_plan.json", plan)
        write_public(stage / "authorization_install_plan.json", install)
        write_public(stage / "materials.json", materials)
        os.rename(stage, final)
    except BaseException:
        # The stage contains a private token, so leave it fail-closed for
        # forensic handling instead of trying a broad recursive deletion.
        raise
    return {
        "status": "ISSUED_NOT_EXECUTED",
        "materials": str(final),
        "bundle_id": manifest["bundle_id"],
        "bundle_manifest_sha256": digest(manifest_path),
        "attempt_token": attempt,
        "authorization_root": authorization_root,
        "expires_at_utc": spec["expires_at_utc"],
        "formal_r0_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-manifest", type=Path, required=True)
    parser.add_argument("--mysql-image-reference", required=True)
    parser.add_argument("--mysql-image-id", required=True)
    parser.add_argument("--client-image-reference", required=True)
    parser.add_argument("--client-image-id", required=True)
    parser.add_argument("--main-agent-token-file", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--valid-minutes", type=int, default=45)
    args = parser.parse_args()
    if args.valid_minutes < 40 or args.valid_minutes > 60:
        parser.error("--valid-minutes must be within 40..60")
    print(json.dumps(materialize(args), sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (MaterialError, OSError, ValueError) as exc:
        raise SystemExit("NOT_READY: %s" % exc)

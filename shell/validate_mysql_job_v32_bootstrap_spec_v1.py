#!/usr/bin/env python3
"""Read-only controller-side validator for a filled JOB v32 bootstrap spec.

It accepts a short-lived, sidecar-bound spec only after all template markers
are gone, verifies the selected bundle's exact closure, and invokes the
bundle's ``command_plan`` only.  It never executes a Docker, SSH, mysql, cp,
or data-plane command.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

# Loading a sealed authority is a read-only operation.  Never let the import
# machinery add __pycache__ entries that would invalidate its exact closure.
sys.dont_write_bytecode = True


class ValidationError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def has_template_marker(value: Any) -> bool:
    if isinstance(value, str):
        return "${" in value or "}" in value and value.startswith("${")
    if isinstance(value, dict):
        return any(has_template_marker(key) or has_template_marker(item) for key, item in value.items())
    if isinstance(value, list):
        return any(has_template_marker(item) for item in value)
    return False


def load_authority(bundle: Path):
    module_path = bundle / "qualification/mysql_job_distributed_v2_authority.py"
    spec = importlib.util.spec_from_file_location("sealed_job_v32_authority", module_path)
    if spec is None or spec.loader is None:
        raise ValidationError("sealed JOB authority is not importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate(bundle: Path, spec_path: Path) -> dict[str, Any]:
    bundle = bundle.resolve(strict=True)
    manifest_path = bundle / "bundle_manifest.json"
    if bundle.is_symlink() or manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValidationError("bundle identity is absent or symlinked")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bundle_id = manifest.get("bundle_id")
    if not isinstance(bundle_id, str) or bundle.name != bundle_id:
        raise ValidationError("bundle content address differs")
    authority = load_authority(bundle)
    authority.verify_bundle_closure(bundle, bundle_id, sha256(manifest_path))
    spec = authority.load_with_sidecar(spec_path, "bootstrap spec")
    if has_template_marker(spec):
        raise ValidationError("bootstrap spec still contains template markers")
    if spec.get("bundle") != {
        "path": str(spec.get("bundle", {}).get("path", "")),
        "bundle_id": bundle_id,
        "manifest_sha256": sha256(manifest_path),
    }:
        raise ValidationError("filled spec bundle binding differs")
    plan = authority.command_plan(spec)
    return {
        "schema_version": "mysql_job_v32_bootstrap_spec_validation_v1",
        "status": "PASS_STATIC_EMIT_PLAN_NOT_EXECUTED",
        "bundle_id": bundle_id,
        "bundle_manifest_sha256": sha256(manifest_path),
        "attempt_token": spec["attempt_token"],
        "qualification_only": plan["qualification_only"],
        "accepted_round_authorized": plan["accepted_round_authorized"],
        "host_mysql_cli_required": plan["preflight"]["host_mysql_cli_required"],
        "docker_socket_mount_authorized": plan["preflight"]["docker_socket_mount_authorized"],
        "command_plan": plan,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(validate(args.bundle, args.spec), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, ValidationError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)

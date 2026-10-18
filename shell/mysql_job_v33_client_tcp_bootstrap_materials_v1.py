#!/usr/bin/env python3
"""Create one local-only qualification material set for JOB v33.

This binds only the client-internal-TCP successor.  It neither connects to a
host nor starts Docker, MySQL, a workload, R0, or a formal round.
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

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import render_mysql_job_v32_bootstrap_spec_tmpfs_v2 as renderer
import validate_mysql_job_v32_bootstrap_spec_tmpfs_v2 as validator


ROOT = HERE.parent
BUNDLE_ID = "9fe3ae69e07d2a445ffba52b4b033bee436d34c5b61afef09de6c6c235f433e6"
MANIFEST_SHA256 = "cd76e59d702448389b03f729f85b3c8b29e940ea79eb9e290e357251758e8d65"
DEFAULT_BUNDLE = ROOT / "artifacts/mysql_job_v33_client_tcp_readiness_successor" / BUNDLE_ID
DEFAULT_OUTPUT_ROOT = ROOT / "artifacts/mysql_job_v33_client_tcp_bootstrap_materials_v1"
MYSQL_REFERENCE = "mysql@sha256:a3dff78d876222746a0bacc36dd7e4bf9e673c85fb7ee0d12ed25bd32c43c19b"
MYSQL_IMAGE_ID = "sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
VALID_MINUTES = 30


class MaterialError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_new(path, payload, mode=0o444):
    path = Path(path)
    sidecar = Path(str(path) + ".sha256")
    if path.exists() or path.is_symlink() or sidecar.exists() or sidecar.is_symlink():
        raise MaterialError("refusing to overwrite sealed material: " + path.name)
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    sidecar.write_text("%s  %s\n" % (hashlib.sha256(payload).hexdigest(), path.name),
                       encoding="ascii")
    os.chmod(path, mode)
    os.chmod(sidecar, mode)


def load(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise MaterialError("absent or symlinked: " + path.name)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MaterialError("invalid JSON: " + path.name) from exc
    if not isinstance(value, dict):
        raise MaterialError("JSON object required: " + path.name)
    return value


def verify_bundle(bundle):
    bundle = Path(bundle).resolve(strict=True)
    manifest_path = bundle / "bundle_manifest.json"
    sidecar = Path(str(manifest_path) + ".sha256")
    if (bundle.name != BUNDLE_ID or bundle.is_symlink() or manifest_path.is_symlink()
            or sidecar.is_symlink() or not manifest_path.is_file() or not sidecar.is_file()
            or sha256(manifest_path) != MANIFEST_SHA256
            or sidecar.read_text(encoding="ascii") != "%s  %s\n" % (MANIFEST_SHA256, manifest_path.name)):
        raise MaterialError("JOB v33 manifest binding differs")
    manifest = load(manifest_path)
    if (manifest.get("bundle_id") != BUNDLE_ID or manifest.get("formal_scope") != "mysql_only"
            or manifest.get("cross_engine_comparison_prohibited") is not True
            or manifest.get("postgresql_mutation_prohibited") is not True
            or manifest.get("formal_launch_authorized") is not False
            or manifest.get("accepted_round_authorized") is not False):
        raise MaterialError("JOB v33 scope/authorization boundary differs")
    unsigned = dict(manifest)
    if hashlib.sha256(canonical({key: value for key, value in unsigned.items() if key != "bundle_id"})).hexdigest() != BUNDLE_ID:
        raise MaterialError("JOB v33 content-addressed identity differs")
    files = manifest.get("files") or {}
    required = (
        "qualification/mysql_job_distributed_live_readback_qualification_v2.py",
        "qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py",
        "distributed_runtime_contract.json",
        "authority/mysql_job_v32_ea888_client_tcp_readiness_invalidation_20260731.json",
    )
    for relative in required:
        candidate = bundle / relative
        if candidate.is_symlink() or not candidate.is_file() or files.get(relative) != sha256(candidate):
            raise MaterialError("JOB v33 closure differs: " + relative)
    invalidation = load(bundle / required[-1])
    if (invalidation.get("bundle_id") != "ea8880956d5a08edfe95a9883c2d236b105774da7a450adfae8f60e238edbfa3"
            or invalidation.get("accepted_rounds_credit") != 0
            or invalidation.get("replay_or_rerun_authorized") is not False):
        raise MaterialError("ea888 zero-credit invalidation differs")
    collector = (bundle / required[0]).read_text(encoding="utf-8")
    if ("def client_tcp_mysql_readiness" not in collector
            or "PASS_EXACT_CLIENT_INTERNAL_TCP_MYSQL_READINESS" not in collector
            or "def port_probe" in collector):
        raise MaterialError("client-internal TCP readiness closure differs")
    return bundle


def verify_materials(directory):
    directory = Path(directory).resolve(strict=True)
    materials = load(directory / "materials_manifest.json")
    for name, expected in (materials.get("files") or {}).items():
        path = directory / name
        if path.is_symlink() or not path.is_file() or sha256(path) != expected:
            raise MaterialError("material digest differs: " + name)
    token = directory / "main_agent_token"
    if stat.S_IMODE(token.stat().st_mode) != 0o600:
        raise MaterialError("main-agent token mode differs")
    spec = load(directory / "bootstrap_spec.json")
    if (materials.get("status") != "SEALED_QUALIFICATION_ONLY_NOT_EXECUTED"
            or materials.get("bundle_id") != BUNDLE_ID
            or spec.get("bundle", {}).get("bundle_id") != BUNDLE_ID
            or spec.get("bundle", {}).get("manifest_sha256") != MANIFEST_SHA256
            or spec.get("formal_scope") != "mysql_only"
            or spec.get("postgresql_mutation_prohibited") is not True
            or spec.get("qualification_only") is not True
            or any(spec.get(key) is not False for key in ("formal_r0_authorized", "formal_authorized", "accepted_round_authorized"))):
        raise MaterialError("material authority boundary differs")
    expires = datetime.fromisoformat(spec["expires_at_utc"].replace("Z", "+00:00"))
    if expires.tzinfo is None or expires.astimezone(timezone.utc) <= datetime.now(timezone.utc):
        raise MaterialError("materials are stale")
    return {"status": "PASS_VERIFIED_UNEXPIRED", "attempt_token": spec["attempt_token"],
            "expires_at_utc": spec["expires_at_utc"], "materials_directory": str(directory)}


def create(bundle, output_root):
    bundle = verify_bundle(bundle)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    attempt = "job-v2-" + now.strftime("%Y%m%dt%H%M%Sz").lower() + "-" + secrets.token_hex(12)
    if renderer.ATTEMPT.fullmatch(attempt) is None:
        raise MaterialError("attempt token differs")
    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    final = output_root / attempt
    if final.exists() or final.is_symlink():
        raise MaterialError("attempt output already exists")
    stage = output_root / ("." + attempt + "." + secrets.token_hex(8) + ".tmp")
    stage.mkdir(mode=0o700)
    token = stage / "main_agent_token"
    descriptor = os.open(str(token), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, ("job-v33-main-agent-" + secrets.token_urlsafe(36) + "\n").encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    spec_path = stage / "bootstrap_spec.json"
    args = argparse.Namespace(bundle_manifest=bundle / "bundle_manifest.json",
        mysql_image_reference=MYSQL_REFERENCE, mysql_image_id=MYSQL_IMAGE_ID,
        client_image_reference=MYSQL_REFERENCE, client_image_id=MYSQL_IMAGE_ID,
        main_agent_token_file=token, attempt_token=attempt, valid_minutes=VALID_MINUTES,
        output=spec_path)
    spec = renderer.render(args)
    renderer.atomic(spec_path, renderer.canonical(spec))
    renderer.atomic(Path(str(spec_path) + ".sha256"),
        (hashlib.sha256(renderer.canonical(spec)).hexdigest() + "  " + spec_path.name + "\n").encode("ascii"))
    validation = validator.validate(bundle, spec_path)
    plan_path = stage / "command_plan.json"
    plan = {"schema_version": "mysql_job_v33_client_tcp_qualification_plan_v1",
            "status": "SEALED_PLAN_NOT_EXECUTED", "bundle_id": BUNDLE_ID,
            "bundle_manifest_sha256": MANIFEST_SHA256, "attempt_token": attempt,
            "bootstrap_spec_sha256": sha256(spec_path), "validation": validation,
            "client_internal_tcp_readiness_required": True,
            "host_port_probe_authorized": False, "qualification_only": True,
            "formal_r0_authorized": False, "formal_authorized": False,
            "accepted_round_authorized": False}
    write_new(plan_path, canonical(plan))
    materials = {"schema_version": "mysql_job_v33_client_tcp_bootstrap_materials_v1",
                 "status": "SEALED_QUALIFICATION_ONLY_NOT_EXECUTED", "bundle_id": BUNDLE_ID,
                 "bundle_manifest_sha256": MANIFEST_SHA256, "attempt_token": attempt,
                 "created_at_utc": spec["created_at_utc"], "expires_at_utc": spec["expires_at_utc"],
                 "valid_minutes": VALID_MINUTES,
                 "files": {"bootstrap_spec.json": sha256(spec_path), "command_plan.json": sha256(plan_path), "main_agent_token": sha256(token)},
                 "main_agent_token_mode": "0600", "qualification_only": True,
                 "formal_r0_authorized": False, "formal_authorized": False,
                 "accepted_round_authorized": False}
    write_new(stage / "materials_manifest.json", canonical(materials))
    verify_materials(stage)
    os.rename(stage, final)
    return verify_materials(final)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--verify-materials", type=Path)
    args = parser.parse_args()
    result = verify_materials(args.verify_materials) if args.verify_materials else create(args.bundle, args.output_root)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (MaterialError, OSError, ValueError, renderer.RenderError, validator.ValidationError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)

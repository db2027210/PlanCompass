#!/usr/bin/env python3
"""Create short-lived qualification-only JOB v35 bootstrap materials.

This tool is local-only.  It binds one unexecuted material set to the exact
v35 content address and records the distinct 65102 TCP target, 3306 server
identity, root authentication identity, and database-counter target.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import stat
import sys


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
BASE_PATH = HERE / "mysql_job_v33_client_tcp_bootstrap_materials_v1.py"
BUNDLE_ID = "189822803ff9c2721f17fbe6ef92c9af6ec0e4e522b478df627f9b151b5586cb"
MANIFEST_SHA256 = (
    "a95443f5be6b65e046a2bd8f6a2c6b549eea04da7853cda3eb8266111f4d6de4"
)
DEFAULT_BUNDLE = ROOT / "artifacts/mysql_job_v35_server_port_successor" / BUNDLE_ID
DEFAULT_OUTPUT_ROOT = (
    ROOT / "artifacts/mysql_job_v35_server_port_bootstrap_materials_v1"
)
INVALIDATION_RELATIVE = (
    "authority/mysql_job_v34_e7d_server_port_readiness_invalidation_20260731.json"
)
COLLECTOR_RELATIVE = (
    "qualification/mysql_job_distributed_live_readback_qualification_v2.py"
)
HOST_PORT = 65102
SERVER_PORT = 3306
AUTHENTICATED_USER = "root"
VALID_MINUTES = 30


class MaterialError(RuntimeError):
    pass


def base_module():
    spec = importlib.util.spec_from_file_location("job_v33_materials", BASE_PATH)
    if spec is None or spec.loader is None:
        raise MaterialError("base material generator is not importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical(value):
    return (
        json.dumps(
            value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise MaterialError("absent or symlinked: %s" % path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise MaterialError("JSON object required: %s" % path)
    return value


def write_new(path, payload, mode=0o444):
    path = Path(path)
    sidecar = Path(str(path) + ".sha256")
    if path.exists() or path.is_symlink() or sidecar.exists() or sidecar.is_symlink():
        raise MaterialError("refusing to overwrite sealed material: " + path.name)
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    sidecar.write_text(
        "%s  %s\n" % (hashlib.sha256(payload).hexdigest(), path.name),
        encoding="ascii",
    )
    os.chmod(path, mode)
    os.chmod(sidecar, mode)


def verify_bundle(bundle):
    bundle = Path(bundle).resolve(strict=True)
    manifest_path = bundle / "bundle_manifest.json"
    sidecar = Path(str(manifest_path) + ".sha256")
    if (
        bundle.name != BUNDLE_ID
        or bundle.is_symlink()
        or manifest_path.is_symlink()
        or sidecar.is_symlink()
        or not manifest_path.is_file()
        or not sidecar.is_file()
        or sha256(manifest_path) != MANIFEST_SHA256
        or sidecar.read_text(encoding="ascii")
        != "%s  bundle_manifest.json\n" % MANIFEST_SHA256
    ):
        raise MaterialError("JOB v35 manifest binding differs")
    manifest = load(manifest_path)
    unsigned = {key: value for key, value in manifest.items() if key != "bundle_id"}
    contract = manifest.get("client_tcp_readiness_contract") or {}
    if (
        manifest.get("bundle_id") != BUNDLE_ID
        or hashlib.sha256(canonical(unsigned)).hexdigest() != BUNDLE_ID
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("postgresql_mutation_prohibited") is not True
        or manifest.get("qualification_authorized") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or contract.get("tcp_target_port") != HOST_PORT
        or contract.get("server_mysql_port") != SERVER_PORT
        or contract.get("authenticated_user") != AUTHENTICATED_USER
        or contract.get("database_counter_tcp_target_port") != HOST_PORT
        or contract.get("full_client_identity_before_and_after") is not True
    ):
        raise MaterialError("JOB v35 scope/port/authority contract differs")
    files = manifest.get("files") or {}
    for relative in (COLLECTOR_RELATIVE, INVALIDATION_RELATIVE, "closure.json"):
        path = bundle / relative
        if (
            path.is_symlink()
            or not path.is_file()
            or files.get(relative) != sha256(path)
        ):
            raise MaterialError("JOB v35 closure differs: " + relative)
    invalidation = load(bundle / INVALIDATION_RELATIVE)
    if (
        invalidation.get("status") != "INVALIDATED_DO_NOT_RERUN"
        or invalidation.get("accepted_rounds_credit") != 0
        or invalidation.get("capture_credit") != 0
        or invalidation.get("qualification_credit") != 0
        or invalidation.get("replay_or_rerun_authorized") is not False
    ):
        raise MaterialError("JOB v34 zero-credit invalidation differs")
    collector = (bundle / COLLECTOR_RELATIVE).read_text(encoding="utf-8")
    required_source = (
        "transport, host_port, server_port, seconds=120",
        'host_port != 65102',
        'server_port != 3306',
        'rows != ["%d\\troot" % server_port]',
        '"--port=%d" % host_port',
        "verified_after_id = _verified_database_counter_client(transport)",
        "database_counter_transport, port, 3306",
    )
    if any(fragment not in collector for fragment in required_source):
        raise MaterialError("JOB v35 dual-port collector differs")
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
    plan = load(directory / "command_plan.json")
    contract = plan.get("client_tcp_readiness_contract") or {}
    if (
        materials.get("status") != "SEALED_QUALIFICATION_ONLY_NOT_EXECUTED"
        or materials.get("bundle_id") != BUNDLE_ID
        or spec.get("bundle", {}).get("bundle_id") != BUNDLE_ID
        or spec.get("bundle", {}).get("manifest_sha256") != MANIFEST_SHA256
        or spec.get("formal_scope") != "mysql_only"
        or spec.get("postgresql_mutation_prohibited") is not True
        or spec.get("qualification_only") is not True
        or any(
            spec.get(key) is not False
            for key in (
                "formal_r0_authorized",
                "formal_authorized",
                "accepted_round_authorized",
            )
        )
        or contract.get("tcp_target_port") != HOST_PORT
        or contract.get("server_mysql_port") != SERVER_PORT
        or contract.get("authenticated_user") != AUTHENTICATED_USER
        or contract.get("database_counter_tcp_target_port") != HOST_PORT
        or contract.get("full_client_identity_before_and_after") is not True
    ):
        raise MaterialError("material authority/dual-port boundary differs")
    expires = datetime.fromisoformat(spec["expires_at_utc"].replace("Z", "+00:00"))
    if expires.tzinfo is None or expires.astimezone(timezone.utc) <= datetime.now(
        timezone.utc
    ):
        raise MaterialError("materials are stale")
    return {
        "status": "PASS_VERIFIED_UNEXPIRED",
        "attempt_token": spec["attempt_token"],
        "expires_at_utc": spec["expires_at_utc"],
        "materials_directory": str(directory),
    }


def create(bundle, output_root):
    base = base_module()
    bundle = verify_bundle(bundle)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    attempt = (
        "job-v2-"
        + now.strftime("%Y%m%dt%H%M%Sz").lower()
        + "-"
        + secrets.token_hex(12)
    )
    if base.renderer.ATTEMPT.fullmatch(attempt) is None:
        raise MaterialError("attempt token differs")
    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    final = output_root / attempt
    if final.exists() or final.is_symlink():
        raise MaterialError("attempt output already exists")
    stage = output_root / (".%s.%s.tmp" % (attempt, secrets.token_hex(8)))
    stage.mkdir(mode=0o700)
    try:
        token = stage / "main_agent_token"
        descriptor = os.open(
            str(token), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        try:
            os.write(
                descriptor,
                (
                    "job-v35-main-agent-" + secrets.token_urlsafe(36) + "\n"
                ).encode("ascii"),
            )
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        spec_path = stage / "bootstrap_spec.json"
        args = argparse.Namespace(
            bundle_manifest=bundle / "bundle_manifest.json",
            mysql_image_reference=base.MYSQL_REFERENCE,
            mysql_image_id=base.MYSQL_IMAGE_ID,
            client_image_reference=base.MYSQL_REFERENCE,
            client_image_id=base.MYSQL_IMAGE_ID,
            main_agent_token_file=token,
            attempt_token=attempt,
            valid_minutes=VALID_MINUTES,
            output=spec_path,
        )
        spec = base.renderer.render(args)
        spec_payload = base.renderer.canonical(spec)
        base.renderer.atomic(spec_path, spec_payload)
        base.renderer.atomic(
            Path(str(spec_path) + ".sha256"),
            (
                hashlib.sha256(spec_payload).hexdigest()
                + "  "
                + spec_path.name
                + "\n"
            ).encode("ascii"),
        )
        validation = base.validator.validate(bundle, spec_path)
        plan_path = stage / "command_plan.json"
        plan = {
            "schema_version":
                "mysql_job_v35_server_port_qualification_plan_v1",
            "status": "SEALED_PLAN_NOT_EXECUTED",
            "bundle_id": BUNDLE_ID,
            "bundle_manifest_sha256": MANIFEST_SHA256,
            "attempt_token": attempt,
            "bootstrap_spec_sha256": sha256(spec_path),
            "validation": validation,
            "client_tcp_readiness_contract": {
                "tcp_target_host": "127.0.0.1",
                "tcp_target_port": HOST_PORT,
                "server_mysql_port": SERVER_PORT,
                "authenticated_user": AUTHENTICATED_USER,
                "database_counter_tcp_target_port": HOST_PORT,
                "full_client_identity_before_and_after": True,
            },
            "client_internal_tcp_readiness_required": True,
            "host_port_probe_authorized": False,
            "qualification_only": True,
            "formal_r0_authorized": False,
            "formal_authorized": False,
            "accepted_round_authorized": False,
        }
        write_new(plan_path, canonical(plan))
        materials = {
            "schema_version":
                "mysql_job_v35_server_port_bootstrap_materials_v1",
            "status": "SEALED_QUALIFICATION_ONLY_NOT_EXECUTED",
            "bundle_id": BUNDLE_ID,
            "bundle_manifest_sha256": MANIFEST_SHA256,
            "attempt_token": attempt,
            "created_at_utc": spec["created_at_utc"],
            "expires_at_utc": spec["expires_at_utc"],
            "valid_minutes": VALID_MINUTES,
            "files": {
                "bootstrap_spec.json": sha256(spec_path),
                "command_plan.json": sha256(plan_path),
                "main_agent_token": sha256(token),
            },
            "main_agent_token_mode": "0600",
            "qualification_only": True,
            "formal_r0_authorized": False,
            "formal_authorized": False,
            "accepted_round_authorized": False,
        }
        write_new(stage / "materials_manifest.json", canonical(materials))
        verify_materials(stage)
        os.rename(stage, final)
    except Exception:
        if stage.exists():
            import shutil
            shutil.rmtree(stage)
        raise
    return verify_materials(final)


def main():
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


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)

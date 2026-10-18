#!/usr/bin/env python3
"""Materialize XP v26 capture argv with an attested post-adoption Python.

This public-only helper performs no remote operation and reads no secret.  It
supersedes capture argv v1, whose argv[0] was the unbound string ``python3``.
The exact interpreter is taken from a sealed node02 compatibility readback,
then its path, hash, version, adoption identity and critical compile closure
are checked before it is used.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys


BUNDLE_ID = "b8e574ea5d2a47faae7cbe1776c7407c6e4b6122b4567c8a9b7dde5513382dcc"
MANIFEST_SHA256 = "1baf26d67479544c2f9aa13e4bd1d9a87fccaec9e6912d0570de3780d896df02"
JAVA_SHA256 = "c8f339ba5d433ef5ecacef4de0640b0a35c99381804413bfbc24375a0410e51c"
RAW_RELATIVE = "launch/mysql_xp_raw_evidence_executor_v1.py"
RAW_SHA256 = "10b176bebd34c2d8e5830f9c829969b2ff61762642a70b7bc83eb98228b0d49a"
COMPATIBILITY_SCHEMA = "mysql_xp_v26_same_live_adoption_successor_v1"
COMPATIBILITY_STATUS = "PASS_STRICT_SAME_LIVE_V26_ADOPTION_COMPATIBILITY"
SAFE_ID = re.compile(r"[A-Za-z0-9_.-]{16,128}")


class PlanError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sealed(path: Path, label: str) -> dict:
    path = Path(path)
    sidecar = path.with_name(path.name + ".sha256")
    if path.is_symlink() or not path.is_file() or sidecar.is_symlink() or not sidecar.is_file():
        raise PlanError(label + " is not sealed")
    if sidecar.read_text(encoding="ascii") != "%s  %s\n" % (sha256(path), path.name):
        raise PlanError(label + " sidecar differs")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PlanError(label + " must be an object")
    return value


def load_materializer(bundle: Path):
    path = bundle / "launch/mysql_xp_node02_formal10_materializer_v1.py"
    spec = importlib.util.spec_from_file_location("xp_v26_materializer_v2", path)
    if spec is None or spec.loader is None:
        raise PlanError("bundle materializer cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    old = list(sys.path)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = old
    return module


def fresh_child(root: Path, path: Path, label: str) -> Path:
    root = root.resolve()
    path = path.resolve()
    if path == root or root not in path.parents or path.exists() or path.is_symlink():
        raise PlanError(label + " must be fresh inside adopted host tmpfs")
    return path


def atomic_json(path: Path, value) -> None:
    if path.exists() or path.is_symlink():
        raise PlanError("capture argv output must be fresh")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, sort_keys=False, indent=2) + "\n").encode("utf-8")
    temporary = path.with_name("." + path.name + ".%d" % os.getpid())
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        sha256(path) + "  " + path.name + "\n", encoding="ascii")


def verified_python(compatibility_path: Path, adoption_path: Path,
                    bundle: Path, identity: dict) -> Path:
    value = sealed(compatibility_path, "same-live compatibility readback")
    python = value.get("post_adoption_python") or {}
    compiled = python.get("critical_compile_sha256") or {}
    executable_text = python.get("path")
    executable = Path(executable_text) if isinstance(executable_text, str) else Path("")
    if (value.get("schema_version") != COMPATIBILITY_SCHEMA
            or value.get("status") != COMPATIBILITY_STATUS
            or value.get("target_bundle_id") != BUNDLE_ID
            or value.get("target_manifest_sha256") != MANIFEST_SHA256
            or value.get("source_adoption_bundle_id") != BUNDLE_ID
            or value.get("source_adoption_sha256") != sha256(adoption_path)
            or value.get("client_container_id") != identity.get("client_container_id")
            or value.get("database_container_id") != identity.get("database_container_id")
            or value.get("same_live_pid_starttime_pass") is not True
            or value.get("v26_adoption_materialized") is not True
            or value.get("capture_r0_compatibility_ready") is not True
            or python.get("version_pass") is not True
            or tuple(python.get("version") or ()) < (3, 7, 0)
            or not executable.is_absolute()
            or executable.is_symlink()
            or not executable.is_file()
            or sha256(executable) != python.get("sha256")
            or compiled.get(RAW_RELATIVE) != RAW_SHA256
            or sha256(bundle / RAW_RELATIVE) != RAW_SHA256):
        raise PlanError("post-adoption Python/adoption/compile identity differs")
    return executable


def materialize(bundle_path: Path, adoption_path: Path, compatibility_path: Path,
                formal_readback: Path, runtime_authority: Path,
                capture_authorization: Path, capture_id: str,
                capture_root: Path, transition_root: Path, output: Path) -> dict:
    bundle = bundle_path.resolve(strict=True)
    if bundle.name != BUNDLE_ID or sha256(bundle / "bundle_manifest.json") != MANIFEST_SHA256:
        raise PlanError("XP v26 bundle identity differs")
    materializer = load_materializer(bundle)
    manifest = materializer.verify_bundle(bundle)
    adoption, identity = materializer.verify_adoption(adoption_path, manifest["bundle_id"])
    interpreter = verified_python(compatibility_path, adoption_path, bundle, identity)
    if SAFE_ID.fullmatch(capture_id or "") is None:
        raise PlanError("capture identity differs")
    host_tmpfs = Path((adoption.get("host_output_tmpfs") or {}).get("path") or "")
    capture_root = fresh_child(host_tmpfs, capture_root, "capture root")
    transition_root = fresh_child(host_tmpfs, transition_root, "transition root")
    output = fresh_child(host_tmpfs, output, "argv output")
    for path, label in ((formal_readback, "formal readback"),
                        (runtime_authority, "runtime authority"),
                        (capture_authorization, "capture authorization")):
        if path.is_symlink() or not path.is_file():
            raise PlanError(label + " must be a sealed public file")
    runtime_root = Path(adoption["runtime_publication"]["live_tree_readback"]["root"])
    java = runtime_root / "bin/java"
    if java.is_symlink() or not java.is_file() or sha256(java) != JAVA_SHA256:
        raise PlanError("published Java identity differs")
    argv = [
        str(interpreter), str(bundle / RAW_RELATIVE),
        "--mode", "capture", "--bundle", str(bundle),
        "--java", str(java), "--java-sha256", JAVA_SHA256,
        "--properties", str(bundle / "qualification/config/hybench_xp_qualification.properties"),
        "--formal-readback", str(formal_readback.resolve()),
        "--capture-readback", str(transition_root / "capture_readback.json"),
        "--stop-file", "/measurement/hybench-stop-request",
        "--trace", str(capture_root / "production_trace.jsonl"),
        "--trace-run-id", capture_id,
        "--client-container", identity["client_container_id"],
        "--runtime-authority-sha256", sha256(runtime_authority),
        "--capture-authorization-sha256", sha256(capture_authorization),
        "--output", str(capture_root / "raw_executor_capture.json"),
    ]
    if any("main_agent_token" in item or "private_credentials" in item
           or "/run/mysql-xp-private" in item for item in argv):
        raise PlanError("private material leaked into public capture argv")
    atomic_json(output, argv)
    return {
        "status": "MATERIALIZED_PYTHON_ATTESTED_PUBLIC_CAPTURE_ARGV_NOT_LAUNCHED",
        "bundle_id": BUNDLE_ID,
        "adopted_service_sha256": sha256(adoption_path),
        "compatibility_readback_sha256": sha256(compatibility_path),
        "post_adoption_python": str(interpreter),
        "post_adoption_python_sha256": sha256(interpreter),
        "capture_id": capture_id,
        "argv_json": str(output),
        "argv_json_sha256": sha256(output),
        "private_material_read": False,
        "private_material_transferred": False,
        "data_plane_started": False,
        "accepted_rounds_credit": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--adopted-service", type=Path, required=True)
    parser.add_argument("--compatibility-readback", type=Path, required=True)
    parser.add_argument("--formal-readback", type=Path, required=True)
    parser.add_argument("--runtime-authority", type=Path, required=True)
    parser.add_argument("--capture-authorization", type=Path, required=True)
    parser.add_argument("--capture-id", required=True)
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--transition-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = materialize(args.bundle, args.adopted_service,
        args.compatibility_readback, args.formal_readback, args.runtime_authority,
        args.capture_authorization, args.capture_id, args.capture_root,
        args.transition_root, args.output)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, PlanError, json.JSONDecodeError) as exc:
        raise SystemExit("NOT_READY: " + str(exc))

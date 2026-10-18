#!/usr/bin/env python3
"""Materialize the public, attempt-bound XP v26 capture executor argv.

This offline helper neither reads secret files nor starts a workload.  The
bundle transition remains the authority: it revalidates this argv, switches
only the adopted database blkio cgroup, invokes the bundle executor, and
restores the formal envelope in ``finally``.
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
SAFE_ID = re.compile(r"[A-Za-z0-9_.-]{16,128}")


class PlanError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_materializer(bundle: Path):
    path = bundle / "launch/mysql_xp_node02_formal10_materializer_v1.py"
    spec = importlib.util.spec_from_file_location("xp_v26_materializer", path)
    if spec is None or spec.loader is None:
        raise PlanError("bundle materializer cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    old = list(sys.path); sys.path.insert(0, str(path.parent))
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
        stream.write(payload); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    sidecar = path.with_name(path.name + ".sha256")
    sidecar.write_text(sha256(path) + "  " + path.name + "\n", encoding="ascii")


def materialize(bundle_path: Path, adoption_path: Path, formal_readback: Path,
                runtime_authority: Path, capture_authorization: Path,
                capture_id: str, capture_root: Path, transition_root: Path,
                output: Path) -> dict:
    bundle = bundle_path.resolve(strict=True)
    if (bundle.name != BUNDLE_ID
            or sha256(bundle / "bundle_manifest.json") != MANIFEST_SHA256):
        raise PlanError("XP v26 bundle identity differs")
    materializer = load_materializer(bundle)
    manifest = materializer.verify_bundle(bundle)
    adoption, identity = materializer.verify_adoption(adoption_path, manifest["bundle_id"])
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
    if (java.is_symlink() or not java.is_file()
            or sha256(java) != JAVA_SHA256):
        raise PlanError("published Java identity differs")
    argv = [
        "python3", str(bundle / "launch/mysql_xp_raw_evidence_executor_v1.py"),
        "--mode", "capture", "--bundle", str(bundle),
        "--java", str(java), "--java-sha256", JAVA_SHA256,
        # The executor validates this public path as a fixed compatibility
        # argument, but v26 Java argv is hard-coded to the private client path.
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
        "status": "MATERIALIZED_PUBLIC_CAPTURE_ARGV_NOT_LAUNCHED",
        "bundle_id": BUNDLE_ID,
        "adopted_service_sha256": sha256(adoption_path),
        "capture_id": capture_id,
        "argv_json": str(output), "argv_json_sha256": sha256(output),
        "private_material_read": False, "private_material_transferred": False,
        "data_plane_started": False, "accepted_rounds_credit": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--adopted-service", type=Path, required=True)
    parser.add_argument("--formal-readback", type=Path, required=True)
    parser.add_argument("--runtime-authority", type=Path, required=True)
    parser.add_argument("--capture-authorization", type=Path, required=True)
    parser.add_argument("--capture-id", required=True)
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--transition-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(materialize(args.bundle, args.adopted_service,
        args.formal_readback, args.runtime_authority, args.capture_authorization,
        args.capture_id, args.capture_root, args.transition_root, args.output),
        sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, PlanError) as exc:
        raise SystemExit("NOT_READY: " + str(exc))

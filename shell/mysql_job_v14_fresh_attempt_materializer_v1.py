#!/usr/bin/env python3
"""Create a fresh short-lived JOB v14 solo-qualification authority.

Control-plane only: this writes a new attempt identifier, private token,
command, authority and launch descriptor.  It never executes the launch.
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
from typing import Any, Mapping

TARGET_ID = "659b121c32f1866d4fe2a07ec2aa5857c81578f8b75cfafedf04654cb3b4bcef"
TARGET_MANIFEST_SHA256 = "f6018261584f06ea9a8968357db26b2c3da6ec019a31bb295530a2979f14b406"
CONTROL_SCHEMA = "mysql_job_v14_duration_control_successor_v1"
PREDECESSOR_CONTROL_ID = "6b55e29ef3c47cca437c94501511317c6c79dd1579be3dc6e3a4983587245f74"
PREDECESSOR_CONTROL_MANIFEST_SHA256 = "5c5e59098304942800ef44dc17c777ffa6df0a81261a275b526a9737474c77ba"
FAILED_MARKER = "6b55-20260803t0427"
FAILED_UNIT = "ra-mysql-job-qual-solo-6b55-20260803t0427.service"


class FreshAttemptError(RuntimeError): pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_support(path: Path) -> tuple[Path, Mapping[str, Any]]:
    root = path.resolve(strict=True)
    manifest_path = root / "bundle_manifest.json"
    if path.is_symlink() or manifest_path.is_symlink() or not manifest_path.is_file():
        raise FreshAttemptError("support bundle path differs")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    unsigned = dict(manifest); bundle_id = unsigned.pop("bundle_id", None)
    if (
        bundle_id != root.name
        or manifest.get("schema_version") != CONTROL_SCHEMA
        or hashlib.sha256(canonical(unsigned)).hexdigest() != bundle_id
        or manifest.get("target_job_bundle_id") != TARGET_ID
        or manifest.get("target_job_manifest_sha256") != TARGET_MANIFEST_SHA256
        or manifest.get("predecessor_support_bundle_id") != PREDECESSOR_CONTROL_ID
        or manifest.get("predecessor_support_manifest_sha256") != PREDECESSOR_CONTROL_MANIFEST_SHA256
        or manifest.get("failed_attempt_reusable") is not False
    ):
        raise FreshAttemptError("support bundle identity differs")
    actual = set()
    for relative, expected in manifest.get("files", {}).items():
        candidate = root / relative
        if candidate.is_symlink() or not candidate.is_file() or digest(candidate) != expected:
            raise FreshAttemptError("support closure differs at " + relative)
        actual.add(relative)
    observed = {
        item.relative_to(root).as_posix() for item in root.rglob("*")
        if item.is_file() and item.name not in {"bundle_manifest.json", "bundle_manifest.json.sha256"}
    }
    if observed != actual:
        raise FreshAttemptError("support exact entry set differs")
    return root, manifest


def fresh_qualification_attempt_id(now: datetime | None = None) -> str:
    clock = now or datetime.now(timezone.utc)
    value = "job-v14-fresh-qual-%s-%s" % (
        clock.strftime("%Y%m%dT%H%M%SZ"), secrets.token_hex(16)
    )
    if FAILED_MARKER in value or len(value) < 32:
        raise FreshAttemptError("fresh qualification attempt generation failed")
    return value


def load_authority(root: Path):
    path = root / "controller/mysql_job_v13_qualification_authority_v2.py"
    spec = importlib.util.spec_from_file_location("_job_v14_authority", path)
    if spec is None or spec.loader is None:
        raise FreshAttemptError("cannot load sealed v14 authority")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def write_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise FreshAttemptError("refusing to overwrite fresh attempt material")
    payload = canonical(value)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload); stream.flush(); os.fsync(stream.fileno())


def materialize(args: argparse.Namespace) -> Mapping[str, Any]:
    root, manifest = verify_support(args.support_bundle)
    if args.ttl_seconds < 60 or args.ttl_seconds > 900:
        raise FreshAttemptError("fresh qualification TTL must be 60..900 seconds")
    for path in (args.bootstrap_spec, args.bootstrap_evidence):
        if path.is_symlink() or not path.is_file() or FAILED_MARKER in path.read_text(encoding="utf-8"):
            raise FreshAttemptError("failed 6b55 attempt material is prohibited")
    raw_spec = json.loads(args.bootstrap_spec.read_text(encoding="utf-8"))
    if (raw_spec.get("bundle") or {}).get("bundle_id") != TARGET_ID:
        raise FreshAttemptError("bootstrap does not bind JOB v14")
    output = args.output_directory
    if output.exists() or output.is_symlink():
        raise FreshAttemptError("fresh attempt output already exists")
    output.mkdir(parents=True, mode=0o700)
    qualification_id = fresh_qualification_attempt_id()
    authority = load_authority(root)
    authority_args = argparse.Namespace(
        bootstrap_spec=args.bootstrap_spec,
        bootstrap_evidence=args.bootstrap_evidence,
        shared_dispatch=root / "qualification/mysql_job_xp_shared_qualification_dispatch_v1.py",
        mode="qualification_solo",
        qualification_attempt_id=qualification_id,
        output_directory=output / "qualification_authority",
        ttl_seconds=args.ttl_seconds,
        peer_scenario=None, peer_qualification_attempt_id=None,
        peer_workload_metric_stream=None, peer_readback=None, peer_container=None,
    )
    result = authority.materialize(authority_args)
    authority_doc = json.loads(Path(result["authority"]).read_text(encoding="utf-8"))
    document = {
        "schema_version": "mysql_job_v14_fresh_attempt_material_v1",
        "status": "MATERIALIZED_FRESH_NOT_EXECUTED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "support_bundle_id": root.name,
        "support_manifest_sha256": digest(root / "bundle_manifest.json"),
        "support_predecessor_bundle_id": PREDECESSOR_CONTROL_ID,
        "target_bundle_id": TARGET_ID,
        "target_manifest_sha256": TARGET_MANIFEST_SHA256,
        "qualification_attempt_id": qualification_id,
        "failed_unit_denylist": [FAILED_UNIT],
        "failed_attempt_marker_denylist": [FAILED_MARKER],
        "failed_attempt_reusable": False,
        "main_agent_token_file": str(output / "qualification_authority/main_agent_token"),
        "launch": result["launch"],
        "authority": result["authority"],
        "expires_epoch": authority_doc["expires_epoch"],
        "debug_only": True,
        "formal_r0_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
    }
    path = output / "fresh_attempt_material.json"
    write_exclusive(path, document)
    return {"material": str(path), **result}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--support-bundle", type=Path, required=True)
    parser.add_argument("--bootstrap-spec", type=Path, required=True)
    parser.add_argument("--bootstrap-evidence", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--ttl-seconds", type=int, default=900)
    args = parser.parse_args()
    print(json.dumps(materialize(args), sort_keys=True))
    return 0


if __name__ == "__main__":
    try: raise SystemExit(main())
    except (FreshAttemptError, OSError, ValueError) as exc: raise SystemExit("NOT_READY: %s" % exc)

#!/usr/bin/env python3
"""Render one short-lived, sidecar-bound JOB v32 bootstrap spec locally.

This controller utility does not connect to node02 and never starts a process
outside its own local output file.  Image identities and the main-agent token
are supplied at the launch boundary; only the token digest is persisted.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys


RUNTIME_ROOT = "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/runtime_algorithm_c_successor_20260729"
PYTHON_TREE = "3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89"
CONTRACT_SHA = "52ba2faa2035dfa9035cb0f6ab75d4461430000097f846c3e94e4a81f99aea58"
RECONCILIATION_SHA = "96beed0c59d0f86ac8c4b517ce94a5af460be2b14089a3d40583a3ef6813df82"
HEX64 = re.compile(r"[0-9a-f]{64}$")
ATTEMPT = re.compile(r"job-v2-[a-z0-9][a-z0-9_.-]{24,95}$")


class RenderError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".%s.%s.tmp" % (path.name, secrets.token_hex(8)))
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(temporary), str(path))


def render(args) -> dict:
    manifest_path = args.bundle_manifest.resolve(strict=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bundle_id = manifest.get("bundle_id")
    if (
        not isinstance(bundle_id, str)
        or HEX64.fullmatch(bundle_id) is None
        or manifest_path.parent.name != bundle_id
    ):
        raise RenderError("local bundle manifest identity differs")
    for label, value in (("mysql image ID", args.mysql_image_id), ("client image ID", args.client_image_id)):
        if not isinstance(value, str) or not value.startswith("sha256:") or HEX64.fullmatch(value[7:]) is None:
            raise RenderError(label + " must be an image SHA-256 ID")
    attempt = args.attempt_token or ("job-v2-" + datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%Sz").lower() + "-" + secrets.token_hex(12))
    if ATTEMPT.fullmatch(attempt) is None:
        raise RenderError("attempt token differs")
    suffix = re.sub(r"[^a-z0-9]", "_", attempt)[:48]
    attempt_root = RUNTIME_ROOT + "/attempts/" + attempt
    bundle_path = RUNTIME_ROOT + "/bundles/" + bundle_id
    python_root = RUNTIME_ROOT + "/runtime/python311-" + PYTHON_TREE
    raw_token_path = args.main_agent_token_file
    if raw_token_path.is_symlink() or not raw_token_path.is_file():
        raise RenderError("main-agent token file is absent or symlinked")
    token_path = raw_token_path.resolve(strict=True)
    token_mode = stat.S_IMODE(token_path.stat().st_mode)
    if token_mode & 0o077:
        raise RenderError("main-agent token file is symlinked or too permissive")
    token = token_path.read_text(encoding="utf-8").strip()
    if not token:
        raise RenderError("main-agent token file is empty")
    now = datetime.now(timezone.utc)
    document = {
        "schema_version": "mysql_job_distributed_v2_bootstrap_spec_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "scenario": "job_memory", "host_id": "node02", "attempt_token": attempt,
        "bundle": {"path": bundle_path, "bundle_id": bundle_id, "manifest_sha256": sha256(manifest_path)},
        "contract_sha256": CONTRACT_SHA, "reconciliation_sha256": RECONCILIATION_SHA,
        "mysql_image": {"reference": args.mysql_image_reference, "image_id": args.mysql_image_id},
        "client_image": {"reference": args.client_image_reference, "image_id": args.client_image_id},
        "paths": {"template": RUNTIME_ROOT.rsplit("/runtime_algorithm", 1)[0] + "/template", "runtime_root": RUNTIME_ROOT, "attempt_root": attempt_root, "datadir": attempt_root + "/datadir", "credentials_dir": attempt_root + "/credentials", "measurement_tmpfs": attempt_root + "/measurement", "python_runtime_root": python_root},
        "containers": {"database": "ra_mysql_job_v2_%s_db" % suffix, "client": "ra_mysql_job_v2_%s_client" % suffix},
        "database_counter_bridge": {"kind": "exact_client_container_docker_exec_stdout_v1", "client_container": "ra_mysql_job_v2_%s_client" % suffix, "mysql_binary_in_client": "/usr/bin/mysql", "stdout_only": True, "docker_socket_mount_prohibited": True, "target_device_write_prohibited": True},
        "python_runtime": {"manifest_relative": "runtime/python_runtime_tree_manifest_v3.json", "manifest_sha256": "4eeab2e0e5302d20a1fbf4a664fc4938abed84c06f3415e11a462b6e616696c4", "tree_content_sha256": PYTHON_TREE, "executable_relative": "bin/python3.11", "executable_path": python_root + "/bin/python3.11", "executable_sha256": "05bd6d6d900bd990b8c00d17df3b3e1425d6c32f0408f07cd29bc495ac4aff39"},
        "mysql_defaults_file": attempt_root + "/credentials/mysql.cnf",
        "collector": bundle_path + "/qualification/mysql_distributed_live_readback_qualification_v1.py",
        "created_at_utc": now.isoformat(), "expires_at_utc": (now + timedelta(minutes=args.valid_minutes)).isoformat(),
        "main_agent_token_sha256": hashlib.sha256(token.encode()).hexdigest(),
        "qualification_only": True, "capture_authorized": False, "formal_r0_authorized": False,
        "formal_authorized": False, "accepted_round_authorized": False,
    }
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-manifest", type=Path, required=True)
    parser.add_argument("--mysql-image-reference", required=True)
    parser.add_argument("--mysql-image-id", required=True)
    parser.add_argument("--client-image-reference", required=True)
    parser.add_argument("--client-image-id", required=True)
    parser.add_argument("--main-agent-token-file", type=Path, required=True)
    parser.add_argument("--attempt-token")
    parser.add_argument("--valid-minutes", type=int, default=45)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.valid_minutes < 40 or args.valid_minutes > 60:
        parser.error("--valid-minutes must be within 40..60")
    document = render(args)
    payload = canonical(document)
    atomic(args.output, payload)
    atomic(args.output.with_name(args.output.name + ".sha256"), hashlib.sha256(payload).hexdigest().encode() + b"  " + args.output.name.encode() + b"\n")
    print(json.dumps({"status": "PASS_RENDERED_QUALIFICATION_ONLY_SPEC", "attempt_token": document["attempt_token"], "bundle_id": document["bundle"]["bundle_id"], "accepted_round_authorized": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RenderError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)

#!/usr/bin/env python3
"""Render a short-lived TPC-H v23-recalibrated qualification spec locally.

This controller-only utility never invokes Docker, a database, workload, or
remote connection.  The output remains qualification-only; the token itself
is read from a private 0600 file and only its SHA-256 is persisted.
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
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE_ID = (
    "afa85e27104fecfb61f0518e722ba8d001595f7e80e10127168d67b9edf1c6bb"
)
DEFAULT_BUNDLE_MANIFEST_SHA256 = (
    "1b88705f9d5c79f4b280d26040a565fe7c1df307628908765a9ef3f78e7f347b"
)
DEFAULT_BUNDLE_MANIFEST = (
    ROOT
    / "artifacts/mysql_tpch_v23_recalibrated_successor_v3"
    / DEFAULT_BUNDLE_ID
    / "bundle_manifest.json"
)
RUNTIME_ROOT = (
    "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/"
    "runtime_algorithm_c_successor_20260729"
)
PYTHON_RUNTIME_SOURCE = "/home/anonymous/recost_runtime_py311_24_11_1"
PYTHON_RUNTIME_TREE_CONTENT_SHA256 = (
    "3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89"
)
PYTHON_RUNTIME_MANIFEST_SHA256 = (
    "4eeab2e0e5302d20a1fbf4a664fc4938abed84c06f3415e11a462b6e616696c4"
)
CONTRACT_SHA256 = (
    "ea0f3a13d44f1f143e63fcd19003f23e90486cfa297df4b07fea99314af82d7b"
)
MYSQL_PORT = 65103
HEX64_RE = re.compile(r"[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}$")
ATTEMPT_RE = re.compile(r"tpch-v2-[a-z0-9][a-z0-9_.-]{24,95}$")


class RenderError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_new(path: Path, payload: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise RenderError(f"refusing to overwrite authority artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.rename(temporary, path)
    descriptor = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _manifest(path: Path) -> dict[str, Any]:
    raw_path = path
    if raw_path.is_symlink() or not raw_path.is_file():
        raise RenderError("bundle manifest is absent or symlinked")
    path = raw_path.resolve(strict=True)
    sidecar = Path(str(path) + ".sha256")
    expected_sidecar = f"{sha256(path)}  {path.name}\n"
    if (
        sidecar.is_symlink()
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii") != expected_sidecar
    ):
        raise RenderError("bundle manifest sidecar differs")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RenderError(f"bundle manifest is invalid: {exc}") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version")
        != "mysql_tpch_v23_recalibrated_successor_v3"
        or not isinstance(manifest.get("bundle_id"), str)
        or HEX64_RE.fullmatch(manifest["bundle_id"]) is None
        or path.parent.name != manifest["bundle_id"]
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("postgresql_mutation_prohibited") is not True
        or HEX64_RE.fullmatch(
            str(manifest.get("historical_v40_invalidation_sha256") or "")
        ) is None
    ):
        raise RenderError("local TPC-H bundle manifest identity differs")
    if (
        manifest["bundle_id"] != DEFAULT_BUNDLE_ID
        or sha256(path) != DEFAULT_BUNDLE_MANIFEST_SHA256
    ):
        raise RenderError(
            "TPC-H bootstrap renderer is bound only to the v23-recalibrated "
            "successor"
        )
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id")
    if hashlib.sha256(canonical(unsigned)).hexdigest() != bundle_id:
        raise RenderError("content-addressed bundle identity differs")
    root_helper = path.parent / "mysql_tpch_client_immutable_identity_v1.py"
    qualification_helper = (
        path.parent
        / "qualification/mysql_tpch_client_immutable_identity_v1.py"
    )
    component_hashes = manifest.get("component_hashes") or {}
    contract_path = path.parent / "tpch_formal10_contract.json"
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RenderError(
            f"client immutable projection contract is invalid: {exc}"
        ) from exc
    identity_contract = contract.get("client_immutable_identity") or {}
    if (
        root_helper.is_symlink()
        or not root_helper.is_file()
        or qualification_helper.is_symlink()
        or not qualification_helper.is_file()
        or sha256(root_helper) != sha256(qualification_helper)
        or component_hashes.get("client_identity_helper_root")
        != sha256(root_helper)
        or component_hashes.get("client_identity_helper_qualification")
        != sha256(qualification_helper)
        or identity_contract.get("projection")
        != "canonical_client_immutable_projection_v1"
        or identity_contract.get("shared_helper_sha256")
        != sha256(root_helper)
        or identity_contract.get(
            "bootstrap_same_live_uses_projection"
        )
        is not True
        or identity_contract.get(
            "command_materializer_uses_projection"
        )
        is not True
        or identity_contract.get(
            "database_counter_transport_uses_projection"
        )
        is not True
        or identity_contract.get(
            "qualification_collector_uses_projection"
        )
        is not True
        or identity_contract.get(
            "docker_exec_state_execids_excluded"
        )
        is not True
    ):
        raise RenderError("client immutable projection closure differs")
    return manifest


def render(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = args.bundle_manifest.resolve(strict=True)
    manifest = _manifest(manifest_path)
    for label, image_id in (
        ("mysql image ID", args.mysql_image_id),
        ("client image ID", args.client_image_id),
    ):
        if IMAGE_ID_RE.fullmatch(str(image_id)) is None:
            raise RenderError(f"{label} must be a SHA-256 image ID")
    for label, reference in (
        ("mysql image reference", args.mysql_image_reference),
        ("client image reference", args.client_image_reference),
    ):
        if not isinstance(reference, str) or "@sha256:" not in reference:
            raise RenderError(f"{label} must be digest-bound")
    raw_token_path = args.main_agent_token_file
    if raw_token_path.is_symlink() or not raw_token_path.is_file():
        raise RenderError("main-agent token file is absent or symlinked")
    token_path = raw_token_path.resolve(strict=True)
    token_stat = token_path.stat()
    if (
        token_path.is_symlink()
        or not stat.S_ISREG(token_stat.st_mode)
        or stat.S_IMODE(token_stat.st_mode) != 0o600
    ):
        raise RenderError("main-agent token file must be a regular 0600 file")
    token = token_path.read_text(encoding="ascii").strip()
    if not token:
        raise RenderError("main-agent token file is empty")
    valid_minutes = int(args.valid_minutes)
    if valid_minutes < 1 or valid_minutes > 60:
        raise RenderError("validity must be between 1 and 60 minutes")
    now = datetime.now(timezone.utc)
    attempt = args.attempt_token or (
        "tpch-v2-"
        + now.strftime("%Y%m%dt%H%M%Sz").lower()
        + "-"
        + secrets.token_hex(12)
    )
    if ATTEMPT_RE.fullmatch(attempt) is None:
        raise RenderError("attempt token differs")
    suffix = re.sub(r"[^a-z0-9]", "_", attempt.lower())[:48]
    attempt_root = RUNTIME_ROOT + "/attempts/" + attempt
    bundle_id = manifest["bundle_id"]
    bundle_path = RUNTIME_ROOT + "/bundles/" + bundle_id
    python_root = (
        RUNTIME_ROOT + "/runtime/python311-" + PYTHON_RUNTIME_TREE_CONTENT_SHA256
    )
    return {
        "schema_version": "mysql_tpch_distributed_v2_bootstrap_spec_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "scenario": "tpch_memory",
        "host_id": "node04",
        "attempt_token": attempt,
        "bundle": {
            "path": bundle_path,
            "bundle_id": bundle_id,
            "manifest_sha256": sha256(manifest_path),
        },
        "runtime": {
            "source_root": PYTHON_RUNTIME_SOURCE,
            "target_root": python_root,
            "portable_manifest_path": bundle_path
            + "/runtime/python_runtime_tree_manifest_v3.json",
            "portable_manifest_sha256": PYTHON_RUNTIME_MANIFEST_SHA256,
            "tree_content_sha256": PYTHON_RUNTIME_TREE_CONTENT_SHA256,
            "publication_mode": "cross_device_tar_stream_exact_manifest_atomic_rename",
        },
        "contract_sha256": CONTRACT_SHA256,
        "v40_invalidation_sha256": manifest["historical_v40_invalidation_sha256"],
        "mysql_image": {
            "reference": args.mysql_image_reference,
            "image_id": args.mysql_image_id,
        },
        "client_image": {
            "reference": args.client_image_reference,
            "image_id": args.client_image_id,
        },
        "paths": {
            "template": (
                "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/template"
            ),
            "runtime_root": RUNTIME_ROOT,
            "attempt_root": attempt_root,
            "datadir": attempt_root + "/datadir",
            "credentials_dir": attempt_root + "/credentials",
            "host_measurement_tmpfs": "/dev/shm/ra_mysql_tpch_v2/" + attempt,
            "python_runtime_root": python_root,
        },
        "containers": {
            "database": f"ra_mysql_tpch_v2_{suffix}_db",
            "client": f"ra_mysql_tpch_v2_{suffix}_client",
        },
        "mysql_binary": "/usr/bin/mysql",
        "mysql_defaults_file": attempt_root + "/credentials/mysql.cnf",
        "collector": bundle_path
        + "/qualification/mysql_distributed_live_readback_qualification_v1.py",
        "created_at_utc": now.isoformat(),
        "expires_at_utc": (
            now + timedelta(minutes=valid_minutes)
        ).isoformat(),
        "main_agent_token_sha256": hashlib.sha256(token.encode("ascii")).hexdigest(),
        "qualification_only": True,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle-manifest",
        type=Path,
        default=DEFAULT_BUNDLE_MANIFEST,
    )
    parser.add_argument("--mysql-image-reference", required=True)
    parser.add_argument("--mysql-image-id", required=True)
    parser.add_argument("--client-image-reference", required=True)
    parser.add_argument("--client-image-id", required=True)
    parser.add_argument("--main-agent-token-file", type=Path, required=True)
    parser.add_argument("--attempt-token")
    parser.add_argument("--valid-minutes", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    document = render(args)
    payload = canonical(document)
    atomic_new(args.output, payload)
    atomic_new(
        Path(str(args.output) + ".sha256"),
        f"{hashlib.sha256(payload).hexdigest()}  {args.output.name}\n".encode(
            "ascii"
        ),
    )
    print(
        json.dumps(
            {
                "status": "PASS_RENDERED_QUALIFICATION_ONLY_SPEC",
                "bundle_id": document["bundle"]["bundle_id"],
                "attempt_token": document["attempt_token"],
                "accepted_round_authorized": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RenderError, UnicodeError, ValueError) as exc:
        sys.stderr.write(f"NOT_READY: {exc}\n")
        raise SystemExit(2)

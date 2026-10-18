#!/usr/bin/env python3
"""Render a short-lived qualification spec bound to the 295f successor."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import render_mysql_tpch_distributed_v2_bootstrap_spec_blkio_v2 as base


ROOT = HERE.parent
BUNDLE_ID = "295f9808ff5b6d81a8760f1feb82eabf5fd0ba8c1d18ea82a2f35b98908ca728"
MANIFEST_SHA256 = "2e9490ba3417c35a4e7567d9a95cc8d006820e8f97adf75a21c9358cfea65b12"
DEFAULT_BUNDLE_MANIFEST = (
    ROOT / "artifacts/mysql_tpch_effective_authority_successor_v1"
    / BUNDLE_ID / "bundle_manifest.json"
)
INVALIDATION_RELATIVE = "invalidated_df893_empty_hostconfig_projection.json"
COLLECTOR_RELATIVE = (
    "qualification/mysql_distributed_live_readback_qualification_v1.py"
)
ATTEMPT_RE = base.ATTEMPT_RE
RenderError = base.RenderError
canonical = base.canonical
atomic_new = base.atomic_new


def sha256(path: Path) -> str:
    return base.sha256(path)


def _load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RenderError("successor file is absent or symlinked")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RenderError("successor JSON is invalid") from exc
    if not isinstance(value, dict):
        raise RenderError("successor JSON object required")
    return value


def verify_successor(manifest_path: Path) -> dict[str, Any]:
    raw = manifest_path
    if raw.is_symlink() or not raw.is_file():
        raise RenderError("successor manifest is absent or symlinked")
    manifest_path = raw.resolve(strict=True)
    if (
        manifest_path != DEFAULT_BUNDLE_MANIFEST.resolve(strict=True)
        or manifest_path.parent.name != BUNDLE_ID
        or sha256(manifest_path) != MANIFEST_SHA256
    ):
        raise RenderError("renderer is bound only to the 295f successor")
    manifest = _load(manifest_path)
    unsigned = dict(manifest)
    identity = unsigned.pop("bundle_id", None)
    files = manifest.get("files") or {}
    collector = manifest_path.parent / COLLECTOR_RELATIVE
    invalidation = manifest_path.parent / INVALIDATION_RELATIVE
    if (
        identity != BUNDLE_ID
        or hashlib.sha256(canonical(unsigned)).hexdigest() != identity
        or manifest.get("schema_version")
        != "mysql_tpch_distributed_formal10_successor_v2"
        or manifest.get("predecessor_bundle_id")
        != "df8939b1d7e22d22d72c3c3e6cf54434bee2c71959e8a28446d847d5b221520d"
        or manifest.get("effective_authority_first_empty_projection") is not True
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("postgresql_mutation_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or collector.is_symlink()
        or not collector.is_file()
        or files.get(COLLECTOR_RELATIVE) != sha256(collector)
        or (manifest.get("component_hashes") or {}).get("collector")
        != sha256(collector)
        or invalidation.is_symlink()
        or not invalidation.is_file()
        or files.get(INVALIDATION_RELATIVE) != sha256(invalidation)
        or manifest.get(
            "invalidated_df893_empty_hostconfig_projection_sha256"
        )
        != sha256(invalidation)
    ):
        raise RenderError("successor policy or closure differs")
    invalid = _load(invalidation)
    if (
        invalid.get("bundle_id")
        != "df8939b1d7e22d22d72c3c3e6cf54434bee2c71959e8a28446d847d5b221520d"
        or invalid.get("status") != "INVALIDATED_DO_NOT_RERUN"
        or invalid.get("accepted_rounds_credit") != 0
        or invalid.get("formal_r0_authorized") is not False
        or invalid.get("replay_or_rerun_authorized") is not False
        or (invalid.get("observed_docker_hostconfig") or {}).get(
            "BlkioDeviceReadBps"
        )
        != []
        or (invalid.get("observed_docker_hostconfig") or {}).get(
            "BlkioDeviceWriteBps"
        )
        != []
    ):
        raise RenderError("df893 zero-credit invalidation differs")
    payload = collector.read_bytes()
    authority_call = payload.find(
        b"effective_blkio_authority = "
        b"validate_tpch_effective_blkio_authority("
    )
    projection_call = payload.find(
        b"container_readback = validate_container_readback(", authority_call
    )
    if (
        b"empty_arrays_with_exact_effective_cgroup_v1_authority" not in payload
        or authority_call < 0
        or projection_call <= authority_call
    ):
        raise RenderError("successor collector authority ordering differs")
    return manifest


def _configure_base() -> None:
    base.DEFAULT_BUNDLE_ID = BUNDLE_ID
    base.DEFAULT_BUNDLE_MANIFEST_SHA256 = MANIFEST_SHA256
    base.DEFAULT_BUNDLE_MANIFEST = DEFAULT_BUNDLE_MANIFEST


def render(args: argparse.Namespace) -> dict[str, Any]:
    verify_successor(args.bundle_manifest)
    _configure_base()
    return base.render(args)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle-manifest", type=Path, default=DEFAULT_BUNDLE_MANIFEST
    )
    parser.add_argument("--mysql-image-reference", required=True)
    parser.add_argument("--mysql-image-id", required=True)
    parser.add_argument("--client-image-reference", required=True)
    parser.add_argument("--client-image-id", required=True)
    parser.add_argument("--main-agent-token-file", type=Path, required=True)
    parser.add_argument("--attempt-token")
    parser.add_argument("--valid-minutes", type=int, default=25)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    document = render(args)
    payload = canonical(document)
    atomic_new(args.output, payload)
    atomic_new(
        Path(str(args.output) + ".sha256"),
        (
            hashlib.sha256(payload).hexdigest()
            + "  "
            + args.output.name
            + "\n"
        ).encode("ascii"),
    )
    print(
        json.dumps(
            {
                "status": "PASS_RENDERED_QUALIFICATION_ONLY_SPEC",
                "bundle_id": BUNDLE_ID,
                "attempt_token": document["attempt_token"],
                "formal_r0_authorized": False,
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
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)

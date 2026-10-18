#!/usr/bin/env python3
"""Verify and seal an exact XP qualification-successor binding.

Known v18-v23 identities are pinned.  The final v23 is accepted only after the
sealed v23 -> v22 -> v21 -> v20 chain and every qualification/materials-critical file
are re-read.  Later schemas require a new support successor.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping


HEX64 = re.compile(r"[0-9a-f]{64}$")
V18 = "f43f33b5d60cf6de2fe63f96852e6a46ea3b132b5d52039a1e60021771dc9262"
V19 = "e91031f5af956af87904a240fcf6ce6673d2511dfc66f1a6337f36698133050d"
V20 = "f2dd55ea6b5ea4db704aa533bddc9161b378c30735b9606a092efccc1315179f"
V21 = "f6528795e2eabc84c7d29ff8d387bc50ffd0bbb055e4fc0987da5aa8626717c2"
V22 = "21d1a853548ebdff573d18a82c7a124b810b5656e3e3a330146f87d3e0b460ad"
V23 = "ba68d4a57f7ebb03f8e9c662250492aac7341757c66c56256665c41b5ee3a77e"
KNOWN = {
    V18: "mysql_xp_node02_successor_bundle_v18",
    V19: "mysql_xp_node02_successor_bundle_v19",
    V20: "mysql_xp_node02_successor_bundle_v20",
    V21: "mysql_xp_node02_successor_bundle_v21",
    V22: "mysql_xp_node02_successor_bundle_v22",
    V23: "mysql_xp_node02_successor_bundle_v23",
}
MANIFEST_SHA256 = {
    V20: "3df7740447d613a653ef500100a54edf755ab05b8728336460ac96b5105ee17f",
    V21: "8f0cde3c6f5266d2bcd716e84cf5e3ae57412baf4dd3fd0ea60f3a723829615e",
    V22: "37863284416171c00a1a9f2e4b036455abf3667a183e3df3eb8aa2b01d36c115",
    V23: "e03f095d21876c687e9b916b8b2b5e4dca6999d5ece745d3b6261becfc5eecf0",
}
BASE_FILES = {
    "collector": "qualification/mysql_distributed_live_readback_qualification_v1.py",
    "qualification_authority": "qualification/mysql_xp_qualification_authority_v1.py",
    "adopted_service": "launch/mysql_xp_node02_adopted_service_v1.py",
}
V22_MATERIAL_FILES = {
    "formal10_materializer": "launch/mysql_xp_node02_formal10_materializer_v1.py",
    "qualification_materials_generator": "test_support/mysql_xp_qualification_bootstrap_materials_v5.py",
    "ttl_remediation": "xp_node02_qualification_ttl_remediation_v1.json",
}
FILES = {**BASE_FILES, **V22_MATERIAL_FILES}
V22_CRITICAL_SHA256 = {
    "collector": "f3994d730f8e7100a312a9d462379557bb946e7108de667cddfce774a5ba9e0d",
    "qualification_authority": "7ae8157b36c1c2149e1abb5de712af3f5548129c4aaa82902d8f3a23336bf8f6",
    "adopted_service": "dc176e70424541910b38b9a967d64db8e439be74874ea71823845b178f5b6f2b",
    "formal10_materializer": "9c1d4fa6fc4a67eee375d941b63217790a394d6f56ad945438d208aa7cdd93d9",
    "qualification_materials_generator": "ed653e25da95f931c8b9486fa0829e981565bceb26fee8d41c3e132a54f1972a",
    "ttl_remediation": "c6cad35539ee2c60908bfe67c4a8ffb338f45ccadc3d5bc2edfddae7f13aaa6c",
}
V23_PARITY_FILES = {
    "authority_ttl_parity_remediation": "xp_node02_authority_ttl_parity_remediation_v1.json",
}
V23_CRITICAL_SHA256 = {
    "collector": "e993fa1dd8fe45af42a18980e4bd0623c99409248bb1a62d627da40439b88ca3",
    "qualification_authority": "aade726a7b4b9e593c8e32c15366c7535ee604f666fcef625609d6918167fe32",
    "adopted_service": "2626612ec569e0dca322b1e594c9054e2b1e7eacffa9713f9a86b899792a3731",
    "formal10_materializer": "e81e4feed8345fcf2c625a4027839e2f354bd181b49a30a881491e0db00e6061",
    "qualification_materials_generator": "dc6bbf8c9604bc65dbe739893139d5859c4f76f8ebde59c7065d6d79e65dc596",
    "ttl_remediation": "c6cad35539ee2c60908bfe67c4a8ffb338f45ccadc3d5bc2edfddae7f13aaa6c",
    "authority_ttl_parity_remediation": "ca6c2458d01a086f9e7bff0ee0c27c7fed15de679bb5c0b50d748b144e460bed",
}


class BindingError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_sealed(path: Path, label: str) -> dict[str, Any]:
    sidecar = Path(str(path) + ".sha256")
    if path.is_symlink() or not path.is_file():
        raise BindingError(label + " absent or unsafe")
    expected = "%s  %s\n" % (sha256(path), path.name)
    if sidecar.is_symlink() or not sidecar.is_file() or sidecar.read_text(encoding="ascii") != expected:
        raise BindingError(label + " sidecar differs")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BindingError(label + " must be an object")
    return value


def _chain_path(bundle: Path, bundle_id: str, version: int) -> Path:
    candidates = (
        bundle.parent / bundle_id,
        bundle.parents[1] / ("mysql_xp_node02_successor_v%d" % version) / bundle_id,
    )
    for candidate in candidates:
        if candidate.is_dir() and not candidate.is_symlink():
            return candidate.resolve(strict=True)
    raise BindingError("XP v%d chain bundle is absent" % version)


def _verify_chain_manifest(bundle: Path, bundle_id: str, schema: str,
                           predecessor: str | None,
                           predecessor_manifest: str | None) -> dict[str, Any]:
    manifest_path = bundle / "bundle_manifest.json"
    manifest = load_sealed(manifest_path, "XP chain manifest")
    unsigned = dict(manifest)
    actual_id = unsigned.pop("bundle_id", None)
    if (
        actual_id != bundle_id or bundle.name != bundle_id
        or hashlib.sha256(canonical(unsigned)).hexdigest() != bundle_id
        or sha256(manifest_path) != MANIFEST_SHA256[bundle_id]
        or manifest.get("schema_version") != schema
        or manifest.get("predecessor_bundle_id") != predecessor
        or manifest.get("predecessor_manifest_sha256") != predecessor_manifest
        or manifest.get("scenario") != "hybench_xp_io"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("postgresql_mutation_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BindingError("XP sealed chain identity differs")
    return manifest


def inspect_bundle(bundle: Path) -> dict[str, Any]:
    bundle = bundle.resolve(strict=True)
    manifest_path = bundle / "bundle_manifest.json"
    manifest = load_sealed(manifest_path, "XP bundle manifest")
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    schema = manifest.get("schema_version")
    known_schema = KNOWN.get(str(bundle_id))
    if (
        bundle.is_symlink()
        or bundle_id != bundle.name
        or HEX64.fullmatch(bundle.name) is None
        or hashlib.sha256(canonical(unsigned)).hexdigest() != bundle.name
        or known_schema is None or schema != known_schema
        or manifest.get("scenario") != "hybench_xp_io"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("postgresql_mutation_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or not isinstance(manifest.get("files"), dict)
    ):
        raise BindingError("XP successor manifest identity/boundary differs")
    chain = []
    if bundle_id in (V22, V23):
        v21 = _chain_path(bundle, V21, 21)
        v20 = _chain_path(bundle, V20, 20)
        _verify_chain_manifest(
            v20, V20, KNOWN[V20], V19,
            "9e58aabf733881726755251a5b1b3f9a3e5e79ff2821b20cbe6ae56b7e91b872",
        )
        _verify_chain_manifest(v21, V21, KNOWN[V21], V20, MANIFEST_SHA256[V20])
        v22 = bundle if bundle_id == V22 else _chain_path(bundle, V22, 22)
        _verify_chain_manifest(v22, V22, KNOWN[V22], V21, MANIFEST_SHA256[V21])
        if bundle_id == V23:
            _verify_chain_manifest(bundle, V23, KNOWN[V23], V22, MANIFEST_SHA256[V22])
        chain = [
            {"bundle_id": item, "schema_version": KNOWN[item],
             "manifest_sha256": MANIFEST_SHA256[item]}
            for item in ((V20, V21, V22, V23) if bundle_id == V23 else (V20, V21, V22))
        ]
    critical_sha256 = V23_CRITICAL_SHA256 if bundle_id == V23 else V22_CRITICAL_SHA256
    if bundle_id in (V22, V23):
        if (
            manifest.get("status") != (
                "SEALED_GENERATOR_SERVICE_60_MINUTE_TTL_PARITY_NOT_LAUNCHED"
                if bundle_id == V23 else "SEALED_60_MINUTE_MATERIAL_TTL_SUCCESSOR_NOT_LAUNCHED"
            )
            or manifest.get("qualification_material_valid_minutes_min") != 10
            or manifest.get("qualification_material_valid_minutes_max") != 60
            or manifest.get("materials_generator_sha256")
            != critical_sha256["qualification_materials_generator"]
            or manifest.get("test_support_materializer")
            != V22_MATERIAL_FILES["qualification_materials_generator"]
        ):
            raise BindingError("XP materials/TTL identity differs")
    artifacts: dict[str, dict[str, str]] = {}
    required_files = (
        {**FILES, **V23_PARITY_FILES} if bundle_id == V23
        else FILES if bundle_id == V22 else BASE_FILES
    )
    for role, relative in required_files.items():
        path = bundle / relative
        expected = manifest["files"].get(relative)
        if (
            path.is_symlink()
            or not path.is_file()
            or HEX64.fullmatch(str(expected or "")) is None
            or sha256(path) != expected
            or (bundle_id in (V22, V23) and expected != critical_sha256[role])
        ):
            raise BindingError("XP successor critical file differs: " + role)
        artifacts[role] = {"relative": relative, "sha256": expected}
    return {
        "bundle": bundle,
        "bundle_id": bundle.name,
        "schema_version": schema,
        "predecessor_bundle_id": manifest.get("predecessor_bundle_id"),
        "manifest_sha256": sha256(manifest_path),
        "sealed_chain": chain,
        "artifacts": artifacts,
    }


def build_binding(bundle: Path, published_bundle: Path | None = None) -> dict[str, Any]:
    row = inspect_bundle(bundle)
    published = row["bundle"] if published_bundle is None else published_bundle
    if not published.is_absolute() or published.name != row["bundle_id"]:
        raise BindingError("published XP bundle path differs")
    return {
        "schema_version": "mysql_xp_qualification_successor_binding_v1",
        "status": "SEALED_EXACT_XP_SUCCESSOR_NOT_LAUNCH_AUTHORITY",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "xp_bundle_path": str(published),
        "xp_bundle_id": row["bundle_id"],
        "xp_bundle_schema_version": row["schema_version"],
        "xp_predecessor_bundle_id": row["predecessor_bundle_id"],
        "xp_manifest_sha256": row["manifest_sha256"],
        "sealed_chain": row["sealed_chain"],
        "artifacts": row["artifacts"],
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


def validate_binding(path: Path, expected_bundle: Path | None = None) -> dict[str, Any]:
    binding = load_sealed(path, "XP successor binding")
    bundle = Path(str(binding.get("xp_bundle_path") or ""))
    if expected_bundle is not None and bundle.resolve(strict=True) != expected_bundle.resolve(strict=True):
        raise BindingError("XP binding bundle path differs")
    expected = build_binding(bundle)
    if binding != expected:
        raise BindingError("XP successor binding content differs")
    return binding


def write_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    payload = canonical(value)
    sidecar = Path(str(path) + ".sha256")
    if any(item.exists() or item.is_symlink() for item in (path, sidecar)):
        raise BindingError("refusing to overwrite XP successor binding")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    sidecar.write_text("%s  %s\n" % (hashlib.sha256(payload).hexdigest(), path.name), encoding="ascii")
    sidecar.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xp-bundle", type=Path, required=True)
    parser.add_argument("--published-xp-bundle", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_binding(args.xp_bundle, args.published_xp_bundle)
    write_exclusive(args.output, result)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BindingError, OSError, ValueError) as exc:
        raise SystemExit("NOT_READY: %s" % exc)

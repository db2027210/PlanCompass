#!/usr/bin/env python3
"""Seal independent XP outer authorization after fresh R0 and strict registries."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def local_json(path: Path) -> tuple[Path, dict[str, Any]]:
    if path.is_symlink():
        raise RuntimeError(f"symlink rejected: {path}")
    path = path.resolve(strict=True)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    sidecar = path.with_name(path.name + ".sha256")
    if (
        not sidecar.is_file()
        or sidecar.is_symlink()
        or sidecar.read_text(encoding="ascii")
        != f"{sha256(path)}  {path.name}\n"
    ):
        raise RuntimeError(f"checksum sidecar absent or differs: {path}")
    return path, value


def build_authorization(
    *,
    bundle: Path,
    config: Path,
    capture_at_seal: Path,
    formal_r0_authority: Path,
    ap_registry: Path,
    mixed_registry: Path,
    registry_audit: Path,
) -> dict[str, Any]:
    bundle = bundle.resolve(strict=True)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        bundle.name != manifest.get("bundle_id")
        or manifest.get("schema_version") != "mysql_xp_v8_immutable_runtime_v1"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("XP runtime bundle identity/scope differs")
    config_path, config_doc = local_json(config)
    seal_path, seal = local_json(capture_at_seal)
    r0_path, r0 = local_json(formal_r0_authority)
    ap_path, ap = local_json(ap_registry)
    mixed_path, mixed = local_json(mixed_registry)
    audit_path, audit = local_json(registry_audit)
    identity = config_doc.get("formal_r0_identity")
    trace_sha = (
        ((seal.get("sources") or {}).get("trace") or {}).get("sha256")
        if isinstance(seal.get("sources"), Mapping)
        else None
    )
    if (
        config_doc.get("runtime_bundle_id") != manifest["bundle_id"]
        or config_doc.get("formal_scope") != "mysql_only"
        or config_doc.get("cross_engine_comparison_prohibited") is not True
        or config_doc.get("authorization_stage") != "formal_outer"
        or config_doc.get("execution_authorized") is not True
        or config_doc.get("outer_execution_authorized") is not True
        or config_doc.get("accepted_round_authorized") is not False
        or config_doc.get("measurement_seconds") != 600
        or config_doc.get("minimum_measurement_seconds") != 180
        or config_doc.get("optimizer", {}).get("original_gradient_only") is not True
        or len(config_doc.get("cost_parameters", {}).get("active") or []) != 7
        or config_doc.get("formal_outer_protocol", {}).get(
            "formal_search_initial_replay_top_k"
        ) != 5
        or config_doc.get("formal_outer_protocol", {}).get(
            "formal_search_max_replay_top_k"
        ) != 5
        or config_doc.get("formal_outer_protocol", {}).get(
            "target_accepted_shrinks"
        ) != 10
        or config_doc.get("three_level_fingerprint", {}).get("qps_role")
        != "outer_acceptance_only"
        or not isinstance(identity, Mapping)
    ):
        raise RuntimeError("XP outer config does not preserve the formal contract")
    if (
        seal.get("bundle_id") != manifest["bundle_id"]
        or seal.get("formal_r0_launch_authorized") is not True
        or seal.get("outer_execution_authorized") is not False
        or seal.get("accepted_round_authorized") is not False
        or r0.get("schema_version") != "mysql_xp_formal_point_gate_v1"
        or r0.get("bundle_id") != manifest["bundle_id"]
        or r0.get("r0_gate_passed") is not True
        or r0.get("correctness_passed") is not True
        or r0.get("stability_passed") is not True
        or r0.get("binding_trace_sha256") != trace_sha
        or r0.get("capture_at_seal_sha256") != sha256(seal_path)
        or r0.get("formal_r0_identity") != identity
        or r0.get("formal_r0_identity_sha256") != canonical_sha256(identity)
        or r0.get("outer_execution_authorized") is not False
        or r0.get("accepted_round_authorized") is not False
    ):
        raise RuntimeError("XP formal R0/capture authority binding differs")
    if (
        ap.get("schema_version") != "mysql_xp_ap_estimator_registry_v1"
        or mixed.get("schema_version")
        != "mysql_xp_mixed_strict_plan_registry_v1"
        or ap.get("execution_authorized") is not True
        or mixed.get("execution_authorized") is not True
        or ap.get("immutable") is not True
        or mixed.get("immutable") is not True
        or ap.get("trace_sha256") != trace_sha
        or mixed.get("trace_sha256") != trace_sha
        or ap.get("request_sha256") != mixed.get("request_sha256")
        or audit.get("schema_version") != "mysql_xp_registry_build_audit_v1"
        or audit.get("execution_authorized") is not True
        or audit.get("reasons") != []
        or audit.get("ap_registry_sha256") != sha256(ap_path)
        or audit.get("mixed_registry_sha256") != sha256(mixed_path)
    ):
        raise RuntimeError("XP strict registry authority is incomplete")
    feature = config_doc.get("estimator", {}).get("feature_source", {})
    workload_registry = config_doc.get("workload_registry", {})
    if (
        feature.get("registry_execution_authorized") is not True
        or feature.get("strict_cluster_usable") is not True
        or Path(str(feature.get("workload_registry_path") or "")).resolve()
        != mixed_path
        or feature.get("workload_registry_sha256") != sha256(mixed_path)
        or workload_registry.get("execution_authorized") is not True
        or workload_registry.get("immutable") is not True
        or Path(str(workload_registry.get("path") or "")).resolve() != mixed_path
        or workload_registry.get("sha256") != sha256(mixed_path)
    ):
        raise RuntimeError("XP outer config is not bound to the strict registry")
    return {
        "schema_version": "mysql_xp_outer_authorization_v1",
        "status": "SEALED_OUTER_AUTHORIZED",
        "launch_authorized": True,
        "bundle_id": manifest["bundle_id"],
        "bundle_manifest_sha256": sha256(manifest_path),
        "config_path": str(config_path),
        "config_sha256": sha256(config_path),
        "capture_at_seal_sha256": sha256(seal_path),
        "capture_trace_sha256": trace_sha,
        "formal_r0_authority_path": str(r0_path),
        "formal_r0_authority_sha256": sha256(r0_path),
        "formal_r0_identity_sha256": canonical_sha256(identity),
        "formal_r0_passed": True,
        "strict_registries_authorized": True,
        "ap_registry_path": str(ap_path),
        "ap_registry_sha256": sha256(ap_path),
        "mixed_registry_path": str(mixed_path),
        "mixed_registry_sha256": sha256(mixed_path),
        "registry_audit_path": str(audit_path),
        "registry_audit_sha256": sha256(audit_path),
        "required_nonempty_subsets": 127,
        "fixed_top5_replay": True,
        "original_gradient_only": True,
        "qps_role": "outer_acceptance_only",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "outer_execution_authorized": True,
        "algorithm_c_execution_authorized": True,
        "accepted_round_authorized": False,
    }


def write_fresh(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"fresh output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_bytes(payload)
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha256(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--capture-at-seal", type=Path, required=True)
    parser.add_argument("--formal-r0-authority", type=Path, required=True)
    parser.add_argument("--ap-registry", type=Path, required=True)
    parser.add_argument("--mixed-registry", type=Path, required=True)
    parser.add_argument("--registry-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    authority = build_authorization(
        bundle=args.bundle,
        config=args.config,
        capture_at_seal=args.capture_at_seal,
        formal_r0_authority=args.formal_r0_authority,
        ap_registry=args.ap_registry,
        mixed_registry=args.mixed_registry,
        registry_audit=args.registry_audit,
    )
    write_fresh(args.output, authority)
    print(json.dumps({
        "status": authority["status"],
        "output": str(args.output),
        "config_sha256": authority["config_sha256"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

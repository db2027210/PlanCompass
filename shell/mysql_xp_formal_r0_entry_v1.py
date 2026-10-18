#!/usr/bin/env python3
"""Bundle-closed, R0-only XP entry point.

This entry point may create exactly one fresh production R0 and its authority
documents.  It cannot enumerate candidates, replay a candidate, or enter the
Algorithm C outer loop.
"""

from __future__ import annotations

import argparse
import copy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import socket
from typing import Any, Mapping

from formal_mysql_sgd_checkpoint import FormalProductionR0Ledger
try:
    from mysql_algorithm_c_local_services import LocalProductionServices
except ModuleNotFoundError:  # Local source-tree test/import path.
    from shell.mysql_algorithm_c_local_services_v25 import LocalProductionServices
from mysql_parameter_optimizer import _validate_replay_baseline


BUNDLE = Path(__file__).resolve().parents[1]
EXPECTED_PORT = 65304
EXPECTED_ENVELOPE = {
    "cpu_cores": 5.3,
    "cpuset": "184-199,440-455",
    "mems": "1",
    "read_bps": 29360128,
    "write_bps": 524288,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def local_file(path: Path) -> Path:
    if path.is_symlink():
        raise RuntimeError(f"regular local file required (symlink rejected): {path}")
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise RuntimeError(f"regular local file required: {path}")
    return resolved


def load_object(path: Path) -> Mapping[str, Any]:
    value = json.loads(local_file(path).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def valid_sidecar(path: Path) -> bool:
    sidecar = path.with_name(path.name + ".sha256")
    return (
        sidecar.is_file()
        and not sidecar.is_symlink()
        and sidecar.read_text(encoding="ascii")
        == f"{sha256(path)}  {path.name}\n"
    )


def write_fresh_json(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"fresh output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(
            value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha256(path)}  {path.name}\n", encoding="ascii"
    )


def validate_r0_inputs(
    config_path: Path, seal_path: Path, run_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    config_path = local_file(config_path)
    seal_path = local_file(seal_path)
    if not valid_sidecar(config_path) or not valid_sidecar(seal_path):
        raise RuntimeError("R0 config/seal checksum sidecar is absent or differs")
    config = dict(load_object(config_path))
    seal = dict(load_object(seal_path))
    manifest = dict(load_object(BUNDLE / "manifest.json"))
    bundle_id = str(manifest.get("bundle_id") or "")
    identity = config.get("formal_r0_identity")
    workload = config.get("workload")
    mysql = config.get("mysql")
    outer = config.get("formal_outer_protocol")
    if (
        socket.gethostname() != "compute00"
        or BUNDLE.name != bundle_id
        or config.get("schema_version") != "mysql_algorithm_c_optimizer_config_v1"
        or config.get("benchmark") != "hybench_xp"
        or config.get("scenario") != "hybench_xp_io"
        or config.get("formal_scope") != "mysql_only"
        or config.get("cross_engine_comparison_prohibited") is not True
        or config.get("runtime_bundle_id") != bundle_id
        or config.get("authorization_stage") != "formal_r0_only"
        or config.get("formal_r0_launch_authorized") is not True
        or config.get("execution_authorized") is not False
        or config.get("outer_execution_authorized") is not False
        or config.get("accepted_round_authorized") is not False
        or config.get("measurement_seconds") != 600
        or config.get("minimum_measurement_seconds") != 600
        or not isinstance(mysql, Mapping)
        or mysql.get("host") != "127.0.0.1"
        or mysql.get("port") != EXPECTED_PORT
        or not isinstance(workload, Mapping)
        or workload.get("capture_only") is not False
        or workload.get("formal_r0_only") is not True
        or workload.get("formal_graceful_quiescence_seconds") != 120
        or workload.get("host_control_cpuset") != EXPECTED_ENVELOPE["cpuset"]
        or not isinstance(outer, Mapping)
        or outer.get("outer_execution_authorized") is not False
        or not isinstance(identity, Mapping)
    ):
        raise RuntimeError("XP formal R0 config is not exact R0-only authority")
    profile_path = local_file(Path(str(config.get("profile") or "")))
    if profile_path != (BUNDLE / "profile/hybench_xp_io_formal_v8.json").resolve():
        raise RuntimeError("XP formal R0 profile is outside the runtime bundle")
    profile = load_object(profile_path)
    if {key: profile.get(key) for key in EXPECTED_ENVELOPE} != EXPECTED_ENVELOPE:
        raise RuntimeError("XP formal R0 effective envelope declaration differs")
    trace_source = (
        (seal.get("sources") or {}).get("trace")
        if isinstance(seal.get("sources"), Mapping)
        else None
    )
    expected_identity = {
        "schema_version": "mysql_xp_formal_r0_identity_v1",
        "bundle_id": bundle_id,
        "capture_at_seal_sha256": sha256(seal_path),
        "capture_trace_sha256": (
            trace_source.get("sha256") if isinstance(trace_source, Mapping) else None
        ),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "resource_envelope": EXPECTED_ENVELOPE,
        "mysql_port": EXPECTED_PORT,
        "workload_seed": 4508333,
    }
    if dict(identity) != expected_identity:
        raise RuntimeError("XP formal R0 stable identity differs")
    if (
        seal.get("schema_version") != "mysql_xp_capture_at_coverage_seal_v1"
        or seal.get("bundle_id") != bundle_id
        or seal.get("formal_r0_launch_authorized") is not True
        or seal.get("outer_execution_authorized") is not False
        or seal.get("algorithm_c_execution_authorized") is not False
        or seal.get("accepted_round_authorized") is not False
    ):
        raise RuntimeError("XP capture AT seal does not authorize only formal R0")
    contract = {
        "bundle_id": os.environ.get("MYSQL_XP_CONTRACT_BUNDLE_ID"),
        "config_sha256": os.environ.get("MYSQL_XP_CONTRACT_CONFIG_SHA256"),
        "seal_sha256": os.environ.get("MYSQL_XP_CONTRACT_R0_SEAL_SHA256"),
        "run_id": os.environ.get("MYSQL_XP_CONTRACT_RUN_ID"),
        "mode": os.environ.get("MYSQL_XP_CONTRACT_MODE"),
    }
    if contract != {
        "bundle_id": bundle_id,
        "config_sha256": sha256(config_path),
        "seal_sha256": sha256(seal_path),
        "run_id": run_id,
        "mode": "formal_r0",
    }:
        raise RuntimeError("XP formal R0 was not admitted by the bundle launch contract")
    return config, seal, expected_identity


def r0_campaign_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "benchmark": "hybench_xp",
        "formal_r0_identity_sha256": canonical_sha256(identity),
        "service_factory_spec": "mysql_algorithm_c_local_services:build",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--sealed-at-registry", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if not args.run_id or any(
        character not in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_-"
        for character in args.run_id
    ):
        raise SystemExit("run-id must be a non-empty token")
    config, seal, identity = validate_r0_inputs(
        args.config, args.sealed_at_registry, args.run_id
    )
    outer = config["formal_outer_protocol"]
    checkpoint = Path(str(outer.get("formal_real_r0_checkpoint_path") or ""))
    authority = Path(str(outer.get("xp_formal_r0_authority_path") or ""))
    if (
        not checkpoint.is_absolute()
        or not authority.is_absolute()
        or checkpoint.exists()
        or checkpoint.with_name(checkpoint.name + ".sha256").exists()
        or authority.exists()
        or authority.with_name(authority.name + ".sha256").exists()
    ):
        raise SystemExit("fresh absolute XP R0 checkpoint/authority paths are required")
    services = LocalProductionServices(config)
    services.lane = replace(services.lane, remote_port=EXPECTED_PORT)
    services.replay_lane = replace(
        services.replay_lane, remote_port=EXPECTED_PORT + 100
    )
    bundle = services.bundle()
    try:
        measurement = bundle["r0_measurement"](config)
        _validate_replay_baseline(measurement, "io")
        FormalProductionR0Ledger.create(
            checkpoint,
            campaign_identity=r0_campaign_identity(identity),
            measurement=measurement,
        )
        point = services.planner.output
        readback = point / "effective_resource_readback_v2.json"
        if not readback.is_file() or readback.is_symlink():
            raise RuntimeError("formal XP R0 effective resource readback is absent")
        readback_sidecar = readback.with_name(readback.name + ".sha256")
        if readback_sidecar.exists() or readback_sidecar.is_symlink():
            raise RuntimeError("formal XP R0 readback sidecar path is not fresh")
        readback_sidecar.write_text(
            f"{sha256(readback)}  {readback.name}\n", encoding="ascii"
        )
        trace_sha = identity["capture_trace_sha256"]
        correctness = measurement.get("correctness_passed") is True
        stable = measurement.get("stable") is True
        write_fresh_json(
            authority,
            {
                "schema_version": "mysql_xp_formal_point_gate_v1",
                "status": "FORMAL_R0_PASSED",
                "bundle_id": identity["bundle_id"],
                "run_id": services.planner.run_id,
                "requested_run_id": args.run_id,
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "r0_gate_passed": correctness and stable,
                "correctness_passed": correctness,
                "stability_passed": stable,
                "binding_trace_sha256": trace_sha,
                "capture_at_seal_sha256": sha256(args.sealed_at_registry),
                "formal_r0_identity": identity,
                "formal_r0_identity_sha256": canonical_sha256(identity),
                "formal_r0_config_sha256": sha256(args.config),
                "production_r0_checkpoint_path": str(checkpoint),
                "production_r0_checkpoint_sha256": sha256(checkpoint),
                "resource_readback_path": str(readback.resolve()),
                "resource_readback_sha256": sha256(readback),
                "measurement": measurement,
                "outer_execution_authorized": False,
                "algorithm_c_execution_authorized": False,
                "accepted_round_authorized": False,
            },
        )
        if not correctness or not stable:
            raise RuntimeError("formal XP R0 did not pass correctness/stability")
        from offline_registry import xp_strict_registry as registry
        from local_capture.xp_explain_runner import run_postseal_explains
        from seal_mysql_xp_outer_authorization import (
            build_authorization as build_outer_authorization,
            write_fresh as write_outer_authorization,
        )

        seal_sources = seal["sources"]
        validation = registry.validate_xp_trace(
            Path(str(seal_sources["trace"]["path"])),
            Path(str(seal_sources["trace_manifest"]["path"])),
            Path(str(seal_sources["runtime_authority"]["path"])),
            Path(str(seal_sources["capture_authorization"]["path"])),
            authority,
        )
        registry_root = authority.parent / "strict_registry"
        if registry_root.exists() or registry_root.is_symlink():
            raise RuntimeError("fresh XP strict-registry output directory is required")
        registry_root.mkdir(parents=True)
        validation_path = registry_root / "xp_trace_validation.json"
        registry.write_artifact(validation_path, validation)
        if validation.get("execution_authorized") is not True:
            raise RuntimeError(
                "XP capture plus formal R0 validation failed: "
                + json.dumps(validation.get("reasons"), sort_keys=True)
            )
        request = registry.build_explain_request(validation)
        request_path = registry_root / "xp_raw_explain_request.json"
        registry.write_artifact(request_path, request)
        idle_path = registry_root / "xp_workload_idle_evidence.json"
        registry.write_artifact(
            idle_path,
            {
                "schema_version": "mysql_xp_workload_idle_evidence_v1",
                "hostname": "compute00",
                "trace_sha256": identity["capture_trace_sha256"],
                "point_readback_sha256": sha256(readback),
                "capture_sealed_before_probe": True,
                "workload_stop_acknowledged": True,
                "inventory_complete": True,
                "candidate_workload_pids": [],
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
            },
        )
        raw_dir = registry_root / "raw_explains"
        raw_bundle = run_postseal_explains(
            validation,
            request,
            point_readback_path=readback,
            idle_evidence_path=idle_path,
            mysql_binary=Path("/usr/bin/mysql"),
            mysql_defaults_file=point / "private/mysql.cnf",
            mysql_port=EXPECTED_PORT,
            mysql_database="hybench_sf10x",
            output_dir=raw_dir,
        )
        verified = registry.verify_explain_bundle(
            validation, request, raw_bundle
        )
        verified_path = registry_root / "xp_verified_plan_set.json"
        registry.write_artifact(verified_path, verified)
        built = registry.build_registries(validation, request, verified)
        if built.get("execution_authorized") is not True:
            raise RuntimeError(
                "XP strict registry build failed: "
                + json.dumps(built.get("reasons"), sort_keys=True)
            )
        ap_path = registry_root / "hybench_xp.ap_estimator_registry.json"
        mixed_path = (
            registry_root / "hybench_xp.mixed_strict_plan_registry.json"
        )
        audit_path = registry_root / "hybench_xp.registry_build_audit.json"
        registry.write_artifact(ap_path, built["ap_registry"])
        registry.write_artifact(mixed_path, built["mixed_registry"])
        registry.write_artifact(
            audit_path,
            {
                "schema_version": "mysql_xp_registry_build_audit_v1",
                "execution_authorized": True,
                "reasons": [],
                "ap_registry_sha256": sha256(ap_path),
                "mixed_registry_sha256": sha256(mixed_path),
            },
        )
        outer_config = copy.deepcopy(config)
        outer_config.update({
            "authorization_stage": "formal_outer",
            "formal_r0_launch_authorized": False,
            "execution_authorized": True,
            "outer_execution_authorized": True,
            "accepted_round_authorized": False,
            "minimum_measurement_seconds": 180,
        })
        outer_config["workload"].update({
            "capture_only": False,
            "formal_r0_only": False,
        })
        outer_config["formal_outer_protocol"]["outer_execution_authorized"] = True
        outer_config["estimator"]["feature_source"].update({
            "binding_capture_status": "sealed_exact_production_capture",
            "registry_execution_authorized": True,
            "strict_cluster_usable": True,
            "workload_registry_path": str(mixed_path.resolve()),
            "workload_registry_sha256": sha256(mixed_path),
        })
        outer_config["workload_registry"] = {
            "binding_capture_status": "sealed_exact_production_capture",
            "binding_consistency": "exact_fail_closed",
            "execution_authorized": True,
            "immutable": True,
            "path": str(mixed_path.resolve()),
            "sha256": sha256(mixed_path),
        }
        outer_config_path = authority.parent / "formal_outer_config.json"
        write_fresh_json(outer_config_path, outer_config)
        outer_authority_path = (
            authority.parent / "mysql_xp_outer_authorization.json"
        )
        outer_document = build_outer_authorization(
            bundle=BUNDLE,
            config=outer_config_path,
            capture_at_seal=args.sealed_at_registry,
            formal_r0_authority=authority,
            ap_registry=ap_path,
            mixed_registry=mixed_path,
            registry_audit=audit_path,
        )
        write_outer_authorization(outer_authority_path, outer_document)
        print(json.dumps({
            "status": "FORMAL_R0_PASSED",
            "point": str(point),
            "checkpoint": str(checkpoint),
            "authority": str(authority),
            "strict_registry": str(registry_root),
            "formal_outer_config": str(outer_config_path),
            "outer_authorization": str(outer_authority_path),
        }, sort_keys=True), flush=True)
        return 0
    finally:
        cleanup = bundle["campaign_cleanup"]()
        if cleanup.get("zero_pressure_verified") is not True:
            raise RuntimeError("XP formal R0 cleanup did not prove zero pressure")


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Seal bundle-bound XP AT1-AT6 capture coverage for formal R0 only."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from typing import Any, Mapping


REQUIRED_AT = ("AT1", "AT2", "AT3", "AT4", "AT5", "AT6")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def atomic_fresh_json(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"fresh seal output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sidecar = path.with_name(path.name + ".sha256")
    sidecar.write_text(f"{sha256(path)}  {path.name}\n", encoding="ascii")


def validate_bundle(bundle: Path) -> tuple[Mapping[str, Any], str]:
    bundle = bundle.resolve(strict=True)
    manifest_path = local_file(bundle / "manifest.json")
    manifest = load_object(manifest_path)
    bundle_id = str(manifest.get("bundle_id") or "")
    if (
        bundle.name != bundle_id
        or manifest.get("schema_version") != "mysql_xp_v8_immutable_runtime_v1"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_r0_authorized") is not False
        or manifest.get("required_nonempty_subsets") != 127
        or manifest.get("fixed_replay_count") != 5
    ):
        raise RuntimeError("XP immutable bundle identity/scope differs")
    payload_files = manifest.get("payload_files")
    if not isinstance(payload_files, Mapping) or not payload_files:
        raise RuntimeError("XP immutable bundle payload closure is absent")
    for relative, expected in payload_files.items():
        candidate = local_file(bundle / str(relative))
        if sha256(candidate) != expected:
            raise RuntimeError(f"XP bundle payload differs: {relative}")
    return manifest, sha256(manifest_path)


def registry_module(bundle: Path) -> Any:
    path = local_file(
        bundle
        / "capture/offline_registry/xp_strict_registry.py"
    )
    spec = importlib.util.spec_from_file_location(
        "xp_strict_registry_bundle_bound", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load bundle-bound XP strict registry validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_seal(
    *,
    bundle: Path,
    trace: Path,
    trace_manifest: Path,
    runtime_authority: Path,
    capture_authorization: Path,
    capture_spec: Path,
) -> dict[str, Any]:
    manifest, bundle_manifest_sha = validate_bundle(bundle)
    bundle = bundle.resolve(strict=True)
    trace = local_file(trace)
    trace_manifest = local_file(trace_manifest)
    runtime_authority = local_file(runtime_authority)
    capture_authorization = local_file(capture_authorization)
    capture_spec = local_file(capture_spec)
    spec = load_object(capture_spec)
    if (
        not valid_sidecar(capture_spec)
        or spec.get("schema_version") != "mysql_xp_fresh_local_capture_spec_v1"
        or spec.get("runtime_bundle_id") != manifest["bundle_id"]
        or spec.get("formal_scope") != "mysql_only"
        or spec.get("cross_engine_comparison_prohibited") is not True
        or spec.get("capture_only") is not True
        or spec.get("outer_execution_authorized") is not False
        or (
            (spec.get("frozen_workload") or {})
            .get("workload", {})
            .get("qps_latency_resource_results")
        )
        != "prohibited"
        or (
            (spec.get("frozen_workload") or {})
            .get("workload", {})
            .get("capture_schedule")
        )
        != "deterministic_finite_ap13_at6_fixed_seed_binding_capture"
    ):
        raise RuntimeError("XP capture spec is not sealed to this runtime bundle")
    capture_envelope = spec.get("frozen_resource_envelope") or {}
    if (
        capture_envelope.get("io_device") != "8:0"
        or capture_envelope.get("io_read_bps") != 1205862400
        or capture_envelope.get("io_write_bps") != 1205862400
    ):
        raise RuntimeError("XP capture-only IO envelope differs")
    validator = registry_module(bundle)
    validation = validator.validate_xp_capture_trace(
        trace,
        trace_manifest,
        runtime_authority,
        capture_authorization,
    )
    if (
        validation.get("capture_authorized") is not True
        or validation.get("execution_authorized") is not False
        or validation.get("reasons") != []
        or not validation.get("run_id")
    ):
        raise RuntimeError(
            "XP capture trace is not eligible for an AT coverage seal: "
            + json.dumps(validation.get("reasons"), sort_keys=True)
        )
    events = validation.get("events")
    if not isinstance(events, list):
        raise RuntimeError("XP capture validation events are absent")
    committed = {
        str(event.get("transaction_type"))
        for event in events
        if isinstance(event, Mapping)
        and event.get("event_type") == "commit"
        and event.get("outcome") == "success"
    }
    at_coverage = {name: name in committed for name in REQUIRED_AT}
    if at_coverage != {name: True for name in REQUIRED_AT}:
        raise RuntimeError("XP committed AT1-AT6 coverage is incomplete")
    return {
        "schema_version": "mysql_xp_capture_at_coverage_seal_v1",
        "status": "SEALED_CAPTURE_AT_COVERAGE_FOR_FORMAL_R0_ONLY",
        "bundle_id": manifest["bundle_id"],
        "bundle_manifest_sha256": bundle_manifest_sha,
        "sealed": True,
        "at_coverage": at_coverage,
        "capture_authorized": True,
        "formal_r0_launch_authorized": True,
        "outer_execution_authorized": False,
        "algorithm_c_execution_authorized": False,
        "accepted_round_authorized": False,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "capture_io_is_not_r0": True,
        "qps_resource_input_prohibited": True,
        "run_id": validation["run_id"],
        "event_count": validation["event_count"],
        "sources": {
            "trace": {"path": str(trace), "sha256": sha256(trace)},
            "trace_manifest": {
                "path": str(trace_manifest),
                "sha256": sha256(trace_manifest),
            },
            "runtime_authority": {
                "path": str(runtime_authority),
                "sha256": sha256(runtime_authority),
            },
            "capture_authorization": {
                "path": str(capture_authorization),
                "sha256": sha256(capture_authorization),
            },
            "capture_spec": {
                "path": str(capture_spec),
                "sha256": sha256(capture_spec),
                "schema_version": spec["schema_version"],
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--trace-manifest", type=Path, required=True)
    parser.add_argument("--runtime-authority", type=Path, required=True)
    parser.add_argument("--capture-authorization", type=Path, required=True)
    parser.add_argument("--capture-spec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    seal = build_seal(
        bundle=args.bundle,
        trace=args.trace,
        trace_manifest=args.trace_manifest,
        runtime_authority=args.runtime_authority,
        capture_authorization=args.capture_authorization,
        capture_spec=args.capture_spec,
    )
    atomic_fresh_json(args.output, seal)
    print(json.dumps({
        "status": seal["status"],
        "bundle_id": seal["bundle_id"],
        "output": str(args.output),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

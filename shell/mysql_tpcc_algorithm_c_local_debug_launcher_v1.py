#!/usr/bin/env python3
"""Fail-closed local TPC-C Algorithm C R0/debug launcher for compute00."""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
import socket
import sys
from typing import Any, Mapping


EXPECTED_CAPTURE_BUNDLE = (
    "eb1b515a7371fb1c4cae26062dbf5279"
    "aba2da776959eb5c03db186454cc153c"
)
EXPECTED_CPUSET = "208-215,464-471"
EXPECTED_DEVICE = "/dev/sda"
EXPECTED_PORT = 65206
EXPECTED_TRANSACTIONS = {
    "Delivery", "NewOrder", "OrderStatus", "Payment", "StockLevel"
}
LOCK = Path(
    "/home/anonymous/program/resource_adaptive_mysql/history/"
    "mysql_gradient_debug/host_control/tpcc_xp_sda.lock"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: object) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, indent=2, sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"required regular file is absent: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if (
        sidecar.is_symlink()
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii")
        != f"{sha256(path)}  {path.name}\n"
    ):
        raise RuntimeError(f"SHA-256 sidecar differs: {path}")


def atomic(path: Path, value: Mapping[str, Any], *, sidecar: bool = True) -> None:
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"fresh output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical(value)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    if sidecar:
        checksum = path.with_name(path.name + ".sha256")
        checksum.write_text(
            f"{hashlib.sha256(payload).hexdigest()}  {path.name}\n",
            encoding="ascii",
        )


def verify_bundle(bundle: Path) -> dict[str, Any]:
    manifest = load(bundle / "bundle_manifest.json")
    files = manifest.get("files")
    if (
        bundle.name != manifest.get("bundle_id")
        or manifest.get("schema_version")
        != "mysql_tpcc_algorithm_c_local_debug_bundle_v1"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("accepted_round_authorized") is not False
        or manifest.get("capture_bundle_id") != EXPECTED_CAPTURE_BUNDLE
        or not isinstance(files, dict)
    ):
        raise RuntimeError("TPC-C Algorithm C bundle identity differs")
    contract = manifest.get("algorithm_contract")
    if (
        not isinstance(contract, Mapping)
        or contract.get("original_gradient_only") is not True
        or contract.get("parameter_count") != 4
        or contract.get("all_nonempty_subset_count") != 15
        or contract.get("global_fixed_top_k") != 5
        or contract.get("qps_role") != "outer_acceptance_only"
        or contract.get("three_level_fingerprint") is not True
        or contract.get("missing_fingerprint_evidence_policy")
        != "independent_singleton"
    ):
        raise RuntimeError("TPC-C Algorithm C contract differs")
    for relative, expected in files.items():
        candidate = bundle / relative
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or sha256(candidate) != expected
        ):
            raise RuntimeError(f"TPC-C Algorithm C bundle differs: {relative}")
    return manifest


def validate_capture(
    bundle: Path, authority_path: Path
) -> dict[str, Any]:
    authority_path = authority_path.resolve(strict=True)
    verify_sidecar(authority_path)
    authority = load(authority_path)
    if (
        authority.get("schema_version")
        != "mysql_tpcc_typed_capture_completion_authority_v1"
        or authority.get("status") != "PASS_CAPTURE_ONLY_COMPLETE"
        or authority.get("formal_scope") != "mysql_only"
        or authority.get("cross_engine_comparison_prohibited") is not True
        or authority.get("immutable_runtime_bundle_id")
        != EXPECTED_CAPTURE_BUNDLE
        or authority.get("capture_workload_authorized") is not True
        or authority.get("capture_result_is_r0") is not False
        or authority.get("capture_result_is_accepted_round") is not False
        or authority.get("formal_r0_authorized") is not False
        or authority.get("outer_execution_authorized") is not False
        or authority.get("accepted_round_authorized") is not False
        or authority.get("qps_resource_input_prohibited") is not True
    ):
        raise RuntimeError("typed capture completion authority differs")
    capture_result_path = Path(
        str(authority.get("typed_capture_result_path"))
    ).resolve(strict=True)
    if (
        sha256(capture_result_path)
        != authority.get("typed_capture_result_sha256")
    ):
        raise RuntimeError("typed capture result hash differs")
    result = load(capture_result_path)
    validation_path = Path(
        str(result.get("collector_validation_path"))
    ).resolve(strict=True)
    l2_path = Path(str(result.get("collector_l2_path"))).resolve(strict=True)
    l3_path = Path(str(result.get("collector_l3_path"))).resolve(strict=True)
    validation = load(validation_path)
    l2 = json.loads(l2_path.read_text(encoding="utf-8"))
    l3 = load(l3_path)
    readback_path = Path(
        str(result.get("effective_resource_readback_path"))
    ).resolve(strict=True)
    readback = load(readback_path)
    effective = readback.get("effective")
    if not isinstance(effective, Mapping):
        raise RuntimeError("typed capture effective readback is absent")
    capture_bundle_manifest = bundle / "typed_capture_bundle_manifest.json"
    if (
        result.get("capture_token") != authority.get("capture_token")
        or result.get("immutable_runtime_bundle_id")
        != EXPECTED_CAPTURE_BUNDLE
        or result.get("immutable_runtime_bundle_manifest_sha256")
        != sha256(capture_bundle_manifest)
        or result.get("bundle_bound_collector_sha256")
        != sha256(bundle / "typed_capture_collector.py")
        or result.get("typed_binding_manifest_sha256")
        != sha256(bundle / "typed_binding_manifest.json")
        or sha256(validation_path)
        != authority.get("collector_validation_sha256")
        or sha256(l2_path) != authority.get("collector_l2_sha256")
        or sha256(l3_path) != authority.get("collector_l3_sha256")
        or sha256(readback_path)
        != authority.get("effective_resource_readback_sha256")
        or sha256(readback_path)
        != result.get("effective_resource_readback_sha256")
        or validation.get("binding_count") != 34
        or validation.get("observed_binding_count") != 34
        or set(validation.get("transaction_types") or [])
        != EXPECTED_TRANSACTIONS
        or validation.get("qps_included") is not False
        or validation.get("execution_authorized") is not False
        or validation.get("capture_workload_authorized") is not True
        or validation.get("outer_execution_authorized") is not False
        or not isinstance(l2, list)
        or {row.get("transaction_type") for row in l2}
        != EXPECTED_TRANSACTIONS
        or set((l3.get("vectors") or {}).keys()) != EXPECTED_TRANSACTIONS
        or l3.get("qps_included") is not False
        or result.get("correctness_invariant_passed") is not True
        or readback.get("verified") is not True
        or readback.get("formal_scope") != "mysql_only"
        or effective.get("cpu_quota_us") != 400000
        or effective.get("cpu_period_us") != 100000
        or effective.get("cpuset_cpus") != EXPECTED_CPUSET
        or effective.get("cpuset_mems") != "1"
        or effective.get("memory_bytes") != 8589934592
        or effective.get("memory_swap_bytes") != 0
        or effective.get("pids_max") != 512
        or effective.get("io_read_bps") != 1205862400
        or effective.get("io_write_bps") != 1205862400
        or effective.get("io_device_major_minor") != "8:0"
        or effective.get("buffer_pool_bytes") != 4294967296
        or effective.get("memsw_controller_available") is not False
    ):
        raise RuntimeError("typed capture L2/L3/envelope evidence differs")
    return {
        "path": str(authority_path),
        "sha256": sha256(authority_path),
        "capture_token": authority["capture_token"],
        "trace_sha256": authority["trace_sha256"],
        "effective_resource_readback_sha256": sha256(readback_path),
    }


def runtime_config(
    bundle: Path, manifest: Mapping[str, Any],
    capture: Mapping[str, Any], run_token: str,
) -> dict[str, Any]:
    config = copy.deepcopy(load(bundle / "config.json"))
    output = (
        Path(str((config.get("output") or {})["directory"])) / run_token
    ).resolve()
    config["profile"] = str((bundle / "profile.json").resolve(strict=True))
    config["output"]["directory"] = str(output)
    typed_manifest = str(
        (bundle / "typed_binding_manifest.json").resolve(strict=True)
    )
    typed_manifest_sha = sha256(bundle / "typed_binding_manifest.json")
    config["workload_registry"]["path"] = typed_manifest
    config["workload_registry"]["sha256"] = typed_manifest_sha
    config["estimator"]["feature_source"][
        "workload_registry_path"
    ] = typed_manifest
    config["estimator"]["feature_source"][
        "workload_registry_sha256"
    ] = typed_manifest_sha
    config["early_stop_source_bundle"]["source"] = manifest["source_directory"]
    config["early_stop_source_bundle"]["source_manifest_sha256"] = sha256(
        Path(str(manifest["source_directory"])) / "source_manifest.json"
    )
    config["typed_capture_authority"] = dict(capture)
    outer = config["formal_outer_protocol"]
    outer["formal_real_r0_checkpoint_path"] = str(
        output / "formal_real_r0.json"
    )
    outer["formal_checkpoint_path"] = str(
        output / "formal_outer_checkpoint.json"
    )
    outer["formal_point_journal_root"] = str(output / "point_journals")
    return config


def campaign_identity(
    bundle: Path, config: Mapping[str, Any], capture: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "schema_version": "mysql_tpcc_local_r0_identity_v1",
        "bundle_id": bundle.name,
        "config_sha256": hashlib.sha256(canonical(config)).hexdigest(),
        "capture_authority_sha256": capture["sha256"],
        "capture_trace_sha256": capture["trace_sha256"],
        "mysql_port": EXPECTED_PORT,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
    }


def load_source(bundle_manifest: Mapping[str, Any]) -> Path:
    source = Path(str(bundle_manifest["source_directory"])).resolve(strict=True)
    source_manifest = load(source / "source_manifest.json")
    for relative, expected in source_manifest["files"].items():
        path = source / relative
        if path.is_symlink() or not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"Algorithm C source differs: {relative}")
    sys.path.insert(0, str(source))
    shell = type(sys)("shell")
    shell.__path__ = [str(source / "shell")]
    sys.modules["shell"] = shell
    return source


def acquire_lane_lock():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    descriptor = LOCK.open("a+")
    try:
        fcntl.flock(descriptor.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        descriptor.close()
        raise RuntimeError("TPC-C/XP /dev/sda lane lock is held") from exc
    return descriptor


def r0_only(
    bundle: Path, manifest: Mapping[str, Any],
    capture: Mapping[str, Any], config: Mapping[str, Any],
) -> None:
    output = Path(config["output"]["directory"])
    if output.exists():
        raise RuntimeError("fresh R0 output already exists")
    output.mkdir(parents=True, exist_ok=False)
    atomic(output / "runtime_config.json", config)
    source = load_source(manifest)
    services = importlib.import_module(
        "mysql_algorithm_c_local_services"
    ).build(config)
    from formal_mysql_sgd_checkpoint import FormalProductionR0Ledger
    from mysql_parameter_optimizer import _validate_replay_baseline
    state_path = output / "r0_only_state.json"
    measurement = None
    checkpoint = Path(
        config["formal_outer_protocol"][
            "formal_real_r0_checkpoint_path"
        ]
    )
    try:
        measurement = services["r0_measurement"](config)
        _validate_replay_baseline(measurement, "cpu")
        FormalProductionR0Ledger.create(
            checkpoint,
            campaign_identity=campaign_identity(bundle, config, capture),
            measurement=measurement,
        )
    finally:
        cleanup = services["campaign_cleanup"]()
        if cleanup.get("zero_pressure_verified") is not True:
            raise RuntimeError("R0 cleanup did not prove zero pressure")
    if measurement is None:
        raise RuntimeError("R0 measurement authority is absent")
    atomic(
        state_path,
        {
            "schema_version": "mysql_tpcc_local_r0_only_state_v1",
            "status": "R0_COMPLETED_OUTER_BLOCKED",
            "bundle_id": bundle.name,
            "capture_authority_sha256": capture["sha256"],
            "r0_checkpoint": str(checkpoint),
            "r0_checkpoint_sha256": sha256(checkpoint),
            "capture_result_is_r0": False,
            "accepted_round_authorized": False,
            "outer_execution_authorized": False,
            "zero_pressure_verified": True,
        },
    )


def seal_outer(
    bundle: Path, manifest: Mapping[str, Any],
    capture: Mapping[str, Any], config: Mapping[str, Any],
    output_path: Path,
) -> None:
    load_source(manifest)
    from formal_mysql_sgd_checkpoint import FormalProductionR0Ledger
    checkpoint = Path(
        config["formal_outer_protocol"]["formal_real_r0_checkpoint_path"]
    )
    measurement = FormalProductionR0Ledger.load_verified(
        checkpoint,
        campaign_identity=campaign_identity(bundle, config, capture),
    )
    utilization = measurement.get("utilization_pct")
    if (
        not isinstance(utilization, Mapping)
        or any(
            not math.isfinite(float(utilization[name]))
            for name in ("cpu", "memory", "io")
        )
    ):
        raise RuntimeError("fresh R0 utilization authority differs")
    atomic(
        output_path,
        {
            "schema_version": "mysql_tpcc_local_outer_debug_authority_v1",
            "status": "OUTER_DEBUG_AUTHORIZED",
            "bundle_id": bundle.name,
            "capture_authority_sha256": capture["sha256"],
            "r0_checkpoint": str(checkpoint),
            "r0_checkpoint_sha256": sha256(checkpoint),
            "original_gradient_only": True,
            "parameter_count": 4,
            "all_nonempty_subset_count": 15,
            "fixed_global_top_k": 5,
            "outer_attempts": 1,
            "accepted_round_authorized": False,
            "qps_role": "outer_acceptance_only",
        },
    )


def outer_debug(
    bundle: Path, manifest: Mapping[str, Any],
    capture: Mapping[str, Any], config: Mapping[str, Any],
    authority_path: Path,
) -> None:
    verify_sidecar(authority_path)
    authority = load(authority_path)
    checkpoint = Path(
        config["formal_outer_protocol"]["formal_real_r0_checkpoint_path"]
    )
    if (
        authority.get("schema_version")
        != "mysql_tpcc_local_outer_debug_authority_v1"
        or authority.get("status") != "OUTER_DEBUG_AUTHORIZED"
        or authority.get("bundle_id") != bundle.name
        or authority.get("capture_authority_sha256") != capture["sha256"]
        or authority.get("r0_checkpoint_sha256") != sha256(checkpoint)
        or authority.get("all_nonempty_subset_count") != 15
        or authority.get("fixed_global_top_k") != 5
        or authority.get("accepted_round_authorized") is not False
    ):
        raise RuntimeError("outer debug authority differs")
    load_source(manifest)
    from formal_mysql_sgd_checkpoint import FormalProductionR0Ledger
    from formal_mysql_sgd_campaign import _bind_runtime_estimator_baseline
    from formal_mysql_sgd_orchestration import build_replay_driver
    from mysql_parameter_optimizer import run_optimization
    services = importlib.import_module(
        "mysql_algorithm_c_local_services"
    ).build(config)
    result_path = None
    try:
        baseline = FormalProductionR0Ledger.load_verified(
            checkpoint,
            campaign_identity=campaign_identity(bundle, config, capture),
        )
        driver = build_replay_driver(
            "tpcc", services["registration"], services["dependencies"], None
        )
        result = run_optimization(
            _bind_runtime_estimator_baseline(config, baseline),
            client=services["sql_client"],
            real_replay_callback=driver,
            baseline_validation=baseline,
            method_id="experience_table_sgd",
        )
        result_path = Path(str(result.result_path))
    finally:
        cleanup = services["campaign_cleanup"]()
        if cleanup.get("zero_pressure_verified") is not True:
            raise RuntimeError("outer cleanup did not prove zero pressure")
    if result_path is None:
        raise RuntimeError("outer debug result authority is absent")
    atomic(
        Path(config["output"]["directory"]) / "outer_debug_completion.json",
        {
            "schema_version":
                "mysql_tpcc_local_outer_debug_completion_v1",
            "status": "DEBUG_ROUND_COMPLETE",
            "result_path": str(result_path),
            "result_sha256": sha256(result_path),
            "accepted_round_authorized": False,
            "zero_pressure_verified": True,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        required=True,
        choices=("preflight", "r0-only", "seal-outer", "outer-debug"),
    )
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--capture-authority", type=Path, required=True)
    parser.add_argument("--run-token", required=True)
    parser.add_argument("--outer-authority", type=Path)
    args = parser.parse_args()
    if socket.gethostname() != "compute00":
        raise RuntimeError("TPC-C local debug is bound to compute00")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{16,128}", args.run_token):
        raise RuntimeError("invalid fresh run token")
    bundle = args.bundle.resolve(strict=True)
    manifest = verify_bundle(bundle)
    capture = validate_capture(bundle, args.capture_authority)
    config = runtime_config(bundle, manifest, capture, args.run_token)
    if args.mode == "preflight":
        print(json.dumps({
            "status": "PASS_OFFLINE_R0_READY_OUTER_BLOCKED",
            "bundle_id": bundle.name,
            "capture_authority_sha256": capture["sha256"],
        }, sort_keys=True))
        return 0
    lock = acquire_lane_lock()
    try:
        if args.mode == "r0-only":
            if args.outer_authority is not None:
                raise RuntimeError("r0-only rejects outer authority")
            r0_only(bundle, manifest, capture, config)
        elif args.mode == "seal-outer":
            if args.outer_authority is None:
                raise RuntimeError("seal-outer requires output authority path")
            seal_outer(
                bundle, manifest, capture, config, args.outer_authority
            )
        else:
            if args.outer_authority is None:
                raise RuntimeError("outer-debug requires sealed authority")
            outer_debug(
                bundle, manifest, capture, config, args.outer_authority
            )
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

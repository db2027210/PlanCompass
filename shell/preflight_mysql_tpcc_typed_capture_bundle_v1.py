#!/usr/bin/env python3
"""Offline-only preflight for one immutable MySQL TPC-C typed capture bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile


EXPECTED_BUNDLE_SCHEMA = "mysql_tpcc_typed_runtime_identity_v1"
EXPECTED_CONFIG_SCHEMA = "mysql_tpcc_typed_capture_config_v1"
EXPECTED_PROFILE_SCHEMA = "mysql_tpcc_typed_capture_profile_v1"
EXPECTED_CPUSET = "208-215,464-471"
EXPECTED_CPU_CORES = 4
EXPECTED_DEVICE = "/dev/sda"
EXPECTED_IO_BPS = 1_205_862_400
EXPECTED_PORT = 65206


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_object(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"required regular file is absent or symlinked: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise RuntimeError(f"existing preflight artifact differs: {path}")
        return
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def validate_bundle(bundle: Path) -> tuple[dict[str, object], Path, Path]:
    bundle = bundle.resolve(strict=True)
    manifest_path = bundle / "bundle_manifest.json"
    manifest = load_object(manifest_path)
    authorization = manifest.get("authorization")
    downstream = (manifest.get("contract") or {}).get(
        "downstream_authority"
    )
    observer = (manifest.get("contract") or {}).get(
        "dynamic_observer_authority"
    )
    diagnostics = (manifest.get("contract") or {}).get(
        "dynamic_gate_diagnostics"
    )
    sealed_tests = (manifest.get("contract") or {}).get(
        "sealed_test_closure"
    )
    if (
        manifest.get("schema_version") != EXPECTED_BUNDLE_SCHEMA
        or manifest.get("bundle_id") != bundle.name
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("capture_only") is not True
        or manifest.get("outer_execution_authorized") is not False
        or not isinstance(authorization, dict)
        or authorization.get("production_trace_collected") is not False
        or authorization.get("formal_r0_authorized") is not False
        or authorization.get("outer_execution_authorized") is not False
        or authorization.get("accepted_round_authorized") is not False
        or not isinstance(downstream, dict)
        or downstream.get("collector_cli") != "--output-dir"
        or downstream.get("trace_header_capture_token_required") is not True
        or downstream.get(
            "bundle_manifest_collector_config_hashes_recorded"
        ) is not True
        or downstream.get("trace_result_l2_l3_hashes_recorded") is not True
        or downstream.get(
            "coverage_resource_performance_schema_hashes_recorded"
        ) is not True
        or downstream.get("correctness_evidence_recorded") is not True
        or downstream.get("trace_and_result_sha256_sidecars_required")
        is not True
        or downstream.get("post_cleanup_completion_authority_required")
        is not True
        or downstream.get("fresh_capture_token_output_required") is not True
        or downstream.get("formal_r0_authorized") is not False
        or downstream.get("outer_execution_authorized") is not False
        or downstream.get("accepted_round_authorized") is not False
        or not isinstance(observer, dict)
        or observer.get("proc_cwd_resolved") is not True
        or observer.get("observer_script_regular_non_symlink_required")
        is not True
        or observer.get("observer_script_sha256_required") is not True
        or observer.get("observer_argv_exact_required") is not True
        or observer.get("wrong_cwd_hash_or_argv_policy")
        != "benchmark_fail_closed"
        or not isinstance(diagnostics, dict)
        or diagnostics.get("window_seconds") != 60
        or diagnostics.get("minimum_intervals") != 60
        or diagnostics.get("maximum_sample_gap_seconds") != 1.5
        or diagnostics.get("fail_closed") is not True
        or diagnostics.get("reset_reason_journal_required") is not True
        or diagnostics.get("proc_sample_duration_required") is not True
        or diagnostics.get("stable_run_length_required") is not True
        or diagnostics.get(
            "added_removed_identity_authority_required"
        ) is not True
        or diagnostics.get("deadline_summary_required") is not True
        or diagnostics.get(
            "qemu_vm_data_plane_classification_required"
        ) is not True
        or diagnostics.get("unknown_device_authority_policy")
        != "fail_closed"
        or diagnostics.get("cpu_overlap_policy")
        != "immediate_fail_closed"
        or diagnostics.get("shared_target_device_policy")
        != "immediate_fail_closed"
        or diagnostics.get("target_resource_readback_drift_policy")
        != "immediate_fail_closed"
        or diagnostics.get(
            "isolated_postgresql_churn_journal_required"
        ) is not True
        or diagnostics.get("disjoint_postgresql_identity_churn_policy")
        != "inventory_only_no_window_reset"
        or not isinstance(sealed_tests, dict)
        or sealed_tests.get("runner")
        != "tests/run_sealed_bundle_tests.py"
        or sealed_tests.get("test_module_count") != 10
        or sealed_tests.get("arbitrary_cwd_supported") is not True
        or sealed_tests.get("workspace_paths_prohibited") is not True
        or sealed_tests.get("old_artifact_paths_prohibited") is not True
        or sealed_tests.get("manifest_hash_verification_required")
        is not True
        or sealed_tests.get("pythonpath_inheritance_prohibited")
        is not True
    ):
        raise RuntimeError("immutable TPC-C typed bundle authority differs")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise RuntimeError("immutable TPC-C typed bundle file map is absent")
    for relative, expected in sorted(files.items()):
        candidate = bundle / str(relative)
        if (
            not isinstance(expected, str)
            or candidate.is_symlink()
            or not candidate.is_file()
            or sha256(candidate) != expected
        ):
            raise RuntimeError(f"immutable TPC-C typed bundle differs: {relative}")

    config_path = bundle / "capture/tpcc_typed_capture_config_v1.json"
    profile_path = bundle / "capture/tpcc_typed_capture_profile_v1.json"
    config = load_object(config_path)
    profile = load_object(profile_path)
    timing = (manifest.get("contract") or {}).get("capture_timing")
    if (
        config.get("schema_version") != EXPECTED_CONFIG_SCHEMA
        or config.get("formal_scope") != "mysql_only"
        or config.get("cross_engine_comparison_prohibited") is not True
        or config.get("capture_only") is not True
        or config.get("execution_authorized") is not False
        or config.get("result_usage") != "typed_binding_l2_l3_registry_only"
        or profile.get("schema_version") != EXPECTED_PROFILE_SCHEMA
        or profile.get("formal_scope") != "mysql_only"
        or profile.get("cross_engine_comparison_prohibited") is not True
        or profile.get("capture_only") is not True
        or profile.get("cpu_cores") != EXPECTED_CPU_CORES
        or profile.get("cpuset") != EXPECTED_CPUSET
        or profile.get("read_bps") != EXPECTED_IO_BPS
        or profile.get("write_bps") != EXPECTED_IO_BPS
        or not isinstance(timing, dict)
        or timing.get("capture_result_is_r0") is not False
        or timing.get("capture_result_is_accepted_round") is not False
    ):
        raise RuntimeError("TPC-C typed capture config/profile authority differs")

    entry_source = (
        bundle / "capture/mysql_tpcc_typed_capture_entry_v1.py"
    ).read_text(encoding="utf-8")
    required_lane_markers = (
        f'EXPECTED_CPUSET = "{EXPECTED_CPUSET}"',
        f'EXPECTED_PORT = {EXPECTED_PORT}',
        f'EXPECTED_DEVICE = "{EXPECTED_DEVICE}"',
        f"EXPECTED_READ_WRITE_BPS = {EXPECTED_IO_BPS}",
        "if args.config.resolve(strict=True) != expected_config.resolve(strict=True):",
        'raise SystemExit("TPC-C typed capture must use the bundle-bound config")',
    )
    missing = [marker for marker in required_lane_markers if marker not in entry_source]
    if missing:
        raise RuntimeError(f"TPC-C typed capture lane binding differs: {missing}")
    return manifest, config_path, profile_path


def java_environment_preflight(bundle: Path) -> dict[str, object]:
    manifest_path = bundle / "config/tpcc_offline_binding_manifest.json"
    jar = bundle / "runtime/benchbase-formal.jar"
    main_class = "com.oltpbenchmark.TpccTypedTracePreflightMain"
    with zipfile.ZipFile(jar) as archive:
        if (
            "com/oltpbenchmark/TpccTypedTracePreflightMain.class"
            not in archive.namelist()
        ):
            raise RuntimeError("typed trace Java preflight class is absent")
    with tempfile.TemporaryDirectory(prefix="mysql_tpcc_typed_preflight_") as temporary:
        trace = Path(temporary) / "trace.jsonl"
        environment = os.environ.copy()
        environment.update(
            {
                "MYSQL_TPCC_TYPED_TRACE_PATH": str(trace),
                "MYSQL_TPCC_TYPED_MANIFEST_PATH": str(manifest_path),
                "MYSQL_TPCC_TYPED_MANIFEST_SHA256": sha256(manifest_path),
                "MYSQL_TPCC_TYPED_CAPTURE_TOKEN": "OFFLINE_PREFLIGHT_ONLY_V1",
                "MYSQL_TPCC_TYPED_METRICS_JDBC_URL": "jdbc:mysql://127.0.0.1:1/tpcc",
                "MYSQL_TPCC_TYPED_DEFAULTS_FILE": "/opt/mysql-tpcc-config/mysql.cnf",
                "MYSQL_TPCC_TYPED_CORRECTNESS_VERIFIED": "1",
                "MYSQL_TPCC_TYPED_CAPTURE_ONLY": "1",
                "MYSQL_TPCC_TYPED_OUTER_AUTHORIZED": "0",
                "MYSQL_TPCC_TYPED_TRACE_ROLE": "OWNER",
            }
        )
        completed = subprocess.run(
            ["java", "-cp", str(jar), main_class],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if (
            completed.returncode != 0
            or completed.stdout.strip() != "TPCC_TYPED_TRACE_PREFLIGHT_PASS"
            or trace.exists()
        ):
            raise RuntimeError(
                "typed trace Java offline preflight failed: "
                f"returncode={completed.returncode} stderr={completed.stderr.strip()}"
            )
    return {
        "main_class": main_class,
        "status": "PASS",
        "database_contacted": False,
        "trace_created": False,
        "stdout": "TPCC_TYPED_TRACE_PREFLIGHT_PASS",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    bundle = args.bundle.resolve(strict=True)
    manifest, config_path, profile_path = validate_bundle(bundle)
    java_preflight = java_environment_preflight(bundle)
    report = {
        "schema_version": "mysql_tpcc_typed_capture_offline_preflight_v1",
        "status": "PASS_OFFLINE_NOT_LAUNCHED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "bundle_id": manifest["bundle_id"],
        "bundle_manifest": str(bundle / "bundle_manifest.json"),
        "bundle_manifest_sha256": sha256(bundle / "bundle_manifest.json"),
        "capture_config": str(config_path),
        "capture_config_sha256": sha256(config_path),
        "capture_profile": str(profile_path),
        "capture_profile_sha256": sha256(profile_path),
        "planned_capture_envelope": {
            "cpu_quota_cores": EXPECTED_CPU_CORES,
            "cpuset_cpus": EXPECTED_CPUSET,
            "io_device": EXPECTED_DEVICE,
            "io_read_bps": EXPECTED_IO_BPS,
            "io_write_bps": EXPECTED_IO_BPS,
            "mysql_port": EXPECTED_PORT,
        },
        "java_environment_preflight": java_preflight,
        "capture_only": True,
        "capture_result_is_r0": False,
        "capture_result_is_accepted_round": False,
        "capture_launch_authorized_by_this_report": False,
        "formal_r0_authorized": False,
        "algorithm_c_authorized": False,
        "outer_execution_authorized": False,
        "accepted_round_authorized": False,
        "qps_resource_input_prohibited": True,
        "side_effects_performed": [],
    }
    payload = canonical(report)
    atomic_write(args.output, payload)
    atomic_write(
        args.output.with_name(args.output.name + ".sha256"),
        f"{hashlib.sha256(payload).hexdigest()}  {args.output.name}\n".encode("ascii"),
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "bundle_id": report["bundle_id"],
                "output": str(args.output.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

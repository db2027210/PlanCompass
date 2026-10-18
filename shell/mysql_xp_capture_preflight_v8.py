#!/usr/bin/env python3
"""Build a fresh, unlaunched local XP capture spec and evidence contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from typing import Any, Dict, Mapping, Optional, Sequence

_BUNDLE_ROOT = Path(__file__).resolve().parents[1]
for _relative in ("python", "orchestration", "driver", "capture"):
    _candidate = _BUNDLE_ROOT / _relative
    if _candidate.is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from mysql_algorithm_c_local_services import JOB_STORAGE_V2, LOCAL_LANES


ROOT = Path("/home/anonymous/program/resource_adaptive_mysql")
_BUNDLED_BINDING_CONTRACT = (
    _BUNDLE_ROOT
    / "authority/hybench_xp.production_binding_capture_contract_v1.json"
)
SPEC_SCHEMA = "mysql_xp_fresh_local_capture_spec_v1"
CONTRACT_SCHEMA = "mysql_xp_capture_evidence_collection_contract_v1"
ISOLATION_SCHEMA = "mysql_xp_job_isolation_authority_v1"
EXPECTED_BUNDLE_SCHEMA = "mysql_xp_v8_immutable_runtime_v1"

# Binding capture is not an Algorithm C performance point.  The production
# 5.15/0.56 MB/s envelope starves the first sixteen XP operations before the
# complete AP/AT binding trace can seal, so use a separately frozen
# capture-only SATA ceiling.  Formal R0 and replay keep using the production
# optimizer profile unchanged.
CAPTURE_IO_READ_BPS = 1_205_862_400
CAPTURE_IO_WRITE_BPS = 1_205_862_400


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_runtime_bundle(path: Path) -> tuple[Path, Dict[str, Any]]:
    bundle = path.resolve(strict=True)
    if bundle.is_symlink() or not bundle.is_dir():
        raise ValueError("XP runtime bundle must be a real immutable directory")
    manifest_path = bundle / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("XP runtime bundle manifest is absent")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("schema_version") != EXPECTED_BUNDLE_SCHEMA
        or manifest.get("bundle_id") != bundle.name
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("host_binding") != "compute00"
        or manifest.get("ssh_prohibited") is not True
        or manifest.get("capture_authorized") is not True
        or manifest.get("capture_io_envelope_is_not_r0") is not True
        or manifest.get("active_parameter_count") != 7
        or manifest.get("required_nonempty_subsets") != 127
        or manifest.get("fixed_replay_count") != 5
    ):
        raise ValueError("XP runtime bundle manifest contract is invalid")
    payload = manifest.get("payload_files")
    if not isinstance(payload, Mapping) or not payload:
        raise ValueError("XP runtime bundle payload closure is absent")
    for relative, expected in payload.items():
        candidate = bundle / str(relative)
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or _sha(candidate) != expected
        ):
            raise ValueError(f"XP runtime bundle payload mismatch: {relative}")
    return bundle, manifest


def _cpuset(value: str):
    result = set()
    for token in value.split(","):
        if "-" in token:
            low, high = (int(part) for part in token.split("-", 1))
            result.update(range(low, high + 1))
        else:
            result.add(int(token))
    return result


def _major_minor(path: Path) -> str:
    status = path.stat()
    return f"{os.major(status.st_rdev)}:{os.minor(status.st_rdev)}"


def _atomic(path: Path, value: Mapping[str, Any]) -> str:
    if path.exists() or path.is_symlink():
        raise ValueError(f"fresh preflight output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.partial")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    digest = _sha(path)
    path.with_name(path.name + ".sha256").write_text(
        f"{digest}  {path.name}\n", encoding="ascii"
    )
    return digest


def _jdk_version(binary: Path) -> str:
    result = subprocess.run(
        [str(binary), "-version"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        timeout=15,
    )
    return (result.stdout + result.stderr).strip()


def build_preflight(
    *,
    run_id: str,
    output_dir: Path,
    runtime_bundle: Path,
    readiness_wait_seconds: int = 600,
    hostname: Optional[str] = None,
) -> Dict[str, Any]:
    hostname = socket.gethostname() if hostname is None else hostname
    if hostname != "compute00" or not run_id:
        raise ValueError("fresh local XP preflight requires compute00 and run_id")
    if output_dir.exists() or output_dir.is_symlink():
        raise ValueError("fresh preflight output directory already exists")
    if not 60 <= readiness_wait_seconds <= 1800:
        raise ValueError("readiness wait must be in [60,1800]")
    runtime_bundle, manifest = _load_runtime_bundle(runtime_bundle)
    job = LOCAL_LANES["job"]
    xp = LOCAL_LANES["xp"]
    cpu_intersection = sorted(_cpuset(job.cpuset) & _cpuset(xp.cpuset))
    device_disjoint = _major_minor(Path(job.device)) != _major_minor(Path(xp.device))
    numa_intersection = sorted(
        set(job.mems.split(",")) & set(xp.mems.split(","))
    )
    if cpu_intersection or not device_disjoint or not numa_intersection:
        raise ValueError("current JOB/XP isolation facts differ from frozen contract")
    storage_manifest = Path(JOB_STORAGE_V2.manifest)
    if not storage_manifest.is_file():
        raise ValueError("JOB NVMe storage authority is absent")
    output_dir.mkdir(parents=True, mode=0o700)
    isolation_path = output_dir / "job_xp_isolation_authority.json"
    isolation = {
        "schema_version": ISOLATION_SCHEMA,
        "hostname": "compute00",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "transport": "local_no_ssh",
        "cpu_disjoint": True,
        "device_disjoint": True,
        "numa_disjoint": False,
        "numa_non_disjoint_declared": True,
        "cpu_intersection": cpu_intersection,
        "numa_intersection": numa_intersection,
        "job_lane": {
            "cpuset_cpus": job.cpuset,
            "cpuset_mems": job.mems,
            "device": job.device,
            "device_major_minor": _major_minor(Path(job.device)),
            "storage_profile": JOB_STORAGE_V2.profile_id,
            "storage_manifest_path": str(storage_manifest),
            "storage_manifest_sha256": _sha(storage_manifest),
        },
        "xp_lane": {
            "cpuset_cpus": xp.cpuset,
            "cpuset_mems": xp.mems,
            "device": xp.device,
            "device_major_minor": _major_minor(Path(xp.device)),
        },
        "runtime_bundle_id": manifest["bundle_id"],
        "runtime_bundle_manifest_path": str(runtime_bundle / "manifest.json"),
        "runtime_bundle_manifest_sha256": _sha(runtime_bundle / "manifest.json"),
        "source_service_path": str(
            runtime_bundle
            / "orchestration/mysql_algorithm_c_local_services.py"
        ),
        "source_service_sha256": _sha(
            runtime_bundle
            / "orchestration/mysql_algorithm_c_local_services.py"
        ),
    }
    isolation_sha = _atomic(isolation_path, isolation)

    profile = json.loads(
        (runtime_bundle / manifest["profile"]["path"])
        .read_text(encoding="utf-8")
    )
    page_size = int(os.sysconf("SC_PAGE_SIZE"))
    memory = int(profile["memory_bytes"]) // page_size * page_size
    connector = runtime_bundle / manifest["runtime"]["connector"]
    statement = runtime_bundle / manifest["runtime"]["statements"]
    driver_root = runtime_bundle / "driver/attribution-classes"
    wrapper_root = runtime_bundle / "formal-wrapper-classes"
    artifacts = {
        "ap_client_source":
            runtime_bundle / "source/src/main/java/com/hybench/workload/APClient.java",
        "client_source":
            runtime_bundle / "source/src/main/java/com/hybench/workload/Client.java",
        "connector_jar": connector,
        "driver_class": driver_root / "formal/mysql/AttributingMysqlDriver.class",
        "formal_binding_trace_source":
            runtime_bundle
            / "source/src/main/java/com/hybench/util/FormalBindingTrace.java",
        "graceful_quiescence_source":
            runtime_bundle
            / "source/src/main/java/com/hybench/util/GracefulQuiescence.java",
        "hybench_fat_jar": runtime_bundle / manifest["runtime"]["jar"],
        "hybench_formal_main_class":
            wrapper_root / "com/hybench/HyBenchFormalMain.class",
        "hybench_formal_main_source":
            runtime_bundle
            / "source/provenance/formal_clients_20260721/com/hybench/"
            "HyBenchFormalMain.java",
        "hybench_source":
            runtime_bundle / "source/src/main/java/com/hybench/HyBench.java",
        "random_generator_source":
            runtime_bundle
            / "source/src/main/java/com/hybench/util/RandomGenerator.java",
        "tp_client_source":
            runtime_bundle / "source/src/main/java/com/hybench/workload/TPClient.java",
    }
    absent = [str(path) for path in artifacts.values() if not path.is_file()]
    if absent or not statement.is_file():
        raise ValueError(f"capture runtime inputs are absent: {absent}")
    evidence_dir = output_dir / "readiness_evidence"
    inventory_path = evidence_dir / "inventory.json"
    rolling_path = evidence_dir / "rolling_non_interference.json"
    jdk = Path("/usr/bin/java").resolve(strict=True)
    binding_contract = (
        _BUNDLED_BINDING_CONTRACT
        if _BUNDLED_BINDING_CONTRACT.is_file()
        else (
            ROOT
            / "config/formal/frozen_mysql_workloads_and_parameters_20260723_v3/"
            "plan_workload_registries/"
            "hybench_xp.production_binding_capture_contract_v1.json"
        )
    )
    spec = {
        "schema_version": SPEC_SCHEMA,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "capture_only": True,
        "outer_execution_authorized": False,
        "launch_token": run_id,
        "runtime_bundle_id": manifest["bundle_id"],
        "runtime_artifacts": {
            role: str(path.resolve()) for role, path in artifacts.items()
        },
        "bootstrap_paths": {
            "wrapper_class_root": str(wrapper_root.resolve()),
            "driver_class_root": str(driver_root.resolve()),
            "connector_jar": str(connector.resolve()),
            "hybench_fat_jar": str(artifacts["hybench_fat_jar"].resolve()),
            "statement_file": str(statement.resolve()),
        },
        "frozen_resource_envelope": {
            "cpu_quota_cores": float(profile["cpu_cores"]),
            "cpuset_cpus": xp.cpuset,
            "cpuset_mems": xp.mems,
            "innodb_buffer_pool_bytes": int(profile["buffer_pool_bytes"]),
            "io_device": _major_minor(Path(xp.device)),
            "io_read_bps": CAPTURE_IO_READ_BPS,
            "io_write_bps": CAPTURE_IO_WRITE_BPS,
            "memory_bytes": memory,
            "memory_swap_bytes": 0,
            "pids_max": int(profile["pids"]),
        },
        "frozen_workload": {
            "workload_seed": 4508333,
            "workload": {
                "ap_query_ids": [f"AP-{value}" for value in range(1, 14)],
                "ap_query_weights": [6] * 9 + [28, 6, 6, 6],
                "capture_ap_query_sequence": [
                    f"AP-{value}" for value in range(1, 14)
                ],
                "at_mix_percent": {
                    "AT1": 40, "AT2": 30, "AT3": 15,
                    "AT4": 5, "AT5": 7, "AT6": 3,
                },
                "xap_clients": 8,
                "xtp_clients": 8,
                "database_name": "hybench_sf10x",
                "scale_factor": "10x",
                "capture_schedule":
                    "deterministic_finite_ap13_at6_fixed_seed_binding_capture",
                "formal_replay_schedule":
                    "original_weighted_fixed_seed_stream",
                "result_usage": "binding_registry_only",
                "qps_latency_resource_results": "prohibited",
            },
        },
        "resolved_jdk_binary": str(jdk),
        "jdk_version_readback": _jdk_version(jdk),
        "inventory_path": str(inventory_path.resolve()),
        "rolling_gate_path": str(rolling_path.resolve()),
        "readiness_wait_seconds": readiness_wait_seconds,
        "binding_capture_contract_path": str(binding_contract.resolve()),
        "binding_capture_contract_sha256": _sha(binding_contract),
        "isolation_authority_path": str(isolation_path.resolve()),
        "isolation_authority_sha256": isolation_sha,
    }
    spec_path = output_dir / "capture_spec.json"
    spec_sha = _atomic(spec_path, spec)
    contract = {
        "schema_version": CONTRACT_SCHEMA,
        "status": "AWAITING_SAME_TOKEN_CAPTURE_READINESS_REQUEST",
        "launch_token": run_id,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "hostname": "compute00",
        "transport": "local_no_ssh",
        "single_collection_command": [
            "/home/anonymous/miniconda3/envs/py313/bin/python",
            str(
                runtime_bundle
                / "capture/mysql_xp_capture_evidence_collector.py"
            ),
            "--capture-spec", str(spec_path.resolve()),
            "--capture-readiness-request", "<same-token-request-path>",
            "--shared-snapshot", "<main-thread-shared-snapshot-json>",
        ],
        "sampling": {
            "duration_seconds": 60,
            "interval_seconds": 1,
            "minimum_samples": 60,
            "maximum_gap_seconds": 1.5,
        },
        "inventory_scope": ["mysql", "postgresql", "benchmark", "controller"],
        "must_inventory_old_containers": [
            "old_mysql_xp_containers",
            "postgresql_tpcc_containers",
            "postgresql_xp_containers",
        ],
        "shared_snapshot_contract": {
            "schema_version": "mysql_four_scene_shared_snapshot_v1",
            "inventory_complete": True,
            "single_collection_per_control_cycle": True,
            "container_groups": {
                "required_groups": [
                    "old_mysql_xp_containers",
                    "postgresql_tpcc_containers",
                    "postgresql_xp_containers",
                ],
                "row_fields": ["name", "status", "category", "pid_if_running"],
                "running_pid_must_reconcile_to_proc": True,
            },
            "process_devices":
                "map every running non-target DB/benchmark PID to major:minor",
        },
        "thresholds": {
            "max_co_resident_cpu_pct": 5.0,
            "max_co_resident_io_bps": 1000000,
            "max_cpuset_overlap_process_count": 0,
            "max_shared_device_process_count": 0,
            "max_unexpected_db_or_benchmark_process_count": 0,
        },
        "outputs": {
            "inventory": str(inventory_path.resolve()),
            "rolling_gate": str(rolling_path.resolve()),
        },
        "capture_spec_sha256": spec_sha,
        "runtime_bundle_id": manifest["bundle_id"],
        "runtime_bundle_manifest_sha256": _sha(
            runtime_bundle / "manifest.json"
        ),
        "launch_authorized": False,
        "outer_execution_authorized": False,
    }
    contract_path = output_dir / "evidence_collection_contract.json"
    contract_sha = _atomic(contract_path, contract)
    return {
        "capture_spec_path": str(spec_path),
        "capture_spec_sha256": spec_sha,
        "isolation_authority_path": str(isolation_path),
        "isolation_authority_sha256": isolation_sha,
        "evidence_contract_path": str(contract_path),
        "evidence_contract_sha256": contract_sha,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--runtime-bundle",
        type=Path,
        default=(
            Path(os.environ["MYSQL_XP_RUNTIME_BUNDLE"])
            if os.environ.get("MYSQL_XP_RUNTIME_BUNDLE")
            else None
        ),
        required=not bool(os.environ.get("MYSQL_XP_RUNTIME_BUNDLE")),
    )
    parser.add_argument("--readiness-wait-seconds", type=int, default=600)
    args = parser.parse_args(argv)
    result = build_preflight(
        run_id=args.run_id,
        output_dir=args.output_dir,
        runtime_bundle=args.runtime_bundle,
        readiness_wait_seconds=args.readiness_wait_seconds,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
